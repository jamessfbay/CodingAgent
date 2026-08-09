#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${CODING_AGENT_CONFIG:-$ROOT_DIR/config.toml}"
AGENT_BIN="$ROOT_DIR/.venv/bin/coding-agent"
PID_FILE="${CODING_AGENT_PID_FILE:-$ROOT_DIR/state/coding-agent.pid}"
LOG_FILE="${CODING_AGENT_LOG_FILE:-$ROOT_DIR/logs/coding-agent.log}"

if [[ ! -x "$AGENT_BIN" ]]; then
  echo "Start failed: $AGENT_BIN was not found. Install CodingAgent first." >&2
  exit 1
fi
if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "Start failed: config file $CONFIG_FILE was not found." >&2
  exit 1
fi
if ! command -v setsid >/dev/null 2>&1; then
  echo "Start failed: setsid is not installed." >&2
  exit 1
fi

mkdir -p -- "$(dirname -- "$PID_FILE")" "$(dirname -- "$LOG_FILE")"

if [[ -f "$PID_FILE" ]]; then
  old_pid="$(<"$PID_FILE")"
  if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "CodingAgent is already running (PID $old_pid)."
    echo "Log: $LOG_FILE"
    exit 0
  fi
  rm -f -- "$PID_FILE"
fi

cd -- "$ROOT_DIR"
nohup setsid "$AGENT_BIN" --config "$CONFIG_FILE" watch \
  >>"$LOG_FILE" 2>&1 </dev/null &
agent_pid=$!

pid_tmp="$PID_FILE.tmp.$$"
printf '%s\n' "$agent_pid" >"$pid_tmp"
mv -- "$pid_tmp" "$PID_FILE"

sleep 1
if ! kill -0 "$agent_pid" 2>/dev/null; then
  rm -f -- "$PID_FILE"
  echo "CodingAgent failed to start. Recent log output:" >&2
  tail -n 30 -- "$LOG_FILE" >&2 || true
  exit 1
fi

echo "CodingAgent started (PID $agent_pid)."
echo "Log: $LOG_FILE"
echo "Stop: $ROOT_DIR/stop.sh"
