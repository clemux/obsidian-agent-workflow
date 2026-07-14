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
| `record` | `PostToolUse` on `Bash` | Reads the task ID from the command and the resulting status from the command's output; writes the title for this session under `$OAW_SESSION_TITLE_STATE_DIR` (default `~/.claude/state/oaw-session-title`). |
| `emit` | `UserPromptSubmit` | Sets the recorded title, unless it already matches the session's current one. |

The status comes from the command's *output*, never from the command text. A lifecycle write that
OAW refuses — `complete` blocked while another session's run is still open — prints no `Status:`
line, so it cannot retitle anything. The mapping follows the task lifecycle:

| `oaw` result | Title |
| --- | --- |
| `Status: active` | `[I] TASK-ID` |
| `Status: review` | `[R] TASK-ID` |
| `Status: done` | `[DONE] TASK-ID` |

`record` runs on every Bash tool call, so it bails on raw stdin before it pays for `jq`: the no-op
path costs about 2 ms, against 10 ms for an unconditional `jq` and 17 ms for a Python equivalent.
That is why this is the repository's only shell script.

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
