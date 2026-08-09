#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${CODING_AGENT_CONFIG:-$ROOT_DIR/config.toml}"
PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
force_login=false
list_only=false
selection=""

usage() {
  echo "Usage: $0 [--list] [--force] [repository-or-number]"
  echo "Authenticate one configured repository with an isolated headless GitHub login."
}

while (($#)); do
  case "$1" in
    --list)
      list_only=true
      ;;
    --force)
      force_login=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -n "$selection" ]]; then
        usage >&2
        exit 2
      fi
      selection="$1"
      ;;
  esac
  shift
done

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Authentication failed: $PYTHON_BIN was not found." >&2
  exit 1
fi
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Authentication failed: config file $CONFIG_FILE was not found." >&2
  exit 1
fi

mapfile -t projects < <(
  PYTHONPATH="$ROOT_DIR/src" "$PYTHON_BIN" - "$CONFIG_FILE" <<'PY'
import sys
from coding_agent.config import load_settings

settings = load_settings(sys.argv[1])
for index, target in enumerate(settings.repositories, start=1):
    github = target.github
    print("\x1f".join((
        str(index),
        github.repository,
        github.auth_user,
        str(github.auth_config_dir or ""),
        github.gh_executable,
    )))
PY
)

if ((${#projects[@]} == 0)); then
  echo "Authentication failed: no repositories are configured." >&2
  exit 1
fi

echo "Configured projects:"
for project in "${projects[@]}"; do
  IFS=$'\x1f' read -r index repository auth_user auth_dir gh_executable <<<"$project"
  printf '  %s) %s  user=%s  credentials=%s\n' \
    "$index" "$repository" "${auth_user:-any}" "${auth_dir:-not-configured}"
done

if [[ "$list_only" == true ]]; then
  exit 0
fi

if [[ -z "$selection" ]]; then
  read -r -p "Select a project number: " selection
fi

selected=""
for project in "${projects[@]}"; do
  IFS=$'\x1f' read -r index repository _auth_user _auth_dir _gh_executable <<<"$project"
  if [[ "$selection" == "$index" || "$selection" == "$repository" ]]; then
    selected="$project"
    break
  fi
done

if [[ -z "$selected" ]]; then
  echo "Authentication failed: unknown project selection $selection." >&2
  exit 2
fi

IFS=$'\x1f' read -r index repository auth_user auth_dir gh_executable <<<"$selected"
if [[ -z "$auth_dir" ]]; then
  echo "Authentication failed: $repository has no github.auth_config_dir." >&2
  exit 1
fi
if [[ ! -x "$gh_executable" ]]; then
  echo "Authentication failed: GitHub CLI was not found at $gh_executable." >&2
  exit 1
fi

mkdir -p -- "$auth_dir"
chmod 700 -- "$auth_dir"

current_user=""
if current_user="$(
  GH_CONFIG_DIR="$auth_dir" GH_PROMPT_DISABLED=1 \
    "$gh_executable" api user --jq .login 2>/dev/null
)"; then
  if [[ -n "$auth_user" && "${current_user,,}" != "${auth_user,,}" ]]; then
    echo "Authentication failed: saved user is $current_user; expected $auth_user." >&2
    echo "Run with --force to authenticate the expected account." >&2
    exit 1
  fi
  if [[ "$force_login" != true ]]; then
    echo "Authentication valid for $repository as $current_user."
    exit 0
  fi
fi

echo
echo "Starting headless GitHub device authentication for $repository."
echo "Expected account: ${auth_user:-any authorized account}"
echo "Credential directory: $auth_dir"
echo "Open the displayed URL on another device and enter the one-time code."
echo

GH_CONFIG_DIR="$auth_dir" \
  "$gh_executable" auth login --hostname github.com --git-protocol https --web

current_user="$(
  GH_CONFIG_DIR="$auth_dir" GH_PROMPT_DISABLED=1 \
    "$gh_executable" api user --jq .login
)"
if [[ -n "$auth_user" && "${current_user,,}" != "${auth_user,,}" ]]; then
  echo "Authentication failed: logged in as $current_user; expected $auth_user." >&2
  exit 1
fi

find "$auth_dir" -type f -exec chmod 600 {} +
echo "Authentication saved for $repository as $current_user."
echo "CodingAgent will use: $auth_dir"
