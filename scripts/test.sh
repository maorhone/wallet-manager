#!/usr/bin/env bash
set -euo pipefail; cd "$(dirname "$0")/.."
[ -d .venv ] || uv sync --quiet; uv sync --quiet --frozen
uv run ruff check . && uv run ruff format --check .
uv run pytest -q -x
