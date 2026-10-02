import os
import signal
import time

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import close_old_connections, transaction
from django.utils import timezone

from frostapp.models import UkmRotationRun
from frostapp.services.ukm_rotation_runs import (
    UkmRotationRunRecorder,
)
from frostapp.services.ukm_rotation_bitrix_auth import (
    audit_rotation,
)


class Command(BaseCommand):
    help = "Выполняет поставленные из веб-интерфейса задачи ротации УКМ."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-seconds", type=float, default=2.0)

    def handle(self, *args, **options):
        self._stop = False
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)

        while not self._stop:
            run = self._claim_next_run()
            if run is None:
                if options["once"]:
                    return
                time.sleep(max(0.25, float(options["poll_seconds"])))
                continue

            self.stdout.write(
                f"START run_id={run.id} "
                f"system={run.target_system} "
                f"initiator={run.requested_by}"
            )
            try:
                run_options = run.options or {}
                call_command(
                    "rotate_qr_codes",
                    system=run.target_system,
                    run_id=str(run.id),
                    batch_size=int(run_options.get("batch_size") or 100),
                    dry_run=bool(run_options.get("dry_run")),
                    only_active=bool(run_options.get("only_active")),
                    only_with_qr=bool(run_options.get("only_with_qr")),
                    user_id=run_options.get("user_id"),
                    tz=str(
                        run_options.get("tz")
                        or os.getenv("ROTATION_TZ", "Asia/Irkutsk")
                    ),
                    idempotent=bool(run_options.get("idempotent")),
                    ukm5_verify=bool(run_options.get("ukm5_verify")),
                    store_ids=run_options.get("store_ids"),
                )
            except Exception as exc:
                error_text = (
                    f"{type(exc).__name__}: {exc}"
                )

                UkmRotationRunRecorder(
                    str(run.id)
                ).fail(error_text)

                self.stderr.write(
                    self.style.ERROR(
                        f"FAILED run_id={run.id} "
                        f"initiator={run.requested_by}: "
                        f"{error_text}"
                    )
                )
            finally:
                close_old_connections()

            if options["once"]:
                return

    def _request_stop(self, *_args):
        self._stop = True

    @staticmethod
    def _claim_next_run():
        close_old_connections()
        with transaction.atomic():
            run = (
                UkmRotationRun.objects
                .select_for_update(skip_locked=True)
                .filter(status="pending")
                .order_by("created_at")
                .first()
            )
            if run is None:
                return None

            now = timezone.now()
            run.status = "running"
            run.started_at = run.started_at or now
            run.heartbeat_at = now
            run.error = ""
            run.save(update_fields=[
                "status",
                "started_at",
                "heartbeat_at",
                "error",
            ])
            actor = (
                (run.options or {}).get(
                    "initiator"
                )
                or {
                    "type": "legacy",
                    "fio": (
                        run.requested_by
                        or "Автоматический запуск / CLI"
                    ),
                }
            )

            audit_rotation(
                "worker.claimed",
                identity=actor,
                run_id=run.id,
                details={
                    "system": run.target_system,
                },
            )
            return run
