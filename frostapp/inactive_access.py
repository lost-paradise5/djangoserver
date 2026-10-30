import datetime
import json
import os
import re
import time
import uuid

from collections import Counter
from contextlib import contextmanager
from urllib.parse import urlparse

import ldap
import requests

from ldap.filter import escape_filter_chars
from ldap.controls.libldap import SimplePagedResultsControl

from django.contrib import admin, messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connection, transaction, IntegrityError
from django.db.models import Q
from django.http import Http404, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from .models import (
    InactiveAccessJob as Job,
    InactiveAccessCandidate as Candidate,
    InactiveAccessAudit as Audit,
)


LIVE_STATUSES = ("queued", "running")
FINAL_CANDIDATE_STATES = ("blocked", "already_disabled")

WORKER_LOCK_ID = 497632190271

AD_ATTRIBUTES = [
    "sAMAccountName",
    "displayName",
    "mail",
    "employeeID",
    "department",
    "title",
    "objectGUID",
    "objectSid",
    "adminCount",
    "whenCreated",
    "userAccountControl",
    "lastLogonTimestamp",
    "lastLogon",
]


class AccessError(Exception):
    """Сообщение можно показать в интерфейсе и сохранить в журнал."""


def legacy():
    # Ленивый импорт исключает циклический импорт с основным views.py.
    from . import views
    return views


def cfg(name, default=""):
    old = legacy()
    fallback = getattr(old, name, default)

    # В твоём views.py эта функция уже есть:
    # сначала окружение Docker, затем /app/.env.
    reader = getattr(old, "_env_value", None)

    if reader:
        return reader(name, str(fallback))

    return os.getenv(name, str(fallback))


def csv_set(name, default=""):
    return {
        part.strip().casefold()
        for part in cfg(name, default).split(",")
        if part.strip()
    }


def json_safe(value):
    return json.loads(
        json.dumps(value, cls=DjangoJSONEncoder)
    )


def error_text(exc):
    if isinstance(exc, AccessError):
        return str(exc)

    # Полный текст сетевого исключения может содержать секретный URL.
    return f"Ошибка: {type(exc).__name__}"


def parse_dt(value):
    if value is None or value == "":
        return None

    if isinstance(value, datetime.datetime):
        result = value
    else:
        try:
            result = parse_datetime(str(value))
        except (TypeError, ValueError):
            result = None

    if result is None:
        raise AccessError("Получена некорректная дата")

    if timezone.is_naive(result):
        result = timezone.make_aware(
            result,
            timezone.get_default_timezone(),
        )

    return result


def bool_value(value):
    if value is True or str(value).upper() in {"Y", "TRUE", "1"}:
        return True

    if value is False or str(value).upper() in {"N", "FALSE", "0"}:
        return False

    raise AccessError("Система вернула неизвестный признак активности")


def is_stale(last_seen, created, days, include_never):
    cutoff = timezone.now() - datetime.timedelta(days=int(days))

    if last_seen is None:
        return bool(
            include_never
            and created is not None
            and created < cutoff
        )

    return (
        last_seen < cutoff
        and (created is None or created < cutoff)
    )


def actor_info(request):
    return {
        "actor": request.user.get_username(),
        "actor_pk": str(request.user.pk),
        "ip": (request.META.get("REMOTE_ADDR") or "")[:64],
    }


def audit(job, event, outcome, message="", candidate=None, details=None):
    # Ошибку записи журнала нельзя проглатывать.
    return Audit.objects.create(
        job=job,
        candidate=candidate,
        actor=job.actor,
        ip=job.ip,
        event=event,
        outcome=outcome,
        message=message,
        details=json_safe(details or {}),
    )


def result(outcome, message, before=None, after=None):
    return {
        "outcome": outcome,
        "message": message,
        "before": json_safe(before or {}),
        "after": json_safe(after or {}),
    }


def candidate_data(
    system,
    target,
    scope,
    identity,
    fio,
    last_seen,
    created,
    snapshot,
):
    return {
        "system": system,
        "target": str(target),
        "scope": scope,
        "identity": str(identity),
        "fio": fio or "",
        "last_seen": last_seen,
        "created": created,
        "snapshot": json_safe(snapshot),
    }


# ============================================================
# Active Directory
# ============================================================

def ad_first(entry, key, binary=False):
    values = entry.get(key) or []

    if not values:
        return b"" if binary else ""

    value = values[0]

    if binary:
        return value

    if isinstance(value, bytes):
        return value.decode("utf-8")

    return str(value)


