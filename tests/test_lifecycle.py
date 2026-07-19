import datetime as dt

import pytest

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


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        (
            "# Note\n\nBody.\n\n```\n## Agent sessions\n",
            (
                "# Note\n\nBody.\n\n```\n## Agent sessions\n"
                f"\n## Agent sessions\n\n{session_entry('New entry.')}\n"
            ),
        ),
        (
            f"# Note\n\n    ## Something\n\n## Agent sessions\n\n{EXISTING_SESSION}\n",
            (
                f"# Note\n\n    ## Something\n\n## Agent sessions\n\n{EXISTING_SESSION}\n"
                f"{session_entry('New entry.')}\n"
            ),
        ),
    ],
    ids=["unclosed-fence", "indented-code"],
)
def test_append_session_entry_ignores_headings_inside_fences(original, expected):
    assert append(original) == expected


@pytest.mark.parametrize("blank_lines_after_heading", [0, 1, 2])
def test_append_session_entry_tolerates_existing_whitespace_variants(blank_lines_after_heading):
    gap = "\n" * blank_lines_after_heading
    original = f"## Agent sessions\n{gap}- Not a session entry.\n\n## Decisions\n\nKeep it.\n"

    result = append(original)

    before_entry = result.split(f"{session_entry('New entry.')}", 1)[0]
    after_entry = result.split(f"{session_entry('New entry.')}", 1)[1]
    assert before_entry.endswith("- Not a session entry.\n\n")
    assert after_entry == "\n\n## Decisions\n\nKeep it.\n"
