#!/usr/bin/env bash
set -uo pipefail
in=$(cat)
fp=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // ""' <<<"$in")
[ -n "$fp" ] && [ -f "$fp" ] || exit 0
cd "${CLAUDE_PROJECT_DIR:-$PWD}" || exit 0
bin=node_modules/.bin; msg=""
case "$fp" in
  *.ts|*.tsx|*.js|*.jsx|*.mjs|*.cjs|*.json|*.css|*.scss|*.html|*.md|*.yml|*.yaml)
    [ -x $bin/prettier ] && timeout 30 $bin/prettier --write --log-level warn "$fp" >/dev/null 2>&1
    case "$fp" in *.ts|*.tsx|*.js|*.jsx|*.mjs|*.cjs)
      [ -x $bin/eslint ] && { msg=$(timeout 60 $bin/eslint --fix --format unix "$fp" 2>&1) || true; } ;;
    esac ;;
  *.py)
    R=$(command -v ruff || { [ -x .venv/bin/ruff ] && echo .venv/bin/ruff; } || true)
    if [ -n "$R" ]; then "$R" format -q "$fp" >/dev/null 2>&1; msg=$("$R" check --fix -q "$fp" 2>&1) || true; fi ;;
esac
if [ -n "$msg" ]; then { echo "Lint errors remain in $fp - fix them before moving on:"; head -n 40 <<<"$msg"; } >&2; exit 2; fi
exit 0
