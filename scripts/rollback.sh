#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export APP_IMAGE=${1:?Usage: rollback.sh previously-verified-image:tag}
docker image inspect "$APP_IMAGE" >/dev/null
docker compose run --rm web python manage.py check --deploy --fail-level WARNING
docker compose run --rm web python manage.py collectstatic --noinput
docker compose up -d --no-build --force-recreate web worker
printf 'Rolled back application only. Persist APP_IMAGE in .env. Schema rollback requires the documented isolated restore.\n'

docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload
