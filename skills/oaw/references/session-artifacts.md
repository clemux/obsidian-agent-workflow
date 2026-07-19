# Session artifact snapshots and lookup details

Read this before running `oaw session snapshot`, or when you need the exact
`session lookup --verbose` metric semantics.

## Session artifact snapshots

When a session should be preserved for retrospectives, snapshot its transient harness artifacts into the vault attachments folder:

```bash
oaw session snapshot 00000000-0000-4000-8000-000000000001 \
  --slug example-session \
  --partial \
  --codex-thread 00000000-0000-4000-8000-000000000002 \
  --codex-thread 00000000-0000-4000-8000-000000000003 \
  --claude-session 00000000-0000-4000-8000-000000000004

# Codex-only session (no Claude parent transcript)
oaw session snapshot "$CODEX_THREAD_ID" --codex-only --partial --slug example-codex-session
```

- The command writes to `Agents/Retrospectives/attachments/<date>-<slug>/` by default.
- By default it copies the Claude parent transcript, nested Claude subagent transcripts, task outputs under `tasks/`, workflow run artifacts under `subagents/workflows/`, persisted workflow scripts under `workflows/scripts/`, discoverable Codex rollouts, referenced plugin job logs, and fork parents referenced by explicit Claude/fork markers or `--claude-session`. Use `--codex-only` when no Claude parent exists; the positional Codex thread's own rollout is always required.
- Fork parents are auto-discovered from `CLAUDE_SESSION_ID`, `claude-session`, `fork ... session`, and `btw-session` references in copied artifacts. Use `--claude-session <id>` for parents those artifacts do not reference clearly; repeat it for multiple fork parents.
- Codex rollouts are discovered by referenced thread IDs or explicit `--codex-thread <id>` flags across the default active and archived roots. Parent and child rollouts may live in different roots. Active wins duplicate rollout filenames. Use `--codex-rollout <filename-or-path>` for an exact rollout. Use `--grep <literal>` only when the literal identifies one rollout; ambiguous grep matches fail and should be replaced with explicit `--codex-thread` or `--codex-rollout` flags.
- It writes `manifest.json` with each source path, destination path, copy time, size, hash, category, mode, and completeness. Use the manifest instead of hand-writing provenance.
- Use `--partial` while the session is still live. Re-run the same command later to refresh the transcript, preserve nested artifacts, pick up new artifacts, and remove stale files listed in the previous manifest.
- Use `--complete` to override current-session detection, `--date` to override the folder date, and `--output-root`, `--claude-root`, `--codex-root`, or `--plugin-data-root` for controlled test/demo locations. An explicit `--codex-root` is one complete override root; it does not add a sibling archive automatically.
- For real vault snapshots, use the installed `oaw session snapshot ...` command. Reserve `uv run python bin/oaw session snapshot ...` for repo-development checks, temp-vault fixtures, or deliberately testing the checkout copy.

## Session lookup `--verbose` metrics

`--verbose` adds best-effort per-artifact metadata. For Codex rollout JSONL, timestamps
are the earliest and latest valid record timestamps, duration is their elapsed wall-clock
interval, turns count message records by `user` and `assistant` role (including injected
instructions stored as user messages), and tokens come from the latest cumulative
`total_token_usage` snapshot. Missing metrics and unsupported harness formats are shown
as `unavailable`; default output remains unchanged.
