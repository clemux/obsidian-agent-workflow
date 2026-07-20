import datetime as dt
import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from oaw import captures, cli, notes
from oaw.frontmatter import parse_frontmatter
from oaw.notes import split_note

SESSION_ENV = {
    "CODEX_THREAD_ID": "test-thread",
    "CLAUDE_SESSION_ID": "",
    "CLAUDE_CODE_SESSION_ID": "",
    "OPENCODE_SESSION_ID": "",
    "GEMINI_SESSION_ID": "",
}


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run(vault: Path, args, *, env=None, **kwargs):
    merged = {"OAW_VAULT": str(vault), **SESSION_ENV}
    if env:
        merged.update(env)
    return CliRunner().invoke(cli.app, args, env=merged, **kwargs)


def local_date() -> str:
    return dt.datetime.now().astimezone().strftime("%Y%m%d")


def write_project_index(vault: Path, folder: str, index_id: str) -> None:
    write(
        vault / f"Projects/{folder}/Index.md",
        f"---\ntype: project\nid: {index_id}\naliases:\n  - {index_id}\n---\n\n# {folder}\n",
    )


def capture_fm(vault: Path, note_id: str) -> dict:
    text = (vault / "Captures/Entries" / f"{note_id}.md").read_text(encoding="utf-8")
    return parse_frontmatter(split_note(text)[1])


def create_capture(vault: Path, title: str, *extra) -> str:
    result = run(vault, ["capture", "create", "--title", title, "--json", *extra])
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)["id"]


def vault_state(vault: Path) -> dict[str, bytes]:
    return {
        path.relative_to(vault).as_posix(): path.read_bytes()
        for path in sorted(vault.rglob("*"))
        if path.is_file()
    }


CANONICAL = "Captures/Entries"


# --------------------------------------------------------------------------- #
# create
# --------------------------------------------------------------------------- #


def test_create_minimal_writes_canonical_capture(tmp_path: Path):
    result = run(tmp_path, ["capture", "create", "--title", "Example Title"])

    assert result.exit_code == 0, result.stderr
    note_id = f"CAP-{local_date()}-example-title"
    path = tmp_path / CANONICAL / f"{note_id}.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    fm = parse_frontmatter(split_note(text)[1])
    assert fm["id"] == note_id
    assert fm["aliases"] == [note_id]
    assert fm["type"] == "capture"
    assert fm["status"] == "inbox"
    created = str(fm["created"])
    parsed = dt.datetime.fromisoformat(created)
    assert parsed.tzinfo is not None
    assert created.endswith("+00:00")
    assert parsed.microsecond == 0
    for blank in ("project", "area", "context", "outcome", "review_after", "destinations"):
        assert fm[blank] == []
    assert "urls" not in fm
    assert fm["session-ids"] == ["test-thread"]
    tags = fm["tags"]
    assert isinstance(tags, list) and "capture" in tags
    assert split_note(text)[2].lstrip("\n").startswith("# Example Title")
    for label in ("Created:", "ID:", "Status:", "Path:"):
        assert label in result.stdout


def test_create_json_receipt_and_stdin_body(tmp_path: Path):
    result = run(
        tmp_path,
        ["capture", "create", "--title", "Stdin cap", "--body-file", "-", "--json"],
        input="context line one\ncontext line two\n",
    )

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert set(payload) == {
        "id",
        "path",
        "title",
        "status",
        "created",
        "project",
        "urls",
        "body_chars",
    }
    text = (tmp_path / payload["path"]).read_text(encoding="utf-8")
    assert payload["body_chars"] == len(split_note(text)[2])
    assert result.stdout.strip() == json.dumps(payload, ensure_ascii=False)


