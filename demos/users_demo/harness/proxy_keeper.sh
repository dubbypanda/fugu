#!/usr/bin/env bash
REPRO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PVENV="${PROXY_PYTHON:-$REPRO/.venv/bin/python}"
PORT="$(cat "$REPRO/harness/.proxy_port" 2>/dev/null || echo 9461)"
cd "$REPRO" || exit 1
while [ ! -f "$REPRO/STOP_MONITOR" ]; do
  if ! curl -sf -m 4 -o /dev/null "http://127.0.0.1:$PORT/healthz" 2>/dev/null; then
    echo "[keeper $(date -u +%H:%M:%S)] proxy down on :$PORT, respawning" >> harness/or_proxy.log
    for _ in 1 2 3 4 5 6; do ss -tln 2>/dev/null | grep -q ":$PORT " || break; sleep 2; done
    PORT="$PORT" setsid "$PVENV" harness/or_proxy.py >> harness/or_proxy.log 2>&1 < /dev/null &
    sleep 5
  fi
  sleep 20
done
