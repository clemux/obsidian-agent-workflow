import datetime as dt

import pytest

from oaw.errors import OawError
from oaw.lifecycle import append_session_entry

EXISTING_SESSION = (
    "- 2026-07-13 - Claude Code - `CLAUDE_CODE_SESSION_ID=old-thread` - Existing entry."
)


def session_entry(note: str) -> str:
    today = dt.date.today().isoformat()
    return f"- {today} - Codex - `CODEX_THREAD_ID=test-thread` - {note}"


def append(text: str, note: str = "New entry.") -> str:
    return append_session_entry(text, "Codex", "CODEX_THREAD_ID=test-thread", note, None)


def test_append_session_entry_keeps_existing_entries_contiguous():
    original = f"## Agent sessions\n\n{EXISTING_SESSION}\n"

    result = append(original)

    assert result == f"## Agent sessions\n\n{EXISTING_SESSION}\n{session_entry('New entry.')}\n"


def test_append_session_entry_creates_section_at_eof():
    original = "# Note\n\nBody."

    result = append(original)

    assert result == f"# Note\n\nBody.\n\n## Agent sessions\n\n{session_entry('New entry.')}\n"


def test_append_session_entry_leaves_one_blank_line_before_following_heading():
    original = f"## Agent sessions\n\n{EXISTING_SESSION}\n\n## Decisions\n\nKeep this decision.\n"

    result = append(original)

    assert result == (
        "## Agent sessions\n\n"
        f"{EXISTING_SESSION}\n"
        f"{session_entry('New entry.')}\n\n"
        "## Decisions\n\n"
        "Keep this decision.\n"
    )


@pytest.mark.parametrize("blank_line", ["  ", "\t"])
def test_append_session_entry_trims_markdown_blank_section_tail(blank_line):
    original = (
        f"## Agent sessions\n\n{EXISTING_SESSION}\n{blank_line}\n"
        "## Decisions\n\nKeep this decision.  \n"
    )

    result = append(original)

    assert result == (
        "## Agent sessions\n\n"
        f"{EXISTING_SESSION}\n"
        f"{session_entry('New entry.')}\n\n"
        "## Decisions\n\n"
        "Keep this decision.  \n"
    )


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
def test_append_session_entry_preserves_following_suffix_exactly(line_ending):
    suffix = f"## Decisions{line_ending}{line_ending}Keep this hard break.  {line_ending}"
    original = (
        f"## Agent sessions{line_ending}{line_ending}"
        f"{EXISTING_SESSION}{line_ending}{line_ending}"
        f"{suffix}"
    )

    result = append(original)

    assert result == (
        f"## Agent sessions{line_ending}{line_ending}"
        f"{EXISTING_SESSION}{line_ending}"
        f"{session_entry('New entry.')}{line_ending}{line_ending}"
        f"{suffix}"
    )


def test_append_session_entry_is_stable_across_repeated_appends():
    original = f"## Agent sessions\n\n{EXISTING_SESSION}\n"

    once = append(original, "First append.")
    twice = append(once, "Second append.")

    assert once == f"## Agent sessions\n\n{EXISTING_SESSION}\n{session_entry('First append.')}\n"
    assert twice == (
        f"## Agent sessions\n\n{EXISTING_SESSION}\n"
        f"{session_entry('First append.')}\n{session_entry('Second append.')}\n"
    )


def test_append_session_entry_preserves_existing_section_content():
    original = (
        "## Agent sessions\n\n"
        "Some hand-authored context.\n\n"
        "- Reminder one.\n"
        "  \n"
        "- Reminder two.\n\n"
        "## Decisions\n\n"
        "Keep this decision.\n"
    )

    result = append(original)

    assert result == (
        "## Agent sessions\n\n"
        "Some hand-authored context.\n\n"
        "- Reminder one.\n"
        "  \n"
        "- Reminder two.\n\n"
        f"{session_entry('New entry.')}\n\n"
        "## Decisions\n\n"
        "Keep this decision.\n"
    )


def test_append_session_entry_ignores_heading_inside_indented_code():
    original = f"# Note\n\n    ## Something\n\n## Agent sessions\n\n{EXISTING_SESSION}\n"
    expected = (
        f"# Note\n\n    ## Something\n\n## Agent sessions\n\n{EXISTING_SESSION}\n"
        f"{session_entry('New entry.')}\n"
    )

    assert append(original) == expected


def test_append_session_entry_refuses_when_document_ends_inside_unclosed_fence():
    # The document layer refuses this append (category b): the heading-looking
    # text is inside a never-closed fence, so the real section is absent and the
    # fallback EOF insertion point sits inside that unclosed protected region --
    # legacy silently appended at EOF anyway, ignoring the still-open fence.
    original = "# Note\n\nBody.\n\n```\n## Agent sessions\n"

    with pytest.raises(OawError, match="unclosed protected region") as excinfo:
        append(original)
    # The refusal must name which construct is unclosed, not just that one is.
    assert "fence" in str(excinfo.value)


@pytest.mark.parametrize("blank_lines_after_heading", [0, 1, 2])
def test_append_session_entry_tolerates_existing_whitespace_variants(blank_lines_after_heading):
    gap = "\n" * blank_lines_after_heading
    original = f"## Agent sessions\n{gap}- Not a session entry.\n\n## Decisions\n\nKeep it.\n"

    result = append(original)

    before_entry = result.split(f"{session_entry('New entry.')}", 1)[0]
    after_entry = result.split(f"{session_entry('New entry.')}", 1)[1]
    assert before_entry.endswith("- Not a session entry.\n\n")
    assert after_entry == "\n\n## Decisions\n\nKeep it.\n"
