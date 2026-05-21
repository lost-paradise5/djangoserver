from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import connection


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

    help = "Ежедневное обновление QR для пользователей с доступом к магазину УКМ и tg_id/max_id"

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

            anchor_store_ids = sorted(int(x) for x in (get_ukm5_full_xml_store_ids() or {2013}))
            allowed_store_ids = set(anchor_store_ids)
            
            
            qs = User.objects.filter(
                tg_id__isnull=False,
                id__in=UKMUser.objects.filter(storeid__in=anchor_store_ids).values('user_id'),
            ).distinct()

            if opts['only_active']:
                qs = qs.filter(active=True)

            if opts['only_with_qr']:
                qs = qs.filter(id__in=QRCode.objects.values('user_id').distinct())

            if opts.get('user_id'):
                qs = qs.filter(id=opts['user_id'])

            qs = qs.order_by('id')

            total = qs.count()

            self.stdout.write(
                f"START rotate_qr_codes | date={today_local.isoformat()} | tz={opts['tz']} | "
                f"targets={','.join(map(str, anchor_store_ids))} | "
                f"candidates={total} | batch={bs} | dry={opts['dry_run']} | idempotent={opts['idempotent']}"
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
            )

        finally:
            self._release_lock()

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
            "status": "",
            "error": "",
            "stores": [],
            "cashier_id": None,
            "store_results": [],
            "store_stats": {"ok": 0, "warning": 0, "error": 0},
            "duration_sec": 0.0,
        }

        try:
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
            issues.append(f"UKM4: {self._shorten(err, 90)}")

        if ukm5_status == "error":
            severity = "error"
            err = (ukm5.get("error") or "UKM5 error").strip()
            issues.append(f"UKM5: {self._shorten(err, 90)}")
        elif ukm5_status == "warning":
            if severity != "error":
                severity = "warning"
            verification = ukm5.get("verification") or {}
            active_count = int(verification.get("active_count") or 0)
            stale_left = int(verification.get("stale_active_left_count") or 0)
            issues.append(f"UKM5: active={active_count}, stale_left={stale_left}")

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
        }
        return mapping.get(status or "", status or "неизвестно")

    def _fit_max_message(self, lines: list[str], limit: int = 3900) -> str:
        result = []
        cur_len = 0

        for idx, line in enumerate(lines):
            chunk = line if not result else "\n" + line
            if cur_len + len(chunk) > limit:
                remaining = len(lines) - idx
                tail = f"… сообщение сокращено, ещё строк: {remaining}"
                if result:
                    extra_chunk = "\n" + tail
                    if cur_len + len(extra_chunk) <= limit:
                        result.append(tail)
                    else:
                        result[-1] = "… сообщение сокращено"
                else:
                    result.append("… сообщение сокращено")
                break

            result.append(line)
            cur_len += len(chunk)

        return "\n".join(result)

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
    ) -> None:
        try:
            header = (
                "🧪 Ночная ротация QR/паролей [DRY-RUN]"
                if dry_run else
                "🌙 Ночная ротация QR/паролей"
            )

            ukm4_ok = ukm4_err = 0
            ukm5_ok = ukm5_warn = ukm5_err = 0

            store_groups = defaultdict(list)

            for info in details:
                fio = (info.get("fio") or "").strip() or "ФИО не указано"
                phone = (info.get("phone") or "").strip() or "—"

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
                        "fio": fio,
                        "roleid": sr.get("roleid"),
                        "phone": phone,
                        "store_status": sr.get("store_status") or "ok",
                    })

            lines = [
                header,
                "",
                f"Дата: {today_local.isoformat()}",
                f"TZ: {tz_name}",
                f"Целевые магазины: {', '.join(map(str, target_store_ids))}",
                f"Кандидатов: {total}",
                f"Обновлено: {rotated}",
                f"Частично: {partial}",
                f"Пропущено: {skipped}",
                f"Ошибок: {failed}",
                f"UKM4: ok={ukm4_ok} err={ukm4_err}",
                f"UKM5: ok={ukm5_ok} warn={ukm5_warn} err={ukm5_err}",
                f"Время: {elapsed_sec:.1f}s",
                "",
                "По магазинам:",
            ]

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
                        lines.append(
                            f"• {u['fio']} | roleid={u['roleid']} | {u['phone']}"
                        )

            msg = self._fit_max_message(lines, limit=3900)
            _send_max_log_async(msg)

        except Exception as e:
            logger.error(f"[ROTATE] Не удалось поставить сводный лог в очередь MAX: {e}", exc_info=True)

    def _acquire_lock(self):
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(987654321012345678)")
            return bool(cur.fetchone()[0])

    def _release_lock(self):
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(987654321012345678)")
































# # djangoserver/frostapp/management/commands/rotate_qr_codes.py

# from django.core.management.base import BaseCommand
# from django.utils import timezone
# from django.db import connection

# from frostapp.models import User, QRCode, UKMUser
# from frostapp.views import (
#     ensure_plain_inn,
#     build_user_password,
#     _set_password_pg,
#     _update_store_mysql_and_xml_for_single_store,
#     _write_converter_user_and_signal,
#     get_trm_employee_id,
#     get_next_trm_employee_id,
#     connect_ukm,
#     _send_max_log_async,
#     UKM5_FULL_XML_STORE_IDS,
#     TRM_SMALL_MAX,
# )

