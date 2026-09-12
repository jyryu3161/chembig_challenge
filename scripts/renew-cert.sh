#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose run --rm certbot renew --quiet
docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload
