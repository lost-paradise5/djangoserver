import hashlib
import json
import os
import logging
import requests
import time
from typing import Tuple, Optional
import random
import string
import datetime
import pymysql
import xml.etree.ElementTree as ET
import cx_Oracle
import re   
from django.http import JsonResponse
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone


from .models import Queue, MODUL_logs, User, UKMUser, OpenInSystem, QRCode, Department, Position, Store

_HEX = set("0123456789abcdefABCDEF")



def get_store_info(storeid: int | str) -> dict:
    """
    Возвращает:
      - ukm4ip  ← PROP_ID 'REP.UKMSERVER' (IP сервера УКМ4, где крутится MySQL import4)
      - is_ukm5 ← наличие/значение PROP_ID 'REP.UKMSERVER5'
    Поиск умеет работать и по SMSTORE (T1.ID), и по UKM4STORE (PROPVAL = 'REP.UKMStoreId').
    """
    ORA_HOST     = os.getenv("ORACLE_HOST", "192.168.17.239")
    ORA_PORT     = int(os.getenv("ORACLE_PORT", "1521"))
    ORA_SERVICE  = os.getenv("ORACLE_SERVICE", "BINUU00")
    ORA_USER     = os.getenv("ORACLE_USER", "supermag")
    ORA_PASSWORD = os.getenv("ORACLE_PASSWORD", "qqq")

    sid_raw = str(storeid).strip()
    logger.info(f"[Oracle] get_store_info: старт. входной storeid={sid_raw!r}")

    conn = cur = None
    try:
        dsn = cx_Oracle.makedsn(ORA_HOST, ORA_PORT, service_name=ORA_SERVICE)
        logger.info("[Oracle] Подключение...")
        conn = cx_Oracle.connect(user=ORA_USER, password=ORA_PASSWORD, dsn=dsn)
        logger.info(f"[Oracle] Подключено. Версия: {getattr(conn, 'version', 'unknown')}")

        # Шаг 1. Пытаемся трактовать как SMSTORE (T1.ID)
        storeloc_id = None
        try:
            sid_int = int(sid_raw)
        except Exception:
            sid_int = None

        cur = conn.cursor()
        if sid_int is not None:
            cur.execute("SELECT COUNT(*) FROM SMSTORELOCATIONS WHERE ID = :sid", sid=sid_int)
            cnt = cur.fetchone()[0]
            logger.info(f"[Oracle] Проверка SMSTORELOCATIONS(ID={sid_int}) → count={cnt}")
            if cnt > 0:
                storeloc_id = sid_int

        # Шаг 2. Если не нашли по T1.ID — ищем по UKM4STORE (REP.UKMStoreId)
        if storeloc_id is None:
            logger.info(f"[Oracle] Пытаемся сопоставить UKM4STORE={sid_raw!r} → STORELOC")
            cur.execute("""
                SELECT STORELOC
                FROM SMSTOREPROPERTIES
                WHERE PROPID = 'REP.UKMStoreId'
                  AND TRIM(PROPVAL) = TRIM(:ukm4)
                FETCH FIRST 1 ROWS ONLY
            """, ukm4=sid_raw)
            row = cur.fetchone()
            if row:
                storeloc_id = int(row[0])
                logger.info(f"[Oracle] Маппинг UKM4STORE={sid_raw} → STORELOC={storeloc_id}")
            else:
                logger.warning(f"[Oracle] Не найден STORELOC по UKM4STORE={sid_raw}")
                return {"is_ukm5": False, "ukm4ip": None}

        # Шаг 3. Тянем все свойства по найденному STORELOC
        cur.execute("""
            SELECT PROPID, PROPVAL
            FROM SMSTOREPROPERTIES
            WHERE STORELOC = :sl
        """, sl=storeloc_id)
        rows = cur.fetchall()
        logger.info(f"[Oracle] Свойств у STORELOC={storeloc_id}: {len(rows)}")

        props = {}
        for propid, propval in rows:
            k = str(propid).strip() if propid is not None else ""
            v = propval.strip() if isinstance(propval, str) else propval
            props[k] = v

        # Интересующие ключи
        ukm4ip  = props.get("REP.UKMSERVER")
        ukm5val = props.get("REP.UKMSERVER5")
        ukm4sid = props.get("REP.UKMStoreId")
        logger.info(f"[Oracle] REP.UKMStoreId={ukm4sid!r}, REP.UKMSERVER={ukm4ip!r}, REP.UKMSERVER5={ukm5val!r}")

        # Попытка альтернативных названий для IP, если вдруг ключ другой
        if not ukm4ip:
            for alt in ("REP.UKM4SERVER", "UKMSERVER", "UKM.SERVER", "REP.UKM_SERVER"):
                if alt in props:
                    ukm4ip = props[alt]
                    logger.info(f"[Oracle] Найден альтернативный ключ IP: {alt} → {ukm4ip!r}")
                    break

        result = {
            "is_ukm5": bool(ukm5val),
            "ukm4ip": ukm4ip.strip() if isinstance(ukm4ip, str) else ukm4ip
        }
        logger.info(f"[Oracle] Результат для входного {sid_raw}: {result}")
        return result

    except Exception as e:
        logger.exception(f"[Oracle] Ошибка в get_store_info(storeid={sid_raw}): {e}")
        return {"is_ukm5": False, "ukm4ip": None}
    finally:
        try:
            if cur:
                cur.close()
            if conn:
                conn.close()
        except Exception as e:
            logger.warning(f"[Oracle] Ошибка при закрытии соединения: {e}")