def test_create_with_project_links_both_sides(tmp_path: Path):
    write_project_index(tmp_path, "Demo", "DEMO-index")

    first = run(tmp_path, ["capture", "create", "--title", "Linked", "--project", "obs:DEMO"])
    assert first.exit_code == 0, first.stderr

    note_id = f"CAP-{local_date()}-linked"
    capture_text = (tmp_path / CANONICAL / f"{note_id}.md").read_text(encoding="utf-8")
    fm = parse_frontmatter(split_note(capture_text)[1])
    assert fm["project"] == "demo"
    assert "[[Projects/Demo/Index|DEMO-index]]" in capture_text
    assert "## Related" in capture_text

    index_text = (tmp_path / "Projects/Demo/Index.md").read_text(encoding="utf-8")
    assert f"[[{CANONICAL}/{note_id}|{note_id}]]" in index_text
    assert index_text.count("## Captures") == 1

    second = run(tmp_path, ["capture", "create", "--title", "Linked", "--project", "obs:DEMO"])
    assert second.exit_code == 0, second.stderr
    assert (tmp_path / CANONICAL / f"{note_id}-2.md").exists()
    index_text = (tmp_path / "Projects/Demo/Index.md").read_text(encoding="utf-8")
    assert index_text.count("## Captures") == 1
    assert index_text.count("[[Captures/Entries/CAP-") == 2


def test_create_project_links_without_index_alias(tmp_path: Path):
    write(tmp_path / "Projects/Foo/Index.md", "---\nid: foo\n---\n\n# Foo\n")

    result = run(tmp_path, ["capture", "create", "--title", "Edge", "--project", "Foo"])
    assert result.exit_code == 0, result.stderr

    note_id = f"CAP-{local_date()}-edge"
    capture_text = (tmp_path / CANONICAL / f"{note_id}.md").read_text(encoding="utf-8")
    assert "[[Projects/Foo/Index|foo]]" in capture_text
    index_text = (tmp_path / "Projects/Foo/Index.md").read_text(encoding="utf-8")
    assert f"[[{CANONICAL}/{note_id}|{note_id}]]" in index_text


def test_create_url_validation_and_dedup(tmp_path: Path):
    result = run(
        tmp_path,
        [
            "capture",
            "create",
            "--title",
            "Sourced",
            "--url",
            "https://example.com/a",
            "--url",
            "https://example.com/a",
            "--url",
            "https://example.com/b",
        ],
    )
    assert result.exit_code == 0, result.stderr
    note_id = f"CAP-{local_date()}-sourced"
    fm = capture_fm(tmp_path, note_id)
    assert fm["urls"] == ["https://example.com/a", "https://example.com/b"]
    text = (tmp_path / CANONICAL / f"{note_id}.md").read_text(encoding="utf-8")
    assert '  - "https://example.com/a"' in text
    assert '  - "https://example.com/b"' in text

    before = vault_state(tmp_path)
    bad = run(tmp_path, ["capture", "create", "--title", "Bad", "--url", "ftp://example.com/x"])
    assert bad.exit_code == 2
    assert bad.stdout == ""
    assert vault_state(tmp_path) == before


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/a\nstatus: discarded",
        "https://example.com/a\rstatus: discarded",
    ],
)
def test_create_rejects_multiline_url_without_writing(tmp_path: Path, url: str):
    before = vault_state(tmp_path)

    result = run(tmp_path, ["capture", "create", "--title", "Bad URL", "--url", url])

    assert result.exit_code == 2
    assert "single-line" in result.stderr
    assert result.stdout == ""
    assert vault_state(tmp_path) == before


def test_create_collision_suffixes(tmp_path: Path):
    note_id = f"CAP-{local_date()}-dup"
    write(
        tmp_path / CANONICAL / f"{note_id}.md",
        f"---\nid: {note_id}\naliases:\n  - {note_id}\ntype: capture\nstatus: inbox\n---\n\n# Dup\n",
    )
    first = run(tmp_path, ["capture", "create", "--title", "Dup", "--json"])
    assert first.exit_code == 0, first.stderr
    assert json.loads(first.stdout)["id"] == f"{note_id}-2"
    assert (tmp_path / CANONICAL / f"{note_id}-2.md").exists()

    second = run(tmp_path, ["capture", "create", "--title", "Dup", "--json"])
    assert second.exit_code == 0, second.stderr
    assert json.loads(second.stdout)["id"] == f"{note_id}-3"


