#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] || { echo 'Create .env first'; exit 1; }
# Provision only the configured data path, never source .env as executable shell.
task_data_dir=$(docker compose config --format json | python3 -c 'import json,sys; print(next(v["source"] for v in json.load(sys.stdin)["services"]["web"]["volumes"] if v["target"]=="/data"))')
sudo install -d -m 750 -o 10001 -g 10001 "$task_data_dir" "$task_data_dir/originals" "$task_data_dir/student" "$task_data_dir/submissions" "$task_data_dir/backups"
sudo chmod 755 "$task_data_dir/student" "$task_data_dir/submissions"
docker compose up -d db redis
docker compose run --rm web python manage.py check --deploy --fail-level WARNING
docker compose run --rm web python manage.py migrate --noinput
docker compose run --rm web python manage.py collectstatic --noinput
docker compose up -d --no-build web worker nginx

docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload
