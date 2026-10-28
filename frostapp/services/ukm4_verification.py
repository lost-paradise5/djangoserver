import json
import re
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

import requests
from django.db import connection, transaction
from django.utils import timezone


# Тот же ключ, который уже использует rotate_qr_codes.
ROTATION_LOCK = 987654321012345678

# Отдельная блокировка для единственного проверяющего worker.
WORKER_LOCK = 487654321012345679


def sql(query, params=()):
    with connection.cursor() as cur:
        cur.execute(query, params)

        if cur.description is None:
            return []

        names = [column[0] for column in cur.description]
        rows = [
            dict(zip(names, row))
            for row in cur.fetchall()
        ]

        # При работе через сырой Django cursor JSONB
        # может возвращаться строкой.
        for row in rows:
            for key in ("meta", "observation"):
                if isinstance(row.get(key), str):
                    row[key] = json.loads(row[key])

        return rows


def env(name, default=""):
    # Ленивый импорт исключает циклический импорт с views.py.
    from frostapp.views import _env_value

    return _env_value(name, default)


def number(name, default):
    return max(1, int(env(name, str(default))))


def text(value):
    if isinstance(value, memoryview):
        value = value.tobytes()

    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode(
            "utf-8",
            errors="strict",
        )

    return str(
        value if value is not None else ""
    ).strip()


def day(value):
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    try:
        return date.fromisoformat(text(value)[:10])
    except (TypeError, ValueError):
        return None


def deleted(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value) not in (
            b"",
            b"0",
            b"\x00",
        )

    return text(value).lower() not in (
        "",
        "0",
        "false",
        "none",
    )


# def start_batch(run_id=None, initiator=None):
#     batch_id = str(uuid.uuid4())

#     sql("""
#         INSERT INTO ukm4_verify_batch(
#             id, rotation_run_id, meta
#         )
#         VALUES (%s, %s, %s::jsonb)
#     """, [
#         batch_id,
#         run_id or None,
#         json.dumps(
#             {"initiator": initiator or {}},
#             ensure_ascii=False,
#         ),
#     ])

#     return batch_id

def start_batch(
    run_id=None,
    initiator=None,
):
    batch_id = str(uuid.uuid4())

    meta = {
        "initiator": initiator or {},
    }

    enabled = env(
        "UKM4_STORE_ALERTS_ENABLED",
        "1",
    ).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    if enabled:
        chat_id = int(
            env(
                "UKM4_STORE_ALERT_CHAT_ID",
                "-80639054600805",
            )
        )

        bot_url = env(
            "UKM4_BACKUP_BOT_URL",
            "https://max.ru/id0300005666_bot",
        )

        if (
            not chat_id
            or not bot_url.startswith(
                "https://max.ru/"
            )
            or len(bot_url) > 300
        ):
            raise ValueError(
                "Проверьте настройки чата "
                "и резервного бота"
            )

        meta["store_alert"] = {
            "chat_id": chat_id,
            "bot_url": bot_url,
            "tz": env(
                "ROTATION_TZ",
                "Asia/Irkutsk",
            ),
        }

    sql("""
        INSERT INTO ukm4_verify_batch(
            id,
            rotation_run_id,
            meta
        )
        VALUES (%s, %s, %s::jsonb)
    """, [
        batch_id,
        run_id or None,
        json.dumps(
            meta,
            ensure_ascii=False,
        ),
    ])

    return batch_id


def close_batch(batch_id, outcome, summary):
    if not batch_id:
        return

    sql("""
        UPDATE ukm4_verify_batch
        SET closed_at = now(),
            outcome = %s,
            meta = meta || %s::jsonb
        WHERE id = %s
          AND closed_at IS NULL
    """, [
        outcome,
        json.dumps(summary, ensure_ascii=False),
        batch_id,
    ])


