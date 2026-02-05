import time
import uuid
from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone
from django.db.models import Q

from frostapp.views import _ad_connect, ad_find_group_dn, VPN_GROUP_CN, vpn_apply_state_for_inn, send_telegram_log, bitrix_find_user_by_inn, bitrix_notify_remote_access_open, VPN_SCHEDULER_LOCK_KEY
from frostapp.models import VpnAccessLease

POLL_SEC = int(__import__("os").environ.get("VPN_SCHEDULER_POLL_SEC", "30"))

def _try_advisory_lock() -> bool:
    # только один воркер должен работать
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", [VPN_SCHEDULER_LOCK_KEY])
        return bool(cur.fetchone()[0])

def _release_advisory_lock():
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", [VPN_SCHEDULER_LOCK_KEY])

class Command(BaseCommand):
    help = "VPN access scheduler: applies OPEN/BLOCK leases and restores baseline after expiry."

    def handle(self, *args, **options):
        while True:
            started = timezone.now()
            try:
                if not _try_advisory_lock():
                    time.sleep(POLL_SEC)
                    continue

                # group dn
                conn = None
                try:
                    conn = _ad_connect()
                    group_dn = ad_find_group_dn(conn, VPN_GROUP_CN)
                finally:
                    try:
                        if conn:
                            conn.unbind_s()
                    except Exception:
                        pass

                if not group_dn:
                    send_telegram_log("❌ VPN SCHEDULER: group not found in AD")
                    time.sleep(POLL_SEC)
                    continue

                now = timezone.now()

                # какие INN “затронуты” (активные или только что истекшие)
                active_cond = Q(status="ACTIVE") & Q(starts_at__lte=now) & (Q(ends_at__isnull=True) | Q(ends_at__gt=now))
                expiring_cond = Q(status="ACTIVE") & Q(ends_at__isnull=False) & Q(ends_at__lte=now)

                active_inns = list(VpnAccessLease.objects.filter(active_cond).values_list("inn", flat=True).distinct())
                expiring_inns = list(VpnAccessLease.objects.filter(expiring_cond).values_list("inn", flat=True).distinct())
                touched = sorted(set(active_inns) | set(expiring_inns))

                # помечаем истёкшие
                VpnAccessLease.objects.filter(expiring_cond).update(status="EXPIRED")

                for inn in touched:
                    try:
                        # применяем итоговое состояние
                        changed, now_open, effective_until = vpn_apply_state_for_inn(inn, group_dn)

                        # если реально открылся (changed -> open), отправим уведомление и пометим notify_sent_at
                        if changed and now_open:
                            # кому писать: возьмём из любой активной OPEN (если там null — найдём по ИНН)
                            lease = VpnAccessLease.objects.filter(
                                inn=inn, status="ACTIVE", lease_type="OPEN", starts_at__lte=now
                            ).order_by("-created_at").first()

                            bx_uid = lease.target_bitrix_user_id if lease and lease.target_bitrix_user_id else None
                            if not bx_uid:
                                bx_user = bitrix_find_user_by_inn(inn)
                                bx_uid = int(bx_user["ID"]) if bx_user else None

                            if bx_uid:
                                bitrix_notify_remote_access_open(bx_uid, until_dt=effective_until)

                            # не спамим: помечаем все “стартовавшие” OPEN как уведомлённые
                            VpnAccessLease.objects.filter(
                                inn=inn,
                                status="ACTIVE",
                                lease_type="OPEN",
                                starts_at__lte=now,
                                notify_sent_at__isnull=True,
                            ).update(notify_sent_at=now)

                    except Exception as e:
                        send_telegram_log("\n".join([
                            "❌ VPN SCHEDULER APPLY ERROR",
                            f"inn: {inn}",
                            f"error: {e}",
                            f"time: {timezone.localtime(timezone.now()).isoformat(sep=' ', timespec='seconds')}",
                        ]))

            finally:
                try:
                    _release_advisory_lock()
                except Exception:
                    pass

            # сон до следующего тика
            elapsed = (timezone.now() - started).total_seconds()
            sleep_for = max(1, POLL_SEC - int(elapsed))
            time.sleep(sleep_for)
