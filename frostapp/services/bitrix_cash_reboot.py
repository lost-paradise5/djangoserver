import os
from typing import Optional

from django.core import signing

from frostapp.models import User


SIGNING_SALT = "frostapp.bitrix_cash_reboot.v1"


class CashRebootIdentityError(Exception):
    """Общая ошибка определения пользователя по ФИО."""


class CashRebootUserNotFound(CashRebootIdentityError):
    """Пользователь не найден."""


class CashRebootUserAmbiguous(CashRebootIdentityError):
    """По ФИО найдено несколько пользователей."""


class CashRebootSessionError(Exception):
    """Ошибка подписанного токена приложения."""


def normalize_fio(value: str) -> str:
    """
    Удаляет лишние пробелы и нормализует строку ФИО.
    Порядок слов не изменяется.
    """
    return " ".join(
        str(value or "").strip().split()
    )


def fio_key(value: str) -> str:
    """
    Ключ для сравнения ФИО без учёта регистра
    и различий Е/Ё.
    """
    return (
        normalize_fio(value)
        .casefold()
        .replace("ё", "е")
    )


def get_session_ttl_seconds() -> int:
    try:
        value = int(
            os.getenv(
                "BITRIX24_CASH_REBOOT_SESSION_TTL",
                "900",
            )
        )
    except (TypeError, ValueError):
        value = 900

    return max(60, min(value, 3600))


def find_active_user_by_fio(raw_fio: str) -> User:
    """
    Ищет единственного активного пользователя по точному ФИО.

    Поиск нечувствителен к:
      - регистру;
      - лишним пробелам;
      - различию Е/Ё.

    Если найдено несколько пользователей, никого автоматически
    не выбираем.
    """
    normalized = normalize_fio(raw_fio)

    if len(normalized) < 5:
        raise CashRebootIdentityError(
            "Введите полное ФИО сотрудника"
        )

    parts = [
        part
        for part in normalized.split(" ")
        if part
    ]

    if len(parts) < 2:
        raise CashRebootIdentityError(
            "Введите как минимум фамилию и имя"
        )

    qs = User.objects.filter(active=True)

    # Сначала уменьшаем выборку средствами PostgreSQL.
    # Каждая часть ФИО должна встречаться в full_name.
    for part in parts:
        qs = qs.filter(full_name__icontains=part)

    candidates = list(
        qs.only(
            "id",
            "full_name",
            "employee_id",
            "phone",
            "mail",
            "active",
        )[:100]
    )

    expected_key = fio_key(normalized)

    matches = [
        user
        for user in candidates
        if fio_key(user.full_name) == expected_key
    ]

    if not matches:
        raise CashRebootUserNotFound(
            "Активный сотрудник с таким ФИО не найден"
        )

    if len(matches) > 1:
        raise CashRebootUserAmbiguous(
            "В системе найдено несколько активных сотрудников "
            "с таким ФИО. Автоматически выбрать пользователя нельзя"
        )

    return matches[0]


def create_user_token(user: User) -> str:
    """
    Создаёт подписанный временный токен.

    Токен не хранится в базе данных.
    Пользователь не сможет подменить user_id без знания
    Django SECRET_KEY.
    """
    payload = {
        "version": 1,
        "user_id": int(user.id),
        "fio_key": fio_key(user.full_name),
    }

    return signing.dumps(
        payload,
        salt=SIGNING_SALT,
        compress=True,
    )


def get_user_from_token(token: Optional[str]) -> User:
    """
    Проверяет подпись и срок действия токена,
    после чего повторно загружает активного пользователя.
    """
    token = str(token or "").strip()

    if not token:
        raise CashRebootSessionError(
            "Сессия не передана. Введите ФИО повторно"
        )

    try:
        payload = signing.loads(
            token,
            salt=SIGNING_SALT,
            max_age=get_session_ttl_seconds(),
        )
    except signing.SignatureExpired as exc:
        raise CashRebootSessionError(
            "Сессия истекла. Введите ФИО повторно"
        ) from exc
    except signing.BadSignature as exc:
        raise CashRebootSessionError(
            "Некорректная сессия. Введите ФИО повторно"
        ) from exc

    if not isinstance(payload, dict):
        raise CashRebootSessionError(
            "Некорректные данные сессии"
        )

    try:
        user_id = int(payload.get("user_id"))
    except (TypeError, ValueError) as exc:
        raise CashRebootSessionError(
            "Некорректный пользователь в сессии"
        ) from exc

    user = (
        User.objects
        .filter(
            id=user_id,
            active=True,
        )
        .only(
            "id",
            "full_name",
            "employee_id",
            "phone",
            "mail",
            "tg_id",
            "max_id",
            "active",
        )
        .first()
    )

    if not user:
        raise CashRebootSessionError(
            "Пользователь больше не активен или удалён"
        )

    signed_fio_key = str(
        payload.get("fio_key") or ""
    )

    if signed_fio_key != fio_key(user.full_name):
        raise CashRebootSessionError(
            "ФИО пользователя изменилось. Введите ФИО повторно"
        )

    return user
