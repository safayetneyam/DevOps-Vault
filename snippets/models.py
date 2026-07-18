"""
Database models for the snippets app.

The `Snippet` model represents a stored terminal command, configuration
block, or code fragment. All persistence lives here; the Django app tier
remains stateless.

`Tool` is the registry of Tools/Stacks that exist in the vault. A Tool
row exists independently of any Snippet — admins can pre-create a
Tools/Stack name (so it appears in the New-Snippet dropdown before
anyone has saved a snippet under it), and Edit/Delete on the sidebar
still operate on snippets that reference the tool by `tool`.

Normalization policy
--------------------
`tags` tokens are always stored **lowercase, stripped, deduplicated** so
the same tag never appears twice in different cases. `tool` names keep
the user-supplied casing verbatim, but **Tool/Stack uniqueness is
case-insensitive**: a registry already containing ``"Jenkins"`` will
reject a second ``create_tool`` for ``"jenkins"``, ``"JENKINS"``,
``"jenKINs"`` etc. The user-supplied casing is preserved on the
display side (create dropdown, edit dropdown, admin sidebar) while the
DB-enforced uniqueness is keyed on a sibling ``name_key`` column that
holds ``name.strip().lower()``. Both rules are enforced on every
`save()` and on attribute assignment; the wire format (DRF) reflects
this back to the client transparently.
"""

from django.db import models


class Tool(models.Model):
    """A registered Tools/Stack name.

    Conceptually "the list of known Tools/Stacks the vault accepts".
    Rows here are independent of snippets — a brand-new admin-added Tool
    with zero snippets appears in the sidebar with ``count = 0`` and is
    selectable in the New-Snippet form. When the first Snippet is saved
    with that ``tool``, it simply joins the existing Tool row.

    Tool names preserve the user-supplied casing on the display side,
    but **uniqueness is case-insensitive**. ``"Jenkins"``, ``"jenkins"``,
    and ``"jenKINs"`` all collide on the same registry row. The DB-level
    guarantee is provided by a separate ``name_key`` column (the
    lowercased name) carrying a real unique index — a regular
    ``CharField`` unique index is collation-dependent and would not be
    portable across Postgres / SQLite / MySQL.
    """

    name = models.CharField(max_length=50)
    # Case-folded copy of ``name`` (``strip().lower()``); carries the
    # unique index so the case-insensitive contract is enforced by the
    # database regardless of column collation. Kept in sync with
    # ``name`` on every save and on attribute assignment. Defined here
    # so a fresh install lands the full registry contract in a single
    # migration (``0001_initial``) — there is no follow-up migration.
    name_key = models.CharField(max_length=50, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Order case-insensitively so the sidebar stays stable even when
        # users mix cases ("Docker" sits next to "docker").
        ordering = ["name"]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name

    def save(self, *args, **kwargs):
        # Strip whitespace; keep the user's original casing on `name`,
        # but also recompute `name_key` so it can never drift out of
        # sync (e.g. if someone constructs a Tool via the ORM and sets
        # `name` directly without going through ``__setattr__``).
        self.name = (self.name or "").strip()
        if not self.name:
            # Defensive: the API layer rejects empty names long before
            # we get here, but if anyone constructs a Tool directly
            # via the ORM with an empty name we fall back to ``"text"``
            # rather than crashing.
            self.name = "text"
        self.name_key = self.name.lower()
        return super().save(*args, **kwargs)

    def __setattr__(self, name, value):
        # Keep ``name_key`` in sync whenever ``name`` is reassigned so
        # the unique-index column never lags behind the display column.
        if name == "name":
            value = (value or "").strip() or "text"
            super().__setattr__("name", value)
            super().__setattr__("name_key", value.lower())
            return
        super().__setattr__(name, value)


class Snippet(models.Model):
    """A single command/snippet entry in the vault."""

    title = models.CharField(max_length=255)
    code_body = models.TextField()
    tool = models.CharField(max_length=50, default="bash")
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
        """Lowercase + strip a single scalar string. None becomes ''.

        Used only for ``tags`` tokens. ``tool`` keeps its original
        casing — see ``_apply_normalization``.
        """
        if value is None:
            return ""
        return str(value).strip().lower()

    @staticmethod
    def _norm_tool(value):
        """Strip surrounding whitespace only; preserve user casing.

        Returns ``""`` for None / non-string input so the caller can
        fall back to the DB default (``"bash"``).
        """
        if not isinstance(value, str):
            return ""
        return value.strip()

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
        """In-place canonicalization of `tool` and `tags`.

        ``tool`` is only stripped — the user-supplied casing is kept
        so a Tool registered as ``"Docker"`` round-trips as ``"Docker"``.
        Tags remain lowercase-deduped as before.
        """
        # `tool` may be defaulted by the DB to "bash" before this
        # runs on a fresh object; .strip() is a no-op on that default.
        norm_tool = self._norm_tool(self.tool)
        self.tool = norm_tool or "bash"
        self.tags = self._norm_tags(self.tags)

    def save(self, *args, **kwargs):
        """Persist with normalized `tool` (stripped, original case)
        and `tags` (lowercased + deduped)."""
        self._apply_normalization()
        return super().save(*args, **kwargs)

    def __setattr__(self, name, value):
        """Normalize on assignment too, so in-memory mutations stay clean."""
        if name == "tool":
            value = self._norm_tool(value) or "bash"
        elif name == "tags":
            value = self._norm_tags(value)
        super().__setattr__(name, value)