def create_check(
    batch_id,
    user_id,
    store_id,
    cashier_id,
    fio,
    inn,
    password,
):
    from frostapp.models import Store

    store_name = (
        Store.objects
        .filter(ukm4store=store_id)
        .values_list("name", flat=True)
        .first()
        or ""
    )

    rows = sql("""
        INSERT INTO ukm4_verify_task(
            id,
            batch_id,
            user_id,
            store_id,
            cashier_id,
            fio,
            employee_inn,
            expected_password,
            store_name
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)

        ON CONFLICT (batch_id, user_id, store_id)
        DO UPDATE SET
            cashier_id = EXCLUDED.cashier_id,
            expected_password = EXCLUDED.expected_password,
            status = 'creating',
            expected_hash = NULL,
            host = '',
            start_date = NULL,
            end_date = NULL,
            prepared_at = NULL,
            submitted_at = NULL,
            last_error = '',
            observation = '{}'::jsonb

        RETURNING id
    """, [
        str(uuid.uuid4()),
        batch_id,
        user_id,
        store_id,
        cashier_id,
        fio,
        inn,
        password,
        store_name,
    ])

    return str(rows[0]["id"])


def prepare_check(task_id, host, converter_row):
    """
    Сохраняется ДО commit MySQL.

    Если процесс оборвётся сразу после commit,
    задание проверки уже останется в PostgreSQL.
    """
    if not task_id:
        return

    delay = number(
        "UKM4_VERIFY_DELAY_SECONDS",
        120,
    )
    timeout = number(
        "UKM4_VERIFY_TIMEOUT_SECONDS",
        900,
    )

    expected_hash = text(
        converter_row.get("password")
    )

    if not expected_hash:
        raise ValueError(
            "Пустой пароль в import4.users"
        )

    with transaction.atomic():
        sql("""
            UPDATE ukm4_verify_task
            SET host = %s,
                expected_hash = %s,
                start_date = %s,
                end_date = %s,
                status = 'pending',
                prepared_at = now(),
                next_check_at =
                    now() + %s * interval '1 second',
                deadline_at =
                    now() + %s * interval '1 second'
            WHERE id = %s
        """, [
            str(host),
            expected_hash,
            converter_row.get("start_date"),
            converter_row.get("end_date"),
            delay,
            delay + timeout,
            task_id,
        ])

        # Новая отправка для того же сотрудника
        # и магазина заменяет незавершённую старую.
        sql("""
            UPDATE ukm4_verify_task AS old
            SET status = 'superseded',
                last_error =
                    'Началась более новая отправка в этот магазин'
            FROM ukm4_verify_task AS fresh
            WHERE fresh.id = %s
              AND old.id <> fresh.id
              AND old.batch_id <> fresh.batch_id
              AND old.user_id = fresh.user_id
              AND old.store_id = fresh.store_id
              AND old.status IN ('creating', 'pending')
        """, [task_id])


def submission_result(task_id, error=""):
    if not task_id:
        return

    sql("""
        UPDATE ukm4_verify_task
        SET status = CASE
                WHEN status = 'creating'
                THEN 'submit_failed'
                ELSE status
            END,
            submitted_at = CASE
                WHEN %s = '' THEN now()
                ELSE submitted_at
            END,
            last_error = %s
        WHERE id = %s
    """, [
        error,
        error,
        task_id,
    ])


def recover_interrupted():
    """
    Закрывает пакеты процессов, которые уже
    не держат существующую блокировку ротации.
    """
    with transaction.atomic():
        sql("""
            UPDATE ukm4_verify_batch AS b
            SET closed_at = now(),
                outcome = 'interrupted'
            WHERE closed_at IS NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM pg_locks l
                  WHERE l.pid = b.owner_pid
                    AND l.locktype = 'advisory'
                    AND l.granted
                    AND l.objsubid = 1
                    AND (
                        (l.classid::bigint << 32)
                        | l.objid::bigint
                    ) = %s
                    AND l.database = (
                        SELECT oid
                        FROM pg_database
                        WHERE datname = current_database()
                    )
              )
        """, [ROTATION_LOCK])

        sql("""
            UPDATE ukm4_verify_task AS t
            SET status = 'submit_failed',
                last_error =
                    'Отправка не дошла до подготовки проверки'
            FROM ukm4_verify_batch AS b
            WHERE t.batch_id = b.id
              AND b.closed_at IS NOT NULL
              AND t.status = 'creating'
        """)


class SchemaError(Exception):
    pass


