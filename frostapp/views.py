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
import uuid
import pymysql
import xml.etree.ElementTree as ET
from xml.dom import minidom
import cx_Oracle
import re   
from django.http import JsonResponse
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
import ipaddress
import paramiko
import csv
from django.db.models import Q
import math
from collections import defaultdict, Counter
from pathlib import Path
from django.shortcuts import render, redirect
from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.http import require_http_methods
from django.urls import reverse
from django.utils.http import urlencode
from django.views.decorators.csrf import csrf_protect


from .models import Queue, MODUL_logs, User, UKMUser, OpenInSystem, QRCode, Department, Position, Store, AuthSession, QRIssueLog

_HEX = set("0123456789abcdefABCDEF")
UKM5_FULL_XML_STORE_ID = 2013
def _parse_int_set_env(name: str, default_csv: str) -> set[int]:
    raw = os.getenv(name, default_csv) or ""
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except Exception:

            pass
    return out


UKM5_FULL_XML_STORE_IDS: set[int] = _parse_int_set_env("UKM5_FULL_XML_STORE_IDS", "2013,9016,1003")
TRM_ID_MAX = 2147483647

TRM_SMALL_MIN = int(os.getenv("TRM_SMALL_MIN", "10000"))   
TRM_SMALL_MAX = int(os.getenv("TRM_SMALL_MAX", "99999")) 
if TRM_SMALL_MIN < 4:
    TRM_SMALL_MIN = 4
if TRM_SMALL_MAX >= TRM_ID_MAX:
    TRM_SMALL_MAX = TRM_ID_MAX - 1


ONEC_WORKING_EMPLOYEES_URL = os.getenv(
    "ONEC_WORKING_EMPLOYEES_URL",
    "http://192.168.17.26/zupcorp_http/hs/API/Get_WorkingEmployees",
)

ONEC_WORKING_EMPLOYEES_TIMEOUT = int(os.getenv("ONEC_WORKING_EMPLOYEES_TIMEOUT", "180"))

ORACLE_SERVICES_ALL = [
    "BINUU00","BINUU01","BINUU02","BINUU03","BINUU04","BINUU5","BINUU05","BINUU06","BINUU07","BINUU08","BINUU09",
    "BINUU10","BINUU011","BINUU11","BINUU12","BINUU14","BINUU15","BINUU16","BINUU17","BINUU18","BINUU21","BINUU22",
    "BINUU23","BINUU24","BINUU25","BINUU26",
    "BINCH00","BINCH1","BINCH2","BINCH3","BINCH4","BINCH5","BINCH6","BINCH7","BINCH8","BINCH9","BINCH10","BINCH11",
    "BINCH12","BINCH13","BINCH14","BINCH15","BINCH16","BINCH17","BINCH18","BINCH19","BINCH20","BINCH21","BINCH22",
]

ORACLE_TNS_MAP = {
    "BINUU00": {"host": "192.168.17.239", "port": 1521, "service_name": "BINUU00"},
    "BINUU01": {"host": "omega1",         "port": 1521, "service_name": "BINUU01"},
    "BINUU02": {"host": "epsilon3",       "port": 1521, "service_name": "BINUU02"},
    "BINUU03": {"host": "omegadb3",       "port": 1521, "service_name": "BINUU03"},
    "BINUU04": {"host": "omega4",         "port": 1521, "service_name": "BINUU04"},
    "BINUU5":  {"host": "omega5",         "port": 1521, "service_name": "BINUU5"},
    "BINUU05": {"host": "delta1",         "port": 1521, "service_name": "BINUU05"},
    "BINUU06": {"host": "10.30.10.254",   "port": 1521, "service_name": "BINUU06"},
    "BINUU07": {"host": "10.30.1.254",    "port": 1521, "service_name": "BINUU07"},
    "BINUU08": {"host": "10.30.8.254",    "port": 1521, "service_name": "BINUU08"},
    "BINUU09": {"host": "omega9new",      "port": 1521, "service_name": "BINUU09"},
    "BINUU10": {"host": "omega14",        "port": 1521, "service_name": "BINUU10"},
    "BINUU011":{"host": "omega11",        "port": 1521, "service_name": "BINUU011"},
    "BINUU11": {"host": "omega33",        "port": 1521, "service_name": "BINUU11"},
    "BINUU12": {"host": "epsilon3",       "port": 1521, "service_name": "BINUU12"},
    "BINUU14": {"host": "epsilon3",       "port": 1521, "service_name": "BINUU14"},
    "BINUU15": {"host": "omega15",        "port": 1521, "service_name": "BINUU15"},
    "BINUU16": {"host": "omega16",        "port": 1521, "service_name": "BINUU16"},
    "BINUU17": {"host": "omega17",        "port": 1521, "service_name": "BINUU17"},


    "BINUU18": {"hosts": ["omega18", "omega18_"], "port": 1521, "service_name": "BINUU18"},

    "BINUU21": {"host": "omega21",        "port": 1521, "service_name": "BINUU21"},
    "BINUU22": {"host": "omega22",        "port": 1521, "service_name": "BINUU22"},
    "BINUU23": {"host": "omega42",        "port": 1521, "service_name": "BINUU23"},
    "BINUU24": {"host": "omega24",        "port": 1521, "service_name": "BINUU24"},
    "BINUU25": {"host": "omega25",        "port": 1521, "service_name": "BINUU25"},
    "BINUU26": {"host": "omega26",        "port": 1521, "service_name": "BINUU26"},

    "BINCH00": {"host": "192.168.202.253","port": 1521, "service_name": "BINCH00"},
    "BINCH1":  {"host": "10.50.50.254",   "port": 1521, "service_name": "BINCH1"},
    "BINCH2":  {"host": "192.168.202.253","port": 1521, "service_name": "BINCH2"},
    "BINCH3":  {"host": "192.168.202.238","port": 1521, "service_name": "BINCH3"},
    "BINCH4":  {"host": "192.168.202.249","port": 1521, "service_name": "BINCH4"},
    "BINCH5":  {"host": "192.168.202.7",  "port": 1521, "service_name": "BINCH5"},
    "BINCH6":  {"host": "192.168.202.12", "port": 1521, "service_name": "BINCH6"},
    "BINCH7":  {"host": "delta7",         "port": 1521, "service_name": "BINCH7"},
    "BINCH8":  {"host": "delta8",         "port": 1521, "service_name": "BINCH8"},
    "BINCH9":  {"host": "192.168.202.253","port": 1521, "service_name": "BINCH9"},
    "BINCH10": {"host": "192.168.202.8",  "port": 1521, "service_name": "BINCH10"},
    "BINCH11": {"host": "192.168.202.253","port": 1521, "service_name": "BINCH11"},
    "BINCH12": {"host": "192.168.202.249","port": 1521, "service_name": "BINCH12"},
    "BINCH13": {"host": "192.168.202.253","port": 1521, "service_name": "BINCH13"},
    "BINCH14": {"host": "192.168.202.13", "port": 1521, "service_name": "BINCH14"},
    "BINCH15": {"host": "delta15",        "port": 1521, "service_name": "BINCH15"},
    "BINCH16": {"host": "192.168.202.11", "port": 1521, "service_name": "BINCH16"},
    "BINCH17": {"host": "192.168.202.249","port": 1521, "service_name": "BINCH17"},
    "BINCH18": {"host": "192.168.202.249","port": 1521, "service_name": "BINCH18"},
    "BINCH19": {"host": "192.168.202.238","port": 1521, "service_name": "BINCH19"},
    "BINCH20": {"host": "192.168.202.249","port": 1521, "service_name": "BINCH20"},
    "BINCH21": {"host": "192.168.202.249","port": 1521, "service_name": "BINCH21"},
    "BINCH22": {"host": "192.168.202.253","port": 1521, "service_name": "BINCH22"},
}

# Транслитерация
_TRANSLIT = {
    "А":"A","Б":"B","В":"V","Г":"G","Д":"D","Е":"E","Ё":"YO","Ж":"ZH","З":"Z","И":"I","Й":"Y","К":"K","Л":"L",
    "М":"M","Н":"N","О":"O","П":"P","Р":"R","С":"S","Т":"T","У":"U","Ф":"F","Х":"KH","Ц":"TS","Ч":"CH","Ш":"SH",
    "Щ":"SHCH","Ъ":"","Ы":"Y","Ь":"","Э":"E","Ю":"YU","Я":"YA",
}

_LOGIN_SAFE_RE = re.compile(r"[^a-z0-9_]+", re.IGNORECASE)



INN_SYNC_PROGRESS_EVERY_ROWS = int(os.getenv("INN_SYNC_PROGRESS_EVERY_ROWS", "2000"))
INN_SYNC_HEARTBEAT_SEC = int(os.getenv("INN_SYNC_HEARTBEAT_SEC", "30"))
INN_SYNC_ORACLE_CALL_TIMEOUT_MS = int(os.getenv("INN_SYNC_ORACLE_CALL_TIMEOUT_MS", "120000"))  # 120s
INN_SYNC_SLOW_STEP_WARN_SEC = float(os.getenv("INN_SYNC_SLOW_STEP_WARN_SEC", "10"))








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
            "ukm4ip": ukm4ip.strip() if isinstance(ukm4ip, str) else ukm4ip,
            "smstore": storeloc_id, 
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
    
    

UKM5_SRV_HOST = os.getenv("UKM5_SRV_HOST", "192.168.17.38")
UKM5_SRV_USER = os.getenv("UKM5_SRV_USER", "ukminfo")
UKM5_SRV_PASSWORD = os.getenv("UKM5_SRV_PASSWORD", "CtHDbCGK.C")  
UKM5_SRV_DB = os.getenv("UKM5_SRV_DB", "srvdata")

SSH_UKM4_ROOT_PASSWORD = os.getenv("SSH_UKM4_ROOT_PASSWORD", "xxxxxx") 
SSH_UKM4_KSO_PASSWORD  = os.getenv("SSH_UKM4_KSO_PASSWORD", "xxxxxx") 
SSH_UKM5_PASSWORD      = os.getenv("SSH_UKM5_PASSWORD", "xxxxxx")   

POS_SSH_PORT = int(os.getenv("POS_SSH_PORT", "22"))
POS_REBOOT_ALLOWED_NETS_RAW = os.getenv("POS_REBOOT_ALLOWED_NETS", "10.0.0.0/8,192.168.0.0/16") 



TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "7330478125:AAEYPbkbSIMj_N56_V7gEvJN2dxh2SF7bMo"
)
TELEGRAM_ADMIN_CHAT_ID = int(os.getenv("TELEGRAM_ADMIN_CHAT_ID", "1811037612"))
TELEGRAM_ADMIN_CHAT_IDS = [
    int(os.getenv("TELEGRAM_ADMIN_CHAT_ID", "1811037612")),  
    396948960,                                              
    5031157629,                                          
]
PIN_TTL_MINUTES = 2    
SESSION_TTL_MINUTES = 10   
MAX_PIN_ATTEMPTS = 3     


def _to_float_or_none(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return None



def send_telegram_log(message: str) -> None:
    """
    Отправка читаемых, многострочных логов в несколько Telegram-чатов администраторов.

    • Не роняет основной поток при ошибках.
    • Длинные сообщения режет по ~4000 символов.
    • Обрезает лишние пробелы по краям.
    • Отключает превью ссылок.
    """
    if not TELEGRAM_BOT_TOKEN:
        return

    # фильтруем пустые/некорректные chat_id
    chat_ids = [cid for cid in TELEGRAM_ADMIN_CHAT_IDS if cid]
    if not chat_ids:
        return

    text = (message or "").strip()
    if not text:
        text = "(пустое сообщение лога)"

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    max_len = 4000

    try:
        # для каждого админ-чата шлём одно и то же сообщение
        for chat_id in chat_ids:
            if len(text) <= max_len:
                requests.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "text": text,
                        "disable_web_page_preview": True,
                    },
                    timeout=5,
                )
            else:
                # режем по кускам
                for i in range(0, len(text), max_len):
                    part = text[i:i + max_len]
                    requests.post(
                        url,
                        json={
                            "chat_id": chat_id,
                            "text": part,
                            "disable_web_page_preview": True,
                        },
                        timeout=5,
                    )
    except Exception as e:
        logger.error(f"[TELEGRAM] Не удалось отправить лог: {e}", exc_info=True)     
        
def log_qr_issue(
    *,
    endpoint: str,
    method: str,
    status: str,
    user: Optional[User] = None,
    employee_inn: str = "",
    employee_fio: str = "",
    tg_id: str = "",
    phone_raw: str = "",
    phone_normalized: str = "",
    sm_store_id: Optional[int] = None,
    ukm_store_id: Optional[int] = None,
    role_id: Optional[int] = None,
    qr_data: str = "",
    error_message: str = "",
    raw_request: Optional[dict] = None,
    latitude: Optional[float] = None,  
    longitude: Optional[float] = None,
) -> None:
    """
    Запись отдельной строки в qr_issue_logs.
    Никакие ошибки наружу не выкидывает.
    """
    try:
        # приведение к строке всего, что может оказаться int/None
        endpoint_str = str(endpoint or "")
        method_str = str(method or "")
        status_str = str(status or "")

        employee_inn_str = str(employee_inn or "")
        employee_fio_str = str(employee_fio or "")

        tg_id_str = "" if tg_id is None else str(tg_id)
        phone_raw_str = "" if phone_raw is None else str(phone_raw)
        phone_norm_str = "" if phone_normalized is None else str(phone_normalized)

        qr_data_str = "" if qr_data is None else str(qr_data)
        error_message_str = "" if error_message is None else str(error_message)
        
        lat_val = _to_float_or_none(latitude) 
        lon_val = _to_float_or_none(longitude)   

        # raw_request в JSONField/текст
        if isinstance(raw_request, dict):
            raw_request_value = raw_request
        else:
            raw_request_value = None

        QRIssueLog.objects.create(
            endpoint=endpoint_str,
            method=method_str,
            status=status_str,
            user=user,
            employee_inn=employee_inn_str or "",
            employee_fio=employee_fio_str or "",
            tg_id=tg_id_str[:32],
            phone_raw=phone_raw_str[:32],
            phone_normalized=phone_norm_str[:32],
            sm_store_id=sm_store_id,
            ukm_store_id=ukm_store_id,
            role_id=role_id,
            qr_data=qr_data_str or "",
            error_message=error_message_str or "",
            raw_request=raw_request_value,
            latitude=lat_val, 
            longitude=lon_val, 
        )
    except Exception as e:
        logger.error(f"[QR/DBLOG] Ошибка записи в qr_issue_logs: {e}", exc_info=True)
        
def send_telegram_to_user(user, message: str) -> bool:
    """
    Отправка сообщения сотруднику по user.tg_id.
    Возвращает True/False, исключения не пробрасывает.
    """
    tg_raw = getattr(user, "tg_id", None)

    # tg_raw может быть int, str, None — приводим к строке безопасно
    if tg_raw is None:
        tg_id = ""
    else:
        tg_id = str(tg_raw).strip()

    if not tg_id or not TELEGRAM_BOT_TOKEN:
        logger.warning(
            f"[TELEGRAM] tg_id отсутствует или пустой для user_id={getattr(user, 'id', None)}"
        )
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": tg_id, "text": message},
            timeout=10,
        )
        if resp.status_code != 200 or not resp.json().get("ok"):
            logger.error(
                f"[TELEGRAM] Ошибка отправки сообщения user_id={getattr(user, 'id', None)} "
                f"status={resp.status_code} body={resp.text}"
            )
            return False
        return True
    except Exception as e:
        logger.exception(
            f"[TELEGRAM] Исключение при отправке сообщения user_id={getattr(user, 'id', None)}: {e}"
        )
        return False


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
XML_DIR = os.path.join(BASE_DIR, 'xml')
os.makedirs(XML_DIR, exist_ok=True)



BASE_CASHIERS = [
    {
        "roleId": "2",
        "id": "1",
        "name": "ЦТО",
        "password": "KS4261347586",
    },
    {
        "roleId": "1",
        "id": "2",
        "name": "Кассир",
        "password": "KS2d02xw95xnr4",
    },
    {
        "roleId": "13",
        "id": "3",
        "name": "Администратор магазина",
        "password": "KSbj3jbznv3jat",
    },
]


def _ensure_base_cashiers(root: ET.Element) -> None:
    """
    Гарантирует, что в <storeCashiers> есть три базовых кассира с id=1,2,3,
    и что они стоят сразу после <version>, в начале списка.
    Если такие id уже есть — переопределяем их.
    """
    ids_to_reset = {c["id"] for c in BASE_CASHIERS}

    # Удаляем существующих кассиров с id 1,2,3
    for cash_el in list(root.findall("cashier")):
        cid = (cash_el.findtext("id") or "").strip()
        if cid in ids_to_reset:
            root.remove(cash_el)

    # Найдём индекс для вставки — сразу после <version>, если есть
    children = list(root)
    insert_idx = 0
    version_el = root.find("version")
    if version_el is not None and version_el in children:
        insert_idx = children.index(version_el) + 1

    # Вставляем базовых кассиров по порядку
    for cfg in BASE_CASHIERS:
        c_el = ET.Element("cashier")
        ET.SubElement(c_el, "roleId").text = cfg["roleId"]
        ET.SubElement(c_el, "id").text = cfg["id"]
        ET.SubElement(c_el, "name").text = cfg["name"]
        ET.SubElement(c_el, "password").text = cfg["password"]
        root.insert(insert_idx, c_el)
        insert_idx += 1


def _write_xml_with_declaration(xml_path: str, root: ET.Element, ensure_base: bool = False) -> None:
    """
    Записывает XML-файл с:
      - заголовком: <?xml version="1.0" encoding="UTF-8"?>
      - красивым форматированием (отступы и переносы строк).
    Для storeCashiers дополнительно может включать базовых кассиров.
    """
    if ensure_base:
        _ensure_base_cashiers(root)

    # Превращаем ElementTree в строку и красиво форматируем через minidom
    rough = ET.tostring(root, encoding="utf-8")
    dom = minidom.parseString(rough)
    pretty = dom.toprettyxml(indent="  ", encoding="UTF-8")

    with open(xml_path, "wb") as f:
        f.write(pretty)



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







_USERS_TTL_COLS_CACHE: dict[str, bool] = {}