def ad_filetime(raw):
    if raw in ("", b"", "0", b"0", None):
        return None

    try:
        number = int(raw)

        if number < 0:
            raise ValueError

        base = datetime.datetime(
            1601,
            1,
            1,
            tzinfo=datetime.timezone.utc,
        )

        return base + datetime.timedelta(
            microseconds=number // 10
        )

    except (TypeError, ValueError, OverflowError):
        raise AccessError("AD вернула некорректный FILETIME") from None


@contextmanager
def ad_connection():
    old = legacy()

    username = cfg("AD_USERNAME", old.AD_USERNAME)
    domain = cfg("AD_DOMAIN", old.AD_DOMAIN)

    bind_user = username

    if "@" not in bind_user and "\\" not in bind_user:
        bind_user = f"{domain}\\{bind_user}"

    uri = cfg(
        "INACTIVE_ACCESS_AD_URI",
        f"ldap://{old.AD_IP}",
    )

    conn = None

    try:
        conn = ldap.initialize(uri)

        conn.protocol_version = 3
        conn.set_option(ldap.OPT_REFERRALS, 0)
        conn.set_option(ldap.OPT_NETWORK_TIMEOUT, 10)
        conn.set_option(ldap.OPT_TIMEOUT, 30)

        start_tls = cfg(
            "INACTIVE_ACCESS_AD_START_TLS",
            "0",
        ).lower() in {"1", "true", "yes", "on"}

        if start_tls and not uri.startswith("ldaps://"):
            conn.start_tls_s()

        conn.simple_bind_s(
            bind_user,
            cfg("AD_PASSWORD", old.AD_PASSWORD),
        )

        yield conn

    finally:
        if conn is not None:
            try:
                conn.unbind_s()
            except ldap.LDAPError:
                pass


def ad_decode(dn, entry):
    raw_guid = ad_first(entry, "objectGUID", binary=True)

    if len(raw_guid) != 16:
        raise AccessError("AD не вернула корректный objectGUID")

    guid = str(uuid.UUID(bytes_le=raw_guid))

    try:
        uac = int(ad_first(entry, "userAccountControl"))
    except ValueError:
        raise AccessError(
            "AD не вернула корректный userAccountControl"
        ) from None

    created_raw = ad_first(entry, "whenCreated")

    try:
        created = datetime.datetime.strptime(
            created_raw.split(".")[0].rstrip("Z"),
            "%Y%m%d%H%M%S",
        ).replace(tzinfo=datetime.timezone.utc)

    except ValueError:
        raise AccessError(
            "AD не вернула корректную дату создания"
        ) from None

    dates = [
        ad_filetime(ad_first(entry, "lastLogonTimestamp")),
        ad_filetime(ad_first(entry, "lastLogon")),
    ]

    last_seen = max(
        (value for value in dates if value is not None),
        default=None,
    )

    raw_sid = ad_first(entry, "objectSid", binary=True)

    rid = (
        int.from_bytes(raw_sid[-4:], "little")
        if len(raw_sid) >= 12
        else None
    )

    sam = ad_first(entry, "sAMAccountName")

    excluded = csv_set(
        "INACTIVE_ACCESS_AD_EXCLUDE_LOGINS",
        "Administrator,Guest,krbtgt",
    )

    service_login = cfg("AD_USERNAME").split("\\")[-1].split("@")[0]
    excluded.add(service_login.casefold())

    protected = (
        sam.casefold() in excluded
        or rid in {500, 501, 502}
        or ad_first(entry, "adminCount") == "1"
    )

    snapshot = {
        "dn": dn,
        "guid": guid,
        "sam": sam,
        "fio": ad_first(entry, "displayName"),
        "mail": ad_first(entry, "mail"),
        "employeeID": ad_first(entry, "employeeID"),
        "department": ad_first(entry, "department"),
        "title": ad_first(entry, "title"),
        "uac": uac,
        "rid": rid,
        "last_seen": last_seen,
        "created": created,
    }

    return snapshot, last_seen, created, protected


def ad_find(conn, sam):
    base = cfg("AD_SEARCH_BASE", legacy().AD_SEARCH_BASE)

    search_filter = (
        "(&(objectCategory=person)"
        "(objectClass=user)"
        f"(sAMAccountName={escape_filter_chars(sam)}))"
    )

    rows = conn.search_s(
        base,
        ldap.SCOPE_SUBTREE,
        search_filter,
        AD_ATTRIBUTES,
    )

    rows = [
        (dn, entry)
        for dn, entry in rows
        if dn and isinstance(entry, dict)
    ]

    if len(rows) != 1:
        raise AccessError(
            "AD: не найдена единственная запись с точным логином"
        )

    return ad_decode(*rows[0])


