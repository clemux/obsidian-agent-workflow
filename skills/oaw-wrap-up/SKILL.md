---
name: oaw-wrap-up
description: Operational end-of-session closure for OAW-managed work. Use whenever the user signals stopping, urgently ("I need to sleep", "gotta go", "stopping now", "calling it a night") or normally ("let's stop here", "wrap up", "are we done?", "done for today"), asks what state the session would leave behind, or explicitly invokes wrap-up — even mid-task, and even when they never mention OAW or wrap-up by name. Inventories this session's agent runs and task worktrees, classifies each as complete, ready for review, or unfinished, writes lifecycle state and resume instructions, and ends with a closure receipt plus an optional retro offer. Not for closing out a single finished task mid-session (use the oaw lifecycle commands directly); reflection belongs to the retro skill, offered afterwards.
---

# OAW wrap-up — session closure

## Purpose and boundaries

Fast operational closure that guarantees no OAW run or repository work is left ambiguous
when a session ends, especially under time pressure. It writes lifecycle state and resume
instructions through the `oaw` CLI. Durability lives in the task notes and run records; the
receipt is a chat message, not a separate note.

Always out of scope:

- Session reflection — the `retro` skill's job. Wrap-up ends by *offering* retro and is
  complete even when the user declines.
- Worktree cleanup — `oaw-task-execution` step 6's job; it requires integration proof
  there is no time to establish during wrap-up.
- Any git mutation except the single explicitly accepted WIP commit. Never push, fetch,
  stash, reset, or delete anything.

When the same stop phrase triggers both this skill and `retro`, precedence resolves the
overlap: run operational closure first, offer retro after. Do not edit the shared retro
trigger to avoid the collision.

Load the main `oaw` skill for command contracts if it is not already loaded. Set
`OAW_VAULT` before any vault command.

## Modes

Select intensity from the trigger language:

- **Urgent** — "I need to sleep", "gotta go", "stopping now", or an explicitly urgent
  invocation. Ask the user at most one question in total (the WIP-commit offer); take safe
  defaults everywhere else and deliver the receipt within minutes.
- **Standard** — "let's stop here", "are we done?", "wrap up". Same inventory;
  classification ambiguities and stale-run reconciliation may be discussed; may offer
  `oaw session snapshot --partial` for retro preservation (read the oaw skill's
  `references/session-artifacts.md` first).

## Workflow

### 1. Inventory (read-only; parallelize freely)

- Current harness session ID from the environment (`CLAUDE_CODE_SESSION_ID`,
  `CODEX_THREAD_ID`, ...).
- `oaw run list --state running --json` for all running runs and
  `oaw run list --state running --current-session --json` for this session's; runs in the
  first set but not the second belong to other sessions. Note `stale` flags.
- `git gtr list`, then per-worktree `git -C <path> status --short --branch` plus branch and
  base; main checkout status; unpushed-commit counts against local upstream refs (no fetch).
- Never infer worktree state from the main checkout or from a worktree listing.

### 2. Classify every open task/run

Exactly three buckets:

- **Complete** — only with evidence of merge into the intended main *and* post-merge
  validation run this session → `oaw task complete <ID> --note "..." --checks "<checks actually run>"`.
- **Ready for review** — implementation done, checks green, not merged →
  `oaw task review <ID> --note "..." --checks "<checks actually run>"`.
- **Unfinished** — everything else, and the default under any uncertainty →
  `oaw task pause <ID> --note "<resume block>"`.

### 3. Resume block

Include in the pause or review note: worktree path, branch, base revision, dirty and
untracked summary, checks already run, unpushed count, and one exact next-session command:

```
cd <repo> && claude "Resume obs:<task-id>. Read the task note resume block."
cd <repo> && codex "Resume obs:<task-id>. Read the task note resume block."
```

Pick the command matching the provider expected to resume; include one, not both. This
reuses the pause discipline of `oaw-task-execution` step 5 by reference; do not restate or
vary it.

### 4. WIP-commit offer

If tracked changes exist in a task worktree, ask exactly one yes/no question: commit them
as `wip: <short description>` on that worktree's feature branch. Urgent mode asks at most
once in total; standard mode may ask per worktree. Declined or unanswered means
record-only. No other git mutation is ever permitted in wrap-up.

### 5. Other-session and stale runs

- Urgent mode never touches them; each goes into the receipt as a named reconciliation
  item.
- Standard mode may propose `oaw run close <RUN-ID> --reason "..."` per run, each requiring
  explicit user confirmation. Never auto-close: a run held by a possibly-live session is
  always surfaced, never closed.

### 6. Orphan work

Substantive session work with no owning task → create or promote its source capture into a
task before closure (`oaw task create ...` / `oaw task create --from-capture ...`), so the
receipt has no unowned lines.

### 7. Closure receipt

Final chat message — never a separate note; durability already lives in the task notes and
run records. Follow this shape so receipts stay scannable across sessions, omitting empty
lines rather than writing "none":

```
Closure receipt
- obs:<task-id> → review (checks: <checks actually run>)
- obs:<task-id> → paused (resume block on the task note)
- Worktrees: <name> dirty — WIP commit declined; <name> clean
- Unpushed: <branch> ahead <n> of <upstream> (local only)
- Reconcile next session: <RUN-ID> — running, other session, stale
- Resume: cd <repo> && claude "Resume obs:<task-id>. Read the task note resume block."
```

### 8. Offer retro

Offer a retrospective as an optional deeper follow-up (load `retro` together with
`oaw-retro-backend`). Wrap-up is complete even when the offer is declined.

## Safety rules

- Never fabricate `--checks`; name only verification actually run this session.
- Uncertain classification → pause. Completion always requires merge plus post-merge
  validation evidence; "tests passed in the worktree" is at most ready-for-review.
- Missing or unresolvable session ID → run-creating lifecycle writes are impossible by
  design. Degrade to a chat receipt plus a capture recording the closure state, and say so
  plainly.
- Blocked tasks: surface the CLI's blocker output; never remove a relationship to make a
  lifecycle command pass.
- Re-running wrap-up is idempotent: it refreshes notes and receipts and never duplicates
  runs (`task start` idempotency and `task note` refresh semantics guarantee this).
- Anything ambiguous — unexpected branch, detached HEAD, unknown repository identity — is
  reported as a reconciliation item, not resolved by guessing.
