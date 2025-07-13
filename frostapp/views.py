import hashlib
import json
import os
import logging
import random
import string
import datetime
import pymysql
import xml.etree.ElementTree as ET
import cx_Oracle
from django.http import JsonResponse
from django.db import transaction
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone


from .models import Queue, MODUL_logs, User, UKMUser, OpenInSystem, QRCode, Department, Position 

_HEX = set("0123456789abcdefABCDEF")

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

def generate_qr_string(inn: str, salt: str = "INDIVIDUAL_SALT") -> str:
    hash_inn = hashlib.sha256(inn.encode('utf-8')).hexdigest()
    expiration = (datetime.datetime.utcnow() + datetime.timedelta(days=1)).strftime('%Y%m%d')
    return f"KS{hash_inn}{expiration}{salt}"
    
def connect_oracle():
    dsn = cx_Oracle.makedsn("192.168.17.239", 1521, service_name="xe")
    return cx_Oracle.connect(user="supermag_user", password="supermag_pass", dsn=dsn, encoding="UTF-8")

def is_ukm5_store(storeid):
    try:
        # Прямое подключение к Oracle по IP, порту и имени сервиса
        dsn = cx_Oracle.makedsn("192.168.17.239", 1521, service_name="BINUU00")
        connection = cx_Oracle.connect("supermag", "qqq", dsn=dsn)
        cursor = connection.cursor()

        cursor.execute("""
            SELECT T1.ID as SMSTORE,
                   T2.PROPVAL as CLOSEDATE,
                   T3.PROPVAL as UKM4STORE,
                   T4.PROPVAL as UKM5STORE
            FROM SMSTORELOCATIONS T1,
                 (SELECT * FROM SMSTOREPROPERTIES WHERE PROPID = 'REP.CLOSEDATE') T2,
                 (SELECT STORELOC, PROPVAL FROM SMSTOREPROPERTIES WHERE PROPID = 'REP.UKMStoreId') T3,
                 (SELECT STORELOC, PROPVAL FROM SMSTOREPROPERTIES WHERE PROPID = 'REP.UKMSERVER5') T4
            WHERE T1.ID = T2.STORELOC(+)
              AND T1.ID = T3.STORELOC(+)
              AND T1.ID = T4.STORELOC(+)
              AND T1.ID = :storeid
        """, storeid=int(storeid))

        row = cursor.fetchone()
        cursor.close()
        connection.close()

        if row and row[3]:  # UKM5STORE
            logger.info(f"[Oracle] Магазин {storeid} определён как UKM5")
            return True
        else:
            logger.info(f"[Oracle] Магазин {storeid} определён как UKM4")
            return False

    except Exception as e:
        logger.error(f"Ошибка при проверке UKM5 через Oracle: {e}")
        return False








def encrypt_inn_full(inn: str) -> str:
    return get_inn_hash(inn)

def encrypt_inn20(inn: str) -> str:
    return encrypt_inn_full(inn)[:20]