def _mysql_users_supports_ttl_cols(cur, cache_key: str) -> bool:
    """
    Проверяет наличие колонок start_date/end_date в таблице users текущей MySQL БД.
    Кэшируем по cache_key (обычно ukm4ip), чтобы не делать SHOW COLUMNS каждый раз.
    """
    if cache_key in _USERS_TTL_COLS_CACHE:
        return _USERS_TTL_COLS_CACHE[cache_key]

    try:
        cur.execute("SHOW COLUMNS FROM users LIKE 'start_date'")
        has_start = cur.fetchone() is not None

        cur.execute("SHOW COLUMNS FROM users LIKE 'end_date'")
        has_end = cur.fetchone() is not None

        ok = bool(has_start and has_end)
        _USERS_TTL_COLS_CACHE[cache_key] = ok
        return ok

    except Exception as e:
        logger.warning(f"[MySQL:{cache_key}] Не смог проверить start_date/end_date в users: {e}")
        _USERS_TTL_COLS_CACHE[cache_key] = False
        return False



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
    Number = количество секунд с 00:00 текущего дня (локальное/настроенное время Django).
    При коллизии (файл с таким Number уже существует для этого store_id) —
    увеличиваем Number, пока не найдём свободный.
    """
    now = timezone.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    seconds_since_midnight = int((now - midnight).total_seconds())  # 0..86399

    base_num = seconds_since_midnight

    # Собираем уже использованные Number для этого магазина
    pattern = re.compile(rf"^storeCashiers_\[{store_id}\]_\[(\d+)\]_\[F\]\.xml$")
    used_numbers = set()

    try:
        for name in os.listdir(XML_DIR):
            m = pattern.match(name)
            if m:
                used_numbers.add(int(m.group(1)))
    except FileNotFoundError:
        pass

    # Если текущий номер ещё не использован — берём его
    if base_num not in used_numbers:
        return base_num

    # Если вдруг такой Number уже есть — двигаемся вверх, пока не найдём свободный
    n = base_num + 1
    # Теоретически может уйти за 86400, но это не критично — это просто уникальный int
    while n in used_numbers:
        n += 1

    return n


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


def build_full_ukm5_xml_for_store(store_id: int) -> Optional[str]:
    """
    Полная пересборка storeCashiers XML для УКМ-5 по одному магазину (ukm4store).

    В файл попадают ВСЕ кассиры, у которых есть UKMUser.storeid = store_id.
    Пароль берём из OpenInSystem (system_id=9).
    id кассира:
      - если есть запись в trm_in_users (user_inn + name) — используем её id;
      - иначе берём MAX(id)+1 и дальше инкрементируем в рамках этой пересборки.

    Возвращает путь к новому XML или None, если магазин не УКМ5.
    """
    info = get_store_info(store_id)
    ukm_host = info.get("ukm4ip")
    if not info.get("is_ukm5", False):
        logger.info(f"[XML/FULL] Store {store_id} не является УКМ-5 (is_ukm5=False) – пересборка не нужна")
        return None

    xml_store_id = resolve_xml_store_id(store_id)
    if not xml_store_id:
        logger.error(f"[XML/FULL] Не удалось получить xml_store_id для storeid={store_id}")
        return None

    links = list(
        UKMUser.objects
        .filter(storeid=store_id)
        .select_related("user")
        .order_by("user_id")
    )
    logger.info(
        f"[XML/FULL] Пересборка storeCashiers для storeid={store_id} "
        f"(xml_store_id={xml_store_id}), кассиров={len(links)}"
    )

    # Генерим Number = секунды с полуночи, избегаем коллизий
    now = timezone.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    seconds_since_midnight = int((now - midnight).total_seconds())

    pattern = re.compile(rf"^storeCashiers_\[{xml_store_id}\]_\[(\d+)\]_\[F\]\.xml$")
    used_numbers: set[int] = set()
    try:
        for name in os.listdir(XML_DIR):
            m = pattern.match(name)
            if m:
                used_numbers.add(int(m.group(1)))
    except FileNotFoundError:
        pass

    number = seconds_since_midnight
    while number in used_numbers:
        number += 1

    filename = f"storeCashiers_[{xml_store_id}]_[{number}]_[F].xml"
    xml_path = os.path.join(XML_DIR, filename)

    root = ET.Element("storeCashiers", fullness="F", storeId=str(xml_store_id))
    version_el = ET.SubElement(root, "version")
    version_el.text = "1.0"

    user_ids = [l.user_id for l in links]
    # Сразу подтянем пароли для всех пользователей
    open_map = {
        row["user_id"]: row["password"]
        for row in OpenInSystem.objects
            .filter(user_id__in=user_ids, system_id=9)
            .values("user_id", "password")
    }

    next_new_id: Optional[int] = None

    for link in links:
        user = link.user
        fio = (user.full_name or "").strip()
        emp_raw = (user.employee_id or "").strip()

        if not fio or not emp_raw:
            logger.warning(
                f"[XML/FULL] Пропуск user_id={user.id}: пустое ФИО ({fio!r}) "
                f"или employee_id ({emp_raw!r})"
            )
            continue

        try:
            plain_inn = ensure_plain_inn(emp_raw)
        except Exception as e:
            logger.warning(
                f"[XML/FULL] Пропуск user_id={user.id}: некорректный INN "
                f"{emp_raw!r}: {e}"
            )
            continue

        password_plain = open_map.get(user.id)
        if not password_plain:
            logger.warning(
                f"[XML/FULL] Пропуск user_id={user.id}: нет OpenInSystem(system_id=9)"
            )
            continue

        # Пытаемся взять id из trm_in_users
        cashier_id = get_trm_employee_id(plain_inn, fio, store_id=store_id, host=ukm_host)
        if cashier_id is None:
            if next_new_id is None:
                try:
                    next_new_id = get_next_trm_employee_id(store_id=store_id, host=ukm_host)
                    logger.info(f"[XML/FULL] trm_in_users@{ukm_host}: base next_id={next_new_id}")
                except Exception as e:
                    logger.error(f"[XML/FULL] Ошибка получения next_id из trm_in_users@{ukm_host}: {e}", exc_info=True)
                    next_new_id = 1
            cashier_id = next_new_id
            next_new_id += 1

        c_el = ET.SubElement(root, "cashier")
        ET.SubElement(c_el, "roleId").text = str(link.roleid)
        ET.SubElement(c_el, "id").text = str(cashier_id)
        ET.SubElement(c_el, "name").text = fio
        ET.SubElement(c_el, "INN").text = plain_inn
        ET.SubElement(c_el, "password").text = password_plain

    _write_xml_with_declaration(xml_path, root, ensure_base=True)

    logger.info(
        f"[XML/FULL] Готов полный storeCashiers для storeid={store_id}: {xml_path}"
    )
    return xml_path






def connect_ukm(host: Optional[str] = None, *, store_id: Optional[int | str] = None):
    resolved_host, src = _resolve_ukmserver_host(host=host, store_id=store_id)
    if src.startswith("fallback"):
        logger.warning(f"[UKM] connect_ukm(): resolved_host={resolved_host} src={src} store_id={store_id!r}")
    else:
        logger.info(f"[UKM] connect_ukm(): resolved_host={resolved_host} src={src} store_id={store_id!r}")

    return pymysql.connect(
        host=resolved_host,
        user="ukminfo",
        password="CtHDbCGK.C",
        database="ukmserver",
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=5,
        read_timeout=10,
        write_timeout=10,
    )
    
    
def _resolve_ukmserver_host(host: Optional[str] = None, store_id: Optional[int | str] = None) -> tuple[str, str]:
    """
    Возвращает (resolved_host, source)
    source: explicit | oracle | fallback_no_ukm4ip | fallback_oracle_error | fallback_no_store_id
    """
    if host:
        return str(host).strip(), "explicit"

    if store_id is not None:
        try:
            info = get_store_info(store_id)
            ip = info.get("ukm4ip")
            if ip:
                return str(ip).strip(), "oracle"
            fb = os.getenv("UKMSERVER_DEFAULT_HOST", "192.168.17.234")
            return fb, "fallback_no_ukm4ip"
        except Exception as e:
            fb = os.getenv("UKMSERVER_DEFAULT_HOST", "192.168.17.234")
            return fb, f"fallback_oracle_error:{type(e).__name__}"

    fb = os.getenv("UKMSERVER_DEFAULT_HOST", "192.168.17.234")
    return fb, "fallback_no_store_id"


def inspect_trm_in_users(
    plain_inn: str,
    fio: str,
    *,
    store_id: Optional[int | str] = None,
    host: Optional[str] = None,
) -> dict:
    """
    Возвращает подробный debug по тому:
      - куда подключились (resolved_host + почему)
      - есть ли store-колонка
      - найден ли сотрудник (с store-фильтром / без)
      - max/next id (global) + опционально max/next по store (только для отчёта)
    НИЧЕГО не меняет в данных.
    """
    resolved_host, host_source = _resolve_ukmserver_host(host=host, store_id=store_id)

    dbg: dict = {
        "store_id": store_id,
        "requested_host": host,
        "resolved_host": resolved_host,
        "host_source": host_source,
        "store_col": None,
        "found_id_store": None,
        "found_id_global": None,
        "found_id_final": None,
        "search_used": None,
        "max_id_all": None,
        "next_id_all": None,
        "max_id_store": None,
        "next_id_store": None,
        "counts": {},
        "errors": [],
    }

    conn = cur = None
    try:
        conn = connect_ukm(host=resolved_host)  # уже без store_id, т.к. host окончательно известен
        cur = conn.cursor()

        # колонки
        cur.execute("SHOW COLUMNS FROM trm_in_users")
        cols = {r["Field"] for r in (cur.fetchall() or [])}
        store_col = next((c for c in ("store", "storeid", "store_id", "shop", "shop_id") if c in cols), None)
        dbg["store_col"] = store_col

        # статистика (не обязательно, но полезно)
        try:
            cur.execute("SELECT COUNT(*) AS c FROM trm_in_users")
            dbg["counts"]["rows_all"] = int((cur.fetchone() or {}).get("c") or 0)
        except Exception as e:
            dbg["errors"].append(f"count_all:{type(e).__name__}:{e}")

        # поиск
        if store_col and store_id is not None:
            # 1) с фильтром по магазину
            try:
                cur.execute(
                    f"SELECT id FROM trm_in_users WHERE user_inn=%s AND name=%s AND `{store_col}`=%s LIMIT 1",
                    (plain_inn, fio, int(store_id)),
                )
                row = cur.fetchone()
                if row and row.get("id") is not None:
                    dbg["found_id_store"] = int(row["id"])
            except Exception as e:
                dbg["errors"].append(f"find_store:{type(e).__name__}:{e}")

            # 2) fallback без store
            try:
                cur.execute(
                    "SELECT id FROM trm_in_users WHERE user_inn=%s AND name=%s LIMIT 1",
                    (plain_inn, fio),
                )
                row2 = cur.fetchone()
                if row2 and row2.get("id") is not None:
                    dbg["found_id_global"] = int(row2["id"])
            except Exception as e:
                dbg["errors"].append(f"find_global:{type(e).__name__}:{e}")

            if dbg["found_id_store"] is not None:
                dbg["found_id_final"] = dbg["found_id_store"]
                dbg["search_used"] = f"inn+fio+{store_col}"
            elif dbg["found_id_global"] is not None:
                dbg["found_id_final"] = dbg["found_id_global"]
                dbg["search_used"] = "inn+fio (fallback_without_store)"
            else:
                dbg["search_used"] = f"inn+fio+{store_col} -> fallback inn+fio (not_found)"

        else:
            # таблица без store-колонки (или store_id не передан)
            try:
                cur.execute(
                    "SELECT id FROM trm_in_users WHERE user_inn=%s AND name=%s LIMIT 1",
                    (plain_inn, fio),
                )
                row = cur.fetchone()
                if row and row.get("id") is not None:
                    dbg["found_id_global"] = int(row["id"])
                    dbg["found_id_final"] = dbg["found_id_global"]
            except Exception as e:
                dbg["errors"].append(f"find_global_only:{type(e).__name__}:{e}")

            dbg["search_used"] = "inn+fio"

        # max/next (global)
        try:
            cur.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM trm_in_users")
            r = cur.fetchone() or {}
            max_id_all = int(r.get("max_id") or 0)
            dbg["max_id_all"] = max_id_all
            dbg["next_id_all"] = max_id_all + 1
        except Exception as e:
            dbg["errors"].append(f"max_all:{type(e).__name__}:{e}")

        # max/next по store (ТОЛЬКО ДЛЯ ОТЧЁТА; НЕ ИСПОЛЬЗУЕМ ДЛЯ ВЫДАЧИ, чтобы не словить дубль PK)
        if store_col and store_id is not None:
            try:
                cur.execute(
                    f"SELECT COALESCE(MAX(id), 0) AS max_id FROM trm_in_users WHERE `{store_col}`=%s",
                    (int(store_id),),
                )
                r = cur.fetchone() or {}
                max_id_store = int(r.get("max_id") or 0)
                dbg["max_id_store"] = max_id_store
                dbg["next_id_store"] = max_id_store + 1
            except Exception as e:
                dbg["errors"].append(f"max_store:{type(e).__name__}:{e}")

        return dbg

    except Exception as e:
        dbg["errors"].append(f"connect_or_query:{type(e).__name__}:{e}")
        return dbg
    finally:
        try:
            if cur: cur.close()
            if conn: conn.close()
        except Exception:
            pass

def get_trm_employee_id(
    plain_inn: str,
    fio: str,
    *,
    store_id: Optional[int | str] = None,
    host: Optional[str] = None
) -> int | None:
    """
    Ищет кассира в trm_in_users на НУЖНОМ ukmserver (по host / store_id).

    Если в таблице есть колонка store/storeid/store_id/shop/shop_id — пробуем искать с фильтром по магазину.
    Если не нашли — делаем fallback без фильтра по магазину (чтобы не сломаться на других схемах).
    """
    conn = cur = None
    try:
        conn = connect_ukm(host=host, store_id=store_id)
        cur = conn.cursor()

        # Узнаём колонки таблицы (схемы могут отличаться)
        cur.execute("SHOW COLUMNS FROM trm_in_users")
        cols = {r["Field"] for r in (cur.fetchall() or [])}

        store_col = None
        for cand in ("store", "storeid", "store_id", "shop", "shop_id"):
            if cand in cols:
                store_col = cand
                break

        # 1) основной поиск
        if store_col and store_id is not None:
            sql = f"SELECT id FROM trm_in_users WHERE user_inn=%s AND name=%s AND `{store_col}`=%s"
            cur.execute(sql, (plain_inn, fio, int(store_id)))
            row = cur.fetchone()
            if row:
                return row.get("id")

            # 2) fallback без store (на случай “глобальной” таблицы)
            cur.execute("SELECT id FROM trm_in_users WHERE user_inn=%s AND name=%s", (plain_inn, fio))
            row2 = cur.fetchone()
            return row2.get("id") if row2 else None

        # если store-колонки нет — как раньше
        cur.execute("SELECT id FROM trm_in_users WHERE user_inn=%s AND name=%s", (plain_inn, fio))
        row = cur.fetchone()
        return row.get("id") if row else None

    finally:
        try:
            if cur:
                cur.close()
            if conn:
                conn.close()
        except Exception:
            pass
        
        
        
def get_next_trm_employee_id(*, store_id: Optional[int | str] = None, host: Optional[str] = None) -> int:
    """
    Следующий id для trm_in_users.
    Берём MAX(id) только в диапазоне TRM_SMALL_MIN..TRM_SMALL_MAX (пятизначные),
    чтобы выбросы 2147483*** не ломали выдачу.
    Используем GET_LOCK() чтобы два параллельных запроса не выдали одинаковый id.
    """
    conn = cur = None
    lock_key = None
    got_lock = False

    try:
        conn = connect_ukm(host=host, store_id=store_id)
        cur = conn.cursor()

        # Лок на конкретный ukm-host (чтобы не конфликтовать между разными ukmserver)
        resolved_host, _ = _resolve_ukmserver_host(host=host, store_id=store_id)
        lock_key = f"trm_id_alloc:{resolved_host}"

        try:
            cur.execute("SELECT GET_LOCK(%s, %s) AS l", (lock_key, 5))
            got_lock = int((cur.fetchone() or {}).get("l") or 0) == 1
        except Exception:
            got_lock = False  # если GET_LOCK запрещён — продолжим без него (хуже, но не упадём)

        # max в “нормальном” диапазоне (пятизначные)
        cur.execute(
            "SELECT COALESCE(MAX(id), 0) AS max_id "
            "FROM trm_in_users "
            "WHERE id BETWEEN %s AND %s",
            (TRM_SMALL_MIN, TRM_SMALL_MAX),
        )
        row = cur.fetchone() or {}
        max_small = int(row.get("max_id") or 0)

        candidate = max(TRM_SMALL_MIN, max_small + 1)

        if candidate > TRM_SMALL_MAX:
            raise RuntimeError(
                f"Исчерпан диапазон id [{TRM_SMALL_MIN}..{TRM_SMALL_MAX}] в trm_in_users. "
                f"Нужно расширять диапазон или менять стратегию."
            )

        # На всякий — проверим что candidate реально свободен (при гонках без lock)
        cur.execute("SELECT 1 AS x FROM trm_in_users WHERE id=%s LIMIT 1", (candidate,))
        if cur.fetchone():
            # если занято — поднимаемся вверх, пока не найдём свободный (в рамках small-range)
            while candidate <= TRM_SMALL_MAX:
                candidate += 1
                cur.execute("SELECT 1 AS x FROM trm_in_users WHERE id=%s LIMIT 1", (candidate,))
                if not cur.fetchone():
                    break
            if candidate > TRM_SMALL_MAX:
                raise RuntimeError(
                    f"Не удалось найти свободный id в диапазоне [{TRM_SMALL_MIN}..{TRM_SMALL_MAX}]."
                )

        return int(candidate)

    finally:
        try:
            if cur and got_lock and lock_key:
                cur.execute("SELECT RELEASE_LOCK(%s)", (lock_key,))
        except Exception:
            pass
        try:
            if cur: cur.close()
            if conn: conn.close()
        except Exception:
            pass
        
        
def resolve_cashier_id_for_store(store_id: int, plain_inn: str, fio: str) -> tuple[int, Optional[str], bool, dict]:
    """
    Возвращает:
      (cashier_id, resolved_ukm_host, found_in_trm, trm_debug)

    cashier_id:
      - если найден в trm_in_users -> found_id_final
      - иначе -> next_id_all (MAX(id)+1 глобально по этой таблице на этом ukmserver)
    """
    info = get_store_info(store_id)
    ukm_host_from_oracle = info.get("ukm4ip")

    trm_dbg = inspect_trm_in_users(
        plain_inn,
        fio,
        store_id=store_id,
        host=ukm_host_from_oracle
    )

    resolved_host = trm_dbg.get("resolved_host") or ukm_host_from_oracle
    found_id = trm_dbg.get("found_id_final")

    if found_id is not None:
        return int(found_id), resolved_host, True, trm_dbg

    next_id = get_next_trm_employee_id(store_id=store_id, host=resolved_host)
    trm_dbg["allocated_id"] = next_id
    trm_dbg["allocated_range"] = f"{TRM_SMALL_MIN}..{TRM_SMALL_MAX}"
    return int(next_id), resolved_host, False, trm_dbg

    
def _calc_next_signal_version(cur) -> int:
    """
    Возвращает следующий номер версии для MySQL-таблицы `signal`.

    Берём MAX(version) по всей таблице и увеличиваем на 1.
    Так версия всегда растёт, даже если нет строк с signal='busy'.
    """
    cur.execute("SELECT MAX(`version`) AS max_ver FROM `signal`")
    row = cur.fetchone() or {}
    max_ver = row.get("max_ver") or 0
    try:
        max_ver = int(max_ver)
    except (TypeError, ValueError):
        max_ver = 0
    return max_ver + 1

def _write_converter_user_and_signal(*args, **kwargs) -> None:
    """
    import4staffbonus (старый конвертер) отключён.
    Оставлено как no-op, чтобы не падать, если где-то остался вызов.
    """
    logger.info("[CONVERTER] import4staffbonus отключён — пропускаю запись users/signal")
    return

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


def _update_store_mysql_and_xml_for_single_store(
    store_id: int,
    cashier_id: int,
    role_id: int,
    plain_inn: str,
    fio: str,
    password_plain: str
) -> None:
    """
    Обновляет кассира по ОДНОМУ магазину:
      • UKM4 (MySQL import4.users + import4.signal)
      • UKM5 (XML storeCashiers_... если магазин UKM5)

    ВАЖНО:
      • Для магазинов из UKM5_FULL_XML_STORE_IDS делаем ПОЛНУЮ пересборку XML,
        а не точечный upsert одного кассира.
    """
    logger.info(
        f"[QR/EMP] Обновление UKM4/UKM5 для storeid={store_id}, "
        f"cashier_id={cashier_id}, role_id={role_id}"
    )

    info = get_store_info(store_id)
    ukm4ip = info.get("ukm4ip")
    is_ukm5 = info.get("is_ukm5", False)
    logger.info(f"[QR/EMP] Store {store_id}: ukm4ip={ukm4ip!r}, is_ukm5={is_ukm5}")

    # -------------------------
    # UKM4 / MySQL import4
    # -------------------------
    if ukm4ip:
        conv = cur = None
        try:
            conv = connect_store_mysql(ukm4ip)
            cur = conv.cursor()

            base_version = _calc_next_signal_version(cur)
            logger.info(
                f"[QR/EMP] Store {store_id} ({ukm4ip}): next version={base_version} "
                f"(по MAX(signal.version))"
            )

            ttl_supported = _mysql_users_supports_ttl_cols(cur, cache_key=str(ukm4ip))
            if ttl_supported:
                now = timezone.now()
                start_date = now.date()
                end_date = (now + datetime.timedelta(days=1)).date()

                cur.execute("""
                    INSERT INTO users (
                        store, id, name, inn, password, role_id, version, deleted,
                        start_date, end_date
                    )
                    VALUES (
                        %s, %s, %s, %s, OLD_PASSWORD(%s), %s, %s, 0,
                        %s, %s
                    )
                    ON DUPLICATE KEY UPDATE
                        name       = VALUES(name),
                        inn        = VALUES(inn),
                        password   = VALUES(password),
                        role_id    = VALUES(role_id),
                        version    = VALUES(version),
                        deleted    = 0,
                        start_date = VALUES(start_date),
                        end_date   = VALUES(end_date)
                """, (
                    store_id,
                    cashier_id,
                    fio,
                    plain_inn,
                    mysql_pwd(password_plain),
                    role_id,
                    base_version,
                    start_date,
                    end_date
                ))
                logger.info(f"[QR/EMP] Store {store_id} ({ukm4ip}): users TTL cols detected, set {start_date}..{end_date}")
            else:
                cur.execute("""
                    INSERT INTO users (store, id, name, inn, password, role_id, version, deleted)
                    VALUES (%s, %s, %s, %s, OLD_PASSWORD(%s), %s, %s, 0)
                    ON DUPLICATE KEY UPDATE
                        name     = VALUES(name),
                        inn      = VALUES(inn),
                        password = VALUES(password),
                        role_id  = VALUES(role_id),
                        version  = VALUES(version),
                        deleted  = 0
                """, (
                    store_id,
                    cashier_id,
                    fio,
                    plain_inn,
                    mysql_pwd(password_plain),
                    role_id,
                    base_version
                ))
                logger.info(f"[QR/EMP] Store {store_id} ({ukm4ip}): users TTL cols NOT found, wrote without dates")

            cur.execute(
                "INSERT INTO `signal`(`signal`,`version`) VALUES ('incr', %s)",
                (base_version,)
            )

            conv.commit()
            logger.info(
                f"[QR/EMP] Store {store_id} ({ukm4ip}): OK users+signal "
                f"(id={cashier_id}, role_id={role_id}, version={base_version})"
            )
        except Exception as e:
            logger.error(f"[QR/EMP] Store {store_id} ({ukm4ip}) MySQL error: {e}", exc_info=True)
            if conv:
                try:
                    conv.rollback()
                except Exception:
                    pass
        finally:
            try:
                if cur:
                    cur.close()
                if conv:
                    conv.close()
            except Exception:
                pass
    else:
        logger.error(f"[QR/EMP] Store {store_id}: ukm4ip not found; пропускаем import4.users/signal")

    # -------------------------
    # UKM5 / XML
    # -------------------------
    if not is_ukm5:
        return

    # Полная пересборка XML для “спец” магазинов
    if int(store_id) in UKM5_FULL_XML_STORE_IDS:
        try:
            xml_path = build_full_ukm5_xml_for_store(store_id)
            logger.info(f"[QR/EMP] Store {store_id}: полный XML пересобран: {xml_path}")
        except Exception as e:
            logger.error(
                f"[QR/EMP] Store {store_id}: ошибка полной пересборки XML/UKM5: {e}",
                exc_info=True
            )
        return

    # Остальные UKM5 — точечное обновление одного кассира
    try:
        xml_path, tree, root = _get_or_create_storecashiers_tree(store_id)

        # удаляем старые записи этого INN
        changed = False
        for cash_el in list(root.findall("cashier")):
            if (cash_el.findtext("INN") or "").strip() == plain_inn:
                root.remove(cash_el)
                changed = True
        if changed:
            logger.info(f"[QR/EMP] Store {store_id}: удалены старые cashier с INN={plain_inn} из {xml_path}")

        cash_el = ET.SubElement(root, "cashier")
        ET.SubElement(cash_el, "roleId").text = str(role_id)
        ET.SubElement(cash_el, "id").text = str(cashier_id)
        ET.SubElement(cash_el, "name").text = fio
        ET.SubElement(cash_el, "INN").text = plain_inn
        ET.SubElement(cash_el, "password").text = password_plain

        _write_xml_with_declaration(xml_path, root, ensure_base=True)
        logger.info(f"[QR/EMP] Store {store_id}: XML обновлён {xml_path}")
    except Exception as e:
        logger.error(f"[QR/EMP] Store {store_id}: ошибка при работе с XML/UKM5: {e}", exc_info=True)
        
# def _update_store_mysql_and_xml_for_single_store(
#     store_id: int,
#     cashier_id: int,
#     role_id: int,
#     plain_inn: str,
#     fio: str,
#     password_plain: str
# ) -> None:
#     """
#     Обновляет кассира по ОДНОМУ магазину:
#       • UKM4 (MySQL import4.users + import4.signal)
#       • UKM5 (XML storeCashiers_... если магазин UKM5)

#     ВАЖНО:
#       • Для магазинов из UKM5_FULL_XML_STORE_IDS делаем ПОЛНУЮ пересборку XML,
#         а не точечный upsert одного кассира.
#     """
#     logger.info(
#         f"[QR/EMP] Обновление UKM4/UKM5 для storeid={store_id}, "
#         f"cashier_id={cashier_id}, role_id={role_id}"
#     )

#     info = get_store_info(store_id)
#     ukm4ip = info.get("ukm4ip")
#     is_ukm5 = info.get("is_ukm5", False)
#     logger.info(f"[QR/EMP] Store {store_id}: ukm4ip={ukm4ip!r}, is_ukm5={is_ukm5}")

#     # -------------------------
#     # UKM4 / MySQL import4
#     # -------------------------
#     if ukm4ip:
#         conv = cur = None
#         try:
#             conv = connect_store_mysql(ukm4ip)
#             cur = conv.cursor()

#             base_version = _calc_next_signal_version(cur)
#             logger.info(
#                 f"[QR/EMP] Store {store_id} ({ukm4ip}): next version={base_version} "
#                 f"(по MAX(signal.version))"
#             )

#             cur.execute("""
#                 INSERT INTO users (store, id, name, inn, password, role_id, version, deleted)
#                 VALUES (%s, %s, %s, %s, OLD_PASSWORD(%s), %s, %s, 0)
#             """, (
#                 store_id,
#                 cashier_id,
#                 fio,
#                 plain_inn,
#                 mysql_pwd(password_plain),
#                 role_id,
#                 base_version
#             ))

#             cur.execute(
#                 "INSERT INTO `signal`(`signal`,`version`) VALUES ('incr', %s)",
#                 (base_version,)
#             )

#             conv.commit()
#             logger.info(
#                 f"[QR/EMP] Store {store_id} ({ukm4ip}): OK users+signal "
#                 f"(id={cashier_id}, role_id={role_id}, version={base_version})"
#             )
#         except Exception as e:
#             logger.error(f"[QR/EMP] Store {store_id} ({ukm4ip}) MySQL error: {e}", exc_info=True)
#             if conv:
#                 try:
#                     conv.rollback()
#                 except Exception:
#                     pass
#         finally:
#             try:
#                 if cur: cur.close()
#                 if conv: conv.close()
#             except Exception:
#                 pass
#     else:
#         logger.error(f"[QR/EMP] Store {store_id}: ukm4ip not found; пропускаем import4.users/signal")

#     # -------------------------
#     # UKM5 / XML
#     # -------------------------
#     if not is_ukm5:
#         return

#     # Полная пересборка XML для “спец” магазинов
#     if int(store_id) in UKM5_FULL_XML_STORE_IDS:
#         try:
#             xml_path = build_full_ukm5_xml_for_store(store_id)
#             logger.info(f"[QR/EMP] Store {store_id}: полный XML пересобран: {xml_path}")
#         except Exception as e:
#             logger.error(
#                 f"[QR/EMP] Store {store_id}: ошибка полной пересборки XML/UKM5: {e}",
#                 exc_info=True
#             )
#         return

#     # Остальные UKM5 — точечное обновление одного кассира
#     try:
#         xml_path, tree, root = _get_or_create_storecashiers_tree(store_id)

#         # удаляем старые записи этого INN
#         changed = False
#         for cash_el in list(root.findall("cashier")):
#             if (cash_el.findtext("INN") or "").strip() == plain_inn:
#                 root.remove(cash_el)
#                 changed = True
#         if changed:
#             logger.info(f"[QR/EMP] Store {store_id}: удалены старые cashier с INN={plain_inn} из {xml_path}")

#         cash_el = ET.SubElement(root, "cashier")
#         ET.SubElement(cash_el, "roleId").text = str(role_id)
#         ET.SubElement(cash_el, "id").text = str(cashier_id)
#         ET.SubElement(cash_el, "name").text = fio
#         ET.SubElement(cash_el, "INN").text = plain_inn
#         ET.SubElement(cash_el, "password").text = password_plain

#         _write_xml_with_declaration(xml_path, root, ensure_base=True)
#         logger.info(f"[QR/EMP] Store {store_id}: XML обновлён {xml_path}")
#     except Exception as e:
#         logger.error(f"[QR/EMP] Store {store_id}: ошибка при работе с XML/UKM5: {e}", exc_info=True)
            
            
            
            

def ensure_plain_inn(value: str) -> str:
    v = (value or "").strip()
    if not (v.isdigit() and len(v) in (10, 12)):
        raise ValueError("ИНН должен содержать 10 или 12 цифр")
    return v


def normalize_phone_ru(phone: str) -> Optional[str]:
    """
    Нормализует телефон к формату +7XXXXXXXXXX.
    Принимает варианты:
      +7 924 000-00-00
      8 (924) 000-00-00
      79240000000
      9240000000
    Если не удаётся нормализовать — возвращает None.
    """
    if not phone:
        return None

    raw = str(phone)
    digits = ''.join(ch for ch in raw if ch.isdigit())

    if not digits:
        return None

    if len(digits) == 11 and digits[0] in ('7', '8'):
        norm = '+7' + digits[1:]
        return norm

    if len(digits) == 10:
        norm = '+7' + digits
        return norm

    if len(digits) == 11:
        return '+' + digits

    return '+' + digits



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
    return base + salt                                           


def _generate_pin_code() -> str:
    return f"{random.randint(0, 9999):04d}"


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

def _oracle_rows_to_jsonable(cur):
    """
    Превращает результат Oracle SELECT в список словарей, пригодных для JSON.
    Даты → ISO-строки, всё непонятное → str().
    """
    cols = [d[0].lower() for d in cur.description]
    items = []

    for row in cur.fetchall():
        obj = {}
        for col, val in zip(cols, row):
            if isinstance(val, (datetime.date, datetime.datetime)):
                obj[col] = val.isoformat(sep=' ')
            elif isinstance(val, (int, float, str, bool)) or val is None:
                obj[col] = val
            else:
                obj[col] = str(val)
        items.append(obj)

    return items



@csrf_exempt
def sm_staff_list(request):
    """
    GET /sm/staff/?limit=100&offset=0&q=иванов

    Возвращает содержимое таблицы SMSTAFF (пользователи Супермага).
    Параметры:
      - limit  (по умолчанию 200, максимум 1000)
      - offset (по умолчанию 0)
      - q      (поиск по surname/name, опционально)
    """
    if request.method != 'GET':
        return JsonResponse(
            {'status': 'error', 'message': 'Только GET'},
            status=405,
            json_dumps_params={'ensure_ascii': False}
        )

    try:
        # Параметры
        try:
            limit = int(request.GET.get('limit', '200'))
        except ValueError:
            limit = 200
        try:
            offset = int(request.GET.get('offset', '0'))
        except ValueError:
            offset = 0

        limit = max(1, min(limit, 1000))
        offset = max(0, offset)

        q = (request.GET.get('q') or '').strip().lower()

        # Базовый SQL
        sql = "SELECT * FROM smstaff"
        binds = {}

        if q:
            sql += """
            WHERE
              LOWER(surname) LIKE :q
              OR LOWER(name) LIKE :q
            """
            binds['q'] = f"%{q}%"

        # Пагинация (Oracle 12+)
        sql += """
        ORDER BY surname
        OFFSET :off ROWS FETCH NEXT :lim ROWS ONLY
        """
        binds['off'] = offset
        binds['lim'] = limit

        conn = cur = None
        try:
            conn = connect_oracle_supermag()
            cur = conn.cursor()
            cur.execute(sql, binds)
            items = _oracle_rows_to_jsonable(cur)
        finally:
            try:
                if cur:
                    cur.close()
                if conn:
                    conn.close()
            except Exception:
                pass

        return JsonResponse(
            {
                'status': 'ok',
                'count': len(items),
                'limit': limit,
                'offset': offset,
                'items': items,
            },
            json_dumps_params={'ensure_ascii': False, 'indent': 2}
        )

    except Exception as e:
        logger.exception("[SM/STAFF_LIST] Unexpected error")
        return JsonResponse(
            {'status': 'error', 'message': str(e)},
            status=500,
            json_dumps_params={'ensure_ascii': False}
        )



