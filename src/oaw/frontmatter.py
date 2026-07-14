"""Hand-rolled frontmatter parsing and conservative mutation helpers."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .errors import OawError

FRONTMATTER_READ_LIMIT = 64 * 1024


def split_inline_comment(value: str) -> tuple[str, str]:
    """Split a YAML-style trailing comment while respecting quoted strings."""
    quote: str | None = None
    escaped = False
    for position, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if quote == '"' and character == "\\":
            escaped = True
            continue
        if character in {'"', "'"}:
            quote = None if quote == character else character if quote is None else quote
            continue
        if character == "#" and quote is None and position > 0 and value[position - 1].isspace():
            comment_start = position - 1
            while comment_start > 0 and value[comment_start - 1].isspace():
                comment_start -= 1
            return value[:comment_start].rstrip(), value[comment_start:]
    return value.rstrip(), ""


def unquote_scalar(value: str) -> str:
    value = value.strip()
    if (value.startswith("'") and value.endswith("'")) or (
        value.startswith('"') and value.endswith('"')
    ):
        return value[1:-1]
    return value


def parse_frontmatter(fm: str) -> dict[str, object]:
    data: dict[str, object] = {}
    current_list: str | None = None
    for raw in fm.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if current_list and re.match(r"^\s*-\s+", line):
            data.setdefault(current_list, [])
            item = re.sub(r"^\s*-\s+", "", line)
            cast = data[current_list]
            if isinstance(cast, list):
                cast.append(unquote_scalar(item))
            continue
        current_list = None
        if ":" not in line or line.startswith(" "):
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value, _ = split_inline_comment(value.strip())
        if not value:
            data[key] = []
            current_list = key
            continue
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            data[key] = [] if not inner else [unquote_scalar(p) for p in inner.split(",")]
        else:
            data[key] = unquote_scalar(value)
    return data


def frontmatter_may_match(frontmatter: str, target: str) -> bool:
    """Return whether raw frontmatter might contain an ID or alias match.

    This deliberately permits false positives.  Resolver performs the authoritative
    parsed comparison after this inexpensive check.
    """
    return target in frontmatter


def read_frontmatter_text(
    path: Path,
    *,
    max_bytes: int | None = FRONTMATTER_READ_LIMIT,
    require_closed: bool = True,
) -> str:
    """Read only the YAML frontmatter body with configurable safety behavior."""
    with path.open("r", encoding="utf-8") as handle:
        first = handle.readline()
        if first.strip() != "---":
            return ""
        total = len(first.encode("utf-8"))
        lines: list[str] = []
        for line in handle:
            total += len(line.encode("utf-8"))
            if max_bytes is not None and total > max_bytes:
                raise OawError(f"frontmatter too large or not closed before safety limit: {path}")
            if line.strip() == "---":
                return "".join(lines)
            lines.append(line)
    if require_closed:
        raise OawError(f"frontmatter is not closed: {path}")
    return ""


def read_frontmatter_only(path: Path) -> tuple[str, dict[str, object]]:
    """Read and parse a note's frontmatter without reading its body."""
    frontmatter = read_frontmatter_text(path)
    return frontmatter, parse_frontmatter(frontmatter)


def set_frontmatter_scalar(text: str, key: str, value: str) -> str:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise OawError("task note has no YAML frontmatter")
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end = idx
            break
    if end is None:
        raise OawError("task note frontmatter is not closed")
    pattern = re.compile(rf"^{re.escape(key)}\s*:")
    for idx in range(1, end):
        if pattern.match(lines[idx]):
            raw = lines[idx].rstrip("\r\n")
            _, comment = split_inline_comment(raw)
            newline = "\r\n" if lines[idx].endswith("\r\n") else "\n"
            lines[idx] = f"{key}: {value}{comment}{newline}"
            return "".join(lines)
    lines.insert(end, f"{key}: {value}\n")
    return "".join(lines)


def parse_yaml_string_list_item(raw: str, key: str) -> str:
    value = raw.strip()
    if value.startswith('"'):
        try:
            parsed, end = json.JSONDecoder().raw_decode(value)
        except json.JSONDecodeError as exc:
            raise OawError(f"{key} contains an invalid quoted string") from exc
        remainder = value[end:].strip()
        if not isinstance(parsed, str) or (remainder and not remainder.startswith("#")):
            raise OawError(f"{key} must contain only string list items")
        return parsed
    if value.startswith("'"):
        match = re.fullmatch(r"'((?:[^']|'')*)'\s*(?:#.*)?", value)
        if not match:
            raise OawError(f"{key} contains an invalid quoted string")
        return match.group(1).replace("''", "'")

    value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
    ambiguous = re.fullmatch(
        r"(?i:true|false|null|yes|no|on|off|~|[-+]?\d+(?:\.\d+)?|\d{4}-\d{2}-\d{2})",
        value,
    )
    if (
        not value
        or value.startswith(("[", "{", "&", "*", "!", "|", ">", "@", "`"))
        or re.search(r":\s", value)
        or ambiguous
    ):
        raise OawError(f"{key} must contain only unambiguous string list items")
    return value


def append_frontmatter_list_value(text: str, key: str, value: str) -> str:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise OawError("note has no YAML frontmatter")
    end = next((idx for idx in range(1, len(lines)) if lines[idx].strip() == "---"), None)
    if end is None:
        raise OawError("note frontmatter is not closed")

    pattern = re.compile(rf"^{re.escape(key)}\s*:")
    key_idx = next((idx for idx in range(1, end) if pattern.match(lines[idx])), None)
    if key_idx is None:
        lines[end:end] = [f"{key}:\n", f"  - {json.dumps(value, ensure_ascii=False)}\n"]
        return "".join(lines)

    _, inline_value = lines[key_idx].split(":", 1)
    if inline_value.strip():
        raise OawError(f"{key} must use a YAML block list before OAW can append safely")

    block_end = key_idx + 1
    while block_end < end and lines[block_end].startswith((" ", "\t")):
        block_end += 1
    existing: list[str] = []
    for line in lines[key_idx + 1 : block_end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = re.match(r"^\s+-\s+(.+?)\r?\n?$", line)
        if not item:
            raise OawError(f"{key} must be a flat YAML block list before OAW can append safely")
        existing.append(parse_yaml_string_list_item(item.group(1), key))
    if value in existing:
        return text
    lines.insert(block_end, f"  - {json.dumps(value, ensure_ascii=False)}\n")
    return "".join(lines)
