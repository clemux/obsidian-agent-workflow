#!/usr/bin/env bash
# Set the Claude Code session title from OAW task lifecycle commands.
#
# Claude Code exposes no agent-callable rename, so the OAW skill tells the agent to skip title
# synchronization silently. This hook does it out of band instead: it notices the `oaw task ...`
# commands the agent already runs and titles the session after the task they touched. The agent
# needs no knowledge of this hook, and the skill needs no Claude-specific policy.
#
#   record   PostToolUse[Bash]    stdin: hook JSON  -> records the desired title for the session
#   emit     UserPromptSubmit     stdin: hook JSON  -> sets it, when it differs from the current one
#
# Both the command text and its output are chosen by the agent, so neither is evidence that a
# lifecycle write happened: a command that merely prints `Status: done` is indistinguishable from
# one that earned it, and a command touching two tasks cannot be attributed by text alone. `record`
# therefore takes only the task ID from the command and asks OAW for that task's real status. A
# refused `complete` leaves the task `active`, so the title stays `[I]` -- not because the hook
# detected the failure, but because it never believed the command in the first place.
#
# `record` runs on every Bash tool call, so it bails on raw stdin before paying for jq, and only
# reaches `oaw` for a command that actually mentions a task. `emit` runs once per user prompt.
# UserPromptSubmit is the only recurring event carrying hookSpecificOutput.sessionTitle, so a new
# title lands on the next user prompt rather than immediately.
#
# See docs/claude-code.md. Markers and the "a title is a navigation aid, never task state" rule
# come from skills/oaw/SKILL.md.
set -euo pipefail

STATE_DIR="${OAW_SESSION_TITLE_STATE_DIR:-${HOME}/.claude/state/oaw-session-title}"
OAW="${OAW_BIN:-oaw}"

# A session id becomes a filename, so anything outside this alphabet could escape the state dir.
safe_session_id() {
	case "$1" in
	'' | *[!A-Za-z0-9_-]*) return 1 ;;
	*) printf '%s' "$1" ;;
	esac
}

record() {
	local input session_id command task_id status marker

	input=$(cat)

	# Hot path: every Bash call reaches this line, almost none mention a task.
	case "$input" in
	*'oaw task '*) ;;
	*) return 0 ;;
	esac

	session_id=$(jq -r '.session_id // empty' <<<"$input")
	session_id=$(safe_session_id "$session_id") || return 0

	command=$(jq -r '.tool_input.command // empty' <<<"$input")
	task_id=$(grep -oE '\boaw[[:space:]]+task[[:space:]]+[a-z]+[[:space:]]+[A-Z][A-Z0-9]*-[A-Za-z0-9-]+' <<<"$command" |
		tail -n1 | grep -oE '[A-Z][A-Z0-9]*-[A-Za-z0-9-]+$' || true)
	[ -n "$task_id" ] || return 0

	# The one source of truth. Not the command, not its output.
	status=$("$OAW" resolve --meta "$task_id" 2>/dev/null |
		grep -oE '^status: [a-z]+$' | tail -n1 | cut -d' ' -f2 || true)

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
	session_id=$(safe_session_id "$session_id") || return 0

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