@csrf_exempt
def sm_staff_columns(request):
    """
    GET /sm/staff/columns/

    Возвращает структуру таблицы SMSTAFF (колонки, типы, длину, nullable).
    """
    if request.method != 'GET':
        return JsonResponse(
            {'status': 'error', 'message': 'Только GET'},
            status=405,
            json_dumps_params={'ensure_ascii': False}
        )

    conn = cur = None
    try:
        sql = """
        SELECT
          column_id,
          column_name,
          data_type,
          data_length,
          nullable
        FROM all_tab_columns
        WHERE owner = 'SUPERMAG'
          AND table_name = 'SMSTAFF'
        ORDER BY column_id
        """
        conn = connect_oracle_supermag()
        cur = conn.cursor()
        cur.execute(sql)
        items = _oracle_rows_to_jsonable(cur)

        return JsonResponse(
            {'status': 'ok', 'count': len(items), 'columns': items},
            json_dumps_params={'ensure_ascii': False, 'indent': 2}
        )

    except Exception as e:
        logger.exception("[SM/STAFF_COLUMNS] Unexpected error")
        return JsonResponse(
            {'status': 'error', 'message': str(e)},
            status=500,
            json_dumps_params={'ensure_ascii': False}
        )
    finally:
        try:
            if cur:
                cur.close()
            if conn:
                conn.close()
        except Exception:
            pass
        
        
        
@csrf_exempt
def sm_sql(request):
    """
    POST /sm/sql/

    ВХОД (JSON):
    {
      "sql": "select ...",
      "binds": {"p1": "val", ...}   # опционально
    }

    Ограничения:
      • Разрешены только SELECT/WITH (без insert/update/delete/alter и т.п.).
    """
    if request.method != 'POST':
        return JsonResponse(
            {'status': 'error', 'message': 'Только POST'},
            status=405,
            json_dumps_params={'ensure_ascii': False}
        )

    raw_body = ""
    try:
        raw_body = request.body.decode('utf-8') if request.body else "{}"
        data = json.loads(raw_body)
    except Exception as e:
        logger.error(f"[SM/SQL] JSON parse error: {e}; body={raw_body!r}")
        return JsonResponse(
            {'status': 'error', 'message': 'Некорректный JSON'},
            status=400,
            json_dumps_params={'ensure_ascii': False}
        )

    sql = (data.get('sql') or '').strip()
    binds = data.get('binds') or {}

    if not sql:
        return JsonResponse(
            {'status': 'error', 'message': 'Пустой sql'},
            status=400,
            json_dumps_params={'ensure_ascii': False}
        )

    sql_lower = sql.lower().lstrip()
    if not (sql_lower.startswith('select') or sql_lower.startswith('with')):
        return JsonResponse(
            {'status': 'error', 'message': 'Разрешены только SELECT/WITH запросы'},
            status=400,
            json_dumps_params={'ensure_ascii': False}
        )

    conn = cur = None
    try:
        conn = connect_oracle_supermag()
        cur = conn.cursor()
        cur.execute(sql, binds)
        items = _oracle_rows_to_jsonable(cur)

        return JsonResponse(
            {
                'status': 'ok',
                'count': len(items),
                'items': items,
            },
            json_dumps_params={'ensure_ascii': False, 'indent': 2}
        )

    except Exception as e:
        logger.exception("[SM/SQL] Unexpected error")
        return JsonResponse(
            {'status': 'error', 'message': str(e)},
            status=500,
            json_dumps_params={'ensure_ascii': False}
        )
    finally:
        try:
            if cur:
                cur.close()
            if conn:
                conn.close()
        except Exception:
            pass





def get_dbname_for_smstore(smstore_id: int) -> Optional[str]:
    """
    Возвращает имя базы (REP.DBNAME) для магазина SMSTORELOCATIONS.id = smstore_id,
    либо None, если свойства нет.

    SELECT propval
    FROM smstoreproperties
    WHERE storeloc = :sid AND propid = 'REP.DBNAME'
    """
    conn = cur = None
    try:
        conn = connect_oracle_supermag()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT propval
            FROM smstoreproperties
            WHERE storeloc = :sid
              AND propid = 'REP.DBNAME'
            """,
            sid=smstore_id
        )
        row = cur.fetchone()
        if not row:
            logger.warning(f"[SM/DBNAME] REP.DBNAME not found for STORELOC={smstore_id}")
            return None

        dbname = row[0]
        if dbname is None:
            logger.warning(f"[SM/DBNAME] REP.DBNAME is NULL for STORELOC={smstore_id}")
            return None

        dbname_str = str(dbname).strip()
        logger.info(f"[SM/DBNAME] STORELOC={smstore_id} -> DBNAME={dbname_str!r}")
        return dbname_str

    except Exception as e:
        logger.exception(f"[SM/DBNAME] Error while fetching dbname for STORELOC={smstore_id}: {e}")
        # Тут либо возвращаем None, либо пробрасываем. Явно пробрасываем, а view вернёт 500.
        raise
    finally:
        try:
            if cur:
                cur.close()
            if conn:
                conn.close()
        except Exception:
            pass


@csrf_exempt
def sm_get_dbname(request):
    """
    GET  /sm/dbname/?storeId=225
    или
    POST /sm/dbname/  { "storeId": 225 }
    """
    if request.method not in ('GET', 'POST'):
        return JsonResponse({'status': 'error', 'message': 'Только GET или POST'}, status=405)

    try:
        raw_store = ""

        if request.method == 'GET':
            raw_store = (
                (request.GET.get('storeId') or
                 request.GET.get('smstore') or
                 request.GET.get('id') or "")
                .strip()
            )
            logger.info(f"[SM/GET_DBNAME] GET, raw_store={raw_store!r}")
        else:
            body = request.body.decode('utf-8') if request.body else "{}"
            logger.info(f"[SM/GET_DBNAME] POST, raw_body={body!r}")
            try:
                data = json.loads(body)
            except Exception as e:
                logger.error(f"[SM/GET_DBNAME] JSON parse error: {e}")
                return JsonResponse({'status': 'error', 'message': 'Некорректный JSON'}, status=400)
            raw_store = str(
                data.get('storeId') or
                data.get('smstore') or
                data.get('id') or ""
            ).strip()
            logger.info(f"[SM/GET_DBNAME] Parsed raw_store={raw_store!r}")

        if not raw_store:
            logger.error("[SM/GET_DBNAME] storeId/smstore не передан")
            return JsonResponse(
                {'status': 'error', 'message': 'Не передан storeId (smstore)'},
                status=400
            )

        try:
            smstore_id = int(raw_store)
        except ValueError:
            logger.error(f"[SM/GET_DBNAME] storeId не число: {raw_store!r}")
            return JsonResponse(
                {'status': 'error', 'message': 'storeId (smstore) должен быть числом'},
                status=400
            )

        logger.info(f"[SM/GET_DBNAME] Ищем REP.DBNAME для STORELOC={smstore_id}")

        try:
            dbname = get_dbname_for_smstore(smstore_id)
        except Exception as e:
            logger.exception(f"[SM/GET_DBNAME] Ошибка внутри get_dbname_for_smstore: {e}")
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)

        if not dbname:
            logger.warning(f"[SM/GET_DBNAME] REP.DBNAME не найден для STORELOC={smstore_id}")
            return JsonResponse(
                {'status': 'error',
                 'message': f'Имя базы (REP.DBNAME) не найдено для STORELOC={smstore_id}'},
                status=404
            )

        logger.info(
            f"[SM/GET_DBNAME] OK: STORELOC={smstore_id} → DBNAME={dbname!r}"
        )
        return JsonResponse({
            'status': 'ok',
            'smstore': smstore_id,
            'dbname': dbname,
        })

    except Exception as e:
        logger.exception("[SM/GET_DBNAME] Unexpected error")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@csrf_exempt
def sm_list_databases(request):
    """
    GET /sm/databases/
    Список магазинов + их база (REP.DBNAME)
    """
    if request.method != 'GET':
        return JsonResponse({'status': 'error', 'message': 'Только GET'}, status=405)

    sql = """
        SELECT
          l.id       AS smstore,
          l.name     AS name,
          p.propval  AS dbname
        FROM smstorelocations l
        LEFT JOIN smstoreproperties p
          ON p.storeloc = l.id
         AND p.propid   = 'REP.DBNAME'
        WHERE
          l.name NOT LIKE 'я%%'
          AND l.name NOT LIKE '%%ТЕСТ%%'
          AND l.formatid IN (19, 20, 9, 8, 6, 22, 23)
        ORDER BY l.name
    """

    conn = cur = None
    try:
        logger.info("[SM/LIST_DB] Старт запроса списка магазинов + баз")
        conn = connect_oracle_supermag()
        cur = conn.cursor()
        cur.execute(sql)

        items = []
        rows = cur.fetchall()
        logger.info(f"[SM/LIST_DB] Получено строк из Oracle: {len(rows)}")

        for smstore, name, dbname in rows:
            name_s = (name or "").strip()
            db_s = (dbname or "").strip() if dbname is not None else ""
            logger.info(
                "[SM/LIST_DB] STORE: smstore=%s, name=%r, dbname=%r",
                smstore, name_s, db_s
            )
            items.append({
                "smstore": smstore,
                "name": name_s,
                "dbname": db_s,
            })

        logger.info(f"[SM/LIST_DB] Итог: count={len(items)}")
        return JsonResponse({
            "status": "ok",
            "count": len(items),
            "items": items
        })

    except Exception as e:
        logger.exception("[SM/LIST_DB] Unexpected error")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    finally:
        try:
            if cur:
                cur.close()
            if conn:
                conn.close()
        except Exception:
            pass














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

        _write_xml_with_declaration(fpath, root, ensure_base=False)

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

                        base_version = _calc_next_signal_version(cur)

                        cur.execute("""
                            INSERT INTO users (store, id, name, inn, password, role_id, version, deleted)
                            VALUES (%s, %s, %s, %s, OLD_PASSWORD(%s), %s, %s, 0)
                        """, (sid, cashier_id, fio, inn, mysql_pwd(password_plain), role_id, base_version))

                        cur.execute("INSERT INTO `signal`(`signal`, `version`) VALUES ('incr', %s)", (base_version,))
                        conv.commit()
                        conv.close()
                        logger.info(
                            f"[MySQL:{ukm4ip}] Добавлен кассир store={sid}, "
                            f"id={cashier_id}, version={base_version}"
                        )
                    except Exception as e:
                        logger.error(f"[MySQL:{ukm4ip}] Ошибка вставки для store={sid}: {e}")
                else:
                    logger.error(f"[Oracle] Не найден UKM4IP для storeid={sid}. Пропуск записи в MySQL.")

                # XML для UKM5
                if is_ukm5:
                    if sid == UKM5_FULL_XML_STORE_ID:
                        # Для магазина 2013 — полный файл, со ВСЕМИ кассирами
                        try:
                            full_xml_path = build_full_ukm5_xml_for_store(sid)
                            if full_xml_path:
                                xml_paths.append(full_xml_path)
                                logger.info(
                                    f"[XML] Полный XML для УКМ5 магазина {sid} пересобран: {full_xml_path}"
                                )
                        except Exception as e:
                            logger.error(
                                f"[XML] Ошибка полной пересборки для storeid={sid}: {e}"
                            )
                    else:
                        # Остальные магазины — старое поведение (вставка одного кассира)
                        try:
                            xml_path, tree, root = _get_or_create_storecashiers_tree(sid)

                            cashier_el = ET.SubElement(root, "cashier")
                            ET.SubElement(cashier_el, "roleId").text = str(role_id)
                            ET.SubElement(cashier_el, "id").text = str(cashier_id)
                            ET.SubElement(cashier_el, "name").text = fio
                            ET.SubElement(cashier_el, "INN").text = inn
                            ET.SubElement(cashier_el, "password").text = password_plain

                            _write_xml_with_declaration(xml_path, root, ensure_base=True)
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

# def validate_and_create_record(payload, required_fields, action_name="CREATE"):
#     """
#     1) Смотрим payload['data'] (других полей в запросе нет).
#     2) Проверяем наличие всех required_fields.
#     3) Если чего-то не хватает → пишем в queue (status='failed') + только в MODUL_logs.
#     4) Если всё ок → queue (status='pending'), без логирования.

#     Возвращает (final_status, missing_fields).
#     """
#     data = payload.get('data', {})
#     if not isinstance(data, dict):
#         data = {}

#     final_status = 'pending'
#     attempts = 0
#     last_attempt = None

#     # Проверяем обязательные поля
#     missing = [f for f in required_fields if not data.get(f)]

#     # Хэшируем ИНН (если он есть и валиден)
#     if data.get('inn'):
#         try:
#             data['inn'] = encrypt_inn(data['inn'])
#         except ValueError:
#             missing.append('inn')

#     if missing:
#         final_status = 'failed'

#     # Создаём запись в queue
#     Queue.objects.create(
#         data=data,
#         attempts=attempts,
#         status=final_status,
#         last_attempt=last_attempt
#     )

#     # Если failed → фиксируем только в MODUL_logs
#     if final_status == 'failed':
#         MODUL_logs.objects.create(
#             data={                       # JSONB-поле в таблице
#                 "error": f"Незаполненные поля: {missing}",
#                 "payload": data
#             }
#         )

#     return final_status, missing

def validate_and_create_record(payload, required_fields, action_name="CREATE"):
    """
    1) Смотрим payload['data'] (других полей в запросе нет).
    2) Проверяем наличие всех required_fields.
    3) Если чего-то не хватает → пишем в queue (status='failed') + только в MODUL_logs.
    4) Если всё ок → queue (status='pending'), без логирования.

    Дополнительно:
    - сохраняем исходный ИНН в data['id_compare']
    - в data['inn'] сохраняем SHA-256 (как раньше)
    """
    data = payload.get('data', {})
    if not isinstance(data, dict):
        data = {}
    else:
        # чтобы не мутировать исходный payload
        data = dict(data)

    final_status = 'pending'
    attempts = 0
    last_attempt = None

    # Проверяем обязательные поля (до любых преобразований)
    missing = [f for f in required_fields if not data.get(f)]

    # ИНН: сохраняем чистый в id_compare + хэшируем в inn
    raw_inn = data.get('inn')
    if raw_inn:
        try:
            plain_inn = ensure_plain_inn(str(raw_inn))  
            data['id_compare'] = plain_inn        
            data['inn'] = encrypt_inn(plain_inn)      
        except ValueError:
            missing.append('inn')
            data.pop('id_compare', None)  # на всякий случай
    # если raw_inn пустой — missing уже содержит 'inn', если он в required_fields

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
            data={
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

    # для id кассира (как было у тебя)
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
            conv = cur = None
            try:
                conv = connect_store_mysql(ukm4ip)
                cur = conv.cursor()

                base_version = _calc_next_signal_version(cur)

                ttl_supported = _mysql_users_supports_ttl_cols(cur, cache_key=str(ukm4ip))
                if ttl_supported:
                    start_date = now.date()
                    end_date = expiration.date()

                    cur.execute("""
                        INSERT INTO users (
                            store, id, name, inn, password, role_id, version, deleted,
                            start_date, end_date
                        )
                        VALUES (
                            %s, %s, %s, %s, OLD_PASSWORD(%s), %s, %s, 0,
                            %s, %s
                        )
                        ON DUPLICATE KEY UPDATE
                            name       = VALUES(name),
                            inn        = VALUES(inn),
                            password   = VALUES(password),
                            role_id    = VALUES(role_id),
                            version    = VALUES(version),
                            deleted    = 0,
                            start_date = VALUES(start_date),
                            end_date   = VALUES(end_date)
                    """, (
                        sid,
                        cashier_id,
                        user.full_name,
                        user.employee_id,
                        mysql_pwd(new_password),
                        ukm_user.roleid,
                        base_version,
                        start_date,
                        end_date
                    ))
                else:
                    cur.execute("""
                        INSERT INTO users (store, id, name, inn, password, role_id, version, deleted)
                        VALUES (%s, %s, %s, %s, OLD_PASSWORD(%s), %s, %s, 0)
                        ON DUPLICATE KEY UPDATE
                            name     = VALUES(name),
                            inn      = VALUES(inn),
                            password = VALUES(password),
                            role_id  = VALUES(role_id),
                            version  = VALUES(version),
                            deleted  = 0
                    """, (
                        sid,
                        cashier_id,
                        user.full_name,
                        user.employee_id,
                        mysql_pwd(new_password),
                        ukm_user.roleid,
                        base_version
                    ))

                cur.execute(
                    "INSERT INTO `signal`(`signal`, `version`) VALUES ('incr', %s)",
                    (base_version,)
                )

                conv.commit()
                logger.info(
                    f"[MySQL:{ukm4ip}] Пароль обновлён store={sid}, "
                    f"id={cashier_id}, version={base_version}"
                )
            except Exception as e:
                logger.error(f"[MySQL:{ukm4ip}] Ошибка обновления пароля для store={sid}: {e}", exc_info=True)
                if conv:
                    try:
                        conv.rollback()
                    except Exception:
                        pass
            finally:
                try:
                    if cur:
                        cur.close()
                    if conv:
                        conv.close()
                except Exception:
                    pass
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
            if sid == UKM5_FULL_XML_STORE_ID:
                # Для магазина 2013 при смене пароля пересобираем полный XML
                xml_path = build_full_ukm5_xml_for_store(sid)
                logger.info(
                    f"[XML] Полный XML пересобран при регенерации QR для storeid={sid}: {xml_path}"
                )
            else:
                # Остальные магазины — точечное обновление одного кассира
                xml_path, tree, root = _get_or_create_storecashiers_tree(sid)

                for el in list(root.findall("cashier")):
                    if (el.findtext("INN") or "").strip() == str(user.employee_id).strip():
                        root.remove(el)

                c_el = ET.SubElement(root, "cashier")
                ET.SubElement(c_el, "roleId").text = str(ukm_user.roleid)
                ET.SubElement(c_el, "id").text = str(next_free_id)
                ET.SubElement(c_el, "name").text = user.full_name
                ET.SubElement(c_el, "INN").text = str(user.employee_id).strip()
                ET.SubElement(c_el, "password").text = new_password

                _write_xml_with_declaration(xml_path, root, ensure_base=True)
                logger.info(f"[XML] Обновлён при регенерации QR: {xml_path}")
                next_free_id += 1
        except Exception as exc:
            logger.error(f"[XML] Ошибка для {sid}: {exc}", exc_info=True)
# def regenerate_qr(user):
#     new_password = build_user_password(user.employee_id)
#     now = timezone.now()
#     expiration = now + datetime.timedelta(days=1)

#     # PostgreSQL: QR + open_in_system
#     QRCode.objects.filter(user=user).delete()
#     QRCode.objects.create(user=user, qr_data=new_password, created_at=now, expires_at=expiration)
#     OpenInSystem.objects.filter(user_id=user.id, system_id=9).update(password=new_password)

#     # для id кассира
#     ukm_conn = connect_ukm()
#     ukm_cursor = ukm_conn.cursor()
#     ukm_cursor.execute("SELECT MAX(id)+1 AS next_id FROM trm_in_users")
#     cashier_id_base = ukm_cursor.fetchone()['next_id'] or 1
#     ukm_conn.close()

#     ukm_emp_id = get_trm_employee_id(user.employee_id, user.full_name)
#     ukm_users = list(UKMUser.objects.filter(user_id=user.id))
#     cashier_counter = 0

#     for ukm_user in ukm_users:
#         sid = ukm_user.storeid
#         cashier_id = ukm_emp_id if ukm_emp_id else (cashier_id_base + cashier_counter)

#         info = get_store_info(sid)
#         ukm4ip = info.get("ukm4ip")

#         if ukm4ip:
#             try:
#                 conv = connect_store_mysql(ukm4ip)
#                 cur = conv.cursor()

#                 base_version = _calc_next_signal_version(cur)

#                 cur.execute("""
#                     INSERT INTO users (store, id, name, inn, password, role_id, version, deleted)
#                     VALUES (%s, %s, %s, %s, OLD_PASSWORD(%s), %s, %s, 0)
#                 """, (
#                     sid,
#                     cashier_id,
#                     user.full_name,
#                     user.employee_id,
#                     mysql_pwd(new_password),
#                     ukm_user.roleid,
#                     base_version
#                 ))
#                 cur.execute("INSERT INTO `signal`(`signal`, `version`) VALUES ('incr', %s)", (base_version,))
#                 conv.commit()
#                 conv.close()
#                 logger.info(
#                     f"[MySQL:{ukm4ip}] Пароль обновлён store={sid}, "
#                     f"id={cashier_id}, version={base_version}"
#                 )
#             except Exception as e:
#                 logger.error(f"[MySQL:{ukm4ip}] Ошибка обновления пароля для store={sid}: {e}")
#         else:
#             logger.error(f"[Oracle] Не найден UKM4IP для storeid={sid}. Пропуск записи в MySQL.")

#         cashier_counter += 1

#     # XML для UKM5 (обновляем записи)
#     next_free_id = cashier_id_base + cashier_counter

#     for ukm_user in ukm_users:
#         sid = ukm_user.storeid
#         if not is_ukm5_store(sid):
#             continue

#         try:
#             if sid == UKM5_FULL_XML_STORE_ID:
#                 # Для магазина 2013 при смене пароля пересобираем полный XML
#                 xml_path = build_full_ukm5_xml_for_store(sid)
#                 logger.info(
#                     f"[XML] Полный XML пересобран при регенерации QR для storeid={sid}: {xml_path}"
#                 )
#             else:
#                 # Остальные магазины — точечное обновление одного кассира
#                 xml_path, tree, root = _get_or_create_storecashiers_tree(sid)

#                 for el in list(root.findall("cashier")):
#                     if el.findtext("INN") == user.employee_id:
#                         root.remove(el)

#                 c_el = ET.SubElement(root, "cashier")
#                 ET.SubElement(c_el, "roleId").text = str(ukm_user.roleid)
#                 ET.SubElement(c_el, "id").text = str(next_free_id)
#                 ET.SubElement(c_el, "name").text = user.full_name
#                 ET.SubElement(c_el, "INN").text = user.employee_id
#                 ET.SubElement(c_el, "password").text = new_password

#                 _write_xml_with_declaration(xml_path, root, ensure_base=True)
#                 logger.info(f"[XML] Обновлён при регенерации QR: {xml_path}")
#                 next_free_id += 1
#         except Exception as exc:
#             logger.error(f"[XML] Ошибка для {sid}: {exc}")



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
def agent_auth_start(request):
    """
    POST /agent/auth/start/
    {
      "phone": "+7924..."
    }

    Логика:
      1) Ищем пользователя по телефону (варианты формата).
      2) Ищем его магазины (ukm_users + stores).
      3) Создаём AuthSession в auth_sessions.
         - если магазинов > 1 → step=SELECT_STORE, отдаём список магазинов,
           статус сессии 'pending'
         - если магазин 1 → сразу шлём PIN в Telegram, статус 'pin_sent',
           step=WAIT_PIN
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)

    try:
        data = json.loads(request.body.decode('utf-8') if request.body else "{}")
    except Exception as e:
        logger.error(f"[AGENT_AUTH/START] JSON parse error: {e}")
        return JsonResponse({'status': 'error', 'message': 'Некорректный JSON'}, status=400)

    phone_raw = str(data.get('phone') or "").strip()
    if not phone_raw:
        return JsonResponse({'status': 'error', 'message': 'Не указан phone'}, status=400)

    phone_norm = normalize_phone_ru(phone_raw)
    phone_candidates = {phone_raw}
    if phone_norm:
        phone_candidates.add(phone_norm)

    users_qs = User.objects.filter(phone__in=list(phone_candidates))
    count = users_qs.count()

    if count == 0:
        return JsonResponse({'status': 'error', 'message': 'Пользователь с таким телефоном не найден'}, status=404)

    if count > 1:
        logger.error(
            f"[AGENT_AUTH/START] Несколько пользователей для phone={phone_candidates}: "
            f"ids={list(users_qs.values_list('id', flat=True))}"
        )
        return JsonResponse({
            'status': 'error',
            'message': 'Найдено несколько пользователей с таким телефоном, обратитесь к администратору'
        }, status=409)

    user = users_qs.first()

    if not user.tg_id:
        return JsonResponse({
            'status': 'error',
            'message': 'Для пользователя не указан tg_id, отправка PIN в Telegram невозможна'
        }, status=400)

    ukm_links = list(UKMUser.objects.filter(user=user).values('storeid', 'roleid'))
    if not ukm_links:
        return JsonResponse({
            'status': 'error',
            'message': 'У пользователя нет магазинов в ukm_users'
        }, status=404)

    store_ids = [row['storeid'] for row in ukm_links]
    store_map = {
        s.ukm4store: s
        for s in Store.objects.filter(ukm4store__in=store_ids)
    }

    stores_payload = []
    for row in ukm_links:
        sid = row['storeid']
        store_obj = store_map.get(sid)
        stores_payload.append({
            'ukm_storeid': sid,
            'smstore': store_obj.smstore if store_obj else None,
            'name': store_obj.name if store_obj else '',
            'address': store_obj.address if store_obj else '',
            'roleid': row['roleid'],
        })

    # UUID для session_id: и в БД, и в ответе
    session_uuid = uuid.uuid4()
    session_id = str(session_uuid)

    # Один магазин – сразу PIN
    if len(ukm_links) == 1:
        selected_sid = ukm_links[0]['storeid']
        pin = _generate_pin_code()
        expires_at = timezone.now() + datetime.timedelta(minutes=PIN_TTL_MINUTES)
        pin_hash = hashlib.sha256(pin.encode('utf-8')).hexdigest()

        AuthSession.objects.create(
            session_id=session_uuid,
            user=user,
            storeid=selected_sid,
            pin_hash=pin_hash,
            status='pin_sent',
            attempts=0,
            expires_at=expires_at,
        )

        ok = send_telegram_to_user(
            user,
            f"Ваш код авторизации: {pin}\nОн действителен {PIN_TTL_MINUTES} минут(ы)."
        )
        if not ok:
            logger.error(f"[AGENT_AUTH/START] Не удалось отправить PIN user_id={user.id}")
            return JsonResponse({
                'status': 'error',
                'message': 'Не удалось отправить PIN в Telegram'
            }, status=500)

        store_obj = store_map.get(selected_sid)
        selected_store_payload = {
            'ukm_storeid': selected_sid,
            'smstore': store_obj.smstore if store_obj else None,
            'name': store_obj.name if store_obj else '',
            'address': store_obj.address if store_obj else '',
        }

        return JsonResponse({
            'status': 'ok',
            'session_id': session_id,
            'step': 'WAIT_PIN',
            'expires_at': expires_at.isoformat(),
            'user': {
                'id': user.id,
                'fio': user.full_name,
            },
            'selected_store': selected_store_payload,
        })

    # Несколько магазинов – сначала выбор магазина
    expires_at = timezone.now() + datetime.timedelta(minutes=SESSION_TTL_MINUTES)
    AuthSession.objects.create(
        session_id=session_uuid,
        user=user,
        storeid=None,
        pin_hash=None,
        status='pending',
        attempts=0,
        expires_at=expires_at,
    )

    return JsonResponse({
        'status': 'ok',
        'session_id': session_id,
        'step': 'SELECT_STORE',
        'expires_at': expires_at.isoformat(),
        'user': {
            'id': user.id,
            'fio': user.full_name,
        },
        'stores': stores_payload,
    })
    
    
@csrf_exempt
def agent_auth_select_store(request):
    """
    POST /agent/auth/select_store/
    {
      "session_id": "...",
      "storeid": 1001   # ukm4store (= ukm_users.storeid)
    }
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)

    try:
        data = json.loads(request.body.decode('utf-8') if request.body else "{}")
    except Exception as e:
        logger.error(f"[AGENT_AUTH/SELECT_STORE] JSON parse error: {e}")
        return JsonResponse({'status': 'error', 'message': 'Некорректный JSON'}, status=400)

    session_id = str(data.get('session_id') or "").strip()
    store_raw = data.get('storeid') or data.get('ukm_storeid')

    if not session_id:
        return JsonResponse({'status': 'error', 'message': 'Не указан session_id'}, status=400)
    if store_raw is None:
        return JsonResponse({'status': 'error', 'message': 'Не указан storeid'}, status=400)

    try:
        storeid = int(store_raw)
    except ValueError:
        return JsonResponse({'status': 'error', 'message': 'storeid должен быть числом'}, status=400)

    sess = (
        AuthSession.objects
        .select_related('user')
        .filter(session_id=session_id)
        .first()
    )
    if not sess:
        return JsonResponse({
            'status': 'error',
            'message': 'Сессия не найдена или уже истекла'
        }, status=404)

    now = timezone.now()
    if now > sess.expires_at:
        sess.status = 'expired'
        sess.save(update_fields=['status', 'updated_at'])
        return JsonResponse({
            'status': 'error',
            'message': 'Сессия истекла, начните авторизацию заново'
        }, status=400)

    if sess.status != 'pending':
        return JsonResponse({
            'status': 'error',
            'message': f'Неверное состояние сессии: {sess.status}, нужно начать заново'
        }, status=400)

    # Проверяем, что этот магазин действительно привязан к пользователю
    if not UKMUser.objects.filter(user=sess.user, storeid=storeid).exists():
        return JsonResponse({
            'status': 'error',
            'message': 'У пользователя нет доступа к указанному магазину'
        }, status=403)

    pin = _generate_pin_code()
    expires_at = timezone.now() + datetime.timedelta(minutes=PIN_TTL_MINUTES)
    pin_hash = hashlib.sha256(pin.encode('utf-8')).hexdigest()

    sess.storeid = storeid
    sess.pin_hash = pin_hash
    sess.status = 'pin_sent'
    sess.attempts = 0
    sess.expires_at = expires_at
    sess.save(update_fields=[
        'storeid', 'pin_hash', 'status', 'attempts', 'expires_at', 'updated_at'
    ])

    ok = send_telegram_to_user(
        sess.user,
        f"Ваш код авторизации: {pin}\nОн действителен {PIN_TTL_MINUTES} минут(ы)."
    )
    if not ok:
        logger.error(f"[AGENT_AUTH/SELECT_STORE] Не удалось отправить PIN user_id={sess.user.id}")
        return JsonResponse({
            'status': 'error',
            'message': 'Не удалось отправить PIN в Telegram'
        }, status=500)

    store_obj = Store.objects.filter(ukm4store=storeid).first()
    store_payload = {
        'ukm_storeid': storeid,
        'smstore': store_obj.smstore if store_obj else None,
        'name': store_obj.name if store_obj else '',
        'address': store_obj.address if store_obj else '',
    }

    return JsonResponse({
        'status': 'ok',
        'session_id': session_id,
        'step': 'WAIT_PIN',
        'expires_at': expires_at.isoformat(),
        'store': store_payload,
    })
    
    