def get_inn_hash(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("ИНН должен быть строкой")

    if len(value) == 64 and all(ch in _HEX for ch in value):
        return value.lower()                  

    if value.isdigit():                     
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    raise ValueError("ИНН должен быть числом (10/12) или 64-символьным SHA-256")


LOG_DIR = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
log_file_path = os.path.join(LOG_DIR, 'ukm_register.log')

logger = logging.getLogger("ukm_logger")
logger.setLevel(logging.INFO)
file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
if not logger.hasHandlers():
    logger.addHandler(file_handler)
    
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
XML_DIR = os.path.join(BASE_DIR, 'xml')
os.makedirs(XML_DIR, exist_ok=True)


#  1С: параметры и HTTP-сессия
ONEC_EMP_IDENT_URL = os.getenv(
    "ONEC_EMP_IDENT_URL",
    "http://192.168.17.26/zupcorp_http/hs/API/Post_EmployeeIdentification"
)
ONEC_USER = os.getenv("ONEC_USER")       
ONEC_PASS = os.getenv("ONEC_PASS")

# Глобальная Session 
_ONEC_SESSION = requests.Session()
_ONEC_SESSION.headers.update({"Content-Type": "application/json; charset=utf-8"})


def resolve_xml_store_id(raw_store_id: int | str) -> int:
    """
    Преобразует id магазина из УКМ (ukm4store),
    в smstore из PostgreSQL таблицы stores.

    Если в таблице stores нет записи или smstore некорректен –
    возвращаем исходный id (как сейчас).
    """
    sid_raw = str(raw_store_id).strip()
    try:
        mapped = (
            Store.objects
            .filter(ukm4store=sid_raw)
            .values_list('smstore', flat=True)
            .first()
        )
        if mapped is None:
            logger.warning(f"[XML] storeid={sid_raw} не найден в stores.ukm4store – используем исходный id")
            return int(sid_raw)

        try:
            return int(mapped)
        except (TypeError, ValueError):
            logger.warning(f"[XML] Некорректный smstore={mapped!r} для ukm4store={sid_raw} – используем исходный id")
            return int(sid_raw)
    except Exception as e:
        logger.exception(f"[XML] Ошибка при resolve_xml_store_id({sid_raw!r}): {e}")
        try:
            return int(sid_raw)
        except Exception:
            return 0


def _find_storecashiers_file_for_store(store_id: int) -> Optional[Tuple[str, int]]:
    """
    Ищет последний файл вида:
      storeCashiers_[smstore]_[Number]_[F].xml
    smstore получаем из таблицы stores по ukm4store = store_id.
    Возвращает (полный_путь, Number) или None, если файлов нет.
    """
    xml_store_id = resolve_xml_store_id(store_id)
    pattern = re.compile(rf"^storeCashiers_\[{xml_store_id}\]_\[(\d+)\]_\[F\]\.xml$")
    best_name = None
    best_num = -1

    try:
        for name in os.listdir(XML_DIR):
            m = pattern.match(name)
            if m:
                num = int(m.group(1))
                if num > best_num:
                    best_num = num
                    best_name = name
    except FileNotFoundError:
        return None

    if best_name is None:
        return None
    return os.path.join(XML_DIR, best_name), best_num


def _generate_storecashiers_number(store_id: int) -> int:
    """
    Генерирует случайный положительный номер файла для магазина,
    гарантируя отсутствие коллизий по Number для этого smstore.
    """
    xml_store_id = resolve_xml_store_id(store_id)
    pattern = re.compile(rf"^storeCashiers_\[{xml_store_id}\]_\[(\d+)\]_\[F\]\.xml$")
    used_numbers = set()

    try:
        for name in os.listdir(XML_DIR):
            m = pattern.match(name)
            if m:
                used_numbers.add(int(m.group(1)))
    except FileNotFoundError:
        pass

    for _ in range(1000):
        num = random.randint(1, 999_999_999)
        if num not in used_numbers:
            return num

    return (max(used_numbers) + 1) if used_numbers else 1


def _get_or_create_storecashiers_tree(store_id: int) -> Tuple[str, ET.ElementTree, ET.Element]:
    """
    Возвращает (xml_path, tree, root) для storeCashiers магазина.

    На вход приходит ukm4store (store_id).
    Для XML:
      - вычисляем smstore через resolve_xml_store_id()
      - имя файла: storeCashiers_[smstore]_[Number]_[F].xml
      - <storeCashiers fullness="F" storeId="smstore">
          <version>1.0</version>
        </storeCashiers>
    """
    xml_store_id = resolve_xml_store_id(store_id)

    found = _find_storecashiers_file_for_store(store_id)
    if found:
        xml_path, number = found
        tree = ET.parse(xml_path)
        root = tree.getroot()

        root.set("fullness", "F")
        root.set("storeId", str(xml_store_id))

        version_el = root.find("version")
        if version_el is None:
            version_el = ET.SubElement(root, "version")
        version_el.text = "1.0"

        return xml_path, tree, root

    # файла ещё нет – создаём новый
    number = _generate_storecashiers_number(store_id)
    filename = f"storeCashiers_[{xml_store_id}]_[{number}]_[F].xml"
    xml_path = os.path.join(XML_DIR, filename)

    root = ET.Element("storeCashiers", fullness="F", storeId=str(xml_store_id))
    version_el = ET.SubElement(root, "version")
    version_el.text = "1.0"
    tree = ET.ElementTree(root)

    return xml_path, tree, root


def connect_ukm():
    return pymysql.connect(
        host="192.168.17.234",
        user="ukminfo",
        password="CtHDbCGK.C",
        database="ukmserver",
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )


def get_trm_employee_id(plain_inn: str, fio: str) -> int | None:
    conn = connect_ukm()
    cur  = conn.cursor()
    cur.execute(
        "SELECT id FROM trm_in_users WHERE user_inn=%s AND name=%s",
        (plain_inn, fio)                    
    )
    row = cur.fetchone()
    cur.close(); conn.close()
    return row["id"] if row else None


def connect_converter():
    return pymysql.connect(
        host="192.168.17.234",
        port=3306,
        user="user1C",
        password="852654",
        database="import4staffbonus",
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )


def generate_qr_string(inn: str, salt: str = "INDIVIDUAL_SALT") -> str:
    expiration = (datetime.datetime.utcnow() + datetime.timedelta(days=1)
                  ).strftime('%Y%m%d')
    return f"KS{inn}{expiration}{salt}"
    
def connect_oracle():
    dsn = cx_Oracle.makedsn("192.168.17.239", 1521, service_name="xe")
    return cx_Oracle.connect(user="supermag_user", password="supermag_pass", dsn=dsn, encoding="UTF-8")

def is_ukm5_store(storeid: int) -> bool:
    """Сохранена стар. сигнатура: True если магазин UKM5, иначе False."""
    info = get_store_info(storeid)
    return info.get("is_ukm5", False)


def connect_store_mysql(host: str):
    """
    Подключение к MySQL конвертера конкретного магазина:
      - host = UKM4IP
      - database = import4
      - user = ukm_import
      - password = jgOKsc2n
    """
    return pymysql.connect(
        host=host,
        port=3306,
        user="ukm_import",
        password="jgOKsc2n",
        database="import4",
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )


def ensure_plain_inn(value: str) -> str:
    v = (value or "").strip()
    if not (v.isdigit() and len(v) in (10, 12)):
        raise ValueError("ИНН должен содержать 10 или 12 цифр")
    return v



def _parse_and_format_dt(dt_raw: str) -> str:
    """
    Приводит дату/время к строке вида 'DD.MM.YYYY H:MM:SS'
    Допускаемые входы: '24.09.2025 8:45:00', '24.09.2025 08:45', '2025-09-24 08:45:00',
    ISO-похожие и т.п. из списка fmts.
    """
    s = (dt_raw or "").strip()
    if not s:
        raise ValueError("Пустой Datetime")

    fmts = [
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
    ]
    dt = None
    last_err = None
    for f in fmts:
        try:
            dt = datetime.datetime.strptime(s, f)
            break
        except Exception as e:
            last_err = e
    if dt is None and len(s.split()) == 2 and s.count(':') == 1 and '.' in s.split()[0]:
        try:
            dt = datetime.datetime.strptime(s + ":00", "%d.%m.%Y %H:%M:%S")
        except Exception:
            pass
    if dt is None:
        raise ValueError(f"Некорректный Datetime: {dt_raw!r}; последняя ошибка: {last_err}")

    out = dt.strftime("%d.%m.%Y %H:%M:%S")
    if out[11] == '0':
        out = out[:11] + out[12:]
    return out


def _onec_auth() -> Optional[Tuple[str, str]]:
    return (ONEC_USER, ONEC_PASS) if ONEC_USER and ONEC_PASS else None


def _post_to_onec(payload: dict, idem_key: str, timeout=(3, 7), retries=2) -> Tuple[int, str]:
    """
    Отправка в 1С с ретраями и идемпотентным ключом (в заголовке).
    timeout: (connect, read); retries: доп. попытки (итого 1+retries).
    Возвращает (status_code, text). Генерирует RuntimeError при сетевых сбоях после всех попыток.
    """
    headers = {"X-Idempotency-Key": idem_key}
    auth = _onec_auth()
    last_exc = None
    for attempt in range(retries + 1):
        try:
            resp = _ONEC_SESSION.post(
                ONEC_EMP_IDENT_URL,
                json=payload,
                headers=headers,
                auth=auth,
                timeout=timeout,
            )
            return resp.status_code, (resp.text or "")
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(0.3 * (2 ** attempt)) 
    raise RuntimeError(f"Ошибка запроса в 1С: {last_exc}")


def encrypt_inn_full(inn: str) -> str:
    return get_inn_hash(inn)

def encrypt_inn20(inn: str) -> str:
    return encrypt_inn_full(inn)[:20]

def build_user_password(plain_inn: str) -> str:
    """
    Формирует пароль вида:

        KS<ИНН><YYYYMMDD><RANDOM>

    * KS        – постоянный префикс
    * ИНН       – 10 / 12 цифр, БЕЗ хэша
    * YYYYMMDD  – дата UTC
    * RANDOM    – заглавные A-Z + цифры, чтобы итог всегда был 40 симв.
    """
    date_part = datetime.datetime.utcnow().strftime("%Y%m%d")     
    base      = f"KS{plain_inn}{date_part}"                   
    salt_len  = 40 - len(base)
    salt      = ''.join(random.choices(string.ascii_uppercase + string.digits,
                                       k=salt_len))
    return base + salt                                             # ровно 40


def mysql_pwd(raw: str) -> str:
    """
    Возвращает строку для OLD_PASSWORD – без префикса 'KS'.
    Если префикса нет, возвращаем как есть (защитный fallback).
    """
    return raw[2:] if raw.startswith("KS") else raw


# Oracle-коннект (единый)
def connect_oracle_supermag():
    ORA_HOST     = os.getenv("ORACLE_HOST", "192.168.17.239")
    ORA_PORT     = int(os.getenv("ORACLE_PORT", "1521"))
    ORA_SERVICE  = os.getenv("ORACLE_SERVICE", "BINUU00")
    ORA_USER     = os.getenv("ORACLE_USER", "supermag")
    ORA_PASSWORD = os.getenv("ORACLE_PASSWORD", "qqq")
    dsn = cx_Oracle.makedsn(ORA_HOST, ORA_PORT, service_name=ORA_SERVICE)
    return cx_Oracle.connect(user=ORA_USER, password=ORA_PASSWORD, dsn=dsn, encoding="UTF-8")

# Базовый SQL
BASE_STORES_SQL = """
SELECT
  r.title AS region,
  t1.id   AS smstore,
  t1.name AS name,
  t1.address AS address,
  t2.propval AS closedate,
  t3.propval AS ukm4store,
  t5.propval AS ukm4ip,
  t4.propval AS ukm5store,
  t6.propval AS latitude,
  t7.propval AS longitude
FROM smstorelocations t1
JOIN smregions r ON r.rgnid = t1.rgnid
LEFT JOIN smstoreproperties t2 ON t2.storeloc = t1.id AND t2.propid = 'REP.CLOSEDATE'
LEFT JOIN smstoreproperties t3 ON t3.storeloc = t1.id AND t3.propid = 'REP.UKMStoreId'
LEFT JOIN smstoreproperties t4 ON t4.storeloc = t1.id AND t4.propid = 'REP.UKMSERVER5'
LEFT JOIN smstoreproperties t5 ON t5.storeloc = t1.id AND t5.propid = 'REP.UKMSERVER'
LEFT JOIN smstoreproperties t6 ON t6.storeloc = t1.id AND t6.propid = 'REP.STORE.Latitude'
LEFT JOIN smstoreproperties t7 ON t7.storeloc = t1.id AND t7.propid = 'REP.STORE.Longitude'
WHERE
  UPPER(t1.name) NOT LIKE 'Я %%'
  AND t1.name NOT LIKE '%%ТЕСТ%%'
  AND t1.idclass IN (
    SELECT id FROM supermag.sastoreclass
    WHERE tree LIKE '1.1.%%' OR tree LIKE '1.2.%%' OR tree LIKE '1.3.%%' OR tree LIKE '1.4.%%' OR tree LIKE '2.1.%%' OR tree LIKE '2.2.%%'
  )
  AND t1.accepted = 1
  AND t1.loctype = 4
  {only_active_clause}
  {extra_filters}
ORDER BY r.title, t1.name
"""

def _fetch_stores(q: str, region: str, only_active: bool):
    extra_filters_sql = []
    binds = {}

    if region:
        extra_filters_sql.append("AND r.title = :region")
        binds['region'] = region

    if q:
        like = f"%{q.lower()}%"
        if q.isdigit():
            extra_filters_sql.append("""
            AND (
                LOWER(t1.name) LIKE :q
                OR LOWER(t1.address) LIKE :q
                OR t1.id = :sid
                OR t3.propval = :ukm4sid
            )""")
            binds['q'] = like
            binds['sid'] = int(q)
            binds['ukm4sid'] = q
        else:
            extra_filters_sql.append("""
            AND (
                LOWER(t1.name) LIKE :q
                OR LOWER(t1.address) LIKE :q
            )""")
            binds['q'] = like

    only_active_clause = "AND (t2.propval IS NULL OR TO_DATE(t2.propval,'DD.MM.YYYY') >= TRUNC(SYSDATE))" if only_active else ""

    sql = BASE_STORES_SQL.format(
        only_active_clause=only_active_clause,
        extra_filters=(" " + " ".join(extra_filters_sql)) if extra_filters_sql else ""
    )

    rows = []
    conn = cur = None
    try:
        conn = connect_oracle_supermag()
        cur = conn.cursor()
        cur.execute(sql, binds)
        cols = [d[0].lower() for d in cur.description]
        for r in cur.fetchall():
            item = dict(zip(cols, r))
            # координаты -> float, если возможно
            for k in ('latitude', 'longitude'):
                if item.get(k) is not None:
                    s = str(item[k]).replace(',', '.')
                    try:
                        item[k] = float(s)
                    except Exception:
                        pass
            rows.append(item)
    finally:
        try:
            if cur: cur.close()
            if conn: conn.close()
        except Exception:
            pass
    return rows

@csrf_exempt
def export_stores_xml(request):
    """
    GET /export/stores/xml/?q=&region=&only_active=1
    Сохраняет файл вида /app/xml/stores_YYYYMMDD_HHMMSS.xml
    """
    try:
        q = (request.GET.get('q') or '').strip()
        region = (request.GET.get('region') or '').strip()
        only_active = (request.GET.get('only_active') or '1') in ('1', 'true', 'True')

        rows = _fetch_stores(q, region, only_active)

        ts = datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        fname = f"stores_{ts}.xml"
        fpath = os.path.join(XML_DIR, fname)

        root = ET.Element("Stores")
        root.set("exported_at_utc", ts)
        root.set("only_active", "1" if only_active else "0")
        if q: root.set("q", q)
        if region: root.set("region", region)

        for r in rows:
            s_el = ET.SubElement(root, "Store")
            ET.SubElement(s_el, "Region").text     = str(r.get('region') or '')
            ET.SubElement(s_el, "SMSTORE").text    = str(r.get('smstore') or '')
            ET.SubElement(s_el, "Name").text       = str(r.get('name') or '')
            ET.SubElement(s_el, "Address").text    = str(r.get('address') or '')
            ET.SubElement(s_el, "CloseDate").text  = str(r.get('closedate') or '')
            ET.SubElement(s_el, "UKM4Store").text  = str(r.get('ukm4store') or '')
            ET.SubElement(s_el, "UKM4IP").text     = str(r.get('ukm4ip') or '')
            ET.SubElement(s_el, "UKM5Store").text  = str(r.get('ukm5store') or '')
            ET.SubElement(s_el, "Latitude").text   = '' if r.get('latitude') is None else str(r.get('latitude'))
            ET.SubElement(s_el, "Longitude").text  = '' if r.get('longitude') is None else str(r.get('longitude'))

        tree = ET.ElementTree(root)
        tree.write(fpath, encoding="utf-8", xml_declaration=True)

        return JsonResponse({
            'status': 'ok',
            'saved_to': fpath,
            'filename': fname,
            'count': len(rows)
        })
    except Exception as e:
        logger.exception("export_stores_xml error")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@csrf_exempt
def register_cashier(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)

    try:
        data = json.loads(request.body)
        required_fields = ['inn', 'surname', 'name', 'patronymic', 'mail', 'phone', 'department', 'position', 'roleId', 'storeid']
        missing = [f for f in required_fields if not data.get(f)]
        if missing:
            logger.error(f"Пропущены поля: {missing}")
            return JsonResponse({'status': 'error', 'message': f'Пропущены поля: {missing}'}, status=400)

        inn = data['inn'].strip()
        fio = f"{data['surname']} {data['name']} {data['patronymic']}"
        mail = data['mail']
        phone = data['phone']
        dep_name = data['department']
        pos_name = data['position']
        role_id = data['roleId']

        store_ids = [int(s.strip()) for s in str(data['storeid']).split(',') if s.strip().isdigit()]
        if not store_ids:
            return JsonResponse({'status': 'error', 'message': 'Некорректный storeid'}, status=400)

        dep_obj = Department.objects.filter(name__iexact=dep_name).first()
        if not dep_obj:
            return JsonResponse({'status': 'error', 'message': f'Отдел «{dep_name}» не найден'}, status=400)

        pos_obj = Position.objects.filter(name__iexact=pos_name).first()
        if not pos_obj:
            return JsonResponse({'status': 'error', 'message': f'Должность «{pos_name}» не найдена'}, status=400)

        with transaction.atomic():
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
            xml_dir = os.path.join(base_dir, 'xml')
            os.makedirs(xml_dir, exist_ok=True)

            password_plain = build_user_password(inn)
            qr_string = password_plain

            # поиск/создание пользователя
            existing_user = User.objects.filter(encrypted_inn=inn, full_name=fio).first()
            if not existing_user:
                new_user = User.objects.create(
                    employee_id=inn,
                    encrypted_inn=inn,
                    full_name=fio,
                    mail=mail,
                    phone=phone,
                    department_id=dep_obj.id,
                    position_id=pos_obj.id,
                    active=True,
                    tg_status=False,
                    created_at=timezone.now(),
                    updated_at=timezone.now()
                )
                user_id = new_user.id

                OpenInSystem.objects.create(
                    user_id=user_id,
                    username=fio,
                    password=password_plain,
                    system_id=9,
                    status=True
                )

                QRCode.objects.create(
                    user=new_user,
                    qr_data=qr_string,
                    created_at=timezone.now()
                )

                logger.info(f"[PostgreSQL] Пользователь {fio} создан с ID={user_id}")
            else:
                new_user = existing_user
                user_id = new_user.id
                logger.info(f"[PostgreSQL] Пользователь уже существует: ID={user_id}")

            existing_storeids = set(UKMUser.objects.filter(user_id=user_id).values_list('storeid', flat=True))

            # id кассира берём из ukmserver
            ukm_conn = connect_ukm()
            ukm_cursor = ukm_conn.cursor()
            ukm_cursor.execute("SELECT MAX(id) + 1 AS next_id FROM trm_in_users")
            cashier_id_base = ukm_cursor.fetchone()['next_id'] or 1
            ukm_conn.close()

            cashier_counter = 0
            xml_paths = []

            for sid in store_ids:
                if sid in existing_storeids:
                    logger.info(f"[Пропуск] Пользователь уже зарегистрирован в storeid={sid}")
                    continue

                # PostgreSQL ukm_users
                UKMUser.objects.create(
                    user=new_user,
                    roleid=role_id,
                    storeid=sid,
                    version=1
                )

                cashier_id = cashier_id_base + cashier_counter
                cashier_counter += 1

                # Oracle → получить UKM4IP & UKM5
                info = get_store_info(sid)
                ukm4ip = info.get("ukm4ip")
                is_ukm5 = info.get("is_ukm5", False)

                # MySQL import4 на UKM4IP: users + signal
                if ukm4ip:
                    try:
                        conv = connect_store_mysql(ukm4ip)
                        cur = conv.cursor()
                        cur.execute("SELECT COUNT(*) AS cnt FROM `signal` WHERE `signal` = 'busy'")
                        base_version = (cur.fetchone()['cnt'] or 0) + 1

                        cur.execute("""
                            INSERT INTO users (store, id, name, inn, password, role_id, version, deleted)
                            VALUES (%s, %s, %s, %s, OLD_PASSWORD(%s), %s, %s, 0)
                        """, (sid, cashier_id, fio, inn, mysql_pwd(password_plain), role_id, base_version))

                        cur.execute("INSERT INTO `signal`(`signal`, `version`) VALUES ('incr', %s)", (base_version,))
                        conv.commit()
                        conv.close()
                        logger.info(f"[MySQL:{ukm4ip}] Добавлен кассир store={sid}, id={cashier_id}, version={base_version}")
                    except Exception as e:
                        logger.error(f"[MySQL:{ukm4ip}] Ошибка вставки для store={sid}: {e}")
                else:
                    logger.error(f"[Oracle] Не найден UKM4IP для storeid={sid}. Пропуск записи в MySQL.")

                # XML для UKM5
                if is_ukm5:
                    try:
                        xml_path, tree, root = _get_or_create_storecashiers_tree(sid)

                        cashier_el = ET.SubElement(root, "cashier")
                        ET.SubElement(cashier_el, "roleId").text = str(role_id)
                        ET.SubElement(cashier_el, "id").text = str(cashier_id)
                        ET.SubElement(cashier_el, "name").text = fio
                        ET.SubElement(cashier_el, "INN").text = inn
                        ET.SubElement(cashier_el, "password").text = password_plain

                        tree.write(xml_path, encoding="utf-8", xml_declaration=True)
                        xml_paths.append(xml_path)
                        logger.info(f"[XML] Файл обновлён: {xml_path}")
                    except Exception as e:
                        logger.error(f"[XML] Ошибка генерации для storeid={sid}: {e}")

        return JsonResponse({
            'status': 'ok',
            'message': 'Кассир зарегистрирован для всех storeid',
            'password': password_plain,
            'storeids': store_ids,
            'xml_paths': xml_paths
        })

    except Exception as e:
        logger.exception("Ошибка регистрации кассира")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


def encrypt_inn(inn):
    """
    Хэшируем (SHA-256) поле inn, если это строка из цифр.
    """
    if not isinstance(inn, str) or not inn.isdigit():
        raise ValueError("ИНН должен быть строкой, содержащей только цифры.")
    hash_object = hashlib.sha256(inn.encode('utf-8'))
    return hash_object.hexdigest()

def validate_and_create_record(payload, required_fields, action_name="CREATE"):
    """
    1) Смотрим payload['data'] (других полей в запросе нет).
    2) Проверяем наличие всех required_fields.
    3) Если чего-то не хватает → пишем в queue (status='failed') + только в MODUL_logs.
    4) Если всё ок → queue (status='pending'), без логирования.

    Возвращает (final_status, missing_fields).
    """
    data = payload.get('data', {})
    if not isinstance(data, dict):
        data = {}

    final_status = 'pending'
    attempts = 0
    last_attempt = None

    # Проверяем обязательные поля
    missing = [f for f in required_fields if not data.get(f)]

    # Хэшируем ИНН (если он есть и валиден)
    if data.get('inn'):
        try:
            data['inn'] = encrypt_inn(data['inn'])
        except ValueError:
            missing.append('inn')

    if missing:
        final_status = 'failed'

    # Создаём запись в queue
    Queue.objects.create(
        data=data,
        attempts=attempts,
        status=final_status,
        last_attempt=last_attempt
    )

    # Если failed → фиксируем только в MODUL_logs
    if final_status == 'failed':
        MODUL_logs.objects.create(
            data={                       # JSONB-поле в таблице
                "error": f"Незаполненные поля: {missing}",
                "payload": data
            }
        )

    return final_status, missing


@csrf_exempt
def queue_create(request):
    """
    Эндпойнт для СОЗДАНИЯ (create).
    Обязательны все поля, кроме:
    surname, start_vacation, end_vacation, type_vacation, shops_id
    """
    if request.method == 'POST':
        try:
            payload = json.loads(request.body.decode('utf-8'))

            required_fields = [
                'inn', 'last_name', 'first_name', 'beginwork_date',
                'organization', 'department', 'bitrix_code', 'subdivision',
                'position', 'phone', 'birth_date', 'action'
            ]

            final_status, missing = validate_and_create_record(
                payload, required_fields, action_name='CREATE'
            )

            if missing:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Пропущены или пусты поля: {missing}. Запись -> failed, логи созданы.'
                }, status=400)
            else:
                return JsonResponse({'status': 'ok', 'message': 'Создано успешно'}, status=201)

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    else:
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)


