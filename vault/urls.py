"""
Top-level URL configuration for the Developer Vault.

Routes:
    /                 -> serves the single-page frontend (index.html)
    /api/snippets/    -> DRF list/create endpoint (see snippets.urls)
    /admin/           -> Django admin (optional)
    /static/...       -> collected static assets (frontend, admin)
"""

from django.contrib import admin
from django.urls import include, path
from django.views.generic import TemplateView

# The single-page frontend lives in /app/frontend/index.html and is
# collected into STATIC_ROOT at deploy time. TemplateView is pointed
# at the *static* path so Django can locate it after collectstatic
# without needing a dedicated templates directory.
urlpatterns = [
    path("admin/", admin.site.urls),
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
