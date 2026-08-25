from .settings import *  # noqa: F401,F403


# Отдельный список маршрутов только для Bitrix-приложения.
ROOT_URLCONF = "frostproject.bitrix_urls"

# Отдельная WSGI-точка входа.
WSGI_APPLICATION = "frostproject.bitrix_wsgi.application"

# Подробные страницы ошибок наружу не показываем.
DEBUG = False
