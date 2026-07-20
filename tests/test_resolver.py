from pathlib import Path

import pytest

from oaw import resolver
from oaw.errors import OawError
from oaw.frontmatter import FRONTMATTER_READ_LIMIT
from oaw.resolver import (
    resolve_id,
    resolve_project_root,
    resolve_project_root_from_references,
    scan_note_references,
)


@pytest.mark.parametrize("configured", [None, "", "   "])
def test_vault_root_requires_configured_environment(monkeypatch, configured: str | None):
    if configured is None:
        monkeypatch.delenv("OAW_VAULT", raising=False)
    else:
        monkeypatch.setenv("OAW_VAULT", configured)

    with pytest.raises(
        OawError,
        match="OAW_VAULT is required; set it to the Obsidian vault path",
    ):
        resolver.vault_root()


def test_vault_root_resolves_configured_environment(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OAW_VAULT", str(tmp_path))

    assert resolver.vault_root() == tmp_path.resolve()


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


def test_scanned_references_ignore_body_decoys(tmp_path: Path):
    (tmp_path / "Unrelated.md").write_text(
        "---\nid: unrelated\n---\n\n# target body decoy\n", encoding="utf-8"
    )
    (tmp_path / "Target.md").write_text("---\nid: target\n---\n\n# Target\n", encoding="utf-8")

    references = scan_note_references(tmp_path)
    matches = resolver.matches_from_references("target", references)

    assert len(matches) == 1
    assert matches[0].title == "Target"


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


def test_exact_project_folder_precedes_bare_alias_collision(tmp_path: Path):
    exact = tmp_path / "Projects/DUP"
    exact.mkdir(parents=True)
    alias_index = tmp_path / "Projects/Alias Target/Index.md"
    alias_index.parent.mkdir(parents=True)
    alias_index.write_text("---\nid: DUP-index\n---\n\n# Alias target\n", encoding="utf-8")
    references = scan_note_references(tmp_path)

    assert resolve_project_root("DUP", tmp_path) == (exact, None)
    assert resolve_project_root_from_references("DUP", tmp_path, references) == (exact, None)
    assert resolve_project_root("obs:DUP", tmp_path) == (alias_index.parent, "DUP")
    assert resolve_project_root_from_references("obs:DUP", tmp_path, references) == (
        alias_index.parent,
        "DUP",
    )


def test_project_alias_resolves_without_exact_folder(tmp_path: Path):
    index = tmp_path / "Projects/Long Project Name/Index.md"
    index.parent.mkdir(parents=True)
    index.write_text("---\nid: SHORT-index\n---\n\n# Long project name\n", encoding="utf-8")
    references = scan_note_references(tmp_path)

    assert resolve_project_root("SHORT", tmp_path) == (index.parent, "SHORT")
    assert resolve_project_root_from_references("SHORT", tmp_path, references) == (
        index.parent,
        "SHORT",
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
