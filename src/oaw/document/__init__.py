"""Source-preserving note document layer (markdown-it-py + PyYAML).

Public surface re-exported here: parsing entry points and the frozen models
returned by the four foundation layers (envelope, frontmatter, markdown,
Obsidian syntax) plus the whole-note assembly (:mod:`oaw.document.model`).
The lower-level pure functions (``scan_envelope``, ``compose_frontmatter``,
``parse_markdown``, the ``find_*`` recognizers) remain importable from their
own submodules for callers that only need one layer.
"""

from __future__ import annotations

from oaw.document.envelope import NoteEnvelope, scan_envelope
from oaw.document.frontmatter_yaml import (
    OWNED_FIELD_TYPES,
    FieldKind,
    FrontmatterField,
    FrontmatterModel,
    compose_frontmatter,
    validate_owned_fields,
)
from oaw.document.markdown import BlockRegion, Heading, MarkdownStructure, parse_markdown
from oaw.document.model import NoteDocument, ProtectedRegion, Section, parse_note, parse_note_source
from oaw.document.obsidian_syntax import (
    ObsidianSpan,
    ObsidianSpanKind,
    find_block_ids,
    find_comments,
    find_markdown_links,
    find_math,
    find_tags,
    find_wikilinks,
)
from oaw.document.types import Diagnostic, NewlineStyle, Severity, SourceIndex, SourceSpan

__all__ = [
    "OWNED_FIELD_TYPES",
    "BlockRegion",
    "Diagnostic",
    "FieldKind",
    "FrontmatterField",
    "FrontmatterModel",
    "Heading",
    "MarkdownStructure",
    "NewlineStyle",
    "NoteDocument",
    "NoteEnvelope",
    "ObsidianSpan",
    "ObsidianSpanKind",
    "ProtectedRegion",
    "Section",
    "Severity",
    "SourceIndex",
    "SourceSpan",
    "compose_frontmatter",
    "find_block_ids",
    "find_comments",
    "find_markdown_links",
    "find_math",
    "find_tags",
    "find_wikilinks",
    "parse_markdown",
    "parse_note",
    "parse_note_source",
    "scan_envelope",
    "validate_owned_fields",
]
