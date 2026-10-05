#!/usr/bin/env bash
# SMSocket kernel status - bash. pid / liveness / urls / log tail.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
PIDF="runtime/SMSocket.pid"; LOGF="runtime/SMSocket.log"
CONF="${SMSSOCKET_CONFIG:-config.yaml}"
PORT="$(sed -n 's/^listen:.*:\([0-9][0-9]*\).*/\1/p' "$CONF" 2>/dev/null | head -1)"
PORT="${PORT:-8000}"
STATE="down"
if [ -f "$PIDF" ]; then
  ID="$(head -1 "$PIDF" | tr -d '[:space:]')"
  if [ -n "$ID" ] && kill -0 "$ID" 2>/dev/null; then STATE="up (pid $ID)"; fi
fi
echo "[status] SMSocket kernel: $STATE"
echo "  web  http://127.0.0.1:${PORT}/"
echo "  api  http://127.0.0.1:${PORT}/v1"
[ -f smsocket.key ] && echo "  key  $(head -1 smsocket.key)"
[ -f "$LOGF" ] && { echo "  last log lines:"; tail -15 "$LOGF" | sed 's/^/    /'; }
