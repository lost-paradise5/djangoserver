import os

from django.core.wsgi import get_wsgi_application


# Используем отдельные настройки независимо от внешних переменных.
os.environ["DJANGO_SETTINGS_MODULE"] = (
    "frostproject.bitrix_settings"
)

application = get_wsgi_application()
