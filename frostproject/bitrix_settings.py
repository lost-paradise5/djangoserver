from .settings import *  # noqa: F401,F403


ROOT_URLCONF = "frostproject.bitrix_urls"

WSGI_APPLICATION = "frostproject.bitrix_wsgi.application"

DEBUG = False


# Логи отдельного Bitrix-приложения в stdout Docker.
LOGGING.setdefault("version", 1)
LOGGING.setdefault("disable_existing_loggers", False)
LOGGING.setdefault("formatters", {})
LOGGING.setdefault("handlers", {})
LOGGING.setdefault("loggers", {})

LOGGING["formatters"]["bitrix_verbose"] = {
    "format": (
        "{asctime} {levelname} {name}: {message}"
    ),
    "style": "{",
}

LOGGING["handlers"]["bitrix_console"] = {
    "class": "logging.StreamHandler",
    "formatter": "bitrix_verbose",
}

LOGGING["loggers"]["frostapp.views"] = {
    "handlers": ["bitrix_console"],
    "level": "INFO",
    "propagate": False,
}

LOGGING["loggers"][
    "frostapp.services.bitrix_cash_reboot"
] = {
    "handlers": ["bitrix_console"],
    "level": "INFO",
    "propagate": False,
}
