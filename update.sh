#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${CODING_AGENT_CONFIG:-$ROOT_DIR/config.toml}"
AGENT_BIN="$ROOT_DIR/.venv/bin/coding-agent"
REPOSITORY="${1:-unsungb/base-all}"

if [[ ! -x "$AGENT_BIN" ]]; then
  echo "Update failed: $AGENT_BIN was not found. Install CodingAgent first." >&2
  exit 1
fi
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Update failed: config file $CONFIG_FILE was not found." >&2
  exit 1
fi

cd -- "$ROOT_DIR"
exec "$AGENT_BIN" --config "$CONFIG_FILE" update --repository "$REPOSITORY"
