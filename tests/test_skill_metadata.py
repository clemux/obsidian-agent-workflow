"""Structural contract for skill display metadata.

Every skill ships `agents/openai.yaml` with the same interface shape. This checks
structure only — wording belongs to the docs and must stay freely editable.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"

REQUIRED_FIELDS = ("display_name:", "short_description:", "default_prompt:")


def test_every_skill_ships_interface_metadata_with_required_fields():
    skill_dirs = sorted(path for path in SKILLS.iterdir() if path.is_dir())
    assert skill_dirs, "no skills found under skills/"

    for skill_dir in skill_dirs:
        metadata_path = skill_dir / "agents" / "openai.yaml"
        assert metadata_path.is_file(), f"{skill_dir.name}: missing agents/openai.yaml"
        metadata = metadata_path.read_text(encoding="utf-8")
        assert metadata.startswith("interface:\n"), (
            f"{skill_dir.name}: openai.yaml must start with an interface block"
        )
        for field in REQUIRED_FIELDS:
            line = next(
                (line for line in metadata.splitlines() if line.strip().startswith(field)),
                None,
            )
            assert line is not None, f"{skill_dir.name}: missing {field}"
            value = line.split(":", 1)[1].strip().strip('"')
            assert value, f"{skill_dir.name}: {field} value is empty"
