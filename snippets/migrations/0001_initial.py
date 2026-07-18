# Initial schema for the snippets app.
#
# Creates the two tables the project ships with, in their final shape,
# so no follow-up migrations are needed:
#
#   - snippets_snippet
#       title, code_body, tool (default 'bash'), tags, created_at.
#       The column is named ``tool`` from day one — earlier revisions
#       used ``language`` and required a separate ``RenameField``
#       migration to land on this name. That rename has been folded
#       into this initial migration so a fresh install applies both
#       tables in a single step.
#
#   - snippets_tool
#       name, name_key (case-folded copy of ``name``, carries the
#       unique index for case-insensitive registry uniqueness),
#       created_at. ``Tool.save()`` and ``Tool.__setattr__`` keep
#       ``name_key`` in sync with ``name`` from the very first row,
#       so there is no backfill migration either.

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Snippet",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("title", models.CharField(max_length=255)),
                ("code_body", models.TextField()),
                ("tool", models.CharField(default="bash", max_length=50)),
                ("tags", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["title"], name="snippets_sn_title_idx"),
                    models.Index(fields=["tags"], name="snippets_sn_tags_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="Tool",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                # Display name: keeps the user-supplied casing verbatim.
                ("name", models.CharField(max_length=50)),
                # Case-folded copy of ``name`` (``strip().lower()``);
                # carries the unique index so case-only duplicates
                # ("Jenkins" vs "jenkins") are rejected at the DB
                # level on a collation-independent column.
                ("name_key", models.CharField(max_length=50, unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
    ]