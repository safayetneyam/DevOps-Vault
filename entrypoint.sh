#!/usr/bin/env bash
# Entry point for the Django web container.
#
# Responsibilities:
#   1. Wait for PostgreSQL to accept connections on DB_HOST:DB_PORT.
#   2. Apply database migrations (idempotent).
#   3. Exec gunicorn to serve the app on :8000.
#
# All configuration comes from environment variables (12-factor).

set -euo pipefail

DB_HOST="${DB_HOST:-db}"
DB_PORT="${DB_PORT:-5432}"
POSTGRES_DB="${POSTGRES_DB:-vault}"
POSTGRES_USER="${POSTGRES_USER:-vault}"

echo "[entrypoint] Waiting for PostgreSQL at ${DB_HOST}:${DB_PORT} ..."

# Poll the TCP port using `nc -z`. Exponential-ish backoff capped at 5s.
attempt=0
max_attempts=60  # ~5 minutes total
until nc -z "${DB_HOST}" "${DB_PORT}" 2>/dev/null; do
    attempt=$((attempt + 1))
    if [ "${attempt}" -ge "${max_attempts}" ]; then
        echo "[entrypoint] ERROR: PostgreSQL not reachable after ${max_attempts} attempts." >&2
        exit 1
    fi
    sleep 2
done

# Give Postgres an extra moment to finish initializing on first boot.
echo "[entrypoint] Probing database with a real query ..."
probe_attempt=0
until python - <<PY 2>/dev/null
import os, psycopg
psycopg.connect(
    host="${DB_HOST}",
    port=int("${DB_PORT}"),
    dbname="${POSTGRES_DB}",
    user="${POSTGRES_USER}",
    password=os.environ.get("POSTGRES_PASSWORD", ""),
    connect_timeout=3,
).close()
PY
do
    probe_attempt=$((probe_attempt + 1))
    if [ "${probe_attempt}" -ge 30 ]; then
        echo "[entrypoint] ERROR: DB auth probe failed; check POSTGRES_* credentials." >&2
        exit 1
    fi
    sleep 2
done

echo "[entrypoint] Running migrations ..."
python manage.py migrate --noinput

echo "[entrypoint] Starting gunicorn on 0.0.0.0:8000 ..."
exec gunicorn vault.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --access-logfile - \
    --error-logfile -