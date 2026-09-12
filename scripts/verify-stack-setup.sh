#!/usr/bin/env bash
# Creates only an isolated, loopback-accessible integration environment.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p artifacts
if [[ ! -e artifacts/verify.env ]]; then
python3 - <<'PY'
from pathlib import Path
import secrets
p=Path('artifacts/verify.env')
p.write_text('DEBUG=1\nSECRET_KEY='+secrets.token_urlsafe(48)+'\nPOSTGRES_DB=chembig_verify\nPOSTGRES_USER=chembig_verify\nPOSTGRES_PASSWORD='+secrets.token_urlsafe(36)+'\nALLOWED_HOSTS=localhost,127.0.0.1\nDATA_DIR=./var/stack-verify\nAPP_IMAGE=chembig:verified\n')
p.chmod(0o600)
PY
fi
export COMPOSE_FILE=compose.yaml:compose.verify.yaml COMPOSE_PROJECT_NAME=chembig-e2e COMPOSE_ENV_FILES=artifacts/verify.env
existing_binding=$(docker compose port nginx 80 2>/dev/null || true)
if [[ -n "$existing_binding" && "$existing_binding" != '127.0.0.1:18080' ]]; then
    echo 'Public service detected. Refusing to replace its configuration with the test setup.'
    exit 1
fi
sudo install -d -m 750 -o 10001 -g 10001 var/stack-verify var/stack-verify/originals var/stack-verify/backups
sudo install -d -m 755 -o 10001 -g 10001 var/stack-verify/student var/stack-verify/submissions
docker compose up -d db redis
docker compose run --rm web python manage.py migrate --noinput
docker compose run --rm web python manage.py collectstatic --noinput
docker compose run --rm web python manage.py seed_demo
docker compose up -d --no-build web worker nginx
printf 'Integration site: http://127.0.0.1:18080\n'
