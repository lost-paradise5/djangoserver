import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import unicodedata
import uuid
from datetime import timedelta
from functools import wraps

import requests
from django.db import connection, transaction
from django.http import JsonResponse
from django.middleware.csrf import rotate_token
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac

from frostapp.models import (
    UkmRotationBitrixSession,
    UkmRotationAuditLog,
)


logger = logging.getLogger("ukm_rotation_auth")

# Ограничение задано непосредственно в серверном коде.
ALLOWED_BITRIX_IDS = frozenset({1, 61518})

PIN_TTL_SECONDS = 300
LOGIN_TTL_SECONDS = 8 * 60 * 60
MAX_PIN_ATTEMPTS = 5

BITRIX_USER_GET_URL = os.getenv(
    "BITRIX_USER_GET_URL",
    "https://gkbin.bitrix24.ru/rest/61518/0ogeiqf5gdy3dot0/user.get.json",
)

BITRIX_NOTIFY_URL = os.getenv(
    "BITRIX_NOTIFY_URL",
    "https://gkbin.bitrix24.ru/rest/61518/1ky2jzwneefj1aor/im.notify.personal.add.json",
)

GRANT_KEY = "ukm_rotation_bitrix_grant"
PENDING_KEY = "ukm_rotation_bitrix_pending"
NONCE_KEY = "ukm_rotation_bitrix_nonce"

# По умолчанию используется REMOTE_ADDR.
# X-Real-IP принимается только от явно разрешённого прокси.
TRUSTED_PROXY_IPS = frozenset(
    value.strip()
    for value in os.getenv(
        "UKM_ROTATION_TRUSTED_PROXY_IPS",
        "",
    ).split(",")
    if value.strip()
)


class RotationAuthError(Exception):
    pass


def normalize_rotation_fio(value):
    value = unicodedata.normalize(
        "NFKC",
        str(value or ""),
    )
    return (
        " ".join(value.split())
        .casefold()
        .replace("ё", "е")
    )


def rotation_client_ip(request):
    remote = str(
        request.META.get("REMOTE_ADDR") or ""
    )
    candidate = remote

    if remote in TRUSTED_PROXY_IPS:
        candidate = str(
            request.META.get("HTTP_X_REAL_IP")
            or remote
        )

    try:
        return str(
            ipaddress.ip_address(candidate.strip())
        )
    except ValueError:
        return "unknown"


def _digest(purpose, value):
    return salted_hmac(
        "ukm.rotation." + purpose,
        str(value),
        algorithm="sha256",
    ).hexdigest()


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _row_identity(row):
    return {
        "type": "bitrix",
        "bitrix_user_id": int(row.bitrix_user_id),
        "fio": row.fio,
        "authenticated_at": (
            row.authenticated_at.isoformat()
            if row.authenticated_at
            else None
        ),
    }


def rotation_actor_label(identity):
    identity = identity or {}

    if identity.get("bitrix_user_id"):
        return (
            f"{identity.get('fio') or 'Пользователь'} "
            f"(Битрикс ID {identity['bitrix_user_id']})"
        )

    return str(
        identity.get("fio")
        or "Автоматический запуск / CLI"
    )


def audit_rotation(
    event,
    *,
    request=None,
    identity=None,
    run_id=None,
    details=None,
):
    identity = identity or {}

    UkmRotationAuditLog.objects.create(
        event=str(event)[:64],
        bitrix_user_id=identity.get(
            "bitrix_user_id"
        ),
        fio=str(
            identity.get("fio") or ""
        )[:255],
        ip_address=(
            rotation_client_ip(request)
            if request is not None
            else str(
                identity.get("ip") or ""
            )[:64]
        ),
        path=(
            request.path[:512]
            if request is not None
            else ""
        ),
        method=(
            request.method[:16]
            if request is not None
            else "WORKER"
        ),
        run_id=run_id,
        details=json.loads(
            json.dumps(
                details or {},
                ensure_ascii=False,
                default=str,
            )
        ),
    )