def read_host(host, tasks):
    from frostapp.views import connect_ukm
    from frostapp.models import Store

    if not host:
        raise SchemaError(
            "Не сохранён адрес сервера магазина"
        )

    # Передаём конкретный сохранённый адрес:
    # fallback на центральный сервер здесь не используется.
    conn = connect_ukm(host=host)

    try:
        # Каждое чтение получает актуальное состояние.
        conn.autocommit(True)

        with conn.cursor() as cur:
            cur.execute(
                "SHOW COLUMNS FROM trm_in_users"
            )

            columns = {
                row["Field"]
                for row in cur.fetchall()
            }

            password_column = env(
                "UKM4_VERIFY_PASSWORD_COLUMN",
                "password",
            )
            start_column = env(
                "UKM4_VERIFY_START_COLUMN",
                "start_date",
            )
            end_column = env(
                "UKM4_VERIFY_END_COLUMN",
                "end_date",
            )

            required = (
                password_column,
                start_column,
                end_column,
                "id",
                "user_inn",
            )

            for column in required:
                if (
                    not re.fullmatch(
                        r"[A-Za-z_][A-Za-z0-9_]*",
                        column,
                    )
                    or column not in columns
                ):
                    raise SchemaError(
                        "В trm_in_users отсутствует "
                        f"колонка {column!r}"
                    )

            store_column = next(
                (
                    column
                    for column in (
                        "store",
                        "storeid",
                        "store_id",
                        "shop",
                        "shop_id",
                    )
                    if column in columns
                ),
                None,
            )

            if not store_column:
                linked_stores = set(
                    Store.objects
                    .filter(ukm4ip=host)
                    .values_list(
                        "ukm4store",
                        flat=True,
                    )
                ) - {None}

                requested_stores = {
                    task["store_id"]
                    for task in tasks
                }

                if len(
                    linked_stores | requested_stores
                ) > 1:
                    raise SchemaError(
                        "На сервере несколько магазинов, "
                        "а store-колонки нет"
                    )

            store_expression = (
                f"`{store_column}`"
                if store_column
                else "NULL"
            )

            deleted_expression = (
                "`deleted`"
                if "deleted" in columns
                else "0"
            )

            cashier_ids = sorted({
                int(task["cashier_id"])
                for task in tasks
            })

            placeholders = ",".join(
                ["%s"] * len(cashier_ids)
            )

            cur.execute(f"""
                SELECT
                    id,
                    user_inn,
                    `{password_column}` AS pwd,
                    `{start_column}` AS ds,
                    `{end_column}` AS de,
                    {store_expression} AS sid,
                    {deleted_expression} AS deleted
                FROM trm_in_users
                WHERE id IN ({placeholders})
            """, cashier_ids)

            rows = defaultdict(list)

            for row in cur.fetchall():
                store_id = (
                    int(row["sid"])
                    if store_column
                    and row["sid"] is not None
                    else None
                )

                rows[
                    (int(row["id"]), store_id)
                ].append(row)

            return rows, bool(store_column)

    finally:
        conn.close()


def compare(task, row):
    identity_ok = (
        text(row.get("user_inn"))
        == task["employee_inn"]
    )

    password_ok = (
        text(row.get("pwd")).lower()
        == text(task["expected_hash"]).lower()
    )

    actual_start = day(row.get("ds"))
    actual_end = day(row.get("de"))

    dates_ok = (
        actual_start is not None
        and actual_end is not None
        and actual_start == day(task["start_date"])
        and actual_end == day(task["end_date"])
    )

    active_ok = not deleted(
        row.get("deleted")
    )

    observation = {
        "identity_ok": identity_ok,
        "password_ok": password_ok,
        "dates_ok": dates_ok,
        "active_ok": active_ok,
        "actual_start": (
            str(actual_start)
            if actual_start
            else "—"
        ),
        "actual_end": (
            str(actual_end)
            if actual_end
            else "—"
        ),
    }

    ready = all((
        identity_ok,
        password_ok,
        dates_ok,
        active_ok,
    ))

    return ready, observation