# from itertools import islice
# from zoneinfo import ZoneInfo
# from datetime import timezone as dt_tz
# import logging
# import os
# import time

# logger = logging.getLogger("ukm_logger")


# def batched(qs, size):
#     """
#     Итерация по queryset-у кусками фиксированного размера.
#     """
#     it = qs.iterator()
#     while True:
#         chunk = list(islice(it, size))
#         if not chunk:
#             break
#         yield chunk


# class Command(BaseCommand):
#     """
#     Ежедневная ротация QR/пароля для пользователей с tg_id и доступами в целевые магазины.
#     """

#     help = "Ежедневное обновление QR/пароля для пользователей с доступом к магазину УКМ и tg_id."

#     def add_arguments(self, parser):
#         parser.add_argument('--batch-size', type=int, default=100)
#         parser.add_argument(
#             '--dry-run',
#             action='store_true',
#             help='Ничего не менять, только логировать, кого бы крутили'
#         )
#         parser.add_argument(
#             '--only-active',
#             action='store_true',
#             help='Обрабатывать только active=True'
#         )
#         parser.add_argument(
#             '--only-with-qr',
#             action='store_true',
#             help='Обрабатывать только тех, у кого уже есть QR'
#         )
#         parser.add_argument(
#             '--user-id',
#             type=int,
#             help='Ограничить обработку одним user_id'
#         )
#         parser.add_argument('--tz', type=str, default=os.getenv('ROTATION_TZ', 'Europe/Stockholm'))
#         parser.add_argument(
#             '--idempotent',
#             action='store_true',
#             help='Пропускать пользователя, если сегодня уже есть свежий QR'
#         )

#     def handle(self, *args, **opts):
#         if not self._acquire_lock():
#             self.stdout.write(self.style.WARNING("Другой запуск уже идёт — выхожу."))
#             return

#         started_at = time.monotonic()

#         try:
#             bs = opts['batch_size']
#             tz = ZoneInfo(opts['tz'])
#             today_local = timezone.now().astimezone(tz).date()

#             anchor_store_ids = sorted(int(x) for x in (UKM5_FULL_XML_STORE_IDS or {2013}))

#             qs = User.objects.filter(
#                 tg_id__isnull=False,
#                 id__in=UKMUser.objects.filter(storeid__in=anchor_store_ids).values('user_id'),
#             ).distinct()

#             if opts['only_active']:
#                 qs = qs.filter(active=True)

#             if opts['only_with_qr']:
#                 qs = qs.filter(id__in=QRCode.objects.values('user_id').distinct())

#             if opts.get('user_id'):
#                 qs = qs.filter(id=opts['user_id'])

#             qs = qs.order_by('id')

#             total = qs.count()

#             self.stdout.write(
#                 f"START rotate_qr_codes | date={today_local.isoformat()} | tz={opts['tz']} | "
#                 f"candidates={total} | batch={bs} | dry={opts['dry_run']} | idempotent={opts['idempotent']}"
#             )

#             rotated = partial = skipped = failed = 0
#             details = []
#             processed = 0

#             for chunk in batched(qs, bs):
#                 for user in chunk:
#                     processed += 1

#                     allowed_store_ids = set(anchor_store_ids)

#                     status, info = self._rotate_one_user(
#                         user=user,
#                         today_local=today_local,
#                         tz=tz,
#                         idempotent=opts['idempotent'],
#                         dry_run=opts['dry_run'],
#                         allowed_store_ids=allowed_store_ids,
#                     )
#                     details.append(info)

#                     if status == 'rotated':
#                         rotated += 1
#                     elif status == 'partial':
#                         partial += 1
#                     elif status == 'skipped':
#                         skipped += 1
#                     else:
#                         failed += 1

#                     self.stdout.write(self._format_user_run_line(processed, total, info))

#             elapsed = time.monotonic() - started_at

#             self.stdout.write(
#                 self.style.SUCCESS(
#                     f"FINISH rotate_qr_codes | ok={rotated} | partial={partial} | "
#                     f"skipped={skipped} | failed={failed} | elapsed={elapsed:.1f}s"
#                 )
#             )

#             self._send_max_summary(
#                 today_local=today_local,
#                 tz_name=opts['tz'],
#                 total=total,
#                 rotated=rotated,
#                 partial=partial,
#                 skipped=skipped,
#                 failed=failed,
#                 details=details,
#                 dry_run=opts['dry_run'],
#                 elapsed_sec=elapsed,
#             )

#         finally:
#             self._release_lock()

#     def _rotate_one_user(
#         self,
#         user,
#         today_local,
#         tz,
#         idempotent: bool,
#         dry_run: bool,
#         allowed_store_ids: set[int] | None = None,
#     ):
#         started_at = time.monotonic()

#         info = {
#             "user_id": user.id,
#             "fio": (user.full_name or "").strip(),
#             "inn": (user.employee_id or "").strip(),
#             "tg_id": getattr(user, "tg_id", None),
#             "status": "",
#             "error": "",
#             "stores": [],
#             "cashier_id": None,
#             "store_results": [],
#             "store_stats": {"ok": 0, "warning": 0, "error": 0},
#             "duration_sec": 0.0,
#         }

