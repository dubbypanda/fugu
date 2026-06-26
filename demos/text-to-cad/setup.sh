#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI was not found on PATH." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "created .env from .env.example"
fi

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  echo "created .venv"
fi

.venv/bin/python -m pip install --upgrade pip tomli pillow

pushd skills/cad >/dev/null
../../.venv/bin/python -m pip install -r requirements.txt
popd >/dev/null

PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-/tmp/fugu-ms-playwright}" \
  .venv/bin/python -m playwright install chromium

echo "installing or updating the Fugu Codex provider/profile"
curl -fsSL https://secret-test-staging.sakana.ai/fugu/install | bash

echo
echo "setup complete"
echo "Edit .env and set SAKANA_API_KEY."
echo "For OpenRouter baselines, also set OPENROUTER_API_KEY."