@csrf_exempt
def queue_update(request):
    """
    Эндпойнт для ИЗМЕНЕНИЯ (update).
    Обязательны все поля, кроме:
    surname, start_vacation, end_vacation, type_vacation, shops_id
    """
    if request.method == 'POST':
        try:
            payload = json.loads(request.body.decode('utf-8'))

            required_fields = [
                'inn', 'last_name', 'first_name', 'beginwork_date',
                'organization', 'department', 'bitrix_code', 'subdivision',
                'position', 'phone', 'birth_date', 'action'
            ]

            final_status, missing = validate_and_create_record(
                payload, required_fields, action_name='UPDATE'
            )

            if missing:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Пропущены поля: {missing}. Запись -> failed, логи созданы.'
                }, status=400)
            else:
                return JsonResponse({'status': 'ok', 'message': 'Изменение успешно'}, status=201)

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    else:
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)


@csrf_exempt
def queue_block(request):
    """
    Эндпойнт для БЛОКИРОВКИ (block).
    Обязательные поля:
    inn, last_name, first_name
    """
    if request.method == 'POST':
        try:
            payload = json.loads(request.body.decode('utf-8'))

            required_fields = [
                'inn', 'last_name', 'first_name'
            ]

            final_status, missing = validate_and_create_record(
                payload, required_fields, action_name='DELETE'
            )

            if missing:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Пропущены поля: {missing}. Запись -> failed, логи созданы.'
                }, status=400)
            else:
                return JsonResponse({'status': 'ok', 'message': 'Блокировка/Удаление успешно'}, status=201)

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    else:
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)


