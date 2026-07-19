"""Structural contracts for skill packaging.

Doc wording stays freely editable; these tests pin only structure and identity:
parseable interface metadata, skill names and prompt targets that match their
directory, and vault examples that never name a real vault.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"

REQUIRED_FIELDS = ("display_name", "short_description", "default_prompt")
VAULT_EXAMPLE = re.compile(r'vault=("[^"]*"|\S+)')
VAULT_PLACEHOLDER = '"Vault Name"'


def skill_dirs() -> list[Path]:
    dirs = sorted(path for path in SKILLS.iterdir() if path.is_dir())
    assert dirs, "no skills found under skills/"
    return dirs


def interface_metadata(skill_dir: Path) -> dict:
    metadata_path = skill_dir / "agents" / "openai.yaml"
    assert metadata_path.is_file(), f"{skill_dir.name}: missing agents/openai.yaml"
    data = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{skill_dir.name}: openai.yaml must be a YAML mapping"
    interface = data.get("interface")
    assert isinstance(interface, dict), f"{skill_dir.name}: missing top-level interface mapping"
    return interface


def test_every_skill_ships_parseable_interface_metadata():
    for skill_dir in skill_dirs():
        interface = interface_metadata(skill_dir)
        for field in REQUIRED_FIELDS:
            value = interface.get(field)
            assert isinstance(value, str) and value.strip(), (
                f"{skill_dir.name}: interface.{field} must be a non-blank string"
            )


def test_skill_identity_matches_directory_manifest_and_prompt_target():
    for skill_dir in skill_dirs():
        skill_md = skill_dir / "SKILL.md"
        assert skill_md.is_file(), f"{skill_dir.name}: missing SKILL.md"
        text = skill_md.read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{skill_dir.name}: SKILL.md must start with frontmatter"
        frontmatter = yaml.safe_load(text.split("---", 2)[1])
        assert isinstance(frontmatter, dict), f"{skill_dir.name}: unparseable SKILL.md frontmatter"
        assert frontmatter.get("name") == skill_dir.name, (
            f"{skill_dir.name}: SKILL.md name '{frontmatter.get('name')}' "
            "must match the skill directory"
        )
        prompt = interface_metadata(skill_dir)["default_prompt"]
        assert f"${skill_dir.name}" in prompt, (
            f"{skill_dir.name}: default_prompt must reference ${skill_dir.name}"
        )


def test_vault_examples_use_the_generic_placeholder():
    for skill_dir in skill_dirs():
        for path in sorted(skill_dir.rglob("*.md")):
            text = path.read_text(encoding="utf-8")
            for match in VAULT_EXAMPLE.finditer(text):
                assert match.group(1) == VAULT_PLACEHOLDER, (
                    f"{path.relative_to(ROOT)}: vault example {match.group(0)!r} "
                    f"must use the generic vault={VAULT_PLACEHOLDER} placeholder"
                )
