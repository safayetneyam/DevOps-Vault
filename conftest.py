"""
Project-level pytest configuration.

Adds fixtures used by both the legacy snippet tests and the new
password-gate tests in ``snippets/tests.py``.

The fixtures below are intentionally explicit (opt-in) — we do NOT
autouse a header because the legacy tests still rely on the
``DEBUG=True + empty hash`` bypass branch of
:class:`snippets.auth.VaultPasswordAuthentication`. Tests that need
to exercise the gate request ``vault_headers`` directly.
"""

from __future__ import annotations

import pytest


# Password the auth tests will use. Matches the placeholder baked
# into .env.example / .env so a developer running the test suite
# locally can copy/paste the same value to interact with the API.
TEST_PASSWORD = "change-me-in-production"


@pytest.fixture
def vault_headers() -> dict[str, str]:
    """Return kwargs for ``APIClient.{get,post,...}(..., **vault_headers)``.

    The fixture shape matches how Django/DRF's test client accepts
    header overrides: ``HTTP_X_VAULT_KEY="…"``.

    The header is named ``X-Vault-Key`` (not ``X-Vault-Password``)
    because Firefox's built-in password heuristic scans any
    header whose name contains ``password`` and offers to save its
    value after a successful 2xx. The new name has no ``password``
    token so the browser never asks the user to save the vault
    password. See ``snippets/auth.py`` for the full rationale.
    """
    return {"HTTP_X_VAULT_KEY": TEST_PASSWORD}
