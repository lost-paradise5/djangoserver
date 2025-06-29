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
    register_cashier
)

urlpatterns = [
    path('admin/', admin.site.urls),

    # Четыре эндпойнта
    path('queue/create/', queue_create, name='queue_create'),
    path('queue/update/', queue_update, name='queue_update'),
    path('queue/block/', queue_block, name='queue_block'),
    path('queue/vacation/', queue_vacation, name='queue_vacation'),
    path('queue/register_cashier/', register_cashier, name='register_cashier'),
]
