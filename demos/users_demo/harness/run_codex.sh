#!/usr/bin/env bash
set -uo pipefail

SECRETS="${FUGU_REPRO_SECRETS:-$HOME/.fugu_repro_secrets.env}"
[ -f "$SECRETS" ] && source "$SECRETS"
export PATH="$HOME/.local/bin:$PATH"

MK="${1:?model_key}"; WD="${2:?workdir}"; PF="${3:?prompt_file}"; TO="${4:-5400}"
mkdir -p "$WD"
PROMPT="$(cat "$PF")"

COMMON=(--dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -C "$WD" \
        --json -o "$WD/last_message.txt")

BIN=codex
declare -a PRE
ENVPREFIX=()
HARNESS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CATALOG="$HARNESS_DIR/openrouter_catalog.json"
OR_DIRECT="https://openrouter.ai/api/v1"
OR_PROXY="${OR_PROXY_URL:-http://127.0.0.1:$(cat "$HARNESS_DIR/.proxy_port" 2>/dev/null || echo 8765)/api/v1}"
orpre() {
  PRE=(exec
    -c model_providers.openrouter.name="OpenRouter"
    -c "model_providers.openrouter.base_url=$1"
    -c model_providers.openrouter.env_key="OPENROUTER_API_KEY"
    -c model_providers.openrouter.wire_api="responses"
    -c model_providers.openrouter.stream_idle_timeout_ms=7200000
    -c model_providers.openrouter.stream_max_retries=5
    -c model_providers.openrouter.request_max_retries=4
    -c model_provider="openrouter"
    -c "model_catalog_json=$CATALOG"
    -m "$2" -c "model_reasoning_effort=$3")
}
case "$MK" in
  fugu-ultra)
    BIN=codex-fugu
    ENVPREFIX=(env CODEX_FUGU_NO_UPDATE=1)
    PRE=(exec -m fugu-ultra -c model_reasoning_effort=xhigh)
    ;;
  gpt55)   orpre "$OR_DIRECT" "openai/gpt-5.5" "xhigh" ;;
  opus48)  orpre "$OR_PROXY"  "anthropic/claude-opus-4.8" "xhigh" ;;
  gemini)  orpre "$OR_DIRECT" "google/gemini-3.1-pro-preview" "high" ;;
  *) echo "unknown model_key: $MK" >&2; exit 2 ;;
esac

START=$(date +%s)
printf '{"model_key":"%s","workdir":"%s","started":%s}\n' "$MK" "$WD" "$START" > "$WD/meta.json"
"${ENVPREFIX[@]}" timeout "$TO" "$BIN" "${PRE[@]}" "${COMMON[@]}" "$PROMPT" \
   > "$WD/events.jsonl" 2> "$WD/run.err"
RC=$?
END=$(date +%s)
printf '{"model_key":"%s","workdir":"%s","started":%s,"ended":%s,"elapsed_s":%s,"rc":%s}\n' \
   "$MK" "$WD" "$START" "$END" "$((END-START))" "$RC" > "$WD/meta.json"
echo "[$MK] rc=$RC elapsed=$((END-START))s -> $WD"
exit $RC
