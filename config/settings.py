"""Secure-by-default settings for the NABD SI production web application."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
if APP_ENV not in {"development", "production", "build"}:
    raise RuntimeError("APP_ENV must be development, production, or build.")
DEBUG = os.getenv("DJANGO_DEBUG", "0").strip().lower() in {"1", "true", "yes"}
IS_PRODUCTION = APP_ENV == "production"
APP_ROLE = os.getenv("APP_ROLE", "web").strip().lower()
if APP_ROLE not in {"web", "ingestion", "maintenance"}:
    raise RuntimeError("APP_ROLE must be web, ingestion, or maintenance.")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "").strip()
if IS_PRODUCTION and APP_ROLE == "web" and len(SECRET_KEY) < 50:
    raise RuntimeError("The production web role requires DJANGO_SECRET_KEY with at least 50 characters from Secrets Manager.")
if not SECRET_KEY:
    if IS_PRODUCTION and APP_ROLE != "web":
        import secrets
        SECRET_KEY = secrets.token_urlsafe(64)  # Ephemeral: non-web tasks never issue browser sessions.
    else:
        SECRET_KEY = "local-only-insecure-secret-key-do-not-deploy-" + ("x" * 40)
if IS_PRODUCTION and DEBUG:
    raise RuntimeError("DJANGO_DEBUG must be false in production.")


def csv_env(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


_allowed_hosts_env = csv_env("DJANGO_ALLOWED_HOSTS")
ALLOWED_HOSTS = _allowed_hosts_env or csv_env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver")
CSRF_TRUSTED_ORIGINS = [value.rstrip("/") for value in csv_env("DJANGO_CSRF_TRUSTED_ORIGINS")]
DJANGO_HEALTHCHECK_CIDRS = csv_env("DJANGO_HEALTHCHECK_CIDRS")
if IS_PRODUCTION:
    import ipaddress
    if APP_ROLE == "web" and not DJANGO_HEALTHCHECK_CIDRS:
        raise RuntimeError("The production web role requires the exact private subnet CIDRs used by the ALB health checks.")
    try:
        health_networks = [ipaddress.ip_network(value, strict=False) for value in DJANGO_HEALTHCHECK_CIDRS]
    except ValueError as exc:
        raise RuntimeError("DJANGO_HEALTHCHECK_CIDRS must contain valid private CIDR networks.") from exc
    if any(not network.is_private or network.prefixlen == 0 for network in health_networks):
        raise RuntimeError("DJANGO_HEALTHCHECK_CIDRS must be private networks, never a catch-all range.")
    if APP_ROLE == "web":
        if not _allowed_hosts_env or any(host.startswith(".") or "*" in host for host in _allowed_hosts_env):
            raise RuntimeError("The production web role requires explicit exact DJANGO_ALLOWED_HOSTS values; wildcards are not allowed.")
        allowed_hostnames = {host.lower() for host in _allowed_hosts_env}
        if not CSRF_TRUSTED_ORIGINS:
            raise RuntimeError("The production web role requires exact HTTPS DJANGO_CSRF_TRUSTED_ORIGINS values.")
        for origin in CSRF_TRUSTED_ORIGINS:
            parsed_origin = urlparse(origin)
            try:
                origin_port = parsed_origin.port
            except ValueError as exc:
                raise RuntimeError("Invalid production CSRF trusted origin.") from exc
            if (
                parsed_origin.scheme != "https"
                or not parsed_origin.hostname
                or parsed_origin.hostname.lower() not in allowed_hostnames
                or parsed_origin.username
                or parsed_origin.password
                or origin_port not in {None, 443}
                or parsed_origin.path
                or parsed_origin.query
                or parsed_origin.fragment
            ):
                raise RuntimeError("Production CSRF origins must be HTTPS origins whose exact host is in DJANGO_ALLOWED_HOSTS.")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "newsroom.apps.NewsroomConfig",
    "mozilla_django_oidc",
]

MIDDLEWARE = [
    "newsroom.middleware.StrictHostMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "newsroom.middleware.SessionTimeoutMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "newsroom.middleware.SecurityResponseHeadersMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "newsroom.context_processors.ui_settings",
            ],
        },
    },
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


def database_from_env():
    raw = os.getenv("DATABASE_URL", "").strip()
    if not raw:
        if IS_PRODUCTION:
            raise RuntimeError("Production requires DATABASE_URL for private PostgreSQL.")
        return {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "local.sqlite3"}}

    parsed = urlparse(raw)
    if parsed.scheme in {"postgres", "postgresql", "postgresql+psycopg"}:
        sslmode = os.getenv("DATABASE_SSLMODE", "verify-full" if IS_PRODUCTION else "disable").strip()
        rootcert = os.getenv("DATABASE_SSLROOTCERT", "/etc/ssl/certs/rds-global-bundle.pem" if IS_PRODUCTION else "").strip()
        if IS_PRODUCTION and sslmode != "verify-full":
            raise RuntimeError("Production PostgreSQL must use DATABASE_SSLMODE=verify-full.")
        if IS_PRODUCTION and (not rootcert or not Path(rootcert).is_file()):
            raise RuntimeError("Production requires DATABASE_SSLROOTCERT to point to the current AWS RDS CA bundle in the image.")
        options = {"sslmode": sslmode}
        if rootcert:
            options["sslrootcert"] = rootcert
        return {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": unquote(parsed.path.lstrip("/")),
                "USER": unquote(parsed.username or ""),
                "PASSWORD": unquote(parsed.password or ""),
                "HOST": parsed.hostname or "",
                "PORT": parsed.port or 5432,
                "CONN_MAX_AGE": 60,
                "CONN_HEALTH_CHECKS": True,
                "OPTIONS": options,
            }
        }
    if parsed.scheme == "sqlite" and not IS_PRODUCTION:
        return {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": unquote(parsed.path)}}
    raise RuntimeError("DATABASE_URL must be a PostgreSQL URL (or SQLite for local development).")


DATABASES = database_from_env()

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 14}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
AUTHENTICATION_BACKENDS = [
    "newsroom.auth.CognitoOIDCBackend",
    "django.contrib.auth.backends.ModelBackend",
]
LOGIN_URL = "/account/sign-in/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

# AWS Cognito hosted OIDC authorization-code flow, with PKCE and RS256/JWKS verification.
COGNITO_REGION = os.getenv("COGNITO_REGION", "eu-central-1").strip()
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "").strip()
COGNITO_DOMAIN = os.getenv("COGNITO_DOMAIN", "").strip().rstrip("/")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "").strip()
COGNITO_CLIENT_SECRET = os.getenv("COGNITO_CLIENT_SECRET", "").strip()
OIDC_CONFIGURED = all((COGNITO_USER_POOL_ID, COGNITO_DOMAIN, COGNITO_CLIENT_ID, COGNITO_CLIENT_SECRET))
if COGNITO_DOMAIN and urlparse(COGNITO_DOMAIN).scheme != "https":
    raise RuntimeError("COGNITO_DOMAIN must use HTTPS; OIDC client credentials must never traverse plaintext HTTP.")
if IS_PRODUCTION and APP_ROLE == "web" and not OIDC_CONFIGURED:
    raise RuntimeError("The production web role requires the Cognito OIDC pool, HTTPS domain, client id and client secret.")
if IS_PRODUCTION and APP_ROLE == "web":
    cognito_domain = urlparse(COGNITO_DOMAIN)
    try:
        cognito_port = cognito_domain.port
    except ValueError as exc:
        raise RuntimeError("Production Cognito domain contains an invalid port.") from exc
    if (
        not cognito_domain.hostname
        or cognito_domain.username
        or cognito_domain.password
        or cognito_domain.path
        or cognito_domain.query
        or cognito_domain.fragment
        or cognito_port not in {None, 443}
        or not COGNITO_USER_POOL_ID.startswith(f"{COGNITO_REGION}_")
    ):
        raise RuntimeError("Production Cognito domain and pool ID must match the configured AWS region and use a clean HTTPS origin.")
OIDC_RP_CLIENT_ID = COGNITO_CLIENT_ID
OIDC_RP_CLIENT_SECRET = COGNITO_CLIENT_SECRET
OIDC_RP_SIGN_ALGO = "RS256"
OIDC_TOKEN_USE_BASIC_AUTH = True
OIDC_TIMEOUT = 10
OIDC_RP_SCOPES = "openid email"
OIDC_USE_PKCE = True
OIDC_PKCE_CODE_CHALLENGE_METHOD = "S256"
OIDC_USE_NONCE = True
OIDC_VERIFY_SSL = True
OIDC_VERIFY_KID = True
OIDC_ALLOW_UNSECURED_JWT = False
OIDC_STORE_ACCESS_TOKEN = False
OIDC_STORE_ID_TOKEN = False
OIDC_CREATE_USER = True
OIDC_OP_AUTHORIZATION_ENDPOINT = f"{COGNITO_DOMAIN}/oauth2/authorize" if COGNITO_DOMAIN else ""
OIDC_OP_TOKEN_ENDPOINT = f"{COGNITO_DOMAIN}/oauth2/token" if COGNITO_DOMAIN else ""
OIDC_OP_USER_ENDPOINT = f"{COGNITO_DOMAIN}/oauth2/userInfo" if COGNITO_DOMAIN else ""
COGNITO_ISSUER = f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}" if COGNITO_USER_POOL_ID else ""
OIDC_OP_JWKS_ENDPOINT = f"{COGNITO_ISSUER}/.well-known/jwks.json" if COGNITO_ISSUER else ""
OIDC_AUTH_REQUEST_EXTRA_PARAMS = {"prompt": "login"}
OIDC_AUTHENTICATION_CALLBACK_URL = "oidc_authentication_callback"

LANGUAGE_CODE = "en"
TIME_ZONE = "Africa/Tripoli"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_BACKEND = (
    "whitenoise.storage.CompressedManifestStaticFilesStorage"
    if APP_ENV in {"production", "build"} else "whitenoise.storage.CompressedStaticFilesStorage"
)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": STATICFILES_BACKEND},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Server-side database sessions; the browser receives only an opaque session id.
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_NAME = "__Host-nabdsi_session" if IS_PRODUCTION else "nabdsi_session"
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = IS_PRODUCTION
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_SAVE_EVERY_REQUEST = True
CSRF_COOKIE_NAME = "__Host-nabdsi_csrf" if IS_PRODUCTION else "nabdsi_csrf"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = IS_PRODUCTION
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_USE_SESSIONS = True
CSRF_FAILURE_VIEW = "django.views.csrf.csrf_failure"

SECURE_SSL_REDIRECT = IS_PRODUCTION
SECURE_REDIRECT_EXEMPT = [r"^healthz$"] if IS_PRODUCTION else []
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 31_536_000 if IS_PRODUCTION else 0
# Production requires a dedicated app domain with HTTPS on all subdomains before rollout.
SECURE_HSTS_INCLUDE_SUBDOMAINS = IS_PRODUCTION
SECURE_HSTS_PRELOAD = IS_PRODUCTION
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

DATA_UPLOAD_MAX_MEMORY_SIZE = 1_048_576
FILE_UPLOAD_MAX_MEMORY_SIZE = 1_048_576
DATA_UPLOAD_MAX_NUMBER_FIELDS = 100

OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
TRANSLATION_PROVIDER = os.getenv("TRANSLATION_PROVIDER", "openai").strip().lower()
if IS_PRODUCTION:
    provider_endpoint = urlparse(OPENAI_BASE_URL)
    try:
        provider_port = provider_endpoint.port
    except ValueError as exc:
        raise RuntimeError("OPENAI_BASE_URL contains an invalid port.") from exc
    if (
        TRANSLATION_PROVIDER != "openai"
        or provider_endpoint.scheme != "https"
        or not provider_endpoint.hostname
        or provider_endpoint.username
        or provider_endpoint.password
        or provider_port not in {None, 443}
        or provider_endpoint.query
        or provider_endpoint.fragment
        or not OPENAI_MODEL
        or len(OPENAI_MODEL) > 100
    ):
        raise RuntimeError("Production requires an OpenAI-compatible model at a clean HTTPS endpoint.")
INGEST_WORKERS = max(1, min(8, int(os.getenv("INGEST_WORKERS", "4"))))
INGEST_LIMIT_PER_SOURCE = max(1, min(30, int(os.getenv("INGEST_LIMIT_PER_SOURCE", "10"))))

DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "security@nabdsi.example")
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = True
EMAIL_TIMEOUT = 10

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"structured": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "structured"}},
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.security": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "mozilla_django_oidc": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}
