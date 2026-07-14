#!/usr/bin/env bash
# Set the Claude Code session title from OAW task lifecycle commands.
#
# Claude Code exposes no agent-callable rename, so the OAW skill tells the agent to skip title
# synchronization silently. This hook does it out of band instead: it watches the `oaw task ...`
# commands the agent already runs and derives the title from each command and its result. The
# agent needs no knowledge of this hook, and the skill needs no Claude-specific policy.
#
#   record   PostToolUse[Bash]    stdin: hook JSON  -> records the desired title for the session
#   emit     UserPromptSubmit     stdin: hook JSON  -> sets it, when it differs from the current one
#
# `record` runs on every Bash tool call, so it bails on raw stdin before paying for jq. `emit` runs
# once per user prompt. UserPromptSubmit is the only recurring event carrying
# hookSpecificOutput.sessionTitle, so a new title lands on the next user prompt, not immediately.
#
# See docs/claude-code.md. Markers and the "a title is a navigation aid, never task state" rule
# come from skills/oaw/SKILL.md.
set -euo pipefail

STATE_DIR="${OAW_SESSION_TITLE_STATE_DIR:-${HOME}/.claude/state/oaw-session-title}"

record() {
	local input session_id command status task_id marker
	input=$(cat)

	# Hot path: every Bash call reaches this line, almost none are OAW lifecycle commands.
	case "$input" in
	*'oaw task '*) ;;
	*) return 0 ;;
	esac

	session_id=$(jq -r '.session_id // empty' <<<"$input")
	command=$(jq -r '.tool_input.command // empty' <<<"$input")
	[ -n "$session_id" ] && [ -n "$command" ] || return 0

	# The task ID comes from the command, but the status comes from the command's output, so a
	# refused lifecycle write (`complete` blocked by another running run) never retitles anything.
	task_id=$(grep -oE '\boaw[[:space:]]+task[[:space:]]+[a-z]+[[:space:]]+[A-Z][A-Z0-9]*-[A-Za-z0-9-]+' <<<"$command" |
		tail -n1 | grep -oE '[A-Z][A-Z0-9]*-[A-Za-z0-9-]+$' || true)
	[ -n "$task_id" ] || return 0

	status=$(jq -r '.tool_response.stdout // empty' <<<"$input" |
		grep -oE '^Status: [a-z]+' | tail -n1 | cut -d' ' -f2 || true)

	case "$status" in
	active) marker='[I]' ;;
	review) marker='[R]' ;;
	done) marker='[DONE]' ;;
	*) return 0 ;;
	esac

	mkdir -p "$STATE_DIR"
	printf '%s %s\n' "$marker" "$task_id" >"${STATE_DIR}/${session_id}"
}

emit() {
	local input session_id current desired
	input=$(cat)

	session_id=$(jq -r '.session_id // empty' <<<"$input")
	[ -n "$session_id" ] || return 0

	desired=$(cat "${STATE_DIR}/${session_id}" 2>/dev/null || true)
	[ -n "$desired" ] || return 0

	# Never emit a no-op, and never fight a title the user set explicitly.
	current=$(jq -r '.session_title // empty' <<<"$input")
	[ "$desired" != "$current" ] || return 0

	jq -nc --arg t "$desired" \
		'{hookSpecificOutput: {hookEventName: "UserPromptSubmit", sessionTitle: $t}}'
}

case "${1:-}" in
record) record ;;
emit) emit ;;
*)
	echo "usage: ${0##*/} {record|emit}   (reads Claude Code hook JSON on stdin)" >&2
	exit 2
	;;
esac
