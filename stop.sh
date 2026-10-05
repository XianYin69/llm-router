#!/usr/bin/env bash
# SMSocket kernel stopper - bash. Stops the detached process (runtime/SMSocket.pid).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDF="$ROOT/runtime/SMSocket.pid"
if [ ! -f "$PIDF" ]; then echo "[stop] no pid file - kernel not started by start.sh"; exit 0; fi
for id in $(tr -d '[:space:],' < "$PIDF"); do
  if kill -0 "$id" 2>/dev/null; then
    pkill -P "$id" 2>/dev/null || true
    kill "$id" 2>/dev/null || true
    sleep 1
    if kill -0 "$id" 2>/dev/null; then kill -9 "$id" 2>/dev/null || true; fi
    echo "[stop] kernel stopped (pid $id)"
  fi
done
rm -f "$PIDF"
