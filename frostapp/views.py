import hashlib
import json
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import Queue, Logs, MODUL_logs

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
    3) Если чего-то не хватает -> пишем в queue (status='failed'), а также в MODUL_logs и logs.
    4) Если всё ок -> queue (status='pending') без логирования.

    Возвращает (final_status, missing_fields).
    """
    # Из тела запроса берём только "data"
    data = payload.get('data', {})
    if not isinstance(data, dict):
        data = {}

    # По умолчанию статус 'pending'
    final_status = 'pending'
    attempts = 0
    last_attempt = None

    # Проверяем обязательные поля
    missing = []
    for field in required_fields:
        value = data.get(field)
        if not value:  # None, "", или ключ отсутствует
            missing.append(field)

    # Шифруем inn, если есть
    if 'inn' in data and data['inn']:
        try:
            data['inn'] = encrypt_inn(data['inn'])
        except ValueError:
            missing.append('inn')

    # Если поля пропущены -> статус failed
    if missing:
        final_status = 'failed'

    # Создаём запись в queue
    record = Queue.objects.create(
        data=data,
        attempts=attempts,
        status=final_status,
        last_attempt=last_attempt
    )

    # Если failed -> пишем в MODUL_logs и logs
    if final_status == 'failed':
        msg = f"Незаполненные поля: {missing}"

        # 1) Запись в MODUL_logs
        modul_data = {
            "error": msg,
            "payload": data
        }
        new_modul_log = MODUL_logs.objects.create(data=modul_data)

        # 2) Запись в logs
        Logs.objects.create(
            location='MODUL',
            modul_id=new_modul_log.id,
            interface_id=None,
            action=action_name,
            old_value=None,
            new_value=json.dumps(modul_data, ensure_ascii=False)
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
    inn, last_name, first_name, position, department
    """
    if request.method == 'POST':
        try:
            payload = json.loads(request.body.decode('utf-8'))

            required_fields = [
                'inn', 'last_name', 'first_name', 'position', 'department'
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
    inn, last_name, first_name, position, department,
    start_vacation, end_vacation, type_vacation
    """
    if request.method == 'POST':
        try:
            payload = json.loads(request.body.decode('utf-8'))

            required_fields = [
                'inn', 'last_name', 'first_name', 'position', 'department',
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

















# import hashlib
# import json
# from django.http import JsonResponse
# from django.views.decorators.csrf import csrf_exempt

# from .models import Queue, Logs, MODUL_logs

# def encrypt_inn(inn):
#     """
#     Хэшируем (SHA-256) поле inn, если это строка из цифр.
#     """
#     if not isinstance(inn, str) or not inn.isdigit():
#         raise ValueError("ИНН должен быть строкой, содержащей только цифры.")
#     hash_object = hashlib.sha256(inn.encode('utf-8'))
#     return hash_object.hexdigest()

# def validate_and_create_record(payload, required_fields, action_name="CREATE"):
#     """
#     1) Проверка обязательных полей в payload['data'].
#     2) Если поля не заполнены -> запись в queue (status='failed'),
#        затем запись в MODUL_logs и logs.
#     3) Если всё хорошо -> запись в queue со status=payload['status'] или 'pending'.

#     Возвращает (final_status, missing_fields).
#     """
#     data = payload.get('data', {})
#     if not isinstance(data, dict):
#         data = {}

#     # Системный статус для queue.status
#     status_value = payload.get('status', 'pending')
#     attempts = payload.get('attempts', 0)
#     last_attempt = payload.get('last_attempt', None)

#     # 1. Проверяем обязательные поля
#     missing = []
#     for field in required_fields:
#         value = data.get(field)
#         if not value:  # None, "", или отсутствует
#             missing.append(field)

#     # 2. Шифруем inn, если есть
#     if 'inn' in data and data['inn']:
#         try:
#             data['inn'] = encrypt_inn(data['inn'])
#         except ValueError:
#             # inn некорректен
#             missing.append('inn')

#     # 3. Определяем итоговый статус
#     final_status = status_value
#     if missing:
#         # Если есть пропущенные поля => failed
#         final_status = 'failed'

#     # 4. Создаём запись в queue
#     record = Queue.objects.create(
#         data=data,
#         attempts=attempts,
#         status=final_status,
#         last_attempt=last_attempt
#     )

#     # 5. Если статус failed -> пишем в MODUL_logs и logs
#     if final_status == 'failed':
#         msg = f"Незаполненные поля: {missing}"

#         # a) Запись в MODUL_logs
#         modul_data = {
#             "error": msg,
#             "payload": data
#         }
#         new_modul_log = MODUL_logs.objects.create(data=modul_data)

#         # b) Запись в logs
#         Logs.objects.create(
#             location='MODUL',
#             record_id=new_modul_log.id,
#             action=action_name,
#             old_value=None,
#             new_value=json.dumps(modul_data, ensure_ascii=False)
#         )

#     return final_status, missing


# @csrf_exempt
# def queue_create(request):
#     if request.method == 'POST':
#         try:
#             payload = json.loads(request.body.decode('utf-8'))

#             required_fields = [
#                 'inn', 'last_name', 'first_name', 'surename', 'employment_date',
#                 'organization', 'department', 'bitrix_department_code',
#                 'subdivision', 'position', 'mobile_phone', 'birth_date', 'status'
#             ]

#             final_status, missing = validate_and_create_record(
#                 payload, required_fields, action_name='CREATE'
#             )

#             if missing:
#                 return JsonResponse({
#                     'status': 'error',
#                     'message': f'Пропущены или пусты поля: {missing}. Запись в queue -> failed, логи созданы.'
#                 }, status=400)
#             else:
#                 return JsonResponse({'status': 'ok', 'message': 'Создано успешно'}, status=201)

#         except Exception as e:
#             return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
#     else:
#         return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)


# @csrf_exempt
# def queue_update(request):
#     if request.method == 'POST':
#         try:
#             payload = json.loads(request.body.decode('utf-8'))

#             required_fields = [
#                 'inn', 'last_name', 'first_name', 'surename', 'employment_date',
#                 'organization', 'department', 'bitrix_department_code',
#                 'subdivision', 'position', 'mobile_phone', 'birth_date', 'status'
#             ]

#             final_status, missing = validate_and_create_record(
#                 payload, required_fields, action_name='UPDATE'
#             )

#             if missing:
#                 return JsonResponse({
#                     'status': 'error',
#                     'message': f'Пропущены поля: {missing}. Запись -> failed, логи созданы.'
#                 }, status=400)
#             else:
#                 return JsonResponse({'status': 'ok', 'message': 'Изменение успешно'}, status=201)

#         except Exception as e:
#             return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
#     else:
#         return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)


# @csrf_exempt
# def queue_block(request):
#     if request.method == 'POST':
#         try:
#             payload = json.loads(request.body.decode('utf-8'))

#             required_fields = [
#                 'inn', 'last_name', 'first_name', 'surename'
#             ]

#             final_status, missing = validate_and_create_record(
#                 payload, required_fields, action_name='DELETE'
#             )

#             if missing:
#                 return JsonResponse({
#                     'status': 'error',
#                     'message': f'Пропущены поля: {missing}. Запись -> failed, логи созданы.'
#                 }, status=400)
#             else:
#                 return JsonResponse({'status': 'ok', 'message': 'Блокировка/Удаление успешно'}, status=201)

#         except Exception as e:
#             return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
#     else:
#         return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)


# @csrf_exempt
# def queue_vacation(request):
#     if request.method == 'POST':
#         try:
#             payload = json.loads(request.body.decode('utf-8'))

#             required_fields = [
#                 'inn', 'last_name', 'first_name', 'surename',
#                 'start_date', 'end_date'
#             ]

#             final_status, missing = validate_and_create_record(
#                 payload, required_fields, action_name='CREATE'
#             )

#             if missing:
#                 return JsonResponse({
#                     'status': 'error',
#                     'message': f'Пропущены поля: {missing}. Запись -> failed, логи созданы.'
#                 }, status=400)
#             else:
#                 return JsonResponse({'status': 'ok', 'message': 'Отпуск успешно'}, status=201)

#         except Exception as e:
#             return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
#     else:
#         return JsonResponse({'status': 'error', 'message': 'Только POST'}, status=405)
