#!/bin/sh
set -eu

SOURCE_PATH="${1:-/app/output/Caddyfile.generated}"
TARGET_PATH="${CADDY_TARGET_FILE:-/deploy-target/Caddyfile}"
CONTAINER_NAME="${CADDY_CONTAINER_NAME:-caddy}"
CONFIG_PATH="${CADDY_CONTAINER_CONFIG_PATH:-/etc/caddy/Caddyfile}"

if [ ! -f "$SOURCE_PATH" ]; then
  echo "generated file not found: $SOURCE_PATH" >&2
  exit 1
fi

if [ ! -f "$TARGET_PATH" ]; then
  echo "deploy target missing: $TARGET_PATH" >&2
  exit 1
fi

BACKUP_PATH="$TARGET_PATH.$(date +%Y%m%d%H%M%S).bak"

cp "$TARGET_PATH" "$BACKUP_PATH"
cp "$SOURCE_PATH" "$TARGET_PATH"

docker exec "$CONTAINER_NAME" caddy validate --config "$CONFIG_PATH" --adapter caddyfile
docker exec "$CONTAINER_NAME" caddy reload --config "$CONFIG_PATH" --adapter caddyfile

rm -f "$BACKUP_PATH"
