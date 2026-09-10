#!/bin/sh
set -eu

: "${PORT:=8080}"
: "${API_PORT:=5000}"

if [ -z "${ADMIN_API_KEY:-}" ]; then
  echo "ERROR: ADMIN_API_KEY must be set" >&2
  exit 1
fi

mkdir -p /run/nginx
sed -e "s/__PORT__/${PORT}/g" -e "s/__API_PORT__/${API_PORT}/g" \
  /opt/railway-firefox/nginx.conf.template > /run/nginx/railway.conf

/opt/venv/bin/gunicorn --workers 1 --bind "127.0.0.1:${API_PORT}" --access-logfile - app:app &
API_PID=$!
trap 'kill "$API_PID" 2>/dev/null || true' TERM INT EXIT

exec nginx -c /run/nginx/railway.conf -g 'daemon off;'
