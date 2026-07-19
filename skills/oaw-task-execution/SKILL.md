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

Classify the primary branch against its current local upstream-tracking ref without
fetching. Being ahead only is allowed: record the unpushed commit count and remind the
user at handoff that those changes remain local. Being behind or diverged is a preflight
failure that requires user evaluation before work continues. Treat a missing or
ambiguous upstream as the same confirmation gate. Never fetch or push autonomously, and
state when the comparison has not been refreshed from the remote.

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

## 6. Clean up after integration

Run cleanup only after the task commits are integrated into the intended local main
branch and post-integration verification succeeds. A workflow that stops at review or a
pull request is not integrated: retain its worktree and leave the exact resume path.

Inventory every worktree owned by the task, not merely the current worktree. Establish
ownership from the task's execution record and this session's repository/worktree
inventory; never infer ownership from a similar branch name. For a task that touched
multiple repositories, group the owned worktrees by repository and repeat the cleanup
checks independently from each repository's main checkout, using that repository's
intended main branch and GTR configuration. Include multiple task-owned worktrees in one
repository when the task used them.

For each task-owned worktree, record its repository, path, branch, and intended main
branch, then verify all of the following from current local refs without fetching:

```bash
git -C <worktree-path> status --short --branch
git -C <worktree-path> branch --show-current
git -C <main-checkout> merge-base --is-ancestor <feature-branch> <intended-main>
```

The status must contain no tracked or untracked changes, the worktree must be on the
expected named feature branch, and the ancestry command must exit zero. Tree equality,
matching patches, or a successful squash merge is not proof that the feature branch is
merged. A missing path or branch, detached HEAD, unexpected repository identity, or
ambiguous intended main branch is a blocker rather than permission to guess.

Remove a worktree that passes every check through GTR from its owning repository, then
rerun `git gtr list` to verify removal:

```bash
git gtr rm <worktree-name> --yes
git gtr list
```

Never pass `--force`. Do not pass `--delete-branch` or otherwise delete the retained
feature branch unless the user separately authorizes branch deletion. If any precheck or
removal fails, retain the worktree and report the exact blocking evidence: repository,
path, branch, intended main, status output, failed ancestry result, or GTR error as
applicable. Record removed and retained worktrees in the final task note and user handoff;
do not describe the task workspace as cleaned when a task-owned worktree remains.
