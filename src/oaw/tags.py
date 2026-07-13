"""Safe tag rendering for newly created notes.

This module deliberately does not mutate existing frontmatter.  Creation
commands may opt into its strict, deterministic YAML block-list output.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from .errors import OawError

SAFE_TAG = re.compile(r"^[a-z0-9]+(?:[._/-][a-z0-9]+)*$")


def creation_tags(defaults: Iterable[str], extras: Iterable[str] | None = None) -> list[str]:
    """Return safe tags in first-seen order, without duplicates.

    Tags are deliberately limited to lowercase path-like identifiers.  This
    keeps newly-created metadata portable and avoids YAML's ambiguous scalar
    forms before the values are JSON-quoted for serialization.
    """
    result: list[str] = []
    for raw in [*defaults, *(extras or [])]:
        if not isinstance(raw, str) or raw != raw.strip() or not raw or not SAFE_TAG.fullmatch(raw):
            raise OawError(
                "tag must be a lowercase safe identifier using letters, digits, '.', '-', or '/'"
            )
        tag = raw
        if tag not in result:
            result.append(tag)
    return result


def creation_tag_block(defaults: Iterable[str], extras: Iterable[str] | None = None) -> list[str]:
    """Serialize creation tags as a JSON-quoted YAML block list."""
    return [
        "tags:",
        *(f"  - {json.dumps(tag, ensure_ascii=False)}" for tag in creation_tags(defaults, extras)),
    ]
