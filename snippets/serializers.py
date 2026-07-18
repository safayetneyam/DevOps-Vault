"""DRF serializers for the snippets app."""

from rest_framework import serializers

from .models import Snippet


class SnippetSerializer(serializers.ModelSerializer):
    """Serializes every field of the Snippet model."""

    class Meta:
        model = Snippet
        fields = [
            "id",
            "title",
            "code_body",
            "tool",
            "tags",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]