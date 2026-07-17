# DevOps Vault

A minimal, containerised snippet manager for terminal commands, configs, and code. Personal use, fully self-hosted, one `docker compose up` away.

- **Backend:** Python 3.11, Django 5, Django REST Framework
- **Database:** PostgreSQL 15 (Alpine)
- **Frontend:** Single-page vanilla HTML/CSS/JavaScript (no build step, no framework)
- **Containerisation:** Docker + Docker Compose
- **Tests:** pytest + pytest-django

---

## Features

- **Search** by title or tag (case-insensitive substring)
- **Create / Edit / Delete** individual snippets
- **Batch delete** selected snippets
- **Admin panel** with grouped view + sidebar to rename or bulk-delete entire tools
- **REST API** for everything (the frontend is just a thin client over it)

---

## Quick Start (local)

```bash
cp .env.example .env
# Edit .env and replace DJANGO_SECRET_KEY and POSTGRES_PASSWORD.

docker compose up --build
```

Then open <http://localhost:8000/>.

What happens on `up`:

1. `db` (Postgres 15 Alpine) starts and waits for its `pg_isready` healthcheck to pass.
2. `web` waits for `db:healthy` via `depends_on: condition: service_healthy`.
3. `entrypoint.sh` polls `db:5432` and probes the DB with a real `psycopg` connection.
4. `python manage.py migrate --noinput` runs (idempotent — `snippets.0001_initial` is committed).
5. `gunicorn` binds `0.0.0.0:8000` and serves both the API and the static frontend.

---

## Routes

| Path | Purpose |
|---|---|
| `/` | Main UI (search, view, create) |
| `/create/` | New-snippet form |
| `/edit/<id>/` | Edit existing snippet |
| `/temp-admin/` | Admin: grouped snippets table + tools/stacks sidebar |
| `/admin/` | Django admin (create a superuser with `docker compose exec web python manage.py createsuperuser`) |
| `/api/snippets/` | REST API root |

---

## REST API

| Method | Path | Body / Query | Purpose |
|---|---|---|---|
| `GET` | `/api/snippets/` | `?q=<text>` | List all snippets, or filter by title/tags (case-insensitive `icontains`) |
| `POST` | `/api/snippets/` | `Snippet` JSON | Create a snippet — returns `201 Created` |
| `GET` | `/api/snippets/<id>/` | — | Fetch one snippet |
| `PUT` / `PATCH` | `/api/snippets/<id>/` | partial or full `Snippet` JSON | Update a snippet |
| `DELETE` | `/api/snippets/<id>/` | — | Delete one snippet |
| `POST` | `/api/snippets/batch-delete/` | `{"ids": [1, 2, 3]}` | Delete many snippets |
| `POST` | `/api/snippets/bulk-rename-tool/` | `{"old": "bash", "new": "shell"}` | Rename a tool across all matching snippets |
| `POST` | `/api/snippets/bulk-delete-tool/` | `{"tool": "bash"}` | Delete every snippet with the given language |

`language` is normalised to lowercase + stripped on save, so lookups are always case-insensitive.

---

## Tests

```bash
docker compose exec web pytest -v
```

The suite covers:

- Snippet creation (POST happy path + validation)
- Search filtering on title and tags
- Detail view + update + delete
- Batch delete
- Bulk rename by tool (happy path, no-op, validation, normalisation)
- Bulk delete by tool (happy path, no-match, validation, normalisation)

---

## Persistence

Data lives in the named Docker volume `vault_postgres_data`.

```bash
# Stop the stack, KEEP the volume:
docker compose down

# Stop AND wipe the database (destructive):
docker compose down -v
```

---

## Configuration

Everything is read from environment variables — no secrets are hardcoded. See `.env.example` for the full contract.

| Variable | Purpose |
|---|---|
| `DJANGO_SECRET_KEY` | Django secret key (rotate before going live) |
| `DJANGO_DEBUG` | `1` for development, `0` for production |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames Django will serve (e.g. `localhost,127.0.0.1,<your-domain>`) |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | DB credentials, shared by `db` and `web` services |
| `DB_HOST` / `DB_PORT` | Where `web` connects to Postgres (defaults to `db:5432`) |
| `VAULT_PASSWORD_HASH` | PBKDF2-SHA256 hash (base64) of the shared write-protection password. Leave empty to disable the gate. Generate via `python -c "import base64, hashlib; print(base64.b64encode(hashlib.pbkdf2_hmac('sha256', b'YOUR_PASSWORD', b'vault-salt-v1', 200000)).decode())"` |

### Write-protection (vault password)

Every mutating endpoint — `POST/PUT/PATCH/DELETE /api/snippets/…` and the three bulk endpoints (`batch-delete`, `bulk-rename-tool`, `bulk-delete-tool`) — requires the shared vault password. Reads (`GET`) stay open.

Clients must send the password in one of two ways:

* `X-Vault-Password: <plaintext>` (preferred — sent by the front-end pages)
* `Authorization: Bearer <plaintext>` (REST-friendly fallback)

The server stores only the PBKDF2 hash; the plaintext lives in `.env` for human reference and is never logged. Comparison is constant-time.

The front-end (`/create/`, `/edit/`, `/temp-admin/`) collects the password via `window.prompt` on every save — there is no session unlock, by design.

---

## Project layout

```
.
├── Dockerfile                # python:3.11-slim, layer-cached pip, runs entrypoint.sh
├── docker-compose.yml        # db (Postgres 15 + healthcheck) + web (gunicorn)
├── entrypoint.sh             # wait-for-db → migrate → exec gunicorn
├── requirements.txt          # Django, DRF, psycopg, pytest, gunicorn
├── pytest.ini                # DJANGO_SETTINGS_MODULE = vault.settings
├── .env.example              # env-var contract (12-factor)
├── .dockerignore / .gitignore
├── manage.py
├── vault/                    # Django project
│   ├── __init__.py
│   ├── settings.py           # env-driven, no hardcoded secrets
│   ├── urls.py               # /api/snippets/ + / + /admin/
│   ├── wsgi.py
│   └── asgi.py
├── snippets/                 # Django app
│   ├── __init__.py
│   ├── apps.py
│   ├── admin.py
│   ├── models.py             # Snippet(title, code_body, language, tags, created_at)
│   ├── serializers.py        # SnippetSerializer (all fields)
│   ├── views.py              # list/create, detail, batch-delete, bulk-rename-tool, bulk-delete-tool
│   ├── urls.py
│   ├── tests.py              # full backend test suite (21 tests)
│   └── migrations/
│       ├── __init__.py
│       └── 0001_initial.py
└── frontend/
    ├── index.html            # search + view + create flow
    ├── create.html           # new-snippet form
    ├── edit.html             # edit-existing-snippet form
    └── temp-admin.html       # admin: grouped table + tools/stacks sidebar
```