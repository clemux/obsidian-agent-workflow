"""Deterministic temporary-vault fixture used by opt-in resolver benchmarks."""

from __future__ import annotations

from pathlib import Path

NOTE_COUNT = 5_000
TARGET_ID = "PERF-TARGET"
_BODY = "The resolver benchmark body is intentionally ignored by frontmatter-only reads.\n" * 20


def generate_resolver_vault(root: Path) -> Path:
    """Create exactly 5,000 deterministic markdown notes and return the target path."""
    target_path: Path | None = None
    for index in range(NOTE_COUNT):
        note_id = TARGET_ID if index == NOTE_COUNT - 1 else f"PERF-NOTE-{index:04d}"
        path = root / f"Bucket-{index % 50:02d}" / f"Note-{index:04d}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nid: {note_id}\naliases:\n  - PERF-ALIAS-{index:04d}\n---\n\n"
            f"# Benchmark note {index}\n\n{_BODY}",
            encoding="utf-8",
        )
        if note_id == TARGET_ID:
            target_path = path
    assert target_path is not None
    return target_path
