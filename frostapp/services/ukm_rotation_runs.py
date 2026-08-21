import json
import uuid
from typing import Any

from django.utils import timezone

from frostapp.models import UkmRotationRun, UkmRotationRunItem


def _json_safe(value: Any) -> Any:
    """Готовит диагностические данные к сохранению в JSONField."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


class UkmRotationRunRecorder:
    """Необязательная запись прогресса management-команды для веб-интерфейса."""

    def __init__(self, run_id: str | uuid.UUID | None):
        self.run_id = uuid.UUID(str(run_id)) if run_id else None

    @property
    def enabled(self) -> bool:
        return self.run_id is not None

    def _update(self, **values) -> None:
        if not self.enabled:
            return
    
        UkmRotationRun.objects.filter(
            id=self.run_id
        ).update(**values)

    def start(self, *, total_users: int, target_store_ids: list[int], options: dict) -> None:
        if not self.enabled:
            return
        now = timezone.now()
        self._update(
            status="running",
            total_users=int(total_users),
            processed_users=0,
            rotated_users=0,
            partial_users=0,
            skipped_users=0,
            failed_users=0,
            target_store_ids=[int(store_id) for store_id in target_store_ids],
            options=_json_safe(options),
            error="",
            started_at=now,
            heartbeat_at=now,
            finished_at=None,
        )
        UkmRotationRunItem.objects.filter(run_id=self.run_id).delete()

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
            "inn": str(info.get("inn") or "")[:20],
        }
        store_results = list(info.get("store_results") or [])

        if store_results:
            rows = []
            for store_result in store_results:
                rows.append(UkmRotationRunItem(
                    **common,
                    store_id=store_result.get("storeid"),
                    role_id=store_result.get("roleid"),
                    cashier_id=store_result.get("cashier_id"),
                    status=str(store_result.get("store_status") or info.get("status") or "unknown")[:32],
                    message=str(store_result.get("store_summary") or info.get("error") or ""),
                    details=_json_safe({
                        "user_status": info.get("status"),
                        "duration_sec": info.get("duration_sec"),
                        "cashier_id_source": store_result.get("cashier_id_source"),
                        "org_inn_check": store_result.get("org_inn_check") or {},
                        "sync": store_result.get("sync") or {},
                    }),
                ))
            UkmRotationRunItem.objects.bulk_create(rows, batch_size=100)
        else:
            UkmRotationRunItem.objects.create(
                **common,
                store_id=None,
                role_id=None,
                cashier_id=info.get("cashier_id"),
                status=str(info.get("status") or "unknown")[:32],
                message=str(info.get("error") or ""),
                details=_json_safe({
                    "duration_sec": info.get("duration_sec"),
                    "stores": info.get("stores") or [],
                }),
            )

        self._update(
            processed_users=int(processed_users),
            rotated_users=int(rotated_users),
            partial_users=int(partial_users),
            skipped_users=int(skipped_users),
            failed_users=int(failed_users),
            heartbeat_at=timezone.now(),
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
        now = timezone.now()
        self._update(
            status=status,
            processed_users=int(processed_users),
            rotated_users=int(rotated_users),
            partial_users=int(partial_users),
            skipped_users=int(skipped_users),
            failed_users=int(failed_users),
            summary={"elapsed_sec": round(float(elapsed_sec), 2)},
            heartbeat_at=now,
            finished_at=now,
        )

    def fail(self, error: str) -> None:
        if not self.enabled:
            return
        now = timezone.now()
        self._update(
            status="failed",
            error=str(error or "Неизвестная ошибка"),
            heartbeat_at=now,
            finished_at=now,
        )
