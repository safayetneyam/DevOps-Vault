# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps. We use psycopg[binary] (no libpq build), so we only need
# a minimal runtime: netcat for the wait-for-db loop in entrypoint.sh.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        netcat-openbsd \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy ONLY the dependency manifest first so this layer is cached
# independently of source changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the rest of the project.
COPY . .

# Make the entrypoint executable (kept readable on Windows checkouts too).
RUN chmod +x ./entrypoint.sh

EXPOSE 8000

# Default: gunicorn via the wait-for-db entrypoint.
CMD ["./entrypoint.sh"]