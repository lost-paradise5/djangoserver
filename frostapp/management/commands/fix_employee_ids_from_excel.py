import os, sys, re, csv
import logging
import datetime as dt
from typing import Optional, Tuple, Dict

import pandas as pd
from django.core.management.base import BaseCommand
from django.db import transaction
from frostapp.models import User  # managed = False ок для UPDATE

# ────────────────────────────── Логгер
def _setup_logger():
    ts = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    log_dir = os.path.join('/app', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f'fix_employee_ids_{ts}.log')

    logger = logging.getLogger('fix_employee_ids')
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    fh = logging.FileHandler(log_path, encoding='utf-8')
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)

    if not logger.handlers:
        logger.addHandler(fh)
        logger.addHandler(ch)

    return logger, log_path

# ────────────────────────────── Нормализации
def _norm_inn(v: str) -> Optional[str]:
    s = re.sub(r'\D+', '', str(v or ''))
    return s if len(s) in (10, 12) else None

def _norm_fio(last: str, first: str, patr: str) -> str:
    parts = [str(last or '').strip(), str(first or '').strip(), str(patr or '').strip()]
    fio = ' '.join(p for p in parts if p)
    # приводим Ё/Е к одному виду
    fio = fio.replace('Ё', 'Е').replace('ё', 'е')
    return re.sub(r'\s+', ' ', fio).strip()

def _norm_name_key(name: str) -> str:
    n = str(name or '').strip().lower()
    n = n.replace('ё', 'е')
    n = re.sub(r'\s+', ' ', n)
    return n

# ────────────────────────────── Вспомогалки для Excel
def _col_letter_to_idx(letter: str) -> int:
    """A -> 0, B -> 1, ..."""
    letter = (letter or '').strip().upper()
    if not letter:
        raise ValueError("Пустая буква колонки")
    idx = 0
    for ch in letter:
        if not ('A' <= ch <= 'Z'):
            raise ValueError(f"Неверная буква колонки: {letter}")
        idx = idx * 26 + (ord(ch) - ord('A') + 1)
    return idx - 1  # zero-based

def _auto_locate_headers(df: pd.DataFrame, logger) -> Tuple[int, Dict[str, int]]:
    """
    Ищем заголовки 'инн/inn', 'фамилия/surname/last', 'имя/name', 'отчество/patronymic'
    в первых ~10 строках, возвращаем (start_row_index, mapping), где mapping = {'inn': col_idx, ...}
    start_row_index — первая строка с данными (ниже максимально найденного заголовка).
    """
    patterns = {
        'inn':      re.compile(r'^\s*(инн|inn)\s*$', re.I),
        'last':     re.compile(r'^\s*(фам|фамилия|surname|last)\s*$', re.I),
        'first':    re.compile(r'^\s*(имя|name)\s*$', re.I),
        'patr':     re.compile(r'^\s*(отчество|patronymic|otchestvo|middle)\s*$', re.I),
    }
    max_scan_rows = min(10, len(df))
    found: Dict[str, Tuple[int, int]] = {}  # key -> (row_idx, col_idx)

    for r in range(max_scan_rows):
        for c in range(df.shape[1]):
            val = str(df.iat[r, c] if r < len(df) else '').strip()
            if not val:
                continue
            for key, rx in patterns.items():
                if key in found:
                    continue
                if rx.match(val):
                    found[key] = (r, c)

    if set(found.keys()) >= {'inn', 'last', 'first', 'patr'}:
        start_row = max(found[k][0] for k in found) + 1
        mapping = {k: found[k][1] for k in found}
        logger.info(f"Авто-заголовки: {found} → start_row={start_row}, mapping={mapping}")
        return start_row, mapping

    raise ValueError("Не удалось автоматически найти все заголовки (ИНН/Фамилия/Имя/Отчество). "
                     "Укажи явно колонки через --col-inn/--col-last/--col-first/--col-patr и --start-row.")

