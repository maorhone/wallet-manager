#!/usr/bin/env bash
set -euo pipefail; cd "$(dirname "$0")/.."
[ -d .venv ] || uv sync --quiet; uv sync --quiet --frozen
uv run ruff check . && uv run ruff format --check .
uv run pytest -q -x
[ -d node_modules ] || npm ci --silent
npm run -s lint && npm run -s typecheck && npm run -s test -- --run
npx playwright install chromium >/dev/null
npm run -s e2e