#         try:
#             if idempotent:
#                 last_qr = QRCode.objects.filter(user=user).order_by('-created_at').first()
#                 if last_qr:
#                     dt = last_qr.created_at
#                     if timezone.is_naive(dt):
#                         dt = timezone.make_aware(dt, dt_tz.utc)
#                     if dt.astimezone(tz).date() == today_local:
#                         info["status"] = "already_rotated_today"
#                         info["error"] = "QR уже обновлён сегодня"
#                         info["duration_sec"] = round(time.monotonic() - started_at, 2)
#                         logger.info(f"[ROTATE][USER] user_id={user.id} status=skipped reason=already_rotated_today")
#                         return "skipped", info

#             fio = info["fio"]
#             inn_raw = info["inn"]

#             if not fio or not inn_raw:
#                 info["status"] = "skipped_no_fio_or_inn"
#                 info["error"] = "full_name или employee_id пусты"
#                 info["duration_sec"] = round(time.monotonic() - started_at, 2)
#                 logger.warning(f"[ROTATE][USER] user_id={user.id} status=skipped reason=empty_fio_or_inn")
#                 return "skipped", info

#             try:
#                 plain_inn = ensure_plain_inn(inn_raw)
#             except Exception as e:
#                 info["status"] = "skipped_bad_inn"
#                 info["error"] = f"Некорректный ИНН: {e}"
#                 info["duration_sec"] = round(time.monotonic() - started_at, 2)
#                 logger.warning(f"[ROTATE][USER] user_id={user.id} status=skipped reason=bad_inn error={e}")
#                 return "skipped", info

#             ukm_links = list(
#                 UKMUser.objects
#                 .filter(user_id=user.id)
#                 .values('storeid', 'roleid')
#                 .order_by('storeid')
#             )
#             info["stores"] = ukm_links

#             if not ukm_links:
#                 info["status"] = "skipped_no_ukm_users"
#                 info["error"] = "Нет записей в ukm_users"
#                 info["duration_sec"] = round(time.monotonic() - started_at, 2)
#                 logger.warning(f"[ROTATE][USER] user_id={user.id} status=skipped reason=no_ukm_users")
#                 return "skipped", info

#             if dry_run:
#                 info["status"] = "dry_run"
#                 info["error"] = "Запуск с --dry-run, изменения не вносились"
#                 info["duration_sec"] = round(time.monotonic() - started_at, 2)
#                 logger.info(f"[ROTATE][USER] user_id={user.id} status=dry_run stores={len(ukm_links)}")
#                 return "skipped", info

#             if not hasattr(self, "_trm_alloc"):
#                 self._trm_alloc = {}

#             def _is_trm_id_taken(store_id: int, candidate: int) -> bool:
#                 conn2 = cur2 = None
#                 try:
#                     conn2 = connect_ukm(store_id=store_id)
#                     cur2 = conn2.cursor()
#                     cur2.execute("SELECT 1 AS x FROM trm_in_users WHERE id=%s LIMIT 1", (candidate,))
#                     return bool(cur2.fetchone())
#                 finally:
#                     try:
#                         if cur2:
#                             cur2.close()
#                         if conn2:
#                             conn2.close()
#                     except Exception:
#                         pass

#             def _alloc_new_trm_id(store_id: int) -> int:
#                 key = f"store:{int(store_id)}"
#                 st = self._trm_alloc.get(key)
#                 if st is None:
#                     base = get_next_trm_employee_id(store_id=store_id, host=None)
#                     st = {"next": int(base), "reserved": set()}
#                     self._trm_alloc[key] = st

#                 candidate = st["next"]
#                 while True:
#                     if candidate in st["reserved"]:
#                         candidate += 1
#                         continue
#                     if candidate > int(TRM_SMALL_MAX):
#                         raise RuntimeError(
#                             f"TRM id overflow: candidate={candidate} > TRM_SMALL_MAX={TRM_SMALL_MAX}"
#                         )
#                     if _is_trm_id_taken(store_id, candidate):
#                         candidate += 1
#                         continue

#                     st["reserved"].add(candidate)
#                     st["next"] = candidate + 1
#                     return int(candidate)

#             new_password = build_user_password(plain_inn)
#             masked = new_password[:6] + "..." + new_password[-4:]
#             logger.info(f"[ROTATE][USER] user_id={user.id} new_password={masked} len={len(new_password)}")

#             _set_password_pg(user, new_password)

#             converter_cashier_id = None

#             for link in ukm_links:
#                 sid = int(link['storeid'])
#                 role_id = int(link['roleid'])

#                 existing_id = None
#                 try:
#                     existing_id = get_trm_employee_id(plain_inn, fio, store_id=sid, host=None)
#                 except Exception as e:
#                     logger.error(
#                         f"[ROTATE][TRM] user_id={user.id} storeid={sid} get_trm_employee_id error: {e}",
#                         exc_info=True
#                     )

#                 if existing_id is not None:
#                     cashier_id_for_store = int(existing_id)
#                     found = True
#                 else:
#                     cashier_id_for_store = _alloc_new_trm_id(sid)
#                     found = False

#                 if converter_cashier_id is None:
#                     converter_cashier_id = cashier_id_for_store

#                 sync_result = _update_store_mysql_and_xml_for_single_store(
#                     store_id=sid,
#                     cashier_id=cashier_id_for_store,
#                     role_id=role_id,
#                     plain_inn=plain_inn,
#                     fio=fio,
#                     password_plain=new_password,
#                 )

