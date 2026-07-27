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
from django.conf import settings
from django.conf.urls.static import static

from frostapp import views
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
    ad_ui_lookup_vpn_toggle,
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
    sm_staff_ui_create,
    maxbot_process_update,
    working_employees_excel_page,
    working_employees_excel_generate,
    export_stores_excel,
    sync_stores_postgres,
    admin_badge_transfer_report_excel,
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
    path('export/stores/excel/', export_stores_excel, name='export_stores_excel'),
    path('employee-identification/', employee_identification, name='employee_identification'),
    path('sm/dbname/', sm_get_dbname, name='sm_get_dbname'),
    path('sm/databases/', sm_list_databases, name='sm_list_databases'),
    path('agent/auth/start/', agent_auth_start, name='agent_auth_start'),
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
    path("ui/ad/lookup/vpn-toggle/", ad_ui_lookup_vpn_toggle, name="ad_ui_lookup_vpn_toggle"),

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


    path('ui/smstaff/create/', sm_staff_ui_create, name='sm_staff_ui_create'),

    path("api/maxbot/process/", maxbot_process_update, name="maxbot_process_update"),













    path("maxbot/", views.maxbot_dashboard, name="maxbot_dashboard"),

    path("maxbot/roles/", views.maxbot_role_list, name="maxbot_role_list"),
    path("maxbot/roles/create/", views.maxbot_role_create, name="maxbot_role_create"),
    path("maxbot/roles/<int:pk>/edit/", views.maxbot_role_edit, name="maxbot_role_edit"),
    path("maxbot/roles/<int:pk>/delete/", views.maxbot_role_delete, name="maxbot_role_delete"),

    path("maxbot/employees/", views.maxbot_employee_list, name="maxbot_employee_list"),
    path("maxbot/employees/create/", views.maxbot_employee_create, name="maxbot_employee_create"),
    path("maxbot/employees/<int:pk>/edit/", views.maxbot_employee_edit, name="maxbot_employee_edit"),
    path("maxbot/employees/<int:pk>/delete/", views.maxbot_employee_delete, name="maxbot_employee_delete"),

    path("maxbot/vehicles/", views.maxbot_vehicle_list, name="maxbot_vehicle_list"),
    path("maxbot/vehicles/create/", views.maxbot_vehicle_create, name="maxbot_vehicle_create"),
    path("maxbot/vehicles/<int:pk>/edit/", views.maxbot_vehicle_edit, name="maxbot_vehicle_edit"),
    path("maxbot/vehicles/<int:pk>/delete/", views.maxbot_vehicle_delete, name="maxbot_vehicle_delete"),

    path("maxbot/scenarios/", views.maxbot_scenario_list, name="maxbot_scenario_list"),
    path("maxbot/scenarios/create/", views.maxbot_scenario_create, name="maxbot_scenario_create"),
    path("maxbot/scenarios/<int:pk>/edit/", views.maxbot_scenario_edit, name="maxbot_scenario_edit"),
    path("maxbot/scenarios/<int:pk>/delete/", views.maxbot_scenario_delete, name="maxbot_scenario_delete"),

    path("maxbot/scenarios/<int:scenario_id>/questions/", views.maxbot_scenario_questions, name="maxbot_scenario_questions"),
    path("maxbot/scenarios/<int:scenario_id>/questions/create/", views.maxbot_question_create, name="maxbot_question_create"),
    path("maxbot/questions/<int:pk>/edit/", views.maxbot_question_edit, name="maxbot_question_edit"),
    path("maxbot/questions/<int:pk>/delete/", views.maxbot_question_delete, name="maxbot_question_delete"),
    path("maxbot/scenarios/<int:scenario_id>/questions/<int:question_id>/set-first/", views.maxbot_question_set_first, name="maxbot_question_set_first"),

    path("maxbot/questions/<int:question_id>/options/", views.maxbot_question_options, name="maxbot_question_options"),
    path("maxbot/questions/<int:question_id>/options/create/", views.maxbot_option_create, name="maxbot_option_create"),
    path("maxbot/options/<int:pk>/edit/", views.maxbot_option_edit, name="maxbot_option_edit"),
    path("maxbot/options/<int:pk>/delete/", views.maxbot_option_delete, name="maxbot_option_delete"),

    path("maxbot/requests/", views.maxbot_request_list, name="maxbot_request_list"),
    path("maxbot/requests/<int:pk>/", views.maxbot_request_detail, name="maxbot_request_detail"),
    path("maxbot/requests/export/", views.maxbot_request_export_excel, name="maxbot_request_export_excel"),



    path(
        "working-employees-sync/",
        views.working_employees_sync_page,
        name="working_employees_sync_page",
    ),
    path(
        "working-employees-sync/run/",
        views.working_employees_sync_run,
        name="working_employees_sync_run",
    ),
    path(
        "working-employees-sync/supermag/run/",
        views.working_employees_sync_supermag_run,
        name="working_employees_sync_supermag_run",
    ),
    path(
        "working-employees-sync/supermag/test-run/",
        views.working_employees_sync_supermag_test_run,
        name="working_employees_sync_supermag_test_run",
    ),


    path(
        "working-employees-by-stores-excel/",
        views.working_employees_by_stores_excel,
        name="working_employees_by_stores_excel",
    ),

    path(
        "reports/admin-badge-transfer-excel/<str:period>/",
        admin_badge_transfer_report_excel,
        name="admin_badge_transfer_report_excel",
    ),
    path(
        "ui/mobile-employees-sync/",
        views.mobile_employees_sync_ui,
        name="mobile_employees_sync_ui",
    ),

    path(
        "max/mobile-supermag/stores/",
        views.max_mobile_supermag_stores,
        name="max_mobile_supermag_stores",
    ),
    path(
        "max/mobile-supermag/open/",
        views.max_mobile_supermag_open,
        name="max_mobile_supermag_open",
    ),
    
    path('reports/working-employees-excel/', working_employees_excel_page, name='working_employees_excel_page'),
    path('reports/working-employees-excel/generate/', working_employees_excel_generate, name='working_employees_excel_generate'),

    
    path('sync/stores/', sync_stores_postgres, name='sync_stores_postgres'),
    path("ui/smstaff/create2/", views.sm_staff_ui_create2, name="sm_staff_ui_create2"),

    path("ui/smstaff/batch-by-store/", views.sm_staff_ui_batch_by_store, name="sm_staff_ui_batch_by_store"),
    path("ui/smstaff/batch-by-store/download/", views.sm_staff_ui_batch_by_store_download, name="sm_staff_ui_batch_by_store_download"),
]
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
