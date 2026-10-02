import json
import logging
import uuid
from typing import Any

from django.db import transaction
from django.utils import timezone

from frostapp.models import (
    UkmRotationRun,
    UkmRotationRunItem,
)
from frostapp.services.ukm_rotation_bitrix_auth import (
    audit_rotation,
    rotation_actor_label,
)


logger = logging.getLogger("ukm_logger")


def _json_safe(value: Any) -> Any:
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            default=str,
        )
    )


class UkmRotationRunRecorder:
    def __init__(
        self,
        run_id: str | uuid.UUID | None,
    ):
        self.run_id = (
            uuid.UUID(str(run_id))
            if run_id
            else None
        )

        self.initiator = {
            "type": "system",
            "fio": "Автоматический запуск / CLI",
        }

        if self.enabled:
            row = (
                UkmRotationRun.objects
                .filter(id=self.run_id)
                .values(
                    "requested_by",
                    "options",
                )
                .first()
            )

            if row is None:
                raise LookupError(
                    f"Запуск {self.run_id} не найден"
                )

            options = row["options"] or {}
            actor = options.get("initiator")

            self.initiator = (
                dict(actor)
                if isinstance(actor, dict) and actor
                else {
                    "type": "legacy",
                    "fio": (
                        row["requested_by"]
                        or "Автоматический запуск / CLI"
                    ),
                }
            )

    @property
    def enabled(self) -> bool:
        return self.run_id is not None

    def _update(self, **values) -> None:
        if self.enabled:
            (
                UkmRotationRun.objects
                .filter(id=self.run_id)
                .update(**values)
            )

    def start(
        self,
        *,
        total_users: int,
        target_store_ids: list[int],
        options: dict,
    ) -> None:
        if not self.enabled:
            return

        stamp = timezone.now()

        with transaction.atomic():
            run = (
                UkmRotationRun.objects
                .select_for_update()
                .get(id=self.run_id)
            )

            saved_options = dict(
                run.options or {}
            )

            merged_options = {
                **saved_options,
                **_json_safe(options),
            }

            # Инициатор сохраняется независимо от
            # параметров, переданных management-командой.
            merged_options["initiator"] = (
                _json_safe(self.initiator)
            )

            self._update(
                status="running",
                total_users=int(total_users),
                processed_users=0,
                rotated_users=0,
                partial_users=0,
                skipped_users=0,
                failed_users=0,
                target_store_ids=[
                    int(value)
                    for value in target_store_ids
                ],
                options=merged_options,
                error="",
                started_at=(
                    run.started_at or stamp
                ),
                heartbeat_at=stamp,
                finished_at=None,
            )

            (
                UkmRotationRunItem.objects
                .filter(run_id=self.run_id)
                .delete()
            )

            audit_rotation(
                "run.started",
                identity=self.initiator,
                run_id=self.run_id,
                details={
                    "total_users": total_users,
                    "store_ids": target_store_ids,
                    "options": options,
                },
            )

        logger.info(
            "[ROTATE][START] run_id=%s "
            "initiator=%s total=%s stores=%s",
            self.run_id,
            rotation_actor_label(self.initiator),
            total_users,
            target_store_ids,
        )

    def record_user(
        self,
        *,
        info: dict,
        processed_users: int,
        rotated_users: int,
        partial_users: int,
        skipped_users: int,
        failed_users: int,
    ) -> None:
        if not self.enabled:
            return

        common = {
            "run_id": self.run_id,
            "user_id": info.get("user_id"),
            "fio": str(info.get("fio") or ""),
            "inn": str(
                info.get("inn") or ""
            )[:20],
        }

        store_results = list(
            info.get("store_results") or []
        )

        rows = []

        if store_results:
            for result in store_results:
                rows.append(
                    UkmRotationRunItem(
                        **common,

                        store_id=result.get(
                            "storeid"
                        ),
                        role_id=result.get(
                            "roleid"
                        ),
                        cashier_id=result.get(
                            "cashier_id"
                        ),

                        status=str(
                            result.get("store_status")
                            or info.get("status")
                            or "unknown"
                        )[:32],

                        message=str(
                            result.get("store_summary")
                            or info.get("error")
                            or ""
                        ),

                        details=_json_safe({
                            "initiator": (
                                self.initiator
                            ),
                            "user_status": (
                                info.get("status")
                            ),
                            "duration_sec": (
                                info.get("duration_sec")
                            ),
                            "cashier_id_source": (
                                result.get(
                                    "cashier_id_source"
                                )
                            ),
                            "org_inn_check": (
                                result.get(
                                    "org_inn_check"
                                ) or {}
                            ),
                            "sync": (
                                result.get("sync")
                                or {}
                            ),
                        }),
                    )
                )

        else:
            rows.append(
                UkmRotationRunItem(
                    **common,

                    store_id=None,
                    role_id=None,
                    cashier_id=info.get(
                        "cashier_id"
                    ),

                    status=str(
                        info.get("status")
                        or "unknown"
                    )[:32],

                    message=str(
                        info.get("error") or ""
                    ),

                    details=_json_safe({
                        "initiator": self.initiator,
                        "duration_sec": (
                            info.get("duration_sec")
                        ),
                        "stores": (
                            info.get("stores")
                            or []
                        ),
                    }),
                )
            )

        with transaction.atomic():
            (
                UkmRotationRunItem.objects
                .bulk_create(
                    rows,
                    batch_size=100,
                )
            )

            self._update(
                processed_users=int(
                    processed_users
                ),
                rotated_users=int(
                    rotated_users
                ),
                partial_users=int(
                    partial_users
                ),
                skipped_users=int(
                    skipped_users
                ),
                failed_users=int(
                    failed_users
                ),
                heartbeat_at=timezone.now(),
            )

        logger.info(
            "[ROTATE][RESULT] run_id=%s "
            "initiator=%s employee_user_id=%s "
            "status=%s processed=%s",
            self.run_id,
            rotation_actor_label(self.initiator),
            info.get("user_id"),
            info.get("status"),
            processed_users,
        )

    def finish(
        self,
        *,
        status: str,
        processed_users: int,
        rotated_users: int,
        partial_users: int,
        skipped_users: int,
        failed_users: int,
        elapsed_sec: float,
    ) -> None:
        if not self.enabled:
            return

        stamp = timezone.now()

        summary = {
            "elapsed_sec": round(
                float(elapsed_sec),
                2,
            ),
            "initiator": self.initiator,
        }

        with transaction.atomic():
            self._update(
                status=status,
                processed_users=int(
                    processed_users
                ),
                rotated_users=int(
                    rotated_users
                ),
                partial_users=int(
                    partial_users
                ),
                skipped_users=int(
                    skipped_users
                ),
                failed_users=int(
                    failed_users
                ),
                summary=_json_safe(summary),
                heartbeat_at=stamp,
                finished_at=stamp,
            )

            audit_rotation(
                "run.finished",
                identity=self.initiator,
                run_id=self.run_id,
                details={
                    **summary,
                    "status": status,
                    "processed": processed_users,
                    "rotated": rotated_users,
                    "partial": partial_users,
                    "skipped": skipped_users,
                    "failed": failed_users,
                },
            )

        logger.info(
            "[ROTATE][FINISH] run_id=%s "
            "initiator=%s status=%s",
            self.run_id,
            rotation_actor_label(self.initiator),
            status,
        )

    def fail(self, error: str) -> None:
        if not self.enabled:
            return

        stamp = timezone.now()

        with transaction.atomic():
            run = (
                UkmRotationRun.objects
                .select_for_update()
                .get(id=self.run_id)
            )

            # Команда и worker могут обработать
            # одно исключение последовательно.
            if run.status == "failed":
                return

            self._update(
                status="failed",
                error=str(
                    error or "Неизвестная ошибка"
                ),
                heartbeat_at=stamp,
                finished_at=stamp,
            )

            audit_rotation(
                "run.failed",
                identity=self.initiator,
                run_id=self.run_id,
                details={
                    "error": str(
                        error
                        or "Неизвестная ошибка"
                    ),
                },
            )

        logger.error(
            "[ROTATE][FAILED] run_id=%s "
            "initiator=%s error=%s",
            self.run_id,
            rotation_actor_label(self.initiator),
            error,
        )











