@csrf_exempt
def queue_vacation(request):
    """
    Эндпойнт для ОТПУСКА (vacation).
    Обязательные поля:
    inn, last_name, first_name,
    start_vacation, end_vacation, type_vacation
    """
    if request.method == 'POST':
        try:
            payload = json.loads(request.body.decode('utf-8'))

            required_fields = [
                'inn', 'last_name', 'first_name',
                'start_vacation', 'end_vacation', 'type_vacation'
            ]

            final_status, missing = validate_and_create_record(
                payload, required_fields, action_name='CREATE'
            )

            if missing:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Пропущены поля: {missing}. Запись -> failed, логи созданы.'
                }, status=400)
            else:
                return JsonResponse({'status': 'ok', 'message': 'Отпуск успешно'}, status=201)

        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    else:
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)


def regenerate_qr(user):
    new_password = build_user_password(user.employee_id)
    now = timezone.now()
    expiration = now + datetime.timedelta(days=1)

    # PostgreSQL: QR + open_in_system
    QRCode.objects.filter(user=user).delete()
    QRCode.objects.create(user=user, qr_data=new_password, created_at=now, expires_at=expiration)
    OpenInSystem.objects.filter(user_id=user.id, system_id=9).update(password=new_password)

    # для id кассира
    ukm_conn = connect_ukm()
    ukm_cursor = ukm_conn.cursor()
    ukm_cursor.execute("SELECT MAX(id)+1 AS next_id FROM trm_in_users")
    cashier_id_base = ukm_cursor.fetchone()['next_id'] or 1
    ukm_conn.close()

    ukm_emp_id = get_trm_employee_id(user.employee_id, user.full_name)
    ukm_users = list(UKMUser.objects.filter(user_id=user.id))
    cashier_counter = 0

    for ukm_user in ukm_users:
        sid = ukm_user.storeid
        cashier_id = ukm_emp_id if ukm_emp_id else (cashier_id_base + cashier_counter)

        info = get_store_info(sid)
        ukm4ip = info.get("ukm4ip")

        if ukm4ip:
            try:
                conv = connect_store_mysql(ukm4ip)
                cur = conv.cursor()
                cur.execute("SELECT COUNT(*) AS cnt FROM `signal` WHERE `signal`='busy'")
                base_version = (cur.fetchone()['cnt'] or 0) + 1

                cur.execute("""
                    INSERT INTO users (store, id, name, inn, password, role_id, version, deleted)
                    VALUES (%s, %s, %s, %s, OLD_PASSWORD(%s), %s, %s, 0)
                """, (
                    sid,
                    cashier_id,
                    user.full_name,
                    user.employee_id,
                    mysql_pwd(new_password),
                    ukm_user.roleid,
                    base_version
                ))
                cur.execute("INSERT INTO `signal`(`signal`, `version`) VALUES ('incr', %s)", (base_version,))
                conv.commit()
                conv.close()
                logger.info(f"[MySQL:{ukm4ip}] Пароль обновлён store={sid}, id={cashier_id}, version={base_version}")
            except Exception as e:
                logger.error(f"[MySQL:{ukm4ip}] Ошибка обновления пароля для store={sid}: {e}")
        else:
            logger.error(f"[Oracle] Не найден UKM4IP для storeid={sid}. Пропуск записи в MySQL.")

        cashier_counter += 1

    # XML для UKM5 (обновляем записи)
    next_free_id = cashier_id_base + cashier_counter

    for ukm_user in ukm_users:
        sid = ukm_user.storeid
        if not is_ukm5_store(sid):
            continue

        try:
            xml_path, tree, root = _get_or_create_storecashiers_tree(sid)

            for el in list(root.findall("cashier")):
                if el.findtext("INN") == user.employee_id:
                    root.remove(el)

            c_el = ET.SubElement(root, "cashier")
            ET.SubElement(c_el, "roleId").text = str(ukm_user.roleid)
            ET.SubElement(c_el, "id").text = str(next_free_id)
            ET.SubElement(c_el, "name").text = user.full_name
            ET.SubElement(c_el, "INN").text = user.employee_id
            ET.SubElement(c_el, "password").text = new_password

            tree.write(xml_path, encoding="utf-8", xml_declaration=True)
            logger.info(f"[XML] Обновлён при регенерации QR: {xml_path}")
            next_free_id += 1
        except Exception as exc:
            logger.error(f"[XML] Ошибка для {sid}: {exc}")



