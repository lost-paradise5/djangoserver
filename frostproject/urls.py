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
from django.urls import path
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
]
