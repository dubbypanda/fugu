#!/usr/bin/env bash
REPRO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INTERVAL="${1:-300}"
MAX_ITERS="${2:-200}"
cd "$REPRO" || exit 1
for ((i=0; i<MAX_ITERS; i++)); do
  [ -f "$REPRO/STOP_MONITOR" ] && { echo "$(date -u +%H:%M:%S) STOP_MONITOR found, exiting" >> "$REPRO/monitor.log"; break; }
  /usr/bin/python3 "$REPRO/harness/monitor.py" >/dev/null 2>&1
  sleep "$INTERVAL"
done
