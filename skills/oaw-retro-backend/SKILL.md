---
name: oaw-retro-backend
description: OAW retro backend adapter for the shared `retro` skill. Use whenever the `retro` skill (or any end-of-session retrospective) needs to persist its outputs in this environment — converting confirmed follow-ups into owned vault tasks or captures, writing the draft retrospective note under `Agents/Retrospectives/`, linking the retro to the work it spawned, and verifying the note exists. Always load this together with `retro` when running a retrospective in an OAW-managed environment.
---

# oaw-retro-backend

Backend adapter that plugs the OAW vault into the shared `retro` skill. The `retro` skill owns the
retrospective *workflow* (evidence gathering, agenda, one-item-at-a-time discussion); this adapter
owns where its durable artifacts *go*. Use these command mappings for the workflow's step 4
(convert follow-ups into owned work) and step 5 (write the retrospective note).

## Step 4 mappings: give follow-ups a home

- **Actionable work with a clear owner** → `oaw task create --project <alias> --title "..."`
  (`--status todo` if it's genuinely next-up; default `backlog` otherwise).
- **An idea, lead, or "maybe later"** → a capture, via the `obsidian-capture` skill.
- **A gap in the agent tooling itself** → a task in the relevant tooling project, not a vague note.

Refer to created items by their `obs:` IDs from then on — in the discussion, in the retro note, and
in the fresh-session handoff:

```bash
cd <repo> && claude "Work on obs:XXX-TSK-thing. Resolve it with the oaw skill, read the task note, and implement it."
cd <repo> && codex "Work on obs:XXX-TSK-thing. Resolve it with the oaw skill, read the task note, and implement it."
```

## Step 5 mappings: write and verify the retro note

Create the draft:

```bash
oaw retro create --title "<short descriptive title>" --summary "<one paragraph: what this session was>"
```

This writes `Agents/Retrospectives/<date> <title>.md` with frontmatter (`type`, `status: draft`,
`provider`, `session-ids`, `id`, `aliases`) and a `## Summary` section. It resolves the session ID
from the harness environment itself — do not pass one, and do not use
`--allow-missing-session-id` unless the user explicitly accepts an untraceable note.

Then append each section as a dated block:

```bash
oaw note observe <RETRO-ID> --section Observations --title "What worked"          --body "- ..."
oaw note observe <RETRO-ID> --section Observations --title "Friction and lessons" --body "- ..."
oaw note observe <RETRO-ID> --section Decisions    --title "<what was decided>"   --body "- ..."
oaw note observe <RETRO-ID> --section Follow-ups   --title "Owned work"           --body "- obs:XXX-TSK-... — ..."
oaw note observe <RETRO-ID> --section Artifacts    --title "Durable outputs"      --body "- obs:... — ..."
```

Reference follow-ups and artifacts by `obs:` ID so they resolve later. Link the retro to the tasks
it spawned with `oaw link ensure-bidirectional ... --write`.

**Verify:** `oaw resolve --meta <RETRO-ID>` to confirm the note exists with `session-ids`
populated, and confirm it appears in `Agents/Retrospectives.base#Recent`.

Report back with the retro's `obs:` ID and the IDs of everything it spawned.

## Session-ID and usage helpers

`oaw` reads the harness session ID itself (`CLAUDE_CODE_SESSION_ID` / `CLAUDE_SESSION_ID` in
Claude Code, `CODEX_THREAD_ID` in Codex) — no need to pass one. For Codex sessions,
`oaw session lookup <thread-id> --verbose` reports timestamps, duration, turn count, and cumulative
token usage from `total_token_usage` — use it instead of hand-parsing the rollout during the
evidence-gathering step.

## Related

- `obs:AGT-TSK-retro-skill` — the task this adapter (and the shared core) implements; record new
  harness mappings there.
- `obs:AGT-TSK-session-retrospectives` — the habit this supports.
- The `oaw` skill — task lifecycle, note intake, retro creation, session snapshots.
- The `obsidian-capture` skill — for parking ideas that aren't yet actionable work.
- The shared `retro` skill — the harness-independent retrospective workflow this adapter backs.
