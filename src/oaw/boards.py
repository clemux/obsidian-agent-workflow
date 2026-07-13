"""Board card rendering and command entrypoints."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from .errors import OawError

NEXT_BOARD = Path("Projects/Next steps.md")
PROJECT_BOARD_COLUMNS = ("Backlog", "Todo", "Active", "Review", "Done")


class ColumnEngine:
    """Apply the common heading and card placement rules used by OAW boards."""

    @staticmethod
    def find_column(lines: list[str], column: str, *, last: bool = False) -> int | None:
        found: int | None = None
        for index, line in enumerate(lines):
            heading = re.match(r"^##\s+(.+?)\s*$", line)
            if heading and heading.group(1) == column:
                if not last:
                    return index
                found = index
        return found

    @staticmethod
    def insert_before_ordered_column(
        lines: list[str], target_column: str, ordered_columns: tuple[str, ...]
    ) -> int:
        target_order = ordered_columns.index(target_column)
        for index, line in enumerate(lines):
            heading = re.match(r"^##\s+(.+?)\s*$", line)
            if (
                heading
                and heading.group(1) in ordered_columns
                and ordered_columns.index(heading.group(1)) > target_order
            ):
                return index
        return len(lines)

    @staticmethod
    def remove_cards(
        lines: list[str], matches: Callable[[str], bool]
    ) -> tuple[list[str], list[str]]:
        kept: list[str] = []
        cards: list[str] = []
        for line in lines:
            if matches(line):
                cards.append(line)
            else:
                kept.append(line)
        return kept, cards

    def insert_card(
        self,
        lines: list[str],
        column: str,
        card: str,
        *,
        ordered_columns: tuple[str, ...] | None = None,
        target_last: bool = False,
    ) -> list[str]:
        target_index = self.find_column(lines, column, last=target_last)
        updated = list(lines)
        if target_index is None:
            if ordered_columns is not None:
                insert_at = self.insert_before_ordered_column(updated, column, ordered_columns)
                updated[insert_at:insert_at] = [f"## {column}", "", card, ""]
            else:
                if updated and updated[-1] != "":
                    updated.append("")
                updated.extend([f"## {column}", "", card])
            return updated
        insert_at = target_index + 1
        while insert_at < len(updated) and updated[insert_at] == "":
            insert_at += 1
        updated.insert(insert_at, card)
        return updated


_COLUMNS = ColumnEngine()


def canonicalize_project_board_columns(lines: list[str]) -> list[str]:
    """Order known project columns while preserving unknown heading blocks in place."""
    heading_indices = [
        index for index, line in enumerate(lines) if re.match(r"^##\s+(.+?)\s*$", line)
    ]
    if not heading_indices:
        return lines
    blocks = [
        lines[
            start : heading_indices[position + 1] if position + 1 < len(heading_indices) else None
        ]
        for position, start in enumerate(heading_indices)
    ]

    def known_column(block: list[str]) -> str | None:
        heading = re.match(r"^##\s+(.+?)\s*$", block[0])
        column = heading.group(1) if heading else None
        return column if column in PROJECT_BOARD_COLUMNS else None

    columns = [known_column(block) for block in blocks]
    ordered = iter(
        block
        for column, block in sorted(
            (
                (column, block)
                for column, block in zip(columns, blocks, strict=True)
                if column is not None
            ),
            key=lambda item: PROJECT_BOARD_COLUMNS.index(item[0]),
        )
    )
    canonical = [
        next(ordered) if column is not None else block
        for column, block in zip(columns, blocks, strict=True)
    ]
    rendered = list(lines[: heading_indices[0]])
    for block in canonical:
        rendered.extend(block)
    return rendered


def project_card_line(task_path: Path, project_root: Path, title: str, note_id: str | None) -> str:
    rel_no_ext = task_path.relative_to(project_root).with_suffix("").as_posix()
    suffix = f" - {note_id}" if note_id else ""
    return f"- [ ] [[{rel_no_ext}|{title}]]{suffix}"


def project_column_for_status(status: str) -> str:
    return {
        "backlog": "Backlog",
        "todo": "Todo",
        "active": "Active",
        "review": "Review",
        "done": "Done",
    }[status]


def render_project_board(
    text: str,
    *,
    task_path: Path,
    project_root: Path,
    title: str,
    note_id: str | None,
    status: str,
) -> str:
    """Render a project board with the task card in its status column."""
    identifiers = {task_path.stem}
    if note_id:
        identifiers.add(note_id)
    lines, existing_cards = _COLUMNS.remove_cards(
        text.splitlines(),
        lambda line: line.startswith("- [ ] ") and any(token in line for token in identifiers),
    )
    card = (
        existing_cards[-1]
        if existing_cards
        else project_card_line(task_path, project_root, title, note_id)
    )
    rendered = canonicalize_project_board_columns(
        _COLUMNS.insert_card(
            lines,
            project_column_for_status(status),
            card,
            ordered_columns=PROJECT_BOARD_COLUMNS,
            target_last=True,
        )
    )
    return "\n".join(rendered) + "\n"


def move_project_board_card(
    project_root: Path, task_path: Path, title: str, note_id: str | None, status: str
) -> bool:
    """Move a task card on its project board, returning whether a board existed."""
    board = project_root / "Board.md"
    if not board.exists():
        return False
    text = board.read_text(encoding="utf-8")
    board.write_text(
        render_project_board(
            text,
            task_path=task_path,
            project_root=project_root,
            title=title,
            note_id=note_id,
            status=status,
        ),
        encoding="utf-8",
    )
    return True


def updated_project_board_text(
    project_root: Path, task_path: Path, title: str, note_id: str, status: str
) -> tuple[Path | None, str | None]:
    """Return a project board update without writing it, for multi-file transactions."""
    board = project_root / "Board.md"
    if not board.exists():
        return None, None
    return (
        board,
        render_project_board(
            board.read_text(encoding="utf-8"),
            task_path=task_path,
            project_root=project_root,
            title=title,
            note_id=note_id,
            status=status,
        ),
    )


def next_steps_board_path(root: Path) -> Path:
    board = root / NEXT_BOARD
    if not board.exists():
        raise OawError(f"board not found: {board}")
    return board


def next_steps_card(link: str, title: str, why: str, card_id: str) -> str:
    clean_link = link.strip().removesuffix(".md")
    clean_title = title.strip()
    clean_why = why.strip()
    clean_id = card_id.strip()
    if not clean_link or not clean_title or not clean_why or not clean_id:
        raise OawError("board add requires non-empty --link, --title, --why, and --id")
    return f"- [ ] [[{clean_link}|{clean_title}]] - {clean_why} ({clean_id})"


def render_next_steps_board(
    text: str,
    *,
    column: str,
    token: str | None,
    card: str | None,
    done: bool,
) -> str:
    """Render the Next steps board after adding or moving one card."""
    lines = text.splitlines()
    if token:
        lines, matches = _COLUMNS.remove_cards(
            lines,
            lambda line: bool(re.match(r"^-\s+\[[ xX]\]\s+", line)) and token in line,
        )
        if len(matches) > 1:
            raise OawError(f"multiple board cards match '{token}'")
        if not matches:
            raise OawError(f"no board card matches '{token}'")
        card = matches[0]
    if card is None:
        raise OawError("missing board card")
    marker = "- [x]" if done else "- [ ]"
    card = re.sub(r"^-\s+\[[ xX]\]", marker, card, count=1)
    return "\n".join(_COLUMNS.insert_card(lines, column, card)) + "\n"


def update_next_steps_board(
    root: Path,
    column: str,
    token: str | None,
    card: str | None,
    done: bool,
) -> None:
    """Write a Next steps board update and emit its stable command output."""
    path = next_steps_board_path(root)
    path.write_text(
        render_next_steps_board(
            path.read_text(encoding="utf-8"),
            column=column,
            token=token,
            card=card,
            done=done,
        ),
        encoding="utf-8",
    )
    print(f"Board: {NEXT_BOARD.as_posix()}")
    print(f"Column: {column}")
    if token:
        print(f"Matched: {token}")


def ensure_project_backlog_column(root: Path, project: str) -> None:
    """Add the project-board Backlog column if it is missing."""
    project_root = root / "Projects" / project
    if not project_root.exists():
        raise OawError(f"project not found: {project_root}")
    path = project_root / "Board.md"
    if not path.exists():
        raise OawError(f"project board not found: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if _COLUMNS.find_column(lines, "Backlog") is not None:
        print(f"Board: {path.relative_to(root).as_posix()}")
        print("Backlog: present")
        return
    insert_at = _COLUMNS.insert_before_ordered_column(lines, "Backlog", PROJECT_BOARD_COLUMNS)
    insertion = ["## Backlog", ""]
    if insert_at > 0 and lines[insert_at - 1] != "":
        insertion.insert(0, "")
    lines[insert_at:insert_at] = insertion
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Board: {path.relative_to(root).as_posix()}")
    print("Backlog: added")