# import json
# import uuid
# from typing import Any

# from django.utils import timezone

# from frostapp.models import UkmRotationRun, UkmRotationRunItem


# def _json_safe(value: Any) -> Any:
#     """Готовит диагностические данные к сохранению в JSONField."""
#     return json.loads(json.dumps(value, ensure_ascii=False, default=str))


# class UkmRotationRunRecorder:
#     """Необязательная запись прогресса management-команды для веб-интерфейса."""

#     def __init__(self, run_id: str | uuid.UUID | None):
#         self.run_id = uuid.UUID(str(run_id)) if run_id else None

#     @property
#     def enabled(self) -> bool:
#         return self.run_id is not None

#     def _update(self, **values) -> None:
#         if not self.enabled:
#             return
    
#         UkmRotationRun.objects.filter(
#             id=self.run_id
#         ).update(**values)

#     def start(self, *, total_users: int, target_store_ids: list[int], options: dict) -> None:
#         if not self.enabled:
#             return
#         now = timezone.now()
#         self._update(
#             status="running",
#             total_users=int(total_users),
#             processed_users=0,
#             rotated_users=0,
#             partial_users=0,
#             skipped_users=0,
#             failed_users=0,
#             target_store_ids=[int(store_id) for store_id in target_store_ids],
#             options=_json_safe(options),
#             error="",
#             started_at=now,
#             heartbeat_at=now,
#             finished_at=None,
#         )
#         UkmRotationRunItem.objects.filter(run_id=self.run_id).delete()

