import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

import requests
from django.core import signing

from frostapp.models import User


logger = logging.getLogger(__name__)

# v3 намеренно делает недействительными старые токены.
# В новой схеме есть два разных типа сессии:
#   launch — Bitrix24 подтвердил вход, но сотрудник ещё не выбран;
#   user   — после ручного ввода ФИО выбран локальный User.
SIGNING_SALT = "frostapp.bitrix_cash_reboot.v3"

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


class CashRebootIdentityError(Exception):
    """Общая ошибка определения локального пользователя."""


class CashRebootUserNotFound(CashRebootIdentityError):
    """Пользователь не найден."""


class CashRebootUserAmbiguous(CashRebootIdentityError):
    """По ФИО найдено несколько пользователей."""


class CashRebootSessionError(Exception):
    """Ошибка подписанного токена приложения."""


class CashRebootBitrixAuthError(Exception):
    """Запуск не подтверждён сервером Bitrix24."""


class CashRebootBitrixUnavailable(CashRebootBitrixAuthError):
    """REST API Bitrix24 временно недоступен."""


@dataclass(frozen=True)
class BitrixLaunchIdentity:
    domain: str
    member_id: str
    bitrix_user_id: int
    full_name: str
    email: str
    app_id: int | None
    app_code: str
    app_installed: bool


@dataclass(frozen=True)
class CashRebootUserSession:
    user: User
    bitrix_identity: BitrixLaunchIdentity


def normalize_fio(value: str) -> str:
    """Удаляет лишние пробелы, не меняя порядок частей ФИО."""
    return " ".join(
        str(value or "").strip().split()
    )


def fio_key(value: str) -> str:
    """Ключ ФИО без учёта регистра и различий Е/Ё."""
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


def _get_rest_timeout_seconds() -> int:
    try:
        value = int(
            os.getenv(
                "BITRIX24_CASH_REBOOT_REST_TIMEOUT",
                "15",
            )
        )
    except (TypeError, ValueError):
        value = 15

    return max(3, min(value, 60))


def _allowed_portal_domains() -> set[str]:
    raw = (
        os.getenv("BITRIX24_CASH_REBOOT_PORTAL_DOMAINS")
        or os.getenv("BITRIX24_CASH_REBOOT_PORTAL_DOMAIN")
        or ""
    )

    return {
        item.strip().lower().rstrip(".")
        for item in raw.split(",")
        if item.strip()
    }


def _launch_value(request, name: str) -> str:
    """
    Bitrix24 передаёт часть параметров в query string,
    а AUTH_ID и остальные данные — form-urlencoded POST-телом.
    """
    return str(
        request.POST.get(name)
        or request.GET.get(name)
        or ""
    ).strip()


def _bitrix_truthy(value) -> bool:
    if isinstance(value, bool):
        return value

    return str(value or "").strip().upper() in {
        "1",
        "Y",
        "YES",
        "TRUE",
    }


def _validated_launch_domain(request) -> str:
    domain = (
        _launch_value(request, "DOMAIN")
        .lower()
        .rstrip(".")
    )

    allowed_domains = _allowed_portal_domains()

    if not allowed_domains:
        raise CashRebootBitrixAuthError(
            "На сервере не настроен разрешённый домен Bitrix24"
        )

    if (
        not domain
        or not _DOMAIN_RE.fullmatch(domain)
        or domain not in allowed_domains
    ):
        raise CashRebootBitrixAuthError(
            "Запуск выполнен не из разрешённого Bitrix24"
        )

    return domain