def _bitrix_call(url, payload):
    try:
        response = requests.post(
            url,
            json=payload,
            timeout=(5, 15),
            allow_redirects=False,
        )
        response.raise_for_status()
        body = response.json()

    except (
        requests.RequestException,
        ValueError,
    ) as exc:
        # Не выводим URL вебхука и тело запроса.
        logger.warning(
            "Bitrix transport failure: %s",
            type(exc).__name__,
        )
        raise RotationAuthError(
            "Битрикс временно недоступен. "
            "Повторите позже."
        ) from None

    if (
        not isinstance(body, dict)
        or "error" in body
        or "error_description" in body
        or "result" not in body
    ):
        logger.warning(
            "Bitrix returned an unsuccessful REST response"
        )
        raise RotationAuthError(
            "Битрикс не выполнил запрос. "
            "Проверьте права вебхука."
        )

    return body["result"]


def _active_allowed_users():
    result = _bitrix_call(
        BITRIX_USER_GET_URL,
        {
            "FILTER": {
                "ID": sorted(ALLOWED_BITRIX_IDS),
                "ACTIVE": True,
            },
        },
    )

    if not isinstance(result, list):
        raise RotationAuthError(
            "Битрикс вернул некорректный "
            "список пользователей."
        )

    users = {}

    for row in result:
        if not isinstance(row, dict):
            continue

        try:
            user_id = int(row.get("ID"))
        except (TypeError, ValueError):
            continue

        active = (
            str(row.get("ACTIVE") or "")
            .strip()
            .lower()
            in {"true", "1", "y"}
        )

        # Дополнительная серверная проверка:
        # не полагаемся только на фильтр REST.
        if (
            user_id not in ALLOWED_BITRIX_IDS
            or not active
        ):
            continue

        fio = " ".join(
            str(row.get(key) or "").strip()
            for key in (
                "LAST_NAME",
                "NAME",
                "SECOND_NAME",
            )
            if str(row.get(key) or "").strip()
        )

        if fio and len(fio) <= 255:
            users[user_id] = {
                "bitrix_user_id": user_id,
                "fio": fio,
            }

    return users


def _advisory_lock(value):
    key = int.from_bytes(
        hashlib.sha256(
            str(value).encode("utf-8")
        ).digest()[:8],
        byteorder="big",
        signed=True,
    )

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s)",
            [key],
        )


