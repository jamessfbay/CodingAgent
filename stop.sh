#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${CODING_AGENT_PID_FILE:-$ROOT_DIR/state/coding-agent.pid}"
STOP_TIMEOUT="${CODING_AGENT_STOP_TIMEOUT:-30}"
SYSTEMD_UNIT="${CODING_AGENT_SYSTEMD_UNIT:-coding-agent.service}"

if [[ ! "$STOP_TIMEOUT" =~ ^[0-9]+$ ]]; then
  echo "Invalid CODING_AGENT_STOP_TIMEOUT: $STOP_TIMEOUT" >&2
  exit 1
fi

process_group_is_running() {
  local pgid="$1"

  # kill -0 also reports zombie processes. Zombies cannot be killed and are no
  # longer doing work, so only count non-zombie members as running.
  ps -eo pgid=,stat= | awk -v pgid="$pgid" '
    $1 == pgid && $2 !~ /^Z/ { found = 1; exit }
    END { exit(found ? 0 : 1) }
  '
}

if [[ ! -f "$PID_FILE" ]]; then
  if command -v systemctl >/dev/null 2>&1 \
    && systemctl is-active --quiet "$SYSTEMD_UNIT" 2>/dev/null; then
    echo "CodingAgent is managed by systemd ($SYSTEMD_UNIT); stopping the service..."
    if systemctl stop "$SYSTEMD_UNIT" 2>/dev/null \
      || sudo -n systemctl stop "$SYSTEMD_UNIT" 2>/dev/null; then
      echo "CodingAgent stopped."
      exit 0
    fi
    echo "Unable to stop $SYSTEMD_UNIT without elevated privileges." >&2
    echo "Run: sudo systemctl stop $SYSTEMD_UNIT" >&2
    exit 1
  fi
  echo "CodingAgent is not running (no PID file)."
  exit 0
fi

agent_pid="$(<"$PID_FILE")"
if [[ ! "$agent_pid" =~ ^[0-9]+$ ]]; then
  echo "Invalid PID file removed: $PID_FILE" >&2
  rm -f -- "$PID_FILE"
  exit 1
fi
if ! kill -0 "$agent_pid" 2>/dev/null; then
  echo "CodingAgent is already stopped; removing stale PID file."
  rm -f -- "$PID_FILE"
  exit 0
fi

cmdline="$(tr '\0' ' ' <"/proc/$agent_pid/cmdline" 2>/dev/null || true)"
if [[ "$cmdline" != *"coding-agent"* || "$cmdline" != *"watch"* ]]; then
  echo "Refusing to stop PID $agent_pid: it does not look like CodingAgent watch." >&2
  echo "Command: ${cmdline:-unavailable}" >&2
  exit 1
fi

agent_pgid="$(ps -o pgid= -p "$agent_pid" | tr -d '[:space:]')"
if [[ ! "$agent_pgid" =~ ^[0-9]+$ ]] || [[ "$agent_pgid" != "$agent_pid" ]]; then
  echo "Refusing to stop PID $agent_pid: it is not the leader of its process group." >&2
  echo "Expected PGID $agent_pid, found ${agent_pgid:-unavailable}." >&2
  exit 1
fi

echo "Stopping CodingAgent process group $agent_pgid (leader PID $agent_pid)..."
kill -TERM -- "-$agent_pgid"

for ((second = 0; second < STOP_TIMEOUT; second++)); do
  if ! process_group_is_running "$agent_pgid"; then
    rm -f -- "$PID_FILE"
    echo "CodingAgent stopped."
    exit 0
  fi
  sleep 1
done

if ! process_group_is_running "$agent_pgid"; then
  rm -f -- "$PID_FILE"
  echo "CodingAgent stopped."
  exit 0
fi

echo "CodingAgent did not exit after $STOP_TIMEOUT seconds; killing process group $agent_pgid." >&2
kill -KILL -- "-$agent_pgid" 2>/dev/null || true
sleep 1

if process_group_is_running "$agent_pgid"; then
  echo "Stop failed: process group $agent_pgid still has running processes. Inspect it manually." >&2
  exit 1
fi

rm -f -- "$PID_FILE"
echo "CodingAgent was force-stopped."
