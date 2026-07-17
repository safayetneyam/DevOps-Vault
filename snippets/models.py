"""
Database models for the snippets app.

The `Snippet` model represents a stored terminal command, configuration
block, or code fragment. All persistence lives here; the Django app tier
remains stateless.

Normalization policy
--------------------
`language` (now semantically "Tools/Stack") and each token inside `tags`
are always stored as **lowercase, stripped** strings. This is enforced
on every `save()` and on attribute assignment, so callers can hand in
mixed-case input (`"Bash"`, `"  bAsh "`, `"k8s, K8S, cleanup"`) and get
canonical data out. The wire format (DRF) reflects this normalization
back to the client transparently.
"""

from django.db import models


class Snippet(models.Model):
    """A single command/snippet entry in the vault."""

    title = models.CharField(max_length=255)
    code_body = models.TextField()
    language = models.CharField(max_length=50, default="bash")
    # Stored as a comma-separated string per spec; the API keeps that
    # contract on the wire. A future migration could split this into a
    # dedicated Tag model without changing the public schema if desired.
    tags = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["title"]),
            models.Index(fields=["tags"]),
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.title

    # ------------------------------------------------------------------
    # Normalization helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _norm_scalar(value):
        """Lowercase + strip a single scalar string. None becomes ''."""
        if value is None:
            return ""
        return str(value).strip().lower()

    @classmethod
    def _norm_tags(cls, value):
        """Normalize a tag string: lowercase each token, strip whitespace,
        drop blanks, then re-join with ', '. Idempotent."""
        if value is None:
            return ""
        tokens = [cls._norm_scalar(t) for t in str(value).split(",")]
        seen = []
        for tok in tokens:
            if tok and tok not in seen:
                seen.append(tok)
        return ", ".join(seen)

    def _apply_normalization(self):
        """In-place canonicalization of `language` and `tags`."""
        # `language` may be defaulted by the DB to "bash" before this
        # runs on a fresh object; .strip().lower() is a no-op there.
        self.language = self._norm_scalar(self.language) or "bash"
        self.tags = self._norm_tags(self.tags)

    def save(self, *args, **kwargs):
        """Persist with normalized `language` and `tags`."""
        self._apply_normalization()
        return super().save(*args, **kwargs)

    def __setattr__(self, name, value):
        """Normalize on assignment too, so in-memory mutations stay clean."""
        if name == "language":
            value = self._norm_scalar(value) or "bash"
        elif name == "tags":
            value = self._norm_tags(value)
        super().__setattr__(name, value)