@csrf_exempt
def agent_auth_verify_pin(request):
    """
    POST /agent/auth/verify_pin/
    {
      "session_id": "...",
      "pin": "1234"
    }

    При успешной проверке возвращает:
    {
      "status": "ok",
      "username": "...",
      "password": "...",
      "dbname": "REP_DBNAME",
      "store": {
        "ukm_storeid": ...,
        "smstore": ...,
        "name": "...",
        "address": "..."
      }
    }
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)

    try:
        data = json.loads(request.body.decode('utf-8') if request.body else "{}")
    except Exception as e:
        logger.error(f"[AGENT_AUTH/VERIFY_PIN] JSON parse error: {e}")
        return JsonResponse({'status': 'error', 'message': 'Некорректный JSON'}, status=400)

    session_id = str(data.get('session_id') or "").strip()
    pin_input = str(data.get('pin') or "").strip()

    if not session_id or not pin_input:
        return JsonResponse({'status': 'error', 'message': 'Нужны session_id и pin'}, status=400)

    sess = (
        AuthSession.objects
        .select_related('user')
        .filter(session_id=session_id)
        .first()
    )
    if not sess:
        return JsonResponse({
            'status': 'error',
            'message': 'Сессия не найдена или уже истекла'
        }, status=404)

    now = timezone.now()

    # Проверка тайм-аута
    if now > sess.expires_at:
        sess.status = 'expired'
        sess.save(update_fields=['status', 'updated_at'])
        return JsonResponse({
            'status': 'error',
            'message': 'PIN истёк, начните авторизацию заново'
        }, status=400)

    # Проверка статуса
    if sess.status != 'pin_sent':
        return JsonResponse({
            'status': 'error',
            'message': f'Неверное состояние сессии: {sess.status}, начните заново'
        }, status=400)

    # Проверка попыток
    if sess.attempts >= MAX_PIN_ATTEMPTS:
        sess.status = 'blocked'
        sess.save(update_fields=['status', 'updated_at'])
        return JsonResponse({
            'status': 'error',
            'message': 'Превышено количество попыток, начните заново'
        }, status=400)

    # Сравнение PIN по хэшу
    pin_hash_input = hashlib.sha256(pin_input.encode('utf-8')).hexdigest()
    if pin_hash_input != (sess.pin_hash or ""):
        sess.attempts = sess.attempts + 1
        if sess.attempts >= MAX_PIN_ATTEMPTS:
            sess.status = 'blocked'
        sess.save(update_fields=['attempts', 'status', 'updated_at'])

        attempts_left = max(0, MAX_PIN_ATTEMPTS - sess.attempts)
        if attempts_left == 0:
            return JsonResponse({
                'status': 'error',
                'message': 'Неверный PIN, попытки закончились',
                'attempts_left': 0
            }, status=400)
        else:
            return JsonResponse({
                'status': 'error',
                'message': 'Неверный PIN',
                'attempts_left': attempts_left
            }, status=400)

    # PIN верный
    sess.status = 'success'
    sess.save(update_fields=['status', 'updated_at'])

    if not sess.storeid:
        return JsonResponse({
            'status': 'error',
            'message': 'Для сессии не выбран магазин, начните заново'
        }, status=500)

    # Логин/пароль из open_in_system (system_id=9)
    creds_qs = OpenInSystem.objects.filter(
        user_id=sess.user.id,
        system_id=9,
        status=True
    )
    if not creds_qs.exists():
        logger.error(f"[AGENT_AUTH/VERIFY_PIN] Нет записи в open_in_system для user_id={sess.user.id}")
        return JsonResponse({
            'status': 'error',
            'message': 'Для пользователя не найдены учётные данные (open_in_system)'
        }, status=500)

    if creds_qs.count() > 1:
        logger.warning(
            f"[AGENT_AUTH/VERIFY_PIN] Несколько записей open_in_system для user_id={sess.user.id}, system_id=9"
        )
    creds = creds_qs.order_by('id').first()

    username = creds.username
    password = creds.password

    # Информация о магазине + DBNAME
    ukm_storeid = sess.storeid
    store_obj = Store.objects.filter(ukm4store=ukm_storeid).first()

    smstore = store_obj.smstore if store_obj else None
    dbname = None
    if smstore is not None:
        try:
            dbname = get_dbname_for_smstore(smstore)
        except Exception as e:
            logger.error(
                f"[AGENT_AUTH/VERIFY_PIN] Ошибка получения DBNAME для smstore={smstore}: {e}",
                exc_info=True
            )
            dbname = None

    store_payload = {
        'ukm_storeid': ukm_storeid,
        'smstore': smstore,
        'name': store_obj.name if store_obj else '',
        'address': store_obj.address if store_obj else '',
    }

    return JsonResponse({
        'status': 'ok',
        'username': username,
        'password': password,
        'dbname': dbname,
        'store': store_payload,
    })




def _get_existing_open_password(
    *,
    user_id: int,
    system_id: int = 9
) -> tuple[Optional[str], Optional[str], Optional[int]]:
    """
    READ-ONLY.
    Возвращает (password, username, open_in_system_id) из open_in_system
    для user_id + system_id.

    • status НЕ проверяем.
    • Если записей несколько — берём последнюю по id и пишем warning.
    """
    rows = list(
        OpenInSystem.objects
        .filter(user_id=user_id, system_id=system_id)
        .order_by("-id")[:2] 
    )
    if not rows:
        return None, None, None

    if len(rows) > 1:
        logger.warning(
            f"[OPEN_IN_SYSTEM] Несколько записей для user_id={user_id}, system_id={system_id}. "
            f"Возвращаю последнюю по id."
        )

    row = rows[0]
    password = (row.password or "").strip() or None
    username = (row.username or "").strip() or None
    return password, username, row.id











@csrf_exempt
def get_qr_code_by_tg(request):
    """
    READ-ONLY.

    POST {"tg_id": "..."} -> возвращает существующий open_in_system.password (system_id=9)

    Делает:
      - находит пользователя по tg_id
      - валидирует employee_id как ИНН
      - проверяет ukm_users
      - читает существующий open_in_system.password (system_id=9) и возвращает
      - пишет QRIssueLog по каждой связке store/role
      - шлёт админ-лог в Telegram (TELEGRAM_ADMIN_CHAT_IDS)

    НЕ делает:
      - НЕ обновляет QRCode / OpenInSystem / UKMUser
      - НЕ пишет в MySQL import4.users/signal
      - НЕ обновляет XML UKM5
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Только POST"}, status=405)

    raw_body = ""
    try:
        raw_body = request.body.decode("utf-8") if request.body else "{}"
        try:
            data = json.loads(raw_body)
        except Exception as e:
            logger.error(f"[QR/TG/RO] JSON parse error: {e}; body={raw_body!r}")
            send_telegram_log(
                "❌ Ошибка (READ-ONLY) при выдаче QR по TG\n"
                f"Этап: Парсинг JSON\nПричина: {e}\n\n"
                f"Сырой запрос:\n{raw_body[:1000]}{'…' if len(raw_body) > 1000 else ''}"
            )
            log_qr_issue(
                endpoint="get_qr_code_by_tg",
                method="BY_TG",
                status="error",
                user=None,
                tg_id="",
                employee_inn="",
                employee_fio="",
                phone_raw="",
                phone_normalized="",
                qr_data="",
                error_message=f"Парсинг JSON: {e}",
                raw_request={"raw_body": raw_body} if raw_body else None,
            )
            return JsonResponse({"status": "error", "message": "Некорректный JSON"}, status=400)

        tg_id_val = data.get("tg_id", "")
        tg_id = str(tg_id_val).strip()

        if not tg_id:
            msg = "Не указан tg_id"
            send_telegram_log(f"❌ Ошибка (READ-ONLY) при выдаче QR по TG\nЭтап: Валидация\nПричина: {msg}")
            log_qr_issue(
                endpoint="get_qr_code_by_tg",
                method="BY_TG",
                status="error",
                user=None,
                tg_id="",
                employee_inn="",
                employee_fio="",
                phone_raw="",
                phone_normalized="",
                qr_data="",
                error_message=f"Валидация: {msg}",
                raw_request={"raw_body": raw_body} if raw_body else None,
            )
            return JsonResponse({"status": "error", "message": msg}, status=400)

        user = User.objects.filter(tg_id=tg_id).first()
        if not user:
            msg = f"Пользователь с tg_id={tg_id!r} не найден"
            send_telegram_log(f"❌ Ошибка (READ-ONLY) при выдаче QR по TG\nЭтап: Поиск пользователя\nПричина: {msg}")
            log_qr_issue(
                endpoint="get_qr_code_by_tg",
                method="BY_TG",
                status="error",
                user=None,
                tg_id=tg_id,
                employee_inn="",
                employee_fio="",
                phone_raw="",
                phone_normalized="",
                qr_data="",
                error_message=f"Поиск пользователя: {msg}",
                raw_request={"raw_body": raw_body} if raw_body else None,
            )
            return JsonResponse({"status": "error", "message": "Пользователь не найден"}, status=404)

        fio = " ".join((user.full_name or "").split()).strip()
        employee_id_raw = (user.employee_id or "").strip()

        try:
            plain_inn = ensure_plain_inn(employee_id_raw)
        except Exception as e:
            msg = f"Некорректный employee_id (ИНН) у user_id={user.id}: {e}"
            send_telegram_log(
                "❌ Ошибка (READ-ONLY) при выдаче QR по TG\n"
                f"Этап: Валидация ИНН\nПричина: {msg}\n\n"
                f"tg_id={tg_id}\nuser_id={user.id}\nФИО={fio or '—'}\nemployee_id={employee_id_raw!r}"
            )
            log_qr_issue(
                endpoint="get_qr_code_by_tg",
                method="BY_TG",
                status="error",
                user=user,
                tg_id=tg_id,
                employee_inn=employee_id_raw,
                employee_fio=fio,
                phone_raw=user.phone or "",
                phone_normalized=normalize_phone_ru(user.phone or "") or "",
                qr_data="",
                error_message=f"Валидация ИНН: {msg}",
                raw_request={"raw_body": raw_body} if raw_body else None,
            )
            return JsonResponse({"status": "error", "message": f"Некорректный employee_id: {e}"}, status=400)

        ukm_links = list(UKMUser.objects.filter(user_id=user.id).values("storeid", "roleid"))
        if not ukm_links:
            msg = f"Нет записей ukm_users для user_id={user.id}"
            send_telegram_log(
                "❌ Ошибка (READ-ONLY) при выдаче QR по TG\n"
                f"Этап: Проверка доступов\nПричина: {msg}\n\n"
                f"user_id={user.id}\nФИО={fio or '—'}\nИНН={plain_inn}\ntg_id={tg_id}"
            )
            log_qr_issue(
                endpoint="get_qr_code_by_tg",
                method="BY_TG",
                status="error",
                user=user,
                tg_id=tg_id,
                employee_inn=plain_inn,
                employee_fio=fio,
                phone_raw=user.phone or "",
                phone_normalized=normalize_phone_ru(user.phone or "") or "",
                qr_data="",
                error_message=f"Проверка доступов: {msg}",
                raw_request={"raw_body": raw_body} if raw_body else None,
            )
            return JsonResponse({"status": "error", "message": "Для пользователя нет записей в ukm_users"}, status=404)

        # open_in_system.password (READ-ONLY)
        password, open_username, open_row_id = _get_existing_open_password(user_id=user.id, system_id=9)
        if not password:
            msg = f"Нет password в open_in_system (system_id=9) для user_id={user.id}"
            send_telegram_log(
                "❌ Ошибка (READ-ONLY) при выдаче QR по TG\n"
                f"Этап: Чтение open_in_system\nПричина: {msg}\n\n"
                f"user_id={user.id}\nФИО={fio or '—'}\nИНН={plain_inn}\ntg_id={tg_id}"
            )
            log_qr_issue(
                endpoint="get_qr_code_by_tg",
                method="BY_TG",
                status="error",
                user=user,
                tg_id=tg_id,
                employee_inn=plain_inn,
                employee_fio=fio,
                phone_raw=user.phone or "",
                phone_normalized=normalize_phone_ru(user.phone or "") or "",
                qr_data="",
                error_message=f"open_in_system: {msg}",
                raw_request={"raw_body": raw_body} if raw_body else None,
            )
            return JsonResponse({"status": "error", "message": "У пользователя нет сохранённого QR (open_in_system)"}, status=404)

        # Для логов/ответа: mapping ukm4store -> Store
        ukm_store_ids_str = [str(int(x["storeid"])) for x in ukm_links if str(x.get("storeid", "")).isdigit()]
        store_map = {
            str(s.ukm4store).strip(): s
            for s in Store.objects.filter(ukm4store__in=ukm_store_ids_str)
        }

        per_store_results = []
        for link in ukm_links:
            sid = int(link["storeid"])
            role_id_db = int(link["roleid"])
            s_obj = store_map.get(str(sid))
            per_store_results.append({
                "ukm_storeid": sid,
                "smstore": getattr(s_obj, "smstore", None),
                "store_name": getattr(s_obj, "name", "") if s_obj else "",
                "roleid": role_id_db,
                "cashier_id": None,
                "ukm_host": None,
                "found_in_trm": None,
            })

        # Telegram-лог
        lines = [
            "✅ QR-код запросили из Telegram-бота",
            "",
            "👤 Сотрудник:",
            f"  • Telegram ID: {tg_id}",
            f"  • user_id (PostgreSQL): {user.id}",
            f"  • ФИО: {fio or '—'}",
            f"  • ИНН: {plain_inn}",
            "",
            "🏬 Магазины (ukm_users):",
        ]
        for r in per_store_results:
            lines.append(f"  • ukm_storeid={r['ukm_storeid']}, smstore={r['smstore']}, roleId={r['roleid']}")
        lines += [
            "",
            "🔐 QR (из open_in_system.password):",
            password,
            f"open_in_system.id={open_row_id}, username={open_username or '—'}",
        ]
        send_telegram_log("\n".join(lines))

        # QRIssueLog по каждой связке store/role
        phone_norm = normalize_phone_ru(user.phone or "") or ""
        try:
            raw_req = json.loads(raw_body) if raw_body else None
        except Exception:
            raw_req = {"raw_body": raw_body}

        for r in per_store_results:
            log_qr_issue(
                endpoint="get_qr_code_by_tg",
                method="BY_TG",
                status="ok",
                user=user,
                employee_inn=plain_inn,
                employee_fio=fio,
                tg_id=tg_id,
                phone_raw=user.phone or "",
                phone_normalized=phone_norm,
                sm_store_id=r["smstore"],
                ukm_store_id=r["ukm_storeid"],
                role_id=r["roleid"],
                qr_data=password,
                error_message="",
                raw_request=raw_req,
            )

        return JsonResponse({
            "status": "ok",
            "qr_data": password,
            "user_id": user.id,
            "stores": per_store_results,
        })

    except Exception as e:
        logger.exception("[QR/TG/RO] Unexpected error")
        try:
            send_telegram_log(
                "💥 Критическая ошибка (READ-ONLY) при выдаче QR по TG\n"
                f"{e}\n\nСырой запрос:\n{raw_body[:1000]}{'…' if len(raw_body) > 1000 else ''}"
            )
        except Exception:
            pass
        return JsonResponse({"status": "error", "message": str(e)}, status=500)