def _bitrix_rest_call(
    *,
    domain: str,
    method: str,
    auth_id: str,
) -> dict:
    """
    Выполняет REST-вызов с токеном пользователя.

    AUTH_ID нигде не логируется и не возвращается браузеру.
    Домен уже прошёл точную проверку по allowlist, поэтому
    пользовательский ввод не может превратить вызов в SSRF.
    """
    url = f"https://{domain}/rest/{method}.json"

    try:
        response = requests.post(
            url,
            data={"auth": auth_id},
            headers={
                "Accept": "application/json",
                "User-Agent": "frostapp-bitrix-cash-reboot/3",
            },
            timeout=(5, _get_rest_timeout_seconds()),
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise CashRebootBitrixUnavailable(
            "Bitrix24 временно недоступен. Повторите попытку позже"
        ) from exc

    if response.is_redirect or response.is_permanent_redirect:
        raise CashRebootBitrixAuthError(
            "Bitrix24 вернул недопустимое перенаправление"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise CashRebootBitrixUnavailable(
            "Bitrix24 вернул некорректный ответ"
        ) from exc

    if not isinstance(payload, dict):
        raise CashRebootBitrixUnavailable(
            "Bitrix24 вернул некорректный ответ"
        )

    error_code = str(payload.get("error") or "").strip()

    if response.status_code >= 400 or error_code:
        logger.warning(
            "[BITRIX/POS/AUTH] REST rejected method=%s "
            "domain=%s http_status=%s error=%s",
            method,
            domain,
            response.status_code,
            error_code or "http_error",
        )

        raise CashRebootBitrixAuthError(
            "Bitrix24 не подтвердил авторизацию пользователя"
        )

    result = payload.get("result")

    if not isinstance(result, dict):
        raise CashRebootBitrixUnavailable(
            "Bitrix24 не вернул данные пользователя"
        )

    return result


def verify_bitrix_launch(
    request,
    *,
    require_installed: bool,
) -> BitrixLaunchIdentity:
    """
    Проверяет, что страница действительно открыта внутри Bitrix24.

    Проверки:
      1. только POST от iframe приложения;
      2. HTTPS-контекст и PLACEMENT=DEFAULT;
      3. домен входит в серверный allowlist;
      4. member_id совпадает с настроенным, если он задан;
      5. app.info подтверждает контекст локального приложения;
      6. user.current возвращает текущего активного сотрудника.
    """
    if request.method != "POST":
        raise CashRebootBitrixAuthError(
            "Откройте приложение из меню Bitrix24"
        )

    domain = _validated_launch_domain(request)
    auth_id = _launch_value(request, "AUTH_ID")
    member_id = _launch_value(request, "member_id")
    placement = _launch_value(request, "PLACEMENT").upper()
    protocol = _launch_value(request, "PROTOCOL")
    launch_status = _launch_value(request, "status").upper()

    if len(auth_id) < 20 or len(auth_id) > 4096:
        raise CashRebootBitrixAuthError(
            "Bitrix24 не передал корректную авторизацию"
        )

    if protocol != "1":
        raise CashRebootBitrixAuthError(
            "Приложение должно открываться из Bitrix24 по HTTPS"
        )

    if placement != "DEFAULT":
        raise CashRebootBitrixAuthError(
            "Некорректный контекст запуска приложения"
        )

    # Поле status само по себе не является доказательством,
    # поэтому ниже обязательно вызывается app.info.
    if launch_status != "L":
        raise CashRebootBitrixAuthError(
            "Разрешён запуск только локального приложения Bitrix24"
        )

    expected_member_id = str(
        os.getenv(
            "BITRIX24_CASH_REBOOT_MEMBER_ID",
            "",
        )
        or ""
    ).strip()

    if expected_member_id and member_id != expected_member_id:
        raise CashRebootBitrixAuthError(
            "Идентификатор портала Bitrix24 не совпадает"
        )

    app_info = _bitrix_rest_call(
        domain=domain,
        method="app.info",
        auth_id=auth_id,
    )

    app_status = str(
        app_info.get("STATUS") or ""
    ).upper()

    if app_status != "L":
        raise CashRebootBitrixAuthError(
            "Токен не относится к локальному приложению Bitrix24"
        )

    app_installed = _bitrix_truthy(
        app_info.get("INSTALLED")
    )

    if require_installed and not app_installed:
        raise CashRebootBitrixAuthError(
            "Приложение Bitrix24 ещё не установлено"
        )

    app_id = None
    try:
        if app_info.get("ID") not in (None, ""):
            app_id = int(app_info.get("ID"))
    except (TypeError, ValueError):
        app_id = None

    app_code = str(
        app_info.get("CODE") or ""
    ).strip()

    expected_app_id_raw = str(
        os.getenv(
            "BITRIX24_CASH_REBOOT_APP_ID",
            "",
        )
        or ""
    ).strip()

    if expected_app_id_raw:
        try:
            expected_app_id = int(expected_app_id_raw)
        except (TypeError, ValueError) as exc:
            raise CashRebootBitrixAuthError(
                "На сервере некорректно настроен ID приложения Bitrix24"
            ) from exc

        if app_id != expected_app_id:
            raise CashRebootBitrixAuthError(
                "Токен относится к другому приложению Bitrix24"
            )

    expected_app_code = str(
        os.getenv(
            "BITRIX24_CASH_REBOOT_APP_CODE",
            "",
        )
        or ""
    ).strip()

    if expected_app_code and app_code != expected_app_code:
        raise CashRebootBitrixAuthError(
            "Код приложения Bitrix24 не совпадает"
        )

    current_user = _bitrix_rest_call(
        domain=domain,
        method="user.current",
        auth_id=auth_id,
    )

    if not _bitrix_truthy(
        current_user.get("ACTIVE", True)
    ):
        raise CashRebootBitrixAuthError(
            "Пользователь Bitrix24 неактивен"
        )

    try:
        bitrix_user_id = int(
            current_user.get("ID")
        )
    except (TypeError, ValueError) as exc:
        raise CashRebootBitrixAuthError(
            "Bitrix24 не вернул ID текущего пользователя"
        ) from exc

    full_name = normalize_fio(
        " ".join(
            filter(
                None,
                [
                    str(current_user.get("LAST_NAME") or "").strip(),
                    str(current_user.get("NAME") or "").strip(),
                    str(current_user.get("SECOND_NAME") or "").strip(),
                ],
            )
        )
    )

    # Общая учётная запись может называться одним словом,
    # например «Администратор». Для допуска используется ID,
    # а не имя профиля Bitrix24.
    if not full_name:
        full_name = (
            f"Пользователь Bitrix24 #{bitrix_user_id}"
        )

    return BitrixLaunchIdentity(
        domain=domain,
        member_id=member_id,
        bitrix_user_id=bitrix_user_id,
        full_name=full_name,
        email=str(
            current_user.get("EMAIL") or ""
        ).strip(),
        app_id=app_id,
        app_code=app_code,
        app_installed=app_installed,
    )


def find_active_user_by_fio(raw_fio: str) -> User:
    """
    Ищет единственного активного пользователя по точному ФИО.

    Поиск нечувствителен к регистру, лишним пробелам и Е/Ё.
    При нескольких совпадениях автоматически никого не выбирает.
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


def _identity_payload(
    bitrix_identity: BitrixLaunchIdentity,
) -> dict:
    return {
        "bitrix_user_id": int(
            bitrix_identity.bitrix_user_id
        ),
        "bitrix_full_name": normalize_fio(
            bitrix_identity.full_name
        ),
        "bitrix_email": str(
            bitrix_identity.email or ""
        ).strip(),
        "bitrix_domain": (
            bitrix_identity.domain.lower().rstrip(".")
        ),
        "member_id": str(
            bitrix_identity.member_id or ""
        ).strip(),
        "app_id": bitrix_identity.app_id,
        "app_code": str(
            bitrix_identity.app_code or ""
        ).strip(),
    }


def _load_token_payload(token: Optional[str]) -> dict:
    token = str(token or "").strip()

    if not token:
        raise CashRebootSessionError(
            "Сессия не передана. Откройте приложение заново из Bitrix24"
        )

    try:
        payload = signing.loads(
            token,
            salt=SIGNING_SALT,
            max_age=get_session_ttl_seconds(),
        )
    except signing.SignatureExpired as exc:
        raise CashRebootSessionError(
            "Сессия истекла. Откройте приложение заново из Bitrix24"
        ) from exc
    except signing.BadSignature as exc:
        raise CashRebootSessionError(
            "Некорректная сессия. Откройте приложение заново из Bitrix24"
        ) from exc

    if not isinstance(payload, dict) or payload.get("version") != 3:
        raise CashRebootSessionError(
            "Эта сессия больше не поддерживается. "
            "Откройте приложение заново из Bitrix24"
        )

    return payload


def _identity_from_payload(payload: dict) -> BitrixLaunchIdentity:
    try:
        bitrix_user_id = int(
            payload.get("bitrix_user_id")
        )
    except (TypeError, ValueError) as exc:
        raise CashRebootSessionError(
            "Некорректный ID пользователя Bitrix24 в сессии"
        ) from exc

    bitrix_domain = str(
        payload.get("bitrix_domain") or ""
    ).strip().lower().rstrip(".")

    if (
        bitrix_user_id <= 0
        or bitrix_domain not in _allowed_portal_domains()
    ):
        raise CashRebootSessionError(
            "Сессия не привязана к разрешённому Bitrix24"
        )

    member_id = str(
        payload.get("member_id") or ""
    ).strip()

    expected_member_id = str(
        os.getenv(
            "BITRIX24_CASH_REBOOT_MEMBER_ID",
            "",
        )
        or ""
    ).strip()

    if expected_member_id and member_id != expected_member_id:
        raise CashRebootSessionError(
            "Сессия относится к другому порталу Bitrix24"
        )

    expected_app_id_raw = str(
        os.getenv(
            "BITRIX24_CASH_REBOOT_APP_ID",
            "",
        )
        or ""
    ).strip()

    if expected_app_id_raw:
        try:
            expected_app_id = int(expected_app_id_raw)
            signed_app_id = int(
                payload.get("app_id")
            )
        except (TypeError, ValueError) as exc:
            raise CashRebootSessionError(
                "Сессия не привязана к разрешённому приложению Bitrix24"
            ) from exc

        if signed_app_id != expected_app_id:
            raise CashRebootSessionError(
                "Сессия относится к другому приложению Bitrix24"
            )

    app_id = None
    try:
        if payload.get("app_id") not in (None, ""):
            app_id = int(payload.get("app_id"))
    except (TypeError, ValueError) as exc:
        raise CashRebootSessionError(
            "Некорректный ID приложения Bitrix24 в сессии"
        ) from exc

    full_name = normalize_fio(
        payload.get("bitrix_full_name")
    )

    if not full_name:
        raise CashRebootSessionError(
            "В сессии отсутствует имя учётной записи Bitrix24"
        )

    return BitrixLaunchIdentity(
        domain=bitrix_domain,
        member_id=member_id,
        bitrix_user_id=bitrix_user_id,
        full_name=full_name,
        email=str(
            payload.get("bitrix_email") or ""
        ).strip(),
        app_id=app_id,
        app_code=str(
            payload.get("app_code") or ""
        ).strip(),
        app_installed=True,
    )


def create_launch_token(
    bitrix_identity: BitrixLaunchIdentity,
) -> str:
    """
    Даёт доступ только к ручному выбору сотрудника по ФИО.
    Сам по себе этот токен не разрешает смотреть кассы или reboot.
    """
    payload = {
        "version": 3,
        "kind": "launch",
        **_identity_payload(bitrix_identity),
    }

    return signing.dumps(
        payload,
        salt=SIGNING_SALT,
        compress=True,
    )


def get_bitrix_identity_from_launch_token(
    token: Optional[str],
) -> BitrixLaunchIdentity:
    payload = _load_token_payload(token)

    if payload.get("kind") != "launch":
        raise CashRebootSessionError(
            "Для выбора сотрудника требуется новая сессия Bitrix24"
        )

    return _identity_from_payload(payload)


def create_user_token(
    user: User,
    bitrix_identity: BitrixLaunchIdentity,
) -> str:
    """
    Создаёт сессию для магазинов и касс, связывая в ней:
      - подтверждённую учётную запись Bitrix24;
      - вручную выбранного локального сотрудника.
    """
    payload = {
        "version": 3,
        "kind": "user",
        "user_id": int(user.id),
        "fio_key": fio_key(user.full_name),
        **_identity_payload(bitrix_identity),
    }

    return signing.dumps(
        payload,
        salt=SIGNING_SALT,
        compress=True,
    )


def get_user_session_from_token(
    token: Optional[str],
) -> CashRebootUserSession:
    """
    Принимает только user-токен. Launch-токен нельзя использовать
    для получения касс и выполнения перезагрузки.
    """
    payload = _load_token_payload(token)

    if payload.get("kind") != "user":
        raise CashRebootSessionError(
            "Сначала введите ФИО сотрудника"
        )

    bitrix_identity = _identity_from_payload(
        payload
    )

    try:
        user_id = int(payload.get("user_id"))
    except (TypeError, ValueError) as exc:
        raise CashRebootSessionError(
            "Некорректные данные пользователя в сессии"
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

    if str(payload.get("fio_key") or "") != fio_key(user.full_name):
        raise CashRebootSessionError(
            "ФИО пользователя изменилось. "
            "Откройте приложение заново из Bitrix24"
        )

    return CashRebootUserSession(
        user=user,
        bitrix_identity=bitrix_identity,
    )


def get_user_from_token(token: Optional[str]) -> User:
    """Совместимый помощник для старых вызовов внутри проекта."""
    return get_user_session_from_token(token).user