#                 store_status, store_summary = self._classify_store_sync(sync_result)

#                 info["store_results"].append({
#                     "storeid": sid,
#                     "roleid": role_id,
#                     "cashier_id": int(cashier_id_for_store),
#                     "found_in_trm": bool(found),
#                     "store_status": store_status,
#                     "store_summary": store_summary,
#                     "sync": sync_result,
#                 })

#                 info["store_stats"][store_status] += 1

#                 logger.info(
#                     f"[ROTATE][STORE] user_id={user.id} storeid={sid} role_id={role_id} "
#                     f"cashier_id={cashier_id_for_store} found_in_trm={found} "
#                     f"status={store_status} summary={store_summary}"
#                 )

#                 _write_converter_user_and_signal(
#                     cashier_id=int(converter_cashier_id),
#                     plain_inn=plain_inn,
#                     fio=fio,
#                     password_plain=new_password,
#                     store_id=sid,
#                     role_id=role_id,
#                 )

#             ok_cnt = info["store_stats"]["ok"]
#             warn_cnt = info["store_stats"]["warning"]
#             err_cnt = info["store_stats"]["error"]

#             if err_cnt > 0 and ok_cnt == 0 and warn_cnt == 0:
#                 final_status = "failed"
#             elif err_cnt > 0 or warn_cnt > 0:
#                 final_status = "partial"
#             else:
#                 final_status = "rotated"

#             info["status"] = final_status
#             info["cashier_id"] = int(converter_cashier_id) if converter_cashier_id is not None else None
#             info["duration_sec"] = round(time.monotonic() - started_at, 2)

#             logger.info(
#                 f"[ROTATE][USER] user_id={user.id} status={final_status} "
#                 f"stores_ok={ok_cnt} stores_warn={warn_cnt} stores_err={err_cnt} "
#                 f"duration={info['duration_sec']}s"
#             )

#             return final_status, info

#         except Exception as e:
#             logger.exception(f"[ROTATE][USER] user_id={user.id} failed: {e}")
#             info["status"] = "failed"
#             info["error"] = str(e)
#             info["duration_sec"] = round(time.monotonic() - started_at, 2)
#             return "failed", info

#     def _classify_store_sync(self, sync: dict) -> tuple[str, str]:
#         sync = sync or {}
#         ukm4 = sync.get("ukm4") or {}
#         ukm5 = sync.get("ukm5") or {}

#         issues = []
#         severity = "ok"

#         ukm4_status = ukm4.get("status")
#         ukm5_status = ukm5.get("status")

#         if ukm4_status in {"error", "skipped_no_ukm4ip"}:
#             severity = "error"
#             err = (ukm4.get("error") or ukm4_status or "").strip()
#             issues.append(f"UKM4: {self._shorten(err, 90)}")

#         if ukm5_status == "error":
#             severity = "error"
#             err = (ukm5.get("error") or "UKM5 error").strip()
#             issues.append(f"UKM5: {self._shorten(err, 90)}")
#         elif ukm5_status == "warning":
#             if severity != "error":
#                 severity = "warning"
#             verification = ukm5.get("verification") or {}
#             active_count = int(verification.get("active_count") or 0)
#             stale_left = int(verification.get("stale_active_left_count") or 0)
#             issues.append(f"UKM5: active={active_count}, stale_left={stale_left}")

#         if not issues:
#             return "ok", "OK"

#         return severity, "; ".join(issues)

#     def _shorten(self, text: str, limit: int) -> str:
#         text = (text or "").strip()
#         if len(text) <= limit:
#             return text
#         return text[: limit - 1] + "…"

#     def _format_user_run_line(self, idx: int, total: int, info: dict) -> str:
#         fio = self._shorten((info.get("fio") or "ФИО не указано"), 36)
#         status_label = (info.get("status") or "").upper()
#         duration = float(info.get("duration_sec") or 0.0)

#         stats = info.get("store_stats") or {}
#         ok_cnt = int(stats.get("ok") or 0)
#         warn_cnt = int(stats.get("warning") or 0)
#         err_cnt = int(stats.get("error") or 0)

#         stores_brief = []
#         for sr in info.get("store_results") or []:
#             code = {
#                 "ok": "OK",
#                 "warning": "WARN",
#                 "error": "ERR",
#             }.get(sr.get("store_status"), "?")
#             stores_brief.append(f"{sr.get('storeid')}:{code}")

#         stores_part = ", ".join(stores_brief) if stores_brief else "—"

#         tail = ""
#         if info.get("error"):
#             tail = f" | note={self._shorten(info['error'], 120)}"

#         return (
#             f"[{idx}/{total}] user_id={info.get('user_id')} | {status_label} | "
#             f"stores ok={ok_cnt} warn={warn_cnt} err={err_cnt} | "
#             f"{duration:.1f}s | {stores_part} | {fio}{tail}"
#         )

#     def _human_status(self, status: str) -> str:
#         mapping = {
#             "rotated": "обновлено",
#             "partial": "частично",
#             "dry_run": "проверено без изменений",
#             "already_rotated_today": "пропущено — уже было сегодня",
#             "skipped_no_fio_or_inn": "пропущено — нет ФИО/ИНН",
#             "skipped_bad_inn": "пропущено — некорректный ИНН",
#             "skipped_no_ukm_users": "пропущено — нет ukm_users",
#             "failed": "ошибка",
#         }
#         return mapping.get(status or "", status or "неизвестно")

