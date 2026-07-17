"""
Tests for the snippets API.

Two scenarios are covered per the spec:
  1. A valid POST creates a Snippet row in the database.
  2. The `?q=` filter matches title OR tags case-insensitively.
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from django.conf import settings
from rest_framework import status
from rest_framework.test import APIClient

from .models import Snippet

# Password used by every auth-gate test. Kept in one place so the
# matching PBKDF2 hash in `vault_hash` stays in sync.
TEST_PASSWORD = "test-vault-password"
TEST_PASSWORD_WRONG = "definitely-not-the-password"


def _hash_vault_password(plain: str) -> str:
    """Mirror of ``vault.settings.check_vault_password``'s hashing step."""
    return base64.b64encode(
        hashlib.pbkdf2_hmac(
            "sha256",
            plain.encode("utf-8"),
            settings.VAULT_PASSWORD_SALT,
            settings.VAULT_PASSWORD_ITERATIONS,
        )
    ).decode("ascii")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> APIClient:
    """APIClient for the legacy test suite.

    The legacy tests predate the password gate. We monkeypatch
    ``settings.VAULT_PASSWORD_HASH`` to ``""`` here so the gate's
    bypass branch fires and the tests can keep asserting on status
    codes without ever sending an ``X-Vault-Password`` header.
    Auth-gate tests use ``gated_client`` instead, which installs a
    real hash and exercises the real code path.
    """
    monkeypatch.setattr(settings, "VAULT_PASSWORD_HASH", "")
    return APIClient()


@pytest.fixture
def gated_client(monkeypatch: pytest.MonkeyPatch) -> APIClient:
    """APIClient wired with a real password hash so the auth class is engaged.

    Auth-gate tests install a hash of ``TEST_PASSWORD`` and pass the matching
    header via ``vault_headers`` (or deliberately omit it / send the wrong
    value to assert 401).
    """
    monkeypatch.setattr(
        settings, "VAULT_PASSWORD_HASH", _hash_vault_password(TEST_PASSWORD)
    )
    return APIClient()


@pytest.fixture
def vault_headers() -> dict[str, str]:
    """Header kwargs accepted by APIClient methods (HTTP_X_VAULT_PASSWORD=...)."""
    return {"HTTP_X_VAULT_PASSWORD": TEST_PASSWORD}


@pytest.fixture
def wrong_vault_headers() -> dict[str, str]:
    return {"HTTP_X_VAULT_PASSWORD": TEST_PASSWORD_WRONG}


@pytest.mark.django_db
def test_post_creates_snippet(client: APIClient) -> None:
    payload = {
        "title": "Force Delete K8s Namespace",
        "code_body": "kubectl delete ns foo --force --grace-period=0",
        "language": "bash",
        "tags": "k8s, kubernetes, cleanup",
    }

    assert Snippet.objects.count() == 0

    response = client.post("/api/snippets/", data=payload, format="json")

    assert response.status_code == status.HTTP_201_CREATED
    assert Snippet.objects.count() == 1

    saved = Snippet.objects.first()
    assert saved is not None
    assert saved.title == payload["title"]
    assert saved.code_body == payload["code_body"]
    assert saved.language == payload["language"]
    assert saved.tags == payload["tags"]

    # The response body must echo the saved object.
    assert response.data["id"] == saved.id
    assert response.data["title"] == payload["title"]


@pytest.mark.django_db
def test_search_filters_by_title_or_tags(client: APIClient) -> None:
    Snippet.objects.create(
        title="Docker Clear",
        code_body="docker system prune -af",
        language="bash",
        tags="docker, clean",
    )

    # Title hit (substring "dock" is in "Docker Clear").
    response = client.get("/api/snippets/?q=dock")
    assert response.status_code == status.HTTP_200_OK
    assert len(response.data) == 1
    assert response.data[0]["title"] == "Docker Clear"

    # Tag hit (substring "clean" matches the tags column).
    response = client.get("/api/snippets/?q=clean")
    assert response.status_code == status.HTTP_200_OK
    assert len(response.data) == 1
    assert response.data[0]["title"] == "Docker Clear"

    # No match returns an empty list.
    response = client.get("/api/snippets/?q=nomatch-xyz")
    assert response.status_code == status.HTTP_200_OK
    assert response.data == []

    # Case-insensitivity: uppercase query still matches.
    response = client.get("/api/snippets/?q=DOCKER")
    assert response.status_code == status.HTTP_200_OK
    assert len(response.data) == 1


