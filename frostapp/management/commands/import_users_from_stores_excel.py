import os, sys, re, csv, logging
import datetime as dt
from typing import Optional, Tuple, Dict, List

import pandas as pd
from django.core.management.base import BaseCommand
from django.db import transaction, connection, IntegrityError
from django.db.models import Q, F
from django.utils import timezone

from frostapp.models import (
    User, Department, Position, UKMUser, OpenInSystem, QRCode
)

# ────────────────────────────── Константы для MySQL-коннектов (как во views.py)
MYSQL_CONVERTER_USER = "ukm_import"
MYSQL_CONVERTER_PASS = "jgOKsc2n"
MYSQL_CONVERTER_DB   = "import4"

MYSQL_UKM_HOST   = "192.168.17.237"
MYSQL_UKM_USER   = "ukminfo"
MYSQL_UKM_PASS   = "CtHDbCGK.C"
MYSQL_UKM_DB     = "ukmserver"

# ────────────────────────────── Логгер
def _setup_logger():
    ts = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
    log_dir = os.path.join('/app', 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f'import_users_from_stores_{ts}.log')

    logger = logging.getLogger('import_users_from_stores')
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

# ────────────────────────────── Утилиты/нормализация
def _norm_inn(v: str) -> Optional[str]:
    s = re.sub(r'\D+', '', str(v or ''))
    return s if len(s) in (10, 12) else None

def _norm_str(x) -> str:
    return re.sub(r'\s+', ' ', str(x or '').strip())

def _blank_if_nan(s: str) -> str:
    ss = (s or '').strip().lower()
    return '' if ss in ('nan', 'none', 'null', '-', '—') else s

def _norm_phone(x: str) -> str:
    s = _blank_if_nan(_norm_str(x))
    if not s:
        return ''
    digits = re.sub(r'\D+', '', s)
    if not digits:
        return ''
    if len(digits) == 11 and digits[0] in ('7', '8'):
        return '+7' + digits[1:]
    if len(digits) == 10:
        return '+7' + digits
    return '+' + digits

def _norm_fio(last: str, first: str, patr: str) -> str:
    parts = [str(last or '').strip(), str(first or '').strip(), str(patr or '').strip()]
    fio = ' '.join(p for p in parts if p)
    fio = fio.replace('Ё', 'Е').replace('ё', 'е')
    return re.sub(r'\s+', ' ', fio).strip()

def _fio_key(fio: str) -> str:
    s = _norm_str(fio).lower()
    return s.replace('ё', 'е')

def _norm_role(x) -> Optional[int]:
    s = _blank_if_nan(_norm_str(x))
    if not s:
        return None
    # вытащим число (на случай "1 (кассир)")
    m = re.search(r'\d+', s)
    if not m:
        return None
    try:
        return int(m.group())
    except Exception:
        return None

def _col_letter_to_idx(letter: str) -> int:
    letter = (letter or '').strip().upper()
    if not letter:
        raise ValueError("Пустая буква колонки")
    idx = 0
    for ch in letter:
        if not ('A' <= ch <= 'Z'):
            raise ValueError(f"Неверная буква колонки: {letter}")
        idx = idx * 26 + (ord(ch) - ord('A') + 1)
    return idx - 1

def _auto_headers(df: pd.DataFrame, logger) -> Tuple[int, Dict[str, int]]:
    """
    Ищем заголовки в первых 10 строках:
      ИНН/inn, Фамилия/Имя/Отчество, ИДМагазин/Store/SMSTORE (опц.),
      Email/Почта (опц.), Телефон (опц.), Отдел/Должность (опц.),
      Роль на кассе (опц.).
    Возвращаем (start_row, mapping).
    """
    pats = {
        'inn': re.compile(r'^\s*(инн|inn)\s*$', re.I),
        'last': re.compile(r'^\s*(фам|фамилия|surname|last)\s*$', re.I),
        'first': re.compile(r'^\s*(имя|name)\s*$', re.I),
        'patr': re.compile(r'^\s*(отчество|patronymic|otchestvo|middle)\s*$', re.I),

        'store': re.compile(r'^\s*(идмагазин|idмагазин|smstore|storeid|store)\s*$', re.I),
        'email': re.compile(r'^\s*(e-?mail|почта|email)\s*$', re.I),
        'phone': re.compile(r'^\s*(телефон|phone|mobile|моб)\s*$', re.I),
        'dept': re.compile(r'^\s*(отдел|department)\s*$', re.I),
        'pos': re.compile(r'^\s*(должность|position)\s*$', re.I),

        'role': re.compile(r'^\s*(роль\s*на\s*кассе|role\s*on\s*cash|role|role[_\s-]*id)\s*$', re.I),
    }
    found: Dict[str, Tuple[int, int]] = {}
    rows_scan = min(10, len(df))

    for r in range(rows_scan):
        for c in range(df.shape[1]):
            val = str(df.iat[r, c] if r < len(df) else '').strip()
            if not val:
                continue
            for key, rx in pats.items():
                if key in found:
                    continue
                if rx.match(val):
                    found[key] = (r, c)

    base = {'inn', 'last', 'first', 'patr'}
    if not base.issubset(found.keys()):
        raise ValueError("Не нашёл базовые заголовки (ИНН/Фамилия/Имя/Отчество). Задай колонки явно флагами.")

    start_row = max(found[k][0] for k in base) + 1
    mapping = {k: v[1] for k, v in found.items()}
    logger.info(f"Автозаголовки: {found} → start_row={start_row}, mapping={mapping}")
    return start_row, mapping

