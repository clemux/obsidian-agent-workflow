import hashlib
import json
import re

import pytest

from tests import support
from tests.support import write


@pytest.fixture
def vault(tmp_path):
    return support.make_vault(tmp_path)


@pytest.fixture
def base_env(vault):
    return support.cli_env(vault)


@pytest.fixture
def run_oaw(vault):
    return support.make_runner(vault)


def test_export_note_requires_safe_marker(run_oaw, vault):
    support.add_task(
        vault,
        "Obsidian Agent Workflow",
        "Resolver CLI.md",
        "OAW-TSK-cli",
        project="obsidian-agent-workflow",
        tags=("projects",),
        body="# Resolver CLI\n\n## Goal\n\nBuild it.\n",
    )
    proc = run_oaw(
        "export",
        "note",
        "OAW-TSK-cli",
        "--output-root",
        str(vault / "exports"),
    )
    assert proc.returncode != 0
    assert "export-scope: work" in proc.stderr


def test_export_note_writes_bundle_manifest_and_artifacts(run_oaw, vault):
    write(
        vault / "Projects/Obsidian Agent Workflow/Tasks/Work export.md",
        """---
type: task
project: obsidian-agent-workflow
status: todo
id: OAW-TSK-work-export
aliases:
  - OAW-TSK-work-export
export-scope: work
return_ingest: true
export_artifacts:
  - scripts/run.sh
---

# Work export

Run this at work.
""",
    )
    write(
        vault / "Projects/Obsidian Agent Workflow/Tasks/scripts/run.sh",
        "#!/bin/sh\necho work\n",
    )
    output_root = vault / "exports"
    proc = run_oaw(
        "export",
        "note",
        "OAW-TSK-work-export",
        "--output-root",
        str(output_root),
    )
    assert proc.returncode == 0, proc.stderr
    bundle = output_root / "OAW-TSK-work-export"
    manifest_path = bundle / "manifest.json"
    note_path = bundle / "note.md"
    artifact_path = bundle / "artifacts/Projects/Obsidian Agent Workflow/Tasks/scripts/run.sh"
    assert manifest_path.exists()
    assert note_path.exists()
    assert artifact_path.exists()
    assert "intentionally exported" in note_path.read_text(encoding="utf-8")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "oaw-safe-export-v1"
    assert manifest["target"] == "work"
    assert manifest["source"]["id"] == "OAW-TSK-work-export"
    assert manifest["source"]["path"] == "Projects/Obsidian Agent Workflow/Tasks/Work export.md"
    assert manifest["artifacts"][0]["path"] == artifact_path.relative_to(bundle).as_posix()

    valid = run_oaw("export", "validate", str(bundle))
    assert valid.returncode == 0, valid.stderr
    assert "Export: valid" in valid.stdout


