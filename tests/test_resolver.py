from pathlib import Path

from oaw.resolver import resolve_id


def test_resolver_ignores_unclosed_unrelated_frontmatter(tmp_path: Path):
    (tmp_path / "Broken.md").write_text("---\nid: broken\n", encoding="utf-8")
    target = tmp_path / "Target.md"
    target.write_text("---\nid: target\n---\n\n# Target\n", encoding="utf-8")

    match = resolve_id("target", tmp_path)

    assert match.path == target
