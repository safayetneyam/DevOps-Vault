"""
Top-level URL configuration for the Developer Vault.

Routes:
    /                  -> serves the single-page frontend (index.html)
    /api/snippets/     -> DRF list/create endpoint (see snippets.urls)
    /create/           -> snippet create form (create.html)
    /temp-admin/       -> custom admin UI (temp-admin.html)
    /edit/<pk>/        -> snippet edit form (edit.html)
    /static/...        -> collected static assets

Note: ``/admin/`` (Django's stock admin) is intentionally NOT routed.
The vault uses its own admin UI in ``frontend/temp-admin.html`` and
the ``django.contrib.admin`` app is removed from ``INSTALLED_APPS`` —
see ``vault/settings.py`` for the rationale (chiefly: dropping the
session middleware that would otherwise attach a ``sessionid``
cookie to every API response, which trips Firefox's password-save
heuristic).
"""

from django.urls import include, path
from django.views.generic import TemplateView

# The single-page frontend lives in /app/frontend/index.html and is
# collected into STATIC_ROOT at deploy time. TemplateView is pointed
# at the *static* path so Django can locate it after collectstatic
# without needing a dedicated templates directory.
urlpatterns = [
    path("api/snippets/", include("snippets.urls")),
    path(
        "",
        TemplateView.as_view(template_name="index.html"),
        name="home",
    ),
    path(
        "create/",
        TemplateView.as_view(template_name="create.html"),
        name="create",
    ),
    path(
        "temp-admin/",
        TemplateView.as_view(template_name="temp-admin.html"),
        name="temp-admin",
    ),
    path(
        "edit/<int:pk>/",
        TemplateView.as_view(template_name="edit.html"),
        name="edit",
    ),
]
