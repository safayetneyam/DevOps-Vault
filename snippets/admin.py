from django.contrib import admin

from .models import Snippet


@admin.register(Snippet)
class SnippetAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "language", "created_at")
    search_fields = ("title", "tags")
    list_filter = ("language",)