def _set_password_pg(user, new_password: str) -> None:
    """
    Обновляет пароль во всех связанных PG-таблицах:
      • qr_code  — пересоздаём запись (expires_at = now + 1 day)
      • open_in_system (system_id=9) — создаём/обновляем пароль
    """
    now = timezone.now()
    expiration = now + datetime.timedelta(days=1)

    # QR: пересоздаём, чтобы был всегда один актуальный
    QRCode.objects.filter(user=user).delete()
    QRCode.objects.create(
        user=user,
        qr_data=new_password,
        created_at=now,
        expires_at=expiration
    )
    logger.info(f"[PG] QRCode regenerated for user_id={user.id}; expires_at={expiration.isoformat()}")

    # open_in_system: обновляем, если запись есть; иначе создаём
    updated = OpenInSystem.objects.filter(user_id=user.id, system_id=9).update(
        username=user.full_name,
        password=new_password,
        status=True
    )
    if updated:
        logger.info(f"[PG] OpenInSystem updated (system_id=9) for user_id={user.id}")
    else:
        OpenInSystem.objects.create(
            user_id=user.id,
            username=user.full_name,
            password=new_password,
            system_id=9,
            status=True
        )
        logger.info(f"[PG] OpenInSystem created (system_id=9) for user_id={user.id}")


