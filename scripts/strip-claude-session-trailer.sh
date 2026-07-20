#!/bin/sh
# Strip Claude Code "Claude-Session:" commit trailers before they are recorded.
#
# Claude Code can append a `Claude-Session: https://claude.ai/code/session_...`
# trailer to commits it makes. Those URLs are owner-only, but they are still
# session identifiers that do not belong in shared git history. This hook removes
# them at commit time so they never enter a commit object in the first place.
#
# Runs as a git `prepare-commit-msg` hook: arg 1 is the path to the commit
# message file. The pre-commit / prek framework passes the same path, so this
# script works whether invoked directly by git or through prek.
set -eu

msg_file="${1:-}"
[ -n "$msg_file" ] && [ -f "$msg_file" ] || exit 0

tmp="$(mktemp "${TMPDIR:-/tmp}/oaw-msg.XXXXXX")"
# grep -v exits 1 when it selects no lines. A real commit always keeps at least
# its subject line, so only a grep error (exit >= 2) is a genuine failure; in
# that case leave the original message untouched rather than risk clobbering it.
if grep -v '^[[:space:]]*Claude-Session:' "$msg_file" >"$tmp" || [ $? -eq 1 ]; then
    mv "$tmp" "$msg_file"
else
    rm -f "$tmp"
fi
