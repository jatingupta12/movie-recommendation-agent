#!/usr/bin/env bash

# Run the one-shot checks, then keep the local API and stdio MCP server alive.
# The MCP process is useful when its stdin/stdout are connected to an MCP host;
# for Codex, configure the server in Codex and let Codex launch its own process.

set -u

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [[ -x "$PROJECT_DIR/.venv/bin/python" ]]; then
  PYTHON="$PROJECT_DIR/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

echo "==> Checking local database"
"$PYTHON" -m weekend_watch.cli health || exit $?

echo "==> Generating weekend digest"
if ! "$PYTHON" -m weekend_watch.cli digest; then
  echo "Warning: digest generation failed. Check TMDB credentials/network; continuing to start local services." >&2
fi

LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/weekend-watch.XXXXXX")"
API_LOG="$LOG_DIR/api.log"
MCP_LOG="$LOG_DIR/mcp.log"
API_PID=""
MCP_PID=""

cleanup() {
  trap - INT TERM EXIT
  for pid in "$MCP_PID" "$API_PID"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  for pid in "$MCP_PID" "$API_PID"; do
    if [[ -n "$pid" ]]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
  echo "Local services stopped. Logs were in $LOG_DIR"
  rm -rf "$LOG_DIR"
}
trap cleanup INT TERM EXIT

echo "==> Starting HTTP API (log: $API_LOG)"
"$PYTHON" -m weekend_watch.api >"$API_LOG" 2>&1 &
API_PID=$!

echo "==> Starting MCP stdio server (log: $MCP_LOG)"
# Keep its stdio input open without consuming this terminal. An MCP host should
# launch its own process with a real JSON-RPC connection to stdin/stdout.
"$PYTHON" -m weekend_watch.mcp_server >"$MCP_LOG" 2>&1 < <(tail -f /dev/null) &
MCP_PID=$!

echo "API: http://127.0.0.1:8000/health (host/port may be configured in .env)"
echo "Press Ctrl-C to stop the local services."
echo "Note: MCP uses stdio and is normally launched directly by Codex or another MCP host."

while kill -0 "$API_PID" 2>/dev/null && kill -0 "$MCP_PID" 2>/dev/null; do
  sleep 1
done

echo "A local service exited. Recent logs:" >&2
tail -n 30 "$API_LOG" "$MCP_LOG" 2>/dev/null || true
exit 1
