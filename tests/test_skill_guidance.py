from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "oaw" / "SKILL.md"
EVAL = ROOT / "skills" / "oaw" / "references" / "session-phase-title-evaluation.md"
METADATA = ROOT / "skills" / "oaw" / "agents" / "openai.yaml"
README = ROOT / "README.md"
TASK_REVIEW_SKILL = ROOT / "skills" / "oaw-task-review" / "SKILL.md"
TASK_EXECUTION_SKILL = ROOT / "skills" / "oaw-task-execution" / "SKILL.md"
TASK_EXECUTION_METADATA = ROOT / "skills" / "oaw-task-execution" / "agents" / "openai.yaml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_session_phase_marker_contract_is_unambiguous():
    skill = read(SKILL)
    section = skill.split("## Session phase titles\n", 1)[1].split("\n## ", 1)[0]

    for marker in (
        "design `[DESIGN]`",
        "implementation\n`[I]`",
        "review or verification `[R]`",
        "wrapping up `[W]`",
        "completed `[DONE]`",
    ):
        assert marker in section
    assert len(section.split()) <= 125


def test_session_title_guidance_keeps_ui_phase_separate_from_lifecycle():
    skill = read(SKILL)
    readme = read(README)

    assert "Titles never change OAW state" in skill
    assert "`[R]` does not imply `status: review`" in skill
    assert "`[DONE]` is allowed only after `task complete` succeeds" in skill
    assert "not durable task state" in readme


def test_session_title_guidance_covers_ownership_resume_and_capability_fallback():
    skill = read(SKILL)

    section = skill.split("## Session phase titles\n", 1)[1].split("\n## ", 1)[0]

    assert "Incidental references do not transfer ownership" in section
    assert "silently skip title\nsynchronization" in section
    assert "Do not investigate support, announce the limitation" in section
    assert "Claude" not in section
    assert "`claude --name`" not in section


def test_repeatable_session_title_evaluation_covers_behavioral_cases():
    evaluation = read(EVAL)

    for heading in (
        "## Initial binding",
        "## Phase transition without lifecycle mutation",
        "## Review title versus review lifecycle",
        "## Incidental reference",
        "## Unsupported capability",
        "## Completion ordering",
        "## Claude interactive handoff",
    ):
        assert heading in evaluation

    assert "do not substitute a\nnon-interactive child process" in evaluation
    assert "unsupported automatic capability" in evaluation


def test_skill_metadata_uses_current_interface_schema():
    metadata = read(METADATA)

    assert metadata.startswith("interface:\n")
    assert 'short_description: "Resolve Obsidian IDs and manage task/session workflow."' in metadata
    assert "Use $oaw" in metadata


def test_task_guidance_keeps_lifecycle_preparedness_and_blockers_separate():
    skill = read(SKILL)
    review_skill = read(TASK_REVIEW_SKILL)

    assert "`needs-triage`, `needs-design`, or `prepared`" in skill
    assert "only a target in `done` satisfies" in skill
    assert "Never persist\n  inverse" in skill
    assert "`todo`: deliberately selected for near-term attention" in review_skill
    assert "Missing metadata is `unassessed`, never implicitly prepared" in review_skill
    assert "Never infer or mutate preparedness from a lifecycle decision" in review_skill


def test_oaw_routes_repository_execution_to_the_companion():
    skill = read(SKILL)

    assert "load the\n`oaw-task-execution` companion before repository edits" in skill
    assert (
        "Keep task resolution, provenance, relationships, and lifecycle writes in\nthis skill"
        in skill
    )
    assert "Do not load the companion for status-only or vault-only work" in skill


def test_task_execution_preflight_gates_worktree_creation():
    skill = read(TASK_EXECUTION_SKILL)

    for command in (
        "git status --short --branch",
        "git gtr list",
        "git gtr config list",
    ):
        assert command in skill
    assert (
        "run every repository-configured baseline\ncheck from the main checkout before worktree creation"
        in skill
    )
    assert (
        "wait for explicit user direction before creating a worktree or editing\nrepository files"
        in skill
    )
    assert "Do not weaken configuration or silently skip a check" in skill
    assert "Do not silently\nfall back to plain `git worktree` or direct edits" in skill


def test_task_execution_classifies_upstream_tracking_state():
    skill = read(TASK_EXECUTION_SKILL)

    assert "against its current local upstream-tracking ref without\nfetching" in skill
    assert "Being ahead only is allowed" in skill
    assert "remind the\nuser at handoff that those changes remain local" in skill
    assert "Being behind or diverged is a preflight\nfailure" in skill
    assert "Treat a missing or\nambiguous upstream as the same confirmation gate" in skill
    assert "Never fetch or push autonomously" in skill
    assert "comparison has not been refreshed from the remote" in skill


def test_task_execution_keeps_parent_accountable_and_delegation_optional():
    skill = read(TASK_EXECUTION_SKILL)

    assert "The parent agent remains accountable" in skill
    assert "Single-agent execution is always valid" in skill
    assert "Do not delegate merely because\nsubagents are available" in skill
    assert "Independent two-stage review is not mandatory" in skill
    assert "A\nnever-fix-manually rule does not apply" in skill


def test_task_execution_preserves_work_before_a_scope_pivot():
    skill = read(TASK_EXECUTION_SKILL)

    assert "pause its OAW\nrun before switching scope" in skill
    assert "Inventory both the main checkout and the actual feature\nworktree" in skill
    assert (
        "tracked changes,\nuntracked files, diff summary, commit state, and checks already run"
        in skill
    )
    assert "never infer worktree cleanliness from the main checkout" in skill
    assert "Preserve in-progress work by default" in skill
    assert "Leave an exact resume instruction that names the\nworktree" in skill
    assert (
        "whether the next session should resume implementation, review existing\nchanges, or consider cleanup"
        in skill
    )


def test_task_execution_metadata_uses_current_interface_schema():
    metadata = read(TASK_EXECUTION_METADATA)

    assert metadata.startswith("interface:\n")
    assert 'short_description: "Execute OAW tasks with safe isolation"' in metadata
    assert "Use $oaw-task-execution" in metadata
