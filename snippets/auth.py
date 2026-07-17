"""
Custom DRF authentication for the Vault's write-protection gate.

`VaultPasswordAuthentication` is a stateless, header-based authenticator:

- On **safe** methods (``GET``/``HEAD``/``OPTIONS``) it returns
  ``None`` immediately so the public read endpoints stay open without
  any client interaction.
- On **unsafe** methods (``POST``/``PUT``/``PATCH``/``DELETE``) it
  requires the shared vault password in the ``X-Vault-Password``
  header (with a ``Authorization: Bearer <pw>`` fallback) and rejects
  the request with ``401`` if the password is missing, malformed, or
  does not match the configured PBKDF2 hash.

The password never reaches the database; only its salted hash lives
in the container's environment (``VAULT_PASSWORD_HASH``). The
constant-time comparison in ``check_vault_password`` lives in
``vault/settings.py`` so this module has nothing to duplicate.
"""

from __future__ import annotations

from collections import namedtuple

from django.conf import settings
from rest_framework import authentication, exceptions

# Imported by symbol so we don't rely on attribute access through DRF's
# `WrappedAttributeError` proxy (which swallows AttributeError raised by
# `getattr(settings, ...)` in some DRF versions).
from vault.settings import check_vault_password


# A minimal stand-in for Django's auth.User that DRF will accept as
# `request.user`. We only need `.is_authenticated` to be truthy so the
# `IsAuthenticated` permission passes.
VaultUser = namedtuple("VaultUser", ["is_authenticated"])


# HTTP methods that are considered "safe" and therefore do not require
# the password. Anything outside this set is treated as a mutation.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class VaultPasswordAuthentication(authentication.BaseAuthentication):
    """Header-based password gate for unsafe HTTP methods."""

    # DRF renders this when a 401 is returned. Keeping it short so the
    # JSON body stays predictable for the front-end's error handler.
    www_authenticate_realm = "vault"

    def authenticate(self, request):
        # Step 1: safe methods are always allowed through. The public
        # listing page must work without any client-side interaction.
        if request.method in _SAFE_METHODS:
            return None

        # Step 2: dev convenience — if no hash is configured, the gate
        # is effectively disabled (no password can ever match nothing).
        # This keeps first-install / unit-test flows simple: an empty
        # ``VAULT_PASSWORD_HASH`` means "open". Production must always
        # set the hash; the bootstrap docs in README make that explicit.
        if not settings.VAULT_PASSWORD_HASH:
            return (VaultUser(is_authenticated=True), None)

        # Step 3: extract the candidate password from headers.
        provided = self._extract_password(request)
        if not provided:
            raise exceptions.AuthenticationFailed(
                "X-Vault-Password header is required for write operations."
            )

        # Step 4: constant-time comparison against the stored hash.
        if not check_vault_password(provided):
            raise exceptions.AuthenticationFailed("Invalid vault password.")

        # Success — return a stub user so DRF treats the request as
        # authenticated. `request.user` will be this namedtuple.
        return (VaultUser(is_authenticated=True), None)

    def authenticate_header(self, request):
        # Tells DRF which challenge to put in the WWW-Authenticate
        # header on a 401 response. Browsers / curl use this to know
        # the realm; we don't actually do HTTP digest, but DRF needs
        # a non-empty return to render 401 instead of 403.
        return f'Basic realm="{self.www_authenticate_realm}"'

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_password(request):
        """Read the password from ``X-Vault-Password`` or ``Authorization``.

        Accepts:
          - ``X-Vault-Password: <plaintext>``  (preferred)
          - ``Authorization: Bearer <plaintext>``  (REST-friendly fallback)

        Returns ``None`` if neither header carries a non-empty value.
        """
        pw = request.META.get("HTTP_X_VAULT_PASSWORD")
        if pw:
            return pw.strip() or None

        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            return token or None

        return None