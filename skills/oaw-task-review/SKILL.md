---
name: oaw-task-review
description: Interactively review and reconcile OAW project-task statuses one task at a time, using a stable lifecycle-ordered inventory, a plan-tool checklist, evidence-based readiness recommendations, confirmed OAW lifecycle writes, uncertainty investigation, and resumable progress. Use when the user asks to review, triage, clean up, or decide statuses for the open tasks in an OAW-managed Obsidian project.
---

# OAW Task Review

Review a fixed snapshot of one project's `active`, `todo`, and `backlog` tasks. Keep the interaction compact, ask for one decision at a time, and mutate task notes only after the user confirms a status.

Use the `oaw` skill for ID resolution and lifecycle command rules. Read its `SKILL.md` completely before beginning a review.

## 1. Select the project and inventory once

Resolve an explicit `obs:<project>` reference with `oaw resolve`. If the project is not unambiguous from the request or current context, ask which OAW project to review.

List the three included statuses in this order:

```bash
oaw list --project "<Project folder>" --status active
oaw list --project "<Project folder>" --status todo
oaw list --project "<Project folder>" --status backlog
```

Create one immutable queue of task IDs from those results: all `active` tasks, then `todo`, then `backlog`, preserving the CLI order within each group. Do not add tasks that enter one of those statuses later, and do not re-add a task after changing its status. Existing `review` tasks are outside the default pass unless the user explicitly expands the scope.

Use the plan tool to expose a concise checklist:

- Put one step per queued task, formatted `<ID> — <short title>`.
- Mark only the current task `in_progress`; leave the remaining tasks `pending`.
- After a confirmed decision is successfully applied, mark that step `completed` and append `→ <status>` to its text.
- Treat the plan as the review cursor and decision ledger. Do not recreate it from the live status lists during the pass.

Tell the user the task count and status breakdown, then begin with the first task. Do not dump every task's details up front.

## 2. Inspect and present one task

Resolve the current task with `oaw resolve --full <ID>`. Inspect only the evidence needed to assess it: its frontmatter, Problem, progress/session entries, related dependency notes, and—when the task claims implementation progress—the relevant repository state or artifacts. Prefer read-only checks. Do not infer completion from status, age, or a vague progress sentence.

Present this compact shape:

```text
<position>/<total> — <ID>: <title>
Current: <status> · Priority: <value or —> · Effort: <value or —>
Summary: <concise problem and material progress>
Dependencies/blockers: <concise evidence or “none found”>
Ready: <yes/no/unclear> — <reason>
Recommendation: <status> — <evidence-based reason>

Choose: active / review / todo / backlog / done / not sure / pause
```

Use these meanings consistently:

- `active`: work is genuinely underway and has a clear next action.
- `review`: implementation or deliverable work is finished and verified, but awaits review or a decision.
- `todo`: implementation-ready and deliberately selected for near-term work, but not underway.
- `backlog`: valid work that is unscheduled, deprioritized, or blocked before implementation.
- `done`: the objective is fully satisfied and verified; no required work remains.

Recommend the best evidence-supported status even when it differs from the current status. Call readiness `yes` only when the outcome is clear, material decisions and dependencies are resolved, and the next implementation action is identifiable. Distinguish “no blocker recorded” from proof that no blocker exists.

End the turn after asking for the current task's decision. Do not inspect or preview the next task while awaiting the answer.

## 3. Apply a confirmed decision

Accept the five lifecycle statuses, `not sure`, or `pause`. If the response is ambiguous, ask a short clarification without changing state.

When the chosen status already matches the note, make no redundant lifecycle write; record the decision in the plan and advance. Otherwise map the decision to the installed OAW command:

```text
active  -> oaw task start <ID> --note "<concise review rationale>"
review  -> oaw task review <ID> --note "<concise review rationale>" --checks "<verification actually run>"
todo    -> oaw task promote <ID> --note "<concise review rationale>"
backlog -> oaw task backlog <ID> --note "<concise review rationale>"
done    -> oaw task complete <ID> --note "<concise completion evidence>" --checks "<verification actually run>"
```

For `review` or `done`, run an appropriate verification first. Reuse an already-run check from the current review only when its result is still applicable. Never invent a check string or treat note inspection alone as implementation verification. If verification cannot be performed safely or fails, report the evidence and ask the user to choose another status; do not issue the lifecycle command.

Rely on successful OAW output to confirm the task-note lifecycle write. If the command fails, keep the plan step `in_progress`, surface the error, and resolve it before advancing. After success, update the plan, make the next queued task `in_progress`, and present only that task.

## 4. Investigate “not sure” with Luna

On the first `not sure` decision for a task, launch exactly one read-only subagent using model `gpt-5.6-luna`. Use an explorer agent when repository inspection is involved. Give it the task ID, resolved task path, project index or repository path, and this neutral assignment:

```text
Investigate whether this OAW task is implementation-ready or already satisfied. Inspect current task-note and repository evidence read-only. Report: material progress, missing work, dependencies/blockers, verification evidence, and the best-supported lifecycle status among active, review, todo, backlog, and done. Cite paths or commands. Do not modify files, notes, git state, or external services.
```

Do not tell the subagent the current recommendation or desired answer. Wait for it, reconcile its findings with the primary evidence, report a compact findings summary, update the recommendation if warranted, and ask for the same task's decision again. Keep the plan step `in_progress` throughout.

Do not spawn a duplicate investigation for the same unchanged uncertainty. If the required subagent capability or model is unavailable, state that limitation and ask whether to investigate locally or pause; do not silently substitute another model.

## 5. Pause, resume, and finish

On `pause`, do not change the current task. Leave it `in_progress` in the plan and report a resume capsule containing:

```text
Project: <project>
Reviewed: <ID>=<decision>, ...
Current: <ID>
Remaining: <ordered IDs after current>
```

Keep this capsule compact. On resume in the same thread, continue from the existing plan. If the plan is unavailable but a capsule is provided, rebuild the checklist from the capsule rather than re-inventorying live statuses. Re-resolve the current task before presenting it because evidence may have changed.

When every queued step is complete, report the decision counts and any tasks whose writes or verification remain unresolved. Do not claim the project has no other open work: the pass intentionally excluded initial `review` tasks and tasks created or moved into scope after the snapshot.
