"""
Django settings for the Developer Vault project.

All configuration is sourced exclusively from environment variables
(12-factor). No secrets, credentials, or host-specific values are
hardcoded here.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from pathlib import Path

from django.conf import settings as _django_settings

try:
    from dotenv import load_dotenv

    # Best-effort load of a local .env file during development. In
    # containerised environments the variables are injected by
    # docker-compose and this is a no-op.
    load_dotenv()
except ImportError:  # pragma: no cover - python-dotenv is a soft dep here
    pass


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


# --- Vault write-protection --------------------------------------------- #
# PBKDF2-SHA256 of the shared mutation password. The plaintext never
# touches the app: clients send the password back via the
# `X-Vault-Key` header (see snippets/auth.py) and we only ever
# compare the derived bytes against this hash.
#
# Why ``X-Vault-Key`` and not ``X-Vault-Password``? Firefox's
# built-in password heuristic flags any request header whose name
# contains the substring ``password`` and offers to save its value
# after a successful 2xx. The new header has no ``password`` token,
# so the browser's "Save password?" prompt never fires. Transport
# security is unchanged (HTTPS still applies in production).
#
# To mint a new hash from a fresh password locally, run:
#   python -c "import hashlib,base64; \
#     print(base64.b64encode(hashlib.pbkdf2_hmac('sha256', \
#       b'YOUR_NEW_PASSWORD', b'vault-salt-v1', 200000)).decode())"
# then paste the printed base64 into VAULT_PASSWORD_HASH below
# (in .env / docker-compose).
VAULT_PASSWORD_HASH = os.environ.get("VAULT_PASSWORD_HASH", "")
VAULT_PASSWORD_SALT = b"vault-salt-v1"
VAULT_PASSWORD_ITERATIONS = 200_000


def check_vault_password(provided: str) -> bool:
    """Constant-time check of `provided` against the configured hash.

    Returns False (never raises) for every malformed input: an empty
    hash, a malformed hash, a non-UTF-8 password, or any other
    decoding error. Callers should treat False as "wrong password".

    Reads through the lazy Django settings proxy
    (``_django_settings.VAULT_PASSWORD_HASH``) so monkeypatching the
    settings proxy in tests actually affects the comparison.
    """
    configured_hash = _django_settings.VAULT_PASSWORD_HASH
    if not provided or not configured_hash:
        return False
    try:
        expected = base64.b64decode(configured_hash, validate=True)
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            provided.encode("utf-8"),
            VAULT_PASSWORD_SALT,
            VAULT_PASSWORD_ITERATIONS,
        )
        return hmac.compare_digest(expected, candidate)
    except (ValueError, TypeError, UnicodeEncodeError):
        # Malformed stored hash or non-string provided — refuse
        # gracefully rather than 500.
        return False


BASE_DIR = Path(__file__).resolve().parent.parent

# --- Core ----------------------------------------------------------------- #
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "insecure-dev-key-change-me")
DEBUG = _env_bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    # ``django.contrib.admin`` is INTENTIONALLY OMITTED: the vault
    # uses its own admin UI (``frontend/temp-admin.html``) backed by
    # the ``snippets`` REST API. Django's stock admin would force
    # ``SessionMiddleware`` and ``AuthenticationMiddleware`` into
    # the MIDDLEWARE stack — both of which emit a ``Set-Cookie:
    # sessionid=…`` header on every API response, which trips
    # Firefox's LoginManager "credential POST → success → new
    # session cookie" save-prompt heuristic. The vault's auth model
    # is stateless and header-based (see ``snippets/auth.py``), so
    # we have no need for Django's admin and no need for the
    # session/back-user machinery that admin requires.
    #
    # ``django.contrib.messages`` is also INTENTIONALLY OMITTED for
    # the same reason: it depends on ``SessionMiddleware`` (which
    # we've removed) for its backend storage, and ``MessageMiddleware``
    # raises ``ImproperlyConfigured`` at request time if the messages
    # framework is loaded but session middleware is absent. We do
    # not queue any flash messages from the API path; user-facing
    # feedback lives in the front-end's own status banner.
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    # Third-party
    "rest_framework",
    # Local
    "snippets.apps.SnippetsConfig",
]

# --- Middleware ----------------------------------------------------------- #
# Intentionally MINIMAL — the vault uses a stateless, header-based auth
# model (see ``snippets/auth.py``) and the front-end is a single-page
# static HTML bundle, so we strip out every Django middleware that would
# either set a ``Set-Cookie`` header on the API response or wire up
# session-based authentication.
#
# Specifically removed:
#   - ``SessionMiddleware`` — every response would otherwise carry
#     ``Set-Cookie: sessionid=…`` (and the request would advertise
#     ``Cookie: sessionid=…`` on subsequent calls). Firefox's
#     LoginManager uses a "credential POST → success → new session
#     cookie" heuristic to decide whether to show its "Save password?"
#     prompt. Even though our auth is header-based, the *presence* of
#     the sessionid cookie in the response is enough to fire the
#     heuristic. With SessionMiddleware gone, the response carries no
#     sessionid cookie and the heuristic has no signal.
#   - ``AuthenticationMiddleware`` — wires ``request.user`` to a Django
#     ``User`` model. Our API views use the stub ``VaultUser`` from
#     DRF's auth class directly, never ``request.user.username``, so
#     this middleware is dead weight and would also set a session
#     cookie on first login.
#   - ``CsrfViewMiddleware`` — CSRF protection exists to defend
#     cookie-based auth from cross-origin form submissions. Our auth
#     requires the ``X-Vault-Key`` header on every mutating call; that
#     header is a non-simple CORS request, which a cross-origin
#     attacker page cannot set without an explicit CORS preflight
#     allow-list (which we never grant), so the vault password acts
#     as the CSRF equivalent. CSRF middleware here is redundant AND
#     it would set ``Set-Cookie: csrftoken=…`` on the first GET,
#     again tripping Firefox's credential-save heuristic.
#
# Kept:
#   - ``SecurityMiddleware`` — applies HTTPS / HSTS / referrer headers.
#     Does not set cookies.
#   - ``CommonMiddleware`` — drives ETags, ``APPEND_SLASH``, and the
#     user-agent-based quirks. Does not set cookies.
#   - ``MessageMiddleware`` — only sets cookies when a Django message
#     is queued (we never queue any from the API path), and even then
#     it relies on SessionMiddleware which we have removed, so in
#     practice this middleware becomes a no-op.
#   - ``XFrameOptionsMiddleware`` — emits ``X-Frame-Options: DENY``.
#     Does not set cookies.
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "vault.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # The single-page frontend (index.html) is rendered via
        # TemplateView so Django can serve it from the root URL.
        # Pointing DIRS at /app/frontend lets Django locate the file
        # without forcing a templates/ directory or a static redirect.
        "DIRS": [BASE_DIR / "frontend"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                # ``django.contrib.auth.context_processors.auth`` and
                # ``django.contrib.messages.context_processors.messages``
                # are intentionally OMITTED because the corresponding
                # apps are not in INSTALLED_APPS — including them here
                # would raise ImproperlyConfigured on the first template
                # render.
            ],
        },
    },
]

WSGI_APPLICATION = "vault.wsgi.application"
ASGI_APPLICATION = "vault.asgi.application"

# --- Database ------------------------------------------------------------- #
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "vault"),
        "USER": os.environ.get("POSTGRES_USER", "vault"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "db"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}

# --- Auth / i18n ---------------------------------------------------------- #
AUTH_PASSWORD_VALIDATORS: list[dict] = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Static / frontend --------------------------------------------------- #
STATIC_URL = "/static/"
STATICFILES_DIRS = [
    BASE_DIR / "frontend",
]
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- DRF ------------------------------------------------------------------ #
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.JSONParser",
    ],
    # The header-based password gate. Safe methods (GET/HEAD/OPTIONS)
    # are exempt inside the auth class itself; mutating handlers that
    # opt in via permission_classes=[IsAuthenticated] are gated.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "snippets.auth.VaultPasswordAuthentication",
    ],
}