@csrf_exempt
def get_qr_code_by_tg(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)

    try:
        data_raw = request.body.decode('utf-8') if request.body else "{}"
        try:
            data = json.loads(data_raw)
        except Exception as e:
            logger.error(f"[QR/TG] JSON parse error: {e}; body={data_raw!r}")
            return JsonResponse({'status': 'error', 'message': 'Некорректный JSON'}, status=400)

        # tg_id может быть int/str — всегда приводим к str перед .strip()
        tg_id_val = data.get('tg_id', '')
        tg_id = str(tg_id_val).strip()
        logger.info(f"[QR/TG] === START === raw_tg_id={tg_id_val!r} → tg_id='{tg_id}'")

        if not tg_id:
            logger.error("[QR/TG] tg_id not provided (empty after coercion)")
            return JsonResponse({'status': 'error', 'message': 'Не указан tg_id'}, status=400)

        # 1) Пользователь по tg_id
        user = User.objects.filter(tg_id=tg_id).first()
        if not user:
            logger.error(f"[QR/TG] User not found by tg_id={tg_id!r}")
            return JsonResponse({'status': 'error', 'message': 'Пользователь не найден'}, status=404)

        fio = (user.full_name or '').strip()
        employee_id_raw = (user.employee_id or '').strip()
        logger.info(f"[QR/TG] User found: id={user.id}, fio={fio!r}, employee_id_raw={employee_id_raw!r}")

        # 2) Валидация ИНН
        try:
            plain_inn = ensure_plain_inn(employee_id_raw)
            logger.info(f"[QR/TG] employee_id verified as INN={plain_inn}")
        except Exception as e:
            logger.error(f"[QR/TG] Bad employee_id for user_id={user.id}: {e}")
            return JsonResponse({'status': 'error', 'message': f'Некорректный employee_id: {e}'}, status=400)

        # 3) Связки ukm_users (storeid/roleid)
        ukm_links = list(UKMUser.objects.filter(user_id=user.id).values('storeid', 'roleid'))
        logger.info(f"[QR/TG] ukm_users rows for user_id={user.id}: count={len(ukm_links)}; rows={ukm_links!r}")
        if not ukm_links:
            logger.error(f"[QR/TG] No ukm_users rows for user_id={user.id}")
            return JsonResponse({'status': 'error', 'message': 'Для пользователя нет записей в ukm_users'}, status=404)

        # 4) Поиск в trm_in_users (укм-сервер)
        ukm_emp_id = get_trm_employee_id(plain_inn, fio)
        if ukm_emp_id is None:
            ukm_conn = connect_ukm()
            cur = ukm_conn.cursor()
            cur.execute("SELECT MAX(id) + 1 AS next_id FROM trm_in_users")
            cashier_id_base = cur.fetchone()['next_id'] or 1
            cur.close(); ukm_conn.close()
            logger.info(f"[QR/TG] trm_in_users: NOT FOUND; next free id base={cashier_id_base}")
        else:
            cashier_id_base = None
            logger.info(f"[QR/TG] trm_in_users: FOUND id={ukm_emp_id}")

        # 5) Генерим НОВЫЙ пароль (KS + INN + YYYYMMDD + random)
        new_password = build_user_password(plain_inn)
        masked = new_password[:6] + "..." + new_password[-4:]
        logger.info(f"[QR/TG] New password (masked): {masked}; len={len(new_password)}")

        # 6) Обновляем пароль в PG: QRCode + open_in_system
        _set_password_pg(user, new_password)
        logger.info(f"[QR/TG] PG updated for user_id={user.id} (QRCode + OpenInSystem)")

        # 7) Для каждого магазина — запись в import4.users + сигнал в import4.signal
        cashier_counter = 0
        for link in ukm_links:
            sid = int(link['storeid'])
            role_id = int(link['roleid'])
            cashier_id = ukm_emp_id if ukm_emp_id is not None else (cashier_id_base + cashier_counter)

            logger.info(f"[QR/TG] -> Store loop: storeid={sid}, role_id={role_id}, cashier_id={cashier_id}")

            info = get_store_info(sid)
            ukm4ip = info.get("ukm4ip")
            is_ukm5 = info.get("is_ukm5", False)
            logger.info(f"[QR/TG] Store {sid}: ukm4ip={ukm4ip!r}, is_ukm5={is_ukm5}")

            if not ukm4ip:
                logger.error(f"[QR/TG] Store {sid}: ukm4ip not found. Skip import4 write.")
                cashier_counter += 1
                continue

            try:
                conv = connect_store_mysql(ukm4ip)
                cur = conv.cursor()

                # Версию берём как (COUNT(signal='busy') + 1)
                cur.execute("SELECT COUNT(*) AS cnt FROM `signal` WHERE `signal`='busy'")
                base_version = (cur.fetchone()['cnt'] or 0) + 1
                logger.info(f"[QR/TG] Store {sid} ({ukm4ip}): version={base_version} via 'busy' counter")

                # Пишем users (пароль — OLD_PASSWORD без префикса KS)
                cur.execute("""
                    INSERT INTO users (store, id, name, inn, password, role_id, version, deleted)
                    VALUES (%s, %s, %s, %s, OLD_PASSWORD(%s), %s, %s, 0)
                """, (sid, cashier_id, fio, plain_inn, mysql_pwd(new_password), role_id, base_version))
                logger.info(f"[QR/TG] Store {sid} ({ukm4ip}): users inserted (deleted=0)")

                # Сигнал «incr»
                cur.execute("INSERT INTO `signal`(`signal`, `version`) VALUES ('incr', %s)", (base_version,))
                logger.info(f"[QR/TG] Store {sid} ({ukm4ip}): signal inserted: incr/{base_version}")

                conv.commit()
                cur.close(); conv.close()
                logger.info(f"[QR/TG] Store {sid} ({ukm4ip}): COMMIT OK")
            except Exception as e:
                logger.error(f"[QR/TG] Store {sid} ({ukm4ip}) WRITE ERROR: {e}", exc_info=True)

            cashier_counter += 1

        # 8) Успех — возвращаем только статус и пароль
        logger.info(f"[QR/TG] === DONE === Returning password (masked): {masked}")
        return JsonResponse({'status': 'ok', 'qr_data': new_password})

    except Exception as e:
        logger.exception("Ошибка при get_qr_code_by_tg (расширенная логика)")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)



