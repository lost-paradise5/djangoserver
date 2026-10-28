import signal
import time

from django.core.management.base import (
    BaseCommand,
    CommandError,
)
from django.db import connection

from frostapp.services.ukm4_verification import (
    WORKER_LOCK,
    recover_interrupted,
    verify_due,
    queue_reports,
    send_one,
    queue_store_alerts,
    send_one_store_alert,
)


class Command(BaseCommand):
    help = (
        "Отложенная проверка ротации УКМ-4 "
        "и отправка итоговых отчётов в MAX."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
        )
        parser.add_argument(
            "--poll-seconds",
            type=float,
            default=5.0,
        )

    def handle(self, *args, **options):
        self._stop = False

        signal.signal(
            signal.SIGTERM,
            self._request_stop,
        )
        signal.signal(
            signal.SIGINT,
            self._request_stop,
        )

        connection.ensure_connection()
        original_connection = connection.connection

        try:
            with connection.cursor() as cur:
                cur.execute(
                    "SELECT pg_try_advisory_lock(%s)",
                    [WORKER_LOCK],
                )
                locked = bool(cur.fetchone()[0])

            if not locked:
                raise CommandError(
                    "Проверяющий worker уже запущен"
                )

            self.stdout.write(
                "UKM4 verification worker started"
            )

            while not self._stop:
                # Проверяем исходное соединение,
                # удерживающее session advisory lock.
                with original_connection.cursor() as cur:
                    cur.execute("SELECT 1")

                if (
                    connection.connection
                    is not original_connection
                ):
                    raise CommandError(
                        "Соединение блокировки изменилось"
                    )

                recover_interrupted()
                verify_due()

                queue_store_alerts()
                queue_reports()

                # За один проход выполняем одну отправку.
                # Предупреждения магазинам идут первыми.
                attempted_delivery = (
                    send_one_store_alert()
                )

                if not attempted_delivery:
                    attempted_delivery = send_one()

                if options["once"]:
                    return

                # Не более одного сообщения за 0.6 сек.
                delay = (
                    0.6
                    if attempted_delivery
                    else max(
                        0.25,
                        float(options["poll_seconds"]),
                    )
                )

                time.sleep(delay)

        finally:
            # Закрытие освобождает advisory lock.
            connection.close()

    def _request_stop(self, *_args):
        self._stop = True
