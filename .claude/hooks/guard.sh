#!/usr/bin/env bash
set -uo pipefail
in=$(cat) || { echo "guard: cannot read input" >&2; exit 2; }
deny() { echo "BLOCKED by builder guard: $1" >&2; exit 2; }
tool=$(jq -r '.tool_name // ""' <<<"$in" 2>/dev/null) || deny "unparseable hook input"
proj=$(realpath -m -- "${CLAUDE_PROJECT_DIR:-$PWD}")
sensitive='^(/workspace|/home|/root|/var|/sys|/proc/[0-9]|/etc|/srv/builder/(\.claude|\.config|bin|\.npm-global|projects/[^/]+))(/|$)'
check_path() {
  local p; p=$(realpath -m -- "${1/#\~/$HOME}")
  case "$p" in "$proj"|"$proj"/*) return 0;; esac
  if [[ "$p" =~ $sensitive ]]; then deny "path outside the project: $p"; fi
}
case "$tool" in
  Bash)
    cmd=$(jq -r '.tool_input.command // ""' <<<"$in")
    grep -qE 'ANTHROPIC_(API_KEY|AUTH_TOKEN|BASE_URL)' <<<"$cmd" && deny "ANTHROPIC_* credentials are forbidden; Pro subscription only"
    grep -qiE 'tailscale([[:space:]]+[^[:space:]]+)*[[:space:]]+funnel' <<<"$cmd" && deny "tailscale funnel is forbidden"
    grep -qE '(^|[;&|(`[:space:]])(sudo|su|tailscale|cloudflared|ngrok)([[:space:]]|$)|tunnel\.sh' <<<"$cmd" && deny "sudo/tunnels are run by the builder bot only"
    grep -qE 'gh[[:space:]]+repo[[:space:]]+(delete|archive|rename)|--visibility[[:space:]=]+public' <<<"$cmd" && deny "repo delete/rename/public is not allowed"
    grep -qE 'git[[:space:]]+push([^;&|]*)(--force|[[:space:]]-f([[:space:]]|$)|--mirror)' <<<"$cmd" && deny "force push is not allowed"
    stripped=$(sed -E 's#[A-Za-z][A-Za-z0-9+.-]*://[^[:space:]]*##g' <<<"$cmd")
    while IFS= read -r p; do [ -n "$p" ] && check_path "$p"; done < <(grep -oE '(~|/)[A-Za-z0-9._/-]*' <<<"$stripped" | grep -E '^(~|/[A-Za-z])')
    ;;
  Read|Edit|Write|MultiEdit|NotebookEdit|Glob|Grep)
    fp=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // .tool_input.path // ""' <<<"$in")
    [ -n "$fp" ] && check_path "$fp"
    if [ "$tool" != Read ] && [ "$tool" != Glob ] && [ "$tool" != Grep ]; then
      real=$(realpath -m -- "$fp")
      case "$real" in "$proj"/.claude/hooks/*|"$proj"/.claude/settings.json) deny "guardrail files are managed by the builder bot";; esac
      jq -r '[.tool_input.content, .tool_input.new_string, (.tool_input.edits // [] | .[].new_string)] | map(select(.)) | join("\n")' <<<"$in" | grep -q 'ANTHROPIC_API_KEY' && deny "writing ANTHROPIC_API_KEY into files is forbidden"
    fi ;;
esac
exit 0