def _build_password(inn: str) -> str:
    """KS<ИНН><YYYYMMDD><RANDOM> ровно 40 символов."""
    import random, string
    date_part = dt.datetime.utcnow().strftime("%Y%m%d")
    base = f"KS{inn}{date_part}"
    need = 40 - len(base)
    salt = ''.join(random.choices(string.ascii_uppercase + string.digits, k=max(0, need)))
    return base + salt

def _mysql_pwd(raw: str) -> str:
    """Для OLD_PASSWORD: убираем префикс 'KS' (если есть)."""
    return raw[2:] if raw.startswith("KS") else raw

def _norm_store_token(v: str) -> str:
    """
    Нормализует значение из колонки 'ИДМагазин':
      - '2013.0' -> '2013'
      - ' 2013 ' -> '2013'
      - '2013;2014' обрабатывается внешним сплитом
    Возвращает строку (в т.ч. пустую).
    """
    s = _blank_if_nan(_norm_str(v))
    if not s:
        return ''
    s = s.replace(',', '.')
    m = re.match(r'^\d+(?:\.\d+)?$', s)
    if m:
        try:
            return str(int(float(s)))
        except Exception:
            pass
    if s.isdigit():
        return str(int(s))
    return s

# ────────────────────────────── Маппинги stores
def _load_sm_to_ukm4_map(logger) -> Dict[str, str]:
    sql = "SELECT smstore, ukm4store FROM stores WHERE smstore IS NOT NULL AND ukm4store IS NOT NULL"
    mapping: Dict[str, str] = {}
    with connection.cursor() as cur:
        cur.execute(sql)
        for sm, ukm4 in cur.fetchall():
            sm_s = _norm_store_token(sm)
            ukm_s = _norm_store_token(ukm4)
            if sm_s:
                mapping[sm_s] = ukm_s
    logger.info(f"Загружен маппинг stores: {len(mapping)} пар smstore→ukm4store")
    return mapping

def _load_ukm4ip_map(logger) -> Dict[str, str]:
    """ukm4store → ukm4ip"""
    sql = "SELECT ukm4store, ukm4ip FROM stores WHERE ukm4store IS NOT NULL AND ukm4ip IS NOT NULL"
    mapping: Dict[str, str] = {}
    with connection.cursor() as cur:
        cur.execute(sql)
        for ukm4store, ip in cur.fetchall():
            k = _norm_store_token(ukm4store)
            v = _blank_if_nan(str(ip or '').strip())
            if k and v:
                mapping[k] = v
    logger.info(f"Загружен маппинг stores: {len(mapping)} пар ukm4store→ukm4ip")
    return mapping

# ────────────────────────────── Хэш ИНН (SHA-256 → hex)
def _hash_inn_hex(inn: str) -> str:
    import hashlib
    return hashlib.sha256(inn.encode('utf-8')).hexdigest()

