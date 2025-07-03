import hashlib
import json
import os
import logging
import random
import string
import pymysql
import xml.etree.ElementTree as ET
import cx_Oracle
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import Queue, MODUL_logs




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




def connect_ukm():
    return pymysql.connect(
        host="192.168.17.237",
        user="ukminfo",
        password="CtHDbCGK.C",
        database="ukmserver",
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )


def connect_converter():
    return pymysql.connect(
        host="192.168.17.237",
        port=3306,
        user="user1C",
        password="852654",
        database="import4staffbonus",
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )


def connect_oracle():
    dsn = cx_Oracle.makedsn("192.168.17.237", 1521, service_name="xe")
    return cx_Oracle.connect(user="supermag_user", password="supermag_pass", dsn=dsn, encoding="UTF-8")

def is_ukm5_store(storeid):
    try:
        dsn = cx_Oracle.makedsn("192.168.17.237", 1521, service_name="BINUU00")
        conn = cx_Oracle.connect(user="sys", password="qqq", dsn=dsn, encoding="UTF-8", mode=cx_Oracle.SYSDBA)
        cursor = conn.cursor()

        query = f"""
            SELECT T1.ID as SMSTORE,
                   T4.PROPVAL as UKM5STORE
            FROM SMSTORELOCATIONS T1,
                 (SELECT STORELOC, PROPVAL FROM SMSTOREPROPERTIES WHERE PROPID = 'REP.UKMSERVER5') T4
            WHERE T1.ID = T4.STORELOC(+)
              AND T1.ID = :storeid
        """

        cursor.execute(query, storeid=storeid)
        row = cursor.fetchone()
        conn.close()

        ukm5_present = row and row[1] is not None
        logger.info(f"Проверка UKM5 по Oracle для storeid={storeid}: {'UKM5' if ukm5_present else 'UKM4'}")
        return ukm5_present

    except Exception as e:
        logger.error(f"Ошибка при проверке UKM5 через Oracle: {e}")
        return False


def generate_password():
    return "KS" + ''.join(random.choices(string.ascii_letters + string.digits, k=8))

@csrf_exempt
def register_cashier(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)

    try:
        data = json.loads(request.body)
        required_fields = ['inn', 'surname', 'name', 'patronymic', 'roleId', 'storeid']
        missing = [f for f in required_fields if not data.get(f)]
        if missing:
            logger.error(f"Пропущены поля: {missing}")
            return JsonResponse({'status': 'error', 'message': f'Пропущены поля: {missing}'}, status=400)

        inn = data['inn']
        fio = f"{data['surname']} {data['name']} {data['patronymic']}"
        role_id = data['roleId']
        storeid = int(data['storeid'])
        password_plain = generate_password()
        password_hashed = password_plain

        # Определяем UKM4/UKM5
        ukm_version = "UKM5" if is_ukm5_store(storeid) else "UKM4"
        logger.info(f"Магазин {storeid} определён как {ukm_version}")

        # Проверка существования в ukmserver
        ukm_conn = connect_ukm()
        ukm_cursor = ukm_conn.cursor()
        ukm_cursor.execute("SELECT * FROM trm_in_users WHERE user_inn=%s AND name=%s", (inn, fio))
        existing = ukm_cursor.fetchone()
        ukm_conn.close()

        if existing:
            logger.info(f"[{ukm_version}] Сотрудник уже существует: {fio}, ID={existing['id']}")
            return JsonResponse({'status': 'ok', 'message': 'Сотрудник уже зарегистрирован'}, status=200)

        # Работа с import4staffbonus
        converter = connect_converter()
        conv_cursor = converter.cursor()

        # Получаем ID из ukmserver.trm_in_users
        ukm_conn = connect_ukm()
        ukm_cursor = ukm_conn.cursor()
        ukm_cursor.execute("SELECT MAX(id) + 1 AS next_id FROM trm_in_users")
        cashier_id = ukm_cursor.fetchone()['next_id'] or 1
        ukm_conn.close()

        # Версия
        conv_cursor.execute("SELECT COUNT(*) AS cnt FROM `signal` WHERE `signal` = 'busy'")
        version = (conv_cursor.fetchone()['cnt'] or 0) + 1

        # Вставка в users
        conv_cursor.execute("""
            INSERT INTO users (store, id, name, inn, password, role_id, version, deleted)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 0)
        """, (storeid, cashier_id, fio, inn, password_hashed, role_id, version))

        # Вставка в signal
        conv_cursor.execute("INSERT INTO `signal`(`signal`, version) VALUES ('incr', %s)", (version,))
        converter.commit()
        converter.close()

        logger.info(f"[{ukm_version}] Кассир {fio} добавлен (ID={cashier_id})")

        # XML для УКМ5
        xml_path = None
        if ukm_version == "UKM5":
            xml_filename = f"StoreCashiers_{storeid}_{cashier_id}_F.xml"
            xml_path = os.path.join(r"\\SGO1\UKM5\SGO\CASHLOAD", xml_filename)
            try:
                root = ET.Element("StoreCashiers")
                cashier = ET.SubElement(root, "Cashier")
                ET.SubElement(cashier, "Id").text = str(cashier_id)
                ET.SubElement(cashier, "Name").text = fio
                ET.SubElement(cashier, "INN").text = inn
                ET.SubElement(cashier, "Password").text = password_plain
                ET.SubElement(cashier, "RoleId").text = str(role_id)
                ET.SubElement(cashier, "Deleted").text = "0"
                tree = ET.ElementTree(root)
                tree.write(xml_path, encoding="utf-8", xml_declaration=True)
                logger.info(f"XML-файл сохранён: {xml_path}")
            except Exception as e:
                logger.error(f"Ошибка генерации XML: {e}")
                xml_path = None

        return JsonResponse({
            'status': 'ok',
            'message': 'Кассир зарегистрирован',
            'cashier_id': cashier_id,
            'password': password_plain,
            'ukm_version': ukm_version,
            'xml_path': xml_path
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