def scan_ad(options):
    output = []
    seen_cookies = set()

    with ad_connection() as conn:
        control = SimplePagedResultsControl(
            True,
            size=500,
            cookie=b"",
        )

        while True:
            msgid = conn.search_ext(
                cfg("AD_SEARCH_BASE", legacy().AD_SEARCH_BASE),
                ldap.SCOPE_SUBTREE,
                "(&(objectCategory=person)(objectClass=user))",
                attrlist=AD_ATTRIBUTES,
                serverctrls=[control],
            )

            _, rows, _, controls = conn.result3(
                msgid,
                timeout=40,
            )

            for dn, entry in rows or []:
                if not dn or not isinstance(entry, dict):
                    continue

                snapshot, last_seen, created, protected = ad_decode(
                    dn,
                    entry,
                )

                if (
                    not snapshot["sam"]
                    or protected
                    or snapshot["uac"] & 2
                ):
                    continue

                if not is_stale(
                    last_seen,
                    created,
                    options["days_ad"],
                    options["include_never"],
                ):
                    continue

                output.append(
                    candidate_data(
                        system="ad",
                        target=snapshot["sam"],
                        scope="",
                        identity=snapshot["guid"],
                        fio=snapshot["fio"],
                        last_seen=last_seen,
                        created=created,
                        snapshot=snapshot,
                    )
                )

            paged = [
                item
                for item in controls
                if item.controlType
                == SimplePagedResultsControl.controlType
            ]

            if not paged:
                raise AccessError(
                    "AD не подтвердила пагинацию; "
                    "список нельзя считать полным"
                )

            cookie = paged[0].cookie

            if not cookie:
                break

            if cookie in seen_cookies:
                raise AccessError("AD повторила страницу результатов")

            seen_cookies.add(cookie)
            control.cookie = cookie

    return output


def operate_ad(candidate, options, dry_run, before_change):
    with ad_connection() as conn:
        before, last_seen, created, protected = ad_find(
            conn,
            candidate.target,
        )

        if (
            protected
            or before["guid"] != candidate.identity
            or before["sam"].casefold()
            != candidate.target.casefold()
        ):
            return result(
                "skipped",
                "Защищённая запись или изменился GUID/логин",
                before,
            )

        if before["uac"] & 2:
            return result(
                "already_disabled",
                "Учётная запись уже отключена",
                before,
                before,
            )

        if not is_stale(
            last_seen,
            created,
            options["days_ad"],
            options["include_never"],
        ):
            return result(
                "skipped",
                "Появилась свежая активность",
                before,
            )

        if dry_run:
            return result(
                "checked",
                "Проверка пройдена; изменение не выполнялось",
                before,
            )

        desired_uac = before["uac"] | 2

        # Событие журнала сохраняется ДО изменения AD.
        before_change(
            before,
            {"userAccountControl": desired_uac},
        )

        conn.modify_s(
            before["dn"],
            [
                (
                    ldap.MOD_REPLACE,
                    "userAccountControl",
                    [str(desired_uac).encode("ascii")],
                )
            ],
        )

        after, _, _, _ = ad_find(conn, candidate.target)

        if (
            after["guid"] == candidate.identity
            and after["uac"] & 2
        ):
            return result(
                "blocked",
                "Отключение подтверждено повторным LDAP-чтением",
                before,
                after,
            )

        return result(
            "unknown",
            "Контрольное чтение AD не подтвердило отключение",
            before,
            after,
        )


# ============================================================
# Битрикс24
# ============================================================

def bx_urls():
    get_url = cfg(
        "BITRIX_USER_GET_URL",
        legacy().BITRIX_USER_GET_URL,
    )

    update_url = cfg(
        "BITRIX_USER_UPDATE_URL",
        legacy().BITRIX_USER_UPDATE_URL,
    )

    if not get_url.startswith("https://"):
        raise AccessError("Не настроен HTTPS-вебхук user.get")

    if not update_url.startswith("https://"):
        raise AccessError("Не настроен HTTPS-вебхук user.update")

    scope = urlparse(get_url).hostname or ""

    if scope != urlparse(update_url).hostname:
        raise AccessError(
            "Вебхуки чтения и изменения относятся "
            "к разным порталам Битрикса"
        )

    return get_url, update_url, scope


def bx_fields():
    fields = [
        "ID",
        "NAME",
        "LAST_NAME",
        "SECOND_NAME",
        "EMAIL",
        "ACTIVE",
        "IS_ONLINE",
        "LAST_LOGIN",
        "LAST_ACTIVITY_DATE",
        "DATE_REGISTER",
        "WORK_POSITION",
    ]

    inn_field = cfg(
        "BITRIX_INN_FIELD",
        getattr(legacy(), "BITRIX_INN_FIELD", ""),
    )

    if inn_field:
        fields.append(inn_field)

    return fields


