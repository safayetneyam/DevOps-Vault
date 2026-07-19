"""
Custom DRF authentication for the Vault's write-protection gate.

`VaultPasswordAuthentication` is a stateless, header-based authenticator:

- On **safe** methods (``GET``/``HEAD``/``OPTIONS``) it returns
  ``None`` immediately so the public read endpoints stay open without
  any client interaction.
- On **unsafe** methods (``POST``/``PUT``/``PATCH``/``DELETE``) it
  requires the shared vault password in the ``X-Vault-Key`` header
  (with a ``Authorization: Bearer <pw>`` fallback) and rejects the
  request when the password is missing, malformed, or does not match
  the configured PBKDF2 hash.

The header name is deliberately **not** named ``X-Vault-Password``
or anything else containing the substring ``password``. Firefox
(and to a lesser extent Chrome / Edge) treat any header whose name
contains ``password`` as a credential-bearing header and offer to
save its value after a successful response. Renaming the header to
``X-Vault-Key`` sidesteps that heuristic so the browser never asks
the user to save the vault password.

The password never reaches the database; only its salted hash lives
in the container's environment (``VAULT_PASSWORD_HASH``). The
constant-time comparison in ``check_vault_password`` lives in
``vault/settings.py`` so this module has nothing to duplicate.

Note on status codes: bad / missing passwords return **403 Forbidden**
(not 401 Unauthorized), and ``authenticate_header`` returns ``None``.
A 401 here would cause DRF to emit ``WWW-Authenticate: Basic
realm="vault"``, which the browser interprets as an HTTP Basic auth
challenge and shows a native username/password dialog. We are NOT
implementing HTTP Basic auth (the vault uses a custom
``X-Vault-Key`` header), so that native prompt is wrong and must
not fire. A 403 with no challenge header lets the front-end
handle the rejection via JavaScript and show its own inline
re-prompt instead.
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

    # The DRF default ``NotAuthenticated`` exception carries a
    # ``WWW-Authenticate`` header when ``authenticate_header()`` returns
    # a non-empty string. By keeping this attribute removed and never
    # using ``AuthenticationFailed`` we ensure the response carries no
    # such header and therefore no browser-native auth dialog.

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
            raise exceptions.PermissionDenied(
                "X-Vault-Key header is required for write operations."
            )

        # Step 4: constant-time comparison against the stored hash.
        # We use ``PermissionDenied`` (HTTP 403) rather than
        # ``AuthenticationFailed`` (HTTP 401) for two reasons:
        #   1. 401 + ``WWW-Authenticate`` would make the browser pop a
        #      native username/password dialog (HTTP Basic auth prompt),
        #      which is wrong because the vault does NOT use HTTP Basic.
        #   2. 403 lets the front-end handle the rejection via JS and
        #      show its own inline re-prompt without the browser
        #      intercepting the response.
        #   3. The header is intentionally named ``X-Vault-Key`` (NOT
        #      ``X-Vault-Password``) so Firefox's "credential in a
        #      header named *password*" heuristic never fires and the
        #      browser does not offer to save the value.
        if not check_vault_password(provided):
            raise exceptions.PermissionDenied("Invalid vault password.")

        # Success — return a stub user so DRF treats the request as
        # authenticated. `request.user` will be this namedtuple.
        return (VaultUser(is_authenticated=True), None)

    def authenticate_header(self, request):
        # Return ``None`` so DRF does NOT emit a ``WWW-Authenticate``
        # header on auth-rejected responses. With this header absent,
        # the browser does not show its native username/password
        # dialog and our ``403`` JSON body reaches the front-end, which
        # then prompts the user inline for the vault password again.
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_password(request):
        """Read the password from ``X-Vault-Key`` or ``Authorization``.

        Accepts:
          - ``X-Vault-Key: <plaintext>``  (preferred)
          - ``Authorization: Bearer <plaintext>``  (REST-friendly fallback)

        Returns ``None`` if neither header carries a non-empty value.

        Header name choice: ``X-Vault-Key`` is intentional. Firefox
        (and to a lesser extent Chrome / Edge) scan outgoing
        requests for any header whose **name** contains the substring
        ``password`` and offer to save its value after a successful
        2xx response — regardless of whether the page contains an
        ``<input type="password">``. Renaming the header to
        ``X-Vault-Key`` — which contains no ``password`` token —
        keeps the browser's credential-save prompt silent. Transport
        security is unchanged (HTTPS still applies in production),
        and the front-end still ships its own custom modal so the
        user never sees a password field that the password manager
        could track.
        """
        pw = request.META.get("HTTP_X_VAULT_KEY")
        if pw:
            return pw.strip() or None

        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
            return token or None

        return None