def save_check(
    task,
    status,
    observation,
    error="",
):
    sql("""
        UPDATE ukm4_verify_task
        SET status = %s,
            observation = %s::jsonb,
            last_error = %s,
            checked_at = now(),
            attempts = attempts + 1,
            next_check_at =
                now() + %s * interval '1 second'
        WHERE id = %s
          AND status = 'pending'
          AND expected_hash = %s
    """, [
        status,
        json.dumps(observation),
        error,
        number(
            "UKM4_VERIFY_POLL_SECONDS",
            30,
        ),
        task["id"],
        task["expected_hash"],
    ])


def verify_due():
    tasks = sql("""
        SELECT *
        FROM ukm4_verify_task
        WHERE status = 'pending'
          AND next_check_at <= now()
        ORDER BY next_check_at, id
        LIMIT 100
    """)

    groups = defaultdict(list)

    for task in tasks:
        groups[task["host"]].append(task)

    # Одно подключение на сервер для группы заданий.
    for host, group in groups.items():
        try:
            rows, has_store = read_host(
                host,
                group,
            )

        except SchemaError as exc:
            for task in group:
                save_check(
                    task,
                    "schema_error",
                    {},
                    str(exc),
                )
            continue

        except Exception as exc:
            from django.db import DatabaseError

            # Ошибку PostgreSQL нельзя выдавать
            # за недоступность MySQL магазина.
            if isinstance(exc, DatabaseError):
                raise

            for task in group:
                expired = (
                    timezone.now()
                    >= task["deadline_at"]
                )

                save_check(
                    task,
                    (
                        "unavailable"
                        if expired
                        else "pending"
                    ),
                    {},
                    "Чтение УКМ-4: "
                    + type(exc).__name__,
                )

            continue

        for task in group:
            if (
                task["start_date"] is None
                or task["end_date"] is None
            ):
                save_check(
                    task,
                    "schema_error",
                    {},
                    "В import4.users нет обеих "
                    "дат срока действия",
                )
                continue

            key = (
                int(task["cashier_id"]),
                (
                    int(task["store_id"])
                    if has_store
                    else None
                ),
            )

            candidates = rows.get(key, [])

            if len(candidates) != 1:
                ready = False
                observation = {
                    "matching_rows": len(candidates),
                }
                error = (
                    "Найдено строк сотрудника: "
                    f"{len(candidates)}"
                )

            else:
                ready, observation = compare(
                    task,
                    candidates[0],
                )

                labels = {
                    "identity_ok": "ИНН",
                    "password_ok": "пароль",
                    "dates_ok": "даты",
                    "active_ok": "активность",
                }

                problems = [
                    label
                    for key, label in labels.items()
                    if observation.get(key) is not True
                ]

                error = (
                    ""
                    if ready
                    else (
                        "Не подтверждено: "
                        + ", ".join(problems)
                    )
                )

            expired = (
                timezone.now()
                >= task["deadline_at"]
            )

            status = (
                "verified"
                if ready
                else (
                    "timeout"
                    if expired
                    else "pending"
                )
            )

            save_check(
                task,
                status,
                observation,
                error,
            )