def bx_excluded_ids():
    get_url, update_url, _ = bx_urls()

    excluded = {"1"}
    excluded.update(
        csv_set("INACTIVE_ACCESS_BITRIX_EXCLUDE_IDS", "1")
    )

    # Защищаем владельцев используемых вебхуков.
    for url in (get_url, update_url):
        match = re.search(
            r"/rest/(\d+)/",
            urlparse(url).path,
        )

        if match:
            excluded.add(match.group(1))

    return excluded


def bx_call(url, payload):
    time.sleep(
        float(cfg("INACTIVE_ACCESS_BITRIX_PAUSE", "0.55"))
    )

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=(8, 45),
            allow_redirects=False,
        )

        if response.status_code != 200:
            raise AccessError(
                f"Битрикс вернул HTTP {response.status_code}"
            )

        data = response.json()

    except (requests.RequestException, ValueError):
        raise AccessError(
            "Сетевая ошибка или некорректный JSON Битрикса"
        ) from None

    if not isinstance(data, dict):
        raise AccessError("Некорректный формат ответа Битрикса")

    if data.get("error"):
        code = re.sub(
            r"[^A-Za-z0-9_]",
            "",
            str(data["error"]),
        )[:80]

        raise AccessError(f"Ошибка API Битрикса: {code}")

    return data


def bx_dates(user):
    required = {
        "ID",
        "ACTIVE",
        "IS_ONLINE",
        "LAST_LOGIN",
        "LAST_ACTIVITY_DATE",
        "DATE_REGISTER",
    }

    if not required.issubset(user):
        raise AccessError(
            "Битрикс не вернул поля активности или регистрации. "
            "Проверьте права вебхука и scope user"
        )

    dates = [
        parse_dt(user.get("LAST_LOGIN")),
        parse_dt(user.get("LAST_ACTIVITY_DATE")),
    ]

    last_seen = max(
        (value for value in dates if value is not None),
        default=None,
    )

    created = parse_dt(user.get("DATE_REGISTER"))

    return last_seen, created


def bx_get_user(uid):
    get_url, _, _ = bx_urls()

    data = bx_call(
        get_url,
        {
            "filter": {"ID": int(uid)},
            "select": bx_fields(),
        },
    )

    users = data.get("result")

    if (
        not isinstance(users, list)
        or len(users) != 1
        or str(users[0].get("ID")) != str(uid)
    ):
        raise AccessError(
            "Битрикс не вернул единственную запись с точным ID"
        )

    return users[0]


def scan_bitrix(options):
    get_url, _, scope = bx_urls()
    excluded = bx_excluded_ids()

    output = []
    start = 0
    seen_pages = set()

    while True:
        if start in seen_pages:
            raise AccessError("Битрикс повторил страницу результатов")

        seen_pages.add(start)

        data = bx_call(
            get_url,
            {
                "start": start,
                "filter": {},
                "select": bx_fields(),
            },
        )

        users = data.get("result")

        if not isinstance(users, list):
            raise AccessError(
                "Битрикс вернул некорректный список пользователей"
            )

        for user in users:
            uid = str(user.get("ID") or "")

            if not uid.isdigit() or uid in excluded:
                continue

            if not bool_value(user.get("ACTIVE")):
                continue

            last_seen, created = bx_dates(user)

            if bool_value(user.get("IS_ONLINE")):
                continue

            if not is_stale(
                last_seen,
                created,
                options["days_bx"],
                options["include_never"],
            ):
                continue

            fio = " ".join(
                str(user.get(key) or "")
                for key in ("LAST_NAME", "NAME", "SECOND_NAME")
            ).strip()

            output.append(
                candidate_data(
                    system="bitrix",
                    target=uid,
                    scope=scope,
                    identity=uid,
                    fio=fio,
                    last_seen=last_seen,
                    created=created,
                    snapshot=user,
                )
            )

        next_page = data.get("next")

        if next_page is None:
            break

        try:
            next_page = int(next_page)
        except (TypeError, ValueError):
            raise AccessError(
                "Битрикс вернул некорректный next"
            ) from None

        if next_page <= start:
            raise AccessError(
                "Пагинация Битрикса не продвигается"
            )

        start = next_page

    return output