def begin_rotation_login(request, entered_fio):
    entered_fio = str(
        entered_fio or ""
    ).strip()

    stamp = timezone.now()
    ip = rotation_client_ip(request)

    # Ограничиваем запросы, включая неправильное ФИО.
    with transaction.atomic():
        _advisory_lock(
            "ukm.rotation.pin.ip:" + ip
        )

        count = (
            UkmRotationAuditLog.objects
            .filter(
                event="pin.request",
                ip_address=ip,
                created_at__gte=(
                    stamp - timedelta(minutes=10)
                ),
            )
            .count()
        )

        if count >= 20:
            raise RotationAuthError(
                "Слишком много запросов. "
                "Повторите через 10 минут."
            )

        audit_rotation(
            "pin.request",
            request=request,
            details={
                "entered_fio": entered_fio[:255],
            },
        )

    if (
        not entered_fio
        or len(entered_fio) > 255
    ):
        raise RotationAuthError(
            "Введите полное ФИО "
            "из профиля Битрикса."
        )

    users = _active_allowed_users()

    matches = [
        user
        for user in users.values()
        if normalize_rotation_fio(
            user["fio"]
        ) == normalize_rotation_fio(
            entered_fio
        )
    ]

    if len(matches) != 1:
        audit_rotation(
            "pin.denied",
            request=request,
            details={
                "reason": (
                    "fio_not_unique_or_not_allowed"
                ),
            },
        )
        raise RotationAuthError(
            "ФИО не найдено среди пользователей "
            "с доступом или неоднозначно."
        )

    identity = matches[0]
    user_id = identity["bitrix_user_id"]

    nonce = request.session.get(NONCE_KEY)

    if (
        not isinstance(nonce, str)
        or len(nonce) != 64
    ):
        nonce = secrets.token_hex(32)
        request.session[NONCE_KEY] = nonce

    pin = (
        f"{secrets.randbelow(1_000_000):06d}"
    )
    challenge_id = uuid.uuid4()

    with transaction.atomic():
        _advisory_lock(
            f"ukm.rotation.pin.user:{user_id}"
        )

        stamp = timezone.now()

        recent = (
            UkmRotationBitrixSession.objects
            .filter(bitrix_user_id=user_id)
        )

        if recent.filter(
            created_at__gte=(
                stamp - timedelta(seconds=60)
            ),
        ).exists():
            raise RotationAuthError(
                "Повторная отправка ПИН "
                "доступна через 60 секунд."
            )

        if recent.filter(
            created_at__gte=(
                stamp - timedelta(hours=1)
            ),
        ).count() >= 10:
            raise RotationAuthError(
                "Превышен лимит отправок ПИН "
                "за час. Повторите позже."
            )

        # Новый запрос отменяет предыдущие
        # неподтверждённые ПИН этого пользователя.
        recent.filter(
            status__in=["sending", "sent"],
        ).update(
            status="revoked",
            pin_hash="",
        )

        row = (
            UkmRotationBitrixSession.objects
            .create(
                id=challenge_id,
                bitrix_user_id=user_id,
                fio=identity["fio"],
                binding_hash=_digest(
                    "browser",
                    nonce,
                ),
                pin_hash=_digest(
                    "pin",
                    f"{challenge_id}:{pin}",
                ),
                status="sending",
                expires_at=(
                    stamp
                    + timedelta(
                        seconds=PIN_TTL_SECONDS
                    )
                ),
                ip_address=ip,
            )
        )

    request.session[PENDING_KEY] = str(
        row.id
    )

    try:
        result = _bitrix_call(
            BITRIX_NOTIFY_URL,
            {
                "USER_ID": user_id,
                "MESSAGE": (
                    "Вход в систему обновления "
                    "паролей УКМ.\n"
                    f"Ваш ПИН-код: {pin}\n"
                    "Код действует 5 минут. "
                    "Никому его не сообщайте."
                ),
            },
        )

        if (
            isinstance(result, bool)
            or not str(result).isdigit()
            or int(result) <= 0
        ):
            raise RotationAuthError(
                "Битрикс не подтвердил "
                "отправку ПИН."
            )

    except RotationAuthError:
        (
            UkmRotationBitrixSession.objects
            .filter(
                id=row.id,
                status="sending",
            )
            .update(
                status="failed",
                pin_hash="",
            )
        )

        audit_rotation(
            "pin.delivery_failed",
            request=request,
            identity=identity,
        )
        raise

    updated = (
        UkmRotationBitrixSession.objects
        .filter(
            id=row.id,
            status="sending",
        )
        .update(status="sent")
    )

    if not updated:
        raise RotationAuthError(
            "ПИН уже заменён новым запросом. "
            "Запросите актуальный код."
        )

    audit_rotation(
        "pin.sent",
        request=request,
        identity=identity,
    )


def pending_rotation_login(request):
    challenge_id = _uuid(
        request.session.get(PENDING_KEY)
    )
    nonce = request.session.get(NONCE_KEY)

    if (
        challenge_id is None
        or not isinstance(nonce, str)
    ):
        return None

    return (
        UkmRotationBitrixSession.objects
        .filter(
            id=challenge_id,
            binding_hash=_digest(
                "browser",
                nonce,
            ),
            status="sent",
            expires_at__gt=timezone.now(),
            bitrix_user_id__in=(
                ALLOWED_BITRIX_IDS
            ),
        )
        .first()
    )


