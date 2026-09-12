#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
domain=${1:?Usage: scripts/init-https.sh challenge.example.ac.kr}
[[ "$domain" =~ ^[a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]] || { echo 'Invalid domain'; exit 1; }
docker compose up -d nginx
docker compose run --rm certbot certonly --webroot -w /var/www/certbot --non-interactive --agree-tos --register-unsafely-without-email -d "$domain"
python3 - "$domain" <<'PY'
import pathlib,sys
root=pathlib.Path('deploy')
config=(root/'nginx-tls.conf.template').read_text().replace('DOMAIN',sys.argv[1]).replace('include /etc/nginx/proxy_params_chembig;', (root/'proxy.conf').read_text())
(root/'nginx.conf').write_text(config)
PY
docker compose exec -T nginx nginx -t
docker compose exec -T nginx nginx -s reload
