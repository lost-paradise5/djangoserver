from django.core.management.base import BaseCommand

from frostapp.views import process_shift_mark_queue_once


class Command(BaseCommand):
    help = "Повторно отправляет в 1С отметки смены из очереди shift_mark_queue"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Сколько записей обработать за один запуск",
        )

    def handle(self, *args, **options):
        limit = options.get("limit")

        stats = process_shift_mark_queue_once(limit=limit)

        self.stdout.write(
            self.style.SUCCESS(
                "SHIFT_QUEUE done: "
                f"taken={stats['taken']} "
                f"sent={stats['sent']} "
                f"retry={stats['retry']} "
                f"errors={stats['errors']}"
            )
        )