@csrf_exempt
def get_qr_code_by_employee_id(request):
    """
    READ-ONLY.

    POST:
    {
      "inn": "7536207278",
      "fio": "Иванов Иван Иванович",
      "storeId": 514,     # SMSTORE (Supermag)
      "roleId": 1,
      "phone": "8 (924) 000-00-00"
    }

    Делает:
      - валидирует INN/FIO/storeId/roleId
      - маппит smstore -> ukm4store через таблицу Store
      - ищет пользователя в users по ИНН (как раньше: plain + sha + sha20)
      - проверяет, что у пользователя есть записи ukm_users
      - (опционально) проверяет, что запрошенный ukm4store есть среди ukm_users
      - читает существующий open_in_system.password (system_id=9) и возвращает
      - пишет QRIssueLog (по каждому магазину пользователя)
      - шлёт админ-лог в Telegram (TELEGRAM_ADMIN_CHAT_IDS)

    НЕ делает:
      - НЕ обновляет QRCode / OpenInSystem / UKMUser
      - НЕ пишет в MySQL import4.users/signal
      - НЕ обновляет XML UKM5
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Только POST"}, status=405)

    raw_body = ""
    try:
        raw_body = request.body.decode("utf-8") if request.body else "{}"
        try:
            data = json.loads(raw_body)
        except Exception as e:
            logger.error(f"[QR/EMP/RO] JSON parse error: {e}; body={raw_body!r}")
            send_telegram_log(
                "❌ Ошибка (READ-ONLY) при выдаче QR по ИНН\n"
                f"Этап: Парсинг JSON\nПричина: {e}\n\n"
                f"Сырой запрос:\n{raw_body[:1000]}{'…' if len(raw_body) > 1000 else ''}"
            )
            log_qr_issue(
                endpoint="get_qr_code_by_employee_id",
                method="BY_INN",
                status="error",
                user=None,
                employee_inn="",
                employee_fio="",
                tg_id="",
                phone_raw="",
                phone_normalized="",
                sm_store_id=None,
                ukm_store_id=None,
                role_id=None,
                qr_data="",
                error_message=f"Парсинг JSON: {e}",
                raw_request={"raw_body": raw_body} if raw_body else None,
            )
            return JsonResponse({"status": "error", "message": "Некорректный JSON"}, status=400)

        inn_raw = str(data.get("inn") or data.get("employee_id") or "").strip()
        fio_raw = str(data.get("fio") or "").strip()
        store_raw = str(data.get("storeId") or data.get("storeid") or "").strip()
        role_raw = str(data.get("roleId") or data.get("roleid") or "").strip()
        phone_raw = str(data.get("phone") or "").strip()

        # INN
        if not inn_raw:
            msg = "Не указан inn"
            send_telegram_log(f"❌ Ошибка (READ-ONLY) при выдаче QR по ИНН\nЭтап: Валидация\nПричина: {msg}")
            log_qr_issue(
                endpoint="get_qr_code_by_employee_id",
                method="BY_INN",
                status="error",
                user=None,
                employee_inn="",
                employee_fio=fio_raw,
                tg_id="",
                phone_raw=phone_raw,
                phone_normalized=normalize_phone_ru(phone_raw) or "",
                sm_store_id=int(store_raw) if store_raw.isdigit() else None,
                ukm_store_id=None,
                role_id=int(role_raw) if role_raw.isdigit() else None,
                qr_data="",
                error_message=f"Валидация: {msg}",
                raw_request={"raw_body": raw_body} if raw_body else None,
            )
            return JsonResponse({"status": "error", "message": msg}, status=400)

        try:
            plain_inn = ensure_plain_inn(inn_raw)
        except Exception as e:
            msg = f"Некорректный ИНН: {e}"
            send_telegram_log(
                "❌ Ошибка (READ-ONLY) при выдаче QR по ИНН\n"
                f"Этап: Валидация ИНН\nПричина: {msg}\ninn={inn_raw!r}"
            )
            log_qr_issue(
                endpoint="get_qr_code_by_employee_id",
                method="BY_INN",
                status="error",
                user=None,
                employee_inn=inn_raw,
                employee_fio=fio_raw,
                tg_id="",
                phone_raw=phone_raw,
                phone_normalized=normalize_phone_ru(phone_raw) or "",
                sm_store_id=int(store_raw) if store_raw.isdigit() else None,
                ukm_store_id=None,
                role_id=int(role_raw) if role_raw.isdigit() else None,
                qr_data="",
                error_message=f"Валидация ИНН: {msg}",
                raw_request={"raw_body": raw_body} if raw_body else None,
            )
            return JsonResponse({"status": "error", "message": msg}, status=400)

        # FIO
        if not fio_raw:
            msg = "Не указано fio"
            send_telegram_log(
                "❌ Ошибка (READ-ONLY) при выдаче QR по ИНН\n"
                f"Этап: Валидация\nПричина: {msg}\ninn={plain_inn}"
            )
            log_qr_issue(
                endpoint="get_qr_code_by_employee_id",
                method="BY_INN",
                status="error",
                user=None,
                employee_inn=plain_inn,
                employee_fio="",
                tg_id="",
                phone_raw=phone_raw,
                phone_normalized=normalize_phone_ru(phone_raw) or "",
                sm_store_id=int(store_raw) if store_raw.isdigit() else None,
                ukm_store_id=None,
                role_id=int(role_raw) if role_raw.isdigit() else None,
                qr_data="",
                error_message=f"Валидация: {msg}",
                raw_request={"raw_body": raw_body} if raw_body else None,
            )
            return JsonResponse({"status": "error", "message": msg}, status=400)
        fio = " ".join(fio_raw.split()).strip()

        # storeId(smstore)
        if not store_raw or not store_raw.isdigit():
            msg = "Некорректный storeId (smstore)"
            send_telegram_log(
                "❌ Ошибка (READ-ONLY) при выдаче QR по ИНН\n"
                f"Этап: Валидация\nПричина: {msg}\nstoreId={store_raw!r}"
            )
            log_qr_issue(
                endpoint="get_qr_code_by_employee_id",
                method="BY_INN",
                status="error",
                user=None,
                employee_inn=plain_inn,
                employee_fio=fio,
                tg_id="",
                phone_raw=phone_raw,
                phone_normalized=normalize_phone_ru(phone_raw) or "",
                sm_store_id=None,
                ukm_store_id=None,
                role_id=int(role_raw) if role_raw.isdigit() else None,
                qr_data="",
                error_message=f"Валидация: {msg}",
                raw_request={"raw_body": raw_body} if raw_body else None,
            )
            return JsonResponse({"status": "error", "message": msg}, status=400)
        sm_store_id = int(store_raw)

        # roleId
        if not role_raw or not role_raw.isdigit():
            msg = "Некорректный roleId"
            send_telegram_log(
                "❌ Ошибка (READ-ONLY) при выдаче QR по ИНН\n"
                f"Этап: Валидация\nПричина: {msg}\nroleId={role_raw!r}"
            )
            log_qr_issue(
                endpoint="get_qr_code_by_employee_id",
                method="BY_INN",
                status="error",
                user=None,
                employee_inn=plain_inn,
                employee_fio=fio,
                tg_id="",
                phone_raw=phone_raw,
                phone_normalized=normalize_phone_ru(phone_raw) or "",
                sm_store_id=sm_store_id,
                ukm_store_id=None,
                role_id=None,
                qr_data="",
                error_message=f"Валидация: {msg}",
                raw_request={"raw_body": raw_body} if raw_body else None,
            )
            return JsonResponse({"status": "error", "message": msg}, status=400)
        role_id_req = int(role_raw)

        phone_norm = normalize_phone_ru(phone_raw) or ""

        # smstore -> ukm4store
        store_obj = Store.objects.filter(smstore=sm_store_id).first()
        if not store_obj or store_obj.ukm4store is None:
            msg = "Магазин не найден в stores или не указан ukm4store"
            send_telegram_log(
                "❌ Ошибка (READ-ONLY) при выдаче QR по ИНН\n"
                f"Этап: Маппинг smstore→ukm4store\nПричина: {msg}\nsmstore={sm_store_id}"
            )
            log_qr_issue(
                endpoint="get_qr_code_by_employee_id",
                method="BY_INN",
                status="error",
                user=None,
                employee_inn=plain_inn,
                employee_fio=fio,
                tg_id="",
                phone_raw=phone_raw,
                phone_normalized=phone_norm,
                sm_store_id=sm_store_id,
                ukm_store_id=None,
                role_id=role_id_req,
                qr_data="",
                error_message=f"Маппинг smstore→ukm4store: {msg}",
                raw_request={"raw_body": raw_body} if raw_body else None,
            )
            return JsonResponse({"status": "error", "message": msg}, status=400)

        try:
            ukm_store_id_req = int(str(store_obj.ukm4store).strip())
        except Exception:
            msg = "Некорректное значение ukm4store"
            send_telegram_log(
                "❌ Ошибка (READ-ONLY) при выдаче QR по ИНН\n"
                f"Этап: Маппинг smstore→ukm4store\nПричина: {msg}\nsmstore={sm_store_id}"
            )
            return JsonResponse({"status": "error", "message": msg}, status=400)

        # Поиск пользователя (как было — несколько стратегий)
        inn_sha = hashlib.sha256(plain_inn.encode("utf-8")).hexdigest()
        inn_sha20 = inn_sha[:20]
        user = (
            User.objects.filter(employee_id=plain_inn).first()
            or User.objects.filter(encrypted_inn=plain_inn).first()
            or User.objects.filter(employee_id=inn_sha).first()
            or User.objects.filter(encrypted_inn=inn_sha).first()
            or User.objects.filter(employee_id=inn_sha20).first()
            or User.objects.filter(encrypted_inn=inn_sha20).first()
        )
        if not user:
            msg = f"Пользователь с INN={plain_inn} не найден в PostgreSQL"
            send_telegram_log(
                "❌ Ошибка (READ-ONLY) при выдаче QR по ИНН\n"
                f"Этап: Поиск пользователя\nПричина: {msg}\n"
                f"inn={plain_inn}\nfio={fio}\nsmstore={sm_store_id} (ukm4store={ukm_store_id_req})"
            )
            log_qr_issue(
                endpoint="get_qr_code_by_employee_id",
                method="BY_INN",
                status="error",
                user=None,
                employee_inn=plain_inn,
                employee_fio=fio,
                tg_id="",
                phone_raw=phone_raw,
                phone_normalized=phone_norm,
                sm_store_id=sm_store_id,
                ukm_store_id=ukm_store_id_req,
                role_id=role_id_req,
                qr_data="",
                error_message=f"Поиск пользователя: {msg}",
                raw_request={"raw_body": raw_body} if raw_body else None,
            )
            return JsonResponse({"status": "error", "message": "Пользователь не найден"}, status=404)

        # ukm_users (READ-ONLY): просто читаем
        ukm_links = list(UKMUser.objects.filter(user_id=user.id).values("storeid", "roleid"))
        if not ukm_links:
            msg = f"Нет записей ukm_users для user_id={user.id}"
            send_telegram_log(
                "❌ Ошибка (READ-ONLY) при выдаче QR по ИНН\n"
                f"Этап: Проверка доступов\nПричина: {msg}\n"
                f"user_id={user.id}\nФИО={fio}\nИНН={plain_inn}\nsmstore={sm_store_id} (ukm4store={ukm_store_id_req})"
            )
            log_qr_issue(
                endpoint="get_qr_code_by_employee_id",
                method="BY_INN",
                status="error",
                user=user,
                employee_inn=plain_inn,
                employee_fio=fio,
                tg_id=str(getattr(user, "tg_id", "") or ""),
                phone_raw=phone_raw,
                phone_normalized=phone_norm,
                sm_store_id=sm_store_id,
                ukm_store_id=ukm_store_id_req,
                role_id=role_id_req,
                qr_data="",
                error_message=f"Проверка доступов: {msg}",
                raw_request={"raw_body": raw_body} if raw_body else None,
            )
            return JsonResponse({"status": "error", "message": "Для пользователя нет записей в ukm_users"}, status=404)

        # Проверим, что запрошенный магазин есть в ukm_users (ничего не меняем)
        has_requested_store = any(int(x["storeid"]) == int(ukm_store_id_req) for x in ukm_links if str(x.get("storeid", "")).isdigit())
        if not has_requested_store:
            msg = f"У пользователя нет доступа к ukm4store={ukm_store_id_req} (запрошен smstore={sm_store_id})"
            send_telegram_log(
                "❌ Ошибка (READ-ONLY) при выдаче QR по ИНН\n"
                f"Этап: Проверка доступов\nПричина: {msg}\n"
                f"user_id={user.id}\nФИО={fio}\nИНН={plain_inn}"
            )
            log_qr_issue(
                endpoint="get_qr_code_by_employee_id",
                method="BY_INN",
                status="error",
                user=user,
                employee_inn=plain_inn,
                employee_fio=fio,
                tg_id=str(getattr(user, "tg_id", "") or ""),
                phone_raw=phone_raw,
                phone_normalized=phone_norm,
                sm_store_id=sm_store_id,
                ukm_store_id=ukm_store_id_req,
                role_id=role_id_req,
                qr_data="",
                error_message=f"Проверка доступов: {msg}",
                raw_request={"raw_body": raw_body} if raw_body else None,
            )
            return JsonResponse({"status": "error", "message": msg}, status=403)

        # open_in_system.password (READ-ONLY)
        password, open_username, open_row_id = _get_existing_open_password(user_id=user.id, system_id=9)
        if not password:
            msg = f"Нет password в open_in_system (system_id=9) для user_id={user.id}"
            send_telegram_log(
                "❌ Ошибка (READ-ONLY) при выдаче QR по ИНН\n"
                f"Этап: Чтение open_in_system\nПричина: {msg}\n\n"
                f"user_id={user.id}\nФИО={fio}\nИНН={plain_inn}"
            )
            log_qr_issue(
                endpoint="get_qr_code_by_employee_id",
                method="BY_INN",
                status="error",
                user=user,
                employee_inn=plain_inn,
                employee_fio=fio,
                tg_id=str(getattr(user, "tg_id", "") or ""),
                phone_raw=phone_raw,
                phone_normalized=phone_norm,
                sm_store_id=sm_store_id,
                ukm_store_id=ukm_store_id_req,
                role_id=role_id_req,
                qr_data="",
                error_message=f"open_in_system: {msg}",
                raw_request={"raw_body": raw_body} if raw_body else None,
            )
            return JsonResponse({"status": "error", "message": "У пользователя нет сохранённого QR (open_in_system)"}, status=404)

        # Store map для вывода/логов
        ukm_store_ids_str = [str(int(x["storeid"])) for x in ukm_links if str(x.get("storeid", "")).isdigit()]
        store_map = {
            str(s.ukm4store).strip(): s
            for s in Store.objects.filter(ukm4store__in=ukm_store_ids_str)
        }

        per_store_results = []
        role_mismatch_notes = []
        for l in ukm_links:
            sid = int(l["storeid"])
            role_id_db = int(l["roleid"])
            s_obj = store_map.get(str(sid))

            if sid == int(ukm_store_id_req) and role_id_db != int(role_id_req):
                role_mismatch_notes.append(f"requested_role={role_id_req} != ukm_users.roleid={role_id_db} (ukm_storeid={sid})")

            per_store_results.append({
                "ukm_storeid": sid,
                "smstore": getattr(s_obj, "smstore", None),
                "store_name": getattr(s_obj, "name", "") if s_obj else "",
                "roleid": role_id_db,
                "cashier_id": None,
                "ukm_host": None,
                "found_in_trm": None,
            })

        # Telegram-лог
        fio_db = " ".join(((user.full_name or "").split())) if (user.full_name or "").strip() else ""
        fio_match = (fio_db == fio) if fio_db else None

        lines = [
            "✅ QR-код запрошен из 1С",
            "",
            "👤 Сотрудник:",
            f"  • user_id (PostgreSQL): {user.id}",
            f"  • ФИО (запрос): {fio}",
            f"  • ФИО (users.full_name): {fio_db or '—'}",
            f"  • fio_match: {fio_match if fio_match is not None else '—'}",
            f"  • ИНН: {plain_inn}",
            f"  • phone_raw: {phone_raw or '—'}",
            f"  • phone_norm: {phone_norm or '—'}",
            "",
            f"🏬 Запрошенный магазин: smstore={sm_store_id} → ukm4store={ukm_store_id_req}, roleId={role_id_req}",
        ]
        if role_mismatch_notes:
            lines += ["", "⚠️ Несовпадение roleId:", *[f"  • {x}" for x in role_mismatch_notes]]

        lines += [
            "",
            "📌 Магазины пользователя (ukm_users):",
        ]
        for r in per_store_results:
            lines.append(f"  • ukm_storeid={r['ukm_storeid']}, smstore={r['smstore']}, roleId={r['roleid']}")

        lines += [
            "",
            "🔐 QR (из open_in_system.password):",
            password,
            f"open_in_system.id={open_row_id}, username={open_username or '—'}",
        ]
        send_telegram_log("\n".join(lines))

        # Логи в таблицу (по каждому магазину)
        try:
            raw_req = json.loads(raw_body) if raw_body else None
        except Exception:
            raw_req = {"raw_body": raw_body}

        for r in per_store_results:
            log_qr_issue(
                endpoint="get_qr_code_by_employee_id",
                method="BY_INN",
                status="ok",
                user=user,
                employee_inn=plain_inn,
                employee_fio=fio,
                tg_id=str(getattr(user, "tg_id", "") or ""),
                phone_raw=phone_raw,
                phone_normalized=phone_norm,
                sm_store_id=r["smstore"],
                ukm_store_id=r["ukm_storeid"],
                role_id=r["roleid"],
                qr_data=password,
                error_message="",
                raw_request=raw_req,
            )

        return JsonResponse({
            "status": "ok",
            "qr_data": password,
            "user_id": user.id,
            "requested": {
                "smstore": sm_store_id,
                "ukm4store": ukm_store_id_req,
                "roleid": role_id_req,
            },
            "stores": per_store_results,
        })

    except Exception as e:
        logger.exception("[QR/EMP/RO] Unexpected error")
        try:
            send_telegram_log(
                "💥 Критическая ошибка (READ-ONLY) при выдаче QR по ИНН\n"
                f"{e}\n\nСырой запрос:\n{raw_body[:1000]}{'…' if len(raw_body) > 1000 else ''}"
            )
        except Exception:
            pass
        return JsonResponse({"status": "error", "message": str(e)}, status=500)





@csrf_exempt
def update_cashier(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Только POST"}, status=405)

    raw_body = ""
    try:
        raw_body = request.body.decode("utf-8") if request.body else "{}"
        data = json.loads(raw_body)

        # ---- входные поля (поддержим несколько вариантов названий)
        inn_raw = str(data.get("inn") or data.get("employee_id") or "").strip()
        fio_raw = str(data.get("fio") or data.get("full_name") or "").strip()
        storeids_raw = data.get("storeid") or data.get("storeids") or data.get("stores")

        role_raw = data.get("roleId") or data.get("roleid") or 1
        try:
            role_id_req = int(role_raw)
        except Exception:
            role_id_req = 1

        plain_inn = ensure_plain_inn(inn_raw)
        fio = " ".join(fio_raw.split()).strip()

        if not fio:
            return JsonResponse({"status": "error", "message": "fio обязателен"}, status=400)
        if not storeids_raw:
            return JsonResponse({"status": "error", "message": "storeid обязателен"}, status=400)

        store_ids = []
        if isinstance(storeids_raw, list):
            for x in storeids_raw:
                s = str(x).strip()
                if s.isdigit():
                    store_ids.append(int(s))
        else:
            store_ids = [int(s.strip()) for s in str(storeids_raw).split(",") if s.strip().isdigit()]

        store_ids = sorted(set(store_ids))
        if not store_ids:
            return JsonResponse({"status": "error", "message": "Некорректный storeid"}, status=400)

        # ---- поиск пользователя (plain/sha/sha20) + нормализованное ФИО
        inn_sha = hashlib.sha256(plain_inn.encode("utf-8")).hexdigest()
        inn_sha20 = inn_sha[:20]

        fio_norm = fio  # уже нормализован
        user = (
            User.objects.filter(full_name__iexact=fio_norm).filter(
                Q(employee_id=plain_inn) | Q(encrypted_inn=plain_inn) |
                Q(employee_id=inn_sha)  | Q(encrypted_inn=inn_sha)  |
                Q(employee_id=inn_sha20)| Q(encrypted_inn=inn_sha20)
            ).order_by("id").first()
        )

        # fallback: если ФИО в базе чуть отличается — найдём по ИНН, но проверим руками
        if not user:
            cand = (
                User.objects.filter(
                    Q(employee_id=plain_inn) | Q(encrypted_inn=plain_inn) |
                    Q(employee_id=inn_sha)  | Q(encrypted_inn=inn_sha)  |
                    Q(employee_id=inn_sha20)| Q(encrypted_inn=inn_sha20)
                ).order_by("id").first()
            )
            if cand:
                fio_db = " ".join((cand.full_name or "").split()).strip()
                if fio_db.lower() == fio_norm.lower():
                    user = cand

        if not user:
            return JsonResponse({"status": "error", "message": "Пользователь не найден"}, status=404)

        # ---- добавим недостающие магазины в ukm_users (в Postgres) + сразу ротируем пароль
        added_storeids: list[int] = []

        with transaction.atomic():
            # подстрахуемся: employee_id должен быть plain ИНН (иначе ensure_plain_inn на user.employee_id потом ломается в других местах)
            if (user.employee_id or "").strip() != plain_inn:
                user.employee_id = plain_inn
                user.updated_at = timezone.now()
                user.save(update_fields=["employee_id", "updated_at"])

            existing_links = {
                int(x.storeid): x
                for x in UKMUser.objects
                    .filter(user_id=user.id, storeid__in=store_ids)
            }

            for sid in store_ids:
                sid = int(sid)
                link = existing_links.get(sid)

                if link is None:
                    UKMUser.objects.create(
                        user=user,
                        roleid=role_id_req,
                        storeid=sid,
                        version=1
                    )
                    added_storeids.append(sid)
                else:
                    # ✅ если магазин уже есть — обновляем роль
                    if int(link.roleid or 0) != int(role_id_req):
                        link.roleid = int(role_id_req)
                        link.version = 1
                        link.save(update_fields=["roleid", "version"])

            # ВАЖНО: как в rotate_qr_codes.py — всегда генерим новый пароль/QR и пишем в Postgres
            new_password = build_user_password(plain_inn)
            _set_password_pg(user, new_password)

        # После транзакции перечитываем ВСЕ магазины пользователя (чтобы новый пароль применить везде)
        ukm_links = list(
            UKMUser.objects
            .filter(user_id=user.id)
            .values("storeid", "roleid")
        )
        ukm_links = sorted(
            [{"storeid": int(x["storeid"]), "roleid": int(x["roleid"])} for x in ukm_links],
            key=lambda x: x["storeid"]
        )

        if not ukm_links:
            return JsonResponse({"status": "error", "message": "Нет записей ukm_users у пользователя"}, status=400)

        # ---- allocator новых trm-id: ключим по resolved_host, чтобы не словить дубль,
        # если разные storeid попадают на один и тот же ukmserver
        trm_alloc: dict[str, dict] = {}  # {resolved_host: {"next": int, "reserved": set[int]}}

        def _is_trm_id_taken(resolved_host: str, candidate: int) -> bool:
            conn2 = cur2 = None
            try:
                conn2 = connect_ukm(host=resolved_host, store_id=None)
                cur2 = conn2.cursor()
                cur2.execute("SELECT 1 AS x FROM trm_in_users WHERE id=%s LIMIT 1", (candidate,))
                return bool(cur2.fetchone())
            finally:
                try:
                    if cur2: cur2.close()
                    if conn2: conn2.close()
                except Exception:
                    pass

        def _alloc_new_trm_id_for_store(store_id: int) -> tuple[int, str]:
            resolved_host, _src = _resolve_ukmserver_host(host=None, store_id=store_id)
            key = str(resolved_host).strip()

            st = trm_alloc.get(key)
            if st is None:
                base = get_next_trm_employee_id(store_id=store_id, host=key)
                st = {"next": int(base), "reserved": set()}
                trm_alloc[key] = st

            candidate = int(st["next"])
            while True:
                if candidate in st["reserved"]:
                    candidate += 1
                    continue
                if candidate > int(TRM_SMALL_MAX):
                    raise RuntimeError(
                        f"TRM id overflow: candidate={candidate} > TRM_SMALL_MAX={TRM_SMALL_MAX} (host={key})"
                    )
                if _is_trm_id_taken(key, candidate):
                    candidate += 1
                    continue

                st["reserved"].add(candidate)
                st["next"] = candidate + 1
                return int(candidate), key

        # ---- обновление UKM4/UKM5 по всем магазинам пользователя
        per_store = []
        errors = 0

        for link in ukm_links:
            sid = int(link["storeid"])
            role_id = int(link["roleid"])

            cashier_id = None
            found_in_trm = False
            resolved_host = None

            try:
                # Ищем на НУЖНОМ ukmserver (через store_id)
                existing_id = get_trm_employee_id(
                    plain_inn,
                    fio_norm,
                    store_id=sid,
                    host=None
                )
                if existing_id is not None:
                    cashier_id = int(existing_id)
                    found_in_trm = True
                    resolved_host, _ = _resolve_ukmserver_host(host=None, store_id=sid)
                else:
                    cashier_id, resolved_host = _alloc_new_trm_id_for_store(sid)
                    found_in_trm = False

                _update_store_mysql_and_xml_for_single_store(
                    store_id=sid,
                    cashier_id=int(cashier_id),
                    role_id=int(role_id),
                    plain_inn=plain_inn,
                    fio=fio_norm,
                    password_plain=new_password,
                )

                _write_converter_user_and_signal(
                    cashier_id=int(cashier_id),
                    plain_inn=plain_inn,
                    fio=fio_norm,
                    password_plain=new_password,
                    store_id=sid,
                    role_id=int(role_id),
                )

                per_store.append({
                    "storeid": sid,
                    "roleid": role_id,
                    "cashier_id": int(cashier_id),
                    "found_in_trm": found_in_trm,
                    "ukm_host": str(resolved_host or ""),
                    "status": "ok",
                    "error": "",
                })

            except Exception as e:
                errors += 1
                logger.error(f"[UPDATE_CASHIER] storeid={sid} error: {e}", exc_info=True)
                per_store.append({
                    "storeid": sid,
                    "roleid": role_id,
                    "cashier_id": int(cashier_id) if cashier_id is not None else None,
                    "found_in_trm": found_in_trm,
                    "ukm_host": str(resolved_host or ""),
                    "status": "error",
                    "error": str(e),
                })


        try:
            masked = new_password[:6] + "..." + new_password[-4:]
            lines = [
                "🔄 update_cashier: ротация QR/пароля + синхронизация магазинов",
                "",
                f"👤 user_id={user.id}",
                f"ФИО='{fio_norm}'",
                f"ИНН={plain_inn}",
                f"Роль из запроса (role_id_req)={role_id_req}",
                f"Новый пароль (masked)={masked} (len={len(new_password)})",
                "",
                f"➕ Добавлены магазины: {', '.join(map(str, added_storeids)) if added_storeids else '—'}",
                f"🏬 Всего магазинов у пользователя (ukm_users): {len(ukm_links)}",
                f"⚠️ Ошибок по магазинам: {errors}",
                "",
                "📋 Детализация:",
            ]
            for r in per_store:
                lines.append(
                    f"• storeid={r['storeid']} role={r['roleid']} "
                    f"cashier_id={r['cashier_id'] or '—'} found_in_trm={r['found_in_trm']} "
                    f"host={r['ukm_host'] or '—'} status={r['status']}"
                )
                if r.get("error"):
                    lines.append(f"    error: {r['error']}")
            send_telegram_log("\n".join(lines))
        except Exception as e:
            logger.error(f"[UPDATE_CASHIER] telegram log failed: {e}", exc_info=True)

        resp = {
            "status": "ok" if errors == 0 else "partial",
            "message": "Доступ обновлён и пароль ротирован" if errors == 0 else "Частично выполнено: есть ошибки по магазинам",
            "user_id": user.id,
            "inn": plain_inn,
            "fio": fio_norm,
            "added_storeids": added_storeids,
            "rotated": True,
            "password": new_password,  
            "stores": per_store,
        }
        return JsonResponse(resp, status=200 if errors == 0 else 207)

    except Exception as exc:
        logger.exception("[UPDATE_CASHIER] error")
        try:
            send_telegram_log(
                "💥 Ошибка update_cashier\n"
                f"{exc}\n\n"
                f"raw_body:\n{raw_body[:1500]}{'…' if len(raw_body) > 1500 else ''}"
            )
        except Exception:
            pass
        return JsonResponse({"status": "error", "message": str(exc)}, status=500)
# @csrf_exempt
# def update_cashier(request):
#     if request.method != 'POST':
#         return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)

#     try:
#         data = json.loads(request.body)

#         plain_inn = ensure_plain_inn(data.get('inn'))
#         fio = data.get('fio')
#         storeids = data.get('storeid')

#         if not (plain_inn and fio and storeids):
#             return JsonResponse({'status': 'error', 'message': 'inn, fio и storeid обязательны'}, status=400)

#         store_ids = [int(s.strip()) for s in str(storeids).split(',') if s.strip().isdigit()]
#         if not store_ids:
#             return JsonResponse({'status': 'error', 'message': 'Некорректный storeid'}, status=400)

#         user = User.objects.filter(employee_id=plain_inn, full_name=fio).first()
#         if not user:
#             return JsonResponse({'status': 'error', 'message': 'Пользователь не найден'}, status=404)

#         open_rec = OpenInSystem.objects.filter(user_id=user.id, system_id=9).first()
#         if not open_rec:
#             return JsonResponse({'status': 'error', 'message': 'Пароль для пользователя не найден'}, status=500)
#         password_plain = open_rec.password

#         ukm_emp_id = get_trm_employee_id(plain_inn, fio)

#         existing_storeids = set(UKMUser.objects.filter(user_id=user.id).values_list('storeid', flat=True))

#         ukm_conn = connect_ukm()
#         ukm_cursor = ukm_conn.cursor()
#         ukm_cursor.execute("SELECT MAX(id)+1 AS next_id FROM trm_in_users")
#         cashier_id_base = ukm_cursor.fetchone()['next_id'] or 1
#         ukm_conn.close()

#         cashier_counter = 0
#         added_storeids = []

#         base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
#         xml_dir = os.path.join(base_dir, 'xml')
#         os.makedirs(xml_dir, exist_ok=True)

#         for sid in store_ids:
#             if sid in existing_storeids:
#                 logger.info(f"[Пропуск] Доступ уже есть: storeid={sid}")
#                 continue

#             UKMUser.objects.create(user=user, roleid=1, storeid=sid, version=1)

#             cashier_id = ukm_emp_id if ukm_emp_id else (cashier_id_base + cashier_counter)
#             cashier_counter += 1

#             info = get_store_info(sid)
#             ukm4ip = info.get("ukm4ip")
#             is_ukm5 = info.get("is_ukm5", False)

#             if ukm4ip:
#                 try:
#                     conv = connect_store_mysql(ukm4ip)
#                     cur = conv.cursor()

#                     base_version = _calc_next_signal_version(cur)

#                     cur.execute("""
#                         INSERT INTO users (store, id, name, inn, password, role_id, version, deleted)
#                         VALUES (%s, %s, %s, %s, OLD_PASSWORD(%s), %s, %s, 0)
#                     """, (
#                         sid, cashier_id, fio, plain_inn, mysql_pwd(password_plain), 1, base_version
#                     ))
#                     cur.execute("INSERT INTO `signal`(`signal`, `version`) VALUES ('incr', %s)", (base_version,))
#                     conv.commit()
#                     conv.close()
#                     logger.info(f"[MySQL:{ukm4ip}] Доступ открыт store={sid}, id={cashier_id}, version={base_version}")
#                 except Exception as exc:
#                     logger.error(f"[MySQL:{ukm4ip}] Ошибка для {sid}: {exc}")
#             else:
#                 logger.error(f"[Oracle] Не найден UKM4IP для storeid={sid}. Пропуск записи в MySQL.")