def complete_rotation_login(
    request,
    entered_pin,
):
    challenge_id = _uuid(
        request.session.get(PENDING_KEY)
    )
    nonce = request.session.get(NONCE_KEY)
    entered_pin = str(
        entered_pin or ""
    ).strip()

    if (
        challenge_id is None
        or not isinstance(nonce, str)
    ):
        raise RotationAuthError(
            "Сначала запросите ПИН по ФИО."
        )

    failure = None

    with transaction.atomic():
        row = (
            UkmRotationBitrixSession.objects
            .select_for_update()
            .filter(id=challenge_id)
            .first()
        )

        if (
            row is None
            or not hmac.compare_digest(
                row.binding_hash,
                _digest("browser", nonce),
            )
        ):
            failure = (
                "Запрос ПИН не найден. "
                "Запросите новый код."
            )

        elif (
            row.bitrix_user_id
            not in ALLOWED_BITRIX_IDS
            or row.status != "sent"
        ):
            failure = (
                "Этот ПИН уже использован, "
                "отменён или заблокирован."
            )

        elif row.expires_at <= timezone.now():
            row.status = "expired"
            row.pin_hash = ""
            row.save(
                update_fields=[
                    "status",
                    "pin_hash",
                ],
            )

            audit_rotation(
                "pin.expired",
                request=request,
                identity=_row_identity(row),
            )

            failure = (
                "ПИН просрочен. "
                "Запросите новый код."
            )

        elif (
            not re.fullmatch(
                r"[0-9]{6}",
                entered_pin,
            )
            or not hmac.compare_digest(
                row.pin_hash,
                _digest(
                    "pin",
                    f"{row.id}:{entered_pin}",
                ),
            )
        ):
            row.attempts += 1

            if row.attempts >= MAX_PIN_ATTEMPTS:
                row.status = "blocked"
                row.pin_hash = ""

            row.save(
                update_fields=[
                    "attempts",
                    "status",
                    "pin_hash",
                ],
            )

            audit_rotation(
                (
                    "pin.blocked"
                    if row.status == "blocked"
                    else "pin.invalid"
                ),
                request=request,
                identity=_row_identity(row),
                details={
                    "attempts": row.attempts,
                },
            )

            failure = (
                "ПИН заблокирован после "
                "5 неверных попыток."
                if row.status == "blocked"
                else (
                    "Неверный ПИН. "
                    "Осталось попыток: "
                    f"{MAX_PIN_ATTEMPTS - row.attempts}."
                )
            )

        else:
            # Повторно проверяем активность
            # пользователя перед выдачей доступа.
            try:
                current = (
                    _active_allowed_users()
                    .get(int(row.bitrix_user_id))
                )

            except RotationAuthError as exc:
                failure = str(exc)

                audit_rotation(
                    "login.unavailable",
                    request=request,
                    identity=_row_identity(row),
                )

            else:
                if current is None:
                    row.status = "revoked"
                    row.pin_hash = ""
                    row.save(
                        update_fields=[
                            "status",
                            "pin_hash",
                        ],
                    )

                    audit_rotation(
                        "login.denied",
                        request=request,
                        identity=_row_identity(row),
                    )

                    failure = (
                        "Пользователь Битрикса "
                        "неактивен или не имеет доступа."
                    )

                else:
                    stamp = timezone.now()

                    row.fio = current["fio"]
                    row.status = "success"
                    row.pin_hash = ""
                    row.authenticated_at = stamp
                    row.auth_expires_at = (
                        stamp
                        + timedelta(
                            seconds=LOGIN_TTL_SECONDS
                        )
                    )

                    row.save(
                        update_fields=[
                            "fio",
                            "status",
                            "pin_hash",
                            "authenticated_at",
                            "auth_expires_at",
                        ],
                    )

                    audit_rotation(
                        "login.success",
                        request=request,
                        identity=_row_identity(row),
                    )

    # Исключение выбрасываем после atomic,
    # чтобы изменения счётчика и блокировка
    # не откатились.
    if failure:
        audit_rotation(
            "login.rejected",
            request=request,
            details={"reason": failure},
        )
        raise RotationAuthError(failure)

    request.session.cycle_key()

    request.session[GRANT_KEY] = {
        "id": str(row.id),
        "nonce": nonce,
    }

    request.session.pop(PENDING_KEY, None)
    request.session.pop(NONCE_KEY, None)

    rotate_token(request)


