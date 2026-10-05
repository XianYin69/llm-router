#!/usr/bin/env bash
# SMSocket kernel launcher - bash entry point (Linux / macOS / Git Bash).
#   ./start.sh              foreground (Ctrl-C stops it)
#   ./start.sh -d           detached: runtime/SMSocket.pid + SMSocket.log
#   ./start.sh -d -p 8011 -c config.yaml --reload
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
RUNTIME="$ROOT/runtime"; mkdir -p "$RUNTIME"
PIDF="$RUNTIME/SMSocket.pid"; LOGF="$RUNTIME/SMSocket.log"; ERRF="$RUNTIME/SMSocket.err.log"
BG=0; PORT=""; LH=""; CONF=""; RELOAD=0; NOKEY=0; ROTATE=0
while [ $# -gt 0 ]; do
  case "$1" in
    -d|--background) BG=1 ;;
    -p|--port) PORT="$2"; shift ;;
    --host) LH="$2"; shift ;;
    -c|--config) CONF="$2"; shift ;;
    --reload) RELOAD=1 ;;
    --no-key) NOKEY=1 ;;
    --rotate-key) ROTATE=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
  shift
done

PY="${SMSSOCKET_PYTHON:-}"
if [ -z "$PY" ] && [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"; fi
if [ -z "$PY" ] && [ -x "$ROOT/.venv/Scripts/python.exe" ]; then PY="$ROOT/.venv/Scripts/python.exe"; fi
if [ -z "$PY" ]; then PY="$(command -v python3 || command -v python || true)"; fi
if [ -z "$PY" ]; then echo "[start] no python interpreter (set SMSSOCKET_PYTHON)" >&2; exit 1; fi

CONF="${CONF:-config.yaml}"
if [ ! -f "$CONF" ]; then echo "[start] missing $CONF - copy config.example.yaml" >&2; exit 1; fi
ARGS="run.py --config $CONF"
if [ -n "$PORT" ]; then ARGS="$ARGS --port $PORT"; fi
if [ -n "$LH" ]; then ARGS="$ARGS --host $LH"; fi
if [ "$RELOAD" = 1 ]; then ARGS="$ARGS --reload"; fi
if [ "$NOKEY" = 1 ]; then ARGS="$ARGS --no-key"; fi
if [ "$ROTATE" = 1 ]; then ARGS="$ARGS --rotate-key"; fi

SHOWN_PORT="${PORT:-$(sed -n 's/^listen:.*:\([0-9][0-9]*\).*/\1/p' "$CONF" | head -1)}"
if [ -z "$SHOWN_PORT" ]; then SHOWN_PORT=8000; fi
KEY="-"
if [ -f smsocket.key ]; then KEY="$(head -1 smsocket.key)"; fi

if [ -f "$PIDF" ]; then
  OLD="$(head -1 "$PIDF" | tr -d '[:space:]')"
  if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null; then
    echo "[start] already running (pid $OLD) - ./stop.sh first"
    exit 0
  fi
  rm -f "$PIDF"
fi

show_info() {
  echo "  web  http://127.0.0.1:${SHOWN_PORT}/"
  echo "  api  http://127.0.0.1:${SHOWN_PORT}/v1"
  if [ -f smsocket.key ]; then echo "  key  $(head -1 smsocket.key)"; fi
  echo "  log  $LOGF"
}

if [ "$BG" = 0 ]; then
  echo "[start] $PY $ARGS"
  exec "$PY" $ARGS
fi

echo "[start] detaching: $PY $ARGS"
nohup "$PY" $ARGS >"$LOGF" 2>"$ERRF" &
echo $! > "$PIDF"
sleep 3
if ! kill -0 "$(cat "$PIDF")" 2>/dev/null; then
  echo "[start] kernel died - last stderr:"; tail -20 "$ERRF" || true
  rm -f "$PIDF"; exit 1
fi
echo "[start] kernel up (pid $(cat "$PIDF"))"
show_info