#             if is_ukm5:
#                 try:
#                     if sid == UKM5_FULL_XML_STORE_ID:
#                         # Для магазина 2013 всегда пересборка полного XML
#                         xml_path = build_full_ukm5_xml_for_store(sid)
#                         logger.info(
#                             f"[XML] Полный XML пересобран для УКМ5 магазина {sid}: {xml_path}"
#                         )
#                     else:
#                         # Остальные УКМ5 — точечное добавление кассира
#                         xml_path, tree, root = _get_or_create_storecashiers_tree(sid)

#                         c_el = ET.SubElement(root, "cashier")
#                         ET.SubElement(c_el, "roleId").text = "1"
#                         ET.SubElement(c_el, "id").text = str(cashier_id)
#                         ET.SubElement(c_el, "name").text = fio
#                         ET.SubElement(c_el, "INN").text = plain_inn
#                         ET.SubElement(c_el, "password").text = password_plain

#                         _write_xml_with_declaration(xml_path, root, ensure_base=True)
#                         logger.info(f"[XML] Обновлён файл {xml_path}")
#                 except Exception as exc:
#                     logger.error(f"[XML] Ошибка для {sid}: {exc}")

#             added_storeids.append(sid)

#         if not added_storeids:
#             return JsonResponse({'status': 'ok', 'message': 'У пользователя уже был доступ ко всем указанным магазинам'})

#         return JsonResponse({'status': 'ok', 'message': 'Доступ открыт', 'added': added_storeids})

#     except Exception as exc:
#         logger.exception("open_access_cashier error")
#         return JsonResponse({'status': 'error', 'message': str(exc)}, status=500)



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

                    base_version = _calc_next_signal_version(cur)

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

            # Для магазина 2013 пересборку сделаем отдельным шагом
            if sid == UKM5_FULL_XML_STORE_ID:
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
                    _write_xml_with_declaration(xml_path, root, ensure_base=True)
                    logger.info(f"[XML] Удалён кассир (INN={inn_raw}) из {xml_path}")
            except Exception as exc:
                logger.error(f"[XML] Ошибка для магазина {sid}: {exc}")

        # удаляем ukm_users в PostgreSQL
        UKMUser.objects.filter(id__in=[u.id for u in ukm_to_remove]).delete()
        if any(u.storeid == UKM5_FULL_XML_STORE_ID for u in ukm_to_remove):
            try:
                xml_path = build_full_ukm5_xml_for_store(UKM5_FULL_XML_STORE_ID)
                logger.info(
                    f"[XML] Полный XML пересобран после удаления доступа для магазина "
                    f"{UKM5_FULL_XML_STORE_ID}: {xml_path}"
                )
            except Exception as exc:
                logger.error(
                    f"[XML] Ошибка пересборки XML для магазина "
                    f"{UKM5_FULL_XML_STORE_ID} после удаления: {exc}"
                )

        return JsonResponse({"status": "ok", "message": "Доступ закрыт", "removed": removed_sids})

    except Exception as exc:
        logger.exception("delete_cashier error")
        return JsonResponse({"status": "error", "message": str(exc)}, status=500)


@csrf_exempt
def employee_identification(request):
    """
    POST /employee-identification/

    ВХОД (JSON, совместим со старой версией):
    {
      "inn": "7536207278",              # или "employee_id"
      "fio": "Иванов Иван Иванович",    # или "FIO"
      "storeId": 514,                   # или "smstore" или старый "mx"
      "datetime": "24.09.2025 8:45:00", # или "Datetime" или "event_datetime"
      // необязательные:
      "phone": "8 (924) 000-00-00",
      "direction": "IN"                 # или "OUT" / "ENTER" / "EXIT" / "event"
      // "roleId" / "roleid" — можно не передавать, оно тут не нужно
    }

    Логика:
      1) Валидация/нормализация входных данных (как в старой версии).
      2) Подготовка payload для 1С в старом формате: INN/FIO/MX/Datetime.
      3) На любой ошибке:
         • красивый лог в Telegram
         • запись в qr_issue_logs со статусом 'error'.
      4) На успехе:
         • красивый лог в Telegram
         • запись в qr_issue_logs со статусом 'ok'/'error' в зависимости от ответа 1С.
    """
    if request.method != 'POST':
        return JsonResponse(
            {'status': 'error', 'message': 'Только POST'},
            status=405
        )

    raw_body = request.body.decode('utf-8') if request.body else "{}"
    lat_val: float | None = None
    lon_val: float | None = None

    def _log_and_return_error(
        http_status: int,
        stage: str,
        human_msg: str,
        *,
        inn_raw: str = "",
        fio_raw: str = "",
        smstore_raw: str = "",
        ukm_store_id: int | None = None,
        phone_raw: str = "",
    ) -> JsonResponse:
        """
        Общий обработчик ошибок:
          • шлёт подробный лог в Telegram
          • пишет строку в qr_issue_logs со статусом 'error'
          • возвращает JsonResponse с текстом ошибки
        """
        # красивый лог в ТГ
        lines = [
            "❌ Ошибка при employee_identification",
            f"🔁 Этап: {stage}",
            f"ℹ️ Причина: {human_msg}",
            "",
            "📨 Контекст запроса:",
            f"  • ИНН (сырое): {inn_raw or '—'}",
            f"  • ФИО (сырое): {fio_raw or '—'}",
            f"  • storeId / mx (сырое): {smstore_raw or '—'}",
            f"  • Телефон (сырое): {phone_raw or '—'}",
            f"  • latitude: {lat_val if lat_val is not None else '—'}",
            f"  • longitude: {lon_val if lon_val is not None else '—'}",
        ]
        if ukm_store_id is not None:
            lines.append(f"  • ukm4store: {ukm_store_id}")
        if raw_body:
            short_body = raw_body if len(raw_body) <= 1000 else raw_body[:1000] + "…"
            lines.extend(["", "📦 Сырой JSON-запрос:", short_body])

        send_telegram_log("\n".join(lines))

        # подготовка полей для записи в qr_issue_logs
        try:
            sm_id_int = int(smstore_raw) if str(smstore_raw).isdigit() else None
        except Exception:
            sm_id_int = None

        phone_norm = normalize_phone_ru(phone_raw) if phone_raw else None

        try:
            log_qr_issue(
                endpoint='employee_identification',
                method='EMP_IDENT',
                status='error',
                user=None,  # при ошибке пользователя можем ещё не знать
                employee_inn=inn_raw or "",
                employee_fio=fio_raw or "",
                tg_id="",
                phone_raw=phone_raw or "",
                phone_normalized=phone_norm or "",
                sm_store_id=sm_id_int,
                ukm_store_id=ukm_store_id,
                role_id=None,          # роль для этого эндпоинта не используется
                qr_data="",       
                error_message=f"{stage}: {human_msg}",
                raw_request={"raw_body": raw_body} if raw_body else None,
                latitude=lat_val,
                longitude=lon_val,
            )
        except Exception:
      
            logger.exception("[EMP_IDENT] Ошибка при записи в qr_issue_logs")

        return JsonResponse(
            {'status': 'error', 'message': human_msg},
            status=http_status
        )

    # 1. Парсинг JSON
    try:
        data = json.loads(raw_body)
    except Exception as e:
        logger.error(f"[EMP_IDENT] JSON parse error: {e}; body={raw_body!r}")
        return _log_and_return_error(
            400,
            "Парсинг JSON",
            f"Некорректный JSON: {e}",
        )
        
    # 1.1 Координаты (опционально)
    lat_val = _to_float_or_none(data.get("latitude"))
    lon_val = _to_float_or_none(data.get("longitude"))

    # 2. Вытаскиваем поля из запроса (совместимость со старым и новым форматами)
    inn_raw = (data.get('inn') or data.get('employee_id') or "").strip()
    fio_raw = (data.get('fio') or data.get('FIO') or "").strip()

    # поддержка storeId / smstore / mx (старое поле)
    smstore_raw = str(
        data.get('storeId')
        or data.get('smstore')
        or data.get('mx')
        or ""
    ).strip()

    phone_raw = (data.get('phone') or "").strip()

    # поддержка и "datetime", и "Datetime", и "event_datetime"
    dt_raw = (
        data.get('datetime')
        or data.get('Datetime')
        or data.get('event_datetime')
        or ""
    )
    dt_raw = dt_raw.strip()

    # направление вход/выход — опционально
    direction = str(data.get('direction') or data.get('event') or "").strip()

    logger.info(
        f"[EMP_IDENT] START: inn={inn_raw!r}, fio={fio_raw!r}, "
        f"storeId/mx={smstore_raw!r}, phone={phone_raw!r}, "
        f"datetime={dt_raw!r}, direction={direction!r},"
        f"latitude={lat_val!r}, longitude={lon_val!r}"
    )

    # 3. Базовая валидация входных полей (как в старой версии)
    if not inn_raw:
        return _log_and_return_error(
            400,
            "Валидация входных данных",
            "Не указан ИНН",
            inn_raw=inn_raw,
            fio_raw=fio_raw,
            smstore_raw=smstore_raw,
            phone_raw=phone_raw,
        )

    try:
        plain_inn = ensure_plain_inn(inn_raw)
    except Exception as e:
        logger.error(f"[EMP_IDENT] Bad INN: {e}")
        return _log_and_return_error(
            400,
            "Валидация ИНН",
            f"Некорректный ИНН: {e}",
            inn_raw=inn_raw,
            fio_raw=fio_raw,
            smstore_raw=smstore_raw,
            phone_raw=phone_raw,
        )

    if not fio_raw:
        return _log_and_return_error(
            400,
            "Валидация входных данных",
            "Не указано ФИО",
            inn_raw=plain_inn,
            fio_raw=fio_raw,
            smstore_raw=smstore_raw,
            phone_raw=phone_raw,
        )
    fio = " ".join(fio_raw.split())

    if not smstore_raw:
        return _log_and_return_error(
            400,
            "Валидация входных данных",
            "Не указан storeId / mx (smstore)",
            inn_raw=plain_inn,
            fio_raw=fio,
            smstore_raw=smstore_raw,
            phone_raw=phone_raw,
        )

    # для Store/ukm4store пробуем привести к int, для 1С MX остаётся строкой
    try:
        sm_store_id_int = int(smstore_raw)
    except ValueError:
        sm_store_id_int = None

    # Нормализация телефона (если есть)
    phone_norm = normalize_phone_ru(phone_raw) if phone_raw else None

    # Маппинг SMSTORE → ukm4store, чтобы записать его в лог
    ukm_store_id = None
    store_obj = None
    if sm_store_id_int is not None:
        store_obj = Store.objects.filter(smstore=sm_store_id_int).first()
        if store_obj and store_obj.ukm4store is not None:
            try:
                ukm_store_id = int(store_obj.ukm4store)
            except (TypeError, ValueError):
                ukm_store_id = None

    # Парсим дату/время события
    if not dt_raw:
        return _log_and_return_error(
            400,
            "Валидация даты/времени",
            "Не указан datetime",
            inn_raw=plain_inn,
            fio_raw=fio,
            smstore_raw=smstore_raw,
            ukm_store_id=ukm_store_id,
            phone_raw=phone_raw,
        )

    try:
        event_dt_str = _parse_and_format_dt(dt_raw)
    except Exception as e:
        return _log_and_return_error(
            400,
            "Валидация даты/времени",
            f"Некорректная дата/время: {e}",
            inn_raw=plain_inn,
            fio_raw=fio,
            smstore_raw=smstore_raw,
            ukm_store_id=ukm_store_id,
            phone_raw=phone_raw,
        )

    # Пытаемся найти пользователя в PG, чтобы связать запись (если есть)
    user_obj = User.objects.filter(employee_id=plain_inn).first()

    # 4. Собираем payload для 1С в старом формате 
    onec_payload = {
        "INN": plain_inn,
        "FIO": fio,
        "MX": smstore_raw,  
        "Datetime": event_dt_str, 
    }

    # Доп.инфа — только в Extra, старую обработку это не ломает
    if phone_norm or direction or sm_store_id_int is not None or store_obj:
        extra = {
            "EmployeeID": encrypt_inn20(plain_inn),
            "StoreId": sm_store_id_int,
            "StoreName": store_obj.name if store_obj else "",
            "Phone": phone_norm or phone_raw,
            "Direction": direction,
        }
        onec_payload["Extra"] = extra

    # Идемпотентный ключ (как раньше — по ИНН/ФИО/магазину/дате)
    idem_key = hashlib.sha256(
        f"{plain_inn}|{fio}|{smstore_raw}|{event_dt_str}".encode("utf-8")
    ).hexdigest()

    # 5. Отправка в 1С
    try:
        status_1c, text_1c = _post_to_onec(onec_payload, idem_key=idem_key)
    except Exception as e:
        logger.exception(f"[EMP_IDENT] Ошибка запроса в 1С: {e}")
        return _log_and_return_error(
            500,
            "Запрос в 1С",
            f"Ошибка запроса в 1С: {e}",
            inn_raw=plain_inn,
            fio_raw=fio,
            smstore_raw=smstore_raw,
            ukm_store_id=ukm_store_id,
            phone_raw=phone_raw,
        )

    # --- 6. Анализ ответа 1С и логирование ---
    ok_1c = (200 <= status_1c < 300)

    # Лог в Telegram (в любом случае)
    resp_short = text_1c if len(text_1c) <= 1000 else text_1c[:1000] + "…"
    tg_lines = [
        "📡 Отметка смены",
        "",
        "👤 Сотрудник:",
        f"  • ФИО: {fio}",
        f"  • ИНН: {plain_inn}",
        f"  • user_id (PostgreSQL): {user_obj.id if user_obj else '—'}",
        "",
        "🏬 Магазин:",
        f"  • MX (из запроса): {smstore_raw}",
        f"  • storeId (SMSTORE int): {sm_store_id_int if sm_store_id_int is not None else '—'}",
        f"  • ukm4store: {ukm_store_id if ukm_store_id is not None else '—'}",
        f"  • Name: {store_obj.name if store_obj else '—'}",
        "",
        "⚙️ Параметры события:",
        f"  • direction: {direction or '—'}",
        f"  • datetime: {event_dt_str or dt_raw or '—'}",
        "",
        "📲 Телефон:",
        f"  • raw: {phone_raw or '—'}",
        f"  • normalized: {phone_norm or '—'}",
        "",
        "🔗 1С:",
        f"  • HTTP статус: {status_1c}",
        f"  • Тело (обрезано): {resp_short}",
    ]
    send_telegram_log("\n".join(tg_lines))

    # Запись в qr_issue_logs
    try:
        log_qr_issue(
            endpoint='employee_identification',
            method=direction or 'EMP_IDENT',
            status='ok' if ok_1c else 'error',
            user=user_obj,
            employee_inn=plain_inn,
            employee_fio=fio,
            tg_id=getattr(user_obj, "tg_id", "") if user_obj else "",
            phone_raw=phone_raw or "",
            phone_normalized=phone_norm or "",
            sm_store_id=sm_store_id_int,
            ukm_store_id=ukm_store_id,
            role_id=None,   # для этого эндпоинта роль не пишем
            qr_data="",     # факт идентификации, а не выдача QR
            error_message="" if ok_1c else f"1С вернула статус {status_1c}",
            raw_request={
                "request": data,
                "onec_payload": onec_payload,
                "onec_status": status_1c,
                "onec_response": text_1c,
            },
            latitude=lat_val,
            longitude=lon_val,
        )
    except Exception:
        logger.exception("[EMP_IDENT] Ошибка при записи успеха/ошибки в qr_issue_logs")

    # --- 7. Ответ клиенту ---
    if not ok_1c:
        return JsonResponse(
            {
                'status': 'error',
                'message': f'1С ответила со статусом {status_1c}',
                'onec_status': status_1c,
                'onec_body': text_1c,
            },
            status=500
        )

    return JsonResponse(
        {
            'status': 'ok',
            'onec_status': status_1c,
            'onec_body': text_1c,
        }
    )










def _human_user_name(user: "User") -> str:
    """
    Возвращает человекочитаемое имя инициатора.
    Подстроено под разные варианты полей.
    """
    for attr in ("full_name", "fio", "name", "username", "login"):
        val = getattr(user, attr, None)
        if val:
            s = str(val).strip()
            if s:
                return s
    # fallback
    return f"user_id={getattr(user, 'id', '—')}"


def _pos_kind_label(*, ukm4: bool, ukm5: bool, is_kso: bool) -> str:
    if ukm4 and is_kso:
        return "КСО УКМ4"
    if ukm4 and not is_kso:
        return "Касса УКМ4"
    if (not ukm4) and ukm5 and is_kso:
        return "КСО УКМ5"
    if (not ukm4) and ukm5 and (not is_kso):
        return "Касса УКМ5"
    return "Неизвестный тип"


def _device_cmd_hint(*, ukm4: bool, ukm5: bool, is_kso: bool) -> tuple[str, bool]:
    """
    Возвращает (команда, use_sudo) как мы реально выполняем reboot.
    """
    if ukm5:
        return ("sudo reboot", True)
    # ukm4
    if is_kso:
        return ("sudo reboot", True)
    return ("reboot", False)









def connect_ukm5_srvdata():
    return pymysql.connect(
        host=UKM5_SRV_HOST,
        user=UKM5_SRV_USER,
        password=UKM5_SRV_PASSWORD,
        database=UKM5_SRV_DB,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
    )


def _parse_allowed_nets(raw: str) -> list[ipaddress._BaseNetwork]:
    out = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(ipaddress.ip_network(part))
        except Exception:
            logger.warning(f"[POS] bad net in POS_REBOOT_ALLOWED_NETS: {part!r}")
    return out

_ALLOWED_NETS = _parse_allowed_nets(POS_REBOOT_ALLOWED_NETS_RAW)


def _ip_allowed(ip: str) -> bool:
    try:
        ip_obj = ipaddress.ip_address(str(ip).strip())
    except Exception:
        return False
    return any(ip_obj in net for net in _ALLOWED_NETS)


def fetch_ukm4_pos_list(*, ukm4_storeid: int, smstore: int, role_id: int | None = None) -> list[dict]:
    info = get_store_info(smstore)
    ukm_host = info.get("ukm4ip")

    conn = cur = None
    items: list[dict] = []
    try:
        conn = connect_ukm(host=ukm_host, store_id=smstore)
        cur = conn.cursor()

        kiosk_pat = "%КИОСК%"
        kso_pat = "%КСО%"

        cur.execute("""
            SELECT
              p.name AS pos_name,
              p.cash_id AS cash_id,
              ps.ip AS ip,
              cg.name AS cg_name,
              CASE
                WHEN cg.name IS NULL THEN 0
                WHEN UPPER(cg.name) LIKE %s OR UPPER(cg.name) LIKE %s THEN 1
                ELSE 0
              END AS is_kso
            FROM trm_in_pos p
            LEFT JOIN (
                SELECT cash_id, ip
                FROM trm_out_pos_state
                WHERE state = 1
            ) ps ON ps.cash_id = p.cash_id
            LEFT JOIN trm_in_configuration_groups cg ON cg.id = p.config_group_id
            WHERE p.store_id = %s
              AND p.active = 1
        """, (kiosk_pat, kso_pat, int(ukm4_storeid)))

        for row in (cur.fetchall() or []):
            is_kso = bool(row.get("is_kso"))
            ip = row.get("ip")

            if not ip or not str(ip).strip():
                continue

            items.append({
                "cash_id": row.get("cash_id"),
                "name": row.get("pos_name") or "",
                "ip": str(ip).strip(),

                "ukm4": True,
                "ukm5": False,
                "is_kso": is_kso,
                "ssh_user": "ukmclient" if is_kso else "root",
                "ukm_store_id": int(ukm4_storeid),   
                "sm_store_id": int(smstore),        
                "role_id": int(role_id) if role_id is not None else None,
            })

        return items
    finally:
        try:
            if cur: cur.close()
            if conn: conn.close()
        except Exception:
            pass


def fetch_ukm5_pos_list(*, smstore: int, ukm4_storeid: int, role_id: int | None = None) -> list[dict]:
    conn = cur = None
    items: list[dict] = []
    try:
        conn = connect_ukm5_srvdata()
        cur = conn.cursor()

        cur.execute(
            "SELECT id FROM store_external_params WHERE external_id=%s LIMIT 1",
            (int(smstore),)
        )
        r = cur.fetchone()
        if not r or not r.get("id"):
            return []

        store_internal_id = int(r["id"])

        cur.execute("""
            SELECT
              p.name AS pos_name,
              p.guid AS guid,
              cs.last_ip_address AS ip
            FROM pos p
            LEFT JOIN tm_client_exchange_statistics cs ON cs.client_uid = p.guid
            WHERE p.store_id = %s
              AND p.active = 1
              AND p.deleted = 0
              AND cs.last_ip_address IS NOT NULL
              AND cs.last_ip_address <> ''
        """, (store_internal_id,))

        for row in (cur.fetchall() or []):
            guid = (row.get("guid") or "").strip()
            ip = (row.get("ip") or "").strip()

            if not guid:
                continue
            if not ip:
                continue  

            items.append({
                "cash_id": guid,
                "name": row.get("pos_name") or "",
                "ip": ip,

                "ukm4": False,
                "ukm5": True,
                "is_kso": True,
                "ssh_user": "ukm5",
                "ukm_store_id": int(ukm4_storeid),  
                "sm_store_id": int(smstore),       
                "role_id": int(role_id) if role_id is not None else None,
            })

        return items
    finally:
        try:
            if cur: cur.close()
            if conn: conn.close()
        except Exception:
            pass


def get_devices_for_tg_id(tg_id: str) -> tuple[Optional[User], list[dict]]:
    tg_id = str(tg_id or "").strip()
    if not tg_id:
        return None, []

    user = User.objects.filter(tg_id=tg_id).first()
    if not user:
        return None, []

    ukm_links = list(
        UKMUser.objects
        .filter(user_id=user.id)
        .values("storeid", "roleid")
    )
    if not ukm_links:
        return user, []

    # storeid -> roleid (берём первый ненулевой, если есть)
    roles_by_store: dict[int, int | None] = {}
    store_ids: list[int] = []
    for x in ukm_links:
        sid_raw = x.get("storeid")
        rid_raw = x.get("roleid")
        if str(sid_raw).isdigit():
            sid = int(sid_raw)
            store_ids.append(sid)
            if sid not in roles_by_store:
                roles_by_store[sid] = int(rid_raw) if str(rid_raw).isdigit() else None

    store_ids = sorted(set(store_ids))
    if not store_ids:
        return user, []

    # ukm4store -> Store из Postgres (если есть)
    store_map = {
        int(s.ukm4store): s
        for s in Store.objects.filter(ukm4store__in=store_ids)
        if s.ukm4store is not None
    }

    devices: list[dict] = []

    for ukm4_storeid in store_ids:
        role_id = roles_by_store.get(ukm4_storeid)

        # 1) smstore пытаемся взять из таблицы Store
        s_obj = store_map.get(int(ukm4_storeid))
        smstore = getattr(s_obj, "smstore", None)

        # 2) если нет — пробуем достать smstore через Oracle по ukm4_storeid
        if smstore is None:
            try:
                info_by_ukm = get_store_info(ukm4_storeid)  # умеет маппить REP.UKMStoreId -> STORELOC
                smstore = info_by_ukm.get("smstore")
            except Exception:
                smstore = None

        if smstore is None:
            logger.warning(f"[POS] smstore not resolved for ukm4store={ukm4_storeid}")
            continue

        # 1) UKM4 кассы
        try:
            devices.extend(
                fetch_ukm4_pos_list(
                    ukm4_storeid=int(ukm4_storeid),
                    smstore=int(smstore),
                    role_id=role_id,
                )
            )
        except Exception as e:
            logger.exception(f"[POS] UKM4 list error ukm4store={ukm4_storeid}, smstore={smstore}: {e}")

        # 2) UKM5 кассы — только если Oracle магазин помечен как UKM5
        try:
            info = get_store_info(int(smstore))
            if info.get("is_ukm5", False):
                devices.extend(
                    fetch_ukm5_pos_list(
                        smstore=int(smstore),
                        ukm4_storeid=int(ukm4_storeid),
                        role_id=role_id,
                    )
                )
        except Exception as e:
            logger.exception(f"[POS] UKM5 list error smstore={smstore}: {e}")

    return user, devices