def get_rotation_identity(request):
    grant = request.session.get(GRANT_KEY)

    if not isinstance(grant, dict):
        return None

    grant_id = _uuid(grant.get("id"))
    nonce = grant.get("nonce")

    if (
        grant_id is None
        or not isinstance(nonce, str)
    ):
        return None

    row = (
        UkmRotationBitrixSession.objects
        .filter(
            id=grant_id,
            status="success",
            auth_expires_at__gt=timezone.now(),
            bitrix_user_id__in=(
                ALLOWED_BITRIX_IDS
            ),
        )
        .first()
    )

    if (
        row is None
        or not hmac.compare_digest(
            row.binding_hash,
            _digest("browser", nonce),
        )
    ):
        return None

    return _row_identity(row)


def end_rotation_login(request):
    identity = get_rotation_identity(
        request
    )
    grant = request.session.get(GRANT_KEY)

    if (
        isinstance(grant, dict)
        and _uuid(grant.get("id"))
        and isinstance(
            grant.get("nonce"),
            str,
        )
    ):
        (
            UkmRotationBitrixSession.objects
            .filter(
                id=_uuid(grant["id"]),
                binding_hash=_digest(
                    "browser",
                    grant["nonce"],
                ),
            )
            .update(
                status="revoked",
                pin_hash="",
            )
        )

    pending = pending_rotation_login(
        request
    )

    if pending:
        (
            UkmRotationBitrixSession.objects
            .filter(id=pending.id)
            .update(
                status="revoked",
                pin_hash="",
            )
        )

    audit_rotation(
        "logout",
        request=request,
        identity=identity,
    )

    for key in (
        GRANT_KEY,
        PENDING_KEY,
        NONCE_KEY,
    ):
        request.session.pop(key, None)

    request.session.cycle_key()
    rotate_token(request)


def ukm_rotation_bitrix_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        identity = get_rotation_identity(
            request
        )

        if identity is None:
            request.session.pop(
                GRANT_KEY,
                None,
            )

            audit_rotation(
                "access.denied",
                request=request,
            )

            login_url = reverse(
                "ukm_rotation_login"
            )

            if (
                view.__name__
                == "ukm_rotation_run_status"
                or request.headers.get(
                    "X-Requested-With"
                ) == "XMLHttpRequest"
            ):
                return JsonResponse(
                    {
                        "error": "auth_required",
                        "login_url": login_url,
                    },
                    status=401,
                )

            return redirect(
                "ukm_rotation_login"
            )

        request.ukm_rotation_identity = (
            identity
        )

        try:
            response = view(
                request,
                *args,
                **kwargs,
            )

        except Exception as exc:
            audit_rotation(
                "request.error",
                request=request,
                identity=identity,
                details={
                    "view": view.__name__,
                    "exception": (
                        type(exc).__name__
                    ),
                },
            )
            raise

        audit_rotation(
            "http.request",
            request=request,
            identity=identity,
            details={
                "view": view.__name__,
                "status_code": (
                    response.status_code
                ),
                "filters": {
                    key: request.GET.get(
                        key,
                        "",
                    )[:100]
                    for key in (
                        "store",
                        "status",
                        "page",
                        "refresh_stores",
                    )
                    if key in request.GET
                },
            },
        )

        return response

    return wrapped
