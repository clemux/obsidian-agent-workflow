import os
import threading
from pathlib import Path
from typing import IO

import pytest

from oaw.notes import read_note, split_note, write_new_note_atomic


def test_split_note_returns_frontmatter_block_content_and_body():
    text = "---\nid: OAW-TSK-example\n---\n\n# Example\n"

    block, frontmatter, body = split_note(text)

    assert block == "---\nid: OAW-TSK-example\n---\n"
    assert frontmatter == "id: OAW-TSK-example\n"
    assert body == "\n# Example\n"


def test_split_note_leaves_plain_or_unclosed_notes_untouched():
    for text in ("# Plain\n", "---\nid: unclosed\n"):
        assert split_note(text) == ("", "", text)


def test_read_note_returns_text_sections(tmp_path: Path):
    path = tmp_path / "Example.md"
    text = "---\nid: example\naliases:\n  - one\n---\nBody\n"
    path.write_text(text, encoding="utf-8")

    assert read_note(path) == (text, "id: example\naliases:\n  - one\n", "Body\n")


@pytest.mark.parametrize("stage", ["write", "flush", "fsync"])
def test_write_new_note_atomic_cleans_temp_and_new_directories_on_stage_failure(
    tmp_path: Path, stage: str
):
    destination = tmp_path / "Agents/Feedback/failure.md"

    def fail_write(_handle: IO[str], _text: str) -> None:
        raise OSError("injected write failure")

    def fail_flush(_handle: IO[str]) -> None:
        raise OSError("injected flush failure")

    def fail_fsync(_fd: int) -> None:
        raise OSError("injected fsync failure")

    with pytest.raises(OSError, match=f"injected {stage} failure"):
        if stage == "write":
            write_new_note_atomic(destination, "complete note", write=fail_write)
        elif stage == "flush":
            write_new_note_atomic(destination, "complete note", flush=fail_flush)
        else:
            write_new_note_atomic(destination, "complete note", fsync=fail_fsync)
    assert not destination.exists()
    assert not (tmp_path / "Agents").exists()


def test_write_new_note_atomic_creates_parents_concurrently_and_allows_one_winner(
    tmp_path: Path,
):
    destination = tmp_path / "Agents/Feedback/race.md"
    start_links = threading.Barrier(2)
    results: list[BaseException | None] = [None, None]

    def link_after_barrier(source: str, target: str) -> None:
        start_links.wait(timeout=5)
        os.link(source, target)

    def create(index: int) -> None:
        try:
            write_new_note_atomic(destination, f"writer {index}", link=link_after_barrier)
        except BaseException as exc:  # Capture the competing FileExistsError for assertion.
            results[index] = exc

    threads = [threading.Thread(target=create, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, FileExistsError) for result in results) == 1
    assert destination.read_text(encoding="utf-8") in {"writer 0", "writer 1"}
    assert (tmp_path / "Agents/Feedback").is_dir()


def test_write_new_note_atomic_keeps_a_peer_created_ancestor_after_failure(tmp_path: Path):
    peer_directory = tmp_path / "Agents"
    destination = peer_directory / "Feedback/failure.md"

    def peer_creates_agents(directory: Path) -> None:
        if directory == peer_directory:
            directory.mkdir()
            raise FileExistsError("peer created this directory")
        directory.mkdir()

    def fail_link(_source: str, _destination: str) -> None:
        raise OSError("injected publication failure")

    with pytest.raises(OSError, match="injected publication failure"):
        write_new_note_atomic(
            destination,
            "complete note",
            link=fail_link,
            mkdir=peer_creates_agents,
        )
    assert peer_directory.is_dir()
    assert not (peer_directory / "Feedback").exists()
