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
        host="192.168.17.237",
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

def build_user_password(plain_inn: str) -> str:
    """
    Формирует пароль вида:

        KS<ИНН><YYYYMMDD><RANDOM>

    * KS        – постоянный префикс
    * ИНН       – 10 / 12 цифр, БЕЗ хэша
    * YYYYMMDD  – дата UTC
    * RANDOM    – заглавные A-Z + цифры, чтобы итог всегда был 40 симв.
    """
    date_part = datetime.datetime.utcnow().strftime("%Y%m%d")      # 8
    base      = f"KS{plain_inn}{date_part}"                       # 2+len(INN)+8
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
            
            password_plain = build_user_password(inn)
            qr_string = password_plain

            existing_user = User.objects.filter(encrypted_inn=inn, full_name=fio).first()
            if not existing_user:
                new_user = User.objects.create(
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

            ukm_conn = connect_ukm()
            ukm_cursor = ukm_conn.cursor()
            ukm_cursor.execute("SELECT MAX(id) + 1 AS next_id FROM trm_in_users")
            cashier_id_base = ukm_cursor.fetchone()['next_id'] or 1
            ukm_conn.close()

            converter = connect_converter()
            conv_cursor = converter.cursor()
            conv_cursor.execute("SELECT COUNT(*) AS cnt FROM `signal` WHERE `signal` = 'busy'")
            base_version = (conv_cursor.fetchone()['cnt'] or 0) + 1
            inserted_any_signal = False

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
                """, (sid, cashier_id, fio, inn, mysql_pwd(password_plain), role_id, version))

                conv_cursor.execute("INSERT INTO `signal`(`signal`, version) VALUES ('incr', %s)", (version,))
                inserted_any_signal = True
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

            if not inserted_any_signal:        
                conv_cursor.execute(
                    "INSERT INTO `signal`(`signal`, version) VALUES ('incr', %s)",
                    (base_version,)
                )

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








def regenerate_qr(user):
    """
    Перегенерирует пароль/QR-код сотрудника, добавляет его во все связанные
    магазины в import4staffbonus.users и обязательно пишет строки в signal.
    """
    
    new_password = build_user_password(user.encrypted_inn)
    now          = timezone.now()
    expiration   = now + datetime.timedelta(days=1)

    # 1) PostgreSQL: QR-код + open_in_system
    QRCode.objects.filter(user=user).delete()
    QRCode.objects.create(user=user,
                          qr_data=new_password,
                          created_at=now,
                          expires_at=expiration)
    OpenInSystem.objects.filter(user_id=user.id,
                                system_id=9).update(password=new_password)

    # 2) MySQL: users + signal
    converter   = connect_converter()
    conv_cursor = converter.cursor()

    # версия для signal
    conv_cursor.execute("SELECT COUNT(*) AS cnt FROM `signal` WHERE `signal`='busy'")
    base_version = (conv_cursor.fetchone()['cnt'] or 0) + 1

    # базовый id берём из ukmserver.trm_in_users
    ukm_conn   = connect_ukm()
    ukm_cursor = ukm_conn.cursor()
    ukm_cursor.execute("SELECT MAX(id)+1 AS next_id FROM trm_in_users")
    cashier_id_base = ukm_cursor.fetchone()['next_id'] or 1
    ukm_conn.close()
    ukm_emp_id = get_trm_employee_id(user.encrypted_inn, user.full_name)
    cashier_counter      = 0       # для сдвига id / версии
    inserted_any_signal  = False
    ukm_users            = list(UKMUser.objects.filter(user_id=user.id))
    plspls = mysql_pwd(new_password)
    logger.info(f"Пароль {new_password} переведен в {plspls}")
    logger.info(f"Пользователи {ukm_users}")

    for ukm_user in ukm_users:
        sid         = ukm_user.storeid
        if ukm_emp_id:               
            cashier_id = ukm_emp_id
        else:                    
            cashier_id = cashier_id_base + cashier_counter
            cashier_counter += 1
        version     = base_version    + cashier_counter

        conv_cursor.execute("""
            INSERT INTO users (store, id, name, inn, password,
                               role_id, version, deleted)
            VALUES (%s, %s, %s, %s, OLD_PASSWORD(%s), %s, %s, 0)
        """, (
            sid,
            cashier_id,
            user.full_name,
            user.encrypted_inn,
            plspls,
            ukm_user.roleid,
            version
        ))

        conv_cursor.execute(
            "INSERT INTO `signal`(`signal`, version) VALUES ('incr', %s)",
            (version,)
        )
        inserted_any_signal = True
        cashier_counter    += 1
        logger.info(f"Вставка прошла для пользователя с id {cashier_id}, Магазин {sid}, Пользователь { user.full_name}, Пароль {plspls}")

    # если ничего не вставили (маловероятно) — всё-таки дёрнем сигнал
    if not inserted_any_signal:
        logger.info(f"Вставлять нечего")
        conv_cursor.execute(
            "INSERT INTO `signal`(`signal`, version) VALUES ('incr', %s)",
            (base_version,)
        )

    converter.commit()
    converter.close()

    # 3) XML для UKM5
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    xml_dir  = os.path.join(base_dir, 'xml')
    os.makedirs(xml_dir, exist_ok=True)

    # продолжаем счётчик ID, чтобы он не конфликтовал в одном файле
    next_free_id = cashier_id_base + cashier_counter

    for ukm_user in ukm_users:
        sid = ukm_user.storeid
        if not is_ukm5_store(sid):
            continue

        xml_filename = f"StoreCashiers_{sid}_F.xml"
        xml_path     = os.path.join(xml_dir, xml_filename)

        try:
            if os.path.exists(xml_path):
                tree = ET.parse(xml_path)
                root = tree.getroot()
            else:
                root = ET.Element("StoreCashiers")
                tree = ET.ElementTree(root)

            # удалить старую запись по INN
            for el in root.findall("Cashier"):
                if el.findtext("INN") == user.encrypted_inn:
                    root.remove(el)

            # новая запись
            c_el = ET.SubElement(root, "Cashier")
            ET.SubElement(c_el, "Id").text       = str(next_free_id)
            ET.SubElement(c_el, "Name").text     = user.full_name
            ET.SubElement(c_el, "INN").text      = user.encrypted_inn
            ET.SubElement(c_el, "Password").text = new_password
            ET.SubElement(c_el, "RoleId").text   = str(ukm_user.roleid)
            ET.SubElement(c_el, "Deleted").text  = "0"

            tree.write(xml_path, encoding="utf-8", xml_declaration=True)
            logger.info(f"XML обновлён при регенерации QR: {xml_path}")
            next_free_id += 1
        except Exception as exc:
            logger.error(f"Ошибка обновления XML для {sid}: {exc}")


@csrf_exempt
def get_qr_code_by_tg(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)

    try:
        data  = json.loads(request.body)
        tg_id = data.get('tg_id')

        if not tg_id:
            return JsonResponse({'status': 'error', 'message': 'Не указан tg_id'}, status=400)

        user = User.objects.filter(tg_id=str(tg_id)).first()
        if not user:
            return JsonResponse({'status': 'error', 'message': 'Пользователь не найден'}, status=404)

        # всегда генерируем новый QR
        inn_hash_20 = user.encrypted_inn[:20]
        regenerate_qr(user)

        new_qr = QRCode.objects.filter(user=user).order_by('-created_at').first()
        return JsonResponse({'status': 'ok', 'qr_data': new_qr.qr_data})

    except Exception as e:
        logger.exception("Ошибка при получении QR-кода")
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)






@csrf_exempt
def update_cashier(request):
    """
    body = {
        "inn"     : "1177228877" | "sha256hash",
        "fio"     : "Фамилия Имя Отчество",
        "storeid" : "1001" | "1001,1002"
    }

    • ищем пользователя по ИНН+ФИО;
    • для каждого нового storeid:
        – добавляем строку в ukm_users (PostgreSQL)
        – вставляем строку в import4staffbonus.users (MySQL) с тем-же паролем
        – вставляем строку в signal ('incr', version)
        – при UKM-5 обновляем XML-файл StoreCashiers_{sid}_F.xml
    • пароль не меняем — берём из open_in_system (system_id = 9)
    """
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)

    try:
        data = json.loads(request.body)

        plain_inn = data.get('inn').strip()
        fio      = data.get('fio')
        storeids = data.get('storeid')

        if not (plain_inn and fio and storeids):
            return JsonResponse({'status': 'error',
                                 'message': 'inn, fio и storeid обязательны'},
                                status=400)

        # ── подготовка данных ────────────────────────────────────────────────
        # encrypted_inn = get_inn_hash(inn_raw)
        store_ids     = [int(s.strip()) for s in str(storeids).split(',') if s.strip().isdigit()]
        if not store_ids:
            return JsonResponse({'status': 'error', 'message': 'Некорректный storeid'}, status=400)

        # ── ищем пользователя в PostgreSQL ──────────────────────────────────
        user = User.objects.filter(encrypted_inn=plain_inn,
                                   full_name=fio).first()
        if not user:
            return JsonResponse({'status': 'error', 'message': 'Пользователь не найден'}, status=404)

        # пароль берём из open_in_system (system_id = 9)
        open_rec = OpenInSystem.objects.filter(user_id=user.id,
                                               system_id=9).first()
        if not open_rec:
            return JsonResponse({'status': 'error',
                                 'message': 'Пароль для пользователя не найден'}, status=500)

        password_plain = open_rec.password

        ukm_emp_id = get_trm_employee_id(plain_inn, fio)

        # уже имеющиеся магазины
        existing_storeids = set(UKMUser.objects.filter(user_id=user.id)
                                              .values_list('storeid', flat=True))

        # ── подготовка MySQL счётчиков (id / version) ───────────────────────
        ukm_conn   = connect_ukm()
        ukm_cursor = ukm_conn.cursor()
        ukm_cursor.execute("SELECT MAX(id)+1 AS next_id FROM trm_in_users")
        cashier_id_base = ukm_cursor.fetchone()['next_id'] or 1
        ukm_conn.close()

        converter   = connect_converter()
        conv_cursor = converter.cursor()
        conv_cursor.execute("SELECT COUNT(*) AS cnt FROM `signal` WHERE `signal`='busy'")
        base_version = (conv_cursor.fetchone()['cnt'] or 0) + 1

        # счётчики
        cashier_counter     = 0
        inserted_signals    = False
        added_storeids      = []

        # директория для XML UKM-5
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        xml_dir  = os.path.join(base_dir, 'xml')
        os.makedirs(xml_dir, exist_ok=True)

        # ── основной цикл по магазинам ───────────────────────────────────────
        for sid in store_ids:
            if sid in existing_storeids:
                logger.info(f"[Пропуск] Доступ уже есть: storeid={sid}")
                continue

            # 1. ukm_users (PostgreSQL)
            UKMUser.objects.create(
                user     = user,
                roleid   = 1,          # ← при желании берите из запроса
                storeid  = sid,
                version  = 1
            )

            # 2. import4staffbonus.users (MySQL)
            if ukm_emp_id:                
                cashier_id = ukm_emp_id
            else:                        
                cashier_id = cashier_id_base + cashier_counter
                cashier_counter += 1      
        
            version    = base_version    + cashier_counter

            conv_cursor.execute("""
                INSERT INTO users (store, id, name, inn, password,
                                   role_id, version, deleted)
                VALUES (%s, %s, %s, %s, OLD_PASSWORD(%s), %s, %s, 0)
            """, (
                sid,
                cashier_id,
                fio,
                plain_inn,
                mysql_pwd(password_plain),
                1,          # role_id по умолчанию
                version
            ))

            # 3. signal
            conv_cursor.execute(
                "INSERT INTO `signal`(`signal`, version) VALUES ('incr', %s)",
                (version,)
            )
            inserted_signals = True
            cashier_counter += 1
            added_storeids.append(sid)

            # 4. XML для UKM-5
            if is_ukm5_store(sid):
                xml_filename = f"StoreCashiers_{sid}_F.xml"
                xml_path     = os.path.join(xml_dir, xml_filename)

                try:
                    if os.path.exists(xml_path):
                        tree = ET.parse(xml_path)
                        root = tree.getroot()
                    else:
                        root = ET.Element("StoreCashiers")
                        tree = ET.ElementTree(root)

                    c_el = ET.SubElement(root, "Cashier")
                    ET.SubElement(c_el, "Id").text       = str(cashier_id)
                    ET.SubElement(c_el, "Name").text     = fio
                    ET.SubElement(c_el, "INN").text      = plain_inn
                    ET.SubElement(c_el, "Password").text = password_plain
                    ET.SubElement(c_el, "RoleId").text   = "1"
                    ET.SubElement(c_el, "Deleted").text  = "0"

                    tree.write(xml_path, encoding="utf-8", xml_declaration=True)
                    logger.info(f"[XML] Обновлён файл {xml_path}")
                except Exception as exc:
                    logger.error(f"[XML] Ошибка для {sid}: {exc}")

        # если ни одной новой записи – ответим, что всё уже было
        if not added_storeids:
            return JsonResponse({'status': 'ok',
                                 'message': 'У пользователя уже был доступ ко всем указанным магазинам'})

        # safety-signal, если по какой-то причине не вставили ни одной строки
        if not inserted_signals:
            conv_cursor.execute(
                "INSERT INTO `signal`(`signal`, version) VALUES ('incr', %s)",
                (base_version,)
            )

        converter.commit()
        converter.close()

        return JsonResponse({'status'  : 'ok',
                             'message' : 'Доступ открыт',
                             'added'   : added_storeids})

    except Exception as exc:
        logger.exception("open_access_cashier error")
        return JsonResponse({'status': 'error', 'message': str(exc)}, status=500)






@csrf_exempt
def delete_cashier(request):
    """
    Закрывает доступ кассира (inn+fio) к указанным магазинам.

    JSON-вход:
        {
          "inn"     : "<цифры или 64-симв. SHA256>",
          "fio"     : "Фамилия Имя Отчество",
          "storeid" : "1001,1002"
        }

    Шаги:
      1) ищем пользователя в PostgreSQL.users;
      2) получаем список ukm_users на удаление (не трогаем пароль/QR);
      3) В MySQL.import4staffbonus.users -> INSERT   deleted = 1   +   signal;
      4) UKM5 XML: <Deleted>1;
      5) удаляем строки из ukm_users.
    """
    if request.method != 'POST':
        return JsonResponse({"status": "error",
                             "message": "Только POST"}, status=405)

    try:
        data = json.loads(request.body)

        inn_raw   = data.get("inn")
        fio       = data.get("fio")
        store_raw = str(data.get("storeid", "")).strip()

        if not (inn_raw and fio and store_raw):
            return JsonResponse({"status": "error",
                                 "message": "inn, fio и storeid обязательны"}, status=400)

        # storeid -> список int
        store_ids = [int(s) for s in store_raw.split(',') if s.strip().isdigit()]
        if not store_ids:
            return JsonResponse({"status": "error",
                                 "message": "Некорректный storeid"}, status=400)

        # поддерживаем и цифры, и уже-захэш. ИНН
        try:
            inn_hash_full = get_inn_hash(inn_raw)
        except ValueError as err:
            return JsonResponse({"status": "error", "message": str(err)}, status=400)

        inn_hash_20 = inn_hash_full[:20]

        # --- PostgreSQL: пользователь и доступы ----------------------------
        user = User.objects.filter(encrypted_inn=inn_raw,
                                   full_name=fio).first()
        if not user:
            return JsonResponse({"status": "error",
                                 "message": "Пользователь не найден"}, status=404)

        # все storeid, которые реально у пользователя
        ukm_to_remove = list(
            UKMUser.objects.filter(user=user, storeid__in=store_ids)
        )
        if not ukm_to_remove:
            return JsonResponse({"status": "ok",
                                 "message": "У пользователя уже нет доступа к указанным магазинам",
                                 "removed": []})

        # текущий пароль (оставим неизменным для записи-заглушки)
        current_password = (OpenInSystem.objects
                            .filter(user_id=user.id, system_id=9)
                            .values_list("password", flat=True)
                            .first()) or build_user_password(inn_hash_20)

        ukm_emp_id = get_trm_employee_id(inn_raw, fio)

        # --- MySQL: INSERT deleted=1 + signal ------------------------------
        converter   = connect_converter()
        conv_cursor = converter.cursor()

        # базовая версия для signal
        conv_cursor.execute("SELECT COUNT(*) AS cnt FROM `signal` WHERE `signal`='busy'")
        base_version = (conv_cursor.fetchone()['cnt'] or 0) + 1

        # берём уникальный ID кассира (как register_cashier)
        ukm_conn   = connect_ukm()
        ukm_cursor = ukm_conn.cursor()
        ukm_cursor.execute("SELECT MAX(id)+1 AS next_id FROM trm_in_users")
        cashier_id_base = ukm_cursor.fetchone()['next_id'] or 1
        ukm_conn.close()

        for idx, ukm_user in enumerate(ukm_to_remove):
            sid        = ukm_user.storeid
            cashier_id = ukm_emp_id if ukm_emp_id else (cashier_id_base + idx)
            version    = base_version   + idx

            # вставка-заглушка
            conv_cursor.execute("""
                INSERT INTO users (store,id,name,inn,password,
                                   role_id,version,deleted)
                VALUES (%s,%s,%s,%s,OLD_PASSWORD(%s),%s,%s,1)
            """, (sid, cashier_id, fio, inn_raw,
                  current_password, ukm_user.roleid, version))

            conv_cursor.execute(
                "INSERT INTO `signal`(`signal`,version) VALUES ('incr',%s)",
                (version,))

        converter.commit()
        converter.close()

        # --- UKM5 XML ------------------------------------------------------
        base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        xml_dir  = os.path.join(base_dir, 'xml')
        os.makedirs(xml_dir, exist_ok=True)

        for ukm_user in ukm_to_remove:
            sid = ukm_user.storeid
            if not is_ukm5_store(sid):
                continue

            xml_path = os.path.join(xml_dir, f"StoreCashiers_{sid}_F.xml")
            if not os.path.exists(xml_path):
                continue

            try:
                tree = ET.parse(xml_path)
                root = tree.getroot()
                changed = False
                for cash_el in root.findall("Cashier"):
                    if cash_el.findtext("INN") == inn_raw:
                        cash_el.find("Deleted").text = "1"
                        changed = True
                if changed:
                    tree.write(xml_path, encoding="utf-8", xml_declaration=True)
                    logger.info(f"XML помечен Deleted=1: {xml_path}")
            except Exception as exc:
                logger.error(f"Ошибка XML для магазина {sid}: {exc}")

        # --- теперь убираем доступ в PostgreSQL ----------------------------
        UKMUser.objects.filter(id__in=[u.id for u in ukm_to_remove]).delete()

        return JsonResponse({"status": "ok",
                             "message": "Доступ закрыт",
                             "removed": [u.storeid for u in ukm_to_remove]})

    except Exception as exc:
        logger.exception("delete_cashier error")
        return JsonResponse({"status": "error", "message": str(exc)}, status=500)