#     def _make_problem_line(self, info: dict) -> str:
#         fio = self._shorten((info.get("fio") or f"user_id={info.get('user_id')}"), 24)

#         problems = []
#         for sr in info.get("store_results") or []:
#             if sr.get("store_status") != "ok":
#                 problems.append(f"{sr.get('storeid')}: {sr.get('store_summary')}")

#         if not problems and info.get("error"):
#             problems.append(self._shorten(info["error"], 120))

#         if not problems:
#             return ""

#         return f"• {info.get('user_id')} {fio} — " + self._shorten("; ".join(problems[:2]), 220)

#     def _send_max_summary(
#         self,
#         today_local,
#         tz_name: str,
#         total: int,
#         rotated: int,
#         partial: int,
#         skipped: int,
#         failed: int,
#         details: list,
#         dry_run: bool,
#         elapsed_sec: float,
#     ) -> None:
#         try:
#             header = (
#                 "🧪 DRY-RUN ротации QR/паролей"
#                 if dry_run else
#                 "✅ Ротация QR/паролей"
#             )

#             ukm4_ok = ukm4_err = 0
#             ukm5_ok = ukm5_warn = ukm5_err = 0

#             for info in details:
#                 for sr in info.get("store_results") or []:
#                     sync = sr.get("sync") or {}
#                     ukm4_status = ((sync.get("ukm4") or {}).get("status") or "")
#                     ukm5_status = ((sync.get("ukm5") or {}).get("status") or "")

#                     if ukm4_status == "ok":
#                         ukm4_ok += 1
#                     elif ukm4_status in {"error", "skipped_no_ukm4ip"}:
#                         ukm4_err += 1

#                     if ukm5_status == "ok":
#                         ukm5_ok += 1
#                     elif ukm5_status == "warning":
#                         ukm5_warn += 1
#                     elif ukm5_status == "error":
#                         ukm5_err += 1

#             lines = [
#                 header,
#                 f"Дата: {today_local.isoformat()} ({tz_name})",
#                 f"Всего: {total} | OK: {rotated} | Частично: {partial} | Пропущено: {skipped} | Ошибок: {failed}",
#                 f"UKM4: ok={ukm4_ok} err={ukm4_err}",
#                 f"UKM5: ok={ukm5_ok} warn={ukm5_warn} err={ukm5_err}",
#                 f"Время: {elapsed_sec:.1f}s",
#             ]

#             problem_lines = []
#             for info in details:
#                 if info.get("status") in {"partial", "failed"}:
#                     line = self._make_problem_line(info)
#                     if line:
#                         problem_lines.append(line)

#             if problem_lines:
#                 lines.append("")
#                 lines.append("Проблемные сотрудники:")
#                 max_len = 3900
#                 for idx, line in enumerate(problem_lines, start=1):
#                     candidate = "\n".join(lines + [line])
#                     if len(candidate) > max_len:
#                         rest = len(problem_lines) - idx + 1
#                         lines.append(f"…ещё: {rest}")
#                         break
#                     lines.append(line)
#             else:
#                 lines.append("")
#                 lines.append("Проблем по магазинам не обнаружено.")

#             msg = "\n".join(lines)
#             _send_max_log_async(msg)

#         except Exception as e:
#             logger.error(f"[ROTATE] Не удалось поставить сводный лог в очередь MAX: {e}", exc_info=True)

#     def _acquire_lock(self):
#         with connection.cursor() as cur:
#             cur.execute("SELECT pg_try_advisory_lock(987654321012345678)")
#             return bool(cur.fetchone()[0])

#     def _release_lock(self):
#         with connection.cursor() as cur:
#             cur.execute("SELECT pg_advisory_unlock(987654321012345678)")






# # djangoserver/frostapp/management/commands/rotate_qr_codes.py

# from django.core.management.base import BaseCommand
# from django.utils import timezone
# from django.db import connection

# from frostapp.models import User, QRCode, UKMUser
# from frostapp.views import (
#     ensure_plain_inn,
#     build_user_password,
#     _set_password_pg,
#     _update_store_mysql_and_xml_for_single_store,
#     _write_converter_user_and_signal,
#     get_trm_employee_id,
#     get_next_trm_employee_id,
#     connect_ukm,
#     send_telegram_log,
#     UKM5_FULL_XML_STORE_IDS,
#     TRM_SMALL_MAX,
# )

# from itertools import islice
# from zoneinfo import ZoneInfo
# from datetime import timezone as dt_tz
# import logging
# import os

# logger = logging.getLogger("ukm_logger")


# def batched(qs, size):
#     """
#     Итерация по queryset-у кусками фиксированного размера.
#     """
#     it = qs.iterator()
#     while True:
#         chunk = list(islice(it, size))
#         if not chunk:
#             break
#         yield chunk


# class Command(BaseCommand):
#     """
#     Ежедневная ротация QR/пароля (PostgreSQL + UKM4 + UKM5 + конвертер import4staffbonus)
#     для всех пользователей, у которых:

