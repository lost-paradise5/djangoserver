import os
from zoneinfo import ZoneInfo

from django.core.management.base import BaseCommand
from django.utils import timezone

from frostapp.views import process_shift_mark_queue_once, _send_admin_log_async


def format_dt_for_log():
    tz_name = os.getenv("SHIFT_TIMEZONE", "Asia/Irkutsk")

    try:
        dt = timezone.now().astimezone(ZoneInfo(tz_name))
    except Exception:
        dt = timezone.localtime(timezone.now())

    return dt.strftime("%d.%m.%Y %H:%M:%S")


class Command(BaseCommand):
    help = "Повторно отправляет в 1С отметки смены из очереди shift_mark_queue"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Сколько записей обработать за один запуск",
        )

        parser.add_argument(
            "--notify-empty",
            action="store_true",
            help="Отправлять MAX-лог даже если очередь пустая",
        )

    def handle(self, *args, **options):
        limit = options.get("limit")
        notify_empty = bool(options.get("notify_empty"))

        stats = process_shift_mark_queue_once(limit=limit)

        text = (
            "🔁 Очередь отметок смены\n\n"
            f"Время запуска: {format_dt_for_log()}\n"
            f"Взято в работу: {stats['taken']}\n"
            f"Успешно отправлено в 1С: {stats['sent']}\n"
            f"Оставлено на повтор: {stats['retry']}\n"
            f"Ошибок обработки: {stats['errors']}"
        )

        self.stdout.write(
            self.style.SUCCESS(
                "SHIFT_QUEUE done: "
                f"taken={stats['taken']} "
                f"sent={stats['sent']} "
                f"retry={stats['retry']} "
                f"errors={stats['errors']}"
            )
        )

        # Чтобы не спамить каждый час, по умолчанию шлём MAX-лог только если была работа.
        # Если нужен лог даже при пустой очереди — запускай команду с --notify-empty.
        if notify_empty or stats["taken"] > 0 or stats["sent"] > 0 or stats["retry"] > 0 or stats["errors"] > 0:
            _send_admin_log_async(text)
