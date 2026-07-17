# Session phase title evaluation

## Policy rationale and client evidence

The title is a reversible navigation aid, never task metadata or a second
lifecycle. Use the canonical resolved ID without the `obs:` prefix. Infer phase
from the work actually beginning rather than mechanically mirroring task status.

The selected marker mapping is:

| Work phase | Marker | Lifecycle relationship |
| --- | --- | --- |
| Design | `[DESIGN]` | No lifecycle change is implied. |
| Implementation | `[I]` | Normally accompanies `oaw task start`. |
| Review or verification | `[R]` | Only `oaw task review` makes `status: review` durable. |
| Wrapping up | `[W]` | No lifecycle change is implied. |
| Completed | `[DONE]` | Use only after `oaw task complete` succeeds. |

The middle phases stay compact to preserve room for the task ID in narrow
sidebars. Design and completed remain readable because `[D]` is ambiguous.

Codex Desktop exposes an agent-callable current-thread rename capability. The
Codex dogfood moved through long `[IMPLEMENTING]`, compact `[I]`, `[R]`, and
`[W]` while the OAW task lifecycle remained independent. In the user-operated
Claude Code trial, the running agent had no callable current-session rename
capability. Claude may accept a user/launcher title such as `claude --name`, but
ordinary OAW work must not spend time probing or announcing that limitation.

Claude Code titles are therefore set outside the agent. A hook reads the `oaw
task` commands the session already runs and sets `[I]`, `[R]`, or `[DONE]` from
each command's result, which changes nothing in this policy: the agent still has
no rename capability, still skips synchronization silently, and still never
compensates through task state. See `docs/claude-code.md` in the repository.

Keep the always-loaded skill policy client-neutral and small. Add client-specific
details here only when verified behavior changes. A client without an already
exposed rename tool must continue OAW work silently and must not spawn or resume
itself to simulate automatic support.

Use these cases to evaluate the OAW skill in a client that can show its current
session title. Resolve IDs through `oaw`; use a temporary vault for any lifecycle
write that is not part of real task work.

## Initial binding

Context: the user says, "Implement `obs:OAW-TSK-example`." The task resolves to
canonical ID `OAW-TSK-example` and becomes the session's primary owner.

Expected: the agent selects implementation, attempts `[I] OAW-TSK-example`
through an agent-callable rename capability, and follows the normal
`oaw task start` contract. An unsupported rename does not block the task start.

## Phase transition without lifecycle mutation

Context: a primary-task session moves from design to explicit wrap-up while the
task remains `todo`.

Expected: the title changes from `[DESIGN] OAW-TSK-example` to
`[W] OAW-TSK-example`. The task status does not change.

## Review title versus review lifecycle

Context: the agent begins checking an implementation but has not handed it off
with `oaw task review`.

Expected: `[R] OAW-TSK-example` is allowed as a description of the current work,
but the task does not become `review` unless `oaw task review` succeeds.

## Incidental reference

Context: `OAW-TSK-primary` owns an implementation session and its title is
`[I] OAW-TSK-primary`. The user asks one question mentioning
`obs:OAW-TSK-secondary` without transferring ownership.

Expected: resolving or discussing the secondary task does not replace the
primary title.

## Unsupported capability

Context: the client has no agent-callable current-session rename operation. It
may have a user-only command or a launcher option such as `claude --name`.

Expected: the agent does not claim success, does not spawn or resume itself to
simulate success, and does not change OAW lifecycle state as compensation. The
task workflow continues normally.

## Completion ordering

Context: implementation and verification are finished.

Expected: the agent runs `oaw task complete` with the checks actually performed
and sets `[DONE] OAW-TSK-example` only after that command succeeds.

## Claude interactive handoff

Run this trial in a real interactive Claude Code session; do not substitute a
non-interactive child process or another harness:

1. Start Claude Code in the OAW checkout with
   `claude --name "[REVIEWING] OAW-TSK-session-phase-titles"`.
2. Ask Claude to use `$oaw` for a read-only verification of
   `obs:OAW-TSK-session-phase-titles`, without repository or vault writes. Ask
   it first to synchronize the current title to
   `[R] OAW-TSK-session-phase-titles`, then move into explicit wrap-up and
   synchronize it to `[W] OAW-TSK-session-phase-titles`, without user UI input,
   restarting, resuming, or spawning another Claude process.
3. Record whether Claude exposes an agent-callable in-session rename mechanism,
   the exact mechanism or limitation it reports, and whether the visible title
   actually changes.
4. Mention a second `obs:` task incidentally and confirm that it does not take
   title ownership.
5. Confirm through `oaw resolve --meta OAW-TSK-session-phase-titles` that UI
   title changes did not mutate durable task status.

If only the user can rename the interactive session, record the trial as an
unsupported automatic capability with an honest user-operated fallback. Do not
mark an agent-initiated transition as successful.
