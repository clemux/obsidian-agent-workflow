from pathlib import Path

import pytest

from oaw.errors import OawError
from oaw.frontmatter import FRONTMATTER_READ_LIMIT
from oaw.resolver import resolve_id


def large_frontmatter_note(note_id: str) -> str:
    padding = "x" * (FRONTMATTER_READ_LIMIT + 1)
    return f"---\npadding: {padding}\nid: {note_id}\n---\n\n# Large target\n"


def test_resolver_ignores_unclosed_unrelated_frontmatter(tmp_path: Path):
    (tmp_path / "Broken.md").write_text("---\nid: broken\n", encoding="utf-8")
    target = tmp_path / "Target.md"
    target.write_text("---\nid: target\n---\n\n# Target\n", encoding="utf-8")

    match = resolve_id("target", tmp_path)

    assert match.path == target


def test_resolver_matches_closed_frontmatter_larger_than_safety_limit(tmp_path: Path):
    target = tmp_path / "Large.md"
    target.write_text(large_frontmatter_note("target"), encoding="utf-8")

    match = resolve_id("target", tmp_path)

    assert match.path == target


def test_resolver_large_frontmatter_participates_in_duplicate_detection(tmp_path: Path):
    (tmp_path / "Large.md").write_text(large_frontmatter_note("duplicate"), encoding="utf-8")
    (tmp_path / "Small.md").write_text(
        "---\nid: duplicate\n---\n\n# Small target\n", encoding="utf-8"
    )

    with pytest.raises(OawError, match="id 'duplicate' is not unique"):
        resolve_id("duplicate", tmp_path)