def test_create_concurrent_collision_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    write_project_index(tmp_path, "Demo", "DEMO-index")
    before = vault_state(tmp_path)

    def always_exists(*_args, **_kwargs):
        raise FileExistsError("racing creator")

    monkeypatch.setattr(captures, "write_new_note_atomic", always_exists)

    result = run(tmp_path, ["capture", "create", "--title", "Race", "--project", "obs:DEMO"])
    assert result.exit_code == 1
    assert "unique capture id" in result.stderr
    assert vault_state(tmp_path) == before


@pytest.mark.parametrize(
    "args",
    [
        ["capture", "create", "--title", ""],
        ["capture", "create", "--title", "   "],
        ["capture", "create", "--title", "Both", "--body", "x", "--body-file", "-"],
        ["capture", "create", "--title", "Empty", "--body", ""],
    ],
)
def test_create_usage_errors(tmp_path: Path, args):
    before = vault_state(tmp_path)
    result = run(tmp_path, args, input="stdin must not matter")
    assert result.exit_code == 2
    assert result.stdout == ""
    assert vault_state(tmp_path) == before


def test_create_session_provenance(tmp_path: Path):
    missing = run(
        tmp_path, ["capture", "create", "--title", "No session"], env={"CODEX_THREAD_ID": ""}
    )
    assert missing.exit_code == 1
    assert not (tmp_path / CANONICAL).exists()

    allowed = run(
        tmp_path,
        ["capture", "create", "--title", "No session", "--allow-missing-session-id"],
        env={"CODEX_THREAD_ID": ""},
    )
    assert allowed.exit_code == 0, allowed.stderr
    fm = capture_fm(tmp_path, f"CAP-{local_date()}-no-session")
    assert "session-ids" not in fm


# --------------------------------------------------------------------------- #
# list
# --------------------------------------------------------------------------- #


def _write_capture(
    vault: Path, relpath: str, note_id: str, *, status="inbox", project="", created=""
):
    lines = [
        "---",
        f"id: {note_id}",
        "aliases:",
        f"  - {note_id}",
        "type: capture",
        f"created: {created}",
        f"status: {status}",
        f"project: {project}" if project else "project:",
        "review_after:",
        "destinations:",
        "---",
        "",
        f"# {note_id}",
        "",
    ]
    write(vault / relpath, "\n".join(lines) + "\n")


def test_list_vault_wide_all_statuses(tmp_path: Path):
    _write_capture(tmp_path, f"{CANONICAL}/CAP-1.md", "CAP-1", status="inbox")
    _write_capture(tmp_path, "Projects/Demo/Inbox/Legacy.md", "CAP-legacy", status="archived")
    write(
        tmp_path / "Projects/Demo/Tasks/Task.md",
        "---\ntype: task\nid: DEMO-TSK-x\n---\n\n# Task\n",
    )

    default = run(tmp_path, ["capture", "list"])
    assert default.exit_code == 0, default.stderr
    assert "CAP-1" in default.stdout
    assert "CAP-legacy" in default.stdout
    assert "DEMO-TSK-x" not in default.stdout

    archived = run(tmp_path, ["capture", "list", "--status", "archived"])
    assert archived.exit_code == 0, archived.stderr
    assert "CAP-legacy" in archived.stdout
    assert "CAP-1" not in archived.stdout


def test_list_project_filter_metadata_first(tmp_path: Path):
    write_project_index(tmp_path, "aproj", "APR-index")
    write_project_index(tmp_path, "bproj", "BPR-index")
    _write_capture(tmp_path, "Projects/aproj/Inbox/meta.md", "CAP-meta", project="bproj")
    _write_capture(tmp_path, "Projects/aproj/Inbox/legacy.md", "CAP-plain")

    to_b = run(tmp_path, ["capture", "list", "--project", "bproj"])
    assert "CAP-meta" in to_b.stdout
    assert "CAP-plain" not in to_b.stdout

    to_a = run(tmp_path, ["capture", "list", "--project", "aproj"])
    assert "CAP-plain" in to_a.stdout
    assert "CAP-meta" not in to_a.stdout