#       • есть tg_id (users.tg_id не NULL)
#       • есть хотя бы одна запись в ukm_users с storeid = 2013 (UKM5_FULL_XML_STORE_ID)

#     Для КАЖДОГО такого пользователя:

#       1) employee_id проверяется как ИНН (10/12 цифр) через ensure_plain_inn().
#       2) В trm_in_users ищется кассир (ИНН + ФИО):
#          - если найден → используем этот id (единый для всех магазинов);
#          - если нет   → берём MAX(id)+1 как базовый id.
#       3) Генерируется новый пароль через build_user_password(ИНН).
#       4) В PostgreSQL обновляется QRCode + OpenInSystem (system_id=9) через _set_password_pg().
#       5) Для КАЖДОГО магазина из ukm_users:
#          - UKM4 + XML UKM5 обновляются через _update_store_mysql_and_xml_for_single_store();
#          - конвертер import4staffbonus.users + signal обновляется через
#            _write_converter_user_and_signal() с тем же cashier_id, что и в trm_in_users.
#       6) В конец отправляется ОДИН большой лог в Telegram с полным списком,
#          кто обработан, кто пропущен и по какой причине.

#     Запускать по cron в 23:59, например:
#       59 23 * * * /opt/venv/bin/python /opt/project/manage.py rotate_qr_codes --idempotent
#     """

#     help = "Ежедневная ротация QR/пароля для пользователей с доступом к магазину 2013 и tg_id."

#     def add_arguments(self, parser):
#         parser.add_argument('--batch-size', type=int, default=100)
#         parser.add_argument('--dry-run', action='store_true',
#                             help='Ничего не менять, только логировать, кого бы крутили')
#         parser.add_argument('--only-active', action='store_true',
#                             help='Обрабатывать только active=True')
#         parser.add_argument('--only-with-qr', action='store_true',
#                             help='Обрабатывать только тех, у кого уже есть QR')
#         parser.add_argument('--user-id', type=int,
#                             help='Ограничить обработку одним user_id (но при этом всё равно требуется tg_id и storeid=2013)')
#         parser.add_argument('--tz', type=str, default=os.getenv('ROTATION_TZ', 'Europe/Stockholm'))
#         parser.add_argument(
#             '--idempotent',
#             action='store_true',
#             help='Пропускать пользователя, если сегодня уже есть свежий QR (по локальной дате)'
#         )

#     def handle(self, *args, **opts):
#         # защитимся от параллельных запусков одной и той же команды
#         if not self._acquire_lock():
#             self.stdout.write(self.style.WARNING("Другой запуск уже идёт — выхожу."))
#             return

#         try:
#             bs = opts['batch_size']
#             tz = ZoneInfo(opts['tz'])
#             today_local = timezone.now().astimezone(tz).date()

#             # Базовый queryset:
#             #   только пользователи с tg_id
#             #   и с доступом к магазину UKM5_FULL_XML_STORE_ID (2013) в ukm_users
#             anchor_store_ids = sorted(int(x) for x in (UKM5_FULL_XML_STORE_IDS or {2013}))
            
#             qs = User.objects.filter(
#                 tg_id__isnull=False,
#                 id__in=UKMUser.objects.filter(storeid__in=anchor_store_ids).values('user_id'),
#             ).distinct()

#             if opts['only_active']:
#                 qs = qs.filter(active=True)

#             if opts['only_with_qr']:
#                 qs = qs.filter(id__in=QRCode.objects.values('user_id').distinct())

#             if opts.get('user_id'):
#                 qs = qs.filter(id=opts['user_id'])

#             qs = qs.order_by('id')

#             total = qs.count()
#             self.stdout.write(
#                 f"candidates={total}, batch={bs}, tz={opts['tz']}, "
#                 f"dry={opts['dry_run']}, idempotent={opts['idempotent']}"
#             )

#             rotated = skipped = failed = 0
#             details = []  # для телеграм-лога

#             for chunk in batched(qs, bs):
#                 for user in chunk:
#                     status, info = self._rotate_one_user(
#                         user=user,
#                         today_local=today_local,
#                         tz=tz,
#                         idempotent=opts['idempotent'],
#                         dry_run=opts['dry_run'],
#                     )
#                     details.append(info)

#                     if status == 'rotated':
#                         rotated += 1
#                     elif status == 'skipped':
#                         skipped += 1
#                     else:
#                         failed += 1

#             self.stdout.write(self.style.SUCCESS(
#                 f"Готово: rotated={rotated}, skipped={skipped}, failed={failed}"
#             ))

#             # Сводный лог в Telegram
#             self._send_telegram_summary(
#                 today_local=today_local,
#                 tz_name=opts['tz'],
#                 total=total,
#                 rotated=rotated,
#                 skipped=skipped,
#                 failed=failed,
#                 details=details,
#                 dry_run=opts['dry_run'],
#             )

#         finally:
#             self._release_lock()

#     def _rotate_one_user(self, user, today_local, tz, idempotent: bool, dry_run: bool):
#         info = {
#             "user_id": user.id,
#             "fio": (user.full_name or "").strip(),
#             "inn": (user.employee_id or "").strip(),
#             "tg_id": getattr(user, "tg_id", None),
#             "status": "",
#             "error": "",
#             "stores": [],
#             "cashier_id": None,
#         }

