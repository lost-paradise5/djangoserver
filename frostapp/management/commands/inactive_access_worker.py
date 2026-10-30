import time

from django.core.management.base import BaseCommand, CommandError

from frostapp.inactive_access import (
    AccessError,
    acquire_worker_lock,
    release_worker_lock,
    recover_interrupted_jobs,
    execute_job,
)

from frostapp.models import InactiveAccessJob


class Command(BaseCommand):
    help = (
        "Обработка очереди поиска и блокировки "
        "неактивных учётных записей"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Обработать одно задание и выйти",
        )

    def handle(self, *args, **options):
        try:
            locked = acquire_worker_lock()
        except AccessError as exc:
            raise CommandError(str(exc)) from exc

        if not locked:
            raise CommandError(
                "Другой inactive_access_worker уже запущен"
            )

        try:
            recover_interrupted_jobs()

            self.stdout.write(
                "Inactive access worker готов"
            )

            while True:
                job = (
                    InactiveAccessJob.objects
                    .filter(status="queued")
                    .order_by("created_at")
                    .first()
                )

                if job:
                    self.stdout.write(
                        f"Задание {job.pk}: {job.kind}"
                    )

                    execute_job(job)

                    self.stdout.write(
                        f"Завершено: {job.status}"
                    )

                if options["once"]:
                    break

                if not job:
                    time.sleep(2)

        finally:
            release_worker_lock()
