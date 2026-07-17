# Product Specification: Developer Snippet & Command Vault (MVP)

## 1. Project Overview
The objective is to build a minimal, functional, and containerized multi-tier web application called "Developer Vault". This application serves as a personal repository for storing and searching terminal commands, configurations, and code snippets. 

The primary goal of this project is to serve as a validation target for a DevOps CI/CD pipeline. The codebase must prioritize clean environment isolation, easy local containerization, and testability over complex business features.

### Tech Stack
- **Backend:** Python 3.11, Django 5.x, Django REST Framework (DRF)
- **Database:** PostgreSQL 15 (Alpine)
- **Frontend:** Single-page Vanilla HTML/CSS/JavaScript (served statically by Django)
- **Containerization:** Docker & Docker Compose

---

## 2. System Architecture & Environment Constraints

- **Strict Twelve-Factor Compliance:** No secrets, credentials, or environment-specific configurations may be hardcoded. The application must read configurations entirely from environment variables.
- **Database Dependency Management:** The backend container must gracefully wait for PostgreSQL to become responsive before applying database migrations (`python manage.py migrate`) and starting the server.
- **Stateless App Tier:** The application container must remain completely stateless. All persistent states must reside strictly within the PostgreSQL container.

---

## 3. Backend Specification

### Database Schema (The `Snippet` Model)
- `id`: Auto-incrementing integer (Primary Key)
- `title`: Varchar (Max length 255) — e.g., "Force Delete K8s Namespace"
- `code_body`: Text — The actual terminal command or configuration file content
- `language`: Varchar (Max length 50, default: "bash") — For styling references
- `tags`: Text — A comma-separated list of keywords (e.g., "k8s, kubernetes, cleanup")
- `created_at`: DateTime (Automatically populated on creation)

### API Endpoints
1. **`POST /api/snippets/`**
   - Payload: JSON object matching the schema fields.
   - Response: `201 Created` with the saved snippet object.
2. **`GET /api/snippets/?q=<search_query>`**
   - If `q` is missing, return a list of all snippets.
   - If `q` is provided, return snippets where the query matches either the `title` OR the `tags` field (using case-insensitive substring matching: `icontains`).
   - Response: `200 OK` with a JSON array of matching snippets.

---

## 4. Frontend Specification

The UI must be contained within a single file (`index.html`) using clean, semantic HTML5, embedded vanilla CSS, and standard JavaScript (`fetch` API). It must handle three actions:

1. **Search & Suggestion Panel:** A clear search bar at the top. As the user types or presses enter, it triggers a call to `/api/snippets/?q=<value>` and dynamically updates a results list below showing the matching snippet titles and tags.
2. **Detail Viewer:** Clicking on a snippet title from the search list instantly renders its `code_body` into a dedicated display block. 
   - **CRITICAL:** The code must be rendered inside a `<pre><code>` HTML block. Do not just use a standard `<div>`. This ensures all whitespace, tabs, and multiline indentation are visually preserved exactly as saved.
3. **Creation Form:** A clean form to input a new snippet's Title, Code Body, Language, and Tags. Submitting this form calls the POST endpoint and updates the main list without reloading the browser window.
   - **CRITICAL:** The "Code Body" input field MUST be a `<textarea>` element, not an `<input type="text">`, to properly capture multiline input and raw whitespace from the user.

---

## 5. Containerization Specification

### `Dockerfile`
- Must use a lightweight, efficient base image (`python:3.11-slim`).
- Set the working directory to `/app`.
- Copy dependency files first to exploit Docker layer caching.
- Expose port `8000`.

### `docker-compose.yml`
Must orchestrate two services:
1. **`db` (PostgreSQL):**
   - Image: `postgres:15-alpine`
   - Exposed internally on port `5432`.
   - Uses a named Docker volume (`postgres_data`) mapped to `/var/lib/postgresql/data` for persistence.
   - Credentials configured via environment variables.
2. **`web` (Django Backend):**
   - Built dynamically from the local `Dockerfile`.
   - Exposed to the host machine on port `8000`.
   - Depends explicitly on the `db` service.
   - Environment variables must mirror the database credentials to establish a secure network connection.

---

## 6. Automated Testing Requirements

To satisfy future CI/CD pipeline verification gates, the codebase must include a robust test script using `pytest` or Django's built-in `TestCase`. 
- **Test 1 (Write Validation):** Ensure sending a valid POST request correctly creates a row in the database.
- **Test 2 (Search Logic Validation):** Populate the test database with a dummy item (e.g., Title: "Docker Clear", Tags: "docker, clean"). Verify that running a GET request filtering for "dock" or "clean" correctly isolates and returns the designated object.

---

```markdown

---

## Output Instructions for Claude
Please generate the complete, production-ready codebase satisfying this layout. Include:
1. The requirements files (`requirements.txt`).
2. The core Django project files, focusing heavily on a decoupled `settings.py`.
3. The `vault` application models, views, routing configurations, and the `index.html` frontend.
4. The production-ready `Dockerfile` and `docker-compose.yml` assets.
5. The automation tests file (`tests.py`).
```

---

## 7. Run Locally

The stack is fully containerised — you only need Docker and Docker Compose on the host.

### One-time setup

```bash
cp .env.example .env
# Edit .env and replace DJANGO_SECRET_KEY and POSTGRES_PASSWORD for any
# non-throwaway environment.
```

### Build and start the stack

```bash
docker compose up --build
```

What happens on `up`:

1. `db` (Postgres 15 Alpine) starts and waits for its `pg_isready` healthcheck to pass.
2. `web` waits for `db:healthy` via `depends_on: condition: service_healthy`.
3. `entrypoint.sh` polls `db:5432` and then probes the DB with a real `psycopg` connection.
4. `python manage.py migrate --noinput` is applied (idempotent — the initial `snippets.0001_initial` migration is committed).
5. `gunicorn` binds `0.0.0.0:8000` and serves both the API and the static frontend.

### Use the app

- Frontend (search, view, create): <http://localhost:8000/>
- API root: <http://localhost:8000/api/snippets/>
- Admin (optional): <http://localhost:8000/admin/>

Quick API smoke test from another shell:

```bash
# Create
curl -X POST http://localhost:8000/api/snippets/ \
     -H 'Content-Type: application/json' \
     -d '{"title":"Force Delete K8s Namespace",
          "code_body":"kubectl delete ns foo --force --grace-period=0",
          "language":"bash",
          "tags":"k8s, kubernetes, cleanup"}'

# Search (title OR tags, case-insensitive)
curl 'http://localhost:8000/api/snippets/?q=k8s'
```

### Run the test suite

```bash
docker compose exec web pytest -v
```

Expected output:

```
snippets/tests.py ..                                                     [100%]
============================== 2 passed in 0.90s ===============================
```

### Persistence and teardown

Data lives in the named Docker volume `vault_postgres_data` (visible in `docker volume ls`).

```bash
# Stop the stack but KEEP the database volume:
docker compose down

# Stop AND wipe the database volume (destructive):
docker compose down -v
```

### Project layout

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
│   ├── views.py              # SnippetListCreateView: GET (filter ?q=), POST (201)
│   ├── urls.py
│   ├── tests.py              # write + search tests
│   └── migrations/
│       ├── __init__.py
│       └── 0001_initial.py
└── frontend/
    └── index.html            # single-file vanilla JS UI (search, <pre><code> viewer, create form with <textarea>)
```