#         try:
#             # Идемпотентность по дате QR
#             if idempotent:
#                 last_qr = QRCode.objects.filter(user=user).order_by('-created_at').first()
#                 if last_qr:
#                     dt = last_qr.created_at
#                     if timezone.is_naive(dt):
#                         dt = timezone.make_aware(dt, dt_tz.utc)
#                     if dt.astimezone(tz).date() == today_local:
#                         info["status"] = "already_rotated_today"
#                         info["error"] = "QR уже обновлён сегодня (idempotent)"
#                         logger.info(f"[ROTATE] user_id={user.id} пропущен: уже есть QR на {today_local}")
#                         return "skipped", info

#             fio = info["fio"]
#             inn_raw = info["inn"]

#             if not fio or not inn_raw:
#                 info["status"] = "skipped_no_fio_or_inn"
#                 info["error"] = "full_name или employee_id пусты"
#                 logger.warning(f"[ROTATE] user_id={user.id} пропуск: fio/inn пусты")
#                 return "skipped", info

#             try:
#                 plain_inn = ensure_plain_inn(inn_raw)
#             except Exception as e:
#                 info["status"] = "skipped_bad_inn"
#                 info["error"] = f"Некорректный ИНН: {e}"
#                 logger.warning(f"[ROTATE] user_id={user.id} пропуск: {info['error']}")
#                 return "skipped", info

#             ukm_links = list(UKMUser.objects.filter(user_id=user.id).values('storeid', 'roleid'))
#             info["stores"] = ukm_links
#             if not ukm_links:
#                 info["status"] = "skipped_no_ukm_users"
#                 info["error"] = "Нет записей в ukm_users"
#                 logger.warning(f"[ROTATE] user_id={user.id} пропуск: нет ukm_users")
#                 return "skipped", info

#             if dry_run:
#                 info["status"] = "dry_run"
#                 info["error"] = "Запуск с --dry-run, изменения не вносились"
#                 logger.info(f"[ROTATE] [DRY] user_id={user.id}, inn={plain_inn}, stores={ukm_links!r}")
#                 return "skipped", info

#             # -----
#             # локальный allocator на один запуск команды:
#             # чтобы новые id (если человека нет в trm_in_users) не совпали между пользователями в рамках одного запуска
#             # ключ — resolved_host
#             # -----
#             if not hasattr(self, "_trm_alloc"):
#                 self._trm_alloc = {}  # {host: {"next": int, "reserved": set[int]}}

#             def _resolve_host_for_store(store_id: int) -> str:
#                 # connect_ukm сам выберет нужный host по store_id
#                 conn = connect_ukm(store_id=store_id)
#                 try:
#                     # host не всегда доступен из объекта коннекта, поэтому просто закрываем.
#                     # Реальный host для lock/allocator будет повторно выбран внутри get_next_trm_employee_id()
#                     # но для кэша нам нужен СТАБИЛЬНЫЙ ключ — используем resolved_host из get_next_trm_employee_id через попытку.
#                     pass
#                 finally:
#                     try:
#                         conn.close()
#                     except Exception:
#                         pass

#                 # Хитрый, но надёжный способ получить “resolved host” без лезвия во внутренности:
#                 # делаем один вызов get_next_trm_employee_id (он сам резолвит host), но НЕ используем результат как id прямо здесь.
#                 # Чтобы не “съесть” id, мы используем allocator с reserved и проверкой наличия id в trm_in_users.
#                 # На практике это работает стабильно: allocator всё равно проверяет id на занятость.
#                 # Возвращаем host из самого connect_ukm нельзя — поэтому кэшируем по store_id тоже.
#                 return str(store_id)

#             def _is_trm_id_taken(store_id: int, candidate: int) -> bool:
#                 # проверяем на том ukmserver, который соответствует store_id
#                 conn2 = cur2 = None
#                 try:
#                     conn2 = connect_ukm(store_id=store_id)
#                     cur2 = conn2.cursor()
#                     cur2.execute("SELECT 1 AS x FROM trm_in_users WHERE id=%s LIMIT 1", (candidate,))
#                     return bool(cur2.fetchone())
#                 finally:
#                     try:
#                         if cur2: cur2.close()
#                         if conn2: conn2.close()
#                     except Exception:
#                         pass

#             def _alloc_new_trm_id(store_id: int) -> int:
#                 # ключ кэша делаем по store_id-группе: в твоей схеме store_id -> свой ukmserver,
#                 # так что этого достаточно, и гарантированно корректно выбирается host через connect_ukm(store_id=...)
#                 key = f"store:{int(store_id)}"
#                 st = self._trm_alloc.get(key)
#                 if st is None:
#                     base = get_next_trm_employee_id(store_id=store_id, host=None)
#                     st = {"next": int(base), "reserved": set()}
#                     self._trm_alloc[key] = st

#                 candidate = st["next"]
#                 while True:
#                     if candidate in st["reserved"]:
#                         candidate += 1
#                         continue
#                     if candidate > int(TRM_SMALL_MAX):
#                         raise RuntimeError(f"TRM id overflow: candidate={candidate} > TRM_SMALL_MAX={TRM_SMALL_MAX}")
#                     if _is_trm_id_taken(store_id, candidate):
#                         candidate += 1
#                         continue

#                     st["reserved"].add(candidate)
#                     st["next"] = candidate + 1
#                     return int(candidate)

