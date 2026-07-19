---
name: oaw-task-execution
description: Execute implementation and review work for OAW-managed repository tasks with pre-worktree baseline checks, GTR feature isolation, parent-owned integration, and discretionary bounded subagents. Use after the main oaw skill resolves an agent-executed task that is moving from design or lifecycle management into repository edits, verification, or review. Do not use for task triage, status-only updates, or vault-only work.
---

# OAW Task Execution

Execute repository work owned by an OAW task. Keep reference resolution, task
provenance, lifecycle transitions, and task relationships in the main `oaw` skill.
Load `git-gtr-worktrees` before inspecting or creating worktrees, and honor the
repository's active `AGENTS.md` instructions throughout.

The parent agent remains accountable for scope, integration, verification, OAW
lifecycle writes, user communication, and every external or human-facing write.
Subagents may own only explicit bounded work. Single-agent execution is always valid.

## 1. Preflight the main checkout

Before creating a feature worktree, inspect the intended main checkout:

```bash
pwd
git rev-parse --show-toplevel
git branch --show-current
git status --short --branch
git gtr list
git gtr config list
```

Read `.gtrconfig` when present. Confirm the repository identity, expected primary
branch, intended base revision, tracking relationship, GTR configuration, and a clean
status including untracked files. Discover the repository-configured test, lint,
formatting, and static-typing commands from its instructions, CI, and tool config.

Treat a dirty checkout, unexpected branch or base, missing or suspicious GTR setup,
untrusted hooks, or any ambiguous state as a confirmation gate. Report the exact
condition and wait for explicit user direction before creating a worktree or editing
repository files. Do not stash, reset, fetch, repair, or silently choose another base.

After the checkout and base are confirmed, run every repository-configured baseline
check from the main checkout before worktree creation. If a check fails, is unavailable,
or has unclear coverage, report the evidence and wait for explicit user direction.
Do not weaken configuration or silently skip a check. An equivalent direct tool
executable may distinguish a broken launcher from a real check failure, but record that
workaround and rerun the affected check through the equivalent executable.

Record accepted anomalies and the baseline results in the task's start or session note.

## 2. Start and isolate the task

Once the baseline is green or the user explicitly accepts the reported condition, use
the main `oaw` skill to start the task if it is not already active.

For new feature work, create a dedicated feature branch and GTR worktree from the
confirmed base. Working directly on the primary checkout requires an explicit choice
from the user for the current session; a general preference or prior-session exception
is not enough. If GTR is missing or unusable, report the gap and wait. Do not silently
fall back to plain `git worktree` or direct edits.

Before editing, verify the worktree with `pwd`, `git branch --show-current`,
`git rev-parse --show-toplevel`, and `git status --short`. After the first small edit,
check both the main checkout and feature worktree. Stop immediately if the change landed
outside the intended worktree.

## 3. Choose implementation ownership

Decide whether delegation materially improves quality, latency, or throughput. Keep the
work in the parent when it is small, tightly coupled, context-heavy, or likely to cost
more to coordinate and verify than to implement directly. Do not delegate merely because
subagents are available.

When delegation helps:

- Assign concrete, independent work with explicit file or responsibility ownership,
  inputs, expected outputs, and completion criteria.
- Tell workers that they share the codebase, must preserve others' edits, and must adapt
  to concurrent changes.
- Avoid overlapping write ownership and keep integration in the parent.
- Follow the repository's routing and cost rules rather than duplicating them here.
- Review the returned diff and evidence; rerun relevant verification yourself.

TDD, per-task commits, and subagent implementation are optional unless the repository or
user requires them.

## 4. Choose review and fix ownership

Use independent review when risk, ambiguity, cross-cutting behavior, or expensive failure
makes a fresh context valuable. Skip it when the change is small, mechanically verified,
and adequately reviewed by the parent. Independent two-stage review is not mandatory.

The parent triages every finding. Fix small or tightly coupled issues directly when that
is the clearest path; delegate only a larger, independently bounded fix. A
never-fix-manually rule does not apply. Verify retained fixes proportionately and discard
unsupported suggestions.

## 5. Pause before a scope pivot

When the current task uncovers separate work or a prolonged investigation, pause its OAW
run before switching scope. Inventory both the main checkout and the actual feature
worktree; never infer worktree cleanliness from the main checkout, branch pointers, or a
worktree listing. Record each checkout's branch and base revision, tracked changes,
untracked files, diff summary, commit state, and checks already run.

Preserve in-progress work by default. Leave an exact resume instruction that names the
worktree and says whether the next session should resume implementation, review existing
changes, or consider cleanup. Do not clean up the worktree unless that is a separate,
justified decision after confirming that no work would be lost.

When implementation is ready, use the main `oaw` skill for `task review` or
`task complete`, naming only checks actually run. Report the feature branch/worktree,
implementation and review ownership decisions, verification, accepted exceptions, and
remaining issues to the user. Keep external writes in the parent context.
