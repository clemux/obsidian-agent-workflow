"""Semantic contracts for the OAW repository-task execution skill."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "skills" / "oaw-task-execution"


def skill_text() -> str:
    return (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def readiness_section() -> str:
    text = skill_text()
    heading = "## 5. Classify integration readiness and hand off"
    start = text.index(heading)
    end = text.index("\n## 6.", start)
    return text[start:end]


def normalized(text: str) -> str:
    return " ".join(text.split())


def test_execution_skill_defines_distinct_repository_readiness_states():
    section = normalized(readiness_section())

    for state in ("**Implementation-ready**", "**Merge-ready**", "**Integrated**"):
        assert state in section

    assert "leave verified work uncommitted" in section
    assert "report it as implementation-ready, never merge-ready" in section
    assert "uncommitted work is not ready for OAW task review" in section
    assert "task must remain active" in section
    assert "A commit, push," in section
    assert "is not integrated" in section


def test_execution_skill_requires_scoped_commit_authorization_and_evidence():
    section = normalized(readiness_section())

    assert "request to commit, prepare for merge, or make work merge-ready" in section
    assert "does not authorize merging, rebasing, pushing, cleaning up" in section
    for evidence in (
        "Stage only exact task-owned paths",
        "git diff --cached --check",
        "git diff --cached --stat",
        "complete `git diff --cached`",
        "no unstaged or untracked residue",
        "Conventional Commit",
        "commit hook run and pass",
        "clean feature worktree",
        "commit SHA",
        "worktree-creation base revision",
        "verification results",
        "intended merge method",
    ):
        assert evidence in section


def test_execution_skill_checks_current_local_refs_and_pins_handoff_state():
    section = normalized(readiness_section())

    assert "current local main ref with the recorded base" in section
    assert "against current local refs to record fast-forward feasibility" in section
    assert "all without fetching" in section
    assert "If main moved or the ancestry check is non-zero" in section
    assert "reconciliation and renewed verification are required" in section
    assert "git merge-base --is-ancestor <intended-main> <feature-branch>" in section
    assert "remote state was not refreshed" in section
    assert "implementation-ready, merge-ready, or integrated" in section
    assert "Every user handoff and OAW task note" in section
    assert "If none was reached, say not yet implementation-ready" in section
    assert "task review` at implementation-ready or merge-ready handoff only after" in section
    assert "all task-owned work is committed and the feature worktree is clean" in section
    assert "task complete` only after integration" in section


def test_execution_skill_keeps_cleanup_integration_gated():
    text = skill_text()
    cleanup = normalized(text[text.index("## 7. Clean up after integration") :])

    assert "only after the task commits are integrated" in cleanup
    assert "post-integration verification succeeds" in cleanup
    assert "stops at review or a pull request is not integrated" in cleanup


def test_execution_skill_metadata_advertises_readiness_handoff():
    metadata = yaml.safe_load((SKILL_DIR / "agents" / "openai.yaml").read_text(encoding="utf-8"))
    interface = metadata["interface"]

    assert "merge-ready handoff" in interface["short_description"].lower()
    assert "commit-gated review" in interface["default_prompt"].lower()
    assert "integration-readiness handoff" in interface["default_prompt"].lower()
