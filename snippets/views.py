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
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Snippet
from .serializers import SnippetSerializer


class SnippetListCreateView(APIView):
    """Combined list/create endpoint for the Snippet resource."""

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
    """Retrieve / update a single Snippet by primary key."""

    def _get_object(self, pk: int) -> Snippet:
        # 404 with a helpful message if the row does not exist.
        return get_object_or_404(Snippet, pk=pk)

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


@api_view(["POST", "DELETE"])
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
    """Match the model's save() normalization: lowercase + stripped.

    The Snippet model lowercases `language` on every save, so a tool
    name we receive from the wire must be normalized the same way
    before we query against the DB column.
    """
    if not isinstance(raw, str):
        return ""
    return raw.strip().lower()


@api_view(["POST"])
def bulk_rename_tool(request: Request) -> Response:
    """
    Rename a Tool/Stack across every snippet that uses it.

    Body: ``{"old": "bash", "new": "shell"}``

    Returns ``{"updated": N, "old": "<normalized>", "new": "<normalized>"}``.

    The rename runs through ``save()`` per row so the model can
    re-normalize `language` (and tags) consistently with everywhere else.
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
    if old_raw == new_raw:
        # Nothing to do; return a 200 with zero so the client doesn't
        # need to special-case this.
        return Response(
            {"updated": 0, "old": old_raw, "new": new_raw},
            status=status.HTTP_200_OK,
        )

    # Match on the normalized column directly. Because the model always
    # lowercases on save, we can use exact equality here.
    qs = Snippet.objects.filter(language=old_raw)
    matched = list(qs)
    for snippet in matched:
        # Use __setattr__ + save() so the same normalization pipeline
        # used elsewhere (lowercase language, dedupe tags) still runs.
        snippet.language = new_raw
        snippet.save()

    return Response(
        {"updated": len(matched), "old": old_raw, "new": new_raw},
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
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

    deleted, _ = Snippet.objects.filter(language=tool_raw).delete()
    return Response(
        {"deleted": deleted, "tool": tool_raw},
        status=status.HTTP_200_OK,
    )