@csrf_exempt
def get_qr_code_by_employee_id(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)

    try:
        data = json.loads(request.body)
        employee_id = (data.get('employee_id') or "").strip()

        if not employee_id:
            return JsonResponse({'status': 'error', 'message': 'Не указан employee_id'}, status=400)

        # валидация: ИНН = 10/12 цифр
        try:
            plain_inn = ensure_plain_inn(employee_id)
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': f'Некорректный employee_id: {e}'}, status=400)

        user = User.objects.filter(employee_id=plain_inn).first()
        if not user:
            return JsonResponse({'status': 'error', 'message': 'Пользователь не найден'}, status=404)

        # всегда генерируем новый QR / пароль
        regenerate_qr(user)

        new_qr = QRCode.objects.filter(user=user).order_by('-created_at').first()
        if not new_qr:
            return JsonResponse({'status': 'error', 'message': 'Не удалось сформировать QR-код'}, status=500)

        return JsonResponse({'status': 'ok', 'qr_data': new_qr.qr_data})

    except Exception as e:
        logger.exception("Ошибка при получении QR-кода по employee_id")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)











@csrf_exempt
def update_cashier(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)

    try:
        data = json.loads(request.body)

        plain_inn = ensure_plain_inn(data.get('inn'))
        fio = data.get('fio')
        storeids = data.get('storeid')

        if not (plain_inn and fio and storeids):
            return JsonResponse({'status': 'error', 'message': 'inn, fio и storeid обязательны'}, status=400)

        store_ids = [int(s.strip()) for s in str(storeids).split(',') if s.strip().isdigit()]
        if not store_ids:
            return JsonResponse({'status': 'error', 'message': 'Некорректный storeid'}, status=400)

        user = User.objects.filter(employee_id=plain_inn, full_name=fio).first()
        if not user:
            return JsonResponse({'status': 'error', 'message': 'Пользователь не найден'}, status=404)

        open_rec = OpenInSystem.objects.filter(user_id=user.id, system_id=9).first()
        if not open_rec:
            return JsonResponse({'status': 'error', 'message': 'Пароль для пользователя не найден'}, status=500)
        password_plain = open_rec.password

        ukm_emp_id = get_trm_employee_id(plain_inn, fio)

        existing_storeids = set(UKMUser.objects.filter(user_id=user.id).values_list('storeid', flat=True))

        ukm_conn = connect_ukm()
        ukm_cursor = ukm_conn.cursor()
        ukm_cursor.execute("SELECT MAX(id)+1 AS next_id FROM trm_in_users")
        cashier_id_base = ukm_cursor.fetchone()['next_id'] or 1
        ukm_conn.close()

        cashier_counter = 0
        added_storeids = []

        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        xml_dir = os.path.join(base_dir, 'xml')
        os.makedirs(xml_dir, exist_ok=True)

        for sid in store_ids:
            if sid in existing_storeids:
                logger.info(f"[Пропуск] Доступ уже есть: storeid={sid}")
                continue

            UKMUser.objects.create(user=user, roleid=1, storeid=sid, version=1)

            cashier_id = ukm_emp_id if ukm_emp_id else (cashier_id_base + cashier_counter)
            cashier_counter += 1

            info = get_store_info(sid)
            ukm4ip = info.get("ukm4ip")
            is_ukm5 = info.get("is_ukm5", False)

            if ukm4ip:
                try:
                    conv = connect_store_mysql(ukm4ip)
                    cur = conv.cursor()
                    cur.execute("SELECT COUNT(*) AS cnt FROM `signal` WHERE `signal`='busy'")
                    base_version = (cur.fetchone()['cnt'] or 0) + 1

                    cur.execute("""
                        INSERT INTO users (store, id, name, inn, password, role_id, version, deleted)
                        VALUES (%s, %s, %s, %s, OLD_PASSWORD(%s), %s, %s, 0)
                    """, (
                        sid, cashier_id, fio, plain_inn, mysql_pwd(password_plain), 1, base_version
                    ))
                    cur.execute("INSERT INTO `signal`(`signal`, `version`) VALUES ('incr', %s)", (base_version,))
                    conv.commit()
                    conv.close()
                    logger.info(f"[MySQL:{ukm4ip}] Доступ открыт store={sid}, id={cashier_id}, version={base_version}")
                except Exception as exc:
                    logger.error(f"[MySQL:{ukm4ip}] Ошибка для {sid}: {exc}")
            else:
                logger.error(f"[Oracle] Не найден UKM4IP для storeid={sid}. Пропуск записи в MySQL.")

            if is_ukm5:
                try:
                    xml_path, tree, root = _get_or_create_storecashiers_tree(sid)

                    c_el = ET.SubElement(root, "cashier")
                    ET.SubElement(c_el, "roleId").text = "1"
                    ET.SubElement(c_el, "id").text = str(cashier_id)
                    ET.SubElement(c_el, "name").text = fio
                    ET.SubElement(c_el, "INN").text = plain_inn
                    ET.SubElement(c_el, "password").text = password_plain

                    tree.write(xml_path, encoding="utf-8", xml_declaration=True)
                    logger.info(f"[XML] Обновлён файл {xml_path}")
                except Exception as exc:
                    logger.error(f"[XML] Ошибка для {sid}: {exc}")

            added_storeids.append(sid)

        if not added_storeids:
            return JsonResponse({'status': 'ok', 'message': 'У пользователя уже был доступ ко всем указанным магазинам'})

        return JsonResponse({'status': 'ok', 'message': 'Доступ открыт', 'added': added_storeids})

    except Exception as exc:
        logger.exception("open_access_cashier error")
        return JsonResponse({'status': 'error', 'message': str(exc)}, status=500)