def operate_bitrix(candidate, options, dry_run, before_change):
    _, update_url, scope = bx_urls()

    if (
        not candidate.target.isdigit()
        or candidate.target != candidate.identity
        or candidate.scope != scope
        or candidate.target in bx_excluded_ids()
    ):
        return result(
            "skipped",
            "ID исключён или не совпадает с сохранённой записью",
        )

    before = bx_get_user(candidate.target)

    if not bool_value(before.get("ACTIVE")):
        return result(
            "already_disabled",
            "Учётная запись уже отключена",
            before,
            before,
        )

    last_seen, created = bx_dates(before)

    if (
        bool_value(before.get("IS_ONLINE"))
        or not is_stale(
            last_seen,
            created,
            options["days_bx"],
            options["include_never"],
        )
    ):
        return result(
            "skipped",
            "Появилась активность или пользователь онлайн",
            before,
        )

    if dry_run:
        return result(
            "checked",
            "Проверка пройдена; изменение не выполнялось",
            before,
        )

    payload = {
        "ID": int(candidate.target),
        "ACTIVE": "N",
    }

    # Журнал фиксируется до запроса изменения.
    before_change(before, payload)

    write_error = ""

    try:
        response = bx_call(update_url, payload)

        if response.get("result") is not True:
            write_error = "user.update не подтвердил изменение"

    except AccessError as exc:
        write_error = str(exc)

    # После ошибки/таймаута не повторяем изменение автоматически.
    # Сначала проверяем фактический ACTIVE.
    try:
        after = bx_get_user(candidate.target)

        if bool_value(after.get("ACTIVE")) is False:
            return result(
                "blocked",
                "ACTIVE=N подтверждено повторным user.get",
                before,
                after,
            )

        return result(
            "unknown",
            write_error or "Битрикс оставил ACTIVE=Y",
            before,
            after,
        )

    except AccessError:
        return result(
            "unknown",
            "Запрос изменения отправлен; "
            "контрольное чтение не удалось",
            before,
        )


# ============================================================
# Супермаг
# ============================================================

def scan_supermag(options, db):
    old = legacy()

    if db not in old.ORACLE_TNS_MAP:
        raise AccessError("Неизвестная база Супермага")

    excluded = {
        value.upper()
        for value in csv_set(
            "SM_LASTLOGIN_EXCLUDE_USERS",
            "S_BUDAYEV,SUPERMAG,SADMIN,SYSTEM,SYS",
        )
    }

    rows, error = old._sm_fetch_stale_users_in_db(
        db=db,
        days=options["days_sm"],
        only_enabled=True,
        q="",
        exclude_users=excluded,
    )

    if error:
        raise AccessError(
            f"Не удалось получить список Супермага: {db}"
        )

    output = []

    for row in rows:
        login = str(row.get("username") or "").strip().upper()

        last_seen = parse_dt(row.get("last_login"))
        created = parse_dt(row.get("created"))

        if not login or login in excluded:
            continue

        if not is_stale(
            last_seen,
            created,
            options["days_sm"],
            options["include_never"],
        ):
            continue

        output.append(
            candidate_data(
                system="supermag",
                target=login,
                scope=db,
                identity=login,
                fio=str(row.get("fio") or ""),
                last_seen=last_seen,
                created=created,
                snapshot=row,
            )
        )

    return output


def operate_supermag(candidate, options, dry_run, before_change):
    # Здесь позже появится согласованная логика блокировки.
    # Старую _sm_block_users_in_db сейчас НЕ вызываем.
    return result(
        "unsupported",
        "Заглушка: блокировка Супермага ещё не реализована. "
        "Oracle не изменялась.",
    )


# ============================================================
# Очередь и выполнение заданий
# ============================================================

def scan_options(request):
    options = {}

    for field, default in (
        ("days_ad", 60),
        ("days_sm", 100),
        ("days_bx", 90),
    ):
        try:
            value = int(request.POST.get(field, default))
        except (TypeError, ValueError):
            raise AccessError(
                "Количество дней должно быть целым числом"
            ) from None

        if not 1 <= value <= 3650:
            raise AccessError("Укажите от 1 до 3650 дней")

        options[field] = value

    systems = list(
        dict.fromkeys(request.POST.getlist("systems"))
    )

    if (
        not systems
        or set(systems) - {"ad", "bitrix", "supermag"}
    ):
        raise AccessError("Выберите системы из списка")

    selected_db = request.POST.get("sm_db", "BINUU00")

    if selected_db == "ALL":
        dbs = sorted(legacy().ORACLE_TNS_MAP)
    elif selected_db in legacy().ORACLE_TNS_MAP:
        dbs = [selected_db]
    else:
        raise AccessError("Неизвестная база Супермага")

    options.update(
        systems=systems,
        dbs=dbs,
        include_never=request.POST.get("include_never") == "1",
    )

    return options


def create_scan_job(request):
    options = scan_options(request)

    try:
        with transaction.atomic():
            job = Job.objects.create(
                kind="scan",
                options=options,
                **actor_info(request),
            )

            audit(
                job,
                "requested",
                "queued",
                "Запрошен поиск",
                details=options,
            )

            return job

    except IntegrityError:
        raise AccessError(
            "Уже есть активное задание. "
            "Дождитесь завершения или проверьте worker."
        ) from None


