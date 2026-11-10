import json
import logging
import re
from collections import Counter, defaultdict

from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, connection, transaction
from django.http import HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods
from requests.exceptions import RequestException

from .models import Store, UKMUser, User
from .views import (
    ONEC_WORKING_EMPLOYEES_TIMEOUT,
    ONEC_WORKING_EMPLOYEES_URL,
    _env_value,
    _request_working_employees,
)

logger = logging.getLogger(__name__)
STORE_IDS_ENV = "UKM_DISCOUNTER_SYNC_SMSTORE_IDS"
ROLE_11_POSITIONS = {
    "администратор",
    "администратор магазина",
    "директор",
    "директор магазина",
}


class StoreAccessError(Exception):
    pass


def _text(value):
    return " ".join(str(value if value is not None else "").split())


def _name_key(value):
    return _text(value).casefold().replace("ё", "е")


def _store_id(value):
    text = str(value).strip()
    if re.fullmatch(r"[0-9]{1,10}", text):
        number = int(text)
        if 0 < number <= 2147483647:
            return number
    return None


def _configured_stores():
    raw = _env_value(STORE_IDS_ENV)
    if not raw:
        raise StoreAccessError(f"Заполни {STORE_IDS_ENV} в .env.")

    values = {_store_id(value) for value in raw.split(",")}
    if None in values:
        raise StoreAccessError(
            f"В {STORE_IDS_ENV} укажи положительные ID через запятую."
        )

    return sorted(values)


def _read_onec():
    username = _env_value("ONEC_WORKING_EMPLOYEES_AUTH_USER")
    password = _env_value("ONEC_WORKING_EMPLOYEES_AUTH_PASSWORD")

    if bool(username) != bool(password):
        raise StoreAccessError("Для Basic Auth в 1С укажи и логин, и пароль.")

    try:
        response = _request_working_employees(
            ONEC_WORKING_EMPLOYEES_URL,
            auth=(username, password) if username else None,
            headers={"Accept": "application/json"},
            timeout=(10, ONEC_WORKING_EMPLOYEES_TIMEOUT),
        )
        response.raise_for_status()
    except RequestException as exc:
        if exc.response is not None:
            raise StoreAccessError(
                f"1С вернула HTTP {exc.response.status_code}."
            ) from exc
        raise StoreAccessError("Не удалось получить ответ от 1С.") from exc

    try:
        payload = response.json()
        if isinstance(payload, str):
            payload = json.loads(payload.lstrip("\ufeff"))
    except ValueError as exc:
        raise StoreAccessError(
            "1С вернула ответ, который не удалось прочитать как JSON."
        ) from exc

    return payload


def _employee_rows(payload):
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        for key in ("items", "data", "result", "value", "employees", "Сотрудники", "Работники"):
            value = payload.get(key)
            if isinstance(value, (list, dict)):
                return _employee_rows(value)

        if "ИНН" in payload or "ИдМагазина" in payload:
            return [payload]

        if payload and all(
            str(key).isdigit() and isinstance(value, dict)
            for key, value in payload.items()
        ):
            return list(payload.values())

    raise StoreAccessError("В ответе 1С не найден список сотрудников.")


def _plan_employees(records, store_ids):
    selected = [
        (smstore, item)
        for item in records
        if isinstance(item, dict)
        and (smstore := _store_id(item.get("ИдМагазина"))) in store_ids
    ]

    missing_stores = sorted(set(store_ids) - {smstore for smstore, _ in selected})
    if not selected:
        return [], {}, missing_stores

    stores = defaultdict(list)
    for store in Store.objects.filter(smstore__in=store_ids).values(
        "smstore", "ukm4store", "name",
    ):
        stores[store["smstore"]].append(store)

    inns = {
        inn
        for _, item in selected
        if re.fullmatch(r"[0-9]{10}|[0-9]{12}", inn := _text(item.get("ИНН")))
    }

    users = defaultdict(list)
    for user in User.objects.filter(employee_id__in=inns).values(
        "id", "employee_id", "full_name", "active",
    ):
        users[user["employee_id"]].append(user)

    report = []
    candidates = defaultdict(list)

    for smstore, item in selected:
        inn = _text(item.get("ИНН"))
        fio = _text(item.get("ФИО")) or " ".join(
            part
            for key in ("Фамилия", "Имя", "Отчество")
            if (part := _text(item.get(key)))
        )
        position = _text(item.get("Должность"))
        roleid = (11 if _name_key(position) in ROLE_11_POSITIONS else 12) if position else None

        row = {
            "smstore": smstore,
            "fio": fio,
            "inn": inn,
            "position": position,
            "roleid": roleid,
        }
        report.append(row)

        mappings = stores[smstore]
        if len(mappings) == 1:
            row.update(mappings[0])

        if inn not in inns or not fio:
            row.update(state="skip", reason="В 1С отсутствуют корректный ИНН или ФИО.")
            continue

        same_inn = users[inn]
        matches = [
            user for user in same_inn
            if _name_key(user["full_name"]) == _name_key(fio)
        ]

        if len(matches) != 1:
            if not same_inn:
                reason = "Пользователь с таким ИНН не найден."
            elif not matches:
                reason = "ИНН найден, ФИО не совпало."
            else:
                reason = "Несколько пользователей с одинаковыми ИНН и ФИО."

            row.update(state="skip", reason=reason, users_found=same_inn)
            continue

        user = matches[0]
        row.update(user_id=user["id"], db_fio=user["full_name"])

        if not user["active"]:
            row.update(state="skip", reason="Пользователь найден, но неактивен.")
        elif len(mappings) != 1 or not _store_id(row.get("ukm4store")):
            reason = {
                0: "Магазин с таким smstore не найден.",
                1: "В stores не заполнен корректный ukm4store.",
            }.get(len(mappings), "Несколько магазинов с одинаковым smstore.")
            row.update(state="skip", reason=reason)
        elif roleid is None:
            row.update(state="skip", reason="В 1С не заполнена должность.")
        else:
            candidates[user["id"], row["ukm4store"]].append(row)

    return report, candidates, missing_stores


