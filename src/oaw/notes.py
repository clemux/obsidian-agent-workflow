"""Pure note-boundary helpers and note reads."""

from __future__ import annotations

from pathlib import Path

from .frontmatter import parse_frontmatter


def split_note(text: str) -> tuple[str, str, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return "", "", text
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            before = "".join(lines[: idx + 1])
            fm = "".join(lines[1:idx])
            body = "".join(lines[idx + 1 :])
            return before, fm, body
    return "", "", text


def read_note(path: Path) -> tuple[str, str, str, dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    _, fm, body = split_note(text)
    return text, fm, body, parse_frontmatter(fm)