def build_user_password(hash20: str) -> str:
    date_part = datetime.datetime.utcnow().strftime("%Y%m%d")
    base      = f"KS{hash20}{date_part}"
    max_len   = 40
    salt_len  = max_len - len(base)
    salt      = ''.join(random.choices(string.ascii_uppercase + string.digits, k=salt_len))
    return base + salt










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

        inn = data['inn']
        fio = f"{data['surname']} {data['name']} {data['patronymic']}"
        mail = data['mail']
        phone = data['phone']
        dep_name = data['department']
        pos_name = data['position']
        role_id = data['roleId']

        # Разбор storeid: может быть одно значение или несколько через запятую
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
            inn_hash_full = get_inn_hash(inn)
            inn_hash_20 = inn_hash_full[:20]
            password_plain = build_user_password(inn_hash_20)
            qr_string = password_plain

            existing_user = User.objects.filter(encrypted_inn=inn_hash_full, full_name=fio).first()
            if not existing_user:
                new_user = User.objects.create(
                    encrypted_inn=inn_hash_full,
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

            ukm_conn = connect_ukm()
            ukm_cursor = ukm_conn.cursor()
            ukm_cursor.execute("SELECT MAX(id) + 1 AS next_id FROM trm_in_users")
            cashier_id_base = ukm_cursor.fetchone()['next_id'] or 1
            ukm_conn.close()

            converter = connect_converter()
            conv_cursor = converter.cursor()
            conv_cursor.execute("SELECT COUNT(*) AS cnt FROM `signal` WHERE `signal` = 'busy'")
            base_version = (conv_cursor.fetchone()['cnt'] or 0) + 1

            cashier_counter = 0
            xml_paths = []

            for idx, sid in enumerate(store_ids):
                if sid in existing_storeids:
                    logger.info(f"[Пропуск] Пользователь уже зарегистрирован в storeid={sid}")
                    continue

                UKMUser.objects.create(
                    user=new_user,
                    roleid=role_id,
                    storeid=sid,
                    version=1
                )

                cashier_id = cashier_id_base + cashier_counter
                version = base_version + cashier_counter

                conv_cursor.execute("""
                    INSERT INTO users (store, id, name, inn, password, role_id, version, deleted)
                    VALUES (%s, %s, %s, %s, OLD_PASSWORD(%s), %s, %s, 0)
                """, (sid, cashier_id, fio, inn_hash_20, password_plain, role_id, version))

                conv_cursor.execute("INSERT INTO `signal`(`signal`, version) VALUES ('incr', %s)", (version,))
                cashier_counter += 1

                ukm_version = "UKM5" if is_ukm5_store(sid) else "UKM4"
                logger.info(f"Магазин {sid} определён как {ukm_version}")

                if ukm_version == "UKM5":
                    xml_filename = f"StoreCashiers_{sid}_F.xml"
                    xml_path = os.path.join(xml_dir, xml_filename)
                    try:
                        if os.path.exists(xml_path):
                            tree = ET.parse(xml_path)
                            root = tree.getroot()
                        else:
                            root = ET.Element("StoreCashiers")
                            tree = ET.ElementTree(root)
                        cashier_el = ET.SubElement(root, "Cashier")
                        ET.SubElement(cashier_el, "Id").text = str(cashier_id)
                        ET.SubElement(cashier_el, "Name").text = fio
                        ET.SubElement(cashier_el, "INN").text = inn
                        ET.SubElement(cashier_el, "Password").text = password_plain
                        ET.SubElement(cashier_el, "RoleId").text = str(role_id)
                        ET.SubElement(cashier_el, "Deleted").text = "0"

                        tree.write(xml_path, encoding="utf-8", xml_declaration=True)
                        xml_paths.append(xml_path)
                        logger.info(f"XML-файл обновлён: {xml_path}")
                    except Exception as e:
                        logger.error(f"Ошибка генерации XML для storeid={sid}: {e}")

            converter.commit()
            converter.close()

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








def regenerate_qr(user, inn_hash_20):
    new_password = build_user_password(inn_hash_20)
    now = timezone.now()
    expiration = now + datetime.timedelta(days=1)

    # Обновляем QR-код и OpenInSystem
    QRCode.objects.filter(user=user).delete()
    QRCode.objects.create(user=user, qr_data=new_password, created_at=now, expires_at=expiration)
    OpenInSystem.objects.filter(user_id=user.id, system_id=9).update(password=new_password)

    # Подключение к MySQL
    converter = connect_converter()
    conv_cursor = converter.cursor()

    # Получаем базовый ID для вставки
    conv_cursor.execute("SELECT MAX(id) + 1 AS next_id FROM users")
    base_id = conv_cursor.fetchone()['next_id'] or 1
    current_id = base_id

    ukm_users = UKMUser.objects.filter(user_id=user.id)

    for ukm_user in ukm_users:
        sid = ukm_user.storeid

        conv_cursor.execute("""
            INSERT INTO users (store, id, name, inn, password, role_id, deleted)
            VALUES (%s, %s, %s, %s, OLD_PASSWORD(%s), %s, 0)
        """, (
            sid,
            current_id,
            user.full_name,
            inn_hash_20,
            new_password,
            ukm_user.roleid
        ))

        current_id += 1  # Инкремент ID для следующего магазина

    converter.commit()
    converter.close()

    # Обновляем XML (только для UKM5)
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    xml_dir = os.path.join(base_dir, 'xml')
    os.makedirs(xml_dir, exist_ok=True)

    for ukm_user in ukm_users:
        sid = ukm_user.storeid
        if is_ukm5_store(sid):
            xml_filename = f"StoreCashiers_{sid}_F.xml"
            xml_path = os.path.join(xml_dir, xml_filename)

            try:
                if os.path.exists(xml_path):
                    tree = ET.parse(xml_path)
                    root = tree.getroot()
                else:
                    root = ET.Element("StoreCashiers")
                    tree = ET.ElementTree(root)

                # Удаляем старую запись по INN
                for el in root.findall("Cashier"):
                    if el.find("INN") is not None and el.find("INN").text == user.encrypted_inn:
                        root.remove(el)

                # Добавляем новую запись
                cashier_el = ET.SubElement(root, "Cashier")
                ET.SubElement(cashier_el, "Id").text = str(current_id)  # Новый ID
                ET.SubElement(cashier_el, "Name").text = user.full_name
                ET.SubElement(cashier_el, "INN").text = user.encrypted_inn
                ET.SubElement(cashier_el, "Password").text = new_password
                ET.SubElement(cashier_el, "RoleId").text = str(ukm_user.roleid)
                ET.SubElement(cashier_el, "Deleted").text = "0"

                tree.write(xml_path, encoding="utf-8", xml_declaration=True)
                logger.info(f"XML обновлён при регенерации QR: {xml_path}")
            except Exception as e:
                logger.error(f"Ошибка обновления XML для {sid}: {e}")




















@csrf_exempt
def get_qr_code_by_tg(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)

    try:
        data = json.loads(request.body)
        tg_id = data.get('tg_id')

        if not tg_id:
            logger.error("Не указан tg_id")
            return JsonResponse({'status': 'error', 'message': 'Не указан tg_id'}, status=400)

        user = User.objects.filter(tg_id=str(tg_id)).first()
        if not user:
            return JsonResponse({'status': 'error', 'message': 'Пользователь не найден'}, status=404)

        # Перегенерировать QR в любом случае
        inn_hash_20 = user.encrypted_inn[:20]
        regenerate_qr(user, inn_hash_20)

        # Получить новый QR-код и вернуть
        new_qr = QRCode.objects.filter(user=user).order_by('-created_at').first()
        return JsonResponse({'status': 'ok', 'qr_data': new_qr.qr_data})

    except Exception as e:
        logger.exception("Ошибка при получении QR-кода")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