def filtered_candidates(scan, system="", query=""):
    rows = Candidate.objects.filter(scan=scan)

    if system in {"ad", "bitrix", "supermag"}:
        rows = rows.filter(system=system)

    if query:
        rows = rows.filter(
            Q(target__icontains=query)
            | Q(fio__icontains=query)
            | Q(scope__icontains=query)
        )

    return rows


def check_scan_age(scan):
    hours = int(cfg("INACTIVE_ACCESS_SNAPSHOT_HOURS", "24"))

    if (
        not scan.finished_at
        or scan.finished_at
        < timezone.now() - datetime.timedelta(hours=hours)
    ):
        raise AccessError(
            "Список устарел. Выполните новый поиск."
        )


def create_block_job(request):
    try:
        scan_id = uuid.UUID(request.POST.get("scan", ""))
    except (ValueError, TypeError, AttributeError):
        raise AccessError("Некорректный ID поиска") from None

    action = request.POST.get("action", "block")

    if action not in {"block", "test"}:
        raise AccessError("Некорректное действие")

    try:
        with transaction.atomic():
            scan = (
                Job.objects
                .select_for_update()
                .filter(
                    pk=scan_id,
                    kind="scan",
                    status__in=["done", "partial"],
                )
                .first()
            )

            if scan is None:
                raise AccessError(
                    "Поиск не найден или ещё не завершён"
                )

            check_scan_age(scan)

            if request.POST.get("row"):
                raw_ids = [request.POST["row"]]

            elif request.POST.get("all_filtered") == "1":
                raw_ids = list(
                    filtered_candidates(
                        scan,
                        request.POST.get("system", ""),
                        request.POST.get("q", "")[:200],
                    )
                    .values_list("pk", flat=True)[:10001]
                )

            else:
                try:
                    raw_ids = json.loads(
                        request.POST.get("selected_ids", "[]")
                    )
                except (ValueError, TypeError):
                    raise AccessError(
                        "Некорректный список выбранных записей"
                    ) from None

            if not isinstance(raw_ids, list):
                raise AccessError("Ожидался список записей")

            try:
                ids = sorted({
                    str(uuid.UUID(str(value)))
                    for value in raw_ids
                })
            except (ValueError, TypeError, AttributeError):
                raise AccessError(
                    "Некорректные идентификаторы записей"
                ) from None

            if not ids or len(ids) > 10000:
                raise AccessError(
                    "Выберите от 1 до 10000 учётных записей"
                )

            count = Candidate.objects.filter(
                scan=scan,
                pk__in=ids,
            ).count()

            if count != len(ids):
                raise AccessError(
                    "Часть записей не принадлежит этому поиску"
                )

            job = Job.objects.create(
                kind="test" if action == "test" else "block",
                source=scan,
                options={"candidate_ids": ids},
                **actor_info(request),
            )

            audit(
                job,
                "requested",
                "queued",
                "Запрошена проверка"
                if action == "test"
                else "Запрошена блокировка",
                details={
                    "candidate_ids": ids,
                    "scan": str(scan.pk),
                    "criteria": scan.options,
                },
            )

            return job

    except IntegrityError:
        raise AccessError(
            "Уже есть активное задание. "
            "Повторный запуск не создан."
        ) from None


def execute_scan(job):
    counts = {}
    errors = {}

    for system in job.options["systems"]:
        scopes = (
            job.options["dbs"]
            if system == "supermag"
            else [""]
        )

        for scope in scopes:
            label = (
                f"{system}:{scope}"
                if scope
                else system
            )

            try:
                if system == "ad":
                    rows = scan_ad(job.options)
                elif system == "bitrix":
                    rows = scan_bitrix(job.options)
                else:
                    rows = scan_supermag(job.options, scope)

            except Exception as exc:
                errors[label] = error_text(exc)

                audit(
                    job,
                    "scan_system",
                    "failed",
                    errors[label],
                    details={
                        "system": system,
                        "scope": scope,
                    },
                )

                continue

            # Сохраняем одну систему/базу целиком.
            with transaction.atomic():
                seen = set()

                for row in rows:
                    key = (
                        row["system"],
                        row["scope"],
                        row["target"].casefold(),
                    )

                    if key in seen:
                        continue

                    seen.add(key)

                    Candidate.objects.create(
                        scan=job,
                        **row,
                    )

                counts[label] = len(seen)

                audit(
                    job,
                    "scan_system",
                    "done",
                    f"Найдено: {len(seen)}",
                    details={
                        "system": system,
                        "scope": scope,
                    },
                )

            job.summary = {
                "counts": counts,
                "errors": errors,
            }

            job.save(update_fields=["summary"])

    job.summary = {
        "counts": counts,
        "errors": errors,
    }

    if errors and counts:
        job.status = "partial"
    elif errors:
        job.status = "failed"
    else:
        job.status = "done"

    job.message = (
        "Поиск завершён"
        if not errors
        else "Есть ошибки сбора. "
             "Показаны только успешно опрошенные системы."
    )


