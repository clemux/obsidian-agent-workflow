# Claude Code session titles

Status: shipped — `scripts/claude-session-title-hook.sh`, registered by `scripts/claude-setup.sh`.

OAW keeps the session that owns a task recognizable as `[MARKER] CANONICAL-TASK-ID`, so a sidebar
full of sessions says which task each one is working. The skill treats this as capability-gated: a
client synchronizes the title only when it exposes an agent-callable rename, and otherwise skips
silently. Codex Desktop exposes one. Claude Code does not — its `/rename` is typed by the user,
`claude --name` is a launcher flag, and no tool reaches the running agent. Left there, the
convention would simply be dead in Claude Code.

A hook closes the gap without the agent participating at all. Claude Code lets `UserPromptSubmit`
and `SessionStart` hooks return `hookSpecificOutput.sessionTitle`, which retitles the live session
and persists for the resume picker. The OAW hook watches the `oaw task` commands an agent already
runs and derives the title from them, so nothing in the skill changes and no agent needs to know
this exists.

## What it does

Two modes, both reading Claude Code's hook JSON on stdin:

| Mode | Hook event | Behavior |
| --- | --- | --- |
| `record` | `PostToolUse` on `Bash` | Takes the task ID from the command, asks `oaw resolve --meta` for that task's status, and writes the title for this session under `$OAW_SESSION_TITLE_STATE_DIR` (default `~/.claude/state/oaw-session-title`). |
| `emit` | `UserPromptSubmit` | Sets the recorded title, unless it already matches the session's current one. |

Everything the hook sees is chosen by the agent. The command text is agent-written, and so is the
output, because the agent chose the command that produced it — a command that merely prints
`Status: done` is indistinguishable from one that earned it, and a command touching two tasks cannot
be attributed by text alone. So the hook takes only the *task ID* from the command and asks OAW for
that task's real status:

| Task status in the vault | Title |
| --- | --- |
| `active` | `[I] TASK-ID` |
| `review` | `[R] TASK-ID` |
| `done` | `[DONE] TASK-ID` |

Any other status — `todo`, `backlog`, a task that does not resolve — sets no title.

A refused lifecycle write therefore cannot mislead the hook. When `complete` is blocked because
another session's run is still open, the task stays `active` and the title stays `[I]`: not because
the hook detected the failure, but because it never believed the command in the first place. The
earlier design read the status out of the command's stdout and could be lied to by stale output; the
tests in `tests/test_claude_session_title_hook.py` keep that door shut.

`record` runs on every Bash tool call, so it bails on raw stdin before it pays for `jq`: the no-op
path costs about 2 ms, against 10 ms for an unconditional `jq` and 17 ms for a Python equivalent.
That is why this is the repository's only shell script. Only a command that actually mentions a task
reaches `oaw resolve`, which adds roughly 250 ms to that rare call and nothing to the common one.

## Install

```bash
./scripts/claude-setup.sh            # show what would change in ~/.claude/settings.json
./scripts/claude-setup.sh --install  # back it up, then register both hook entries
```

Re-running `--install` is a no-op, and the script refuses to touch a settings file it cannot parse.
`CLAUDE_SETTINGS` overrides the target.

The entries must live in user settings (`~/.claude/settings.json`), not in a project's
`.claude/settings.json`: project hooks only fire inside their own directory tree, and `oaw task`
commands get run from whatever repository the agent is working in. Claude Code has no include
mechanism, so the registration points at an absolute path into this checkout — move the checkout and
you must re-run the setup script. To register by hand instead:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "/path/to/obsidian-agent-workflow/scripts/claude-session-title-hook.sh record", "timeout": 5}]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [{"type": "command", "command": "/path/to/obsidian-agent-workflow/scripts/claude-session-title-hook.sh emit", "timeout": 5}]
      }
    ]
  }
}
```

## Limits

Claude Code reads hook definitions when a session starts, so a fresh registration takes effect in
the next session, not the current one.

A title lands on the next user prompt rather than the moment the task changes state, because
`UserPromptSubmit` is the only recurring event that carries `sessionTitle`.

Only `[I]`, `[R]`, and `[DONE]` are set. `[DESIGN]` and `[W]` mark phases that no `oaw` command
marks, so the hook cannot infer them and does not guess.

## Why not a plugin

A Claude Code plugin is the supported way to distribute hooks that should work across projects, and
this hook would qualify. It is deferred deliberately, not overlooked: a plugin adds a marketplace and
a manifest to maintain for a hook whose only user is this repository's own author. Converting later
is cheap — the `hooks` block moves into `hooks/hooks.json` beside a `.claude-plugin/plugin.json`, and
the script itself is unchanged.