@csrf_exempt
def pos_list_by_tg(request):
    """
    POST {"tg_id":"..."} -> список касс/КСО UKM4 + UKM5 с ip.
    """
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Только POST"}, status=405)

    try:
        body = json.loads(request.body.decode("utf-8") if request.body else "{}")
    except Exception:
        return JsonResponse({"status": "error", "message": "Некорректный JSON"}, status=400)

    tg_id = str(body.get("tg_id") or "").strip()
    if not tg_id:
        return JsonResponse({"status": "error", "message": "Не указан tg_id"}, status=400)

    user, devices = get_devices_for_tg_id(tg_id)
    if not user:
        return JsonResponse({"status": "error", "message": "Пользователь не найден"}, status=404)

    # Отдадим ровно то, что тебе нужно
    return JsonResponse(
        {
            "status": "ok",
            "tg_id": tg_id,
            "user_id": user.id,
            "devices": devices
        },
        json_dumps_params={"ensure_ascii": False},
    )


def _ssh_reboot(ip: str, *, username: str, password: str, use_sudo: bool) -> dict:
    """
    Делает reboot по SSH (в Docker).
    """
    port = POS_SSH_PORT

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=ip,
            port=port,
            username=username,
            password=password,
            timeout=10,
            banner_timeout=10,
            auth_timeout=10,
            allow_agent=False,
            look_for_keys=False,
        )

        if use_sudo:
            # сначала пробуем без запроса пароля (если NOPASSWD)
            stdin, stdout, stderr = client.exec_command("sudo -n reboot", get_pty=True, timeout=10)
            err = (stderr.read() or b"").decode("utf-8", errors="ignore").strip()

            if err and ("password" in err.lower() or "a password is required" in err.lower()):
                stdin, stdout, stderr = client.exec_command("sudo -S reboot", get_pty=True, timeout=10)
                stdin.write(password + "\n")
                stdin.flush()
                err = (stderr.read() or b"").decode("utf-8", errors="ignore").strip()

            out = (stdout.read() or b"").decode("utf-8", errors="ignore").strip()

            return {"ok": True, "stdout": out, "stderr": err}

        else:
            stdin, stdout, stderr = client.exec_command("reboot", timeout=10)
            out = (stdout.read() or b"").decode("utf-8", errors="ignore").strip()
            err = (stderr.read() or b"").decode("utf-8", errors="ignore").strip()
            return {"ok": True, "stdout": out, "stderr": err}

    finally:
        try:
            client.close()
        except Exception:
            pass


@csrf_exempt
def pos_reboot(request):
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "Только POST"}, status=405)

    try:
        body = json.loads(request.body.decode("utf-8") if request.body else "{}")
    except Exception:
        return JsonResponse({"status": "error", "message": "Некорректный JSON"}, status=400)

    tg_id = str(body.get("tg_id") or "").strip()
    ip = str(body.get("ip") or "").strip()

    # эти поля могут присылать, но мы НЕ доверяем им (используем только для сверки/логов)
    req_ukm4 = body.get("ukm4", None)
    req_ukm5 = body.get("ukm5", None)
    req_is_kso = body.get("is_kso", None)

    if not tg_id or not ip:
        return JsonResponse({"status": "error", "message": "Нужны tg_id и ip"}, status=400)

    if not _ip_allowed(ip):
        return JsonResponse({"status": "error", "message": f"IP {ip} запрещён (allowlist)"}, status=403)

    # 1) ищем пользователя и все его устройства
    user, devices = get_devices_for_tg_id(tg_id)
    if not user:
        return JsonResponse({"status": "error", "message": "Пользователь не найден"}, status=404)

    # 2) находим устройство по IP (и берём ВСЮ правду из него)
    dev = next((d for d in devices if str(d.get("ip") or "").strip() == ip), None)
    if not dev:
        return JsonResponse({"status": "error", "message": "Этот IP не найден среди касс пользователя (запрещено)"}, status=403)

    ukm4 = bool(dev.get("ukm4"))
    ukm5 = bool(dev.get("ukm5"))
    is_kso = bool(dev.get("is_kso"))

    # сверка если клиент прислал флаги (чтобы не было путаницы)
    if req_ukm4 is not None and bool(req_ukm4) != ukm4:
        return JsonResponse({"status": "error", "message": "ukm4 не совпадает с типом кассы пользователя"}, status=400)
    if req_ukm5 is not None and bool(req_ukm5) != ukm5:
        return JsonResponse({"status": "error", "message": "ukm5 не совпадает с типом кассы пользователя"}, status=400)
    if req_is_kso is not None and bool(req_is_kso) != is_kso:
        return JsonResponse({"status": "error", "message": "is_kso не совпадает с типом кассы пользователя"}, status=400)

    if ukm4 and ukm5:
        return JsonResponse({"status": "error", "message": "Некорректное устройство: ukm4 и ukm5 одновременно"}, status=500)
    if not (ukm4 or ukm5):
        return JsonResponse({"status": "error", "message": "Некорректное устройство: не ukm4 и не ukm5"}, status=500)

    # нормальные поля устройства
    cash_id = dev.get("cash_id")
    name = dev.get("name") or ""
    ssh_user = dev.get("ssh_user") or ""
    sm_store_id = dev.get("sm_store_id")
    ukm_store_id = dev.get("ukm_store_id")
    role_id = dev.get("role_id")

    kind = _pos_kind_label(ukm4=ukm4, ukm5=ukm5, is_kso=is_kso)
    cmd_hint, use_sudo_hint = _device_cmd_hint(ukm4=ukm4, ukm5=ukm5, is_kso=is_kso)

    # 3) выбираем ssh-логин и пароль (УЖЕ по реальному типу)
    if ukm5:
        username = "ukm5"
        password = SSH_UKM5_PASSWORD
        use_sudo = True
    else:
        if is_kso:
            username = "ukmclient"
            password = SSH_UKM4_KSO_PASSWORD
            use_sudo = True
        else:
            username = "root"
            password = SSH_UKM4_ROOT_PASSWORD
            use_sudo = False

    if not password:
        return JsonResponse({"status": "error", "message": f"Не задан пароль SSH для {username} (env)"}, status=500)

    initiator_name = _human_user_name(user)

    logger.info(
        f"[POS/REBOOT] start: tg_id={tg_id} user_id={user.id} "
        f"initiator={initiator_name!r} ip={ip} kind={kind} "
        f"sm_store_id={sm_store_id} ukm_store_id={ukm_store_id} role_id={role_id} "
        f"ssh_user={username} cash_id={cash_id} name={name!r}"
    )

    # 4) выполняем reboot + логируем в QRIssueLog + Telegram
    try:
        res = _ssh_reboot(ip, username=username, password=password, use_sudo=use_sudo)

        # DB лог
        log_qr_issue(
            endpoint="pos_reboot",
            method="POS_REBOOT",
            status="ok",
            user=user,
            tg_id=tg_id,
            sm_store_id=int(sm_store_id) if str(sm_store_id).isdigit() else sm_store_id,
            ukm_store_id=int(ukm_store_id) if str(ukm_store_id).isdigit() else ukm_store_id,
            role_id=int(role_id) if str(role_id).isdigit() else role_id,
            employee_inn="",
            employee_fio="",
            phone_raw="",
            phone_normalized="",
            qr_data="",
            error_message="",
            raw_request={
                "request": body,
                "device": dev,
                "ssh_user": username,
                "ssh_port": POS_SSH_PORT,
                "cmd": cmd_hint,
                "result": res,
            },
        )

        # Telegram лог (читабельно)
        msg_lines = [
            "🔁 Перезагрузка кассы",
            "",
            "👤 Инициатор:",
            f"  • {initiator_name}",
            f"  • user_id: {user.id}",
            f"  • tg_id: {tg_id}",
            "",
            "🏬 Магазин:",
            f"  • ukm_store_id (ukm_users.storeid): {ukm_store_id if ukm_store_id is not None else '—'}",
            f"  • sm_store_id (stores.smstore / Oracle STORELOC): {sm_store_id if sm_store_id is not None else '—'}",
            f"  • role_id: {role_id if role_id is not None else '—'}",
            "",
            "💻 Касса:",
            f"  • Тип: {kind}",
            f"  • name: {name or '—'}",
            f"  • cash_id: {cash_id if cash_id is not None else '—'}",
            f"  • ip: {ip}",
            "",
            "🔐 SSH:",
            f"  • user: {username}",
            f"  • port: {POS_SSH_PORT}",
            f"  • cmd: {cmd_hint}",
            "",
            "✅ Результат:",
            f"  • ok: {res.get('ok')}",
            f"  • stdout: {res.get('stdout') or '—'}",
            f"  • stderr: {res.get('stderr') or '—'}",
        ]
        send_telegram_log("\n".join(msg_lines))

        return JsonResponse(
            {"status": "ok", "result": res},
            json_dumps_params={"ensure_ascii": False},
        )

    except Exception as e:
        logger.exception(f"[POS/REBOOT] error: {e}")

        # DB лог ошибки
        try:
            log_qr_issue(
                endpoint="pos_reboot",
                method="POS_REBOOT",
                status="error",
                user=user,
                tg_id=tg_id,
                sm_store_id=int(sm_store_id) if str(sm_store_id).isdigit() else sm_store_id,
                ukm_store_id=int(ukm_store_id) if str(ukm_store_id).isdigit() else ukm_store_id,
                role_id=int(role_id) if str(role_id).isdigit() else role_id,
                employee_inn="",
                employee_fio="",
                phone_raw="",
                phone_normalized="",
                qr_data="",
                error_message=str(e),
                raw_request={
                    "request": body,
                    "device": dev,
                    "ssh_user": username,
                    "ssh_port": POS_SSH_PORT,
                    "cmd": cmd_hint,
                },
            )
        except Exception:
            logger.exception("[POS/REBOOT] failed to write QRIssueLog")

        # Telegram лог ошибки
        msg_lines = [
            "❌ Ошибка перезагрузки кассы",
            "",
            "👤 Инициатор:",
            f"  • {initiator_name}",
            f"  • user_id: {user.id}",
            f"  • tg_id: {tg_id}",
            "",
            "🏬 Магазин:",
            f"  • ukm_store_id: {ukm_store_id if ukm_store_id is not None else '—'}",
            f"  • sm_store_id: {sm_store_id if sm_store_id is not None else '—'}",
            f"  • role_id: {role_id if role_id is not None else '—'}",
            "",
            "💻 Касса:",
            f"  • Тип: {kind}",
            f"  • name: {name or '—'}",
            f"  • cash_id: {cash_id if cash_id is not None else '—'}",
            f"  • ip: {ip}",
            "",
            "🔐 SSH:",
            f"  • user: {username}",
            f"  • port: {POS_SSH_PORT}",
            f"  • cmd: {cmd_hint}",
            "",
            f"🧨 Ошибка: {e}",
        ]
        send_telegram_log("\n".join(msg_lines))

        return JsonResponse(
            {"status": "error", "message": str(e)},
            status=500,
            json_dumps_params={"ensure_ascii": False},
        )










