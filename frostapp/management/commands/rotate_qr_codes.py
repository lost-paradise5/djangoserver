from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.db import connection
from django.db.models import Q



from frostapp.models import User, QRCode, UKMUser, Store, OpenInSystem
from frostapp.services.ukm_rotation_runs import UkmRotationRunRecorder

from frostapp.views import (
    ensure_plain_inn,
    build_user_password,
    _set_password_pg,
    _update_store_mysql_and_xml_for_single_store,
    _write_converter_user_and_signal,
    get_trm_employee_id,
    get_next_trm_employee_id,
    connect_ukm,
    _send_max_log_async,
    get_ukm5_full_xml_store_ids,
    get_ukm5_employee_id,
    get_next_ukm5_employee_id,
    is_ukm5_store,
    clear_ukm_store_runtime_caches,
    TRM_SMALL_MAX,
)



from collections import defaultdict
from itertools import islice
from zoneinfo import ZoneInfo
from datetime import timezone as dt_tz
import logging
import os
import time
import json
import requests



logger = logging.getLogger("ukm_logger")
EXCLUDED_ROTATION_DEPARTMENT_ID = 458



def _read_env_value(name: str, default: str = "", env_path: str = "/app/.env") -> str:
    """
    Читает настройку сначала из окружения контейнера, затем из /app/.env.
    Это позволяет команде работать и с docker --env-file, и с read-only mount .env.
    """
    value = os.getenv(name)
    if value is not None and str(value).strip() != "":
        return str(value).strip()

    try:
        path = os.path.abspath(env_path)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as env_file:
                for raw_line in env_file:
                    line = (raw_line or "").strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("export "):
                        line = line[len("export "):].strip()
                    if "=" not in line:
                        continue
                    key, raw_value = line.split("=", 1)
                    if key.strip() != name:
                        continue
                    result = raw_value.strip()
                    if (
                        len(result) >= 2
                        and result[0] == result[-1]
                        and result[0] in {"'", '"'}
                    ):
                        result = result[1:-1]
                    return result
    except Exception as exc:
        logger.warning(
            "[ROTATE][ENV] Не удалось прочитать %s из %s: %s",
            name,
            env_path,
            exc,
        )

    return default


def _read_env_int(name: str, default: int) -> int:
    raw = _read_env_value(name, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "[ROTATE][ENV] %s=%r не является числом; использую %s",
            name,
            raw,
            default,
        )
        return int(default)


ONEC_WORKING_EMPLOYEES_URL = _read_env_value(
    "ONEC_WORKING_EMPLOYEES_URL",
    "http://192.168.17.26/zupcorp_http/hs/API/Get_WorkingEmployees",
)
ONEC_WORKING_EMPLOYEES_AUTH_USER = _read_env_value(
    "ONEC_WORKING_EMPLOYEES_AUTH_USER",
    "",
)
ONEC_WORKING_EMPLOYEES_AUTH_PASSWORD = _read_env_value(
    "ONEC_WORKING_EMPLOYEES_AUTH_PASSWORD",
    "",
)
ROTATION_ONEC_TIMEOUT = _read_env_int(
    "ROTATION_ONEC_TIMEOUT",
    _read_env_int("ONEC_WORKING_EMPLOYEES_TIMEOUT", 600),
)



def batched(qs, size):
    """
    Итерация по queryset кусками без долгоживущего
    серверного PostgreSQL-курсора.
    """
    size = max(1, int(size))
    last_user_id = None

    while True:
        page_qs = qs

        if last_user_id is not None:
            page_qs = page_qs.filter(id__gt=last_user_id)

        chunk = list(
            page_qs
            .order_by("id")[:size]
        )

        if not chunk:
            break

        yield chunk
        last_user_id = int(chunk[-1].id)