def report_lines(batch, tasks):
    tz = ZoneInfo(
        env("ROTATION_TZ", "Asia/Irkutsk")
    )

    stamp = (
        timezone.now()
        .astimezone(tz)
        .strftime("%d.%m.%Y %H:%M:%S")
    )

    labels = {
        "verified": "подтверждено",
        "timeout": "не совпало за время ожидания",
        "unavailable": "БД недоступна",
        "schema_error": "нельзя проверить схему",
        "submit_failed": "ошибка до отправки",
        "superseded": "заменено новым запуском",
    }

    outcomes = {
        "success": "отправка завершена",
        "partial": "отправка завершена частично",
        "failed": "ошибка ротации",
        "interrupted": "процесс ротации прерван",
    }

    counts = Counter(
        task["status"]
        for task in tasks
    )

    people = len({
        task["user_id"]
        for task in tasks
    })

    meta = batch["meta"] or {}
    rotation = meta.get("rotation") or {}

    actor = (
        (meta.get("initiator") or {}).get("fio")
        or "Автоматический запуск / CLI"
    )

    lines = [
        f"УКМ-4: проверка результата {stamp} ({tz.key})",
        f"Пакет: {batch['id']}",
        f"Запустил: {actor}",
        outcomes.get(
            batch["outcome"],
            batch["outcome"],
        ),
        (
            f"Сотрудников в проверке: {people}; "
            f"пар сотрудник-магазин: {len(tasks)}"
        ),
        (
            f"Кандидатов: {rotation.get('candidates', '—')}; "
            "отправлено без ошибок: "
            f"{rotation.get('submitted_users', '—')}; "
            f"частично: {rotation.get('partial', '—')}; "
            f"пропущено: {rotation.get('skipped', '—')}; "
            f"ошибок отправки: {rotation.get('failed', '—')}"
        ),
    ]

    lines.extend(
        f"{label}: {counts[status]}"
        for status, label in labels.items()
    )

    groups = defaultdict(list)

    for task in tasks:
        groups[task["store_id"]].append(task)

    for store_id, group in sorted(groups.items()):
        confirmed = sum(
            task["status"] == "verified"
            for task in group
        )

        superseded = sum(
            task["status"] == "superseded"
            for task in group
        )

        problems = (
            len(group)
            - confirmed
            - superseded
        )

        lines.extend([
            "",
            (
                f"Магазин {store_id} — "
                f"{group[0]['store_name']}"
            ),
            (
                f"Всего {len(group)}; "
                f"подтверждено {confirmed}; "
                f"проблем {problems}; "
                f"заменено {superseded}"
            ),
        ])

        for task in group:
            observation = (
                task["observation"] or {}
            )

            password_result = (
                "да"
                if observation.get("password_ok") is True
                else "не подтверждено"
            )

            dates_result = (
                "да"
                if observation.get("dates_ok") is True
                else "не подтверждено"
            )

            checked = (
                task["checked_at"]
                .astimezone(tz)
                .strftime("%d.%m %H:%M:%S")
                if task["checked_at"]
                else "не проверялся"
            )

            lines.extend([
                (
                    f"{task['fio']}, "
                    f"user_id={task['user_id']}, "
                    f"cashier_id={task['cashier_id']}"
                ),
                (
                    f"{labels.get(task['status'], task['status'])}; "
                    f"проверка: {checked}"
                ),
                (
                    "Ожидаемый пароль: "
                    f"{task['expected_password']}"
                ),
                (
                    "Ожидаемые даты: "
                    f"{task['start_date']} — "
                    f"{task['end_date']}"
                ),
                (
                    f"Пароль совпал: {password_result}; "
                    f"даты совпали: {dates_result}"
                ),
                (
                    "Даты в базе: "
                    f"{observation.get('actual_start', '—')} — "
                    f"{observation.get('actual_end', '—')}"
                ),
                task["last_error"],
            ])

    return lines


def chunks(lines, limit=3700):
    result = []
    current = ""

    for line in lines:
        rest = str(line) + "\n"

        while rest:
            take = min(
                len(rest),
                limit - len(current),
            )

            current += rest[:take]
            rest = rest[take:]

            if len(current) == limit:
                result.append(current)
                current = ""

    if current:
        result.append(current)

    return result


def queue_reports():
    with transaction.atomic():
        batches = sql("""
            SELECT b.*
            FROM ukm4_verify_batch b
            WHERE closed_at IS NOT NULL
              AND report_created_at IS NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM ukm4_verify_task t
                  WHERE t.batch_id = b.id
                    AND t.status IN ('creating', 'pending')
              )
            ORDER BY started_at
            LIMIT 5
            FOR UPDATE OF b SKIP LOCKED
        """)

        for batch in batches:
            tasks = sql("""
                SELECT *
                FROM ukm4_verify_task
                WHERE batch_id = %s
                ORDER BY store_id, fio
            """, [batch["id"]])

            messages = chunks(
                report_lines(batch, tasks)
            )

            for part, body in enumerate(
                messages,
                start=1,
            ):
                sql("""
                    INSERT INTO ukm4_max_outbox(
                        batch_id, part, text
                    )
                    VALUES (%s, %s, %s)
                    ON CONFLICT (batch_id, part)
                    DO NOTHING
                """, [
                    batch["id"],
                    part,
                    (
                        f"Пакет {batch['id']}; "
                        f"часть {part}/{len(messages)}\n"
                        f"{body}"
                    ),
                ])

            sql("""
                UPDATE ukm4_verify_batch
                SET report_created_at = now()
                WHERE id = %s
            """, [batch["id"]])