class Command(BaseCommand):
    help = "Обновляет users.employee_id (ИНН) из Excel по совпадению users.full_name."

    def add_arguments(self, parser):
        parser.add_argument('--file', required=True, help='Путь к Excel (.xlsx) внутри контейнера')
        parser.add_argument('--sheet', default=0, help='Лист: имя или индекс (по умолчанию 0)')

        # Явные колонки и первая строка с данными (1-based)
        parser.add_argument('--col-inn', help='Колонка ИНН (буква, напр. B)')
        parser.add_argument('--col-last', help='Колонка Фамилия (буква, напр. C)')
        parser.add_argument('--col-first', help='Колонка Имя (буква, напр. D)')
        parser.add_argument('--col-patr', help='Колонка Отчество (буква, напр. E)')
        parser.add_argument('--start-row', type=int, help='Первая строка с данными (1-based). Если не указана — авто.')

        parser.add_argument('--dry-run', action='store_true', help='Только показать изменения, без записи')
        parser.add_argument('--batch', type=int, default=200, help='Размер батча для записи (по умолчанию 200)')
        parser.add_argument('--backup-csv', default=None, help='Путь для CSV с планируемыми изменениями')

    def handle(self, *args, **opts):
        logger, log_path = _setup_logger()
        xlsx_path = opts['file']
        sheet = opts['sheet']
        dry = opts['dry_run']
        batch = opts['batch']
        backup_csv = opts['backup_csv']

        logger.info(f"Старт: file={xlsx_path}, sheet={sheet}, dry_run={dry}, batch={batch}")
        if not os.path.exists(xlsx_path):
            logger.error(f"Файл не найден: {xlsx_path}")
            return

        # Читаем как есть (без заголовков), чтобы по желанию найти их автоматически
        try:
            df = pd.read_excel(xlsx_path, sheet_name=sheet, header=None, dtype=str)
        except Exception as e:
            logger.error(f"Не удалось прочитать Excel: {e}")
            return

        # Определяем колонки
        col_inn = opts.get('col_inn')
        col_last = opts.get('col_last')
        col_first = opts.get('col_first')
        col_patr = opts.get('col_patr')
        start_row_opt = opts.get('start_row')

        if all([col_inn, col_last, col_first, col_patr]):
            # режим явных букв колонок
            ci = _col_letter_to_idx(col_inn)
            cl = _col_letter_to_idx(col_last)
            cf = _col_letter_to_idx(col_first)
            cp = _col_letter_to_idx(col_patr)
            start_row = (start_row_opt - 1) if start_row_opt and start_row_opt > 0 else 1  # по умолчанию ниже первой строки
            logger.info(f"Режим явных колонок: inn={col_inn}({ci}), last={col_last}({cl}), first={col_first}({cf}), patr={col_patr}({cp}), start_row={start_row+1}")
            sub = df.iloc[start_row:, [ci, cl, cf, cp]].copy()
            sub.columns = ['inn_raw', 'last', 'first', 'patr']
        else:
            # авто-режим: пытаемся найти заголовки в первых строках
            try:
                start_row, mapping = _auto_locate_headers(df, logger)
                sub = df.iloc[start_row:, [mapping['inn'], mapping['last'], mapping['first'], mapping['patr']]].copy()
                sub.columns = ['inn_raw', 'last', 'first', 'patr']
            except Exception as e:
                logger.error(str(e))
                return

        # Нормализация данных
        sub['inn_norm'] = sub['inn_raw'].map(_norm_inn)
        sub['fio'] = sub.apply(lambda r: _norm_fio(r['last'], r['first'], r['patr']), axis=1)
        sub = sub[sub['fio'] != '']  # выбрасываем пустые ФИО
        total_rows = len(sub)
        logger.info(f"Подготовлено строк к обработке: {total_rows}")

        # Кэш пользователей из БД
        qs = User.objects.all().only('id', 'full_name', 'employee_id')
        db_map = {}
        for u in qs:
            key = _norm_name_key(u.full_name)
            db_map.setdefault(key, []).append((u.id, u.employee_id))
        logger.info(f"Из БД пользователей: {qs.count()}, уникальных ключей ФИО={len(db_map)}")
        logger.info(f"Лог: {log_path}")

        to_update = []  # (user_id, old_inn, new_inn, fio)
        stats = {'unchanged': 0, 'bad_inn': 0, 'not_found': 0, 'duplicate': 0}

        for i, row in sub.iterrows():
            fio = row['fio']
            inn_new = row['inn_norm']
            if not inn_new:
                stats['bad_inn'] += 1
                logger.warning(f"[{i}] ПЛОХОЙ ИНН: fio='{fio}', raw='{row['inn_raw']}' → пропуск")
                continue

            key = _norm_name_key(fio)
            matches = db_map.get(key, [])
            if not matches:
                stats['not_found'] += 1
                logger.warning(f"[{i}] НЕ НАЙДЕН в БД по full_name: '{fio}' → пропуск")
                continue
            if len(matches) > 1:
                stats['duplicate'] += 1
                logger.warning(f"[{i}] ДУБЛИКАТЫ ФИО в БД: '{fio}', ids={[m[0] for m in matches]} → пропуск")
                continue

            user_id, old_inn = matches[0]
            old_inn_str = str(old_inn or '')
            if old_inn_str == inn_new:
                stats['unchanged'] += 1
                logger.info(f"[{i}] БЕЗ ИЗМЕНЕНИЙ id={user_id} '{fio}' → ИНН уже {inn_new}")
                continue

            logger.info(f"[{i}] ОБНОВЛЕНИЕ id={user_id} '{fio}': {old_inn_str} → {inn_new}")
            to_update.append((user_id, old_inn_str, inn_new, fio))

        logger.info(f"ИТОГО: план обновлений={len(to_update)}, без_изменений={stats['unchanged']}, "
                    f"не_найдено={stats['not_found']}, дубликаты={stats['duplicate']}, плохой_инн={stats['bad_inn']}")

        # CSV-бэкап плана
        if backup_csv is None:
            ts = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_csv = os.path.join('/app', 'logs', f'fix_employee_ids_plan_{ts}.csv')
        try:
            with open(backup_csv, 'w', newline='', encoding='utf-8') as f:
                wr = csv.writer(f, delimiter=';')
                wr.writerow(['user_id', 'full_name', 'old_employee_id', 'new_employee_id'])
                for uid, old_inn, new_inn, fio in to_update:
                    wr.writerow([uid, fio, old_inn, new_inn])
            logger.info(f"Бэкап плана записан: {backup_csv}")
        except Exception as e:
            logger.warning(f"Не удалось записать backup CSV: {e}")

        if dry:
            logger.info("dry-run: записи в БД не выполнялись.")
            return

        # Запись батчами
        updated = 0
        with transaction.atomic():
            for k in range(0, len(to_update), batch):
                chunk = to_update[k:k+batch]
                for uid, _, new_inn, _ in chunk:
                    User.objects.filter(id=uid).update(employee_id=new_inn)
                updated += len(chunk)
                logger.info(f"Записан батч #{k//batch + 1}: {len(chunk)} строк")

        logger.info(f"ГОТОВО: обновлено {updated}. Лог: {log_path}; CSV: {backup_csv}")