def test_list_sort_and_ties(tmp_path: Path):
    _write_capture(
        tmp_path, f"{CANONICAL}/CAP-full.md", "CAP-full", created="2026-07-19T10:00:00+00:00"
    )
    _write_capture(tmp_path, f"{CANONICAL}/CAP-date.md", "CAP-date", created="2026-07-20")
    _write_capture(tmp_path, f"{CANONICAL}/CAP-missing.md", "CAP-missing", created="")
    _write_capture(
        tmp_path, f"{CANONICAL}/CAP-tieB.md", "CAP-tieB", created="2026-07-18T00:00:00+00:00"
    )
    _write_capture(
        tmp_path, f"{CANONICAL}/CAP-tieA.md", "CAP-tieA", created="2026-07-18T00:00:00+00:00"
    )

    newer = run(tmp_path, ["capture", "list"])
    order = [line.split("\t")[0] for line in newer.stdout.splitlines()]
    # date-only 2026-07-20 midnight is newest; full 2026-07-19; ties (same created) id-asc; missing last.
    assert order == ["CAP-date", "CAP-full", "CAP-tieA", "CAP-tieB", "CAP-missing"]

    older = run(tmp_path, ["capture", "list", "--sort", "older"])
    order_older = [line.split("\t")[0] for line in older.stdout.splitlines()]
    assert order_older == ["CAP-tieA", "CAP-tieB", "CAP-full", "CAP-date", "CAP-missing"]


def test_list_output_shapes(tmp_path: Path):
    empty = run(tmp_path, ["capture", "list"])
    assert empty.exit_code == 0
    assert empty.stdout == ""
    empty_json = run(tmp_path, ["capture", "list", "--json"])
    assert empty_json.stdout.strip() == "[]"

    _write_capture(tmp_path, f"{CANONICAL}/CAP-a.md", "CAP-a", created="2026-07-19T10:00:00+00:00")
    text = run(tmp_path, ["capture", "list"])
    fields = text.stdout.splitlines()[0].split("\t")
    assert fields[0] == "CAP-a"
    assert fields[3] == "-"  # empty project
    assert fields[5] == str(len(split_note((tmp_path / CANONICAL / "CAP-a.md").read_text())[2]))

    js = run(tmp_path, ["capture", "list", "--json"])
    obj = json.loads(js.stdout)[0]
    assert obj["project"] is None
    assert isinstance(obj["body_chars"], int)


def test_list_malformed_capture_warning(tmp_path: Path):
    _write_capture(tmp_path, f"{CANONICAL}/CAP-ok.md", "CAP-ok")
    write(
        tmp_path / CANONICAL / "broken.md",
        "---\ntype: capture\nstatus: inbox\n# missing closing fence\n",
    )

    result = run(tmp_path, ["capture", "list", "--json"])
    assert result.exit_code == 0
    assert "warning" in result.stderr
    assert "broken.md" in result.stderr
    assert [obj["id"] for obj in json.loads(result.stdout)] == ["CAP-ok"]


def test_list_reads_only_matched_bodies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for i in range(5):
        write(
            tmp_path / f"Notes/plain-{i}.md",
            f"---\ntype: note\nid: N-{i}\n---\n\n# note {i}\n",
        )
    _write_capture(tmp_path, f"{CANONICAL}/CAP-inbox.md", "CAP-inbox", status="inbox")
    _write_capture(tmp_path, f"{CANONICAL}/CAP-parked.md", "CAP-parked", status="parked")
    write(
        tmp_path / CANONICAL / "CAP-big.md",
        "---\nid: CAP-big\ntype: capture\nstatus: parked\n---\n\n# big\n" + ("x" * 5000) + "\n",
    )

    read_paths: list[str] = []
    original = captures._read_capture_body

    def spy(path):
        read_paths.append(path.relative_to(tmp_path).as_posix())
        return original(path)

    monkeypatch.setattr(captures, "_read_capture_body", spy)
    captures.list_captures(tmp_path, "inbox", None, "newer", False)

    assert read_paths == [f"{CANONICAL}/CAP-inbox.md"]


