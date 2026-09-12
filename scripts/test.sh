#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose -f compose.test.yaml up -d --wait
export DEBUG=1 POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=55432 POSTGRES_DB=chembig_test POSTGRES_USER=chembig_test POSTGRES_PASSWORD=local-test-only REDIS_URL=redis://127.0.0.1:56379/0
.venv/bin/python manage.py test tests --noinput "$@"
