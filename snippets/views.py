"""
API views for the snippets app.

Implements:
    GET    /api/snippets/                  -> list all snippets
    GET    /api/snippets/?q=...            -> case-insensitive substring filter
                                              on title OR tags
    POST   /api/snippets/                  -> create a new snippet
    GET    /api/snippets/<id>/             -> retrieve a single snippet
    PUT    /api/snippets/<id>/             -> full update of a snippet
    PATCH  /api/snippets/<id>/             -> partial update of a snippet
    POST   /api/snippets/batch-delete/     -> delete many snippets in one shot
    DELETE /api/snippets/batch-delete/     -> alias of the POST endpoint
    POST   /api/snippets/bulk-rename-tool/ -> rename a Tool/Stack across all
                                              snippets that use it
    POST   /api/snippets/bulk-delete-tool/ -> bulk-delete every snippet that
                                              uses the given Tool/Stack
"""

from __future__ import annotations

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Snippet, Tool
from .serializers import SnippetSerializer


class SnippetListCreateView(APIView):
    """Combined list/create endpoint for the Snippet resource."""

    # The class-level default assumes a mutation (POST); the GET
    # handler overrides this via `get_permissions()` to keep the
    # listing endpoint open. Without this split, the public page
    # would require the password on every reload.
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        # Per-request: GET is public, everything else is gated.
        if self.request.method == "GET":
            return [AllowAny()]
        return [IsAuthenticated()]

    def get(self, request: Request) -> Response:
        query = request.query_params.get("q", "").strip()
        queryset = Snippet.objects.all()
        if query:
            queryset = queryset.filter(
                Q(title__icontains=query) | Q(tags__icontains=query)
            )
        serializer = SnippetSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request: Request) -> Response:
        serializer = SnippetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class SnippetDetailView(APIView):
    """Retrieve / update / delete a single Snippet by primary key."""

    def _get_object(self, pk: int) -> Snippet:
        # 404 with a helpful message if the row does not exist.
        return get_object_or_404(Snippet, pk=pk)

    def get_permissions(self):
        # Public read; gated write/delete.
        if self.request.method == "GET":
            return [AllowAny()]
        return [IsAuthenticated()]

    def get(self, request: Request, pk: int) -> Response:
        snippet = self._get_object(pk)
        serializer = SnippetSerializer(snippet)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request: Request, pk: int) -> Response:
        """Full update: every writable field must be supplied."""
        snippet = self._get_object(pk)
        serializer = SnippetSerializer(snippet, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request: Request, pk: int) -> Response:
        """Partial update: only the supplied fields are changed."""
        snippet = self._get_object(pk)
        serializer = SnippetSerializer(snippet, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def delete(self, request: Request, pk: int) -> Response:
        """Delete a single snippet by id."""
        snippet = self._get_object(pk)
        snippet.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["POST", "DELETE"])
