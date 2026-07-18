from django.contrib import admin

from .models import Snippet, Tool


@admin.register(Snippet)
class SnippetAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "tool", "created_at")
    search_fields = ("title", "tags")
    list_filter = ("tool",)


@admin.register(Tool)
class ToolAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "created_at")
    search_fields = ("name",)