def test_export_validate_rejects_tampered_marker(run_oaw, vault):
    write(
        vault / "Projects/Obsidian Agent Workflow/Tasks/Work export.md",
        """---
type: task
id: OAW-TSK-work-export
aliases:
  - OAW-TSK-work-export
export-scope: work
---

# Work export
""",
    )
    output_root = vault / "exports"
    proc = run_oaw(
        "export",
        "note",
        "OAW-TSK-work-export",
        "--output-root",
        str(output_root),
    )
    assert proc.returncode == 0, proc.stderr
    bundle = output_root / "OAW-TSK-work-export"
    note_path = bundle / "note.md"
    note_path.write_text(
        note_path.read_text(encoding="utf-8").replace(
            "export-scope: work",
            "export-scope: personal",
        ),
        encoding="utf-8",
    )
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["note"]["sha256"] = hashlib.sha256(note_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    proc = run_oaw("export", "validate", str(bundle))
    assert proc.returncode != 0
    assert "export-scope: work" in proc.stderr


def test_export_note_failure_leaves_no_partial_bundle_and_retry_succeeds(run_oaw, vault, base_env):
    note = vault / "Projects/Obsidian Agent Workflow/Tasks/Retry export.md"
    artifact = note.parent / "missing.txt"
    write(
        note,
        """---
type: task
id: OAW-TSK-retry-export
export-scope: work
export_artifacts:
  - missing.txt
---

# Retry export
""",
    )
    output_root = vault / "exports"
    before = support.snapshot_tree_without_following_symlinks(vault)

    failed = support.run_oaw_subprocess(
        [
            str(x)
            for x in (
                "export",
                "note",
                "OAW-TSK-retry-export",
                "--output-root",
                output_root,
            )
        ],
        base_env,
    )

    assert failed.returncode != 0
    # The only permitted effect of the failed export is the empty output root;
    # the staging directory is cleaned up and nothing else under the vault
    # changes (no partial bundle, no leftover tmp staging entry).
    assert support.snapshot_tree_without_following_symlinks(vault) == {
        **before,
        "exports": ("directory", None),
    }

    write(artifact, "ready\n")
    retried = support.run_oaw_subprocess(
        [
            str(x)
            for x in (
                "export",
                "note",
                "OAW-TSK-retry-export",
                "--output-root",
                output_root,
            )
        ],
        base_env,
    )
    assert retried.returncode == 0, retried.stderr
    assert (output_root / "OAW-TSK-retry-export/manifest.json").exists()


def test_export_note_sanitizes_bundle_name_from_id(run_oaw, vault):
    write(
        vault / "Projects/Obsidian Agent Workflow/Tasks/Escape export.md",
        """---
type: task
id: ../escape
export-scope: work
---

# Escape export
""",
    )
    output_root = vault / "exports"

    proc = run_oaw(
        "export",
        "note",
        "../escape",
        "--output-root",
        str(output_root),
    )

    assert proc.returncode == 0, proc.stderr
    assert (output_root / "escape/manifest.json").exists()
    assert not (vault / "escape").exists()


@pytest.mark.parametrize(
    "path_kind",
    [
        pytest.param("relative", id="relative-parent-escape"),
        pytest.param("absolute", id="absolute-outside-path"),
    ],
)
def test_export_validate_rejects_paths_outside_bundle(run_oaw, vault, path_kind):
    write(
        vault / "Projects/Obsidian Agent Workflow/Tasks/Path export.md",
        """---
type: task
id: OAW-TSK-path-export
export-scope: work
---

# Path export
""",
    )
    output_root = vault / "exports"
    exported = run_oaw(
        "export",
        "note",
        "OAW-TSK-path-export",
        "--output-root",
        str(output_root),
    )
    assert exported.returncode == 0, exported.stderr
    bundle = output_root / "OAW-TSK-path-export"
    outside = output_root / "stolen.md"
    outside.write_text((bundle / "note.md").read_text(encoding="utf-8"), encoding="utf-8")
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["note"]["sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()

    escaped_path = "../stolen.md" if path_kind == "relative" else str(outside)
    manifest["note"]["path"] = escaped_path
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    proc = run_oaw("export", "validate", str(bundle))
    assert proc.returncode != 0
    assert re.search(r"manifest path (escapes bundle|must be bundle-relative)", proc.stderr)


def test_safe_export_ingest_dry_run_reads_markers_and_leaves_files(run_oaw, vault):
    ingestion = vault / "handoff"
    safe = ingestion / "safe.md"
    legacy = ingestion / "legacy.md"
    unsafe = ingestion / "unsafe.md"
    write(
        safe,
        """---
export-scope: personal
---

# Safe

Body.
""",
    )
    write(
        legacy,
        """---
tags:
  - safe-export-personal
---

# Legacy
""",
    )
    write(
        unsafe,
        """---
project: private
---

# Unsafe
""",
    )

    proc = run_oaw(
        "ingest",
        "safe-export",
        "--ingestion-root",
        str(ingestion),
        "--destination",
        "Imports/Handoff",
    )

    assert proc.returncode == 0, proc.stderr
    assert "Mode: dry-run" in proc.stdout
    assert (
        "ACCEPT safe.md [export-scope: personal] -> Imports/Handoff/safe.md; dry-run" in proc.stdout
    )
    assert (
        "ACCEPT legacy.md [tag: safe-export-personal] -> Imports/Handoff/legacy.md; dry-run"
        in proc.stdout
    )
    assert "REJECT unsafe.md [missing safe export marker] -> quarantine; dry-run" in proc.stdout
    assert safe.exists()
    assert legacy.exists()
    assert unsafe.exists()
    assert not (vault / "Imports/Handoff/safe.md").exists()
    assert not (ingestion / ".rejected/unsafe.md").exists()


def test_safe_export_ingest_write_ingests_safe_and_quarantines_rejected(run_oaw, vault):
    ingestion = vault / "handoff"
    safe = ingestion / "nested/safe.md"
    unsafe = ingestion / "unsafe.md"
    existing = vault / "Imports/Handoff/nested/safe.md"
    write(
        safe,
        """---
export-approved: personal
---

# Safe
""",
    )
    write(
        unsafe,
        """---
export-scope: work
---

# Unsafe
""",
    )
    write(existing, "existing\n")

    proc = run_oaw(
        "ingest",
        "safe-export",
        "--ingestion-root",
        str(ingestion),
        "--destination",
        "Imports/Handoff",
        "--write",
    )

    assert proc.returncode == 0, proc.stderr
    assert (
        "ACCEPT nested/safe.md [export-approved: personal] -> "
        "Imports/Handoff/nested/safe-2.md; removed source" in proc.stdout
    )
    assert (
        "REJECT unsafe.md [missing safe export marker] -> quarantine .rejected/unsafe.md"
        in proc.stdout
    )
    assert not safe.exists()
    assert not unsafe.exists()
    assert existing.read_text(encoding="utf-8") == "existing\n"
    assert (vault / "Imports/Handoff/nested/safe-2.md").exists()
    assert (ingestion / ".rejected/unsafe.md").exists()


def test_safe_export_ingest_rejects_unclosed_frontmatter(run_oaw, vault):
    ingestion = vault / "handoff"
    broken = ingestion / "broken.md"
    write(
        broken,
        """---
export-scope: personal
# no closing fence
Body that should not be trusted.
""",
    )

    proc = run_oaw(
        "ingest",
        "safe-export",
        "--ingestion-root",
        str(ingestion),
    )

    assert proc.returncode == 0, proc.stderr
    assert "REJECT broken.md [frontmatter is not closed:" in proc.stdout


def test_safe_export_ingest_refuses_absolute_destination(run_oaw, vault):
    ingestion = vault / "handoff"
    write(
        ingestion / "safe.md",
        """---
export-scope: personal
---

# Safe
""",
    )

    proc = run_oaw(
        "ingest",
        "safe-export",
        "--ingestion-root",
        str(ingestion),
        "--destination",
        str(vault / "absolute"),
    )

    assert proc.returncode != 0
    assert "--destination must be vault-relative" in proc.stderr


def test_safe_export_ingest_refuses_conflicting_modes(run_oaw, vault):
    proc = run_oaw(
        "ingest",
        "safe-export",
        "--dry-run",
        "--write",
    )

    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "not allowed with argument" in proc.stderr


def test_safe_export_ingest_refuses_root_that_contains_vault(run_oaw, vault):
    ingestion = vault / "misconfigured"
    nested_vault = ingestion / "vault"
    note = nested_vault / "Projects/Demo/Tasks/Unsafe.md"
    write(note, "---\nid: DEMO-TSK-unsafe\n---\n\n# Unsafe\n")

    proc = run_oaw(
        "ingest",
        "safe-export",
        "--ingestion-root",
        str(ingestion),
        "--write",
        env={"OAW_VAULT": str(nested_vault)},
    )

    assert proc.returncode != 0
    assert "ingestion root must not be or contain the vault" in proc.stderr
    assert note.exists()


def test_safe_export_ingest_refuses_destination_inside_ingestion_root(run_oaw, vault):
    ingestion = vault / "handoff"
    write(
        ingestion / "safe.md",
        "---\nexport-scope: personal\n---\n\n# Safe\n",
    )

    proc = run_oaw(
        "ingest",
        "safe-export",
        "--ingestion-root",
        str(ingestion),
        "--destination",
        "handoff/imported",
    )

    assert proc.returncode != 0
    assert "destination must not be inside the ingestion root" in proc.stderr


def test_safe_export_ingest_dry_run_previews_collision_destination(run_oaw, vault):
    ingestion = vault / "handoff"
    write(
        ingestion / "safe.md",
        "---\nexport-scope: personal\n---\n\n# Safe\n",
    )
    write(vault / "Imports/Handoff/safe.md", "existing\n")

    proc = run_oaw(
        "ingest",
        "safe-export",
        "--ingestion-root",
        str(ingestion),
        "--destination",
        "Imports/Handoff",
    )

    assert proc.returncode == 0, proc.stderr
    assert "-> Imports/Handoff/safe-2.md; dry-run" in proc.stdout