def test_body_chars_counts_code_points(tmp_path: Path):
    result = run(
        tmp_path,
        ["capture", "create", "--title", "Emoji", "--body", "rocket 🚀 tail  ", "--json"],
    )
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    text = (tmp_path / payload["path"]).read_text(encoding="utf-8")
    body = split_note(text)[2]
    assert payload["body_chars"] == len(body)
    assert len(body) < len(body.encode("utf-8"))


# --------------------------------------------------------------------------- #
# show
# --------------------------------------------------------------------------- #


def test_show_any_location_text_and_json(tmp_path: Path):
    _write_capture(
        tmp_path, f"{CANONICAL}/CAP-canon.md", "CAP-canon", created="2026-07-19T10:00:00+00:00"
    )
    _write_capture(tmp_path, "Projects/Demo/Inbox/legacy.md", "CAP-legacy", status="reference")

    text = run(tmp_path, ["capture", "show", "CAP-canon"])
    assert text.exit_code == 0, text.stderr
    for label in ("ID:", "Path:", "Title:", "Status:", "Created:", "Tags:"):
        assert label in text.stdout
    assert "# CAP-canon" in text.stdout

    js = run(tmp_path, ["capture", "show", "CAP-legacy", "--json"])
    obj = json.loads(js.stdout)
    for key in (
        "id",
        "path",
        "title",
        "status",
        "created",
        "project",
        "area",
        "context",
        "outcome",
        "review_after",
        "destinations",
        "urls",
        "session_ids",
        "tags",
        "body",
        "body_chars",
    ):
        assert key in obj
    assert obj["status"] == "reference"
    assert isinstance(obj["destinations"], list)
    assert obj["project"] is None
    assert isinstance(obj["body_chars"], int)


def test_show_rejects_non_capture_and_unknown(tmp_path: Path):
    write(
        tmp_path / "Projects/Demo/Tasks/Task.md",
        "---\ntype: task\nid: DEMO-TSK-x\naliases:\n  - DEMO-TSK-x\n---\n\n# Task\n",
    )
    mismatch = run(tmp_path, ["capture", "show", "DEMO-TSK-x"])
    assert mismatch.exit_code == 1
    assert "not a capture" in mismatch.stderr
    assert "task" in mismatch.stderr

    unknown = run(tmp_path, ["capture", "show", "CAP-nope"])
    assert unknown.exit_code == 1


# --------------------------------------------------------------------------- #
# triage
# --------------------------------------------------------------------------- #


def make_canonical_capture(
    tmp_path: Path, note_id: str, *, status="inbox", extra_fm="", extra_body=""
):
    lines = [
        "---",
        f"id: {note_id}",
        "aliases:",
        f"  - {note_id}",
        "type: capture",
        "created: 2026-07-19T10:00:00+00:00",
        f"status: {status}",
        "project:",
        "review_after:",
        "destinations:",
    ]
    if extra_fm:
        lines.append(extra_fm)
    lines += ["---", "", f"# {note_id}", ""]
    if extra_body:
        lines += [extra_body, ""]
    write(tmp_path / CANONICAL / f"{note_id}.md", "\n".join(lines) + "\n")


def test_triage_incubating_flow(tmp_path: Path):
    make_canonical_capture(tmp_path, "CAP-inc")

    missing = run(
        tmp_path, ["capture", "triage", "CAP-inc", "--status", "incubating", "--reason", "x"]
    )
    assert missing.exit_code == 2

    ok = run(
        tmp_path,
        [
            "capture",
            "triage",
            "CAP-inc",
            "--status",
            "incubating",
            "--review-after",
            "2026-08-01",
            "--reason",
            "let it settle",
        ],
    )
    assert ok.exit_code == 0, ok.stderr
    text = (tmp_path / CANONICAL / "CAP-inc.md").read_text(encoding="utf-8")
    fm = parse_frontmatter(split_note(text)[1])
    assert fm["status"] == "incubating"
    assert fm["review_after"] == "2026-08-01"
    assert fm["session-ids"] == ["test-thread"]
    assert "## Triage" in text
    assert "inbox -> incubating" in text
    assert "let it settle" in text
    assert "CODEX_THREAD_ID=test-thread" in text