def _custom_transliterate(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    out = []
    for ch in text:
        up = ch.upper()
        if up in _TRANSLIT:
            mapped = _TRANSLIT[up]
            # сохраняем регистр примерно, но нам всё равно lower() потом
            out.append(mapped if ch.isupper() else mapped.lower())
        else:
            out.append(ch)
    return "".join(out)

def _normalize_login_piece(s: str) -> str:
    s = _custom_transliterate(s).lower()
    s = s.replace(".", "_").replace("-", "_").replace(" ", "_")
    s = _LOGIN_SAFE_RE.sub("", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s

def _expand_login_variants(login: str) -> set[str]:
    """
    Частые расхождения транслита (ё/e/yo и т.п.) — добавим “варианты”,
    чтобы шанс матчинга был выше.
    """
    variants = {login}
    if "yo" in login:
        variants.add(login.replace("yo", "e"))
    if "kh" in login:
        variants.add(login.replace("kh", "h"))
    if "ts" in login:
        variants.add(login.replace("ts", "c"))
    return {v for v in variants if v}

def _build_candidate_logins(lastname: str, firstname: str, patronymic: str) -> set[str]:
    ln = _normalize_login_piece(lastname)
    fn = _normalize_login_piece(firstname)
    sn = _normalize_login_piece(patronymic)

    out = set()
    if fn and ln:
        # 1) i_ivanov
        out.add(f"{fn[0]}_{ln}")
        # 2) ivan_ivanov
        out.add(f"{fn}_{ln}")

        # 3) ivan_i_ivanov (если есть отчество)
        if sn:
            out.add(f"{fn}_{sn[0]}_{ln}")

    expanded = set()
    for x in out:
        expanded |= _expand_login_variants(x)
    return expanded

def _is_valid_inn_digits(inn: str) -> bool:
    inn = (inn or "").strip()
    return inn.isdigit() and len(inn) in (10, 12)

def _connect_oracle_service(service_key: str):
    """
    Подключение к Oracle по service_key (например BINUU01, BINCH12 и т.п.)
    Использует ORACLE_TNS_MAP: у каждого сервиса свой host/port/service_name.

    Важно:
      - выставляем conn.callTimeout, чтобы не было "тишины часами" при зависшем execute/commit/fetch.
    """
    ORA_USER     = os.getenv("ORACLE_USER", "supermag")
    ORA_PASSWORD = os.getenv("ORACLE_PASSWORD", "qqq")

    call_timeout_ms = int(os.getenv("INN_SYNC_ORACLE_CALL_TIMEOUT_MS", "120000"))  # 120s

    info = ORACLE_TNS_MAP.get(service_key)

    if not info:
        host = os.getenv("ORACLE_HOST", "192.168.17.239")
        port = int(os.getenv("ORACLE_PORT", "1521"))
        service_name = service_key
        hosts = [host]
    else:
        service_name = (info.get("service_name") or service_key).strip()
        port = int(info.get("port", 1521))
        hosts = info.get("hosts") or [info.get("host")]
        hosts = [h for h in hosts if h]

    last_err = None

    for host in hosts:
        # 1) SERVICE_NAME
        try:
            dsn = cx_Oracle.makedsn(host, port, service_name=service_name)
            logger.info(
                f"[INN_SYNC][ORACLE] connect service_key={service_key} host={host} port={port} service_name={service_name}"
            )
            conn = cx_Oracle.connect(user=ORA_USER, password=ORA_PASSWORD, dsn=dsn, encoding="UTF-8")
            try:
                conn.callTimeout = call_timeout_ms
                logger.info(f"[INN_SYNC][ORACLE] callTimeout={call_timeout_ms}ms service={service_key} host={host}")
            except Exception as e:
                logger.warning(f"[INN_SYNC][ORACLE] cannot set callTimeout service={service_key}: {e}")
            return conn
        except cx_Oracle.DatabaseError as e:
            last_err = e
            logger.warning(
                f"[INN_SYNC][ORACLE] connect failed (service_name) {service_key}@{host}:{port}/{service_name}: {e}"
            )

        # 2) SID fallback
        try:
            dsn2 = cx_Oracle.makedsn(host, port, sid=service_name)
            logger.info(
                f"[INN_SYNC][ORACLE] retry as SID service_key={service_key} host={host} port={port} sid={service_name}"
            )
            conn = cx_Oracle.connect(user=ORA_USER, password=ORA_PASSWORD, dsn=dsn2, encoding="UTF-8")
            try:
                conn.callTimeout = call_timeout_ms
                logger.info(f"[INN_SYNC][ORACLE] callTimeout={call_timeout_ms}ms service={service_key} host={host} (SID)")
            except Exception as e:
                logger.warning(f"[INN_SYNC][ORACLE] cannot set callTimeout service={service_key} (SID): {e}")
            return conn
        except cx_Oracle.DatabaseError as e2:
            last_err = e2
            logger.warning(
                f"[INN_SYNC][ORACLE] connect failed (sid) {service_key}@{host}:{port} sid={service_name}: {e2}"
            )

    raise last_err or RuntimeError(f"Cannot connect to Oracle service {service_key}")

def _fetch_onec_working_employees() -> list[dict]:
    auth = None
    if ONEC_USER and ONEC_PASS:
        auth = (ONEC_USER, ONEC_PASS)

    t0 = time.time()
    logger.info(f"[INN_SYNC] 1C request: {ONEC_WORKING_EMPLOYEES_URL}")
    r = requests.get(
        ONEC_WORKING_EMPLOYEES_URL,
        auth=auth,
        timeout=ONEC_WORKING_EMPLOYEES_TIMEOUT,
        headers={"Accept": "application/json"},
    )
    r.raise_for_status()
    data = r.json()
    dt = time.time() - t0
    logger.info(f"[INN_SYNC] 1C employees fetched: {len(data)} in {dt:.1f}s")
    if not isinstance(data, list):
        raise ValueError("1C ответ не list[...], проверь API Get_WorkingEmployees")
    return data

def _build_employee_index(onec_employees: list[dict]):
    """
    Вернёт:
      - login_to_emps: dict[login] -> list[{inn, fio, raw}]
      - emp_stats: словарь счётчиков
    """
    login_to_emps: dict[str, list[dict]] = defaultdict(list)

    stats = Counter()
    stats["employees_total"] = len(onec_employees)

    for e in onec_employees:
        inn = str(e.get("ИНН") or "").strip()
        lastname = str(e.get("Фамилия") or "").strip()
        firstname = str(e.get("Имя") or "").strip()
        patronymic = str(e.get("Отчество") or "").strip()

        fio = " ".join([x for x in [lastname, firstname, patronymic] if x]).strip()

        if not fio:
            stats["employees_no_fio"] += 1
            continue

        if not _is_valid_inn_digits(inn):
            stats["employees_bad_inn"] += 1
            # всё равно можно проиндексировать (иногда ИНН пустой, но логин нужен для диагностики)
            inn = ""

        cand = _build_candidate_logins(lastname, firstname, patronymic)
        if not cand:
            stats["employees_no_login_candidates"] += 1
            continue

        rec = {"inn": inn, "fio": fio, "raw": e}

        for login in cand:
            login_to_emps[login].append(rec)

        stats["employees_indexed"] += 1

    # посчитаем конфликты (один логин -> несколько разных людей)
    ambiguous = 0
    for login, lst in login_to_emps.items():
        uniq = {(x.get("inn") or "", x.get("fio") or "") for x in lst}
        if len(uniq) > 1:
            ambiguous += 1
    stats["logins_ambiguous"] = ambiguous
    stats["logins_total"] = len(login_to_emps)

    return login_to_emps, stats

def _oracle_fetch_smstaff_rows(cur, only_enabled: bool, only_null_inn: bool):
    where = []
    if only_enabled:
        # обычно userenabled = '1' (как в твоём ответе)
        where.append("(userenabled = '1' OR userenabled = 1)")
    if only_null_inn:
        where.append("(inn IS NULL OR TRIM(inn) = '')")

    sql = """
        SELECT id, surname, serverlogin, inn, userenabled
        FROM smstaff
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id"
    cur.execute(sql)
    rows = cur.fetchall()
    return rows

def _normalize_staff_key(v: str) -> str:
    v = (v or "").strip()
    if not v:
        return ""
    # serverlogin часто в UPPER; surname часто lower; приводим к lower
    v = v.replace(".", "_").replace("-", "_").replace(" ", "_")
    v = v.lower()
    v = _LOGIN_SAFE_RE.sub("", v)
    v = re.sub(r"_+", "_", v).strip("_")
    return v

def sm_sync_inn_from_onec(
    dry_run: bool = True,
    overwrite_existing_inn: bool = False,
    services: list[str] | None = None,
    only_enabled: bool = True,
    only_null_inn: bool = True,
) -> dict:
    """
    Главная функция синхронизации ИНН из 1C в Oracle.smstaff.

    Добавлено:
      - run_id в логах
      - тайминги по шагам
      - heartbeat и прогресс по сканированию
      - логи "chunk_start/chunk_done" с id диапазоном
      - rollback при ошибке и продолжение на следующую базу
    """
    services = services or ORACLE_SERVICES_ALL

    # --- настройки логов/прогресса/таймингов из env ---
    progress_every_rows = int(os.getenv("INN_SYNC_PROGRESS_EVERY_ROWS", "2000"))
    heartbeat_sec = int(os.getenv("INN_SYNC_HEARTBEAT_SEC", "30"))
    slow_warn_sec = float(os.getenv("INN_SYNC_SLOW_STEP_WARN_SEC", "10"))
    batch_size = int(os.getenv("INN_SYNC_UPDATE_BATCH", "1"))

    run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    out_dir = Path(LOG_DIR) / "inn_sync"
    out_dir.mkdir(parents=True, exist_ok=True)

    report_csv = out_dir / f"inn_sync_details_{run_ts}.csv"
    report_json = out_dir / f"inn_sync_summary_{run_ts}.json"

    logger.info(
        f"[INN_SYNC] START run_id={run_id} dry_run={dry_run} overwrite_existing_inn={overwrite_existing_inn} "
        f"only_enabled={only_enabled} only_null_inn={only_null_inn} batch_size={batch_size} services={len(services)}"
    )
    logger.info(f"[INN_SYNC] run_id={run_id} output CSV={report_csv} JSON={report_json}")

    # 1) 1C
    onec_employees = _fetch_onec_working_employees()

    # 2) индекс по логинам
    t_index = time.time()
    login_to_emps, emp_stats = _build_employee_index(onec_employees)
    dt_index = time.time() - t_index
    logger.info(f"[INN_SYNC] run_id={run_id} 1C index built in {dt_index:.1f}s stats={dict(emp_stats)}")

    summary = {
        "run_id": run_id,
        "dry_run": dry_run,
        "overwrite_existing_inn": overwrite_existing_inn,
        "only_enabled": only_enabled,
        "only_null_inn": only_null_inn,
        "batch_size": batch_size,
        "services_requested": services,
        "employee_index": dict(emp_stats),
        "db_results": [],
        "totals": Counter(),
        "files": {"details_csv": str(report_csv), "summary_json": str(report_json)},
    }

    with report_csv.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "service", "staff_id", "surname", "serverlogin", "inn_before",
                "match_key", "match_status", "inn_proposed", "employee_fio",
                "reason"
            ],
        )
        w.writeheader()

        for service in services:
            db_counts = Counter()
            db_result = {"service": service}

            conn = cur = None
            service_start = time.time()
            logger.info(f"[INN_SYNC] run_id={run_id} service={service} START")

            try:
                # connect
                conn = _connect_oracle_service(service)
                cur = conn.cursor()
                db_counts["connect_ok"] += 1

                # небольшая оптимизация чтения (не обязательно, но полезно)
                try:
                    cur.arraysize = int(os.getenv("INN_SYNC_ORACLE_ARRAYSIZE", "1000"))
                except Exception:
                    pass

                # fetch rows
                t_fetch = time.time()
                rows = _oracle_fetch_smstaff_rows(cur, only_enabled=only_enabled, only_null_inn=only_null_inn)
                dt_fetch = time.time() - t_fetch
                db_counts["staff_rows_scanned"] += len(rows)

                if dt_fetch > slow_warn_sec:
                    logger.warning(
                        f"[INN_SYNC] run_id={run_id} service={service} SLOW fetch_rows dt={dt_fetch:.1f}s rows={len(rows)}"
                    )
                else:
                    logger.info(
                        f"[INN_SYNC] run_id={run_id} service={service} fetch_rows dt={dt_fetch:.1f}s rows={len(rows)}"
                    )

                # дубли ключей в smstaff
                t_dups = time.time()
                staff_keys = []
                for (sid, surname, serverlogin, inn_before, userenabled) in rows:
                    sk1 = _normalize_staff_key(serverlogin)
                    sk2 = _normalize_staff_key(surname)
                    if sk1:
                        staff_keys.append(sk1)
                    if sk2 and sk2 != sk1:
                        staff_keys.append(sk2)

                dup_keys = {k for k, c in Counter(staff_keys).items() if c > 1}
                db_counts["staff_duplicate_keys"] += len(dup_keys)
                dt_dups = time.time() - t_dups
                if dt_dups > slow_warn_sec:
                    logger.warning(f"[INN_SYNC] run_id={run_id} service={service} SLOW dup_keys dt={dt_dups:.1f}s dup={len(dup_keys)}")
                else:
                    logger.info(f"[INN_SYNC] run_id={run_id} service={service} dup_keys dt={dt_dups:.1f}s dup={len(dup_keys)}")

                updates = []

                scanned = 0
                last_hb = time.time()

                # scan rows
                for (sid, surname, serverlogin, inn_before, userenabled) in rows:
                    scanned += 1
                    now = time.time()

                    if scanned % progress_every_rows == 0:
                        logger.info(f"[INN_SYNC] run_id={run_id} service={service} scan_progress {scanned}/{len(rows)}")
                    elif now - last_hb >= heartbeat_sec:
                        logger.info(f"[INN_SYNC] run_id={run_id} service={service} heartbeat scan scanned={scanned}/{len(rows)}")
                        last_hb = now

                    inn_before_s = (inn_before or "").strip() if inn_before is not None else ""

                    if inn_before_s and not overwrite_existing_inn:
                        db_counts["skip_already_has_inn"] += 1
                        w.writerow({
                            "service": service, "staff_id": sid, "surname": surname, "serverlogin": serverlogin,
                            "inn_before": inn_before_s,
                            "match_key": "", "match_status": "skip_has_inn",
                            "inn_proposed": "", "employee_fio": "", "reason": "already has INN",
                        })
                        continue

                    key_server = _normalize_staff_key(serverlogin)
                    key_surname = _normalize_staff_key(surname)

                    match_key = ""
                    emps = None

                    if key_server and key_server in login_to_emps:
                        match_key = key_server
                        emps = login_to_emps[key_server]
                    elif key_surname and key_surname in login_to_emps:
                        match_key = key_surname
                        emps = login_to_emps[key_surname]

                    if not emps:
                        db_counts["not_found"] += 1
                        w.writerow({
                            "service": service, "staff_id": sid, "surname": surname, "serverlogin": serverlogin,
                            "inn_before": inn_before_s,
                            "match_key": "", "match_status": "not_found",
                            "inn_proposed": "", "employee_fio": "", "reason": "no match in 1C index",
                        })
                        continue

                    uniq_people = {(x.get("inn") or "", x.get("fio") or "") for x in emps}
                    if len(uniq_people) > 1:
                        db_counts["ambiguous"] += 1
                        sample = "; ".join([f"{fio}:{inn}" for (inn, fio) in list(uniq_people)[:5]])
                        w.writerow({
                            "service": service, "staff_id": sid, "surname": surname, "serverlogin": serverlogin,
                            "inn_before": inn_before_s,
                            "match_key": match_key, "match_status": "ambiguous",
                            "inn_proposed": "", "employee_fio": "", "reason": f"multiple employees for login: {sample}",
                        })
                        continue

                    (inn_prop, fio_prop) = next(iter(uniq_people))
                    inn_prop = (inn_prop or "").strip()

                    if not _is_valid_inn_digits(inn_prop):
                        db_counts["bad_inn_in_1c_match"] += 1
                        w.writerow({
                            "service": service, "staff_id": sid, "surname": surname, "serverlogin": serverlogin,
                            "inn_before": inn_before_s,
                            "match_key": match_key, "match_status": "bad_inn",
                            "inn_proposed": inn_prop, "employee_fio": fio_prop,
                            "reason": "matched employee has empty/invalid INN in 1C",
                        })
                        continue

                    if inn_before_s and inn_before_s == inn_prop:
                        db_counts["already_same_inn"] += 1
                        w.writerow({
                            "service": service, "staff_id": sid, "surname": surname, "serverlogin": serverlogin,
                            "inn_before": inn_before_s,
                            "match_key": match_key, "match_status": "already_same",
                            "inn_proposed": inn_prop, "employee_fio": fio_prop,
                            "reason": "INN already equals proposed",
                        })
                        continue

                    db_counts["matched_ok"] += 1
                    if match_key in dup_keys:
                        db_counts["matched_but_staff_key_duplicate"] += 1

                    w.writerow({
                        "service": service, "staff_id": sid, "surname": surname, "serverlogin": serverlogin,
                        "inn_before": inn_before_s,
                        "match_key": match_key, "match_status": "will_update" if not dry_run else "matched_dry_run",
                        "inn_proposed": inn_prop, "employee_fio": fio_prop,
                        "reason": "ok",
                    })

                    if not dry_run:
                        updates.append({"inn": inn_prop, "id": sid})

                logger.info(
                    f"[INN_SYNC] run_id={run_id} service={service} prepared_updates={len(updates)} "
                    f"matched_ok={db_counts.get('matched_ok', 0)} not_found={db_counts.get('not_found', 0)} "
                    f"ambiguous={db_counts.get('ambiguous', 0)} skip_has_inn={db_counts.get('skip_already_has_inn', 0)}"
                )

                # updates
                if not dry_run and updates:
                    updated_total = 0
                    total_to_update = len(updates)

                    chunks_total = (total_to_update + batch_size - 1) // batch_size

                    for i in range(0, total_to_update, batch_size):
                        chunk = updates[i:i + batch_size]
                        first_id = chunk[0]["id"]
                        last_id = chunk[-1]["id"]
                        chunk_no = (i // batch_size) + 1

                        logger.info(
                            f"[INN_SYNC] run_id={run_id} service={service} chunk_start "
                            f"{chunk_no}/{chunks_total} size={len(chunk)} ids={first_id}-{last_id}"
                        )

                        t_chunk = time.time()
                        try:
                            cur.executemany("UPDATE smstaff SET inn = :inn WHERE id = :id", chunk)
                            conn.commit()
                        except Exception as e:
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                            db_counts["update_failed_chunks"] += 1
                            logger.exception(
                                f"[INN_SYNC] run_id={run_id} service={service} chunk_failed "
                                f"size={len(chunk)} ids={first_id}-{last_id}: {e}"
                            )
                            # продолжаем дальше, чтобы не останавливать весь прогон
                            continue

                        dt_chunk = time.time() - t_chunk
                        updated_total += len(chunk)

                        if dt_chunk > slow_warn_sec:
                            logger.warning(
                                f"[INN_SYNC] run_id={run_id} service={service} chunk_done SLOW dt={dt_chunk:.1f}s "
                                f"committed {updated_total}/{total_to_update} ids={first_id}-{last_id}"
                            )
                        else:
                            logger.info(
                                f"[INN_SYNC] run_id={run_id} service={service} chunk_done dt={dt_chunk:.1f}s "
                                f"committed {updated_total}/{total_to_update} ids={first_id}-{last_id}"
                            )

                    db_counts["updated"] += updated_total
                else:
                    db_counts["updated"] += 0

                db_result["duration_sec"] = round(time.time() - service_start, 2)

                logger.info(
                    f"[INN_SYNC] run_id={run_id} service={service} DONE "
                    f"updated={db_counts.get('updated', 0)} failed_chunks={db_counts.get('update_failed_chunks', 0)} "
                    f"duration={db_result['duration_sec']}s"
                )

            except Exception as e:
                db_counts["connect_fail"] += 1
                db_result["error"] = str(e)
                logger.exception(f"[INN_SYNC] run_id={run_id} service={service} FAILED: {e}")

            finally:
                try:
                    if cur:
                        cur.close()
                    if conn:
                        conn.close()
                except Exception:
                    pass

            db_result.update(dict(db_counts))
            summary["db_results"].append(db_result)
            summary["totals"].update(db_counts)

    summary["totals"] = dict(summary["totals"])

    with report_json.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    logger.info(f"[INN_SYNC] FINISH run_id={run_id} dry_run={dry_run} CSV={report_csv} JSON={report_json}")
    return summary


@csrf_exempt
def sm_staff_sync_inn(request):
    """
    POST /sm/staff/sync-inn/
    Body JSON:
    {
      "dry_run": true,
      "overwrite_existing_inn": false,
      "only_enabled": true,
      "only_null_inn": true,
      "services": ["BINUU00","BINUU01"]
    }
    """
    if request.method != "POST":
        return JsonResponse(
            {"status": "error", "message": "Только POST"},
            status=405,
            json_dumps_params={"ensure_ascii": False},
        )

    try:
        body = request.body.decode("utf-8") if request.body else "{}"
        data = json.loads(body or "{}")

        dry_run = bool(data.get("dry_run", True))
        overwrite_existing_inn = bool(data.get("overwrite_existing_inn", False))
        only_enabled = bool(data.get("only_enabled", True))
        only_null_inn = bool(data.get("only_null_inn", True))

        services = data.get("services")
        if services is not None:
            if not isinstance(services, list) or not all(isinstance(x, str) for x in services):
                return JsonResponse(
                    {"status": "error", "message": "services должен быть list[str]"},
                    status=400,
                    json_dumps_params={"ensure_ascii": False},
                )
            services = [s.strip() for s in services if s and s.strip()]

        logger.info(
            f"[INN_SYNC_VIEW] request dry_run={dry_run} overwrite_existing_inn={overwrite_existing_inn} "
            f"only_enabled={only_enabled} only_null_inn={only_null_inn} services={services or 'ALL'}"
        )

        result = sm_sync_inn_from_onec(
            dry_run=dry_run,
            overwrite_existing_inn=overwrite_existing_inn,
            services=services,
            only_enabled=only_enabled,
            only_null_inn=only_null_inn,
        )

        return JsonResponse(
            {"status": "ok", "result": result},
            json_dumps_params={"ensure_ascii": False, "indent": 2},
        )

    except Exception as e:
        logger.exception(f"[INN_SYNC_VIEW] error: {e}")
        return JsonResponse(
            {"status": "error", "message": str(e)},
            status=500,
            json_dumps_params={"ensure_ascii": False},
        )


def _oracle_get_table_columns_set(cur, owner: str, table: str) -> set[str]:
    """
    Возвращает set({ 'ID', 'SURNAME', ... }) по all_tab_columns.
    Важно: bind-имена делаем "безопасными", чтобы не ловить ORA-01745.
    """
    cur.execute(
        """
        SELECT column_name
        FROM all_tab_columns
        WHERE owner = :b_owner
          AND table_name = :b_table
        """,
        b_owner=owner.upper(),
        b_table=table.upper(),
    )
    return {str(r[0]).upper() for r in cur.fetchall()}


def _normalize_service_key(raw: str) -> str:
    return (raw or "").strip().upper()

def _is_allowed_service(service_key: str) -> bool:
    # Жёсткий whitelist: только то, что ты явно описал
    if service_key in ORACLE_TNS_MAP:
        return True
    if service_key in ORACLE_SERVICES_ALL:
        return True
    return False


@csrf_exempt
def sm_staff_list_by_db(request):
    """
    GET /sm/staff/by-db/?db=BINUU00&limit=50&offset=0&q=иванов&only_enabled=1

    - db: ключ сервиса из ORACLE_TNS_MAP (BINUU00/BINUU01/BINCH12/...)
    - only_enabled=1: фильтр (userenabled = 1 OR userenabled='1')
    - пагинация через ROW_NUMBER() (совместимо с Oracle 11g+)
    - устойчиво к отсутствующим колонкам (patronymic/name и т.д.)
    """
    if request.method != "GET":
        return JsonResponse(
            {"status": "error", "message": "Только GET"},
            status=405,
            json_dumps_params={"ensure_ascii": False},
        )

    db = (request.GET.get("db") or request.GET.get("service") or "").strip().upper()
    if not db:
        return JsonResponse(
            {"status": "error", "message": "Не передан параметр db (например db=BINUU00)"},
            status=400,
            json_dumps_params={"ensure_ascii": False},
        )

    # whitelist
    if db not in ORACLE_TNS_MAP:
        return JsonResponse(
            {"status": "error", "message": f"Неизвестная база db={db!r}. Добавь её в ORACLE_TNS_MAP."},
            status=400,
            json_dumps_params={"ensure_ascii": False},
        )

    # limit/offset
    try:
        limit = int(request.GET.get("limit", "200"))
    except ValueError:
        limit = 200
    try:
        offset = int(request.GET.get("offset", "0"))
    except ValueError:
        offset = 0

    limit = max(1, min(limit, 1000))
    offset = max(0, offset)

    q = (request.GET.get("q") or "").strip().lower()

    # only_enabled: по умолчанию 0 (не фильтровать), если передали 1/true/yes -> фильтровать
    only_enabled_raw = (request.GET.get("only_enabled") or "").strip().lower()
    only_enabled = only_enabled_raw in ("1", "true", "yes", "y", "on")

    conn = cur = None
    try:
        conn = _connect_oracle_service(db)
        cur = conn.cursor()

        # какие колонки реально есть в этой базе
        cols_set = _oracle_get_table_columns_set(cur, owner="SUPERMAG", table="SMSTAFF")

        def has(col: str) -> bool:
            return col.upper() in cols_set

        # SELECT: если колонки нет — отдаём NULL AS col
        select_parts = []
        def add_select(col: str):
            if has(col):
                select_parts.append(col)
            else:
                select_parts.append(f"NULL AS {col}")

        add_select("id")
        add_select("surname")
        add_select("name")
        add_select("patronymic")
        add_select("serverlogin")
        add_select("inn")
        add_select("userenabled")

        base_sql = f"SELECT {', '.join(select_parts)} FROM smstaff"
        binds = {}

        where_parts = []

        if only_enabled:
            if has("userenabled"):
                where_parts.append("(userenabled = '1' OR userenabled = 1)")
            else:
                # если вдруг колонки нет — фильтровать нечем
                pass

        if q:
            like = f"%{q}%"
            or_parts = []
            if has("surname"):
                or_parts.append("LOWER(surname) LIKE :q")
            if has("name"):
                or_parts.append("LOWER(name) LIKE :q")
            if has("patronymic"):
                or_parts.append("LOWER(patronymic) LIKE :q")
            if has("serverlogin"):
                or_parts.append("LOWER(serverlogin) LIKE :q")

            if or_parts:
                where_parts.append("(" + " OR ".join(or_parts) + ")")
                binds["q"] = like

        if where_parts:
            base_sql += " WHERE " + " AND ".join(where_parts)

        # ORDER BY (для ROW_NUMBER)
        if has("surname"):
            order_expr = "surname"
        elif has("id"):
            order_expr = "id"
        else:
            order_expr = "1"

        # Oracle 11g/12c-safe pagination
        # rn > offset  AND rn <= offset+limit
        sql = f"""
            SELECT *
            FROM (
                SELECT t.*, ROW_NUMBER() OVER (ORDER BY {order_expr}) rn
                FROM (
                    {base_sql}
                ) t
            )
            WHERE rn > :b_off
              AND rn <= :b_off_to
        """
        binds["b_off"] = offset
        binds["b_off_to"] = offset + limit

        cur.execute(sql, binds)
        items = _oracle_rows_to_jsonable(cur)

        return JsonResponse(
            {
                "status": "ok",
                "db": db,
                "count": len(items),
                "limit": limit,
                "offset": offset,
                "only_enabled": only_enabled,
                "columns_present": sorted(list(cols_set)),
                "items": items,
            },
            json_dumps_params={"ensure_ascii": False, "indent": 2},
        )

    except Exception as e:
        logger.exception(f"[SM/STAFF_BY_DB] error db={db}: {e}")
        return JsonResponse(
            {"status": "error", "message": str(e), "db": db},
            status=500,
            json_dumps_params={"ensure_ascii": False},
        )
    finally:
        try:
            if cur:
                cur.close()
            if conn:
                conn.close()
        except Exception:
            pass









def _ui_services_list() -> list[str]:
    # Показываем только те базы, где реально есть host/port в ORACLE_TNS_MAP
    return sorted(list(ORACLE_TNS_MAP.keys()))


def _ui_has_col(cols_set: set[str], col: str) -> bool:
    return col.upper() in cols_set


@require_http_methods(["GET"])
def sm_staff_ui_list(request):
    """
    UI список SMSTAFF по выбранной базе:
    GET /ui/smstaff/?db=BINUU00&login=ivan&inn=123&only_enabled=1&page=1&page_size=50
    """
    services = _ui_services_list()
    db = (request.GET.get("db") or "").strip().upper()
    if not db:
        # по умолчанию первая база
        db = services[0] if services else "BINUU00"
    if not _is_allowed_service(db) or db not in ORACLE_TNS_MAP:
        return render(request, "frostapp/smstaff_list.html", {
            "services": services,
            "db": db,
            "error": f"Неизвестная база db={db!r}. Добавь её в ORACLE_TNS_MAP.",
            "items": [],
        })
    login = (request.GET.get("login") or "").strip().lower()
    inn = (request.GET.get("inn") or "").strip()
    only_enabled = (request.GET.get("only_enabled") or "").strip().lower() in ("1", "true", "yes", "on")
    try:
        page = int(request.GET.get("page", "1"))
    except ValueError:
        page = 1
    try:
        page_size = int(request.GET.get("page_size", "50"))
    except ValueError:
        page_size = 50
    page = max(1, page)
    page_size = max(10, min(200, page_size))
    offset = (page - 1) * page_size
    conn = cur = None
    items = []
    total = 0
    cols_present = []
    try:
        conn = _connect_oracle_service(db)
        cur = conn.cursor()
        cols_set = _oracle_get_table_columns_set(cur, owner="SUPERMAG", table="SMSTAFF")
        cols_present = sorted(list(cols_set))
        def SEL(col: str) -> str:
            # если колонки нет — отдадим NULL
            return col if _ui_has_col(cols_set, col) else f"NULL AS {col}"
        select_sql = ", ".join([
            SEL("id"),
            SEL("surname"),
            SEL("name"),
            SEL("patronymic"),
            SEL("serverlogin"),
            SEL("inn"),
            SEL("userenabled"),
        ])
        where = []
        binds = {}
        if only_enabled and _ui_has_col(cols_set, "userenabled"):
            where.append("(userenabled = '1' OR userenabled = 1)")
        if login and _ui_has_col(cols_set, "serverlogin"):
            where.append("LOWER(serverlogin) LIKE :b_login")
            binds["b_login"] = f"%{login}%"
        if inn and _ui_has_col(cols_set, "inn"):
            # ищем подстрокой (удобно), но можно заменить на "=" если нужно строго
            where.append("TRIM(inn) LIKE :b_inn")
            binds["b_inn"] = f"%{inn}%"
        base_from = f"FROM smstaff"
        if where:
            base_from += " WHERE " + " AND ".join(where)
        # total count
        cur.execute(f"SELECT COUNT(*) {base_from}", binds)
        total = int(cur.fetchone()[0])
        # ORDER BY для пагинации
        order_expr = "surname" if _ui_has_col(cols_set, "surname") else "id"
        sql = f"""
            SELECT *
            FROM (
                SELECT t.*, ROW_NUMBER() OVER (ORDER BY {order_expr}) rn
                FROM (
                    SELECT {select_sql}
                    {base_from}
                ) t
            )
            WHERE rn > :b_off
              AND rn <= :b_to
        """
        binds2 = dict(binds)
        binds2["b_off"] = offset
        binds2["b_to"] = offset + page_size
        cur.execute(sql, binds2)
        items = _oracle_rows_to_jsonable(cur)
    except Exception as e:
        logger.exception(f"[UI/SMSTAFF] list error db={db}: {e}")
        return render(request, "frostapp/smstaff_list.html", {
            "services": services,
            "db": db,
            "error": str(e),
            "items": [],
        })
    finally:
        try:
            if cur: cur.close()
            if conn: conn.close()
        except Exception:
            pass
    pages = max(1, (total + page_size - 1) // page_size)
    # ссылки пагинации с сохранением фильтров
    def page_url(p: int) -> str:
        if p < 1 or p > pages:  # Защита от некорректных значений
            return ""
        q = {
            "db": db,
            "login": login,
            "inn": inn,
            "only_enabled": "1" if only_enabled else "",
            "page_size": str(page_size),
            "page": str(p),
        }
        # выкидываем пустые
        q = {k: v for k, v in q.items() if v not in ("", None)}
        return f"{reverse('sm_staff_ui_list')}?{urlencode(q)}"

    # Вычисление конкретных URL для пагинации
    first_page_url = page_url(1) if pages > 0 else ''
    prev_page_url = page_url(page - 1) if page > 1 else ''
    next_page_url = page_url(page + 1) if page < pages else ''
    last_page_url = page_url(pages) if pages > 0 else ''

    return render(request, "frostapp/smstaff_list.html", {
        "services": services,
        "db": db,
        "login": login,
        "inn": inn,
        "only_enabled": only_enabled,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "total": total,
        "items": items,
        "cols_present": cols_present,
        "page_url": page_url,  # Оставляем для других нужд, если требуется
        "first_page_url": first_page_url,
        "prev_page_url": prev_page_url,
        "next_page_url": next_page_url,
        "last_page_url": last_page_url,
    })


@require_http_methods(["GET", "POST"])
@csrf_protect
def sm_staff_ui_edit_inn(request, db: str, staff_id: str):
    """
    UI редактирование ИНН:
    GET/POST /ui/smstaff/<db>/edit/<staff_id>/
    staff_id в urls.py допускает отрицательные (re_path), но мы их отвергаем валидацией.
    """

    # --- normalize db ---
    db = (db or "").strip().upper()

    # --- parse staff_id ---
    try:
        staff_id_int = int(staff_id)
    except (ValueError, TypeError):
        return render(request, "frostapp/smstaff_edit.html", {
            "db": db,
            "staff_id": staff_id,
            "row": None,
            "ok": False,
            "error": f"Некорректный ID пользователя: {staff_id}. ID должен быть числом.",
        })

    if staff_id_int <= 0:
        return render(request, "frostapp/smstaff_edit.html", {
            "db": db,
            "staff_id": staff_id_int,
            "row": None,
            "ok": False,
            "error": f"Некорректный ID пользователя: {staff_id_int}. ID должен быть положительным числом.",
        })

    # --- validate db against map/allowlist ---
    if not _is_allowed_service(db) or db not in ORACLE_TNS_MAP:
        return render(request, "frostapp/smstaff_edit.html", {
            "db": db,
            "staff_id": staff_id_int,
            "row": None,
            "ok": False,
            "error": f"Неизвестная база db={db!r}. Добавь её в ORACLE_TNS_MAP.",
        })

    conn = cur = None
    row = None
    error = ""
    ok = False

    try:
        conn = _connect_oracle_service(db)
        cur = conn.cursor()

        cols_set = _oracle_get_table_columns_set(cur, owner="SUPERMAG", table="SMSTAFF")

        # Без INN редактировать нечего
        if not _ui_has_col(cols_set, "inn"):
            return render(request, "frostapp/smstaff_edit.html", {
                "db": db,
                "staff_id": staff_id_int,
                "row": None,
                "ok": False,
                "error": "В этой базе в SMSTAFF нет колонки INN — редактирование невозможно.",
            })

        def SEL(col: str) -> str:
            # если колонки нет — отдадим NULL AS col, чтобы шаблон не ломался
            return col if _ui_has_col(cols_set, col) else f"NULL AS {col}"

        select_sql = ", ".join([
            SEL("id"),
            SEL("surname"),
            SEL("name"),
            SEL("patronymic"),
            SEL("serverlogin"),
            SEL("inn"),
            SEL("userenabled"),
        ])

        # читаем строку
        cur.execute(f"""
            SELECT {select_sql}
            FROM smstaff
            WHERE id = :b_id
        """, b_id=staff_id_int)

        items = _oracle_rows_to_jsonable(cur)
        if not items:
            return render(request, "frostapp/smstaff_edit.html", {
                "db": db,
                "staff_id": staff_id_int,
                "row": None,
                "ok": False,
                "error": f"Пользователь с id={staff_id_int} не найден в {db}.",
            })

        row = items[0]  # dict с ключами lower-case

        if request.method == "POST":
            new_inn = (request.POST.get("inn") or "").strip()  

            # Валидация
            if new_inn and not _is_valid_inn_digits(new_inn):
                error = "ИНН должен быть числом длиной 10 или 12."
            else:
                login_val = (row.get("serverlogin") or "").strip()
                if not login_val:
                    error = "У выбранного пользователя пустой serverlogin — нельзя обновить по всем базам."
                else:
                    # Переходим на прогресс-страницу, которая сама пробежит все базы
                    dbs = _ui_services_list()
                    return render(request, "frostapp/smstaff_sync_inn.html", {
                        "start_db": db,
                        "login": login_val,
                        "new_inn": new_inn,  # "" => очистка (NULL)
                        "dbs": dbs,
                        "return_url": f"/ui/smstaff/?db={db}",
                    })

    except Exception as e:
        logger.exception(f"[UI/SMSTAFF] edit error db={db} id={staff_id_int}: {e}")
        error = str(e)
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

    finally:
        try:
            if cur:
                cur.close()
            if conn:
                conn.close()
        except Exception:
            pass

    return render(request, "frostapp/smstaff_edit.html", {
        "db": db,
        "staff_id": staff_id_int,
        "row": row,
        "error": error,
        "ok": ok,
    })


@require_http_methods(["POST"])
@csrf_protect
def sm_staff_ui_sync_inn_one(request):
    """
    JSON: обновляет INN по serverlogin в ОДНОЙ базе.
    Вызывается со страницы прогресса по очереди для каждой базы.
    body: {"db":"BINUU00","login":"i_ivanov","inn":"123..."}  # inn может быть "" для очистки
    """
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse({"ok": False, "error": "Некорректный JSON"}, status=400)

    db = (payload.get("db") or "").strip().upper()
    login = (payload.get("login") or "").strip()
    inn = (payload.get("inn") or "")
    inn = (inn.strip() if isinstance(inn, str) else "")

    if not db or db not in ORACLE_TNS_MAP or not _is_allowed_service(db):
        return JsonResponse({"ok": False, "db": db, "error": "Неизвестная/запрещённая база"}, status=400)

    if not login:
        return JsonResponse({"ok": False, "db": db, "error": "Пустой login"}, status=400)

    if inn and not _is_valid_inn_digits(inn):
        return JsonResponse({"ok": False, "db": db, "error": "ИНН должен быть числом длиной 10 или 12"}, status=400)

    conn = cur = None
    try:
        conn = _connect_oracle_service(db)
        cur = conn.cursor()

        cols_set = _oracle_get_table_columns_set(cur, owner="SUPERMAG", table="SMSTAFF")

        if not _ui_has_col(cols_set, "inn"):
            return JsonResponse({"ok": False, "db": db, "error": "В SMSTAFF нет колонки INN"}, status=400)

        if not _ui_has_col(cols_set, "serverlogin"):
            return JsonResponse({"ok": False, "db": db, "error": "В SMSTAFF нет колонки SERVERLOGIN"}, status=400)

        # Обновляем по логину (без учёта регистра)
        if inn:
            cur.execute(
                """
                UPDATE smstaff
                   SET inn = :b_inn
                 WHERE LOWER(serverlogin) = LOWER(:b_login)
                """,
                b_inn=inn, b_login=login
            )
        else:
            cur.execute(
                """
                UPDATE smstaff
                   SET inn = NULL
                 WHERE LOWER(serverlogin) = LOWER(:b_login)
                """,
                b_login=login
            )

        affected = int(cur.rowcount or 0)
        conn.commit()

        # ok=True даже если affected=0 (просто не нашли пользователя в этой базе)
        level = "success" if affected > 0 else "warning"
        msg = "Обновлено" if affected > 0 else "Пользователь не найден (0 строк)"

        return JsonResponse({
            "ok": True,
            "db": db,
            "level": level,        # success | warning
            "affected": affected,
            "message": msg,
            "login": login,
            "inn": inn,
        })

    except Exception as e:
        logger.exception(f"[UI/SMSTAFF] sync inn one error db={db} login={login}: {e}")
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        return JsonResponse({"ok": False, "db": db, "error": str(e)}, status=500)

    finally:
        try:
            if cur: cur.close()
            if conn: conn.close()
        except Exception:
            pass
