from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "oaw" / "SKILL.md"
EVAL = ROOT / "skills" / "oaw" / "references" / "session-phase-title-evaluation.md"
METADATA = ROOT / "skills" / "oaw" / "agents" / "openai.yaml"
README = ROOT / "README.md"


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