@csrf_exempt
def delete_cashier(request):
    if request.method != 'POST':
        return JsonResponse({"status": "error", "message": "Только POST"}, status=405)

    try:
        data = json.loads(request.body)

        inn_raw = ensure_plain_inn(data.get("inn"))
        fio = data.get("fio")
        store_raw = str(data.get("storeid", "")).strip()

        if not (inn_raw and fio and store_raw):
            return JsonResponse({"status": "error", "message": "inn, fio и storeid обязательны"}, status=400)

        store_ids = [int(s) for s in store_raw.split(',') if s.strip().isdigit()]
        if not store_ids:
            return JsonResponse({"status": "error", "message": "Некорректный storeid"}, status=400)

        user = User.objects.filter(employee_id=inn_raw, full_name=fio).first()
        if not user:
            return JsonResponse({"status": "error", "message": "Пользователь не найден"}, status=404)

        ukm_to_remove = list(UKMUser.objects.filter(user=user, storeid__in=store_ids))
        if not ukm_to_remove:
            return JsonResponse({"status": "ok", "message": "У пользователя уже нет доступа к указанным магазинам", "removed": []})

        current_password = (OpenInSystem.objects
                            .filter(user_id=user.id, system_id=9)
                            .values_list("password", flat=True)
                            .first()) or build_user_password(inn_raw)

        ukm_emp_id = get_trm_employee_id(inn_raw, fio)

        ukm_conn = connect_ukm()
        ukm_cursor = ukm_conn.cursor()
        ukm_cursor.execute("SELECT MAX(id)+1 AS next_id FROM trm_in_users")
        cashier_id_base = ukm_cursor.fetchone()['next_id'] or 1
        ukm_conn.close()

        removed_sids = []
        for idx, ukm_user in enumerate(ukm_to_remove):
            sid = ukm_user.storeid
            cashier_id = ukm_emp_id if ukm_emp_id else (cashier_id_base + idx)

            info = get_store_info(sid)
            ukm4ip = info.get("ukm4ip")

            if ukm4ip:
                try:
                    conv = connect_store_mysql(ukm4ip)
                    cur = conv.cursor()
                    cur.execute("SELECT COUNT(*) AS cnt FROM `signal` WHERE `signal`='busy'")
                    base_version = (cur.fetchone()['cnt'] or 0) + 1

                    cur.execute("""
                        INSERT INTO users (store,id,name,inn,password,role_id,version,deleted)
                        VALUES (%s,%s,%s,%s,OLD_PASSWORD(%s),%s,%s,1)
                    """, (sid, cashier_id, fio, inn_raw, current_password, ukm_user.roleid, base_version))
                    cur.execute("INSERT INTO `signal`(`signal`,`version`) VALUES ('incr',%s)", (base_version,))
                    conv.commit()
                    conv.close()
                    removed_sids.append(sid)
                    logger.info(f"[MySQL:{ukm4ip}] Закрыт доступ store={sid}, id={cashier_id}, version={base_version}")
                except Exception as exc:
                    logger.error(f"[MySQL:{ukm4ip}] Ошибка для магазина {sid}: {exc}")
            else:
                logger.error(f"[Oracle] Не найден UKM4IP для storeid={sid}. Пропуск записи в MySQL.")

        for ukm_user in ukm_to_remove:
            sid = ukm_user.storeid
            if not is_ukm5_store(sid):
                continue

            found = _find_storecashiers_file_for_store(sid)
            if not found:
                continue
            xml_path, number = found

            if not os.path.exists(xml_path):
                continue

            try:
                tree = ET.parse(xml_path)
                root = tree.getroot()

                xml_store_id = resolve_xml_store_id(sid)

                # Гарантируем корректные атрибуты корня
                root.set("fullness", "F")
                root.set("storeId", str(xml_store_id))

                version_el = root.find("version")
                if version_el is None:
                    version_el = ET.SubElement(root, "version")
                version_el.text = "1.0"

                changed = False
                for cash_el in list(root.findall("cashier")):
                    if cash_el.findtext("INN") == inn_raw:
                        root.remove(cash_el)
                        changed = True

                if changed:
                    tree.write(xml_path, encoding="utf-8", xml_declaration=True)
                    logger.info(f"[XML] Удалён кассир (INN={inn_raw}) из {xml_path}")
            except Exception as exc:
                logger.error(f"[XML] Ошибка для магазина {sid}: {exc}")

        # удаляем ukm_users в PostgreSQL
        UKMUser.objects.filter(id__in=[u.id for u in ukm_to_remove]).delete()

        return JsonResponse({"status": "ok", "message": "Доступ закрыт", "removed": removed_sids})

    except Exception as exc:
        logger.exception("delete_cashier error")
        return JsonResponse({"status": "error", "message": str(exc)}, status=500)




@csrf_exempt
def employee_identification(request):
    """
    POST /employee-identification/
    Тело:
    {
      "inn": "1234567890",          # 10/12 цифр
      "fio": "Иванов Иван Иванович",
      "mx": "137",                  # id магазина, строкой
      "datetime": "24.09.2025 8:45:00"
    }
    Никаких записей в БД. Только валидация, нормализация и отправка в 1С.
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)

    try:
        body = request.body.decode('utf-8') if request.body else "{}"
        data = json.loads(body)

        inn_raw = (data.get("inn") or "").strip()
        fio_raw = (data.get("fio") or "").strip()
        mx_raw  = (data.get("mx")  or data.get("storeid") or "").strip()
        dt_raw  = (data.get("datetime") or data.get("Datetime") or "").strip()

        # Валидации/нормализация — без записи в БД
        inn_plain = ensure_plain_inn(inn_raw)          # 10/12 цифр
        if not fio_raw:
            return JsonResponse({'status': 'error', 'message': 'Пустой FIO'}, status=400)
        if not mx_raw:
            return JsonResponse({'status': 'error', 'message': 'Пустой MX (id магазина)'}, status=400)
        dt_norm = _parse_and_format_dt(dt_raw)         # 'DD.MM.YYYY H:MM:SS'

        onec_payload = {
            "INN": inn_plain,
            "FIO": fio_raw,
            "MX": str(mx_raw),
            "Datetime": dt_norm,
        }
        idem_key = hashlib.sha256(
            f"{inn_plain}|{fio_raw}|{mx_raw}|{dt_norm}".encode("utf-8")
        ).hexdigest()

        status_code, text = _post_to_onec(onec_payload, idem_key)
        ok = 200 <= status_code < 300

        logger.info(f"[1C] POST {ONEC_EMP_IDENT_URL} → {status_code}; payload={onec_payload}")

        return JsonResponse({
            "status": "ok" if ok else "error",
            "onec_status_code": status_code,
            "onec_response": text,
            "payload": onec_payload
        }, status=200 if ok else 502)

    except Exception as e:
        logger.exception("employee_identification error")
        return JsonResponse({"status": "error", "message": str(e)}, status=400)
