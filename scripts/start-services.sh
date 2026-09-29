#!/usr/bin/env bash
# Start external services needed for local development.
#
#   Redis      - required: Celery broker + result backend + live-event pub/sub
#   PostgreSQL - optional: backend falls back to SQLite (./ansible_gui.db) when
#                DATABASE_URL is unset, so Postgres is only started via Docker.
#
# Strategy: prefer `docker compose` (starts both); without Docker, fall back
# to a native redis-server for Redis and skip Postgres.
set -euo pipefail
cd "$(dirname "$0")/.."

docker_ok() {
  docker compose version >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

if docker_ok; then
  echo "==> starting redis + postgres via docker compose"
  docker compose up -d redis postgres
  echo "redis:    localhost:6379 (container network only)"
  echo "postgres: container network only (backend uses DATABASE_URL)"
  exit 0
fi

echo "==> docker unavailable; starting Redis natively"
if ! command -v redis-server >/dev/null 2>&1; then
  echo "ERROR: redis-server not found."
  echo "Install it with:  sudo apt install -y redis-server   (Debian/Ubuntu)"
  echo "              or:  brew install redis                (macOS)"
  exit 1
fi
if redis-cli ping >/dev/null 2>&1; then
  echo "redis already running on localhost:6379"
else
  # no persistence: dev broker only; job state lives in the database
  redis-server --daemonize yes --save '' --appendonly no --port 6379
  sleep 0.5
  redis-cli ping >/dev/null 2>&1 || { echo "ERROR: redis failed to start"; exit 1; }
  echo "redis started on localhost:6379"
fi
echo "NOTE: postgres not started (needs docker). Backend uses SQLite by default."
