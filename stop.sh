#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="${CODING_AGENT_PID_FILE:-$ROOT_DIR/state/coding-agent.pid}"

if [[ ! -f "$PID_FILE" ]]; then
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

echo "Stopping CodingAgent (PID $agent_pid)..."
kill -TERM -- "-$agent_pid" 2>/dev/null || kill -TERM "$agent_pid"

for ((second = 1; second <= 30; second++)); do
  if ! kill -0 "$agent_pid" 2>/dev/null; then
    rm -f -- "$PID_FILE"
    echo "CodingAgent stopped."
    exit 0
  fi
  sleep 1
done

echo "CodingAgent did not exit after 30 seconds; killing its process group." >&2
kill -KILL -- "-$agent_pid" 2>/dev/null || kill -KILL "$agent_pid" 2>/dev/null || true
sleep 1

if kill -0 "$agent_pid" 2>/dev/null; then
  echo "Stop failed: PID $agent_pid still exists. Inspect it manually." >&2
  exit 1
fi

rm -f -- "$PID_FILE"
echo "CodingAgent was force-stopped."
