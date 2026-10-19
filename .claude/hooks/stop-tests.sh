#!/usr/bin/env bash
set -uo pipefail
in=$(cat)
proj="${CLAUDE_PROJECT_DIR:-$PWD}"; cd "$proj" || exit 0
state="$proj/.claude/state"; mkdir -p "$state"
[ "$(jq -r '.permission_mode // ""' <<<"$in")" = plan ] && exit 0
if [ -x ./scripts/test.sh ]; then tcmd=./scripts/test.sh
elif [ -f package.json ] && jq -e '.scripts.test' package.json >/dev/null 2>&1; then tcmd="npm test --silent"
elif [ -f pyproject.toml ] || [ -d tests ]; then tcmd="python3 -m pytest -q -x"
else exit 0; fi
fp=$(git status --porcelain 2>/dev/null | sha1sum | cut -c1-12)$(git rev-parse --short HEAD 2>/dev/null)
[ -f "$state/last-green" ] && [ "$(cat "$state/last-green")" = "$fp" ] && exit 0
out=$(timeout 540 bash -c "$tcmd" </dev/null 2>&1); rc=$?
if [ $rc -eq 0 ]; then echo "$fp" > "$state/last-green"; rm -f "$state/stop-blocks"; exit 0; fi
n=$(( $(cat "$state/stop-blocks" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$state/stop-blocks"
if [ "$n" -gt 3 ]; then { echo "tests still failing after 3 blocked stops (exit $rc)"; tail -n 40 <<<"$out"; } > "$state/TESTS_FAILING"; exit 0; fi
jq -n --arg r "Tests are failing (\`$tcmd\` exit $rc). Fix the cause - do not skip, delete or weaken tests - then finish. Last output:
$(tail -n 60 <<<"$out")" '{decision:"block", reason:$r}'
exit 0
