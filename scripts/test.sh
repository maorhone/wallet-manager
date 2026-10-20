#!/usr/bin/env bash
set -euo pipefail; cd "$(dirname "$0")/.."
[ -d .venv ] || uv sync --quiet; uv sync --quiet --frozen
# Separate lines: under `set -e` a failure on the left of `&&` does not stop the script.
uv run ruff check .
uv run ruff format --check .
uv run pytest -q -x
[ -d node_modules ] || npm ci --silent
npm run -s lint
npm run -s typecheck
npm run -s test -- --run
npx playwright install chromium >/dev/null
# Own ports + state file so the suite never collides with a preview stack on 4100/4111/4112.
export E2E_UI_PORT="${E2E_UI_PORT:-4120}" E2E_API_PORT="${E2E_API_PORT:-4121}" E2E_FAKE_PORT="${E2E_FAKE_PORT:-4122}"
export E2E_STATE_FILE="${E2E_STATE_FILE:-$PWD/frontend/.e2e-state.test.json}"
npm run -s e2e
