#!/usr/bin/env bash
# Start the existing ChemBIG database/files at http://52.78.201.155:8502.
set -euo pipefail
cd "$(dirname "$0")/.."
export COMPOSE_FILE=compose.yaml:compose.verify.yaml:compose.public.yaml
export COMPOSE_PROJECT_NAME=chembig-e2e
export COMPOSE_ENV_FILES=artifacts/verify.env
docker compose up -d --no-build web worker nginx
docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload
