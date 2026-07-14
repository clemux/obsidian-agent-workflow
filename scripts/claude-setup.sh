#!/usr/bin/env bash
# Register the OAW session-title hook in Claude Code's user settings.
#
# The hook must fire in every project, because `oaw task ...` commands are run from whatever repo
# the agent happens to be working in. A committed project `.claude/settings.json` only applies
# inside its own directory tree, so user settings are the only scope that works. Claude Code has no
# include mechanism, so the entries point at an absolute path into this checkout.
#
#   ./scripts/claude-setup.sh              show what would change, write nothing
#   ./scripts/claude-setup.sh --install    back up settings.json, then apply
#
# Re-running --install is a no-op. Set CLAUDE_SETTINGS to target a different settings file.
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
HOOK="${SCRIPT_DIR}/claude-session-title-hook.sh"
SETTINGS="${CLAUDE_SETTINGS:-${HOME}/.claude/settings.json}"

install=false
case "${1:-}" in
--install) install=true ;;
"" | --dry-run) ;;
*)
	echo "usage: ${0##*/} [--dry-run|--install]" >&2
	exit 2
	;;
esac

[ -x "$HOOK" ] || {
	echo "error: hook not found or not executable: $HOOK" >&2
	exit 1
}

if [ ! -f "$SETTINGS" ]; then
	echo "error: no settings file at $SETTINGS" >&2
	echo "hint: start Claude Code once, or set CLAUDE_SETTINGS" >&2
	exit 1
fi

# Never touch a settings file we cannot parse: a broken global config breaks every session.
jq empty "$SETTINGS" 2>/dev/null || {
	echo "error: $SETTINGS is not valid JSON; refusing to modify it" >&2
	exit 1
}

# Add each entry only when absent, so re-running changes nothing. PostToolUse joins the existing
# Bash matcher block when there is one, rather than adding a second block for the same matcher.
# Claude Code runs the command through a shell, so a checkout path containing a space would split
# into two arguments; quote it. Deduplicate on the hook's path rather than the whole command string,
# so an entry written by an older version of this script is recognized instead of duplicated.
patched=$(jq --arg path "$HOOK" --arg hook "'${HOOK//\'/\'\\\'\'}'" '
  def record_entry: {type: "command", command: ($hook + " record"), timeout: 5};
  def emit_entry:   {type: "command", command: ($hook + " emit"),   timeout: 5};
  def has_cmd($mode): any(.hooks[]?; (.command? // "") | contains($path) and endswith($mode));

  .hooks //= {}
  | .hooks.PostToolUse //= []
  | .hooks.UserPromptSubmit //= []

  | if any(.hooks.PostToolUse[]?; .matcher == "Bash")
    then .hooks.PostToolUse |= map(
      if .matcher == "Bash" and (has_cmd("record") | not)
      then .hooks += [record_entry] else . end)
    else .hooks.PostToolUse += [{matcher: "Bash", hooks: [record_entry]}]
    end

  | if any(.hooks.UserPromptSubmit[]?; has_cmd("emit"))
    then .
    elif (.hooks.UserPromptSubmit | length) > 0
    then .hooks.UserPromptSubmit[0].hooks += [emit_entry]
    else .hooks.UserPromptSubmit += [{hooks: [emit_entry]}]
    end
' "$SETTINGS")

if [ "$patched" = "$(jq . "$SETTINGS")" ]; then
	echo "already registered in $SETTINGS; nothing to do"
	exit 0
fi

if ! $install; then
	echo "would change $SETTINGS:"
	diff <(jq -S . "$SETTINGS") <(jq -S . <<<"$patched") || true
	echo
	echo "re-run with --install to apply"
	exit 0
fi

cp -- "$SETTINGS" "${SETTINGS}.bak"
# Write through a temporary file: a crash midway through a direct redirect would leave every future
# session reading a truncated global config.
printf '%s\n' "$patched" >"${SETTINGS}.tmp"
mv -- "${SETTINGS}.tmp" "$SETTINGS"
echo "registered the OAW session-title hook in $SETTINGS (backup: ${SETTINGS}.bak)"
echo "hooks are read at session start, so this takes effect in your next Claude Code session"