def test_triage_review_after_scoping(tmp_path: Path):
    make_canonical_capture(tmp_path, "CAP-scope")
    bad = run(
        tmp_path,
        [
            "capture",
            "triage",
            "CAP-scope",
            "--status",
            "parked",
            "--no-reason",
            "--review-after",
            "2026-08-01",
        ],
    )
    assert bad.exit_code == 2

    write(
        tmp_path / CANONICAL / "CAP-clear.md",
        "---\nid: CAP-clear\naliases:\n  - CAP-clear\ntype: capture\n"
        "created: 2026-07-19T10:00:00+00:00\nstatus: incubating\nproject:\n"
        "review_after: 2026-08-01\ndestinations:\n---\n\n# CAP-clear\n",
    )
    cleared = run(tmp_path, ["capture", "triage", "CAP-clear", "--status", "parked", "--no-reason"])
    assert cleared.exit_code == 0, cleared.stderr
    fm = parse_frontmatter(split_note((tmp_path / CANONICAL / "CAP-clear.md").read_text())[1])
    assert fm["review_after"] == []


def test_triage_triaged_requires_destination(tmp_path: Path):
    make_canonical_capture(tmp_path, "CAP-route")
    write(
        tmp_path / "Projects/Demo/Tasks/Dest.md",
        "---\ntype: task\nid: DEMO-TSK-dest\naliases:\n  - DEMO-TSK-dest\n---\n\n# Dest\n\n## Related\n",
    )
    before = (tmp_path / CANONICAL / "CAP-route.md").read_bytes()
    refused = run(
        tmp_path, ["capture", "triage", "CAP-route", "--status", "triaged", "--reason", "go"]
    )
    assert refused.exit_code == 1
    assert (tmp_path / CANONICAL / "CAP-route.md").read_bytes() == before

    ok = run(
        tmp_path,
        [
            "capture",
            "triage",
            "CAP-route",
            "--status",
            "triaged",
            "--reason",
            "go",
            "--destination",
            "DEMO-TSK-dest",
        ],
    )
    assert ok.exit_code == 0, ok.stderr
    capture_text = (tmp_path / CANONICAL / "CAP-route.md").read_text(encoding="utf-8")
    dest_text = (tmp_path / "Projects/Demo/Tasks/Dest.md").read_text(encoding="utf-8")
    assert "[[Projects/Demo/Tasks/Dest|DEMO-TSK-dest]]" in capture_text
    assert "[[Captures/Entries/CAP-route|CAP-route]]" in dest_text

    again = run(
        tmp_path,
        [
            "capture",
            "triage",
            "CAP-route",
            "--status",
            "discarded",
            "--reason",
            "done",
            "--destination",
            "DEMO-TSK-dest",
        ],
    )
    assert again.exit_code == 0, again.stderr
    capture_text = (tmp_path / CANONICAL / "CAP-route.md").read_text(encoding="utf-8")
    dest_text = (tmp_path / "Projects/Demo/Tasks/Dest.md").read_text(encoding="utf-8")
    assert capture_text.count("[[Projects/Demo/Tasks/Dest|DEMO-TSK-dest]]") == 1
    assert dest_text.count("[[Captures/Entries/CAP-route|CAP-route]]") == 1


def test_triage_reason_contract(tmp_path: Path):
    for bad_args in (["--reason", "x", "--no-reason"], [], ["--reason", "   "]):
        make_canonical_capture(tmp_path, "CAP-reason")
        result = run(tmp_path, ["capture", "triage", "CAP-reason", "--status", "parked", *bad_args])
        assert result.exit_code == 2, bad_args

    make_canonical_capture(tmp_path, "CAP-noreason")
    receipt = run(
        tmp_path,
        ["capture", "triage", "CAP-noreason", "--status", "parked", "--no-reason", "--json"],
    )
    assert receipt.exit_code == 0, receipt.stderr
    payload = json.loads(receipt.stdout)
    assert payload["reason"] is None
    assert payload["reason_omitted"] is True
    text = (tmp_path / CANONICAL / "CAP-noreason.md").read_text(encoding="utf-8")
    assert "reason omitted via --no-reason" in text


