from .settings import *  # noqa: F401,F403


# Отдельный список маршрутов только для Bitrix-приложения.
ROOT_URLCONF = "frostproject.bitrix_urls"

# Отдельная WSGI-точка входа.
WSGI_APPLICATION = "frostproject.bitrix_wsgi.application"

# Подробные страницы ошибок наружу не показываем.
DEBUG = False


# Логи Bitrix-приложения в stdout Docker.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,

    "formatters": {
        "bitrix_verbose": {
            "format": (
                "{asctime} {levelname} "
                "{name}: {message}"
            ),
            "style": "{",
        },
    },

    "handlers": {
        "bitrix_console": {
            "class": "logging.StreamHandler",
            "formatter": "bitrix_verbose",
        },
    },

    "loggers": {
        "frostapp.views": {
            "handlers": ["bitrix_console"],
            "level": "INFO",
            "propagate": False,
        },

        "frostapp.services.bitrix_cash_reboot": {
            "handlers": ["bitrix_console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
