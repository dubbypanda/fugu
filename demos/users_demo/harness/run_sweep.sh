#!/usr/bin/env bash
set -uo pipefail
HARNESS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPRO="$(cd "$HARNESS/.." && pwd)"
SECRETS="${FUGU_REPRO_SECRETS:-$HOME/.fugu_repro_secrets.env}"
PROXY_PYTHON="${PROXY_PYTHON:-$REPRO/.venv/bin/python}"
[ -f "$SECRETS" ] && source "$SECRETS"
TO="${1:-18000}"
export PATH="$HOME/.local/bin:$PATH"
rm -f "$REPRO/STOP_MONITOR"

PORT=""
for p in $(seq 9410 9520); do ss -tln 2>/dev/null | grep -q ":$p " || { PORT=$p; break; }; done
echo "$PORT" > "$HARNESS/.proxy_port"
PORT="$PORT" setsid "$PROXY_PYTHON" "$HARNESS/or_proxy.py" >> "$HARNESS/or_proxy.log" 2>&1 < /dev/null &
setsid bash "$HARNESS/proxy_keeper.sh" >/dev/null 2>&1 < /dev/null &
sleep 5
curl -sf -m 5 -o /dev/null "http://127.0.0.1:$PORT/healthz" && echo "proxy up on :$PORT" || echo "WARN: proxy not healthy on :$PORT"

setsid bash "$HARNESS/monitor_daemon.sh" 600 200 >/dev/null 2>&1 < /dev/null &

for f in "$REPRO"/prompts/*.txt; do
  [ -f "$f" ] || continue
  d="$(basename "$f" .txt)"
  mkdir -p "$REPRO/$d"
  setsid bash "$HARNESS/run_demo.sh" "$d" "$TO" > "$REPRO/$d/orchestrator.log" 2>&1 < /dev/null &
  echo "launched $d"
  sleep 3
done

echo ""
echo "Sweep launched (proxy :$PORT, ${TO}s/cell)."
echo "Monitor:  python3 $HARNESS/monitor.py     |  tail -f $REPRO/monitor.log"
echo "When done: python3 $HARNESS/collect.py    ; touch $REPRO/STOP_MONITOR to stop daemons."
