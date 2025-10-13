import os, sys, re, csv
import logging
import datetime as dt
from typing import Optional, Tuple, Dict

import pandas as pd
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from frostapp.models import User, Department, Position  # managed=False

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
    fio = fio.replace('Ё', 'Е').replace('ё', 'е')
    return re.sub(r'\s+', ' ', fio).strip()

def _norm_name_key(name: str) -> str:
    n = str(name or '').strip().lower()
    n = n.replace('ё', 'е')
    n = re.sub(r'\s+', ' ', n)
    return n

def _norm_str(x) -> str:
    return re.sub(r'\s+', ' ', str(x or '').strip())

def _blank_if_nan(s: str) -> str:
    ss = (s or '').strip().lower()
    return '' if ss in ('nan', 'none', 'null', '-') else s

def _norm_phone(x: str) -> str:
    """
    Лёгкая нормализация: оставляем цифры, приводим российские номера к +7,
    иначе возвращаем '+' + цифры, либо пусто.
    """
    s = _blank_if_nan(_norm_str(x))
    if not s:
        return ''
    digits = re.sub(r'\D+', '', s)
    if not digits:
        return ''
    # Россия: 11 цифр, начинается на 8 или 7
    if len(digits) == 11 and digits[0] == '8':
        return '+7' + digits[1:]
    if len(digits) == 11 and digits[0] == '7':
        return '+7' + digits[1:]
    if len(digits) == 10:  # без кода страны
        return '+7' + digits
    # Иначе просто с плюсом
    return '+' + digits

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
    Ищем 'инн/inn', 'фамилия/surname/last', 'имя/name', 'отчество/patronymic'
    Возвращаем start_row (первая строка с данными) и mapping словарь.
    """
    patterns = {
        'inn':      re.compile(r'^\s*(инн|inn)\s*$', re.I),
        'last':     re.compile(r'^\s*(фам|фамилия|surname|last)\s*$', re.I),
        'first':    re.compile(r'^\s*(имя|name)\s*$', re.I),
        'patr':     re.compile(r'^\s*(отчество|patronymic|otchestvo|middle)\s*$', re.I),
    }
    max_scan_rows = min(10, len(df))
    found: Dict[str, Tuple[int, int]] = {}

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
    help = ("Обновляет users.employee_id по Excel и, при необходимости, "
            "создаёт недостающих пользователей с email/phone/department/position. "
            "Телефон для существующих пользователей добавляется только если он пустой.")

    def add_arguments(self, parser):
        parser.add_argument('--file', required=True, help='Путь к Excel (.xls/.xlsx) внутри контейнера')
        parser.add_argument('--sheet', default=0, help='Лист: имя или индекс (по умолчанию 0)')

        # Явные колонки и первая строка с данными (1-based)
        parser.add_argument('--col-inn', help='Колонка ИНН (буква, напр. B)')
        parser.add_argument('--col-last', help='Колонка Фамилия (буква, напр. C)')
        parser.add_argument('--col-first', help='Колонка Имя (буква, напр. D)')
        parser.add_argument('--col-patr', help='Колонка Отчество (буква, напр. E)')
        parser.add_argument('--col-email', help='Колонка Email (буква)')
        parser.add_argument('--col-phone', help='Колонка Телефон (буква)')  # <-- НОВОЕ
        parser.add_argument('--col-dept', help='Колонка Отдел (буква)')
        parser.add_argument('--col-pos', help='Колонка Должность (буква)')
        parser.add_argument('--start-row', type=int, help='Первая строка с данными (1-based). Если не указана — авто.')

        parser.add_argument('--dry-run', action='store_true', help='Только показать изменения, без записи')
        parser.add_argument('--batch', type=int, default=200, help='Размер батча для записи (по умолчанию 200)')
        parser.add_argument('--backup-csv', default=None, help='Путь для CSV с планируемыми изменениями')
        parser.add_argument('--create-missing', action='store_true', help='Создавать пользователей, если не найдены по ФИО')

    def handle(self, *args, **opts):
        logger, log_path = _setup_logger()
        x_path = opts['file']
        sheet = opts['sheet']
        dry = opts['dry_run']
        batch = opts['batch']
        backup_csv = opts['backup_csv']
        allow_create = opts['create_missing']

        logger.info(f"Старт: file={x_path}, sheet={sheet}, dry_run={dry}, batch={batch}, create_missing={allow_create}")
        if not os.path.exists(x_path):
            logger.error(f"Файл не найден: {x_path}")
            return

        # Читаем без заголовков
        try:
            df = pd.read_excel(x_path, sheet_name=sheet, header=None, dtype=str)
        except Exception as e:
            logger.error(f"Не удалось прочитать Excel: {e}")
            return

        # Определяем колонки
        col_inn   = opts.get('col_inn')
        col_last  = opts.get('col_last')
        col_first = opts.get('col_first')
        col_patr  = opts.get('col_patr')
        col_email = opts.get('col_email')
        col_phone = opts.get('col_phone')   # <-- НОВОЕ
        col_dept  = opts.get('col_dept')
        col_pos   = opts.get('col_pos')
        start_row_opt = opts.get('start_row')

        if all([col_inn, col_last, col_first, col_patr]):
            ci = _col_letter_to_idx(col_inn)
            cl = _col_letter_to_idx(col_last)
            cf = _col_letter_to_idx(col_first)
            cp = _col_letter_to_idx(col_patr)
            extra_idxs = {}
            if col_email: extra_idxs['email'] = _col_letter_to_idx(col_email)
            if col_phone: extra_idxs['phone'] = _col_letter_to_idx(col_phone)  # <-- НОВОЕ
            if col_dept:  extra_idxs['dept']  = _col_letter_to_idx(col_dept)
            if col_pos:   extra_idxs['pos']   = _col_letter_to_idx(col_pos)

            start_row = (start_row_opt - 1) if start_row_opt and start_row_opt > 0 else 1
            logger.info(
                f"Колонки: inn={col_inn}({ci}), last={col_last}({cl}), first={col_first}({cf}), "
                f"patr={col_patr}({cp}), email={col_email}, phone={col_phone}, dept={col_dept}, pos={col_pos}; "
                f"start_row={start_row+1}"
            )

            used_cols = [ci, cl, cf, cp] + list(extra_idxs.values())
            sub = df.iloc[start_row:, used_cols].copy()

            col_names = ['inn_raw', 'last', 'first', 'patr']
            for k in ('email', 'phone', 'dept', 'pos'):
                if k in extra_idxs:
                    col_names.append(k)
            sub.columns = col_names
        else:
            # авто-заголовки только для базовых 4-х; остальные — буквами
            try:
                start_row, mapping = _auto_locate_headers(df, logger)
                base_cols = [mapping['inn'], mapping['last'], mapping['first'], mapping['patr']]
                used_cols = base_cols.copy()
                extra_idxs = {}
                if col_email: extra_idxs['email'] = _col_letter_to_idx(col_email); used_cols.append(extra_idxs['email'])
                if col_phone: extra_idxs['phone'] = _col_letter_to_idx(col_phone); used_cols.append(extra_idxs['phone'])  # <-- НОВОЕ
                if col_dept:  extra_idxs['dept']  = _col_letter_to_idx(col_dept);  used_cols.append(extra_idxs['dept'])
                if col_pos:   extra_idxs['pos']   = _col_letter_to_idx(col_pos);   used_cols.append(extra_idxs['pos'])

                sub = df.iloc[start_row:, used_cols].copy()
                col_names = ['inn_raw', 'last', 'first', 'patr']
                for k in ('email', 'phone', 'dept', 'pos'):
                    if k in extra_idxs:
                        col_names.append(k)
                sub.columns = col_names
            except Exception as e:
                logger.error(str(e))
                return

        # Нормализация
        if 'email' not in sub.columns: sub['email'] = ''
        if 'phone' not in sub.columns: sub['phone'] = ''   # <-- НОВОЕ
        if 'dept'  not in sub.columns: sub['dept']  = ''
        if 'pos'   not in sub.columns: sub['pos']   = ''

        sub['email'] = sub['email'].map(_norm_str).map(_blank_if_nan)
        sub['phone'] = sub['phone'].map(_norm_phone)       # <-- НОВОЕ (с нормализацией)
        sub['dept']  = sub['dept'].map(_norm_str).map(_blank_if_nan)
        sub['pos']   = sub['pos'].map(_norm_str).map(_blank_if_nan)

        sub['inn_norm'] = sub['inn_raw'].map(_norm_inn)
        sub['fio'] = sub.apply(lambda r: _norm_fio(r['last'], r['first'], r['patr']), axis=1)
        sub = sub[sub['fio'] != '']  # выкинем пустые ФИО
        total_rows = len(sub)
        logger.info(f"Подготовлено строк к обработке: {total_rows}")

        # Кэш пользователей из БД (по нормализованному ФИО), тянем и phone
        qs = User.objects.all().only('id', 'full_name', 'employee_id', 'phone')
        db_map = {}
        for u in qs:
            db_map.setdefault(_norm_name_key(u.full_name), []).append({
                'id': u.id,
                'employee_id': u.employee_id,
                'phone': (u.phone or '').strip()
            })

        # Кэш отделов/должностей
        dep_map = {_norm_name_key(d.name): d.id for d in Department.objects.all().only('id', 'name')}
        pos_map = {_norm_name_key(p.name): p.id for p in Position.objects.all().only('id', 'name')}
        logger.info(
            f"Пользователи={qs.count()} (уник. ключей ФИО={len(db_map)}); "
            f"Отделов={len(dep_map)}, Должностей={len(pos_map)}"
        )

        to_update = []       # (user_id, old_inn, new_inn, fio)
        to_create = []       # (fio, inn, email, phone, dep_id, pos_id)
        to_phone_only = []   # (user_id, phone) — для существующих, если phone пуст и в Excel есть
        stats = {'unchanged': 0, 'bad_inn': 0, 'not_found': 0, 'duplicate': 0, 'no_dept': 0, 'no_pos': 0}

        for i, row in sub.iterrows():
            fio = row['fio']
            inn_new = row['inn_norm']
            email = row['email']
            phone = row['phone']
            dep_name = row['dept']
            pos_name = row['pos']

            if not inn_new:
                stats['bad_inn'] += 1
                logger.warning(f"[{i}] ПЛОХОЙ ИНН: fio='{fio}', raw='{row['inn_raw']}' → пропуск")
                continue

            key = _norm_name_key(fio)
            matches = db_map.get(key, [])

            if not matches:
                # Создание, если разрешено и заданы отдел/должность
                if not allow_create:
                    stats['not_found'] += 1
                    logger.warning(f"[{i}] НЕ НАЙДЕН в БД: '{fio}' → пропуск (create_missing=FALSE)")
                    continue

                dep_id = dep_map.get(_norm_name_key(dep_name)) if dep_name else None
                pos_id = pos_map.get(_norm_name_key(pos_name)) if pos_name else None

                if dep_id is None:
                    stats['no_dept'] += 1
                    logger.warning(f"[{i}] ОТДЕЛ не найден по имени: '{dep_name}' (fio='{fio}') → пропуск создания")
                    continue
                if pos_id is None:
                    stats['no_pos'] += 1
                    logger.warning(f"[{i}] ДОЛЖНОСТЬ не найдена по имени: '{pos_name}' (fio='{fio}') → пропуск создания")
                    continue

                to_create.append((fio, inn_new, email, phone, dep_id, pos_id))
                logger.info(f"[{i}] СОЗДАНИЕ: '{fio}', inn={inn_new}, email='{email}', phone='{phone}', dep_id={dep_id}, pos_id={pos_id}")
                continue

            if len(matches) > 1:
                stats['duplicate'] += 1
                logger.warning(f"[{i}] ДУБЛИКАТЫ ФИО в БД: '{fio}', ids={[m['id'] for m in matches]} → пропуск")
                continue

            user_id = matches[0]['id']
            old_inn_str = str(matches[0]['employee_id'] or '')
            old_phone = matches[0]['phone']

            # Дополнительно: если в БД телефона нет, а в Excel есть — запланируем обновление телефона
            if not old_phone and phone:
                to_phone_only.append((user_id, phone))
                logger.info(f"[{i}] ДОП. ТЕЛЕФОН для id={user_id} '{fio}': '{phone}' (в БД был пуст)")

            if old_inn_str == inn_new:
                stats['unchanged'] += 1
                logger.info(f"[{i}] БЕЗ ИЗМЕНЕНИЙ id={user_id} '{fio}' → ИНН уже {inn_new}")
                continue

            to_update.append((user_id, old_inn_str, inn_new, fio))
            logger.info(f"[{i}] ОБНОВЛЕНИЕ id={user_id} '{fio}': {old_inn_str} → {inn_new}")

        logger.info(
            f"ИТОГО: план обновлений={len(to_update)}, план созданий={len(to_create)}, "
            f"телефонов_к_добавлению_у_существующих={len(to_phone_only)}, "
            f"без_изменений={stats['unchanged']}, не_найдено={stats['not_found']}, "
            f"дубликаты={stats['duplicate']}, плохой_инн={stats['bad_inn']}, "
            f"нет_отдела={stats['no_dept']}, нет_должности={stats['no_pos']}"
        )

        # CSV бэкапы
        ts = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
        if backup_csv is None:
            backup_csv = os.path.join('/app', 'logs', f'fix_employee_ids_plan_{ts}.csv')
        create_csv = os.path.join('/app', 'logs', f'create_users_plan_{ts}.csv')
        phone_csv  = os.path.join('/app', 'logs', f'phone_fill_plan_{ts}.csv')

        try:
            with open(backup_csv, 'w', newline='', encoding='utf-8') as f:
                wr = csv.writer(f, delimiter=';')
                wr.writerow(['user_id', 'full_name', 'old_employee_id', 'new_employee_id'])
                for uid, old_inn, new_inn, fio in to_update:
                    wr.writerow([uid, fio, old_inn, new_inn])
            logger.info(f"Бэкап плана обновлений: {backup_csv}")
        except Exception as e:
            logger.warning(f"Не удалось записать backup CSV (updates): {e}")

        try:
            with open(create_csv, 'w', newline='', encoding='utf-8') as f:
                wr = csv.writer(f, delimiter=';')
                wr.writerow(['full_name', 'employee_id', 'email', 'phone', 'department_id', 'position_id'])
                for fio, inn, email, phone, dep_id, pos_id in to_create:
                    wr.writerow([fio, inn, email, phone, dep_id, pos_id])
            logger.info(f"Бэкап плана созданий: {create_csv}")
        except Exception as e:
            logger.warning(f"Не удалось записать backup CSV (creates): {e}")

        try:
            with open(phone_csv, 'w', newline='', encoding='utf-8') as f:
                wr = csv.writer(f, delimiter=';')
                wr.writerow(['user_id', 'phone'])
                for uid, phone in to_phone_only:
                    wr.writerow([uid, phone])
            logger.info(f"Бэкап плана телефонов: {phone_csv}")
        except Exception as e:
            logger.warning(f"Не удалось записать backup CSV (phones): {e}")

        if dry:
            logger.info("dry-run: записи в БД не выполнялись.")
            return

        # Запись батчами
        updated = 0
        created = 0
        phone_filled = 0
        with transaction.atomic():
            # 1) Обновления employee_id
            for k in range(0, len(to_update), batch):
                chunk = to_update[k:k + batch]
                for uid, _, new_inn, _ in chunk:
                    User.objects.filter(id=uid).update(employee_id=new_inn)
                updated += len(chunk)
                logger.info(f"[WRITE] Обновлён батч #{k // batch + 1}: {len(chunk)} записей")

            # 2) Телефоны для существующих пользователей (только если пусто)
            for k in range(0, len(to_phone_only), batch):
                chunk = to_phone_only[k:k + batch]
                for uid, phone in chunk:
                    affected = (User.objects
                                .filter(id=uid)
                                .filter(Q(phone__isnull=True) | Q(phone=''))
                                .update(phone=phone))
                    phone_filled += affected
                logger.info(f"[WRITE] Добавлены телефоны (только пустые) в батче #{k // batch + 1}: {len(chunk)} записей")

            # 3) Создания пользователей c обработкой конфликтов по encrypted_inn
            now = timezone.now()
            for k in range(0, len(to_create), batch):
                chunk = to_create[k:k + batch]
                objs = []
                for fio, inn, email, phone, dep_id, pos_id in chunk:
                    objs.append(User(
                        employee_id=inn,
                        encrypted_inn=inn,  # как в register_cashier — без хэша
                        full_name=fio,
                        mail=email or '',
                        phone=phone or '',
                        department_id=dep_id,
                        position_id=pos_id,
                        active=True,
                        tg_status=False,
                        created_at=now,
                        updated_at=now
                    ))
                created_objs = User.objects.bulk_create(objs, batch_size=batch, ignore_conflicts=True)
                created += len(created_objs)
                logger.info(f"[WRITE] Создан батч пользователей #{k // batch + 1}: {len(created_objs)} записей (с учётом конфликтов)")

                # Для тех, кто уже существовал с таким же INN — дотянем поля (email/dep/pos/phone только если их нет)
                for o in objs:
                    # email/dep/pos — старое поведение (перезаполняем только если пусто с помощью F?)
                    User.objects.filter(encrypted_inn=o.encrypted_inn).update(
                        mail=o.mail or F('mail'),
                        department_id=o.department_id or F('department_id'),
                        position_id=o.position_id or F('position_id'),
                    )
                    # телефон — только если пусто
                    User.objects.filter(encrypted_inn=o.encrypted_inn) \
                        .filter(Q(phone__isnull=True) | Q(phone='')) \
                        .update(phone=o.phone)

                logger.info(f"[WRITE] Дотянули email/dep/pos (и phone при пустом) для существующих записей в батче #{k // batch + 1}")

        logger.info(
            f"ГОТОВО: обновлено={updated}, создано={created}, телефонов_добавлено_к_существующим={phone_filled}. "
            f"Лог: {log_path}; CSV updates: {backup_csv}; CSV creates: {create_csv}; CSV phones: {phone_csv}"
        )
