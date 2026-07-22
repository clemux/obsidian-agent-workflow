"""Tests for the pure ``oaw doctor`` engine (:mod:`oaw.doctor`).

Each test builds its own minimal ``tmp_path`` vault: a ``.obsidian/app.json``
(when the scenario needs one) plus a handful of ``*.md`` notes. No CLI is
exercised here -- :func:`oaw.doctor.run_doctor` is called directly and its
:class:`~oaw.doctor.DoctorReport` inspected.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from oaw.doctor import DoctorReport, Status, run_doctor

SUPPORTED_VERSION = "1.12.7"


def _write_app_json(vault: Path, **settings: object) -> None:
    obsidian_dir = vault / ".obsidian"
    obsidian_dir.mkdir(parents=True, exist_ok=True)
    (obsidian_dir / "app.json").write_text(json.dumps(settings), encoding="utf-8")


def _write_note(vault: Path, relative_path: str, text: str) -> Path:
    path = vault / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _all_checks(report: DoctorReport) -> list:
    return [*report.environment, *report.parser, *report.vault]


def _find(report: DoctorReport, name: str):
    for check in _all_checks(report):
        if check.name == name:
            return check
    raise AssertionError(
        f"no check named {name!r} in report: {[c.name for c in _all_checks(report)]}"
    )


def _green_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True)
    _write_note(vault, "Note.md", "## A Clean Note\n\nNothing wrong here.\n")
    return vault


# --- green vault -------------------------------------------------------------------


def test_green_vault_reports_pass_everywhere_and_zero_exit_code(tmp_path: Path) -> None:
    vault = _green_vault(tmp_path)
    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    assert report.exit_code == 0
    assert all(check.status is Status.PASS for check in _all_checks(report))
    assert report.vault_issues == ()


# --- required setting: strictLineBreaks ---------------------------------------------


def test_missing_app_json_fails_strict_line_breaks_and_warns_missing_file(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_note(vault, "Note.md", "# Fine\n")

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    strict = _find(report, "setting:strictLineBreaks")
    assert strict.status is Status.FAIL
    app_json = _find(report, "app-json")
    assert app_json.status is Status.WARN
    assert report.exit_code != 0


def test_strict_line_breaks_explicitly_false_fails(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=False)
    _write_note(vault, "Note.md", "# Fine\n")

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    strict = _find(report, "setting:strictLineBreaks")
    assert strict.status is Status.FAIL
    # A present-but-wrong app.json is still readable: that check itself passes.
    assert _find(report, "app-json").status is Status.PASS
    assert report.exit_code != 0


def test_strict_line_breaks_true_passes(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True)

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    assert _find(report, "setting:strictLineBreaks").status is Status.PASS


# --- rename-rewrite hazard -----------------------------------------------------------


def test_shortest_link_format_with_always_update_links_warns_rename_hazard(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    # newLinkFormat omitted entirely -> resolves to Obsidian's own default, "shortest".
    _write_app_json(vault, strictLineBreaks=True, alwaysUpdateLinks=True)

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    hazard = _find(report, "rename-rewrite-hazard")
    assert hazard.status is Status.WARN
    assert "rename" in hazard.detail.lower()
    # This is a WARN, not a FAIL: it must not by itself force a non-zero exit code.
    assert report.exit_code == 0


def test_shortest_link_format_without_always_update_links_passes(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True, alwaysUpdateLinks=False)

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    assert _find(report, "rename-rewrite-hazard").status is Status.PASS


def test_non_shortest_link_format_with_always_update_links_does_not_warn(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True, alwaysUpdateLinks=True, newLinkFormat="absolute")

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    assert _find(report, "rename-rewrite-hazard").status is Status.PASS


# --- Obsidian version classification --------------------------------------------------


def test_newer_untested_obsidian_version_warns(tmp_path: Path) -> None:
    vault = _green_vault(tmp_path)

    report = run_doctor(vault, obsidian_version="1.99.0")

    version_check = _find(report, "obsidian-version")
    assert version_check.status is Status.WARN
    assert "newer" in version_check.detail.lower()
    assert report.exit_code == 0


def test_unknown_obsidian_version_warns_when_absent(tmp_path: Path) -> None:
    vault = _green_vault(tmp_path)

    report = run_doctor(vault, obsidian_version=None)

    version_check = _find(report, "obsidian-version")
    assert version_check.status is Status.WARN
    assert report.exit_code == 0


def test_unparseable_obsidian_version_string_warns_unknown(tmp_path: Path) -> None:
    vault = _green_vault(tmp_path)

    report = run_doctor(vault, obsidian_version="not-a-version")

    version_check = _find(report, "obsidian-version")
    assert version_check.status is Status.WARN


def test_older_untested_obsidian_version_warns(tmp_path: Path) -> None:
    vault = _green_vault(tmp_path)

    report = run_doctor(vault, obsidian_version="1.0.0")

    version_check = _find(report, "obsidian-version")
    assert version_check.status is Status.WARN
    assert report.exit_code == 0


# --- vault compatibility scan: invalid UTF-8 -------------------------------------------


def test_invalid_utf8_note_warns_and_scan_continues(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True)
    _write_note(vault, "Good.md", "# Fine\n")
    (vault / "Bad.md").write_bytes(b"\xff\xfe not valid utf-8\n")

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    scan = _find(report, "vault-scan")
    assert scan.status is Status.WARN
    codes = {issue.code for issue in report.vault_issues}
    assert "vault.invalid-utf8" in codes
    bad_issue = next(issue for issue in report.vault_issues if issue.code == "vault.invalid-utf8")
    assert bad_issue.path == "Bad.md"
    # A per-note problem is a WARN, not a FAIL: it must not disable OAW globally.
    assert report.exit_code == 0


# --- vault compatibility scan: duplicate frontmatter key --------------------------------


def test_duplicate_frontmatter_key_note_warns(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True)
    _write_note(
        vault,
        "Duplicate.md",
        "---\nid: OAW-TSK-example\nid: OAW-TSK-example-again\n---\n\nBody.\n",
    )

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    scan = _find(report, "vault-scan")
    assert scan.status is Status.WARN
    codes = {issue.code for issue in report.vault_issues}
    assert "frontmatter.duplicate-key" in codes
    assert report.exit_code == 0


# --- vault compatibility scan: write-safety WARNING diagnostics -------------------------


def test_unclosed_frontmatter_note_warns(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True)
    _write_note(
        vault,
        "Unclosed.md",
        "---\nid: OAW-TSK-example\nstatus: todo\n\nBody without a closing delimiter.\n",
    )

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    assert _find(report, "vault-scan").status is Status.WARN
    codes = {issue.code for issue in report.vault_issues}
    assert "envelope.unclosed-frontmatter" in codes
    assert report.exit_code == 0


def test_unclosed_fence_note_warns(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True)
    _write_note(vault, "Fence.md", "# Note\n\n```python\ncode here\n")

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    assert _find(report, "vault-scan").status is Status.WARN
    codes = {issue.code for issue in report.vault_issues}
    assert "markdown.unclosed-fence" in codes
    assert report.exit_code == 0


def test_unclosed_html_block_note_warns(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True)
    _write_note(vault, "Html.md", "# Note\n\n<!--\nnever closed\n")

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    codes = {issue.code for issue in report.vault_issues}
    assert "markdown.unclosed-html-block" in codes
    assert report.exit_code == 0


def test_unclosed_obsidian_comment_note_warns(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True)
    _write_note(vault, "Comment.md", "# Note\n\n%%never closes\n")

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    codes = {issue.code for issue in report.vault_issues}
    assert "obsidian.unclosed-comment" in codes
    assert report.exit_code == 0


def test_unclosed_math_block_note_warns(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True)
    _write_note(vault, "Math.md", "# Note\n\n$$\nE = mc^2\n")

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    codes = {issue.code for issue in report.vault_issues}
    assert "obsidian.unclosed-math" in codes
    assert report.exit_code == 0


def test_wrong_shaped_owned_fields_note_warns(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True)
    _write_note(
        vault,
        "OwnedFields.md",
        "---\nid:\n  - not-a-scalar\ndestinations: not-a-list\n---\n\nBody.\n",
    )

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    codes = [issue.code for issue in report.vault_issues]
    assert codes.count("frontmatter.owned-field-type") == 2
    assert report.exit_code == 0


def test_bare_empty_destinations_field_scans_clean(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True)
    _write_note(
        vault,
        "Capture.md",
        "---\nid: OAW-CAP-x\ndestinations:\n---\n\nBody.\n",
    )

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    codes = [issue.code for issue in report.vault_issues]
    assert "frontmatter.owned-field-type" not in codes
    assert report.exit_code == 0


def test_scalar_destinations_field_still_warns(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True)
    _write_note(
        vault,
        "Capture.md",
        "---\nid: OAW-CAP-x\ndestinations: actual-scalar-text\n---\n\nBody.\n",
    )

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    codes = [issue.code for issue in report.vault_issues]
    assert codes.count("frontmatter.owned-field-type") == 1
    assert report.exit_code == 0


# --- vault compatibility scan: unreadable/symlinked files ------------------------------


def test_dangling_symlink_note_warns_skipped_without_following(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True)
    _write_note(vault, "Good.md", "# Fine\n")
    (vault / "Dangling.md").symlink_to(vault / "does-not-exist.md")

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    assert _find(report, "vault-scan").status is Status.WARN
    issue = next(issue for issue in report.vault_issues if issue.path == "Dangling.md")
    assert issue.code == "vault.symlinked-note"
    assert "symlinked note skipped" in issue.message


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
def test_unreadable_note_warns_and_scan_completes(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True)
    _write_note(vault, "Good.md", "# Fine\n")
    unreadable = _write_note(vault, "Unreadable.md", "# Secret\n")
    unreadable.chmod(0o000)

    try:
        report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)
    finally:
        unreadable.chmod(0o644)

    assert _find(report, "vault-scan").status is Status.WARN
    issue = next(issue for issue in report.vault_issues if issue.path == "Unreadable.md")
    assert issue.code == "vault.unreadable-note"
    assert "could not read note" in issue.message
    assert report.exit_code == 0


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
def test_unreadable_directory_warns_and_scan_completes(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True)
    _write_note(vault, "Good.md", "# Fine\n")
    blocked_dir = vault / "Blocked"
    blocked_dir.mkdir()
    _write_note(vault, "Blocked/Hidden.md", "# Hidden\n")
    blocked_dir.chmod(0o000)

    try:
        report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)
    finally:
        blocked_dir.chmod(0o755)

    assert _find(report, "vault-scan").status is Status.WARN
    codes = {issue.code for issue in report.vault_issues}
    assert "vault.walk-error" in codes
    assert report.exit_code == 0


def test_vault_scan_skips_dotfile_directories(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True)
    _write_note(vault, "Good.md", "# Fine\n")
    # A malformed note tucked inside .obsidian/.trash must never be scanned.
    (vault / ".obsidian" / "plugins-cache.md").parent.mkdir(parents=True, exist_ok=True)
    (vault / ".obsidian" / "plugins-cache.md").write_bytes(b"\xff\xfe bad\n")
    (vault / ".trash").mkdir()
    (vault / ".trash" / "Deleted.md").write_text("---\nid: a\nid: b\n---\n", encoding="utf-8")

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    assert _find(report, "vault-scan").status is Status.PASS
    assert report.vault_issues == ()


# --- packaged fixture integrity (group 2) ---------------------------------------------


def test_packaged_fixtures_all_load_and_pass(tmp_path: Path) -> None:
    vault = _green_vault(tmp_path)

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)

    assert 8 <= len(report.parser) <= 12
    failures = [c for c in report.parser if c.status is not Status.PASS]
    assert not failures, failures
    names = {c.name for c in report.parser}
    assert "heading-atx" in names
    assert "fence-unclosed" in names
    assert "frontmatter-duplicate-key" in names


# --- report payload shape --------------------------------------------------------------


def test_report_payload_shape_is_stable(tmp_path: Path) -> None:
    vault = _green_vault(tmp_path)

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)
    payload = report.to_payload()

    assert set(payload) == {"exit_code", "environment", "parser", "vault", "vault_issues"}
    assert payload["exit_code"] == 0
    for group_name in ("environment", "parser", "vault"):
        group = payload[group_name]
        assert isinstance(group, list) and group
        for entry in group:
            assert set(entry) == {"name", "status", "detail"}
            assert entry["status"] in {"pass", "warn", "fail"}
    assert payload["vault_issues"] == []
    # The payload must be plain, JSON-serializable data (used verbatim by --json).
    json.dumps(payload)


def test_report_payload_includes_full_vault_issue_list_beyond_detail_cap(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_app_json(vault, strictLineBreaks=True)
    for i in range(25):
        _write_note(vault, f"Dup{i:02d}.md", f"---\nid: a\nid: b-{i}\n---\n\nBody {i}.\n")

    report = run_doctor(vault, obsidian_version=SUPPORTED_VERSION)
    payload = report.to_payload()

    # Each note's single duplicated key produces two ERROR diagnostics (one
    # per occurrence), so 25 notes carry 50 issues in total.
    assert len(report.vault_issues) == 50
    assert len(payload["vault_issues"]) == 50
    scan_detail = _find(report, "vault-scan").detail
    assert "+30 more" in scan_detail