def send_one():
    # Импорт внутри функции исключает
    # циклический импорт с views.py.
    from frostapp.views import send_max_admin_log

    rows = sql("""
        SELECT o.*
        FROM ukm4_max_outbox o
        WHERE o.sent_at IS NULL
          AND o.next_attempt_at <= now()
          AND NOT EXISTS (
              SELECT 1
              FROM ukm4_max_outbox previous
              WHERE previous.batch_id = o.batch_id
                AND previous.part < o.part
                AND previous.sent_at IS NULL
          )
        ORDER BY o.id
        LIMIT 1
    """)

    if not rows:
        return False

    item = rows[0]

    sent = send_max_admin_log(
        item["text"]
    )

    if not sent:
        delay = min(
            3600,
            15 * 2 ** min(
                item["attempts"],
                8,
            ),
        )

        sql("""
            UPDATE ukm4_max_outbox
            SET attempts = attempts + 1,
                last_error = %s,
                next_attempt_at =
                    now() + %s * interval '1 second'
            WHERE id = %s
              AND sent_at IS NULL
        """, [
            (
                "Отправка админу MAX "
                "не подтверждена"
            ),
            delay,
            item["id"],
        ])

        return True

    sql("""
        UPDATE ukm4_max_outbox
        SET sent_at = now(),
            attempts = attempts + 1,
            last_error = ''
        WHERE id = %s
          AND sent_at IS NULL
    """, [item["id"]])

    return True










ALERT_FAILURE_STATUSES = frozenset({
    "timeout",
    "unavailable",
    "schema_error",
    "submit_failed",
})


def _store_alert_tasks(
    batch_id,
    store_id,
):
    return sql("""
        SELECT
            t.*,
            EXISTS (
                SELECT 1
                FROM ukm4_verify_task newer
                WHERE newer.user_id = t.user_id
                  AND newer.store_id = t.store_id
                  AND newer.batch_id <> t.batch_id
                  AND newer.prepared_at >
                      COALESCE(
                          t.prepared_at,
                          t.created_at
                      )
            ) AS has_newer_attempt
        FROM ukm4_verify_task t
        WHERE t.batch_id = %s
          AND t.store_id = %s
        ORDER BY t.user_id
    """, [
        batch_id,
        store_id,
    ])


def _store_alert_text(
    batch,
    store_id,
    tasks,
):
    from frostapp.models import Store

    failures = [
        task
        for task in tasks
        if (
            task["status"]
            in ALERT_FAILURE_STATUSES
            and not task["has_newer_attempt"]
        )
    ]

    if not failures:
        return None

    # В текущем проекте Store связан с stores.
    # Номер магазина здесь — ukm4store, не PK id.
    name = (
        Store.objects
        .filter(ukm4store=store_id)
        .order_by("id")
        .values_list("name", flat=True)
        .first()
        or "Название не указано"
    )

    name = " ".join(
        str(name).split()
    )[:250]

    config = batch["meta"]["store_alert"]
    tz = ZoneInfo(config["tz"])

    started = (
        batch["started_at"]
        .astimezone(tz)
        .strftime("%d.%m.%Y %H:%M")
    )

    counts = Counter(
        task["status"]
        for task in failures
    )

    confirmed = sum(
        task["status"] == "verified"
        and not task["has_newer_attempt"]
        for task in tasks
    )

    labels = {
        "timeout": (
            "Не совпали ожидаемые данные"
        ),
        "unavailable": (
            "База УКМ-4 недоступна для проверки"
        ),
        "schema_error": (
            "Не удалось проверить структуру данных"
        ),
        "submit_failed": (
            "Ошибка передачи в конвертер"
        ),
    }

    lines = [
        (
            "⚠️ УКМ-4: не подтверждено обновление "
            "паролей и сроков действия"
        ),
        f"Магазин: {store_id} — {name}",
        f"Запуск: {started} ({tz.key})",
        (
            f"Не подтверждено: {len(failures)} "
            f"из {len(tasks)} сотрудников."
        ),
        f"Подтверждено: {confirmed}.",
    ]

    lines.extend(
        f"{label}: {counts[status]}."
        for status, label in labels.items()
        if counts[status]
    )

    lines.extend([
        "",
        "Используйте резервный бейдж.",
        "Бот для резервного бейджа:",
        config["bot_url"],
        f"Пакет: {batch['id']}",
    ])

    return "\n".join(lines)


