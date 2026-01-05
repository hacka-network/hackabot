import os
import sys
from pathlib import Path

import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

BASE_DIR = Path(__file__).resolve().parent.parent

TESTING = "pytest" in sys.modules

if TESTING:
    SECRET_KEY = os.environ.get(
        "DJANGO_SECRET_KEY", "test-secret-key-for-testing-only"
    )
    HACKABOT_ENV = os.environ.get("HACKABOT_ENV", "dev")
else:
    SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
    HACKABOT_ENV = os.environ.get("HACKABOT_ENV")
    if HACKABOT_ENV not in ("dev", "production"):
        raise RuntimeError("HACKABOT_ENV must be one of: dev/production")

IS_PRODUCTION = HACKABOT_ENV == "production"

DEBUG = HACKABOT_ENV == "dev"
ALLOWED_HOSTS = ["*"] if DEBUG else ["bot.hacka.network"]

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "hackabot.apps.bot",
    "hackabot.apps.worker",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

ROOT_URLCONF = "hackabot.urls"
APPEND_SLASH = True

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    },
]

ASGI_APPLICATION = "hackabot.asgi.application"

# Database
if IS_PRODUCTION:
    import dj_database_url

    DATABASES = {
        "default": dj_database_url.config(conn_max_age=600, ssl_require=True)
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    dict(
        NAME="django.contrib.auth.password_validation."
        "UserAttributeSimilarityValidator"
    ),
    dict(
        NAME="django.contrib.auth.password_validation.MinimumLengthValidator"
    ),
    dict(
        NAME="django.contrib.auth.password_validation.CommonPasswordValidator"
    ),
    dict(
        NAME="django.contrib.auth.password_validation.NumericPasswordValidator"
    ),
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_ROOT = BASE_DIR / "staticfiles"
STATIC_URL = "/static/"

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

if IS_PRODUCTION:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    DISABLE_SERVER_SIDE_CURSORS = True

# Sentry
SENTRY_DSN = os.environ.get("SENTRY_DSN")
if SENTRY_DSN:
    sentry_sdk.init(
        environment="production" if IS_PRODUCTION else "dev",
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        send_default_pii=False,
    )
