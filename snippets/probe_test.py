import pytest
from django.conf import settings
from rest_framework.test import APIClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "VAULT_PASSWORD_HASH", "")
    return APIClient()


@pytest.mark.django_db
def test_probe(client):
    print("\n=== PROBE ===")
    print("settings.VAULT_PASSWORD_HASH =", repr(settings.VAULT_PASSWORD_HASH))
    print("settings.DEBUG =", settings.DEBUG)
    print("os env =", __import__("os").environ.get("VAULT_PASSWORD_HASH"))
    from snippets.auth import VaultPasswordAuthentication
    inst = VaultPasswordAuthentication()
    print("bypass branch should fire? ", not settings.VAULT_PASSWORD_HASH and settings.DEBUG)