# ────────────────────────────── MySQL helper'ы
def _connect_ukm_central(logger):
    import pymysql
    try:
        return pymysql.connect(
            host=MYSQL_UKM_HOST,
            user=MYSQL_UKM_USER,
            password=MYSQL_UKM_PASS,
            database=MYSQL_UKM_DB,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
    except Exception as e:
        logger.error(f"[MySQL:ukmserver] Ошибка подключения: {e}")
        return None

def _get_trm_employee_id(logger, conn, plain_inn: str, fio: str) -> Optional[int]:
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM trm_in_users WHERE user_inn=%s AND name=%s", (plain_inn, fio))
            row = cur.fetchone()
            return int(row["id"]) if row and row.get("id") is not None else None
    except Exception as e:
        logger.warning(f"[MySQL:ukmserver] get_trm_employee_id error: {e}")
        return None

def _get_next_trm_id(logger, conn) -> int:
    if conn is None:
        return 1
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COALESCE(MAX(id),0)+1 AS next_id FROM trm_in_users")
            row = cur.fetchone() or {}
            return int(row.get("next_id") or 1)
    except Exception as e:
        logger.warning(f"[MySQL:ukmserver] next_id error: {e}")
        return 1

def _connect_store_mysql(logger, host: str):
    import pymysql
    try:
        return pymysql.connect(
            host=host,
            port=3306,
            user=MYSQL_CONVERTER_USER,
            password=MYSQL_CONVERTER_PASS,
            database=MYSQL_CONVERTER_DB,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
    except Exception as e:
        logger.error(f"[MySQL:{host}] Ошибка подключения к import4: {e}")
        return None

def _push_to_converter(logger, host: str, store: int, cashier_id: int, fio: str, inn: str, password_plain: str, role_id: int):
    """
    Вставляем дельтовую строку в import4.users + инкремент в signal.
    Пароль кладём как OLD_PASSWORD(<без 'KS'>), deleted=0.
    """
    conn = _connect_store_mysql(logger, host)
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM `signal` WHERE `signal`='busy'")
            base_version = (cur.fetchone().get('cnt') or 0) + 1

            cur.execute("""
                INSERT INTO users (store, id, name, inn, password, role_id, version, deleted)
                VALUES (%s, %s, %s, %s, OLD_PASSWORD(%s), %s, %s, 0)
            """, (store, cashier_id, fio, inn, _mysql_pwd(password_plain), role_id, base_version))

            cur.execute("INSERT INTO `signal`(`signal`, `version`) VALUES ('incr', %s)", (base_version,))
        logger.info(f"[MySQL:{host}] Пуш в import4: store={store}, id={cashier_id}, role_id={role_id}, version={base_version}")
        return True
    except Exception as e:
        logger.error(f"[MySQL:{host}] Ошибка push в import4 для store={store}: {e}")
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass

# ────────────────────────────── Команда
class Command(BaseCommand):
    help = (
        "Импорт пользователей из Excel (ИНН/ФИО/email/телефон/отдел/должность/Роль). "
        "Если в строке указан ИДМагазин — создаются записи в ukm_users (storeid = stores.ukm4store, role_id из колонки), "
        "open_in_system (system_id=9) и qr_code. "
        "Дополнительно: роль прокидывается в конвертер (import4.users) по ukm4ip из таблицы stores."
    )

    def add_arguments(self, parser):
        parser.add_argument('--file', required=True, help='Путь к .xls/.xlsx внутри контейнера')
        parser.add_argument('--sheet', default=0, help='Имя либо индекс листа (по умолчанию 0)')

        # Явные колонки (буквы). Если не заданы — попытаемся определить автоматически в первых 10 строках.
        parser.add_argument('--col-inn')
        parser.add_argument('--col-last')
        parser.add_argument('--col-first')
        parser.add_argument('--col-patr')
        parser.add_argument('--col-email')
        parser.add_argument('--col-phone')
        parser.add_argument('--col-dept')
        parser.add_argument('--col-pos')
        parser.add_argument('--col-store', help='Колонка ИДМагазин (например, O)')
        parser.add_argument('--col-role', help='Колонка Роль на кассе (например, N / 14 столбец)')

        parser.add_argument('--start-row', type=int, help='1-based; если не указан — авто от заголовков')
        parser.add_argument('--roleid', type=int, default=1, help='roleid по умолчанию, если в Excel пусто')
        parser.add_argument('--batch', type=int, default=300)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        logger, log_path = _setup_logger()

        x_path = opts['file']
        sheet = opts['sheet']
        dry   = opts['dry_run']
        batch = opts['batch']
        default_roleid = int(opts['roleid'])

        logger.info(f"Старт: file={x_path}, sheet={sheet}, dry={dry}, default_roleid={default_roleid}")
        if not os.path.exists(x_path):
            logger.error(f"Файл не найден: {x_path}")
            return

        try:
            df = pd.read_excel(x_path, sheet_name=sheet, header=None, dtype=str)
        except Exception as e:
            logger.error(f"Ошибка чтения Excel: {e}")
            return

        # ── Определение колонок
        col_inn   = opts.get('col_inn')
        col_last  = opts.get('col_last')
        col_first = opts.get('col_first')
        col_patr  = opts.get('col_patr')
        col_email = opts.get('col_email')
        col_phone = opts.get('col_phone')
        col_dept  = opts.get('col_dept')
        col_pos   = opts.get('col_pos')
        col_store = opts.get('col_store')
        col_role  = opts.get('col_role')
        start_row_opt = opts.get('start_row')

        try_auto = not all([col_inn, col_last, col_first, col_patr])
        if try_auto:
            try:
                start_row, mapping = _auto_headers(df, logger)
            except Exception as e:
                logger.error(str(e))
                return
            base_cols = [mapping['inn'], mapping['last'], mapping['first'], mapping['patr']]
            used_cols = base_cols.copy()
            extra = {}
            if 'email' in mapping: extra['email'] = mapping['email']; used_cols.append(mapping['email'])
            if 'phone' in mapping: extra['phone'] = mapping['phone']; used_cols.append(mapping['phone'])
            if 'dept'  in mapping: extra['dept']  = mapping['dept'];  used_cols.append(mapping['dept'])
            if 'pos'   in mapping: extra['pos']   = mapping['pos'];   used_cols.append(mapping['pos'])
            if 'store' in mapping: extra['store'] = mapping['store']; used_cols.append(mapping['store'])
            if 'role'  in mapping: extra['role']  = mapping['role'];  used_cols.append(mapping['role'])
            # переопределяем, если явно передали буквы
            if col_store: extra['store'] = _col_letter_to_idx(col_store)
            if col_email: extra['email'] = _col_letter_to_idx(col_email)
            if col_phone: extra['phone'] = _col_letter_to_idx(col_phone)
            if col_dept:  extra['dept']  = _col_letter_to_idx(col_dept)
            if col_pos:   extra['pos']   = _col_letter_to_idx(col_pos)
            if col_role:  extra['role']  = _col_letter_to_idx(col_role)

            used_cols = base_cols + list(extra.values())
            sub = df.iloc[start_row:, used_cols].copy()
            names = ['inn_raw','last','first','patr']
            for k in ('email','phone','dept','pos','store','role'):
                if k in extra:
                    names.append(k)
            sub.columns = names
            logger.info(f"Авто-режим: start_row={start_row+1}, колонки={names}")
        else:
            ci = _col_letter_to_idx(col_inn)
            cl = _col_letter_to_idx(col_last)
            cf = _col_letter_to_idx(col_first)
            cp = _col_letter_to_idx(col_patr)
            extra = {}
            if col_email: extra['email'] = _col_letter_to_idx(col_email)
            if col_phone: extra['phone'] = _col_letter_to_idx(col_phone)
            if col_dept:  extra['dept']  = _col_letter_to_idx(col_dept)
            if col_pos:   extra['pos']   = _col_letter_to_idx(col_pos)
            if col_store: extra['store'] = _col_letter_to_idx(col_store)
            if col_role:  extra['role']  = _col_letter_to_idx(col_role)
            start_row = (start_row_opt - 1) if start_row_opt and start_row_opt > 0 else 1
            used_cols = [ci, cl, cf, cp] + list(extra.values())
            sub = df.iloc[start_row:, used_cols].copy()
            names = ['inn_raw','last','first','patr']
            for k in ('email','phone','dept','pos','store','role'):
                if k in extra: names.append(k)
            sub.columns = names
            logger.info(f"Явные колонки: start_row={start_row+1}, колонки={names}")

        # ── Нормализация данных
        for c in ('email','phone','dept','pos','store','role'):
            if c not in sub.columns:
                sub[c] = ''
        sub['email'] = sub['email'].map(_norm_str).map(_blank_if_nan)
        sub['phone'] = sub['phone'].map(_norm_phone)
        sub['dept']  = sub['dept'].map(_norm_str).map(_blank_if_nan)
        sub['pos']   = sub['pos'].map(_norm_str).map(_blank_if_nan)
        sub['store'] = sub['store'].map(_norm_store_token)
        sub['role']  = sub['role'].map(_norm_role)

        sub['inn'] = sub['inn_raw'].map(_norm_inn)
        sub['fio'] = sub.apply(lambda r: _norm_fio(r['last'], r['first'], r['patr']), axis=1)
        sub = sub[sub['fio'] != '']
        sub = sub[sub['inn'].notna()]  # выбросим строки с плохим ИНН
        logger.info(f"Подготовлено строк: {len(sub)}")

        # ── Кэши БД
        users = User.objects.all().only('id','full_name','employee_id','encrypted_inn','mail','phone','department_id','position_id')
        by_fio: Dict[str, List[dict]] = {}
        by_inn_plain: Dict[str, int] = {}   # по employee_id (цифровой ИНН)
        by_inn_hash:  Dict[str, int] = {}   # по encrypted_inn, если он уже 64-hex
        for u in users:
            by_fio.setdefault(_fio_key(u.full_name), []).append({
                'id': u.id,
                'employee_id': u.employee_id or '',
                'encrypted_inn': u.encrypted_inn or '',
                'mail': u.mail or '',
                'phone': u.phone or '',
                'department_id': u.department_id,
                'position_id': u.position_id,
                'full_name': u.full_name,
            })
            emp = (u.employee_id or '').strip()
            if emp.isdigit():
                by_inn_plain[emp] = u.id
            enc = (u.encrypted_inn or '').strip().lower()
            if len(enc) == 64 and re.fullmatch(r'[0-9a-f]{64}', enc):
                by_inn_hash[enc] = u.id

        dep_map = {_fio_key(d.name): d.id for d in Department.objects.all().only('id','name')}
        pos_map = {_fio_key(p.name): p.id for p in Position.objects.all().only('id','name')}

        sm_to_ukm4 = _load_sm_to_ukm4_map(logger)
        ukm4_to_ip = _load_ukm4ip_map(logger)

        logger.info(f"Кэши: users={users.count()} (fio_keys={len(by_fio)}), deps={len(dep_map)}, pos={len(pos_map)}, stores_map={len(sm_to_ukm4)}, ip_map={len(ukm4_to_ip)}")

        # ── План работ
        create_users = []      # (fio, inn, email, phone, dep_id, pos_id)
        update_inn   = []      # (user_id, old_inn, new_inn, fio)
        phone_fill   = []      # (user_id, phone)
        create_ukm   = []      # (user_id, storeid_ukm4, roleid_row)
        update_ukm   = []      # (user_id, storeid_ukm4, roleid_row) — если роль изменилась
        create_open  = []      # (user_id, username, password)
        create_qr    = []      # (user_id, qr_data, created_at, expires_at)
        hash_sync    = []      # (user_id, inn, fio) — синхронизировать encrypted_inn → SHA-256(inn)

        # Для пуша в конвертер: (user_id, fio, inn, storeid_ukm4, roleid_row)
        mysql_jobs: Dict[Tuple[int, int], Tuple[int, str, str, int, int]] = {}

        stats = {'dupl_fio':0, 'not_found_dep':0, 'not_found_pos':0, 'no_store_map':0}

        now = timezone.now()
        for i, r in sub.iterrows():
            inn = r['inn']
            fio = r['fio']
            email = r['email']
            phone = r['phone']
            dep_id = dep_map.get(_fio_key(r['dept'])) if r['dept'] else None
            pos_id = pos_map.get(_fio_key(r['pos'])) if r['pos'] else None
            roleid_row = r['role'] if r['role'] is not None else default_roleid

            if r['dept'] and dep_id is None:
                stats['not_found_dep'] += 1
                logger.warning(f"[{i}] Отдел не найден: '{r['dept']}' (fio={fio})")
            if r['pos'] and pos_id is None:
                stats['not_found_pos'] += 1
                logger.warning(f"[{i}] Должность не найдена: '{r['pos']}' (fio={fio})")

            # Ищем пользователя
            cand = by_fio.get(_fio_key(fio), [])
            user_id = None
            if len(cand) == 1:
                user_id = cand[0]['id']
                old_inn = cand[0]['employee_id'] or ''
                old_phone = cand[0]['phone'] or ''
                # синхронизация encrypted_inn → hash
                hash_sync.append((user_id, inn, fio))
                # телефон, если пуст
                if not old_phone and phone:
                    phone_fill.append((user_id, phone))
                # обновить ИНН, если отличается
                if old_inn != inn:
                    update_inn.append((user_id, old_inn, inn, fio))
            elif len(cand) > 1:
                stats['dupl_fio'] += 1
                logger.warning(f"[{i}] Дубликаты ФИО в БД: '{fio}', ids={[c['id'] for c in cand]} → пропуск")
            else:
                # не нашли по ФИО — попробуем по ИНН: сперва plain, затем hash
                uid_by_inn = by_inn_plain.get(inn) or by_inn_hash.get(_hash_inn_hex(inn))
                if uid_by_inn:
                    user_id = uid_by_inn
                    hash_sync.append((user_id, inn, fio))
                    urow = next((c for v in by_fio.values() for c in v if c['id']==uid_by_inn), None)
                    if urow and not urow['phone'] and phone:
                        phone_fill.append((user_id, phone))
                else:
                    # план на создание
                    if dep_id is None or pos_id is None:
                        logger.warning(f"[{i}] Нет dep/pos → не создаю пользователя '{fio}'")
                    else:
                        create_users.append((fio, inn, email, phone, dep_id, pos_id))

            # обработка магазинов (ИДМагазин)
            store_raw = r['store']
            if user_id and store_raw:
                tokens = re.split(r'[;, ]+', store_raw.strip())
                tokens = [_norm_store_token(t) for t in tokens if t]
                for sm in tokens:
                    if not sm:
                        continue
                    ukm4 = sm_to_ukm4.get(sm)
                    if not ukm4:
                        stats['no_store_map'] += 1
                        logger.warning(f"[{i}] Нет маппинга SMSTORE→UKM4STORE: '{sm}' (fio={fio})")
                        continue
                    try:
                        storeid_final = int(str(ukm4).strip())
                    except Exception:
                        logger.warning(f"[{i}] ukm4store не число: '{ukm4}' (sm={sm}, fio={fio})")
                        continue

                    # ukm_users: создаём или обновляем роль при расхождении
                    existing = UKMUser.objects.filter(user_id=user_id, storeid=storeid_final).first()
                    if existing is None:
                        create_ukm.append((user_id, storeid_final, roleid_row))
                    else:
                        if existing.roleid != roleid_row:
                            update_ukm.append((user_id, storeid_final, roleid_row))

                    # работу в конвертер складываем в «задания»
                    mysql_jobs[(user_id, storeid_final)] = (user_id, fio, inn, storeid_final, roleid_row)

                # open_in_system — если нет записи с system_id=9 → создадим
                open_exists = OpenInSystem.objects.filter(user_id=user_id, system_id=9).exists()
                if not open_exists:
                    pwd = _build_password(inn)
                    create_open.append((user_id, fio, pwd))
                    create_qr.append((user_id, pwd, now, now + dt.timedelta(days=1)))

        logger.info(
            f"ПЛАН: create_users={len(create_users)}, update_inn={len(update_inn)}, "
            f"phone_fill={len(phone_fill)}, create_ukm={len(create_ukm)}, update_ukm={len(update_ukm)}, "
            f"create_open={len(create_open)}, create_qr={len(create_qr)}, mysql_jobs={len(mysql_jobs)}; "
            f"warns: dupl_fio={stats['dupl_fio']}, dep_nf={stats['not_found_dep']}, "
            f"pos_nf={stats['not_found_pos']}, no_store_map={stats['no_store_map']}"
        )

        # CSV планы
        ts = dt.datetime.now().strftime('%Y%m%d_%H%M%S')
        base = '/app/logs'
        os.makedirs(base, exist_ok=True)
        files = {
            'users_create': os.path.join(base, f'users_create_{ts}.csv'),
            'inn_update':   os.path.join(base, f'users_inn_update_{ts}.csv'),
            'phone_fill':   os.path.join(base, f'users_phone_fill_{ts}.csv'),
            'ukm_users':    os.path.join(base, f'ukm_users_create_{ts}.csv'),
            'ukm_users_upd':os.path.join(base, f'ukm_users_update_{ts}.csv'),
            'open':         os.path.join(base, f'open_in_system_create_{ts}.csv'),
            'qr':           os.path.join(base, f'qr_code_create_{ts}.csv'),
            'import4':      os.path.join(base, f'import4_push_{ts}.csv'),
        }
        try:
            with open(files['users_create'],'w',newline='',encoding='utf-8') as f:
                wr = csv.writer(f, delimiter=';'); wr.writerow(['full_name','employee_id','email','phone','department_id','position_id'])
                for row in create_users: wr.writerow(row)
            with open(files['inn_update'],'w',newline='',encoding='utf-8') as f:
                wr = csv.writer(f, delimiter=';'); wr.writerow(['user_id','old_inn','new_inn','fio'])
                for row in update_inn: wr.writerow(row)
            with open(files['phone_fill'],'w',newline='',encoding='utf-8') as f:
                wr = csv.writer(f, delimiter=';'); wr.writerow(['user_id','phone'])
                for row in phone_fill: wr.writerow(row)
            with open(files['ukm_users'],'w',newline='',encoding='utf-8') as f:
                wr = csv.writer(f, delimiter=';'); wr.writerow(['user_id','storeid(ukm4)','roleid'])
                for row in create_ukm: wr.writerow(row)
            with open(files['ukm_users_upd'],'w',newline='',encoding='utf-8') as f:
                wr = csv.writer(f, delimiter=';'); wr.writerow(['user_id','storeid(ukm4)','roleid(new)'])
                for row in update_ukm: wr.writerow(row)
            with open(files['open'],'w',newline='',encoding='utf-8') as f:
                wr = csv.writer(f, delimiter=';'); wr.writerow(['user_id','username','password','system_id','status'])
                for uid, uname, pwd in create_open: wr.writerow([uid, uname, pwd, 9, True])
            with open(files['qr'],'w',newline='',encoding='utf-8') as f:
                wr = csv.writer(f, delimiter=';'); wr.writerow(['user_id','qr_data','created_at','expires_at'])
                for uid, pwd, c, e in create_qr: wr.writerow([uid, pwd, c.isoformat(), e.isoformat()])
            with open(files['import4'],'w',newline='',encoding='utf-8') as f:
                wr = csv.writer(f, delimiter=';'); wr.writerow(['user_id','fio','inn','storeid(ukm4)','roleid'])
                for (_uid, _sid), (uid, fio, inn, sid, rid) in mysql_jobs.items():
                    wr.writerow([uid, fio, inn, sid, rid])
            logger.info(f"CSV планы записаны: {files}")
        except Exception as e:
            logger.warning(f"Не удалось записать часть CSV: {e}")

        if dry:
            logger.info("dry-run: без записи в БД.")
            return

        # ── Запись в PostgreSQL
        created_users = 0
        updated_inn_cnt = 0
        filled_phone  = 0
        created_ukm   = 0
        updated_ukm   = 0
        created_open  = 0
        created_qr    = 0
        inn_conflicts: List[tuple] = []  # (user_id, fio, new_inn, owner_user_id, owner_fio)

        with transaction.atomic():
            # 1) Создание пользователей (ignore_conflicts, потом «дотягиваем» недостающее)
            for k in range(0, len(create_users), batch):
                chunk = create_users[k:k+batch]
                objs = []
                now_ts = timezone.now()
                for fio, inn, email, phone, dep_id, pos_id in chunk:
                    objs.append(User(
                        employee_id=inn,
                        encrypted_inn=_hash_inn_hex(inn),     # храним хэш
                        full_name=fio,
                        mail=email or '',
                        phone=phone or '',
                        department_id=dep_id,
                        position_id=pos_id,
                        active=True,
                        tg_status=False,
                        created_at=now_ts,
                        updated_at=now_ts
                    ))
                try:
                    created = User.objects.bulk_create(objs, batch_size=batch, ignore_conflicts=True)
                    created_users += len(created)
                except IntegrityError:
                    for o in objs:
                        try:
                            o.save(force_insert=True)
                            created_users += 1
                        except IntegrityError:
                            pass

                # Дотянуть пустые поля у уже существующих по employee_id
                for o in objs:
                    User.objects.filter(employee_id=o.employee_id).update(
                        mail=o.mail or F('mail'),
                        department_id=o.department_id or F('department_id'),
                        position_id=o.position_id or F('position_id'),
                    )
                    (User.objects
                        .filter(employee_id=o.employee_id)
                        .filter(Q(phone__isnull=True)|Q(phone=''))
                        .update(phone=o.phone))

            # 2) Обновление ИНН (и encrypted_inn → hash)
            for k in range(0, len(update_inn), batch):
                chunk = update_inn[k:k+batch]
                for uid, _, inn_new, fio in chunk:
                    target_hash = _hash_inn_hex(inn_new)
                    owner = (User.objects
                             .exclude(id=uid)
                             .filter(encrypted_inn=target_hash)
                             .values('id', 'full_name')
                             .first())
                    if owner:
                        logger.warning(
                            f"[WRITE] Конфликт encrypted_inн (hash) для ИНН='{inn_new}' у user_id={uid} ('{fio}'); "
                            f"уже занят user_id={owner['id']} ('{owner['full_name']}'). Обновляю только employee_id."
                        )
                        User.objects.filter(id=uid).update(employee_id=inn_new)
                        inn_conflicts.append((uid, fio, inn_new, owner['id'], owner['full_name']))
                        updated_inn_cnt += 1
                    else:
                        User.objects.filter(id=uid).update(
                            employee_id=inn_new,
                            encrypted_inn=target_hash
                        )
                        updated_inn_cnt += 1

            # 3) Телефон (только пустые)
            for k in range(0, len(phone_fill), batch):
                for uid, phone in phone_fill[k:k+batch]:
                    affected = (User.objects
                                .filter(id=uid)
                                .filter(Q(phone__isnull=True) | Q(phone=''))
                                .update(phone=phone))
                    filled_phone += affected

            # 3.5) Синхронизация encrypted_inn → SHA-256 по данным Excel, даже если ИНН не менялся
            if hash_sync:
                sync_map: Dict[int, Tuple[str, str]] = {}
                for uid, inn, fio in hash_sync:
                    sync_map[uid] = (inn, fio)
                for uid, (inn, fio) in sync_map.items():
                    target_hash = _hash_inn_hex(inn)
                    owner = (User.objects
                             .exclude(id=uid)
                             .filter(encrypted_inn=target_hash)
                             .values('id','full_name')
                             .first())
                    if owner:
                        logger.warning(
                            f"[WRITE] Пропуск sync encrypted_inн для user_id={uid} ('{fio}'): "
                            f"хэш уже у user_id={owner['id']} ('{owner['full_name']}')."
                        )
                        continue
                    cur = (User.objects.filter(id=uid)
                           .values_list('encrypted_inn', flat=True)
                           .first()) or ''
                    if cur.strip().lower() != target_hash:
                        User.objects.filter(id=uid).update(encrypted_inn=target_hash)

            # 4) ukm_users (create/update роли)
            for k in range(0, len(create_ukm), batch):
                objs = [UKMUser(user_id=uid, roleid=rid, storeid=sid, version=1)
                        for (uid, sid, rid) in create_ukm[k:k+batch]]
                for o in objs:
                    if not UKMUser.objects.filter(user_id=o.user_id, storeid=o.storeid).exists():
                        o.save()
                        created_ukm += 1
            for k in range(0, len(update_ukm), batch):
                for uid, sid, rid in update_ukm[k:k+batch]:
                    affected = UKMUser.objects.filter(user_id=uid, storeid=sid).update(roleid=rid)
                    updated_ukm += affected

            # 5) open_in_system
            for k in range(0, len(create_open), batch):
                for uid, username, pwd in create_open[k:k+batch]:
                    if not OpenInSystem.objects.filter(user_id=uid, system_id=9).exists():
                        OpenInSystem.objects.create(
                            user_id=uid, username=username, password=pwd, system_id=9, status=True
                        )
                        created_open += 1

            # 6) qr_code
            for k in range(0, len(create_qr), batch):
                for uid, pwd, created_at, expires_at in create_qr[k:k+batch]:
                    have_active = QRCode.objects.filter(
                        user_id=uid, expires_at__gt=timezone.now()
                    ).exists()
                    if not have_active:
                        QRCode.objects.create(
                            user_id=uid, qr_data=pwd, created_at=created_at, expires_at=expires_at
                        )
                        created_qr += 1

        # ── Пуш в конвертер (import4) — вне транзакции PostgreSQL
        # Готовим пароли: для каждого user_id возьмём существующий open_in_system.password (system_id=9),
        # если его нет — сформируем временно (не сохраняем в PG).
        pwd_cache: Dict[int, str] = {}
        need_pw_for = {uid for (uid, _sid) in mysql_jobs.keys()}
        for uid in need_pw_for:
            p = (OpenInSystem.objects
                 .filter(user_id=uid, system_id=9)
                 .values_list('password', flat=True)
                 .first())
            if not p:
                # fallback — сгенерим локально от employee_id
                inn_plain = (User.objects.filter(id=uid).values_list('employee_id', flat=True).first()) or ''
                p = _build_password(inn_plain) if inn_plain else _build_password('0000000000')
            pwd_cache[uid] = p

        # Вычислим cashier_id (trm_in_users.id) для каждого user (глобально)
        ukm_central = _connect_ukm_central(logger)
        next_free_trm_id = _get_next_trm_id(logger, ukm_central)
        cashier_id_by_user: Dict[int, int] = {}

        for (uid, sid), (uid_, fio, inn, storeid, rid) in mysql_jobs.items():
            # cashier_id: возьмём существующий, иначе выделим новый
            if uid not in cashier_id_by_user:
                existing_id = _get_trm_employee_id(logger, ukm_central, inn, fio)
                if existing_id is not None:
                    cashier_id_by_user[uid] = existing_id
                else:
                    cashier_id_by_user[uid] = next_free_trm_id
                    next_free_trm_id += 1

        # Пошлём в import4 по каждому store
        pushed_ok = 0
        for (uid, sid), (uid_, fio, inn, storeid, rid) in mysql_jobs.items():
            host = _blank_if_nan(ukm4_to_ip.get(str(storeid), ''))
            if not host:
                logger.error(f"[import4] Не найден ukm4ip для ukm4store={storeid} → пропуск.")
                continue
            pwd_plain = pwd_cache.get(uid) or _build_password(inn)
            cashier_id = cashier_id_by_user.get(uid) or _get_next_trm_id(logger, ukm_central)
            if _push_to_converter(logger, host, storeid, cashier_id, fio, inn, pwd_plain, rid):
                pushed_ok += 1

        try:
            if ukm_central:
                ukm_central.close()
        except Exception:
            pass

        # выгрузим конфликты encrypted_inn, если были
        if inn_conflicts:
            conflicts_path = os.path.join('/app/logs', f'users_inn_conflicts_{ts}.csv')
            try:
                with open(conflicts_path, 'w', newline='', encoding='utf-8') as f:
                    wr = csv.writer(f, delimiter=';')
                    wr.writerow(['user_id', 'fio', 'requested_inn', 'owner_user_id', 'owner_fio'])
                    for row in inn_conflicts:
                        wr.writerow(row)
                logger.info(f"Зафиксированы конфликты encrypted_inn: {conflicts_path} (шт: {len(inn_conflicts)})")
            except Exception as e:
                logger.warning(f"Не удалось записать CSV конфликтов encrypted_inн: {e}")

        logger.info(
            f"ГОТОВО. users: создано={created_users}, inn_обновлено={updated_inn_cnt}, "
            f"phone_добавлено={filled_phone}; ukm_users создано={created_ukm}, обновлено_роль={updated_ukm}; "
            f"open_in_system создано={created_open}; qr_code создано={created_qr}; "
            f"в import4 отправлено={pushed_ok}/{len(mysql_jobs)}. Лог: {log_path}"
        )
