import json
import os
import re
import time

from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import requests

from django.utils import timezone
from requests.exceptions import RequestException

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from frostapp.models import Store


DIRECTOR_POSITIONS = {
    "директор",
    "директор магазина",
    "администратор",
    "администратор магазина",
}

BITRIX_PAGE_TIMEOUT = int(
    os.getenv("DIRECTORS_REPORT_BITRIX_TIMEOUT", "60")
)

BITRIX_MAX_PAGES = int(
    os.getenv("DIRECTORS_REPORT_BITRIX_MAX_PAGES", "1000")
)

PERSON_EXACT_SCORE = 85
PERSON_SIMILAR_SCORE = 68
SHARED_EXACT_SCORE = 85
SHARED_SIMILAR_SCORE = 68


class DirectorsReportError(RuntimeError):
    pass


def _string(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, (list, tuple, set)):
        return " ".join(
            _string(item)
            for item in value
            if _string(item)
        ).strip()

    return str(value).strip()


def _normalize_text(value: Any) -> str:
    text = _string(value).casefold().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я]+", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_inn(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        for item in value:
            result = _normalize_inn(item)
            if result:
                return result
        return ""

    digits = re.sub(r"\D+", "", _string(value))
    return digits if len(digits) in (10, 12) else ""


def _to_int(value: Any) -> int | None:
    try:
        text = _string(value)
        if not text:
            return None
        return int(text)
    except (TypeError, ValueError):
        return None


def _bool_from_bitrix(value: Any) -> bool:
    return _string(value).upper() in {
        "Y",
        "YES",
        "TRUE",
        "1",
    }


