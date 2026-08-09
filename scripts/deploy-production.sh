#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="${DEPLOY_ROOT:-/var/www/workspace}"
EXPECTED_ROOT="/var/www/workspace"

if [[ "$ROOT_DIR" != "$EXPECTED_ROOT" ]]; then
  echo "Refusing unexpected deployment root: $ROOT_DIR" >&2
  exit 1
fi

cd "$ROOT_DIR"
git fetch origin main
git merge --ff-only origin/main

./buildFrontend.sh

# Production process management is installation-specific. Configure this hook
# on the protected self-hosted runner, for example to invoke systemd or Forever.
if [[ -z "${DEPLOY_RESTART_COMMAND:-}" ]]; then
  echo "DEPLOY_RESTART_COMMAND is required on the protected production runner" >&2
  exit 1
fi

/usr/bin/env bash -lc "$DEPLOY_RESTART_COMMAND"

curl --fail --silent --show-error "${DEPLOY_HEALTHCHECK_URL:-https://api.jox.fun/health}"
