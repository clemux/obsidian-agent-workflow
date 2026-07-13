from pathlib import Path

import pytest

from oaw import resolver
from oaw.errors import OawError
from oaw.frontmatter import FRONTMATTER_READ_LIMIT
from oaw.resolver import resolve_id, resolve_project_root_from_references, scan_note_references


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


def test_scanned_references_prefilter_before_parsing(tmp_path: Path, monkeypatch):
    (tmp_path / "Unrelated.md").write_text(
        "---\nid: unrelated\nmarker: must-not-parse\n---\n", encoding="utf-8"
    )
    (tmp_path / "Target.md").write_text("---\nid: target\n---\n\n# Target\n", encoding="utf-8")
    original = resolver.parse_frontmatter
    parsed: list[str] = []

    def recording_parse(frontmatter: str):
        parsed.append(frontmatter)
        return original(frontmatter)

    monkeypatch.setattr(resolver, "parse_frontmatter", recording_parse)

    references = scan_note_references(tmp_path)
    matches = resolver.matches_from_references("target", references)

    assert len(matches) == 1
    assert matches[0].title == "Target"
    assert len(parsed) == 1
    assert "id: target" in parsed[0]
    assert "must-not-parse" not in parsed[0]


def test_scanned_project_alias_ignores_nested_projects_directory(tmp_path: Path):
    real = tmp_path / "Projects/Real/Index.md"
    real.parent.mkdir(parents=True)
    real.write_text("---\nid: REAL-index\n---\n\n# Real\n", encoding="utf-8")
    nested = tmp_path / "Archive/Projects/Unrelated/Index.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("---\nid: REAL-index\n---\n\n# Unrelated\n", encoding="utf-8")

    references = scan_note_references(tmp_path)

    assert resolve_project_root_from_references("REAL", tmp_path, references) == (
        real.parent,
        "REAL",
    )


def test_scanned_project_alias_matches_are_sorted(tmp_path: Path):
    for project in ("Zulu", "Alpha"):
        index = tmp_path / "Projects" / project / "Index.md"
        index.parent.mkdir(parents=True)
        index.write_text("---\nid: DUP-index\n---\n", encoding="utf-8")
    references = list(reversed(scan_note_references(tmp_path)))

    matches = resolver.project_alias_matches_from_references("DUP", references)

    assert [match.relpath for match in matches] == [
        "Projects/Alpha/Index.md",
        "Projects/Zulu/Index.md",
    ]