def queue_store_alerts():
    with transaction.atomic():
        pairs = sql("""
            SELECT
                b.id AS batch_id,
                t.store_id
            FROM ukm4_verify_batch b
            JOIN ukm4_verify_task t
                ON t.batch_id = b.id
            WHERE b.closed_at IS NOT NULL
              AND b.meta ? 'store_alert'
              AND NOT EXISTS (
                  SELECT 1
                  FROM ukm4_store_alert_outbox a
                  WHERE a.batch_id = b.id
                    AND a.store_id = t.store_id
              )
            GROUP BY b.id, t.store_id

            HAVING COUNT(*) FILTER (
                WHERE t.status IN (
                    'creating',
                    'pending'
                )
            ) = 0

            AND COUNT(*) FILTER (
                WHERE t.status IN (
                    'timeout',
                    'unavailable',
                    'schema_error',
                    'submit_failed'
                )
            ) > 0

            ORDER BY
                MIN(b.started_at),
                t.store_id
            LIMIT 20
        """)

        for pair in pairs:
            batch = sql("""
                SELECT *
                FROM ukm4_verify_batch
                WHERE id = %s
            """, [
                pair["batch_id"],
            ])[0]

            tasks = _store_alert_tasks(
                pair["batch_id"],
                pair["store_id"],
            )

            body = _store_alert_text(
                batch,
                pair["store_id"],
                tasks,
            )

            sql("""
                INSERT INTO ukm4_store_alert_outbox(
                    batch_id,
                    store_id,
                    chat_id,
                    text,
                    canceled_at
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    %s,
                    CASE
                        WHEN %s THEN now()
                        ELSE NULL
                    END
                )
                ON CONFLICT (
                    batch_id,
                    store_id
                )
                DO NOTHING
            """, [
                pair["batch_id"],
                pair["store_id"],
                batch["meta"]["store_alert"]["chat_id"],
                body or "",
                body is None,
            ])


def send_one_store_alert():
    from frostapp.views import (
        send_max_to_chat,
    )

    rows = sql("""
        SELECT *
        FROM ukm4_store_alert_outbox
        WHERE sent_at IS NULL
          AND canceled_at IS NULL
          AND next_attempt_at <= now()
        ORDER BY id
        LIMIT 1
    """)

    if not rows:
        return False

    item = rows[0]

    batch = sql("""
        SELECT *
        FROM ukm4_verify_batch
        WHERE id = %s
    """, [
        item["batch_id"],
    ])[0]

    # Заново проверяем актуальность перед
    # каждой попыткой отправки.
    tasks = _store_alert_tasks(
        item["batch_id"],
        item["store_id"],
    )

    body = _store_alert_text(
        batch,
        item["store_id"],
        tasks,
    )

    if body is None:
        sql("""
            UPDATE ukm4_store_alert_outbox
            SET canceled_at = now(),
                last_error = %s
            WHERE id = %s
        """, [
            "Заменено более новой отправкой",
            item["id"],
        ])

        return True

    message_id = send_max_to_chat(
        item["chat_id"],
        body,
    )

    if message_id:
        sql("""
            UPDATE ukm4_store_alert_outbox
            SET sent_at = now(),
                message_id = %s,
                text = %s,
                attempts = attempts + 1,
                last_error = ''
            WHERE id = %s
        """, [
            message_id,
            body,
            item["id"],
        ])

    else:
        delay = min(
            3600,
            15 * 2 ** min(
                item["attempts"],
                8,
            ),
        )

        sql("""
            UPDATE ukm4_store_alert_outbox
            SET attempts = attempts + 1,
                last_error = %s,
                next_attempt_at =
                    now() + %s * interval '1 second'
            WHERE id = %s
        """, [
            "MAX не подтвердил отправку в чат",
            delay,
            item["id"],
        ])

    return True
