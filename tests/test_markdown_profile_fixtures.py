"""Runner for the ``tests/fixtures/markdown_profile/**`` compatibility corpus.

Each fixture directory holds a ``source.md`` (read as bytes, then decoded as
UTF-8 -- CRLF fixtures carry literal ``\\r\\n`` bytes and are marked ``-text``
in ``.gitattributes`` so Git never rewrites their line endings) and an
``expect.json`` describing:

- ``obsidian``: a grounded observation of documented Obsidian/CommonMark/GFM
  behavior (required).
- ``parser``: optional keys asserted against ``oaw.document.model``'s
  ``NoteDocument`` once that module exists. Every key is optional; only
  present keys are asserted, and list-valued keys ("headings", "protected",
  "obsidian_spans") are matched as a subset -- each expected entry must be
  found among the actual results, but the actual results may contain
  additional entries this corpus does not enumerate. "diagnostics" is
  likewise a subset of expected diagnostic codes. This keeps fixtures honest
  without requiring an exhaustive enumeration of every parser-internal
  region.
- ``editing``: optional list of ``oaw.document.editing`` operation
  assertions, checked once that module exists.

The two schema-validation tests below (``test_fixture_has_source_and_valid_expect``
and friends) never import ``oaw.document.model`` or ``oaw.document.editing``
and therefore always run, regardless of how much of the document layer has
landed. The parser- and editing-assertion tests guard their own imports and
``pytest.skip`` per-test (not at module level) so this file keeps validating
the corpus shape even while phase 2/3 modules are still missing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_ROOT = Path(__file__).parent / "fixtures" / "markdown_profile"

FAMILIES = {
    "headings",
    "fences",
    "frontmatter",
    "comments-math",
    "tables",
    "callouts-quotes",
    "links",
    "embeds-blockids",
    "malformed",
    "mixed",
}

ALLOWED_TOP_KEYS = {"obsidian", "parser", "editing"}
ALLOWED_OBSIDIAN_KEYS = {"version", "source", "observation"}
ALLOWED_OBSIDIAN_SOURCES = {"documented", "probed"}
ALLOWED_PARSER_KEYS = {"headings", "protected", "obsidian_spans", "diagnostics", "newline"}
ALLOWED_NEWLINE_VALUES = {"lf", "crlf", "none"}
ALLOWED_EDITING_KEYS = {"op", "heading", "block", "key", "value", "expect", "result_contains"}
ALLOWED_EDITING_OPS = {
    "append_block_to_section",
    "set_frontmatter_scalar",
    "append_frontmatter_list_item",
    "remove_frontmatter_list_item",
}
ALLOWED_EDITING_EXPECT = {"ok", "error"}


def _fixture_dirs() -> list[Path]:
    dirs = []
    if not FIXTURES_ROOT.is_dir():
        return dirs
    for family_dir in sorted(FIXTURES_ROOT.iterdir()):
        if not family_dir.is_dir():
            continue
        for fixture_dir in sorted(family_dir.iterdir()):
            if fixture_dir.is_dir():
                dirs.append(fixture_dir)
    return dirs


FIXTURE_DIRS = _fixture_dirs()
FIXTURE_IDS = [f"{d.parent.name}/{d.name}" for d in FIXTURE_DIRS]


def _load_expect(fixture_dir: Path) -> dict:
    return json.loads((fixture_dir / "expect.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Schema / JSON-validity checks -- always collected, never import oaw.document.
# --------------------------------------------------------------------------- #


def test_corpus_is_not_empty() -> None:
    assert FIXTURE_DIRS, "expected at least one fixture under tests/fixtures/markdown_profile"


def test_every_declared_family_has_fixtures() -> None:
    present = {d.parent.name for d in FIXTURE_DIRS}
    missing = FAMILIES - present
    assert not missing, f"families with no fixtures directory: {sorted(missing)}"
    unexpected = present - FAMILIES
    assert not unexpected, (
        f"fixture directories outside the declared families: {sorted(unexpected)}"
    )


def test_every_family_has_four_to_eight_fixtures() -> None:
    counts: dict[str, int] = {}
    for fixture_dir in FIXTURE_DIRS:
        family = fixture_dir.parent.name
        counts[family] = counts.get(family, 0) + 1
    out_of_range = {family: count for family, count in counts.items() if not (4 <= count <= 8)}
    assert not out_of_range, f"families outside the 4-8 fixture range: {out_of_range}"


@pytest.mark.parametrize("fixture_dir", FIXTURE_DIRS, ids=FIXTURE_IDS)
def test_fixture_has_source_and_valid_expect(fixture_dir: Path) -> None:
    source_path = fixture_dir / "source.md"
    expect_path = fixture_dir / "expect.json"
    assert source_path.is_file(), f"missing source.md in {fixture_dir}"
    assert expect_path.is_file(), f"missing expect.json in {fixture_dir}"

    raw = source_path.read_bytes()
    raw.decode("utf-8")  # must always be valid UTF-8 (a leading BOM is permitted)

    expect = json.loads(expect_path.read_text(encoding="utf-8"))
    assert isinstance(expect, dict), f"{expect_path} must contain a JSON object"

    unknown_top = set(expect) - ALLOWED_TOP_KEYS
    assert not unknown_top, f"unknown top-level keys in {expect_path}: {unknown_top}"

    assert "obsidian" in expect, f"{expect_path} missing required 'obsidian' key"
    obsidian = expect["obsidian"]
    assert isinstance(obsidian, dict), f"'obsidian' must be an object in {expect_path}"
    assert set(obsidian) == ALLOWED_OBSIDIAN_KEYS, (
        f"'obsidian' must have exactly {ALLOWED_OBSIDIAN_KEYS} in {expect_path}, got {set(obsidian)}"
    )
    assert obsidian["source"] in ALLOWED_OBSIDIAN_SOURCES, expect_path
    assert isinstance(obsidian["version"], str) and obsidian["version"], expect_path
    assert isinstance(obsidian["observation"], str) and obsidian["observation"].strip(), expect_path

    if "parser" in expect:
        parser = expect["parser"]
        assert isinstance(parser, dict), f"'parser' must be an object in {expect_path}"
        unknown_parser = set(parser) - ALLOWED_PARSER_KEYS
        assert not unknown_parser, f"unknown parser keys in {expect_path}: {unknown_parser}"
        if "newline" in parser:
            assert parser["newline"] in ALLOWED_NEWLINE_VALUES, expect_path
        if "headings" in parser:
            assert isinstance(parser["headings"], list)
            for entry in parser["headings"]:
                assert {"level", "text", "line"} <= set(entry), expect_path
        if "protected" in parser:
            assert isinstance(parser["protected"], list)
            for entry in parser["protected"]:
                assert {"kind", "lines"} <= set(entry), expect_path
        if "obsidian_spans" in parser:
            assert isinstance(parser["obsidian_spans"], list)
            for entry in parser["obsidian_spans"]:
                assert {"kind", "line"} <= set(entry), expect_path
        if "diagnostics" in parser:
            assert isinstance(parser["diagnostics"], list)
            for code in parser["diagnostics"]:
                assert isinstance(code, str) and "." in code, expect_path

    if "editing" in expect:
        editing = expect["editing"]
        assert isinstance(editing, list), f"'editing' must be a list in {expect_path}"
        for entry in editing:
            assert isinstance(entry, dict), expect_path
            unknown_editing = set(entry) - ALLOWED_EDITING_KEYS
            assert not unknown_editing, f"unknown editing keys in {expect_path}: {unknown_editing}"
            assert entry.get("op") in ALLOWED_EDITING_OPS, f"unknown editing op in {expect_path}"
            assert entry.get("expect") in ALLOWED_EDITING_EXPECT, expect_path


def test_crlf_fixtures_present_per_family() -> None:
    """Every family carries at least two fixtures whose bytes contain CRLF."""
    by_family: dict[str, int] = {family: 0 for family in FAMILIES}
    for fixture_dir in FIXTURE_DIRS:
        raw = (fixture_dir / "source.md").read_bytes()
        if b"\r\n" in raw:
            by_family[fixture_dir.parent.name] += 1
    insufficient = {family: count for family, count in by_family.items() if count < 2}
    assert not insufficient, f"families with fewer than 2 CRLF fixtures: {insufficient}"


def test_no_real_vault_identifiers_in_fixtures() -> None:
    """Fixture content must stay synthetic: no personal home-directory paths."""
    for fixture_dir in FIXTURE_DIRS:
        raw = (fixture_dir / "source.md").read_bytes()
        assert b"/home/" not in raw, f"personal path leaked into {fixture_dir}"


# --------------------------------------------------------------------------- #
# Parser assertions -- require oaw.document.model; skipped per-test until it
# exists (module import is attempted once, at collection time, but a failure
# only skips the tests that need it -- the schema tests above are unaffected).
# --------------------------------------------------------------------------- #

try:
    from oaw.document import model as _model
except ImportError:
    _model = None

try:
    from oaw.document import editing as _editing
except ImportError:
    _editing = None

try:
    from oaw.errors import OawError
except ImportError:  # pragma: no cover - oaw.errors always ships with the package
    OawError = Exception  # type: ignore[assignment,misc]


def _kind_value(kind: object) -> str:
    return kind.value if hasattr(kind, "value") else str(kind)


def _actual_headings(document) -> list[dict]:
    return [
        {
            "level": heading.level,
            "text": heading.text,
            "line": document.index.offset_to_line(heading.span.start),
        }
        for heading in document.markdown.headings
    ]


def _actual_protected(document) -> list[dict]:
    regions = []
    for region in document.protected_regions:
        start_line = document.index.offset_to_line(region.span.start)
        end_offset = max(region.span.start, region.span.end - 1)
        end_line = document.index.offset_to_line(end_offset)
        regions.append(
            {"kind": region.kind, "lines": [start_line, end_line], "closed": region.closed}
        )
    return regions


def _actual_obsidian_spans(document) -> list[dict]:
    spans = []
    for span in document.obsidian_spans:
        entry = {
            "kind": _kind_value(span.kind),
            "line": document.index.offset_to_line(span.span.start),
        }
        if span.target is not None:
            entry["target"] = span.target
        spans.append(entry)
    return spans


def _assert_subset(expected_entries: list[dict], actual_entries: list[dict], label: str) -> None:
    for expected in expected_entries:
        found = any(
            all(actual.get(key) == value for key, value in expected.items())
            for actual in actual_entries
        )
        assert found, f"expected {label} {expected} not found in {actual_entries}"


@pytest.mark.parametrize("fixture_dir", FIXTURE_DIRS, ids=FIXTURE_IDS)
def test_fixture_parser_assertions(fixture_dir: Path) -> None:
    if _model is None:
        pytest.skip("oaw.document.model is not implemented yet")

    expect = _load_expect(fixture_dir)
    parser_expect = expect.get("parser")
    if not parser_expect:
        pytest.skip("fixture carries no parser assertions")

    source = (fixture_dir / "source.md").read_bytes().decode("utf-8")
    document = _model.parse_note_source(source)

    if "headings" in parser_expect:
        _assert_subset(parser_expect["headings"], _actual_headings(document), "heading")
    if "protected" in parser_expect:
        _assert_subset(parser_expect["protected"], _actual_protected(document), "protected region")
    if "obsidian_spans" in parser_expect:
        _assert_subset(
            parser_expect["obsidian_spans"], _actual_obsidian_spans(document), "obsidian span"
        )
    if "diagnostics" in parser_expect:
        actual_codes = {diagnostic.code for diagnostic in document.diagnostics}
        missing = set(parser_expect["diagnostics"]) - actual_codes
        assert not missing, f"expected diagnostics {missing} not in {actual_codes}"
    if "newline" in parser_expect:
        mapping = {"lf": "\n", "crlf": "\r\n", "none": "\n"}
        assert document.newline == mapping[parser_expect["newline"]]


# --------------------------------------------------------------------------- #
# Editing assertions -- require oaw.document.editing on top of model.
# --------------------------------------------------------------------------- #


def _run_editing_op(document, case: dict):
    assert _editing is not None, "caller must skip before invoking editing ops"
    op = case["op"]
    if op == "append_block_to_section":
        return _editing.append_block_to_section(document, case["heading"], case["block"])
    if op == "set_frontmatter_scalar":
        return _editing.set_frontmatter_scalar(document, case["key"], case["value"])
    if op == "append_frontmatter_list_item":
        return _editing.append_frontmatter_list_item(document, case["key"], case["value"])
    if op == "remove_frontmatter_list_item":
        return _editing.remove_frontmatter_list_item(document, case["key"], case["value"])
    raise AssertionError(
        f"unknown editing op in fixture: {op}"
    )  # pragma: no cover - schema-guarded


@pytest.mark.parametrize("fixture_dir", FIXTURE_DIRS, ids=FIXTURE_IDS)
def test_fixture_editing_assertions(fixture_dir: Path) -> None:
    if _model is None or _editing is None:
        pytest.skip("oaw.document.model/editing is not implemented yet")

    expect = _load_expect(fixture_dir)
    editing_cases = expect.get("editing")
    if not editing_cases:
        pytest.skip("fixture carries no editing assertions")

    source = (fixture_dir / "source.md").read_bytes().decode("utf-8")
    document = _model.parse_note_source(source)

    for case in editing_cases:
        if case["expect"] == "error":
            with pytest.raises(OawError):
                _run_editing_op(document, case)
            continue

        result = _run_editing_op(document, case)
        if "result_contains" in case:
            assert case["result_contains"] in result.source
