from django.urls import path
from django.views.generic import RedirectView

from frostapp import views


urlpatterns = [
    # При открытии корня домена направляем в приложение.
    path(
        "",
        RedirectView.as_view(
            url="/bitrix/cash-reboot/",
            permanent=False,
        ),
        name="bitrix_cash_reboot_root",
    ),

    path(
        "bitrix/cash-reboot/",
        views.bitrix_cash_reboot_app,
        name="bitrix_cash_reboot_app",
    ),
    path(
        "bitrix/cash-reboot/install/",
        views.bitrix_cash_reboot_install,
        name="bitrix_cash_reboot_install",
    ),
    path(
        "bitrix/cash-reboot/health/",
        views.bitrix_cash_reboot_health,
        name="bitrix_cash_reboot_health",
    ),
    path(
        "bitrix/cash-reboot/api/identify/",
        views.bitrix_cash_reboot_identify,
        name="bitrix_cash_reboot_identify",
    ),
    path(
        "bitrix/cash-reboot/api/devices/",
        views.bitrix_cash_reboot_devices,
        name="bitrix_cash_reboot_devices",
    ),


    path(
        "bitrix/cash-reboot/api/reserve-badge/",
        views.bitrix_cash_reboot_reserve_badge,
        name="bitrix_cash_reboot_reserve_badge",
    ),
    path(
        "bitrix/cash-reboot/api/reboot/",
        views.bitrix_cash_reboot_execute,
        name="bitrix_cash_reboot_execute",
    ),
]