def _extract_onec_rows(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        raise DirectorsReportError(
            "1С вернула ответ неизвестного формата."
        )

    for key in (
        "result",
        "data",
        "employees",
        "Сотрудники",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            return [
                item
                for item in value
                if isinstance(item, dict)
            ]

    raise DirectorsReportError(
        "В ответе 1С не найден список сотрудников."
    )


def _load_onec_directors(
    *,
    url: str,
    username: str,
    password: str,
    timeout: int,
) -> tuple[list[dict], int]:
    if not url:
        raise DirectorsReportError(
            "Не настроен ONEC_WORKING_EMPLOYEES_URL."
        )

    auth = None
    if username:
        auth = (username, password)

    try:
        response = requests.get(
            url,
            auth=auth,
            timeout=(10, max(30, timeout)),
        )
        response.raise_for_status()
    except RequestException as exc:
        raise DirectorsReportError(
            f"Не удалось получить сотрудников из 1С: {exc}"
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise DirectorsReportError(
            "1С вернула ответ, который не является JSON."
        ) from exc

    all_rows = _extract_onec_rows(payload)
    result = []
    seen = set()

    for item in all_rows:
        position_raw = _string(item.get("Должность"))
        position_normalized = _normalize_text(position_raw)

        if position_normalized not in DIRECTOR_POSITIONS:
            continue

        surname = _string(item.get("Фамилия"))
        first_name = _string(item.get("Имя"))
        middle_name = _string(item.get("Отчество"))

        fio = " ".join(
            part
            for part in (
                surname,
                first_name,
                middle_name,
            )
            if part
        ).strip()

        if not fio:
            fio = _string(item.get("ФИО"))

        inn = _normalize_inn(item.get("ИНН"))
        smstore = _to_int(item.get("ИдМагазина"))

        dedup_key = (
            inn or _normalize_text(fio),
            smstore,
            position_normalized,
        )

        if dedup_key in seen:
            continue

        seen.add(dedup_key)

        result.append({
            "inn": inn,
            "surname": surname,
            "first_name": first_name,
            "middle_name": middle_name,
            "fio": fio,
            "position": position_raw,
            "smstore": smstore,
            "email": _string(item.get("Почта")),
            "phone": _string(item.get("НомерТелефона")),
            "department": _string(item.get("Подразделение")),
            "department_guid": _string(
                item.get("ПодразделениеGuid")
            ),
            "organization": _string(
                item.get("ОрганизацияНаименование")
            ),
        })

    return result, len(all_rows)


def _bitrix_request(
    session: requests.Session,
    url: str,
    data: dict,
) -> dict:
    last_error = None

    for attempt in range(1, 5):
        try:
            response = session.post(
                url,
                data=data,
                timeout=(10, BITRIX_PAGE_TIMEOUT),
            )

            body = response.json()

            error_code = _string(body.get("error")).upper()

            if (
                response.status_code == 429
                or error_code in {
                    "QUERY_LIMIT_EXCEEDED",
                    "OPERATION_TIME_LIMIT",
                }
            ):
                retry_after = response.headers.get(
                    "Retry-After",
                    "",
                )
                try:
                    wait_seconds = max(
                        1.0,
                        float(retry_after),
                    )
                except (TypeError, ValueError):
                    wait_seconds = float(attempt)

                time.sleep(min(wait_seconds, 5.0))
                continue

            response.raise_for_status()

            if body.get("error"):
                description = (
                    body.get("error_description")
                    or body.get("error")
                )
                raise DirectorsReportError(
                    f"Ошибка Bitrix24: {description}"
                )

            return body

        except DirectorsReportError:
            raise

        except (
            RequestException,
            ValueError,
        ) as exc:
            last_error = exc

            if attempt < 4:
                time.sleep(float(attempt))
                continue

    raise DirectorsReportError(
        f"Не удалось выполнить запрос к Bitrix24: "
        f"{last_error}"
    )


def _load_all_bitrix_users(
    *,
    url: str,
) -> list[dict]:
    if not url:
        raise DirectorsReportError(
            "Не настроен BITRIX_USER_GET_URL."
        )

    session = requests.Session()
    users = []
    start = 0
    visited_starts = set()

    for _page_number in range(BITRIX_MAX_PAGES):
        if start in visited_starts:
            raise DirectorsReportError(
                "Bitrix24 вернул циклическую пагинацию."
            )

        visited_starts.add(start)

        body = _bitrix_request(
            session,
            url,
            {"start": start},
        )

        page = body.get("result", [])

        if not isinstance(page, list):
            raise DirectorsReportError(
                "Bitrix24 вернул некорректный список "
                "пользователей."
            )

        users.extend(
            user
            for user in page
            if isinstance(user, dict)
        )

        next_start = body.get("next")

        if next_start in (None, ""):
            break

        try:
            start = int(next_start)
        except (TypeError, ValueError) as exc:
            raise DirectorsReportError(
                "Bitrix24 вернул некорректное значение next."
            ) from exc
    else:
        raise DirectorsReportError(
            "Превышено допустимое количество страниц "
            "Bitrix24."
        )

    return users


def _bitrix_display_name(user: dict) -> str:
    fio = " ".join(
        value
        for value in (
            _string(user.get("LAST_NAME")),
            _string(user.get("NAME")),
            _string(user.get("SECOND_NAME")),
        )
        if value
    ).strip()

    if fio:
        return fio

    return (
        _string(user.get("EMAIL"))
        or f"Пользователь ID {user.get('ID', '?')}"
    )


def _prepare_bitrix_users(
    users: list[dict],
    inn_field: str,
) -> list[dict]:
    prepared = []

    for user in users:
        name_text = _normalize_text(
            " ".join([
                _string(user.get("NAME")),
                _string(user.get("LAST_NAME")),
                _string(user.get("SECOND_NAME")),
            ])
        )

        search_text = _normalize_text(
            " ".join([
                name_text,
                _string(user.get("WORK_POSITION")),
                _string(user.get("EMAIL")),
            ])
        )

        prepared.append({
            "raw": user,
            "id": _string(user.get("ID")),
            "display": _bitrix_display_name(user),
            "active": _bool_from_bitrix(
                user.get("ACTIVE", "Y")
            ),
            "email": _string(user.get("EMAIL")),
            "work_position": _string(
                user.get("WORK_POSITION")
            ),
            "inn": _normalize_inn(user.get(inn_field)),
            "name_text": name_text,
            "search_text": search_text,
            "name_tokens": set(name_text.split()),
            "search_tokens": set(search_text.split()),
        })

    return prepared


def _best_token_ratio(
    expected: str,
    candidate_tokens: set[str],
) -> float:
    expected = _normalize_text(expected)

    if not expected or not candidate_tokens:
        return 0.0

    return max(
        SequenceMatcher(
            None,
            expected,
            token,
        ).ratio()
        for token in candidate_tokens
    )


def _personal_match_score(
    employee: dict,
    bitrix_user: dict,
) -> tuple[int, str]:
    employee_inn = employee["inn"]
    bitrix_inn = bitrix_user["inn"]

    surname_tokens = set(
        _normalize_text(employee["surname"]).split()
    )
    first_name_tokens = set(
        _normalize_text(employee["first_name"]).split()
    )
    middle_name_tokens = set(
        _normalize_text(employee["middle_name"]).split()
    )

    user_tokens = bitrix_user["name_tokens"]

    surname_exact = (
        bool(surname_tokens)
        and surname_tokens.issubset(user_tokens)
    )
    first_name_exact = (
        bool(first_name_tokens)
        and first_name_tokens.issubset(user_tokens)
    )
    middle_name_exact = (
        not middle_name_tokens
        or middle_name_tokens.issubset(user_tokens)
    )

    inn_exact = (
        bool(employee_inn)
        and bool(bitrix_inn)
        and employee_inn == bitrix_inn
    )

    if inn_exact and surname_exact and first_name_exact:
        return 100, "ИНН и ФИО"

    if inn_exact:
        return 96, "ИНН"

    if surname_exact and first_name_exact:
        if (
            employee_inn
            and bitrix_inn
            and employee_inn != bitrix_inn
        ):
            return 78, "ФИО, но ИНН отличается"

        if middle_name_exact:
            return 92, "Полное ФИО"

        return 88, "Фамилия и имя"

    surname_ratio = _best_token_ratio(
        employee["surname"],
        user_tokens,
    )
    first_name_ratio = _best_token_ratio(
        employee["first_name"],
        user_tokens,
    )

    if surname_ratio >= 0.88 and first_name_ratio >= 0.88:
        score = round(
            68
            + min(surname_ratio, first_name_ratio) * 10
        )
        return score, "Похожее ФИО"

    return 0, ""


def _public_match(
    bitrix_user: dict,
    score: int,
    matched_by: str,
) -> dict:
    return {
        "id": bitrix_user["id"],
        "display": bitrix_user["display"],
        "active": bitrix_user["active"],
        "email": bitrix_user["email"],
        "work_position": bitrix_user["work_position"],
        "inn": bitrix_user["inn"],
        "score": score,
        "matched_by": matched_by,
    }


def _match_person(
    employee: dict,
    bitrix_users: list[dict],
) -> tuple[list[dict], list[dict]]:
    exact = []
    similar = []

    for bitrix_user in bitrix_users:
        score, matched_by = _personal_match_score(
            employee,
            bitrix_user,
        )

        if not score:
            continue

        match = _public_match(
            bitrix_user,
            score,
            matched_by,
        )

        if score >= PERSON_EXACT_SCORE:
            exact.append(match)
        elif score >= PERSON_SIMILAR_SCORE:
            similar.append(match)

    sort_key = lambda item: (
        -item["score"],
        not item["active"],
        item["display"].casefold(),
        item["id"],
    )

    exact.sort(key=sort_key)
    similar.sort(key=sort_key)

    return exact, similar[:10]


def _load_store_alias_overrides() -> dict[str, list[str]]:
    raw = os.getenv(
        "DIRECTORS_STORE_ALIASES_JSON",
        "",
    ).strip()

    if not raw:
        return {}

    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise DirectorsReportError(
            "DIRECTORS_STORE_ALIASES_JSON содержит "
            "некорректный JSON."
        ) from exc

    if not isinstance(payload, dict):
        raise DirectorsReportError(
            "DIRECTORS_STORE_ALIASES_JSON должен быть "
            "JSON-объектом."
        )

    result = {}

    for key, value in payload.items():
        if isinstance(value, str):
            aliases = [value]
        elif isinstance(value, list):
            aliases = [
                _string(item)
                for item in value
                if _string(item)
            ]
        else:
            continue

        if aliases:
            result[str(key)] = aliases

    return result


def _build_store_identity(
    store: dict | None,
    smstore: int | None,
    overrides: dict[str, list[str]],
) -> dict:
    identity = {
        "label": "",
        "number": None,
        "aliases": [],
        "recognized": False,
    }

    if not store:
        return identity

    raw_name = _string(store.get("name"))
    lower_name = raw_name.casefold().replace("ё", "е")

    brand_match = re.search(
        r"(николаевский|дискаунтер)",
        lower_name,
        flags=re.IGNORECASE,
    )

    if brand_match:
        tail = lower_name[brand_match.end():]
        tail = tail.strip(" \t\r\n-–—№#")

        numeric_match = re.match(
            r"0*(\d+)(?=\D|$)",
            tail,
        )

        if numeric_match:
            raw_number = numeric_match.group(1)
            identity["number"] = int(raw_number)
            identity["label"] = raw_number
            identity["recognized"] = True
        else:
            location_match = re.match(
                r"([a-zа-яё]+(?:\s*[-–—]\s*\d+)?)",
                tail,
                flags=re.IGNORECASE,
            )

            if location_match:
                alias = location_match.group(1).strip()
                identity["aliases"].append(alias)
                identity["label"] = alias
                identity["recognized"] = True

    for alias in overrides.get(str(smstore), []):
        if alias not in identity["aliases"]:
            identity["aliases"].append(alias)

        if not identity["label"]:
            identity["label"] = alias

        identity["recognized"] = True

    return identity


def _best_alias_ratio(
    alias: str,
    bitrix_user: dict,
) -> float:
    normalized_alias = _normalize_text(alias)
    alias_tokens = normalized_alias.split()
    candidate_tokens = bitrix_user["name_text"].split()

    if not alias_tokens or not candidate_tokens:
        return 0.0

    size = len(alias_tokens)
    windows = []

    for index in range(len(candidate_tokens)):
        window = " ".join(
            candidate_tokens[index:index + size]
        )
        if window:
            windows.append(window)

    if not windows:
        return 0.0

    return max(
        SequenceMatcher(
            None,
            normalized_alias,
            window,
        ).ratio()
        for window in windows
    )


def _shared_match_score(
    identity: dict,
    bitrix_user: dict,
) -> tuple[int, str]:
    name_text = bitrix_user["name_text"]
    name_tokens = bitrix_user["name_tokens"]

    has_admin_marker = bool(
        re.search(
            r"администратор|админ",
            name_text,
            flags=re.IGNORECASE,
        )
    )

    has_brand_marker = bool(
        re.search(
            r"николаевский|дискаунтер",
            name_text,
            flags=re.IGNORECASE,
        )
    )

    store_number = identity.get("number")

    if store_number is not None:
        numbers = {
            int(item)
            for item in re.findall(r"\d+", name_text)
        }

        if (
            store_number in numbers
            and has_admin_marker
        ):
            return 100, "Администратор + номер магазина"

        if (
            store_number in numbers
            and has_brand_marker
        ):
            return 94, "Название сети + номер магазина"

    for alias in identity.get("aliases", []):
        alias_tokens = set(
            _normalize_text(alias).split()
        )

        if alias_tokens and alias_tokens.issubset(name_tokens):
            if has_admin_marker:
                return 100, "Администратор + название магазина"

            return 92, "Название магазина"

    for alias in identity.get("aliases", []):
        ratio = _best_alias_ratio(alias, bitrix_user)

        if ratio >= 0.84:
            score = round(68 + ratio * 10)
            return score, "Похожее название магазина"

    return 0, ""


def _match_shared_account(
    identity: dict,
    bitrix_users: list[dict],
) -> tuple[list[dict], list[dict]]:
    if not identity.get("recognized"):
        return [], []

    exact = []
    similar = []

    for bitrix_user in bitrix_users:
        score, matched_by = _shared_match_score(
            identity,
            bitrix_user,
        )

        if not score:
            continue

        match = _public_match(
            bitrix_user,
            score,
            matched_by,
        )

        if score >= SHARED_EXACT_SCORE:
            exact.append(match)
        elif score >= SHARED_SIMILAR_SCORE:
            similar.append(match)

    sort_key = lambda item: (
        -item["score"],
        not item["active"],
        item["display"].casefold(),
        item["id"],
    )

    exact.sort(key=sort_key)
    similar.sort(key=sort_key)

    return exact, similar[:10]


def _personal_status(
    exact: list[dict],
    similar: list[dict],
) -> str:
    if len(exact) == 1:
        return "Найдено"

    if len(exact) > 1:
        return "Несколько совпадений"

    if similar:
        return "Требует проверки"

    return "Не найдено"


def _shared_status(
    exact: list[dict],
    similar: list[dict],
) -> str:
    if exact:
        return f"Найдено: {len(exact)}"

    if similar:
        return "Требует проверки"

    return "Не найдено"


def build_directors_report(
    *,
    onec_url: str,
    onec_username: str,
    onec_password: str,
    onec_timeout: int,
    bitrix_user_get_url: str,
    bitrix_inn_field: str,
) -> dict:
    employees, onec_total = _load_onec_directors(
        url=onec_url,
        username=onec_username,
        password=onec_password,
        timeout=onec_timeout,
    )

    bitrix_raw_users = _load_all_bitrix_users(
        url=bitrix_user_get_url,
    )

    bitrix_users = _prepare_bitrix_users(
        bitrix_raw_users,
        bitrix_inn_field,
    )

    store_ids = {
        employee["smstore"]
        for employee in employees
        if employee["smstore"] is not None
    }

    stores_by_smstore = defaultdict(list)

    for store in Store.objects.filter(
        smstore__in=store_ids
    ).only(
        "id",
        "smstore",
        "name",
        "region",
        "address",
        "close_date",
    ):
        stores_by_smstore[store.smstore].append(store)

    aliases = _load_store_alias_overrides()

    prepared_stores = {}

    for smstore, stores in stores_by_smstore.items():
        stores.sort(
            key=lambda item: (
                item.close_date is not None,
                -(item.id or 0),
            )
        )

        selected = stores[0]

        prepared_stores[smstore] = {
            "id": selected.id,
            "smstore": selected.smstore,
            "name": _string(selected.name),
            "region": _string(selected.region),
            "address": _string(selected.address),
            "close_date": (
                selected.close_date.isoformat()
                if selected.close_date
                else ""
            ),
            "duplicate_count": len(stores),
        }

    shared_cache = {}
    rows = []

    for employee in employees:
        smstore = employee["smstore"]
        store = prepared_stores.get(smstore)
        warnings = []

        if smstore is None:
            warnings.append(
                "В 1С не заполнен ИдМагазина."
            )
        elif not store:
            warnings.append(
                f"В stores не найден smstore={smstore}."
            )
        else:
            if store["duplicate_count"] > 1:
                warnings.append(
                    "В stores найдено несколько записей "
                    "с одинаковым smstore."
                )

            if store["close_date"]:
                warnings.append(
                    f"У магазина указана дата закрытия: "
                    f"{store['close_date']}."
                )

        personal_exact, personal_similar = _match_person(
            employee,
            bitrix_users,
        )

        if len(personal_exact) > 1:
            warnings.append(
                "Найдено несколько личных учётных "
                "записей Bitrix24."
            )

        store_cache_key = (
            f"store:{store['id']}"
            if store
            else f"smstore:{smstore}"
        )

        if store_cache_key not in shared_cache:
            identity = _build_store_identity(
                store,
                smstore,
                aliases,
            )

            shared_exact, shared_similar = (
                _match_shared_account(
                    identity,
                    bitrix_users,
                )
            )

            shared_cache[store_cache_key] = {
                "identity": identity,
                "exact": shared_exact,
                "similar": shared_similar,
            }

        shared_result = shared_cache[store_cache_key]
        identity = shared_result["identity"]

        if store and not identity["recognized"]:
            warnings.append(
                "Не удалось автоматически выделить номер "
                "или название магазина из stores.name."
            )

        rows.append({
            "employee": employee,
            "store": store,
            "store_identity": (
                identity["label"]
                or "Не определён"
            ),
            "personal_status": _personal_status(
                personal_exact,
                personal_similar,
            ),
            "personal_exact": personal_exact,
            "personal_similar": personal_similar,
            "shared_status": _shared_status(
                shared_result["exact"],
                shared_result["similar"],
            ),
            "shared_exact": shared_result["exact"],
            "shared_similar": shared_result["similar"],
            "warnings": warnings,
        })

    rows.sort(
        key=lambda row: (
            _string(
                (row.get("store") or {}).get("region")
            ).casefold(),
            _string(
                (row.get("store") or {}).get("name")
            ).casefold(),
            row["employee"]["fio"].casefold(),
        )
    )

    unique_store_results = {}

    for row in rows:
        smstore = row["employee"]["smstore"]
        key = f"smstore:{smstore}"

        if key not in unique_store_results:
            unique_store_results[key] = row

    personal_found = sum(
        1
        for row in rows
        if row["personal_status"] == "Найдено"
    )
    personal_ambiguous = sum(
        1
        for row in rows
        if row["personal_status"] == "Несколько совпадений"
    )
    personal_possible = sum(
        1
        for row in rows
        if row["personal_status"] == "Требует проверки"
    )
    personal_missing = sum(
        1
        for row in rows
        if row["personal_status"] == "Не найдено"
    )

    shared_found = sum(
        1
        for row in unique_store_results.values()
        if row["shared_exact"]
    )
    shared_possible = sum(
        1
        for row in unique_store_results.values()
        if (
            not row["shared_exact"]
            and row["shared_similar"]
        )
    )
    shared_missing = sum(
        1
        for row in unique_store_results.values()
        if (
            not row["shared_exact"]
            and not row["shared_similar"]
        )
    )

    summary = {
        "onec_total_employees": onec_total,
        "directors_1c": len(rows),
        "stores_1c": len(unique_store_results),
        "stores_mapped": len({
            row["employee"]["smstore"]
            for row in rows
            if row["store"]
        }),
        "store_rows_not_mapped": sum(
            1
            for row in rows
            if not row["store"]
        ),
        "bitrix_users": len(bitrix_users),
        "personal_found": personal_found,
        "personal_ambiguous": personal_ambiguous,
        "personal_possible": personal_possible,
        "personal_missing": personal_missing,
        "shared_found": shared_found,
        "shared_possible": shared_possible,
        "shared_missing": shared_missing,
    }

    return {
        "generated_at": timezone.localtime().isoformat(),
        "summary": summary,
        "rows": rows,
    }


def _excel_safe(value: Any) -> Any:
    if value is None:
        return ""

    if not isinstance(value, str):
        return value

    if value.startswith(("=", "+", "-", "@")):
        return "'" + value

    return value


def _matches_to_text(
    exact: list[dict],
    similar: list[dict],
) -> str:
    parts = []

    for match in exact:
        active = "активен" if match["active"] else "неактивен"
        parts.append(
            f"ТОЧНО: {match['display']} "
            f"[ID {match['id']}; {active}; "
            f"{match['matched_by']}]"
        )

    for match in similar:
        active = "активен" if match["active"] else "неактивен"
        parts.append(
            f"ПОХОЖЕ: {match['display']} "
            f"[ID {match['id']}; {active}; "
            f"{match['matched_by']}]"
        )

    return "\n".join(parts)


def _style_worksheet(
    worksheet,
    *,
    freeze: str = "A2",
):
    worksheet.freeze_panes = freeze

    if worksheet.max_row >= 1:
        for cell in worksheet[1]:
            cell.font = Font(
                bold=True,
                color="FFFFFF",
            )
            cell.fill = PatternFill(
                "solid",
                fgColor="1F4E78",
            )
            cell.alignment = Alignment(
                horizontal="center",
                vertical="center",
                wrap_text=True,
            )

    thin = Side(
        style="thin",
        color="D9E2F3",
    )

    for row in worksheet.iter_rows():
        for cell in row:
            cell.border = Border(
                left=thin,
                right=thin,
                top=thin,
                bottom=thin,
            )
            cell.alignment = Alignment(
                vertical="top",
                wrap_text=True,
            )

    for column_index in range(
        1,
        worksheet.max_column + 1,
    ):
        max_length = 0

        for row_index in range(
            1,
            min(worksheet.max_row, 300) + 1,
        ):
            value = worksheet.cell(
                row=row_index,
                column=column_index,
            ).value

            if value is not None:
                max_length = max(
                    max_length,
                    min(len(str(value)), 60),
                )

        worksheet.column_dimensions[
            get_column_letter(column_index)
        ].width = max(12, min(max_length + 2, 60))

    worksheet.auto_filter.ref = worksheet.dimensions


def write_directors_report_xlsx(
    report: dict,
    target_path: Path,
):
    target_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    workbook = Workbook()

    summary_ws = workbook.active
    summary_ws.title = "Сводка"

    summary_ws.append([
        "Показатель",
        "Значение",
    ])

    summary_labels = {
        "onec_total_employees": "Всего строк получено из 1С",
        "directors_1c": "Директора и администраторы из 1С",
        "stores_1c": "Уникальных магазинов в 1С",
        "stores_mapped": "Магазинов найдено в stores",
        "store_rows_not_mapped": "Строк без магазина в stores",
        "bitrix_users": "Пользователей загружено из Bitrix24",
        "personal_found": "Личные записи найдены",
        "personal_ambiguous": "Несколько личных совпадений",
        "personal_possible": "Похожие личные записи",
        "personal_missing": "Личные записи не найдены",
        "shared_found": "Общие записи магазинов найдены",
        "shared_possible": "Похожие общие записи",
        "shared_missing": "Общие записи не найдены",
    }

    summary_ws.append([
        "Дата формирования",
        report["generated_at"],
    ])

    for key, label in summary_labels.items():
        summary_ws.append([
            label,
            report["summary"].get(key, 0),
        ])

    _style_worksheet(summary_ws)

    details_ws = workbook.create_sheet(
        "По директорам"
    )

    details_ws.append([
        "Регион",
        "smstore / ИдМагазина",
        "Название магазина",
        "Адрес магазина",
        "Идентификатор для поиска",
        "ФИО в 1С",
        "ИНН",
        "Должность",
        "Подразделение",
        "Телефон",
        "Почта",
        "Статус личной записи",
        "Личные записи Bitrix24",
        "Статус общей записи",
        "Общие записи Bitrix24",
        "Предупреждения",
    ])

    for row in report["rows"]:
        employee = row["employee"]
        store = row.get("store") or {}

        details_ws.append([
            _excel_safe(store.get("region", "")),
            employee.get("smstore") or "",
            _excel_safe(store.get("name", "")),
            _excel_safe(store.get("address", "")),
            _excel_safe(row["store_identity"]),
            _excel_safe(employee["fio"]),
            _excel_safe(employee["inn"]),
            _excel_safe(employee["position"]),
            _excel_safe(employee["department"]),
            _excel_safe(employee["phone"]),
            _excel_safe(employee["email"]),
            _excel_safe(row["personal_status"]),
            _excel_safe(
                _matches_to_text(
                    row["personal_exact"],
                    row["personal_similar"],
                )
            ),
            _excel_safe(row["shared_status"]),
            _excel_safe(
                _matches_to_text(
                    row["shared_exact"],
                    row["shared_similar"],
                )
            ),
            _excel_safe("\n".join(row["warnings"])),
        ])

    _style_worksheet(details_ws)

    problem_ws = workbook.create_sheet(
        "Требует проверки"
    )

    problem_ws.append([
        "smstore",
        "Магазин",
        "ФИО",
        "Проблема",
        "Возможные совпадения",
    ])

    for row in report["rows"]:
        employee = row["employee"]
        store = row.get("store") or {}

        if row["personal_status"] != "Найдено":
            problem_ws.append([
                employee.get("smstore") or "",
                _excel_safe(store.get("name", "")),
                _excel_safe(employee["fio"]),
                _excel_safe(
                    "Личная запись: "
                    + row["personal_status"]
                ),
                _excel_safe(
                    _matches_to_text(
                        row["personal_exact"],
                        row["personal_similar"],
                    )
                ),
            ])

        if not row["shared_exact"]:
            problem_ws.append([
                employee.get("smstore") or "",
                _excel_safe(store.get("name", "")),
                _excel_safe(employee["fio"]),
                _excel_safe(
                    "Общая запись: "
                    + row["shared_status"]
                ),
                _excel_safe(
                    _matches_to_text(
                        row["shared_exact"],
                        row["shared_similar"],
                    )
                ),
            ])

    _style_worksheet(problem_ws)

    workbook.save(target_path)