def execute_block(job):
    try:
        check_scan_age(job.source)
    except AccessError as exc:
        job.status = "failed"
        job.message = str(exc)
        return

    counts = Counter()
    dry_run = job.kind == "test"

    handlers = {
        "ad": operate_ad,
        "bitrix": operate_bitrix,
        "supermag": operate_supermag,
    }

    for candidate_id in job.options["candidate_ids"]:
        candidate = Candidate.objects.get(
            pk=candidate_id,
            scan=job.source,
        )

        if candidate.state in FINAL_CANDIDATE_STATES:
            counts["already_disabled"] += 1

            audit(
                job,
                "target_result",
                "already_disabled",
                "Уже обработана в этом списке; "
                "повторного изменения нет",
                candidate=candidate,
            )

            continue

        if not dry_run:
            candidate.state = "processing"
            candidate.updated_at = timezone.now()

            candidate.save(
                update_fields=["state", "updated_at"]
            )

        audit(
            job,
            "target_started",
            "running",
            candidate=candidate,
            details={
                "system": candidate.system,
                "target": candidate.target,
                "scope": candidate.scope,
                "dry_run": dry_run,
            },
        )

        attempted = False

        def before_change(before, intended):
            nonlocal attempted

            # Worker работает в autocommit.
            # Это событие фиксируется до внешнего изменения.
            audit(
                job,
                "change_requested",
                "pending",
                candidate=candidate,
                details={
                    "before": before,
                    "intended": intended,
                },
            )

            attempted = True

        try:
            answer = handlers[candidate.system](
                candidate,
                job.source.options,
                dry_run,
                before_change,
            )

        except Exception as exc:
            answer = result(
                "unknown" if attempted else "failed",
                error_text(exc),
            )

        with transaction.atomic():
            audit(
                job,
                "target_result",
                answer["outcome"],
                answer["message"],
                candidate=candidate,
                details={
                    "before": answer.get("before", {}),
                    "after": answer.get("after", {}),
                    "dry_run": dry_run,
                },
            )

            if not dry_run:
                candidate.state = answer["outcome"]
                candidate.message = answer["message"]
                candidate.updated_at = timezone.now()

                candidate.save(
                    update_fields=[
                        "state",
                        "message",
                        "updated_at",
                    ]
                )

        counts[answer["outcome"]] += 1

        job.summary = dict(counts)
        job.save(update_fields=["summary"])

    job.summary = dict(counts)

    has_remarks = any(
        counts[name]
        for name in (
            "failed",
            "unknown",
            "unsupported",
            "skipped",
        )
    )

    job.status = "partial" if has_remarks else "done"

    job.message = (
        "Проверка завершена без изменения систем"
        if dry_run
        else "Обработка завершена. "
             "Смотрите результаты по каждой записи."
    )


def execute_job(job):
    job.status = "running"
    job.started_at = timezone.now()

    with transaction.atomic():
        job.save(
            update_fields=["status", "started_at"]
        )

        audit(job, "started", "running")

    if job.kind == "scan":
        execute_scan(job)
    else:
        execute_block(job)

    job.finished_at = timezone.now()

    with transaction.atomic():
        job.save(
            update_fields=[
                "status",
                "finished_at",
                "summary",
                "message",
            ]
        )

        audit(
            job,
            "finished",
            job.status,
            job.message,
            details=job.summary,
        )


def recover_interrupted_jobs():
    # Вызывать только после захвата lock новым worker.
    for job in Job.objects.filter(status="running"):
        with transaction.atomic():
            candidates = Candidate.objects.filter(
                pk__in=job.options.get("candidate_ids", []),
                state="processing",
            )

            for candidate in candidates:
                candidate.state = "unknown"
                candidate.message = (
                    "Worker прервался. "
                    "Требуется проверка фактического состояния."
                )
                candidate.updated_at = timezone.now()

                candidate.save(
                    update_fields=[
                        "state",
                        "message",
                        "updated_at",
                    ]
                )

                audit(
                    job,
                    "interrupted_target",
                    "unknown",
                    candidate.message,
                    candidate=candidate,
                )

            job.status = "interrupted"
            job.finished_at = timezone.now()
            job.message = (
                "Предыдущий worker прервался. "
                "Автоматического повтора изменений нет."
            )

            job.save(
                update_fields=[
                    "status",
                    "finished_at",
                    "message",
                ]
            )

            audit(
                job,
                "interrupted",
                "unknown",
                job.message,
            )