class Command(BaseCommand):

    """
    Ежедневная ротация QR/пароля для пользователей с tg_id и доступами
    в целевые магазины из UKM5_FULL_XML_STORE_IDS.
    """


    help = "Ежедневное обновление QR для всех пользователей с доступом к целевым магазинам УКМ."


    def add_arguments(self, parser):
        parser.add_argument(
            "--system",
            choices=("ukm4", "ukm5"),
            required=True,
            help=(
                "ukm4: создать новый пароль, записать его в PostgreSQL и "
                "обновить только УКМ-4; ukm5: взять сохранённый пароль из "
                "open_in_system и обновить только УКМ-5"
            ),
        )
        parser.add_argument(
            "--run-id",
            help="UUID запуска из веб-интерфейса (необязательно)",
        )
        parser.add_argument('--batch-size', type=int, default=100)
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Ничего не менять, только логировать, кого бы крутили'
        )

        parser.add_argument(
            '--only-active',
            action='store_true',
            help='Обрабатывать только active=True'
        )

        parser.add_argument(
            '--only-with-qr',
            action='store_true',
            help='Обрабатывать только тех, у кого уже есть QR'
        )

        parser.add_argument(
            '--user-id',
            type=int,
            help='Ограничить обработку одним user_id'
        )

        parser.add_argument('--tz', type=str, default=os.getenv('ROTATION_TZ', 'Europe/Stockholm'))

        parser.add_argument(

            '--idempotent',
            action='store_true',
            help='Пропускать пользователя, если сегодня уже есть свежий QR'

        )

        parser.add_argument(
            '--ukm5-verify',
            action='store_true',
            help=(
                'Ждать подтверждения импорта в srvdata. По умолчанию '
                'ротация работает быстро: отправляет один POST и не опрашивает '
                'таблицы user/user_card.'
            ),
        )



    def handle(self, *args, **opts):
        target_system = str(opts["system"]).lower()
        self.target_system = target_system
        recorder = UkmRotationRunRecorder(opts.get("run_id"))

        if not self._acquire_lock():
            message = "Другой запуск ротации уже идёт — выхожу."
            recorder.fail(message)
            raise CommandError(message)



        started_at = time.monotonic()



        try:

            bs = opts['batch_size']
            tz = ZoneInfo(opts['tz'])
            today_local = timezone.now().astimezone(tz).date()
            ukm5_submit_only = not bool(opts.get('ukm5_verify'))



            # anchor_store_ids = sorted(
            #     int(x)
            #     for x in (get_ukm5_full_xml_store_ids() or {2013})
            # )

            # if target_system == "ukm5":
            #     target_store_ids = [
            #         store_id
            #         for store_id in anchor_store_ids
            #         if is_ukm5_store(store_id)
            #     ]
            # else:
            #     target_store_ids = list(anchor_store_ids)
            # ukm-rotation-worker работает постоянно, поэтому перед каждым новым
            # запуском сбрасываем сведения, оставшиеся от предыдущего прогона.
            clear_ukm_store_runtime_caches()
            
            anchor_store_ids = sorted(
                int(x)
                for x in (get_ukm5_full_xml_store_ids() or {2013})
            )
            
            if target_system == "ukm5":
                target_store_ids = [
                    store_id
                    for store_id in anchor_store_ids
                    if is_ukm5_store(store_id)
                ]
            
                logger.info(
                    "[ROTATE][UKM5][STORE_DISCOVERY] checked=%s ukm5=%s not_ukm5=%s",
                    anchor_store_ids,
                    target_store_ids,
                    sorted(set(anchor_store_ids) - set(target_store_ids)),
                )
            else:
                target_store_ids = list(anchor_store_ids)

            if not target_store_ids:
                raise RuntimeError(
                    f"Для режима {target_system.upper()} не найдено ни одного "
                    "целевого магазина"
                )

            allowed_store_ids = set(target_store_ids)

            

            target_user_ids = UKMUser.objects.filter(
                storeid__in=target_store_ids
            ).values("user_id")

            

            base_qs = User.objects.filter(
                id__in=target_user_ids
            ).distinct()

            

            if opts["only_active"]:
                base_qs = base_qs.filter(active=True)

            

            if opts["only_with_qr"]:
                base_qs = base_qs.filter(
                    id__in=QRCode.objects.values("user_id").distinct()
                )

            

            if opts.get("user_id"):
                base_qs = base_qs.filter(id=opts["user_id"])

            

            # Эти пользователи отдельно попадут в отчёт MAX,

            # но кандидатами считаться и обрабатываться не будут.

            excluded_department_qs = base_qs.filter(
                department_id=EXCLUDED_ROTATION_DEPARTMENT_ID
            )

            

            qs = base_qs.exclude(
                department_id=EXCLUDED_ROTATION_DEPARTMENT_ID
            ).order_by("id")

            

            excluded_department_details = self._collect_excluded_department_users(
                excluded_qs=excluded_department_qs,
                allowed_store_ids=allowed_store_ids,
            )

            

            total = qs.count()

            recorder.start(
                total_users=total,
                target_store_ids=target_store_ids,
                options={
                    "system": target_system,
                    "tz": opts["tz"],
                    "batch_size": bs,
                    "dry_run": bool(opts["dry_run"]),
                    "only_active": bool(opts["only_active"]),
                    "only_with_qr": bool(opts["only_with_qr"]),
                    "user_id": opts.get("user_id"),
                    "idempotent": bool(opts["idempotent"]),
                    "ukm5_verify": bool(opts.get("ukm5_verify")),
                },
            )


            # Fail-closed: ответ 1С обязателен до любых изменений паролей.
            self._onec_by_employee_inn = {}
            self._onec_stats = {
                "rows_received": 0,
                "rows_indexed": 0,
                "rows_skipped": 0,
                "unique_employee_inn": 0,
                "elapsed_sec": 0.0,
            }
            self._trm_store_inn_map = {}

            if total > 0:
                self.stdout.write(
                    "Загружаю сотрудников из 1С для проверки ИНН организаций..."
                )

                try:
                    (
                        self._onec_by_employee_inn,
                        self._onec_stats,
                    ) = self._load_onec_working_employees()
                except Exception as exc:
                    elapsed = time.monotonic() - started_at
                    error_text = (
                        "Ночная ротация остановлена до внесения изменений. "
                        f"Не удалось получить сотрудников из 1С: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    logger.error(
                        "[ROTATE][ORG_INN][FATAL] %s",
                        error_text,
                        exc_info=True,
                    )
                    self.stderr.write(self.style.ERROR(error_text))
                    raise CommandError(error_text) from exc

                self.stdout.write(
                    "Загружаю ИНН организаций из trm_in_store..."
                )
                self._trm_store_inn_map = self._load_trm_store_inns(
                    target_store_ids
                )



            self.stdout.write(
                f"START rotate_qr_codes | system={target_system} | "
                f"date={today_local.isoformat()} | tz={opts['tz']} | "

                f"targets={','.join(map(str, target_store_ids))} | "

                f"candidates={total} | "

                f"excluded_department_{EXCLUDED_ROTATION_DEPARTMENT_ID}="

                f"{len(excluded_department_details)} | "

                f"batch={bs} | dry={opts['dry_run']} | "

                f"idempotent={opts['idempotent'] if target_system == 'ukm4' else False} | "
                f"ukm5_mode={'n/a' if target_system == 'ukm4' else ('submit_only' if ukm5_submit_only else 'verify')}"
            )



            rotated = partial = skipped = failed = 0
            details = []
            processed = 0


            for chunk in batched(qs, bs):
                for user in chunk:
                    processed += 1

                    status, info = self._rotate_one_user(
                        user=user,
                        today_local=today_local,
                        tz=tz,
                        idempotent=opts["idempotent"],
                        dry_run=opts["dry_run"],
                        allowed_store_ids=allowed_store_ids,
                        ukm5_submit_only=ukm5_submit_only,
                        target_system=target_system,
                    )

                    details.append(info)



                    if status == 'rotated':
                        rotated += 1

                    elif status == 'partial':
                        partial += 1

                    elif status == 'skipped':
                        skipped += 1

                    else:
                        failed += 1

                    recorder.record_user(
                        info=info,
                        processed_users=processed,
                        rotated_users=rotated,
                        partial_users=partial,
                        skipped_users=skipped,
                        failed_users=failed,
                    )


                    self.stdout.write(self._format_user_run_line(processed, total, info))



            elapsed = time.monotonic() - started_at



            self.stdout.write(
                self.style.SUCCESS(
                    f"FINISH rotate_qr_codes | ok={rotated} | partial={partial} | "
                    f"skipped={skipped} | failed={failed} | elapsed={elapsed:.1f}s"
                )
            )



            self._send_max_summary(
                today_local=today_local,
                tz_name=opts['tz'],
                total=total,
                rotated=rotated,
                partial=partial,
                skipped=skipped,
                failed=failed,
                details=details,
                dry_run=opts['dry_run'],
                elapsed_sec=elapsed,
                target_store_ids=target_store_ids,
                target_system=target_system,
                excluded_department_details=excluded_department_details,
                excluded_department_id=EXCLUDED_ROTATION_DEPARTMENT_ID,
            )

            if not opts["dry_run"] and (failed > 0 or partial > 0 or skipped > 0):
                run_status = "partial"
            else:
                run_status = "success"

            recorder.finish(
                status=run_status,
                processed_users=processed,
                rotated_users=rotated,
                partial_users=partial,
                skipped_users=skipped,
                failed_users=failed,
                elapsed_sec=elapsed,
            )



        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            recorder.fail(error_text)
            logger.exception(
                "[ROTATE][FATAL] system=%s error=%s",
                target_system,
                exc,
            )
            _send_max_log_async(
                f"⛔ Ротация {target_system.upper()} завершилась ошибкой\n\n"
                f"{error_text}"
            )
            raise
        finally:
            self._release_lock()







    @staticmethod
    def _digits_only(value) -> str:
        return "".join(
            ch for ch in str(value or "")
            if ch.isdigit()
        )

    @staticmethod
    def _field_value(row: dict, *names):
        if not isinstance(row, dict):
            return None

        normalized = {
            str(key).strip().casefold().replace("ё", "е"): value
            for key, value in row.items()
        }

        for name in names:
            key = str(name).strip().casefold().replace("ё", "е")
            if key in normalized:
                return normalized[key]

        return None

    def _extract_onec_records(self, payload) -> list[dict]:
        """
        Поддерживает наиболее вероятные формы JSON от 1С:
          - обычный список;
          - {"data": [...]}, {"value": [...]}, {"items": [...]};
          - объект с числовыми ключами: {"3395": {...}, ...};
          - одиночный объект сотрудника.
        """
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]

        if not isinstance(payload, dict):
            return []

        for key in (
            "data",
            "value",
            "items",
            "employees",
            "result",
            "Сотрудники",
        ):
            nested = payload.get(key)
            if isinstance(nested, (list, dict)):
                records = self._extract_onec_records(nested)
                if records:
                    return records

        values = list(payload.values())
        if values and all(isinstance(item, dict) for item in values):
            return values

        employee_inn = self._field_value(payload, "ИНН", "Инн", "inn")
        if employee_inn is not None:
            return [payload]

        return []

    def _load_onec_working_employees(self) -> tuple[dict[str, list[dict]], dict]:
        """
        Загружает Get_WorkingEmployees ОДИН РАЗ за запуск и строит индекс:
            ИНН сотрудника -> список его записей из 1С.

        При недоступности 1С выбрасывает исключение. Ротация работает fail-closed:
        без ответа 1С ни один пароль не меняется.
        """
        auth = None
        auth_user = ONEC_WORKING_EMPLOYEES_AUTH_USER
        auth_password = ONEC_WORKING_EMPLOYEES_AUTH_PASSWORD

        if auth_user or auth_password:
            if not auth_user or not auth_password:
                raise RuntimeError(
                    "Для 1С должны быть одновременно настроены "
                    "ONEC_WORKING_EMPLOYEES_AUTH_USER и "
                    "ONEC_WORKING_EMPLOYEES_AUTH_PASSWORD"
                )
            auth = (auth_user, auth_password)

        started_at = time.monotonic()

        response = requests.get(
            ONEC_WORKING_EMPLOYEES_URL,
            auth=auth,
            headers={"Accept": "application/json"},
            timeout=ROTATION_ONEC_TIMEOUT,
        )
        response.raise_for_status()

        try:
            payload = response.json()
        except Exception:
            payload = json.loads(
                response.content.decode("utf-8-sig")
            )

        records = self._extract_onec_records(payload)
        if not records:
            raise RuntimeError(
                "1С вернула пустой или неподдерживаемый список сотрудников"
            )

        index: dict[str, list[dict]] = defaultdict(list)
        accepted_rows = 0
        skipped_rows = 0

        for row in records:
            employee_inn = self._digits_only(
                self._field_value(
                    row,
                    "ИНН",
                    "Инн",
                    "inn",
                    "employee_inn",
                )
            )

            if len(employee_inn) not in (10, 12):
                skipped_rows += 1
                continue

            # Никакой фильтрации по полям «Состояние» и
            # «ОформленПоТрудовомуДоговору».
            # В индекс попадает любая строка, которую вернул endpoint 1С,
            # если в ней присутствует корректный ИНН сотрудника.

            company_inn = self._digits_only(
                self._field_value(
                    row,
                    "ИннОрганизации",
                    "ИННОрганизации",
                    "organization_inn",
                    "company_inn",
                )
            )

            store_id_raw = self._field_value(
                row,
                "ИдМагазина",
                "ИДМагазина",
                "store_id",
                "storeid",
            )
            try:
                onec_store_id = int(str(store_id_raw).strip())
            except (TypeError, ValueError):
                onec_store_id = None

            index[employee_inn].append({
                "employee_inn": employee_inn,
                "company_inn": company_inn,
                "store_id": onec_store_id,
                "position": str(
                    self._field_value(row, "Должность", "position") or ""
                ).strip(),
                "department": str(
                    self._field_value(row, "Подразделение", "department") or ""
                ).strip(),
            })
            accepted_rows += 1

        if not index:
            raise RuntimeError(
                "После проверки ответа 1С не осталось сотрудников с корректным ИНН"
            )

        elapsed = time.monotonic() - started_at
        stats = {
            "rows_received": len(records),
            "rows_indexed": accepted_rows,
            "rows_skipped": skipped_rows,
            "unique_employee_inn": len(index),
            "elapsed_sec": round(elapsed, 2),
        }

        logger.info(
            "[ROTATE][ORG_INN][1C] loaded rows=%s indexed=%s "
            "skipped=%s unique_inn=%s elapsed=%.2fs",
            stats["rows_received"],
            stats["rows_indexed"],
            stats["rows_skipped"],
            stats["unique_employee_inn"],
            elapsed,
        )

        return dict(index), stats

    def _load_trm_store_inns(self, store_ids: list[int]) -> dict[int, dict]:
        """
        Загружает trm_in_store.inn для всех целевых магазинов.
        Магазины группируются по stores.ukm4ip, поэтому на один кассовый
        сервер выполняется одно подключение и один SELECT.
        """
        target_ids = sorted({int(store_id) for store_id in store_ids})
        result: dict[int, dict] = {
            store_id: {
                "ok": False,
                "store_id": store_id,
                "host": None,
                "store_name": "",
                "company_inn": "",
                "raw_company_inn": "",
                "error": "Магазин не найден в PostgreSQL stores",
            }
            for store_id in target_ids
        }

        store_rows = list(
            Store.objects
            .filter(ukm4store__in=target_ids)
            .values("ukm4store", "ukm4ip", "name")
            .order_by("ukm4store")
        )

        host_groups: dict[str, list[int]] = defaultdict(list)

        for row in store_rows:
            store_id = int(row["ukm4store"])
            host = str(row.get("ukm4ip") or "").strip()
            store_name = str(row.get("name") or "").strip()

            result[store_id].update({
                "host": host or None,
                "store_name": store_name,
            })

            if not host:
                result[store_id]["error"] = "В stores.ukm4ip не заполнен IP"
                continue

            host_groups[host].append(store_id)
            result[store_id]["error"] = (
                "Активная запись trm_in_store для магазина не найдена"
            )

        for host, host_store_ids in host_groups.items():
            conn = cur = None
            try:
                conn = connect_ukm(host=host)
                cur = conn.cursor()

                placeholders = ",".join(["%s"] * len(host_store_ids))
                sql = (
                    "SELECT store_id, inn "
                    "FROM trm_in_store "
                    f"WHERE store_id IN ({placeholders}) "
                    "AND COALESCE(deleted, 0) = 0"
                )
                cur.execute(sql, tuple(host_store_ids))

                for row in cur.fetchall() or []:
                    try:
                        store_id = int(row.get("store_id"))
                    except (TypeError, ValueError):
                        continue

                    raw_company_inn = str(row.get("inn") or "").strip()
                    company_inn = self._digits_only(raw_company_inn)

                    if len(company_inn) not in (10, 12):
                        result[store_id].update({
                            "ok": False,
                            "raw_company_inn": raw_company_inn,
                            "company_inn": company_inn,
                            "error": (
                                "В trm_in_store.inn отсутствует корректный "
                                "ИНН организации"
                            ),
                        })
                        continue

                    result[store_id].update({
                        "ok": True,
                        "raw_company_inn": raw_company_inn,
                        "company_inn": company_inn,
                        "error": "",
                    })

            except Exception as exc:
                error_text = (
                    f"Ошибка чтения trm_in_store на {host}: "
                    f"{type(exc).__name__}: {exc}"
                )
                logger.error(
                    "[ROTATE][ORG_INN][TRM] host=%s stores=%s error=%s",
                    host,
                    host_store_ids,
                    exc,
                    exc_info=True,
                )
                for store_id in host_store_ids:
                    result[store_id].update({
                        "ok": False,
                        "error": error_text,
                    })
            finally:
                try:
                    if cur:
                        cur.close()
                    if conn:
                        conn.close()
                except Exception:
                    pass

        ok_count = sum(1 for item in result.values() if item.get("ok"))
        error_count = len(result) - ok_count
        logger.info(
            "[ROTATE][ORG_INN][TRM] stores=%s ok=%s errors=%s servers=%s",
            len(result),
            ok_count,
            error_count,
            len(host_groups),
        )

        return result

    def _select_onec_company_inn(
        self,
        employee_inn: str,
        store_id: int,
    ) -> dict:
        records = list(
            (getattr(self, "_onec_by_employee_inn", {}) or {})
            .get(employee_inn, [])
        )

        if not records:
            return {
                "ok": False,
                "technical_error": False,
                "status": "employee_not_found_1c",
                "company_inn": "",
                "source": "",
                "message": "Сотрудник с этим ИНН не найден в ответе 1С",
            }

        exact_store_records = [
            row for row in records
            if row.get("store_id") == int(store_id)
        ]
        selected_records = exact_store_records or records
        source = (
            "employee_inn+store_id"
            if exact_store_records
            else "employee_inn_unique_company"
        )

        company_inns = sorted({
            str(row.get("company_inn") or "").strip()
            for row in selected_records
            if str(row.get("company_inn") or "").strip()
        })

        if not company_inns:
            return {
                "ok": False,
                "technical_error": False,
                "status": "company_inn_missing_1c",
                "company_inn": "",
                "source": source,
                "message": "В записи сотрудника 1С не заполнен ИннОрганизации",
            }

        if len(company_inns) > 1:
            return {
                "ok": False,
                "technical_error": False,
                "status": "company_inn_ambiguous_1c",
                "company_inn": "",
                "source": source,
                "message": (
                    "В 1С найдено несколько разных ИНН организаций: "
                    + ", ".join(company_inns)
                ),
            }

        return {
            "ok": True,
            "technical_error": False,
            "status": "onec_company_inn_resolved",
            "company_inn": company_inns[0],
            "source": source,
            "message": "ИНН организации сотрудника определён",
        }

    def _check_store_company_inn(
        self,
        employee_inn: str,
        store_id: int,
    ) -> dict:
        onec_result = self._select_onec_company_inn(
            employee_inn=employee_inn,
            store_id=store_id,
        )

        store_result = (
            getattr(self, "_trm_store_inn_map", {}) or {}
        ).get(int(store_id))

        base = {
            "store_id": int(store_id),
            "employee_inn": employee_inn,
            "onec_company_inn": onec_result.get("company_inn") or "",
            "trm_company_inn": "",
            "onec_source": onec_result.get("source") or "",
            "host": None,
            "store_name": "",
        }

        if not onec_result.get("ok"):
            return {
                **base,
                "allowed": False,
                "technical_error": bool(onec_result.get("technical_error")),
                "status": onec_result.get("status") or "onec_error",
                "message": onec_result.get("message") or "Ошибка данных 1С",
            }

        if not store_result:
            return {
                **base,
                "allowed": False,
                "technical_error": True,
                "status": "trm_store_not_loaded",
                "message": "Данные trm_in_store для магазина не загружены",
            }

        base.update({
            "trm_company_inn": store_result.get("company_inn") or "",
            "host": store_result.get("host"),
            "store_name": store_result.get("store_name") or "",
        })

        if not store_result.get("ok"):
            return {
                **base,
                "allowed": False,
                "technical_error": True,
                "status": "trm_store_inn_error",
                "message": store_result.get("error") or "Ошибка trm_in_store",
            }

        onec_company_inn = onec_result["company_inn"]
        trm_company_inn = store_result["company_inn"]

        if onec_company_inn != trm_company_inn:
            return {
                **base,
                "allowed": False,
                "technical_error": False,
                "status": "company_inn_mismatch",
                "message": (
                    f"ИНН организации не совпал: "
                    f"1С={onec_company_inn}, "
                    f"trm_in_store={trm_company_inn}"
                ),
            }

        return {
            **base,
            "allowed": True,
            "technical_error": False,
            "status": "matched",
            "message": (
                f"ИНН организации совпал: {onec_company_inn}"
            ),
        }

    def _append_blocked_store_result(
        self,
        info: dict,
        *,
        store_id: int,
        role_id: int,
        org_check: dict,
    ) -> None:
        store_status = (
            "error" if org_check.get("technical_error") else "warning"
        )
        summary = org_check.get("message") or "Проверка ИНН не пройдена"

        info["store_results"].append({
            "storeid": int(store_id),
            "roleid": int(role_id),
            "cashier_id": None,
            "found_in_trm": False,
            "store_status": store_status,
            "store_summary": summary,
            "org_inn_check": org_check,
            "sync": {
                "ukm4": {
                    "status": "skipped_org_inn_check",
                    "error": summary,
                },
                "ukm5": {
                    "status": "skipped_org_inn_check",
                    "error": summary,
                },
            },
        })
        info["store_stats"][store_status] += 1

        logger.warning(
            "[ROTATE][ORG_INN][BLOCK] user_id=%s storeid=%s "
            "roleid=%s status=%s message=%s",
            info.get("user_id"),
            store_id,
            role_id,
            org_check.get("status"),
            summary,
        )

    def _collect_excluded_department_users(
        self,
        excluded_qs,
        allowed_store_ids: set[int],
    ) -> list[dict]:

        """
        Собирает пользователей department_id=458 для отчёта MAX.
    
        Эти пользователи не являются кандидатами и не проходят через:
          - _set_password_pg;
          - поиск/выделение trm_in_users.id;
          - import4.users;
          - import4.signal;
          - УКМ-5.
        """

        user_rows = list(

            excluded_qs
            .order_by("full_name", "id")
            .values(
                "id",
                "full_name",
                "phone",
                "employee_id",
                "department_id",
            )
        )

    

        if not user_rows:
            return []

    

        user_ids = [int(row["id"]) for row in user_rows]

    
        links_qs = UKMUser.objects.filter(user_id__in=user_ids)

    

        if allowed_store_ids:
            links_qs = links_qs.filter(
                storeid__in=sorted(allowed_store_ids)
            )

    

        links_by_user = defaultdict(list)

    

        for link in (
            links_qs
            .values("user_id", "storeid", "roleid")
            .order_by("user_id", "storeid", "roleid")
        ):

            links_by_user[int(link["user_id"])].append({
                "storeid": int(link["storeid"]),
                "roleid": int(link["roleid"]),
            })

    
        result = []

    

        for row in user_rows:
            user_id = int(row["id"])

    
            result.append({
                "user_id": user_id,
                "fio": (row.get("full_name") or "").strip()
                       or "ФИО не указано",
                "phone": (row.get("phone") or "").strip() or "—",
                "inn": (row.get("employee_id") or "").strip(),
                "department_id": row.get("department_id"),
                "stores": links_by_user.get(user_id, []),
            })

    

        return result



    @staticmethod
    def _get_saved_ukm_password(user_id: int) -> str:
        """Читает уже сохранённый открытый пароль, не изменяя PostgreSQL и QR."""
        row = (
            OpenInSystem.objects
            .filter(user_id=int(user_id), system_id=9, status=True)
            .exclude(password__isnull=True)
            .exclude(password="")
            .order_by("-id")
            .values("id", "password")
            .first()
        )
        if not row:
            raise LookupError(
                "В open_in_system нет активного пароля system_id=9. "
                "Сначала выполните обновление в УКМ-4."
            )
        password = str(row.get("password") or "")
        if not password:
            raise LookupError(
                "В open_in_system.password пусто. "
                "Сначала выполните обновление в УКМ-4."
            )
        return password



    def _rotate_one_user(
        self,
        user,
        today_local,
        tz,
        idempotent: bool,
        dry_run: bool,
        allowed_store_ids: set[int] | None = None,
        ukm5_submit_only: bool = True,
        target_system: str = "ukm4",
    ):
        started_at = time.monotonic()

        info = {
            "user_id": user.id,
            "fio": (user.full_name or "").strip(),
            "inn": (user.employee_id or "").strip(),
            "phone": (getattr(user, "phone", None) or "").strip(),
            "tg_id": getattr(user, "tg_id", None),
            "max_id": getattr(user, "max_id", None),
            "status": "",
            "error": "",
            "stores": [],
            "cashier_id": None,
            "store_results": [],
            "store_stats": {"ok": 0, "warning": 0, "error": 0},
            "org_inn_stats": {"matched": 0, "blocked": 0, "errors": 0},
            "eligible_store_count": 0,
            "blocked_store_count": 0,
            "duration_sec": 0.0,
            "department_id": getattr(user, "department_id", None),
            "target_system": target_system,
        }

        try:
            department_id = int(
                getattr(user, "department_id", 0) or 0
            )

            if department_id == EXCLUDED_ROTATION_DEPARTMENT_ID:
                info["status"] = "skipped_excluded_department"
                info["error"] = (
                    f"Исключён из ротации: department_id="
                    f"{EXCLUDED_ROTATION_DEPARTMENT_ID}"
                )
                info["duration_sec"] = round(
                    time.monotonic() - started_at,
                    2,
                )
                logger.info(
                    f"[ROTATE][USER] user_id={user.id} "
                    f"status=skipped reason=excluded_department "
                    f"department_id={department_id}"
                )
                return "skipped", info

            if idempotent and target_system == "ukm4":
                last_qr = (
                    QRCode.objects
                    .filter(user=user)
                    .order_by("-created_at")
                    .first()
                )
                if last_qr:
                    last_dt = last_qr.created_at
                    if timezone.is_naive(last_dt):
                        last_dt = timezone.make_aware(last_dt, dt_tz.utc)
                    if last_dt.astimezone(tz).date() == today_local:
                        info["status"] = "already_rotated_today"
                        info["error"] = "QR уже обновлён сегодня"
                        info["duration_sec"] = round(
                            time.monotonic() - started_at,
                            2,
                        )
                        logger.info(
                            f"[ROTATE][USER] user_id={user.id} "
                            f"status=skipped reason=already_rotated_today"
                        )
                        return "skipped", info

            fio = info["fio"]
            inn_raw = info["inn"]

            if not fio or not inn_raw:
                info["status"] = "skipped_no_fio_or_inn"
                info["error"] = "full_name или employee_id пусты"
                info["duration_sec"] = round(
                    time.monotonic() - started_at,
                    2,
                )
                logger.warning(
                    f"[ROTATE][USER] user_id={user.id} "
                    f"status=skipped reason=empty_fio_or_inn"
                )
                return "skipped", info

            try:
                plain_inn = ensure_plain_inn(inn_raw)
            except Exception as exc:
                info["status"] = "skipped_bad_inn"
                info["error"] = f"Некорректный ИНН: {exc}"
                info["duration_sec"] = round(
                    time.monotonic() - started_at,
                    2,
                )
                logger.warning(
                    f"[ROTATE][USER] user_id={user.id} "
                    f"status=skipped reason=bad_inn error={exc}"
                )
                return "skipped", info

            ukm_qs = UKMUser.objects.filter(user_id=user.id)
            if allowed_store_ids:
                ukm_qs = ukm_qs.filter(
                    storeid__in=sorted(allowed_store_ids)
                )

            ukm_links = list(
                ukm_qs
                .values("storeid", "roleid")
                .order_by("storeid", "roleid")
            )
            info["stores"] = ukm_links

            if not ukm_links:
                info["status"] = "skipped_no_target_stores"
                info["error"] = "Нет записей ukm_users в целевых магазинах"
                info["duration_sec"] = round(
                    time.monotonic() - started_at,
                    2,
                )
                logger.warning(
                    f"[ROTATE][USER] user_id={user.id} "
                    f"status=skipped reason=no_target_stores"
                )
                return "skipped", info

            # Сначала проверяем ВСЕ магазины. До окончания этого блока
            # пароль PostgreSQL, import4 и UKM5 не изменяются.
            eligible_links: list[tuple[dict, dict]] = []

            for link in ukm_links:
                store_id = int(link["storeid"])
                role_id = int(link["roleid"])

                org_check = self._check_store_company_inn(
                    employee_inn=plain_inn,
                    store_id=store_id,
                )

                if org_check.get("allowed"):
                    eligible_links.append((link, org_check))
                    info["org_inn_stats"]["matched"] += 1
                    logger.info(
                        "[ROTATE][ORG_INN][OK] user_id=%s storeid=%s "
                        "employee_inn=%s company_inn=%s source=%s",
                        user.id,
                        store_id,
                        plain_inn,
                        org_check.get("onec_company_inn"),
                        org_check.get("onec_source"),
                    )
                else:
                    info["blocked_store_count"] += 1
                    if org_check.get("technical_error"):
                        info["org_inn_stats"]["errors"] += 1
                    else:
                        info["org_inn_stats"]["blocked"] += 1

                    self._append_blocked_store_result(
                        info,
                        store_id=store_id,
                        role_id=role_id,
                        org_check=org_check,
                    )

            info["eligible_store_count"] = len(eligible_links)

            if not eligible_links:
                info["status"] = "skipped_no_org_inn_match"
                info["error"] = (
                    "Обновление не выполнялось: нет ни одного магазина, "
                    "где ИНН организации 1С совпал с trm_in_store.inn"
                )
                info["duration_sec"] = round(
                    time.monotonic() - started_at,
                    2,
                )

                logger.warning(
                    "[ROTATE][USER] user_id=%s status=skipped "
                    "reason=no_org_inn_match blocked=%s errors=%s",
                    user.id,
                    info["org_inn_stats"]["blocked"],
                    info["org_inn_stats"]["errors"],
                )
                return "skipped", info

            if dry_run:
                for link, org_check in eligible_links:
                    store_id = int(link["storeid"])
                    role_id = int(link["roleid"])
                    info["store_results"].append({
                        "storeid": store_id,
                        "roleid": role_id,
                        "cashier_id": None,
                        "found_in_trm": False,
                        "store_status": "ok",
                        "store_summary": (
                            "DRY-RUN: " + org_check.get("message", "ИНН совпал")
                        ),
                        "org_inn_check": org_check,
                        "sync": {
                            "ukm4": {
                                "status": "skipped_dry_run" if target_system == "ukm4" else "skipped_not_requested",
                                "error": "",
                            },
                            "ukm5": {
                                "status": "skipped_dry_run" if target_system == "ukm5" else "skipped_not_requested",
                                "error": "",
                            },
                        },
                    })
                    info["store_stats"]["ok"] += 1

                info["status"] = "dry_run"
                info["error"] = "Проверка выполнена, изменения не вносились"
                info["duration_sec"] = round(
                    time.monotonic() - started_at,
                    2,
                )
                return "skipped", info

            if not hasattr(self, "_trm_alloc"):
                self._trm_alloc = {}

            def _is_trm_id_taken(store_id: int, candidate: int) -> bool:
                conn2 = cur2 = None
                try:
                    conn2 = connect_ukm(store_id=store_id)
                    cur2 = conn2.cursor()
                    cur2.execute(
                        "SELECT 1 AS x FROM trm_in_users "
                        "WHERE id=%s LIMIT 1",
                        (candidate,),
                    )
                    return bool(cur2.fetchone())
                finally:
                    try:
                        if cur2:
                            cur2.close()
                        if conn2:
                            conn2.close()
                    except Exception:
                        pass

            def _alloc_new_trm_id(store_id: int) -> int:
                key = f"store:{int(store_id)}"
                state = self._trm_alloc.get(key)
                if state is None:
                    base = get_next_trm_employee_id(
                        store_id=store_id,
                        host=None,
                    )
                    state = {"next": int(base), "reserved": set()}
                    self._trm_alloc[key] = state

                candidate = state["next"]
                while True:
                    if candidate in state["reserved"]:
                        candidate += 1
                        continue
                    if candidate > int(TRM_SMALL_MAX):
                        raise RuntimeError(
                            f"TRM id overflow: candidate={candidate} "
                            f"> TRM_SMALL_MAX={TRM_SMALL_MAX}"
                        )
                    if _is_trm_id_taken(store_id, candidate):
                        candidate += 1
                        continue

                    state["reserved"].add(candidate)
                    state["next"] = candidate + 1
                    return int(candidate)

            if not hasattr(self, "_ukm5_alloc"):
                self._ukm5_alloc = {}

            def _alloc_new_ukm5_id(store_id: int) -> int:
                key = int(store_id)
                state = self._ukm5_alloc.get(key)
                if state is None:
                    state = {
                        "next": int(get_next_ukm5_employee_id(store_id)),
                        "reserved": set(),
                    }
                    self._ukm5_alloc[key] = state

                candidate = int(state["next"])
                while candidate in state["reserved"]:
                    candidate += 1
                if candidate > int(TRM_SMALL_MAX):
                    raise RuntimeError(
                        f"UKM5 id overflow: candidate={candidate} "
                        f"> TRM_SMALL_MAX={TRM_SMALL_MAX}"
                    )
                state["reserved"].add(candidate)
                state["next"] = candidate + 1
                return candidate

            if target_system == "ukm4":
                password_plain = build_user_password(plain_inn)
                # Только УКМ-4 создаёт новый QR и меняет open_in_system.
                _set_password_pg(user, password_plain)

                stored_password = self._get_saved_ukm_password(user.id)
                if stored_password != password_plain:
                    raise RuntimeError(
                        "Пароль после записи в open_in_system не совпал "
                        "со сгенерированным"
                    )
                password_source = "generated_and_saved"
            else:
                try:
                    password_plain = self._get_saved_ukm_password(user.id)
                except LookupError as exc:
                    info["status"] = "skipped_no_saved_password"
                    info["error"] = str(exc)
                    info["duration_sec"] = round(
                        time.monotonic() - started_at,
                        2,
                    )
                    return "skipped", info
                password_source = "open_in_system"

            masked = (
                password_plain[:6] + "..." + password_plain[-4:]
                if len(password_plain) > 10
                else "*" * len(password_plain)
            )
            logger.info(
                f"[ROTATE][USER] user_id={user.id} "
                f"password={masked} len={len(password_plain)} "
                f"password_source={password_source} "
                f"eligible_stores={len(eligible_links)}"
            )

            converter_cashier_id = None

            for link, org_check in eligible_links:
                store_id = int(link["storeid"])
                role_id = int(link["roleid"])

                existing_id = None
                cashier_id_source = ""
                if target_system == "ukm5":
                    try:
                        existing_id = get_ukm5_employee_id(
                            store_id=store_id,
                            plain_inn=plain_inn,
                        )
                        if existing_id is not None:
                            cashier_id_source = "ukm5_existing"
                    except Exception as exc:
                        logger.warning(
                            "[ROTATE][UKM5] user_id=%s storeid=%s "
                            "existing user lookup failed: %s",
                            user.id,
                            store_id,
                            exc,
                            exc_info=True,
                        )

                try:
                    if existing_id is None and target_system == "ukm4":
                        existing_id = get_trm_employee_id(
                            plain_inn,
                            fio,
                            store_id=store_id,
                            host=None,
                        )
                        if existing_id is not None:
                            cashier_id_source = "ukm4_existing"
                except Exception as exc:
                    logger.error(
                        f"[ROTATE][TRM] user_id={user.id} "
                        f"storeid={store_id} "
                        f"get_trm_employee_id error: {exc}",
                        exc_info=True,
                    )

                if existing_id is not None:
                    cashier_id_for_store = int(existing_id)
                    found = target_system == "ukm4"
                else:
                    if target_system == "ukm5":
                        cashier_id_for_store = _alloc_new_ukm5_id(store_id)
                        cashier_id_source = "ukm5_allocated"
                    else:
                        cashier_id_for_store = _alloc_new_trm_id(store_id)
                        cashier_id_source = "ukm4_allocated"
                    found = False

                if converter_cashier_id is None:
                    converter_cashier_id = cashier_id_for_store

                sync_result = _update_store_mysql_and_xml_for_single_store(
                    store_id=store_id,
                    cashier_id=cashier_id_for_store,
                    role_id=role_id,
                    plain_inn=plain_inn,
                    fio=fio,
                    password_plain=password_plain,
                    target_system=target_system,
                    ukm5_submit_only=ukm5_submit_only,
                )

                store_status, store_summary = self._classify_store_sync(
                    sync_result,
                    target_system=target_system,
                )

                info["store_results"].append({
                    "storeid": store_id,
                    "roleid": role_id,
                    "cashier_id": int(cashier_id_for_store),
                    "found_in_trm": bool(found),
                    "cashier_id_source": cashier_id_source,
                    "store_status": store_status,
                    "store_summary": store_summary,
                    "org_inn_check": org_check,
                    "sync": sync_result,
                })
                info["store_stats"][store_status] += 1

                logger.info(
                    f"[ROTATE][STORE] user_id={user.id} "
                    f"storeid={store_id} role_id={role_id} "
                    f"cashier_id={cashier_id_for_store} "
                    f"found_in_trm={found} status={store_status} "
                    f"org_inn={org_check.get('onec_company_inn')} "
                    f"summary={store_summary}"
                )

                # Сейчас функция no-op, но передаём корректный id конкретного
                # магазина на случай её будущего включения.
                if target_system == "ukm4":
                    _write_converter_user_and_signal(
                        cashier_id=int(cashier_id_for_store),
                        plain_inn=plain_inn,
                        fio=fio,
                        password_plain=password_plain,
                        store_id=store_id,
                        role_id=role_id,
                    )

            ok_cnt = info["store_stats"]["ok"]
            warn_cnt = info["store_stats"]["warning"]
            err_cnt = info["store_stats"]["error"]

            if err_cnt > 0 and ok_cnt == 0 and warn_cnt == 0:
                final_status = "failed"
            elif err_cnt > 0 or warn_cnt > 0:
                final_status = "partial"
            else:
                final_status = "rotated"

            info["status"] = final_status
            info["cashier_id"] = (
                int(converter_cashier_id)
                if converter_cashier_id is not None
                else None
            )
            info["duration_sec"] = round(
                time.monotonic() - started_at,
                2,
            )

            logger.info(
                f"[ROTATE][USER] user_id={user.id} "
                f"status={final_status} "
                f"eligible={info['eligible_store_count']} "
                f"blocked={info['blocked_store_count']} "
                f"stores_ok={ok_cnt} stores_warn={warn_cnt} "
                f"stores_err={err_cnt} duration={info['duration_sec']}s"
            )

            return final_status, info

        except Exception as exc:
            logger.exception(
                f"[ROTATE][USER] user_id={user.id} failed: {exc}"
            )
            info["status"] = "failed"
            info["error"] = str(exc)
            info["duration_sec"] = round(
                time.monotonic() - started_at,
                2,
            )
            return "failed", info

    def _classify_store_sync(
        self,
        sync: dict,
        *,
        target_system: str,
    ) -> tuple[str, str]:

        sync = sync or {}
        ukm4 = sync.get("ukm4") or {}
        ukm5 = sync.get("ukm5") or {}
    

        issues = []
        notes = []
        severity = "ok"

    

        ukm4_status = ukm4.get("status")
        ukm5_status = ukm5.get("status")

    
        if target_system == "ukm4" and ukm4_status in {"error", "skipped_no_ukm4ip"}:
            severity = "error"
            err = (ukm4.get("error") or ukm4_status or "").strip()
            issues.append(f"UKM4: {self._shorten(err, 120)}")

    

        if target_system == "ukm5" and ukm5_status in {"error", "skipped_not_ukm5"}:
            severity = "error"
            err = (ukm5.get("error") or "UKM5 error").strip()
            issues.append(f"UKM5: {self._shorten(err, 120)}")

        elif target_system == "ukm5" and ukm5_status == "submitted":
            http_status = ukm5.get("active_import_status_code")
            notes.append(
                "UKM5 POST принят"
                + (f" (HTTP {http_status})" if http_status else "")
                + ", проверка srvdata пропущена"
            )

    

        elif target_system == "ukm5" and ukm5_status == "warning":
            if severity != "error":
                severity = "warning"

    

            verification = ukm5.get("verification") or {}

    
            user_found = verification.get("user_found")
            password_matches = verification.get("password_matches")
            active_count = int(verification.get("active_count") or 0)
            stale_left = int(verification.get("stale_active_left_count") or 0)
            final_ready = verification.get("final_ready")

    
            warn_reasons = []

    

            if user_found is not True:
                warn_reasons.append("user_found=False")

    

            if password_matches is not True:
                warn_reasons.append("password_matches=False")

    

            if active_count != 1:
                warn_reasons.append(f"active_count={active_count}")

    

            if stale_left != 0:
                warn_reasons.append(f"stale_left={stale_left}")

    

            if final_ready is not True:
                warn_reasons.append("final_ready=False")

    

            if not warn_reasons:
                warn_reasons.append("verification не прошла без явной причины")

    
            issues.append("UKM5 WARN: " + ", ".join(warn_reasons))

    

        if not issues:
            return "ok", "; ".join(notes) if notes else "OK"

    

        return severity, "; ".join(issues)



    def _shorten(self, text: str, limit: int) -> str:
        text = (text or "").strip()

        if len(text) <= limit:
            return text

        return text[: limit - 1] + "…"



    def _format_user_run_line(self, idx: int, total: int, info: dict) -> str:
        fio = self._shorten((info.get("fio") or "ФИО не указано"), 36)
        status_label = (info.get("status") or "").upper()
        duration = float(info.get("duration_sec") or 0.0)


        stats = info.get("store_stats") or {}

        ok_cnt = int(stats.get("ok") or 0)

        warn_cnt = int(stats.get("warning") or 0)

        err_cnt = int(stats.get("error") or 0)


        stores_brief = []

        for sr in info.get("store_results") or []:
            code = {
                "ok": "OK",
                "warning": "WARN",
                "error": "ERR",
            }.get(sr.get("store_status"), "?")

            stores_brief.append(f"{sr.get('storeid')}:{code}")



        stores_part = ", ".join(stores_brief) if stores_brief else "—"



        tail = ""

        if info.get("error"):
            tail = f" | note={self._shorten(info['error'], 120)}"



        return (
            f"[{idx}/{total}] user_id={info.get('user_id')} | {status_label} | "
            f"stores ok={ok_cnt} warn={warn_cnt} err={err_cnt} | "
            f"{duration:.1f}s | {stores_part} | {fio}{tail}"
        )



    def _human_status(self, status: str) -> str:

        mapping = {

            "rotated": "обновлено",

            "partial": "частично",

            "dry_run": "проверено без изменений",

            "already_rotated_today": "пропущено — уже было сегодня",

            "skipped_no_fio_or_inn": "пропущено — нет ФИО/ИНН",

            "skipped_bad_inn": "пропущено — некорректный ИНН",

            "skipped_no_saved_password": (
                "пропущено — нет сохранённого пароля open_in_system"
            ),

            "skipped_no_target_stores": "пропущено — нет доступов в целевых магазинах",
            "skipped_no_org_inn_match": (
                "пропущено — нет магазинов с совпавшим ИНН организации"
            ),

            "failed": "ошибка",
            "skipped_excluded_department": (
                "пропущено — исключённое подразделение"
            ),
        }

        return mapping.get(status or "", status or "неизвестно")





    def _contact_note(self, info: dict) -> str:
        tg_id = info.get("tg_id")
        max_id = info.get("max_id")

        tg_text = str(tg_id).strip() if tg_id is not None else ""
        has_tg = bool(tg_text)

    

        try:
            has_max = max_id is not None and int(max_id) > 0
        except Exception:
            has_max = bool(str(max_id or "").strip())

    

        if not has_tg and not has_max:
            return "нет tg_id и max_id"

    

        if not has_tg:
            return "нет tg_id"

    

        if not has_max:
            return "нет max_id"

    
        return ""



    def _split_max_messages(self, lines: list[str], limit: int = 3900) -> list[str]:
        """
        Делит большой MAX-отчёт на несколько сообщений.
        Ничего не обрезает.

        limit делаем 3900, чтобы не упираться в лимиты MAX.
        """

        chunks: list[str] = []
        current: list[str] = []
        current_len = 0



        safe_limit = max(1000, int(limit) - 250)


        for line in lines:
            line = str(line)



            # Если вдруг одна строка слишком длинная — режем её кусками.

            if len(line) > safe_limit:
                parts = [
                    line[i:i + safe_limit]
                    for i in range(0, len(line), safe_limit)
                ]
            else:

                parts = [line]



            for part in parts:
                add_len = len(part) + 1



                if current and current_len + add_len > safe_limit:
                    chunks.append("\n".join(current))
                    current = []
                    current_len = 0


                current.append(part)
                current_len += add_len



        if current:
            chunks.append("\n".join(current))



        if len(chunks) <= 1:
            return chunks



        total = len(chunks)
        result = []



        for idx, chunk in enumerate(chunks, start=1):
            prefix = f"🌙 Ночная ротация QR/паролей — часть {idx}/{total}\n\n"
            result.append(prefix + chunk)


        return result



    def _send_max_summary(
        self,
        today_local,
        tz_name: str,
        total: int,
        rotated: int,
        partial: int,
        skipped: int,
        failed: int,
        details: list,
        dry_run: bool,
        elapsed_sec: float,
        target_store_ids: list[int],
        target_system: str,
        excluded_department_details: list[dict],
        excluded_department_id: int,
    ) -> None:

        try:

            system_label = "УКМ-4" if target_system == "ukm4" else "УКМ-5"
            header = (
                f"🧪 Обновление {system_label} [DRY-RUN]"
                if dry_run else
                f"🌙 Обновление {system_label}"
            )



            ukm4_ok = ukm4_err = 0

            ukm5_ok = ukm5_submitted = ukm5_warn = ukm5_err = 0

            org_inn_matched = 0
            org_inn_blocked = 0
            org_inn_errors = 0



            no_tg = 0

            no_max = 0

            no_tg_and_no_max = 0

            excluded_department_details = (
                excluded_department_details or []
            )

            excluded_department_count = len(
                excluded_department_details
            )



            store_groups = defaultdict(list)



            for info in details:

                fio = (info.get("fio") or "").strip() or "ФИО не указано"
                phone = (info.get("phone") or "").strip() or "—"


                tg_id = info.get("tg_id")
                max_id = info.get("max_id")


                tg_text = str(tg_id).strip() if tg_id is not None else ""
                has_tg = bool(tg_text)

                

                try:
                    has_max = max_id is not None and int(max_id) > 0
                except Exception:
                    has_max = bool(str(max_id or "").strip())



                if not has_tg:
                    no_tg += 1



                if not has_max:
                    no_max += 1



                if not has_tg and not has_max:
                    no_tg_and_no_max += 1



                contact_note = self._contact_note(info)


                for sr in info.get("store_results") or []:

                    org_check = sr.get("org_inn_check") or {}
                    org_status = org_check.get("status")
                    if org_status == "matched":
                        org_inn_matched += 1
                    elif org_check.get("technical_error"):
                        org_inn_errors += 1
                    elif org_status:
                        org_inn_blocked += 1

                    sync = sr.get("sync") or {}
                    ukm4_status = ((sync.get("ukm4") or {}).get("status") or "")
                    ukm5_status = ((sync.get("ukm5") or {}).get("status") or "")



                    if ukm4_status == "ok":
                        ukm4_ok += 1

                    elif ukm4_status in {"error", "skipped_no_ukm4ip"}:
                        ukm4_err += 1



                    if ukm5_status == "ok":
                        ukm5_ok += 1

                    elif ukm5_status == "submitted":
                        ukm5_submitted += 1

                    elif ukm5_status == "warning":
                        ukm5_warn += 1

                    elif ukm5_status == "error":
                        ukm5_err += 1



                    store_groups[int(sr["storeid"])].append({
                        "user_id": info.get("user_id"),
                        "fio": fio,
                        "roleid": sr.get("roleid"),
                        "phone": phone,
                        "tg_id": tg_id,
                        "max_id": max_id,
                        "contact_note": contact_note,
                        "store_status": sr.get("store_status") or "ok",
                        "store_summary": sr.get("store_summary") or "",
                        "org_inn_check": org_check,

                    })



            if target_system == "ukm4":
                sync_summary = [f"UKM4: ok={ukm4_ok} err={ukm4_err}"]
            else:
                sync_summary = [
                    f"UKM5: confirmed={ukm5_ok} "
                    f"submitted={ukm5_submitted} "
                    f"warn={ukm5_warn} err={ukm5_err}"
                ]

            lines = [
                header,
                "",

                f"Дата: {today_local.isoformat()}",

                f"TZ: {tz_name}",

                f"Целевые магазины: {', '.join(map(str, target_store_ids))}",

                f"Кандидатов: {total}",

                (

                    f"Исключено по department_id={excluded_department_id}: "
                    f"{excluded_department_count}"

                ),

                f"Обновлено: {rotated}",

                f"Частично: {partial}",

                f"Пропущено: {skipped}",

                f"Ошибок: {failed}",

                *sync_summary,
                (
                    "Проверка ИНН организаций: "
                    f"совпало={org_inn_matched} "
                    f"заблокировано={org_inn_blocked} "
                    f"ошибок={org_inn_errors}"
                ),
                (
                    "1С: записей="
                    f"{(getattr(self, '_onec_stats', {}) or {}).get('rows_received', 0)}, "
                    "уникальных ИНН="
                    f"{(getattr(self, '_onec_stats', {}) or {}).get('unique_employee_inn', 0)}, "
                    "время="
                    f"{(getattr(self, '_onec_stats', {}) or {}).get('elapsed_sec', 0)}s"
                ),

                f"Без tg_id: {no_tg}",
                f"Без max_id: {no_max}",
                f"Без tg_id и max_id: {no_tg_and_no_max}",
                f"Время: {elapsed_sec:.1f}s",
                ]


            if excluded_department_details:

                lines.extend([
                    "",
                    (
                        f"⛔ Не обрабатывались: "
                        f"department_id={excluded_department_id}"
                    ),
                    (
                        (
                            "Пароль PostgreSQL, QR, import4.users и "
                            "import4.signal не изменялись."
                            if target_system == "ukm4"
                            else "УКМ-5 и open_in_system не изменялись."
                        )
                    ),
                ])

            

                for excluded_user in excluded_department_details:
                    stores = excluded_user.get("stores") or []

                    stores_text = ", ".join(

                        (
                            f"{store['storeid']}"
                            f"(roleid={store['roleid']})"
                        )
                        for store in stores
                    ) or "—"

            

                    lines.append(
                        f"• {excluded_user['fio']} | "
                        f"{excluded_user['phone']} | "
                        f"магазины: {stores_text}"
                    )

            

            lines.extend([
                "",
                "По магазинам:",
            ])



            if not store_groups:
                lines.append("Нет обработанных записей.")

            else:

                for sid in sorted(store_groups.keys()):
                    users = sorted(
                        store_groups[sid],
                        key=lambda x: (
                            x.get("fio") or "",
                            int(x.get("roleid") or 0),
                            x.get("phone") or "",
                        ),
                    )


                    ok_cnt = sum(1 for x in users if x["store_status"] == "ok")
                    warn_cnt = sum(1 for x in users if x["store_status"] == "warning")
                    err_cnt = sum(1 for x in users if x["store_status"] == "error")



                    lines.append("")
                    lines.append(
                        f"🏬 Магазин {sid}: {len(users)} чел. | OK={ok_cnt} WARN={warn_cnt} ERR={err_cnt}"
                    )



                    for u in users:
                        note = ""
                        if u.get("contact_note"):
                            note = f" | ⚠️ {u['contact_note']}"

                        phone = u.get("phone") or "—"

                        summary_note = ""

                        if u.get("store_status") in {"warning", "error"} and u.get("store_summary"):
                            summary_note = f" | {u['store_status'].upper()}: {u['store_summary']}"



                        lines.append(
                            f"• {u['fio']} | {phone} | roleid={u['roleid']}{note}{summary_note}"
                        )



            messages = self._split_max_messages(lines, limit=3900)


            for msg in messages:
                _send_max_log_async(msg)


            logger.info(
                f"[ROTATE][MAX] summary queued: messages={len(messages)}, "
                f"stores={len(store_groups)}, users={total}"
            )



        except Exception as e:
            logger.error(f"[ROTATE] Не удалось поставить сводный лог в очередь MAX: {e}", exc_info=True)



    def _acquire_lock(self):
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(987654321012345678)")
            return bool(cur.fetchone()[0])



    def _release_lock(self):
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(987654321012345678)")
