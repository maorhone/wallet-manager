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
is_guardrail() {
  case "$1" in "$proj"/.claude/hooks|"$proj"/.claude/hooks/*|"$proj"/.claude/settings.json|"$proj"/.claude/settings.local.json|"$proj"/.claude/settings*.json) return 0;; esac
  return 1
}
case "$tool" in
  Bash)
    cmd=$(jq -r '.tool_input.command // ""' <<<"$in")
    grep -qE 'ANTHROPIC_(API_KEY|AUTH_TOKEN|BASE_URL)' <<<"$cmd" && deny "ANTHROPIC_* credentials are forbidden; Pro subscription only"
    grep -qiE 'tailscale([[:space:]]+[^[:space:]]+)*[[:space:]]+funnel' <<<"$cmd" && deny "tailscale funnel is forbidden"
    grep -qE '(^|[;&|(`[:space:]])(sudo|su|tailscale|cloudflared|ngrok)([[:space:]]|$)|tunnel\.sh' <<<"$cmd" && deny "sudo/tunnels are run by the builder bot only"
    grep -qE 'gh[[:space:]]+repo[[:space:]]+(delete|archive|rename|sync)|--visibility[[:space:]=]+public' <<<"$cmd" && deny "repo delete/rename/sync/public is not allowed"
    grep -qE '(^|[^A-Za-z0-9_-])git([[:space:]]+(-[Cc][[:space:]]+[^[:space:]]+|--?[A-Za-z][^[:space:]]*))*[[:space:]]+push([[:space:]]|$)' <<<"$cmd" && deny "git push is done by the builder bot after it runs scripts/test.sh itself. Commit locally (git commit) and finish; do not push"
    grep -qE 'git[[:space:]]+remote[[:space:]]+(add|set-url|remove|rm|rename)' <<<"$cmd" && deny "changing git remotes is not allowed"
    grep -qE '\.claude/(hooks|settings[A-Za-z0-9._-]*\.json)' <<<"$cmd" && deny "guardrail files (.claude/hooks/*, .claude/settings*.json) are managed by the builder bot; do not read or change them from Bash"
    grep -qE 'core\.hooksPath|disableAllHooks' <<<"$cmd" && deny "changing hook configuration is not allowed"
    stripped=$(sed -E 's#[A-Za-z][A-Za-z0-9+.-]*://[^[:space:]]*##g' <<<"$cmd")
    while IFS= read -r p; do [ -n "$p" ] && check_path "$p"; done < <(grep -oE '(~|/)[A-Za-z0-9._/-]*' <<<"$stripped" | grep -E '^(~|/[A-Za-z])')
    ;;
  Read|Edit|Write|MultiEdit|NotebookEdit|Glob|Grep)
    fp=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // .tool_input.path // ""' <<<"$in")
    [ -n "$fp" ] && check_path "$fp"
    if [ "$tool" != Read ] && [ "$tool" != Glob ] && [ "$tool" != Grep ]; then
      real=$(realpath -m -- "$fp")
      is_guardrail "$real" && deny "guardrail files are managed by the builder bot"
      jq -r '[.tool_input.content, .tool_input.new_string, (.tool_input.edits // [] | .[].new_string)] | map(select(.)) | join("\n")' <<<"$in" | grep -qE 'ANTHROPIC_API_KEY|disableAllHooks' && deny "writing ANTHROPIC_API_KEY or disableAllHooks into files is forbidden"
    fi ;;
esac
exit 0