@permission_classes([IsAuthenticated])
def snippet_batch_delete(request: Request) -> Response:
    """
    Batch-delete snippets.

    Accepts a JSON body of the form ``{"ids": [1, 2, 3]}`` and removes
    every matching row in a single ``DELETE ... WHERE id IN (...)`` SQL
    statement. Returns the number of rows actually deleted.

    Behavior:
        - Unknown IDs are silently ignored (no 404 per id).
        - Missing or malformed ``ids`` payload yields 400.
        - Empty ``ids`` list is a no-op and returns ``{"deleted": 0}``.
    """
    payload = request.data if isinstance(request.data, dict) else {}
    ids = payload.get("ids")

    if not isinstance(ids, list):
        return Response(
            {"detail": "`ids` must be a JSON array of snippet IDs."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Coerce each entry to int; drop anything non-numeric so a junk
    # payload can't trigger a DB error.
    clean_ids: list[int] = []
    for raw in ids:
        try:
            clean_ids.append(int(raw))
        except (TypeError, ValueError):
            continue

    if not clean_ids:
        return Response({"deleted": 0}, status=status.HTTP_200_OK)

    deleted, _ = Snippet.objects.filter(id__in=clean_ids).delete()
    return Response({"deleted": deleted}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------
# Bulk operations on a whole Tool/Stack
# ---------------------------------------------------------------------


def _normalize_tool(raw: object) -> str:
    """Match the model's save() normalization: stripped only.

    The Snippet model preserves user casing on `tool` (it only strips
    whitespace), so a tool name we receive from the wire must be
    normalized the same way before we query against the DB column.
    Lowercasing here would silently turn ``"Docker"`` into ``"docker"``
    on the wire even though the registry keeps them distinct.

    For registry-level lookups (uniqueness, bulk rename/delete),
    use :func:`_tool_key` instead — that's the lowercased key used by
    ``Tool.name_key`` and matches across casings.
    """
    if not isinstance(raw, str):
        return ""
    return raw.strip()


def _tool_key(raw: object) -> str:
    """Return the case-folded registry key for a tool name.

    Used for matching against ``Tool.name_key`` so that ``"Jenkins"``
    / ``"jenkins"`` / ``"jenKINs"`` all resolve to the same registry
    row. Display string still comes from ``Tool.name`` (the user's
    original casing).
    """
    if not isinstance(raw, str):
        return ""
    return raw.strip().lower()


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def bulk_rename_tool(request: Request) -> Response:
    """
    Rename a Tool/Stack across every snippet that uses it.

    Body: ``{"old": "bash", "new": "shell"}``

    Returns ``{"updated": N, "old": "<normalized>", "new": "<normalized>"}``.

    Matching the ``old`` tool is case-insensitive: ``"Jenkins"`` will
    hit snippets stored as ``"jenkins"`` and vice versa. The ``new``
    tool name is also compared case-insensitively against every
    registered Tool; a rename to an already-occupied key returns 409.
    """
    payload = request.data if isinstance(request.data, dict) else {}

    old_raw = _normalize_tool(payload.get("old"))
    new_raw = _normalize_tool(payload.get("new"))

    if not old_raw:
        return Response(
            {"detail": "`old` is required and must be a non-empty string."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not new_raw:
        return Response(
            {"detail": "`new` is required and must be a non-empty string."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    old_key = _tool_key(old_raw)
    new_key = _tool_key(new_raw)

    if old_key == new_key:
        # Nothing to do; return a 200 with zero so the client doesn't
        # need to special-case this.
        return Response(
            {"updated": 0, "old": old_raw, "new": new_raw},
            status=status.HTTP_200_OK,
        )

    # Block the rename if the target key already exists in the
    # registry under a different display name. Compare via the
    # lowercased ``name_key`` so "rename to JENKINS" with an existing
    # "jenkins" row returns 409 rather than silently creating a
    # duplicate.
    if Tool.objects.filter(name_key=new_key).exists():
        existing = Tool.objects.get(name_key=new_key)
        return Response(
            {
                "detail": "A tool with that name already exists.",
                "name": existing.name,
            },
            status=status.HTTP_409_CONFLICT,
        )

    # Match snippets by lowercased key so a "rename JENKINS -> shell"
    # call still catches snippets stored under "jenkins" / "Jenkins".
    qs = Snippet.objects.filter(tool__iexact=old_key)
    matched = list(qs)
    for snippet in matched:
        # Use __setattr__ + save() so the same normalization pipeline
        # used elsewhere (stripped tool, dedupe tags) still runs.
        snippet.tool = new_raw
        snippet.save()

    # Also update the Tool registry row's display name so the sidebar
    # reflects the rename on the next ``list_tools`` fetch. Without
    # this the registry would keep showing the OLD name even though
    # every snippet underneath it has the NEW name, leaving the
    # admin sidebar in a half-renamed state. Going through attribute
    # assignment + ``save()`` ensures ``__setattr__`` keeps
    # ``name_key`` in sync with the new ``name`` automatically.
    registry = Tool.objects.filter(name_key=old_key).first()
    if registry is not None and registry.name != new_raw:
        registry.name = new_raw
        registry.save()

    return Response(
        {"updated": len(matched), "old": old_raw, "new": new_raw},
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def bulk_delete_tool(request: Request) -> Response:
    """
    Bulk-delete every snippet that uses the given Tool/Stack.

    Body: ``{"tool": "bash"}``

    Returns ``{"deleted": N, "tool": "<normalized>"}``.
    """
    payload = request.data if isinstance(request.data, dict) else {}

    tool_raw = _normalize_tool(payload.get("tool"))
    if not tool_raw:
        return Response(
            {"detail": "`tool` is required and must be a non-empty string."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Case-insensitive match so "delete JENKINS" still nukes snippets
    # stored under "jenkins" / "Jenkins".
    deleted, _ = Snippet.objects.filter(tool__iexact=_tool_key(tool_raw)).delete()
    return Response(
        {"deleted": deleted, "tool": tool_raw},
        status=status.HTTP_200_OK,
    )


# ---------------------------------------------------------------------
# Create a brand-new Tool/Stack
# ---------------------------------------------------------------------


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_tool(request: Request) -> Response:
    """
    Register a brand-new Tools/Stack name in the vault.

    Body: ``{"name": "helm"}``

    Behavior:
        - Empty / missing / non-string ``name`` -> 400.
        - Strips the supplied name but **keeps its casing** on display
          (``"Helm"`` round-trips as ``"Helm"``). Uniqueness is
          enforced case-insensitively: a registry already containing
          ``"Jenkins"`` will reject ``"jenkins"``, ``"JENKINS"`` etc.
          via the lowercased ``Tool.name_key`` column. The frontend
          does the same check BEFORE prompting the vault password so
          the user never has to type it just to be told the name is
          taken.
        - If a Tool with that case-folded key already exists -> 409,
          with the existing display name echoed back so the client can
          show "A stack named \\"Jenkins\\" already exists".
        - On success returns ``{"name": "<display name>", "created": true}``
          with HTTP 201.

    The new Tool row is independent of any Snippet — saving a snippet
    under a previously-unused tool name still works without first
    registering it; the registry is purely a UI convenience so the
    Tools/Stack dropdown lists user-added names without waiting for
    someone to save a snippet.
    """
    payload = request.data if isinstance(request.data, dict) else {}

    name_raw = _normalize_tool(payload.get("name"))
    if not name_raw:
        return Response(
            {"detail": "`name` is required and must be a non-empty string."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Case-insensitive uniqueness: a Tool whose ``name_key`` matches
    # already owns this slot, regardless of display casing.
    existing = Tool.objects.filter(name_key=_tool_key(name_raw)).first()
    if existing is not None:
        return Response(
            {"detail": "A tool with this name already exists.",
             "name": existing.name},
            status=status.HTTP_409_CONFLICT,
        )

    tool = Tool(name=name_raw)
    tool.save()
    return Response(
        {"name": tool.name, "created": True},
        status=status.HTTP_201_CREATED,
    )


# ---------------------------------------------------------------------
# List registered Tools (used by the New/Edit snippet dropdowns AND
# by the admin sidebar)
# ---------------------------------------------------------------------


@api_view(["GET"])
@permission_classes([AllowAny])
def list_tools(request: Request) -> Response:
    """Return the registry of Tools/Stacks the admin has declared.

    Response is ``{"tools": ["bash", "docker", ...]}`` — sorted
    alphabetically for deterministic dropdown ordering. Empty list
    if the admin has not added any tools (the New/Edit pages fall
    back to their built-in defaults in that case).

    This endpoint is intentionally public (no auth) because the Tools
    dropdown is rendered on the New/Edit pages, which never require a
    password for read-only operations.
    """
    names = list(
        Tool.objects.values_list("name", flat=True).order_by("name")
    )
    return Response({"tools": names}, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------
# Delete a registered Tool/Stack
# ---------------------------------------------------------------------


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def delete_tool(request: Request, name: str) -> Response:
    """Remove a Tools/Stack from the registry AND delete every snippet
    under it.

    URL: ``DELETE /api/snippets/tools/<name>/``

    Behavior:
        - Normalizes ``<name>`` the same way the bulk endpoints do.
        - If no Tool row exists with that name -> 404.
        - On success, removes every Snippet whose ``tool`` matches
          the normalized name and deletes the Tool row itself. Returns
          ``{"deleted_snippets": N, "name": "<normalized>"}``.

    After this call, the tool name disappears from every dropdown
    (Create / Edit / Admin sidebar) because those pages all read from
    the same registry table.
    """
    name_norm = _normalize_tool(name)
    if not name_norm:
        return Response(
            {"detail": "`name` is required and must be a non-empty string."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Match by lowercased key so "DELETE /tools/JENKINS/" removes the
    # row stored under "jenkins" / "Jenkins".
    target = Tool.objects.filter(name_key=_tool_key(name_norm)).first()
    if target is None:
        return Response(
            {"detail": "No tool with that name exists.",
             "name": name_norm},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Cascade: drop every snippet that uses this tool, then drop
    # the registry row. We do snippets first so the unique-name index
    # doesn't briefly see the row without its dependents.
    deleted_snippets, _ = (
        Snippet.objects.filter(tool__iexact=target.name_key).delete()
    )
    Tool.objects.filter(pk=target.pk).delete()

    return Response(
        {"deleted_snippets": deleted_snippets, "name": target.name},
        status=status.HTTP_200_OK,
    )