#     def record_user(
#         self,
#         *,
#         info: dict,
#         processed_users: int,
#         rotated_users: int,
#         partial_users: int,
#         skipped_users: int,
#         failed_users: int,
#     ) -> None:
#         if not self.enabled:
#             return

#         common = {
#             "run_id": self.run_id,
#             "user_id": info.get("user_id"),
#             "fio": str(info.get("fio") or ""),
#             "inn": str(info.get("inn") or "")[:20],
#         }
#         store_results = list(info.get("store_results") or [])

#         if store_results:
#             rows = []
#             for store_result in store_results:
#                 rows.append(UkmRotationRunItem(
#                     **common,
#                     store_id=store_result.get("storeid"),
#                     role_id=store_result.get("roleid"),
#                     cashier_id=store_result.get("cashier_id"),
#                     status=str(store_result.get("store_status") or info.get("status") or "unknown")[:32],
#                     message=str(store_result.get("store_summary") or info.get("error") or ""),
#                     details=_json_safe({
#                         "user_status": info.get("status"),
#                         "duration_sec": info.get("duration_sec"),
#                         "cashier_id_source": store_result.get("cashier_id_source"),
#                         "org_inn_check": store_result.get("org_inn_check") or {},
#                         "sync": store_result.get("sync") or {},
#                     }),
#                 ))
#             UkmRotationRunItem.objects.bulk_create(rows, batch_size=100)
#         else:
#             UkmRotationRunItem.objects.create(
#                 **common,
#                 store_id=None,
#                 role_id=None,
#                 cashier_id=info.get("cashier_id"),
#                 status=str(info.get("status") or "unknown")[:32],
#                 message=str(info.get("error") or ""),
#                 details=_json_safe({
#                     "duration_sec": info.get("duration_sec"),
#                     "stores": info.get("stores") or [],
#                 }),
#             )

#         self._update(
#             processed_users=int(processed_users),
#             rotated_users=int(rotated_users),
#             partial_users=int(partial_users),
#             skipped_users=int(skipped_users),
#             failed_users=int(failed_users),
#             heartbeat_at=timezone.now(),
#         )

#     def finish(
#         self,
#         *,
#         status: str,
#         processed_users: int,
#         rotated_users: int,
#         partial_users: int,
#         skipped_users: int,
#         failed_users: int,
#         elapsed_sec: float,
#     ) -> None:
#         if not self.enabled:
#             return
#         now = timezone.now()
#         self._update(
#             status=status,
#             processed_users=int(processed_users),
#             rotated_users=int(rotated_users),
#             partial_users=int(partial_users),
#             skipped_users=int(skipped_users),
#             failed_users=int(failed_users),
#             summary={"elapsed_sec": round(float(elapsed_sec), 2)},
#             heartbeat_at=now,
#             finished_at=now,
#         )

#     def fail(self, error: str) -> None:
#         if not self.enabled:
#             return
#         now = timezone.now()
#         self._update(
#             status="failed",
#             error=str(error or "Неизвестная ошибка"),
#             heartbeat_at=now,
#             finished_at=now,
#         )