def acquire_worker_lock():
    if connection.vendor != "postgresql":
        raise AccessError("Worker требует PostgreSQL")

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_try_advisory_lock(%s)",
            [WORKER_LOCK_ID],
        )

        return cursor.fetchone()[0]


def release_worker_lock():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_unlock(%s)",
            [WORKER_LOCK_ID],
        )


# ============================================================
# Интерфейс
# ============================================================

def find_job(raw_id):
    try:
        job_id = uuid.UUID(str(raw_id))
    except (ValueError, TypeError, AttributeError):
        raise Http404 from None

    return get_object_or_404(Job, pk=job_id)


@staff_member_required
@never_cache
@require_GET
def page(request):
    if request.GET.get("job"):
        job = find_job(request.GET["job"])
    else:
        job = Job.objects.first()

    if job and job.kind == "scan":
        scan = job
    else:
        scan = job.source if job else None

    system = request.GET.get("system", "")
    query = request.GET.get("q", "").strip()[:200]

    candidates = (
        filtered_candidates(scan, system, query)
        if scan
        else Candidate.objects.none()
    )

    paginator = Paginator(candidates, 200)
    current_page = paginator.get_page(request.GET.get("page"))

    defaults = (
        scan.options
        if scan
        else {
            "days_ad": 60,
            "days_sm": 100,
            "days_bx": 90,
            "systems": ["ad", "bitrix", "supermag"],
            "dbs": ["BINUU00"],
            "include_never": True,
        }
    )

    selected_sm_db = (
        defaults["dbs"][0]
        if len(defaults.get("dbs", [])) == 1
        else "ALL"
    )

    context = admin.site.each_context(request)

    context.update({
        "title": "Неактивные учётные записи",
        "job": job,
        "scan": scan,
        "jobs": Job.objects.all()[:30],
        "active_job": Job.objects.filter(
            status__in=LIVE_STATUSES
        ).first(),
        "rows": current_page.object_list,
        "page_obj": current_page,
        "system": system,
        "q": query,
        "defaults": defaults,
        "sm_dbs": sorted(legacy().ORACLE_TNS_MAP),
        "selected_sm_db": selected_sm_db,
        "scan_ready": bool(
            scan and scan.status in {"done", "partial"}
        ),
        "events": (
            Audit.objects
            .filter(job=job)
            .select_related("candidate")[:300]
            if job
            else []
        ),
    })

    return render(
        request,
        "frostapp/inactive_access.html",
        context,
    )


@staff_member_required
@csrf_protect
@require_POST
def start_scan(request):
    try:
        job = create_scan_job(request)

    except AccessError as exc:
        messages.error(request, str(exc))
        return redirect("inactive_access_ui")

    return redirect(
        reverse("inactive_access_ui")
        + f"?job={job.pk}"
    )


@staff_member_required
@csrf_protect
@require_POST
def start_block(request):
    try:
        job = create_block_job(request)

    except AccessError as exc:
        messages.error(request, str(exc))
        return redirect("inactive_access_ui")

    return redirect(
        reverse("inactive_access_ui")
        + f"?job={job.pk}"
    )


@staff_member_required
@never_cache
@require_GET
def status(request, job_id):
    job = get_object_or_404(Job, pk=job_id)

    return JsonResponse({
        "status": job.status,
        "summary": job.summary,
        "message": job.message,
        "finished": job.status not in LIVE_STATUSES,
    })


@staff_member_required
@csrf_protect
@require_POST
def legacy_write_closed(request):
    job = Job.objects.create(
        kind="block",
        status="failed",
        finished_at=timezone.now(),
        message="Старый маршрут изменения закрыт",
        options={"legacy_path": request.path},
        **actor_info(request),
    )

    audit(
        job,
        "legacy_write_rejected",
        "rejected",
        "Используйте новый интерфейс с журналом",
    )

    return JsonResponse(
        {
            "ok": False,
            "error": "USE_AUDITED_INTERFACE",
            "message": (
                "Операции перенесены в новый интерфейс"
            ),
            "url": reverse("inactive_access_ui"),
        },
        status=409,
    )


def protect_legacy_urls(patterns):
    read_names = {
        "bitrix_inactive_users_ui",
        "sm_oracle_inactive_users_ui",
        "ad_ui_users",
    }

    write_names = {
        "bitrix_users_toggle_active",
        "sm_oracle_users_block",
        "ad_ui_users_toggle",
    }

    for pattern in patterns:
        name = getattr(pattern, "name", None)

        if name in write_names:
            pattern.callback = legacy_write_closed

        elif name in read_names:
            pattern.callback = staff_member_required(
                pattern.callback
            )

    return patterns