def test_triage_same_status_refused(tmp_path: Path):
    make_canonical_capture(tmp_path, "CAP-same")
    before = (tmp_path / CANONICAL / "CAP-same.md").read_bytes()
    result = run(tmp_path, ["capture", "triage", "CAP-same", "--status", "inbox", "--no-reason"])
    assert result.exit_code == 1
    assert (tmp_path / CANONICAL / "CAP-same.md").read_bytes() == before


def test_triage_refuses_outside_canonical_store(tmp_path: Path):
    _write_capture(tmp_path, "Projects/Demo/Inbox/legacy.md", "CAP-outside", status="inbox")
    before = (tmp_path / "Projects/Demo/Inbox/legacy.md").read_bytes()
    result = run(
        tmp_path, ["capture", "triage", "CAP-outside", "--status", "parked", "--no-reason"]
    )
    assert result.exit_code == 1
    assert "canonical" in result.stderr.lower() or "Captures/Entries" in result.stderr
    assert (tmp_path / "Projects/Demo/Inbox/legacy.md").read_bytes() == before


def test_triage_rollback_on_failed_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    make_canonical_capture(tmp_path, "CAP-roll")
    dest = tmp_path / "Projects/Demo/Tasks/Dest.md"
    write(
        dest,
        "---\ntype: task\nid: DEMO-TSK-dest\naliases:\n  - DEMO-TSK-dest\n---\n\n# Dest\n\n## Related\n",
    )
    capture_before = (tmp_path / CANONICAL / "CAP-roll.md").read_bytes()
    dest_before = dest.read_bytes()

    original_commit = notes.VaultTransaction.commit

    def failing_commit(self, replace=os.replace):
        def bad_replace(src, target):
            if target == str(dest):
                raise OSError("injected destination write failure")
            os.replace(src, target)

        return original_commit(self, replace=bad_replace)

    monkeypatch.setattr(notes.VaultTransaction, "commit", failing_commit)

    result = run(
        tmp_path,
        [
            "capture",
            "triage",
            "CAP-roll",
            "--status",
            "triaged",
            "--reason",
            "go",
            "--destination",
            "DEMO-TSK-dest",
        ],
    )
    assert result.exit_code == 1
    assert result.stdout == ""
    assert (tmp_path / CANONICAL / "CAP-roll.md").read_bytes() == capture_before
    assert dest.read_bytes() == dest_before


def test_triage_conflict_on_concurrent_modification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    make_canonical_capture(tmp_path, "CAP-conflict")
    path = tmp_path / CANONICAL / "CAP-conflict.md"
    original_commit = notes.VaultTransaction.commit

    def mutating_commit(self, replace=os.replace):
        path.write_text(path.read_text(encoding="utf-8") + "\nCONCURRENT EDIT\n", encoding="utf-8")
        return original_commit(self, replace=replace)

    monkeypatch.setattr(notes.VaultTransaction, "commit", mutating_commit)

    result = run(
        tmp_path, ["capture", "triage", "CAP-conflict", "--status", "parked", "--no-reason"]
    )
    assert result.exit_code == 1
    assert "changed on disk" in result.stderr
    text = path.read_text(encoding="utf-8")
    assert text.endswith("CONCURRENT EDIT\n")
    assert "## Triage" not in text
    assert "status: inbox" in text


def test_unknown_frontmatter_and_body_preserved(tmp_path: Path):
    make_canonical_capture(
        tmp_path,
        "CAP-preserve",
        extra_fm="custom-key: keep-me # inline comment",
        extra_body="## Notes\n\nExisting body section stays.",
    )
    result = run(
        tmp_path, ["capture", "triage", "CAP-preserve", "--status", "parked", "--reason", "shelve"]
    )
    assert result.exit_code == 0, result.stderr
    text = (tmp_path / CANONICAL / "CAP-preserve.md").read_text(encoding="utf-8")
    assert "custom-key: keep-me # inline comment" in text
    assert "## Notes\n\nExisting body section stays." in text
    assert "status: parked" in text
    assert "## Triage" in text