def _process_links(report, candidates, *, write):
    user_ids = {row["user_id"] for row in report if "user_id" in row}
    existing = defaultdict(list)

    for link in UKMUser.objects.filter(user_id__in=user_ids).values(
        "user_id", "storeid", "roleid", "version",
    ).order_by("storeid", "roleid", "id"):
        existing[link["user_id"]].append(link)

    for row in report:
        row["existing"] = existing.get(row.get("user_id"), [])

    new_links = []
    for (user_id, storeid), rows in candidates.items():
        at_store = [
            link for link in existing[user_id]
            if link["storeid"] == storeid
        ]
        roles = {row["roleid"] for row in rows}

        if at_store:
            for row in rows:
                has_role = any(link["roleid"] == row["roleid"] for link in at_store)
                reason = (
                    "Привязка уже есть."
                    if has_role
                    else "Привязка уже есть с другой ролью."
                )
                row.update(state="exists", reason=reason)

        elif len(roles) != 1:
            for row in rows:
                row.update(
                    state="skip",
                    reason="В 1С конфликт ролей для пользователя и магазина.",
                )

        else:
            first, *duplicates = rows
            if write:
                first.update(state="added", reason="Привязка добавлена.")
                new_links.append(
                    UKMUser(
                        user_id=user_id,
                        storeid=storeid,
                        roleid=first["roleid"],
                        version=1,
                    )
                )
            else:
                first.update(state="planned", reason="Будет добавлена.")

            for row in duplicates:
                row.update(state="duplicate", reason="Повтор строки 1С. Учтена один раз.")

    if new_links:
        UKMUser.objects.bulk_create(new_links, batch_size=500)


def _apply_links(report, candidates, *, write):
    if not write or not candidates:
        _process_links(report, candidates, write=False)
        return

    if connection.vendor != "postgresql":
        raise StoreAccessError("Запись доступна только через подключение PostgreSQL.")

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            table = connection.ops.quote_name(UKMUser._meta.db_table)
            cursor.execute(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE")

        _process_links(report, candidates, write=True)


@transaction.non_atomic_requests
@never_cache
@staff_member_required
@csrf_protect
@require_http_methods(["GET", "POST"])
def ukm_store_access(request):
    if not request.user.is_superuser:
        raise PermissionDenied

    tab = "source" if request.GET.get("tab") == "source" else "access"
    mode = request.POST.get("mode") if request.method == "POST" else None

    if request.method == "POST" and mode not in {"test", "apply", "source"}:
        return HttpResponseBadRequest("Неизвестный режим.")

    if mode:
        tab = "source" if mode == "source" else "access"

    ctx = {"tab": tab, "mode": mode, "store_env": STORE_IDS_ENV}
    status = 200

    try:
        if tab == "access":
            ctx["store_ids"] = _configured_stores()

        if mode == "source":
            payload = _read_onec()
            try:
                rows = _employee_rows(payload)
            except StoreAccessError:
                rows = [payload]

            ctx.update(
                source_loaded=True,
                source_payload=payload,
                source_rows=rows,
                source_count=len(rows),
                source_is_list=rows is payload,
            )
            logger.info(
                "ukm_store_access actor=%s mode=source rows=%s",
                request.user.pk, len(rows),
            )

        elif mode in {"test", "apply"}:
            records = _employee_rows(_read_onec())
            report, candidates, missing_stores = _plan_employees(records, ctx["store_ids"])
            _apply_links(report, candidates, write=mode == "apply")

            counts = Counter(row["state"] for row in report)
            ctx.update(
                report=report,
                result_ready=True,
                missing_stores=missing_stores,
                total_1c=len(records),
                counts=dict(counts),
                found=sum("user_id" in row for row in report),
            )
            logger.info(
                "ukm_store_access actor=%s mode=%s stores=%s result=%s",
                request.user.pk, mode, ctx["store_ids"], dict(counts),
            )

    except StoreAccessError as exc:
        ctx["error"] = str(exc)

    except DatabaseError as exc:
        logger.warning(
            "ukm_store_access actor=%s mode=%s database_error=%s",
            request.user.pk, mode, type(exc).__name__,
        )
        ctx["error"] = "PostgreSQL отклонил операцию. Проверь журнал Django."
        status = 503

    return render(request, "frostapp/ukm_store_access.html", ctx, status=status)
