"""
URL configuration for frostproject project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, re_path
from frostapp.views import (
    queue_create,
    queue_update,
    queue_block,
    queue_vacation,
    register_cashier, 
    get_qr_code_by_tg,
    update_cashier,
    delete_cashier,
    get_qr_code_by_employee_id,
    export_stores_xml,
    employee_identification,
    sm_get_dbname,         
    sm_list_databases,
    agent_auth_start,
    agent_auth_select_store,
    agent_auth_verify_pin,
    sm_staff_list,
    sm_staff_columns,
    sm_sql,
    pos_list_by_tg,
    pos_reboot,
    sm_staff_sync_inn,
    sm_staff_list_by_db,
    sm_staff_ui_list,
    sm_staff_ui_edit_inn,
    sm_staff_ui_sync_inn_one,
    vpn_ui_login,
    vpn_ui_pin,
    vpn_ui_users,
    vpn_ui_toggle,
    ad_ui_lookup,
    ldap_tools_home,
    ldap_tools_employees,
    ldap_tools_sync_page,
    ldap_tools_sync_stream,
    ldap_tools_sync_log_download,
    tg_admin_badge_start,
    tg_admin_badge_admins,
    tg_admin_badge_request,
    tg_admin_badge_decision,
    bitrix_inactive_users_ui,
    bitrix_users_toggle_active,
    sm_oracle_inactive_users_ui,
    sm_oracle_users_block,
    ad_ui_users,
    ad_ui_users_toggle,
    inactive_users_report_send_to_bitrix,
)

urlpatterns = [
    path('admin/', admin.site.urls),

    # Четыре эндпойнта
    path('queue/create/', queue_create, name='queue_create'),
    path('queue/update/', queue_update, name='queue_update'),
    path('queue/block/', queue_block, name='queue_block'),
    path('queue/vacation/', queue_vacation, name='queue_vacation'),
    path('queue/register_cashier/', register_cashier, name='register_cashier'),
    path('get_qr_code/', get_qr_code_by_tg, name='get_qr_code'),
    path('queue/update_cashier/', update_cashier, name='update_cashier'),
    path('queue/delete_cashier/', delete_cashier, name='delete_cashier'),
    path('get_qr_code_by_employee_id/', get_qr_code_by_employee_id, name='get_qr_code_by_employee_id'),
    path('export/stores/xml/', export_stores_xml, name='export_stores_xml'),
    path('employee-identification/', employee_identification, name='employee_identification'),
    path('sm/dbname/', sm_get_dbname, name='sm_get_dbname'),
    path('sm/databases/', sm_list_databases, name='sm_list_databases'),
    path('agent/auth/start/', agent_auth_start, name='agent_auth_start'),
    path('agent/auth/select_store/', agent_auth_select_store, name='agent_auth_select_store'),
    path('agent/auth/verify_pin/', agent_auth_verify_pin, name='agent_auth_verify_pin'),
    path('sm/staff/', sm_staff_list, name='sm_staff_list'),
    path('sm/staff/columns/', sm_staff_columns, name='sm_staff_columns'),
    path('sm/sql/', sm_sql, name='sm_sql'),
    path('pos/by_tg/', pos_list_by_tg, name='pos_list_by_tg'),
    path('pos/reboot/', pos_reboot, name='pos_reboot'),
    path('sm/staff/sync-inn/', sm_staff_sync_inn, name='sm_staff_sync_inn'),
    path('sm/staff/by-db/', sm_staff_list_by_db, name='sm_staff_list_by_db'),
    path('ui/smstaff/', sm_staff_ui_list, name='sm_staff_ui_list'),
    re_path(r'^ui/smstaff/(?P<db>[^/]+)/edit/(?P<staff_id>-?\d+)/$', 
            sm_staff_ui_edit_inn, 
            name='sm_staff_ui_edit_inn'),
    path('ui/smstaff/sync-inn-one/', sm_staff_ui_sync_inn_one, name='sm_staff_ui_sync_inn_one'),
    path("ui/vpn/", vpn_ui_login, name="vpn_ui_login"),
    path("ui/vpn/pin/", vpn_ui_pin, name="vpn_ui_pin"),
    path("ui/vpn/users/", vpn_ui_users, name="vpn_ui_users"),
    path("ui/vpn/toggle/", vpn_ui_toggle, name="vpn_ui_toggle"),
    path("ui/ad/lookup/", ad_ui_lookup, name="ad_ui_lookup"),

    path("ui/ldap-tools/", ldap_tools_home, name="ldap_tools_home"),
    path("ui/ldap-tools/employees/", ldap_tools_employees, name="ldap_tools_employees"),

    # страница запуска (test/apply)
    path("ui/ldap-tools/sync/<str:mode>/", ldap_tools_sync_page, name="ldap_tools_sync_page"),

    # SSE поток прогресса
    path("ui/ldap-tools/sync/<str:mode>/stream/", ldap_tools_sync_stream, name="ldap_tools_sync_stream"),

    # скачать лог
    path("ui/ldap-tools/sync/log/<str:filename>/", ldap_tools_sync_log_download, name="ldap_tools_sync_log_download"),

    # Telegram-bot: запрос бейджа админа
    path('tg/admin-badge/start/', tg_admin_badge_start, name='tg_admin_badge_start'),
    path('tg/admin-badge/admins/', tg_admin_badge_admins, name='tg_admin_badge_admins'),
    path('tg/admin-badge/request/', tg_admin_badge_request, name='tg_admin_badge_request'),
    path('tg/admin-badge/decision/', tg_admin_badge_decision, name='tg_admin_badge_decision'),
    path("ui/bitrix/inactive-users/", bitrix_inactive_users_ui, name="bitrix_inactive_users_ui"),
    path("ui/bitrix/inactive-users/toggle/", bitrix_users_toggle_active, name="bitrix_users_toggle_active"),
    path("ui/sm/oracle-inactive-users/", sm_oracle_inactive_users_ui, name="sm_oracle_inactive_users_ui"),
    path("ui/sm/oracle-inactive-users/block/", sm_oracle_users_block, name="sm_oracle_users_block"),
    path("ui/ad-users/", ad_ui_users, name="ad_ui_users"),
    path("ui/ad-users/toggle/", ad_ui_users_toggle, name="ad_ui_users_toggle"),


    path("api/reports/inactive-users/send/", inactive_users_report_send_to_bitrix, name="inactive_users_report_send_to_bitrix"),
]
