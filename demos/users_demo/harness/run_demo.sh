#!/usr/bin/env bash
set -uo pipefail
HARNESS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPRO="$(cd "$HARNESS/.." && pwd)"
SECRETS="${FUGU_REPRO_SECRETS:-$HOME/.fugu_repro_secrets.env}"
[ -f "$SECRETS" ] && source "$SECRETS"

NAME="${1:?demo name (matches prompts/<name>.txt)}"; TO="${2:-18000}"
PROMPT="$REPRO/prompts/$NAME.txt"
[ -f "$PROMPT" ] || { echo "no prompt at $PROMPT" >&2; exit 2; }
DEMO="$REPRO/$NAME"
mkdir -p "$DEMO"/fugu-ultra "$DEMO"/gpt55 "$DEMO"/opus48 "$DEMO"/gemini

START=$(date +%s)
echo "[$(date -u +%H:%M:%S)] DEMO START $NAME (timeout ${TO}s/model)"
declare -a PIDS=()
for mk in fugu-ultra gpt55 opus48 gemini; do
  ( bash "$HARNESS/run_codex.sh" "$mk" "$DEMO/$mk" "$PROMPT" "$TO" ) >"$DEMO/$mk/launch.log" 2>&1 &
  PIDS+=($!)
done

FAIL=0
for p in "${PIDS[@]}"; do wait "$p" || FAIL=$((FAIL+1)); done
END=$(date +%s)
echo "[$(date -u +%H:%M:%S)] DEMO COMPLETE $NAME  elapsed=$((END-START))s failures=$FAIL"
