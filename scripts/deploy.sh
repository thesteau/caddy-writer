#!/bin/sh
set -eu

SOURCE_PATH="${1:-/app/output/Caddyfile.generated}"
TARGET_DIR="${CADDY_OUTPUT_DIR:-/deploy-target}"
TARGET_NAME="${CADDY_OUTPUT_FILENAME:-Caddyfile.generated}"
TARGET_PATH="$TARGET_DIR/$TARGET_NAME"

if [ ! -f "$SOURCE_PATH" ]; then
  echo "generated file not found: $SOURCE_PATH" >&2
  exit 1
fi

if [ ! -d "$TARGET_DIR" ]; then
  echo "caddy output directory not found: $TARGET_DIR" >&2
  exit 1
fi

cp "$SOURCE_PATH" "$TARGET_PATH"
echo "Copied generated file to: $TARGET_PATH"