@pytest.mark.django_db
def test_batch_delete_removes_only_named_snippets(client: APIClient) -> None:
    """POST /api/snippets/batch-delete/ removes the listed snippets in one shot."""
    keep = Snippet.objects.create(
        title="Keep Me",
        code_body="echo keep",
        language="bash",
        tags="misc",
    )
    gone_a = Snippet.objects.create(
        title="Bye A",
        code_body="echo a",
        language="bash",
        tags="misc",
    )
    gone_b = Snippet.objects.create(
        title="Bye B",
        code_body="echo b",
        language="python",
        tags="misc",
    )

    assert Snippet.objects.count() == 3

    response = client.post(
        "/api/snippets/batch-delete/",
        data={"ids": [gone_a.id, gone_b.id]},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["deleted"] == 2
    assert Snippet.objects.count() == 1
    assert Snippet.objects.filter(id=keep.id).exists()
    assert not Snippet.objects.filter(id=gone_a.id).exists()
    assert not Snippet.objects.filter(id=gone_b.id).exists()


@pytest.mark.django_db
def test_batch_delete_via_delete_method(client: APIClient) -> None:
    """DELETE /api/snippets/batch-delete/ is the REST-friendly alias."""
    s = Snippet.objects.create(
        title="Bye",
        code_body="echo",
        language="bash",
        tags="misc",
    )
    response = client.delete(
        "/api/snippets/batch-delete/",
        data={"ids": [s.id]},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["deleted"] == 1
    assert not Snippet.objects.filter(id=s.id).exists()


@pytest.mark.django_db
def test_batch_delete_rejects_non_list_payload(client: APIClient) -> None:
    """A non-array `ids` field should fail validation, not 500."""
    Snippet.objects.create(
        title="Untouched",
        code_body="echo",
        language="bash",
        tags="misc",
    )
    response = client.post(
        "/api/snippets/batch-delete/",
        data={"ids": "not-a-list"},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert Snippet.objects.count() == 1  # nothing was deleted


@pytest.mark.django_db
def test_batch_delete_empty_ids_is_noop(client: APIClient) -> None:
    """Empty list == no-op that returns 200 with deleted=0."""
    s = Snippet.objects.create(
        title="Still Here",
        code_body="echo",
        language="bash",
        tags="misc",
    )
    response = client.post(
        "/api/snippets/batch-delete/",
        data={"ids": []},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["deleted"] == 0
    assert Snippet.objects.filter(id=s.id).exists()


# ---------------------------------------------------------------------
# /api/snippets/<id>/  -> GET / PUT / PATCH on a single snippet
# ---------------------------------------------------------------------


@pytest.mark.django_db
def test_get_single_snippet_returns_row(client: APIClient) -> None:
    s = Snippet.objects.create(
        title="Single",
        code_body="echo one",
        language="bash",
        tags="misc, alpha",
    )
    response = client.get(f"/api/snippets/{s.id}/")
    assert response.status_code == status.HTTP_200_OK
    assert response.data["id"] == s.id
    assert response.data["title"] == "Single"
    assert response.data["code_body"] == "echo one"
    assert response.data["language"] == "bash"
    assert response.data["tags"] == "misc, alpha"


@pytest.mark.django_db
def test_get_unknown_snippet_returns_404(client: APIClient) -> None:
    response = client.get("/api/snippets/99999/")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_put_replaces_snippet_in_full(client: APIClient) -> None:
    s = Snippet.objects.create(
        title="Old Title",
        code_body="old code",
        language="bash",
        tags="old",
    )
    response = client.put(
        f"/api/snippets/{s.id}/",
        data={
            "title": "New Title",
            "code_body": "new code",
            "language": "kubernetes",
            "tags": "k8s, fresh",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    s.refresh_from_db()
    assert s.title == "New Title"
    assert s.code_body == "new code"
    assert s.language == "kubernetes"
    # Backend normalizes tags to lowercase + dedupe + joined with ", ".
    assert s.tags == "k8s, fresh"


@pytest.mark.django_db
def test_patch_partial_update_only_touches_supplied_fields(
    client: APIClient,
) -> None:
    s = Snippet.objects.create(
        title="Keep Title",
        code_body="keep code",
        language="bash",
        tags="keep, me",
    )
    response = client.patch(
        f"/api/snippets/{s.id}/",
        data={"title": "Patched Title Only"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    s.refresh_from_db()
    assert s.title == "Patched Title Only"
    # Untouched fields must remain.
    assert s.code_body == "keep code"
    assert s.language == "bash"
    assert s.tags == "keep, me"


@pytest.mark.django_db
def test_put_unknown_snippet_returns_404(client: APIClient) -> None:
    response = client.put(
        "/api/snippets/424242/",
        data={
            "title": "x",
            "code_body": "y",
            "language": "bash",
            "tags": "",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


# ---------------------------------------------------------------------
# Bulk Tool/Stack operations
# ---------------------------------------------------------------------


@pytest.mark.django_db
def test_bulk_rename_tool_updates_all_matching_rows(client: APIClient) -> None:
    """POST /api/snippets/bulk-rename-tool/ renames language everywhere."""
    a = Snippet.objects.create(
        title="A", code_body="echo a", language="bash", tags="misc"
    )
    b = Snippet.objects.create(
        title="B", code_body="echo b", language="bash", tags="misc"
    )
    c = Snippet.objects.create(
        title="C", code_body="echo c", language="python", tags="misc"
    )

    response = client.post(
        "/api/snippets/bulk-rename-tool/",
        data={"old": "bash", "new": "shell"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated"] == 2
    assert response.data["old"] == "bash"
    assert response.data["new"] == "shell"

    a.refresh_from_db()
    b.refresh_from_db()
    c.refresh_from_db()
    assert a.language == "shell"
    assert b.language == "shell"
    assert c.language == "python"  # untouched


@pytest.mark.django_db
def test_bulk_rename_tool_normalizes_case(client: APIClient) -> None:
    """Both `old` and `new` are normalized to lowercase before matching."""
    s = Snippet.objects.create(
        title="Mixed", code_body="x", language="bash", tags="t"
    )
    response = client.post(
        "/api/snippets/bulk-rename-tool/",
        data={"old": "BASH", "new": "Shell"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated"] == 1
    assert response.data["old"] == "bash"
    assert response.data["new"] == "shell"
    s.refresh_from_db()
    assert s.language == "shell"


@pytest.mark.django_db
def test_bulk_rename_tool_same_name_is_noop(client: APIClient) -> None:
    s = Snippet.objects.create(
        title="Same", code_body="x", language="bash", tags="t"
    )
    response = client.post(
        "/api/snippets/bulk-rename-tool/",
        data={"old": "bash", "new": "bash"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated"] == 0
    s.refresh_from_db()
    assert s.language == "bash"


@pytest.mark.django_db
def test_bulk_rename_tool_unknown_tool_returns_zero(client: APIClient) -> None:
    response = client.post(
        "/api/snippets/bulk-rename-tool/",
        data={"old": "ghost", "new": "phantom"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated"] == 0


@pytest.mark.django_db
def test_bulk_rename_tool_rejects_missing_old(client: APIClient) -> None:
    response = client.post(
        "/api/snippets/bulk-rename-tool/",
        data={"new": "shell"},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_bulk_rename_tool_rejects_missing_new(client: APIClient) -> None:
    response = client.post(
        "/api/snippets/bulk-rename-tool/",
        data={"old": "bash"},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_bulk_delete_tool_removes_all_with_that_language(
    client: APIClient,
) -> None:
    """POST /api/snippets/bulk-delete-tool/ removes every matching row."""
    keep = Snippet.objects.create(
        title="Keep", code_body="x", language="python", tags="t"
    )
    Snippet.objects.create(
        title="Bye 1", code_body="x", language="bash", tags="t"
    )
    Snippet.objects.create(
        title="Bye 2", code_body="x", language="bash", tags="t"
    )

    response = client.post(
        "/api/snippets/bulk-delete-tool/",
        data={"tool": "bash"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["deleted"] == 2
    assert response.data["tool"] == "bash"
    # Only the `python` row survives.
    assert Snippet.objects.count() == 1
    assert Snippet.objects.filter(id=keep.id).exists()


@pytest.mark.django_db
def test_bulk_delete_tool_normalizes_case(client: APIClient) -> None:
    Snippet.objects.create(
        title="Bye", code_body="x", language="bash", tags="t"
    )
    response = client.post(
        "/api/snippets/bulk-delete-tool/",
        data={"tool": "BASH"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["deleted"] == 1
    assert Snippet.objects.count() == 0


@pytest.mark.django_db
def test_bulk_delete_tool_unknown_is_noop(client: APIClient) -> None:
    s = Snippet.objects.create(
        title="Alive", code_body="x", language="python", tags="t"
    )
    response = client.post(
        "/api/snippets/bulk-delete-tool/",
        data={"tool": "ghost"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["deleted"] == 0
    assert Snippet.objects.filter(id=s.id).exists()


@pytest.mark.django_db
def test_bulk_delete_tool_rejects_missing_tool(client: APIClient) -> None:
    response = client.post(
        "/api/snippets/bulk-delete-tool/",
        data={},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


# ---------------------------------------------------------------------------
# Password-gate tests
#
# These tests exercise ``snippets.auth.VaultPasswordAuthentication`` end-to-end
# by monkey-patching ``settings.VAULT_PASSWORD_HASH`` to a hash of a known
# password, then asserting that mutating endpoints return 401 without the
# header and 2xx with the right one.
#
# GET endpoints must stay open (public read view).
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_create_without_password_returns_401(gated_client: APIClient) -> None:
    payload = {
        "title": "No Auth Header",
        "code_body": "echo hi",
        "language": "bash",
        "tags": "auth",
    }
    response = gated_client.post("/api/snippets/", data=payload, format="json")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert Snippet.objects.count() == 0


@pytest.mark.django_db
def test_create_with_wrong_password_returns_401(
    gated_client: APIClient,
    wrong_vault_headers: dict[str, str],
) -> None:
    payload = {
        "title": "Wrong Password",
        "code_body": "echo hi",
        "language": "bash",
        "tags": "auth",
    }
    response = gated_client.post(
        "/api/snippets/", data=payload, format="json", **wrong_vault_headers
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert Snippet.objects.count() == 0


@pytest.mark.django_db
def test_create_with_correct_password_returns_201(
    gated_client: APIClient,
    vault_headers: dict[str, str],
) -> None:
    payload = {
        "title": "Authorized Create",
        "code_body": "echo hi",
        "language": "bash",
        "tags": "auth",
    }
    response = gated_client.post(
        "/api/snippets/", data=payload, format="json", **vault_headers
    )
    assert response.status_code == status.HTTP_201_CREATED
    assert Snippet.objects.count() == 1


@pytest.mark.django_db
def test_put_with_correct_password_updates_snippet(
    gated_client: APIClient,
    vault_headers: dict[str, str],
) -> None:
    snippet = Snippet.objects.create(
        title="Old Title",
        code_body="echo old",
        language="bash",
        tags="auth",
    )
    response = gated_client.put(
        f"/api/snippets/{snippet.id}/",
        data={
            "title": "New Title",
            "code_body": "echo new",
            "language": "bash",
            "tags": "auth, updated",
        },
        format="json",
        **vault_headers,
    )
    assert response.status_code == status.HTTP_200_OK
    snippet.refresh_from_db()
    assert snippet.title == "New Title"
    assert snippet.code_body == "echo new"


@pytest.mark.django_db
def test_put_without_password_returns_401(gated_client: APIClient) -> None:
    snippet = Snippet.objects.create(
        title="Stays Put",
        code_body="x",
        language="bash",
        tags="",
    )
    response = gated_client.put(
        f"/api/snippets/{snippet.id}/",
        data={"title": "Hacked", "code_body": "y", "language": "bash", "tags": ""},
        format="json",
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    snippet.refresh_from_db()
    assert snippet.title == "Stays Put"


@pytest.mark.django_db
def test_delete_single_snippet_with_correct_password_returns_204(
    gated_client: APIClient,
    vault_headers: dict[str, str],
) -> None:
    snippet = Snippet.objects.create(
        title="To Be Deleted",
        code_body="x",
        language="bash",
        tags="",
    )
    response = gated_client.delete(
        f"/api/snippets/{snippet.id}/", **vault_headers
    )
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not Snippet.objects.filter(id=snippet.id).exists()


@pytest.mark.django_db
def test_delete_single_snippet_without_password_returns_401(
    gated_client: APIClient,
) -> None:
    snippet = Snippet.objects.create(
        title="Survives",
        code_body="x",
        language="bash",
        tags="",
    )
    response = gated_client.delete(f"/api/snippets/{snippet.id}/")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert Snippet.objects.filter(id=snippet.id).exists()


@pytest.mark.django_db
def test_batch_delete_with_correct_password_deletes(
    gated_client: APIClient,
    vault_headers: dict[str, str],
) -> None:
    a = Snippet.objects.create(title="A", code_body="a", language="bash", tags="")
    b = Snippet.objects.create(title="B", code_body="b", language="bash", tags="")
    response = gated_client.post(
        "/api/snippets/batch-delete/",
        data={"ids": [a.id, b.id]},
        format="json",
        **vault_headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert Snippet.objects.count() == 0


@pytest.mark.django_db
def test_batch_delete_without_password_returns_401(gated_client: APIClient) -> None:
    a = Snippet.objects.create(title="A", code_body="a", language="bash", tags="")
    response = gated_client.post(
        "/api/snippets/batch-delete/",
        data={"ids": [a.id]},
        format="json",
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert Snippet.objects.filter(id=a.id).exists()


@pytest.mark.django_db
def test_bulk_rename_with_correct_password_renames(
    gated_client: APIClient,
    vault_headers: dict[str, str],
) -> None:
    Snippet.objects.create(title="pod ps", code_body="x", language="docker", tags="")
    Snippet.objects.create(title="pod images", code_body="y", language="docker", tags="")
    response = gated_client.post(
        "/api/snippets/bulk-rename-tool/",
        data={"old": "docker", "new": "podman"},
        format="json",
        **vault_headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["updated"] == 2
    assert Snippet.objects.filter(language="podman").count() == 2


@pytest.mark.django_db
def test_bulk_rename_without_password_returns_401(gated_client: APIClient) -> None:
    Snippet.objects.create(title="stays", code_body="x", language="docker", tags="")
    response = gated_client.post(
        "/api/snippets/bulk-rename-tool/",
        data={"old": "docker", "new": "podman"},
        format="json",
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert Snippet.objects.filter(language="docker").exists()


@pytest.mark.django_db
def test_bulk_delete_tool_with_correct_password_deletes(
    gated_client: APIClient,
    vault_headers: dict[str, str],
) -> None:
    Snippet.objects.create(title="kubectl get", code_body="x", language="kubectl", tags="")
    Snippet.objects.create(title="kubectl apply", code_body="y", language="kubectl", tags="")
    Snippet.objects.create(title="survivor", code_body="z", language="python", tags="")
    response = gated_client.post(
        "/api/snippets/bulk-delete-tool/",
        data={"tool": "kubectl"},
        format="json",
        **vault_headers,
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["deleted"] == 2
    assert Snippet.objects.count() == 1


@pytest.mark.django_db
def test_bulk_delete_tool_without_password_returns_401(
    gated_client: APIClient,
) -> None:
    Snippet.objects.create(title="kubectl get", code_body="x", language="kubectl", tags="")
    response = gated_client.post(
        "/api/snippets/bulk-delete-tool/",
        data={"tool": "kubectl"},
        format="json",
    )
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert Snippet.objects.filter(language="kubectl").exists()


@pytest.mark.django_db
def test_get_list_does_not_require_password(gated_client: APIClient) -> None:
    Snippet.objects.create(title="Public View", code_body="x", language="bash", tags="")
    response = gated_client.get("/api/snippets/")
    assert response.status_code == status.HTTP_200_OK
    assert isinstance(response.data, list)
    assert any(item["title"] == "Public View" for item in response.data)


@pytest.mark.django_db
def test_get_detail_does_not_require_password(gated_client: APIClient) -> None:
    snippet = Snippet.objects.create(
        title="Public Detail", code_body="x", language="bash", tags=""
    )
    response = gated_client.get(f"/api/snippets/{snippet.id}/")
    assert response.status_code == status.HTTP_200_OK
    assert response.data["title"] == "Public Detail"


@pytest.mark.django_db
def test_bearer_fallback_header_works(
    gated_client: APIClient,
) -> None:
    payload = {
        "title": "Bearer Header",
        "code_body": "x",
        "language": "bash",
        "tags": "",
    }
    response = gated_client.post(
        "/api/snippets/",
        data=payload,
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {TEST_PASSWORD}",
    )
    assert response.status_code == status.HTTP_201_CREATED