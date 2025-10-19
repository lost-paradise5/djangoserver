from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import connection
from frostapp.models import User, QRCode
from frostapp.views import regenerate_qr
from itertools import islice
from zoneinfo import ZoneInfo
import logging
import os

logger = logging.getLogger("ukm_logger")

def batched(qs, size):
    it = qs.iterator()
    while True:
        chunk = list(islice(it, size))
        if not chunk:
            break
        yield chunk

class Command(BaseCommand):
    help = "Ежедневная ротация QR/пароля (PostgreSQL + UKM4 + UKM5)."

    def add_arguments(self, parser):
        parser.add_argument('--batch-size', type=int, default=100)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--only-active', action='store_true')
        parser.add_argument('--only-with-qr', action='store_true')
        parser.add_argument('--user-id', type=int)
        parser.add_argument('--tz', type=str, default=os.getenv('ROTATION_TZ', 'Europe/Stockholm'))
        parser.add_argument('--idempotent', action='store_true',
                            help='Пропускать юзера, если сегодня уже есть свежий QR')

    def handle(self, *args, **opts):
        # защитимся от параллельных запусков одной и той же команды
        if not self._acquire_lock():
            self.stdout.write(self.style.WARNING("Другой запуск уже идёт — выхожу."))
            return

        try:
            bs = opts['batch_size']
            tz = ZoneInfo(opts['tz'])
            today_local = timezone.now().astimezone(tz).date()

            qs = User.objects.all()
            if opts['only_active']:
                qs = qs.filter(active=True)
            if opts['only_with_qr']:
                qs = qs.filter(id__in=QRCode.objects.values('user_id').distinct())
            if opts.get('user_id'):
                qs = qs.filter(id=opts['user_id'])

            total = qs.count()
            self.stdout.write(f"users={total}, batch={bs}, tz={opts['tz']}, dry={opts['dry_run']}")

            rotated = skipped = failed = 0
            for chunk in batched(qs, bs):
                for user in chunk:
                    try:
                        if opts['idempotent']:
                            last_qr = QRCode.objects.filter(user=user).order_by('-created_at').first()
                            if last_qr:
                                dt = last_qr.created_at
                                if timezone.is_naive(dt):
                                    dt = timezone.make_aware(dt, timezone.utc)
                                if dt.astimezone(tz).date() == today_local:
                                    skipped += 1
                                    continue
                        if opts['dry_run']:
                            logger.info(f"[DRY] rotate user {user.id} {user.full_name}")
                            continue

                        regenerate_qr(user)
                        rotated += 1
                    except Exception as e:
                        failed += 1
                        logger.exception(f"Сбой ротации для user_id={user.id}: {e}")

            self.stdout.write(self.style.SUCCESS(
                f"Готово: rotated={rotated}, skipped={skipped}, failed={failed}"
            ))
        finally:
            self._release_lock()

    def _acquire_lock(self):
        with connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(987654321012345678)")
            return bool(cur.fetchone()[0])

    def _release_lock(self):
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(987654321012345678)")
