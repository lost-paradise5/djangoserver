# djangoserver/frostapp/management/commands/rotate_qr_codes.py

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
    UKM5_FULL_XML_STORE_IDS,
    TRM_SMALL_MAX,
)

from itertools import islice
from zoneinfo import ZoneInfo
from datetime import timezone as dt_tz
import logging
import os

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
    Ежедневная ротация QR/пароля (PostgreSQL + UKM4 + UKM5 + конвертер import4staffbonus)
    для всех пользователей, у которых:

      • есть tg_id (users.tg_id не NULL)
      • есть хотя бы одна запись в ukm_users с storeid из UKM5_FULL_XML_STORE_IDS

    Для КАЖДОГО такого пользователя:

      1) employee_id проверяется как ИНН (10/12 цифр) через ensure_plain_inn().
      2) В trm_in_users ищется кассир (ИНН + ФИО):
         - если найден → используем этот id (единый для всех магазинов);
         - если нет   → берём MAX(id)+1 как базовый id.
      3) Генерируется новый пароль через build_user_password(ИНН).
      4) В PostgreSQL обновляется QRCode + OpenInSystem (system_id=9) через _set_password_pg().
      5) Для КАЖДОГО магазина из ukm_users:
         - UKM4 + XML UKM5 обновляются через _update_store_mysql_and_xml_for_single_store();
         - конвертер import4staffbonus.users + signal обновляется через
           _write_converter_user_and_signal() с тем же cashier_id, что и в trm_in_users.
      6) В конце отправляется один сводный лог в MAX администратору.
    """

    help = "Ежедневное обновление QR/пароля для пользователей с доступом к магазину УКМ и tg_id."

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
            help='Ограничить обработку одним user_id (но при этом всё равно требуется tg_id и подходящий storeid)'
        )
        parser.add_argument('--tz', type=str, default=os.getenv('ROTATION_TZ', 'Europe/Stockholm'))
        parser.add_argument(
            '--idempotent',
            action='store_true',
            help='Пропускать пользователя, если сегодня уже есть свежий QR (по локальной дате)'
        )

    def handle(self, *args, **opts):
        if not self._acquire_lock():
            self.stdout.write(self.style.WARNING("Другой запуск уже идёт — выхожу."))
            return

        try:
            bs = opts['batch_size']
            tz = ZoneInfo(opts['tz'])
            today_local = timezone.now().astimezone(tz).date()

            anchor_store_ids = sorted(int(x) for x in (UKM5_FULL_XML_STORE_IDS or {2013}))

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
                f"candidates={total}, batch={bs}, tz={opts['tz']}, "
                f"dry={opts['dry_run']}, idempotent={opts['idempotent']}"
            )

            rotated = skipped = failed = 0
            details = []  # для MAX-лога

            for chunk in batched(qs, bs):
                for user in chunk:
                    status, info = self._rotate_one_user(
                        user=user,
                        today_local=today_local,
                        tz=tz,
                        idempotent=opts['idempotent'],
                        dry_run=opts['dry_run'],
                    )
                    details.append(info)

                    if status == 'rotated':
                        rotated += 1
                    elif status == 'skipped':
                        skipped += 1
                    else:
                        failed += 1

            self.stdout.write(
                self.style.SUCCESS(
                    f"Готово: rotated={rotated}, skipped={skipped}, failed={failed}"
                )
            )

            self._send_max_summary(
                today_local=today_local,
                tz_name=opts['tz'],
                total=total,
                rotated=rotated,
                skipped=skipped,
                failed=failed,
                details=details,
                dry_run=opts['dry_run'],
            )

        finally:
            self._release_lock()

    def _rotate_one_user(self, user, today_local, tz, idempotent: bool, dry_run: bool):
        info = {
            "user_id": user.id,
            "fio": (user.full_name or "").strip(),
            "inn": (user.employee_id or "").strip(),
            "tg_id": getattr(user, "tg_id", None),
            "status": "",
            "error": "",
            "stores": [],
            "cashier_id": None,
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
                        info["error"] = "QR уже обновлён сегодня (idempotent)"
                        logger.info(f"[ROTATE] user_id={user.id} пропущен: уже есть QR на {today_local}")
                        return "skipped", info

            fio = info["fio"]
            inn_raw = info["inn"]

            if not fio or not inn_raw:
                info["status"] = "skipped_no_fio_or_inn"
                info["error"] = "full_name или employee_id пусты"
                logger.warning(f"[ROTATE] user_id={user.id} пропуск: fio/inn пусты")
                return "skipped", info

            try:
                plain_inn = ensure_plain_inn(inn_raw)
            except Exception as e:
                info["status"] = "skipped_bad_inn"
                info["error"] = f"Некорректный ИНН: {e}"
                logger.warning(f"[ROTATE] user_id={user.id} пропуск: {info['error']}")
                return "skipped", info

            ukm_links = list(UKMUser.objects.filter(user_id=user.id).values('storeid', 'roleid'))
            info["stores"] = ukm_links
            if not ukm_links:
                info["status"] = "skipped_no_ukm_users"
                info["error"] = "Нет записей в ukm_users"
                logger.warning(f"[ROTATE] user_id={user.id} пропуск: нет ukm_users")
                return "skipped", info

            if dry_run:
                info["status"] = "dry_run"
                info["error"] = "Запуск с --dry-run, изменения не вносились"
                logger.info(f"[ROTATE] [DRY] user_id={user.id}, inn={plain_inn}, stores={ukm_links!r}")
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
            logger.info(f"[ROTATE] user_id={user.id} новый пароль (masked)={masked}, len={len(new_password)}")

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
                        f"[ROTATE] get_trm_employee_id error user_id={user.id} storeid={sid}: {e}",
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

                logger.info(
                    f"[ROTATE] user_id={user.id} storeid={sid}, roleId={role_id}, "
                    f"cashier_id_for_store={cashier_id_for_store}, found_in_trm={found}"
                )

                _update_store_mysql_and_xml_for_single_store(
                    store_id=sid,
                    cashier_id=cashier_id_for_store,
                    role_id=role_id,
                    plain_inn=plain_inn,
                    fio=fio,
                    password_plain=new_password,
                )

                _write_converter_user_and_signal(
                    cashier_id=int(converter_cashier_id),
                    plain_inn=plain_inn,
                    fio=fio,
                    password_plain=new_password,
                    store_id=sid,
                    role_id=role_id,
                )

            info["status"] = "rotated"
            info["cashier_id"] = int(converter_cashier_id) if converter_cashier_id is not None else None
            info["new_password"] = new_password
            return "rotated", info

        except Exception as e:
            logger.exception(f"[ROTATE] Сбой ротации для user_id={user.id}: {e}")
            info["status"] = "failed"
            info["error"] = str(e)
            return "failed", info

    def _human_status(self, status: str) -> str:
        mapping = {
            "rotated": "обновлено",
            "dry_run": "проверено без изменений (dry-run)",
            "already_rotated_today": "пропущено — уже обновлялся сегодня",
            "skipped_no_fio_or_inn": "пропущено — не заполнены ФИО или ИНН",
            "skipped_bad_inn": "пропущено — некорректный ИНН",
            "skipped_no_ukm_users": "пропущено — нет доступов в ukm_users",
            "failed": "ошибка",
        }
        return mapping.get(status or "", status or "неизвестно")

    def _send_max_summary(
        self,
        today_local,
        tz_name: str,
        total: int,
        rotated: int,
        skipped: int,
        failed: int,
        details: list,
        dry_run: bool,
    ) -> None:
        """
        Формирует и отправляет один сводный лог в MAX.
        Отправка идёт асинхронно.
        """
        try:
            header = (
                "🧪 [DRY-RUN] Плановое обновление QR-кодов и паролей"
                if dry_run else
                "✅ Обновление паролей"
            )

            lines = [
                header,
                "",
                f"1. Дата: {today_local.isoformat()} (TZ={tz_name})",
                f"2. Всего сотрудников: {total}",
                f"3. Обновлено: {rotated}",
                f"4. Пропущено: {skipped}",
                f"5. Ошибок: {failed}",
                "",
                "6. Сотрудники:",
            ]

            if not details:
                lines.append("1. Нет записей")
            else:
                for idx, info in enumerate(details, start=1):
                    fio = (info.get("fio") or "").strip() or "ФИО не указано"
                    stores_count = len(info.get("stores") or [])
                    status_label = self._human_status(info.get("status"))

                    lines.append(f"{idx}. {fio}")
                    lines.append(f"   Статус: {status_label}")
                    lines.append(f"   Доступов в УКМ: {stores_count}")

                    if info.get("error"):
                        lines.append(f"   Примечание: {info['error']}")

            _send_max_log_async("\n".join(lines))

        except Exception as e:
            logger.error(f"[ROTATE] Не удалось поставить сводный лог в очередь MAX: {e}", exc_info=True)

    def _acquire_lock(self):
        """
        Advisory lock в PostgreSQL для избежания параллельных запусков.
        """
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
