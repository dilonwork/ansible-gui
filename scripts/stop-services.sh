#!/usr/bin/env bash
# Stop the external services started by start-services.sh.
set -euo pipefail
cd "$(dirname "$0")/.."

if docker compose version >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  echo "==> stopping redis + postgres (docker compose)"
  docker compose stop redis postgres
  exit 0
fi

echo "==> stopping native redis-server"
redis-cli shutdown nosave >/dev/null 2>&1 && echo "redis stopped" \
  || echo "redis was not running"
