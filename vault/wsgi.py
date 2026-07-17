"""WSGI config for the Developer Vault project."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vault.settings")

application = get_wsgi_application()