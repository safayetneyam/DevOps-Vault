"""URL routes for the snippets app."""

from django.urls import path

from .views import (
    SnippetDetailView,
    SnippetListCreateView,
    bulk_delete_tool,
    bulk_rename_tool,
    create_tool,
    delete_tool,
    list_tools,
    snippet_batch_delete,
)

app_name = "snippets"

urlpatterns = [
    path("", SnippetListCreateView.as_view(), name="list-create"),
    # /api/snippets/<id>/ -> GET / PUT / PATCH on a single snippet.
    path("<int:pk>/", SnippetDetailView.as_view(), name="detail"),
    path("batch-delete/", snippet_batch_delete, name="batch-delete"),
    # Bulk operations on a whole Tool/Stack.
    path("bulk-rename-tool/", bulk_rename_tool, name="bulk-rename-tool"),
    path("bulk-delete-tool/", bulk_delete_tool, name="bulk-delete-tool"),
    # Tool/Stack registry: list + create + delete.
    path("tools/", list_tools, name="list-tools"),
    path("tools/create/", create_tool, name="create-tool"),
    path("tools/<str:name>/", delete_tool, name="delete-tool"),
]