#             # Новый пароль
#             new_password = build_user_password(plain_inn)
#             masked = new_password[:6] + "..." + new_password[-4:]
#             logger.info(f"[ROTATE] user_id={user.id} новый пароль (masked)={masked}, len={len(new_password)}")

#             # PostgreSQL: QRCode + OpenInSystem
#             _set_password_pg(user, new_password)

#             # “converter_cashier_id” — оставляем для совместимости, даже если сейчас converter no-op
#             converter_cashier_id = None

#             # Для каждого магазина: выбираем правильный ukm-host через store_id в get_trm_employee_id/connect_ukm
#             for link in ukm_links:
#                 sid = int(link['storeid'])
#                 role_id = int(link['roleid'])

#                 # Ищем cashier id в trm_in_users на НУЖНОМ ukmserver (по store_id)
#                 existing_id = None
#                 try:
#                     existing_id = get_trm_employee_id(plain_inn, fio, store_id=sid, host=None)
#                 except Exception as e:
#                     logger.error(f"[ROTATE] get_trm_employee_id error user_id={user.id} storeid={sid}: {e}", exc_info=True)

#                 if existing_id is not None:
#                     cashier_id_for_store = int(existing_id)
#                     found = True
#                 else:
#                     cashier_id_for_store = _alloc_new_trm_id(sid)
#                     found = False

#                 if converter_cashier_id is None:
#                     converter_cashier_id = cashier_id_for_store

#                 logger.info(
#                     f"[ROTATE] user_id={user.id} storeid={sid}, roleId={role_id}, "
#                     f"cashier_id_for_store={cashier_id_for_store}, found_in_trm={found}"
#                 )

#                 _update_store_mysql_and_xml_for_single_store(
#                     store_id=sid,
#                     cashier_id=cashier_id_for_store,
#                     role_id=role_id,
#                     plain_inn=plain_inn,
#                     fio=fio,
#                     password_plain=new_password,
#                 )

#                 _write_converter_user_and_signal(
#                     cashier_id=int(converter_cashier_id),
#                     plain_inn=plain_inn,
#                     fio=fio,
#                     password_plain=new_password,
#                     store_id=sid,
#                     role_id=role_id,
#                 )

#             info["status"] = "rotated"
#             info["cashier_id"] = int(converter_cashier_id) if converter_cashier_id is not None else None
#             info["new_password"] = new_password
#             return "rotated", info

#         except Exception as e:
#             logger.exception(f"[ROTATE] Сбой ротации для user_id={user.id}: {e}")
#             info["status"] = "failed"
#             info["error"] = str(e)
#             return "failed", info

#     def _send_telegram_summary(
#         self,
#         today_local,
#         tz_name: str,
#         total: int,
#         rotated: int,
#         skipped: int,
#         failed: int,
#         details: list,
#         dry_run: bool,
#     ) -> None:
#         """
#         Формирует и отправляет один большой лог в Telegram.
#         """
#         try:
#             header = (
#                 "🧪 [DRY-RUN] Ночная ротация QR/паролей (storeid=2013)\n"
#                 if dry_run else
#                 "✅ Ночная ротация QR/паролей (storeid=2013)\n"
#             )

#             lines = [
#                 header.rstrip(),
#                 "",
#                 f"📅 Локальная дата (TZ={tz_name}): {today_local.isoformat()}",
#                 f"👥 Кандидатов (users с tg_id и доступом к storeid=2013): {total}",
#                 f"🔄 Обновлено паролей: {rotated}",
#                 f"⏭ Пропущено: {skipped}",
#                 f"⚠️ С ошибками: {failed}",
#                 "",
#                 "📋 Детализация по сотрудникам:",
#             ]

#             if not details:
#                 lines.append("  (нет записей)")
#             else:
#                 for info in details:
#                     stores = info.get("stores") or []
#                     stores_str = (
#                         ", ".join(
#                             f"{s['storeid']}(role={s['roleid']})" for s in stores
#                         ) if stores else "нет записей в ukm_users"
#                     )

#                     line = (
#                         f"• user_id={info.get('user_id')}, "
#                         f"ФИО='{info.get('fio')}', "
#                         f"ИНН={info.get('inn') or '—'}, "
#                         f"tg_id={info.get('tg_id') or '—'}, "
#                         f"статус={info.get('status')}, "
#                         f"магазины: {stores_str}"
#                     )
#                     lines.append(line)

#                     if info.get("error"):
#                         lines.append(f"    Причина/ошибка: {info['error']}")

#                     # Если нужно видеть пароль в логе — раскомментируй:
#                     # if info.get("new_password"):
#                     #     lines.append(f"    Новый пароль: {info['new_password']}")

#             send_telegram_log("\n".join(lines))
#         except Exception as e:
#             logger.error(f"[ROTATE] Не удалось отправить сводный лог в Telegram: {e}", exc_info=True)

#     def _acquire_lock(self):
#         """
#         Advisory lock в PostgreSQL для избежания параллельных запусков.
#         """
#         with connection.cursor() as cur:
#             cur.execute("SELECT pg_try_advisory_lock(987654321012345678)")
#             return bool(cur.fetchone()[0])

#     def _release_lock(self):
#         with connection.cursor() as cur:
#             cur.execute("SELECT pg_advisory_unlock(987654321012345678)")
