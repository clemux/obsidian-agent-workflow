import json

from oaw import cli, resolver

from .support import write


def test_resolve_short_project_alias_to_project_index(run_oaw):
    proc = run_oaw("resolve", "--json", "obs:CDX")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["id"] == "CDX-index"
    assert data["matched_by"] == "project-alias"
    assert data["relative_path"] == "Projects/Codex Delegation/Index.md"


def test_resolve_exact_match_wins_over_project_alias(run_oaw, legacy_vault):
    write(
        legacy_vault / "Projects/Codex Delegation/Tasks/Short code.md",
        """---
type: task
id: CDX
aliases:
  - CDX
---

# Short code
""",
    )
    proc = run_oaw("resolve", "--json", "obs:CDX")
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["id"] == "CDX"
    assert data["matched_by"] == "id"


def test_resolve_ambiguous_project_alias_fails_with_candidates(run_oaw, legacy_vault):
    write(
        legacy_vault / "Projects/Other Codex/Index.md",
        """---
type: project
id: CDX-index
aliases:
  - CDX-index
---

# Other Codex
""",
    )
    proc = run_oaw("resolve", "obs:CDX")
    assert proc.returncode != 0
    assert "not unique" in proc.stderr
    assert "Projects/Codex Delegation/Index.md (project-alias)" in proc.stderr
    assert "Projects/Other Codex/Index.md (project-alias)" in proc.stderr


def test_duplicate_ids_fail(run_oaw, legacy_vault):
    write(
        legacy_vault / "Other.md",
        """---
id: AGT-TSK-obsidian-task-ids
---

# Other
""",
    )
    proc = run_oaw("resolve", "AGT-TSK-obsidian-task-ids")
    assert proc.returncode != 0
    assert "not unique" in proc.stderr


def test_resolve_prefilters_unrelated_frontmatter_before_parsing(
    run_oaw, legacy_vault, monkeypatch
):
    for index in range(50):
        write(
            legacy_vault / f"Noise/{index}.md",
            f"""---
id: NOISE-{index}
aliases:
  - OTHER-{index}
---

# PERF-TARGET body decoy
""",
        )
    write(
        legacy_vault / "Target.md",
        """---
id: PERF-TARGET
aliases:
  - PERF-ALIAS
---

# Performance target
""",
    )
    original = resolver.parse_frontmatter
    parsed: list[str] = []

    def recording_parse(frontmatter: str):
        parsed.append(frontmatter)
        return original(frontmatter)

    monkeypatch.setattr(resolver, "parse_frontmatter", recording_parse)

    match = cli.resolve_id("PERF-TARGET", legacy_vault)

    assert match.title == "Performance target"
    assert len(parsed) == 1
    assert "id: PERF-TARGET" in parsed[0]
