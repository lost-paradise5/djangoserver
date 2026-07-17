from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import connection
from django.db.models import Q

from frostapp.models import User, QRCode, UKMUser
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
    TRM_SMALL_MAX,
)

from collections import defaultdict
from itertools import islice
from zoneinfo import ZoneInfo
from datetime import timezone as dt_tz
import logging
import os
import time

logger = logging.getLogger("ukm_logger")
EXCLUDED_ROTATION_DEPARTMENT_ID = 458


def batched(qs, size):
    """
    Итерация по queryset-у кусками фиксированного размера.
    """
    it = qs.iterator()
    while True:
        chunk = list(islice(it, size))
        if not chunk:
            break
        yield chunk


class Command(BaseCommand):
    """
    Ежедневная ротация QR/пароля для пользователей с tg_id и доступами
    в целевые магазины из UKM5_FULL_XML_STORE_IDS.
    """

    help = "Ежедневное обновление QR для всех пользователей с доступом к целевым магазинам УКМ."

    def add_arguments(self, parser):
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

    def handle(self, *args, **opts):
        if not self._acquire_lock():
            self.stdout.write(self.style.WARNING("Другой запуск уже идёт — выхожу."))
            return

        started_at = time.monotonic()

        try:
            bs = opts['batch_size']
            tz = ZoneInfo(opts['tz'])
            today_local = timezone.now().astimezone(tz).date()

            anchor_store_ids = sorted(
                int(x)
                for x in (get_ukm5_full_xml_store_ids() or {2013})
            )
            allowed_store_ids = set(anchor_store_ids)
            
            target_user_ids = UKMUser.objects.filter(
                storeid__in=anchor_store_ids
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

            self.stdout.write(
                f"START rotate_qr_codes | date={today_local.isoformat()} | tz={opts['tz']} | "
                f"targets={','.join(map(str, anchor_store_ids))} | "
                f"candidates={total} | "
                f"excluded_department_{EXCLUDED_ROTATION_DEPARTMENT_ID}="
                f"{len(excluded_department_details)} | "
                f"batch={bs} | dry={opts['dry_run']} | "
                f"idempotent={opts['idempotent']}"
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
                        idempotent=opts['idempotent'],
                        dry_run=opts['dry_run'],
                        allowed_store_ids=allowed_store_ids,
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
                target_store_ids=anchor_store_ids,
                excluded_department_details=excluded_department_details,
                excluded_department_id=EXCLUDED_ROTATION_DEPARTMENT_ID,
            )

        finally:
            self._release_lock()



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

    def _rotate_one_user(
        self,
        user,
        today_local,
        tz,
        idempotent: bool,
        dry_run: bool,
        allowed_store_ids: set[int] | None = None,
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
            "duration_sec": 0.0,
            "department_id": getattr(user, "department_id", None),
        }

        try:
            # Дополнительная защита:
            # даже при прямом вызове пользователя отдела 458 не обрабатываем.
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
                    f"status=skipped "
                    f"reason=excluded_department "
                    f"department_id={department_id}"
                )
        
                return "skipped", info
        
            if idempotent:
                last_qr = QRCode.objects.filter(user=user).order_by('-created_at').first()
                if last_qr:
                    dt = last_qr.created_at
                    if timezone.is_naive(dt):
                        dt = timezone.make_aware(dt, dt_tz.utc)
                    if dt.astimezone(tz).date() == today_local:
                        info["status"] = "already_rotated_today"
                        info["error"] = "QR уже обновлён сегодня"
                        info["duration_sec"] = round(time.monotonic() - started_at, 2)
                        logger.info(
                            f"[ROTATE][USER] user_id={user.id} status=skipped reason=already_rotated_today"
                        )
                        return "skipped", info

            fio = info["fio"]
            inn_raw = info["inn"]

            if not fio or not inn_raw:
                info["status"] = "skipped_no_fio_or_inn"
                info["error"] = "full_name или employee_id пусты"
                info["duration_sec"] = round(time.monotonic() - started_at, 2)
                logger.warning(
                    f"[ROTATE][USER] user_id={user.id} status=skipped reason=empty_fio_or_inn"
                )
                return "skipped", info

            try:
                plain_inn = ensure_plain_inn(inn_raw)
            except Exception as e:
                info["status"] = "skipped_bad_inn"
                info["error"] = f"Некорректный ИНН: {e}"
                info["duration_sec"] = round(time.monotonic() - started_at, 2)
                logger.warning(
                    f"[ROTATE][USER] user_id={user.id} status=skipped reason=bad_inn error={e}"
                )
                return "skipped", info

            ukm_qs = UKMUser.objects.filter(user_id=user.id)
            if allowed_store_ids:
                ukm_qs = ukm_qs.filter(storeid__in=sorted(allowed_store_ids))

            ukm_links = list(
                ukm_qs.values('storeid', 'roleid').order_by('storeid')
            )
            info["stores"] = ukm_links

            if not ukm_links:
                info["status"] = "skipped_no_target_stores"
                info["error"] = "Нет записей ukm_users в целевых магазинах"
                info["duration_sec"] = round(time.monotonic() - started_at, 2)
                logger.warning(
                    f"[ROTATE][USER] user_id={user.id} status=skipped reason=no_target_stores"
                )
                return "skipped", info

            if dry_run:
                info["status"] = "dry_run"
                info["error"] = "Запуск с --dry-run, изменения не вносились"
                info["duration_sec"] = round(time.monotonic() - started_at, 2)
                logger.info(
                    f"[ROTATE][USER] user_id={user.id} status=dry_run stores={len(ukm_links)}"
                )
                return "skipped", info

            if not hasattr(self, "_trm_alloc"):
                self._trm_alloc = {}

            def _is_trm_id_taken(store_id: int, candidate: int) -> bool:
                conn2 = cur2 = None
                try:
                    conn2 = connect_ukm(store_id=store_id)
                    cur2 = conn2.cursor()
                    cur2.execute("SELECT 1 AS x FROM trm_in_users WHERE id=%s LIMIT 1", (candidate,))
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
                st = self._trm_alloc.get(key)
                if st is None:
                    base = get_next_trm_employee_id(store_id=store_id, host=None)
                    st = {"next": int(base), "reserved": set()}
                    self._trm_alloc[key] = st

                candidate = st["next"]
                while True:
                    if candidate in st["reserved"]:
                        candidate += 1
                        continue
                    if candidate > int(TRM_SMALL_MAX):
                        raise RuntimeError(
                            f"TRM id overflow: candidate={candidate} > TRM_SMALL_MAX={TRM_SMALL_MAX}"
                        )
                    if _is_trm_id_taken(store_id, candidate):
                        candidate += 1
                        continue

                    st["reserved"].add(candidate)
                    st["next"] = candidate + 1
                    return int(candidate)

            new_password = build_user_password(plain_inn)
            masked = new_password[:6] + "..." + new_password[-4:]
            logger.info(f"[ROTATE][USER] user_id={user.id} new_password={masked} len={len(new_password)}")

            _set_password_pg(user, new_password)

            converter_cashier_id = None

            for link in ukm_links:
                sid = int(link['storeid'])
                role_id = int(link['roleid'])

                existing_id = None
                try:
                    existing_id = get_trm_employee_id(plain_inn, fio, store_id=sid, host=None)
                except Exception as e:
                    logger.error(
                        f"[ROTATE][TRM] user_id={user.id} storeid={sid} get_trm_employee_id error: {e}",
                        exc_info=True
                    )

                if existing_id is not None:
                    cashier_id_for_store = int(existing_id)
                    found = True
                else:
                    cashier_id_for_store = _alloc_new_trm_id(sid)
                    found = False

                if converter_cashier_id is None:
                    converter_cashier_id = cashier_id_for_store

                sync_result = _update_store_mysql_and_xml_for_single_store(
                    store_id=sid,
                    cashier_id=cashier_id_for_store,
                    role_id=role_id,
                    plain_inn=plain_inn,
                    fio=fio,
                    password_plain=new_password,
                )

                store_status, store_summary = self._classify_store_sync(sync_result)

                info["store_results"].append({
                    "storeid": sid,
                    "roleid": role_id,
                    "cashier_id": int(cashier_id_for_store),
                    "found_in_trm": bool(found),
                    "store_status": store_status,
                    "store_summary": store_summary,
                    "sync": sync_result,
                })

                info["store_stats"][store_status] += 1

                logger.info(
                    f"[ROTATE][STORE] user_id={user.id} storeid={sid} role_id={role_id} "
                    f"cashier_id={cashier_id_for_store} found_in_trm={found} "
                    f"status={store_status} summary={store_summary}"
                )

                _write_converter_user_and_signal(
                    cashier_id=int(converter_cashier_id),
                    plain_inn=plain_inn,
                    fio=fio,
                    password_plain=new_password,
                    store_id=sid,
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
            info["cashier_id"] = int(converter_cashier_id) if converter_cashier_id is not None else None
            info["duration_sec"] = round(time.monotonic() - started_at, 2)

            logger.info(
                f"[ROTATE][USER] user_id={user.id} status={final_status} "
                f"stores_ok={ok_cnt} stores_warn={warn_cnt} stores_err={err_cnt} "
                f"duration={info['duration_sec']}s"
            )

            return final_status, info


        except Exception as e:
            logger.exception(f"[ROTATE][USER] user_id={user.id} failed: {e}")
            info["status"] = "failed"
            info["error"] = str(e)
            info["duration_sec"] = round(time.monotonic() - started_at, 2)
            return "failed", info

    def _classify_store_sync(self, sync: dict) -> tuple[str, str]:
        sync = sync or {}
        ukm4 = sync.get("ukm4") or {}
        ukm5 = sync.get("ukm5") or {}
    
        issues = []
        severity = "ok"
    
        ukm4_status = ukm4.get("status")
        ukm5_status = ukm5.get("status")
    
        if ukm4_status in {"error", "skipped_no_ukm4ip"}:
            severity = "error"
            err = (ukm4.get("error") or ukm4_status or "").strip()
            issues.append(f"UKM4: {self._shorten(err, 120)}")
    
        if ukm5_status == "error":
            severity = "error"
            err = (ukm5.get("error") or "UKM5 error").strip()
            issues.append(f"UKM5: {self._shorten(err, 120)}")
    
        elif ukm5_status == "warning":
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
            return "ok", "OK"
    
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
            "skipped_no_target_stores": "пропущено — нет доступов в целевых магазинах",
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
        excluded_department_details: list[dict],
        excluded_department_id: int,
    ) -> None:
        try:
            header = (
                "🧪 Ночная ротация QR/паролей [DRY-RUN]"
                if dry_run else
                "🌙 Ночная ротация QR/паролей"
            )

            ukm4_ok = ukm4_err = 0
            ukm5_ok = ukm5_warn = ukm5_err = 0

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
                    sync = sr.get("sync") or {}
                    ukm4_status = ((sync.get("ukm4") or {}).get("status") or "")
                    ukm5_status = ((sync.get("ukm5") or {}).get("status") or "")

                    if ukm4_status == "ok":
                        ukm4_ok += 1
                    elif ukm4_status in {"error", "skipped_no_ukm4ip"}:
                        ukm4_err += 1

                    if ukm5_status == "ok":
                        ukm5_ok += 1
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
                    })

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
                f"UKM4: ok={ukm4_ok} err={ukm4_err}",
                f"UKM5: ok={ukm5_ok} warn={ukm5_warn} err={ukm5_err}",
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
                        "Пароль PostgreSQL, import4.users, import4.signal, "
                        "trm_in_users и УКМ-5 не изменялись."
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


