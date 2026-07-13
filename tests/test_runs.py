import datetime as dt
from pathlib import Path

import pytest

from oaw.errors import OawError
from oaw.notes import VaultTransaction
from oaw.runs import Identity, Run, is_stale, run_id


def test_run_id_is_deterministic_and_session_scoped():
    first = Identity("codex", "Codex", "session-a", "CODEX_THREAD_ID")
    second = Identity("codex", "Codex", "session-b", "CODEX_THREAD_ID")
    assert run_id("OAW-TSK-example", first) == run_id("OAW-TSK-example", first)
    assert run_id("OAW-TSK-example", first).startswith("AGT-RUN-OAW-TSK-example-codex-")
    assert len(run_id("OAW-TSK-example", first).rsplit("-", 1)[1]) == 12
    assert run_id("OAW-TSK-example", first) != run_id("OAW-TSK-example", second)


def test_stale_boundary_is_strictly_more_than_24_hours(tmp_path: Path):
    now = dt.datetime(2026, 7, 12, 12, tzinfo=dt.timezone.utc)
    run = Run(tmp_path / "run.md", {"last_event_at": "2026-07-11T12:00:00Z"}, "")
    assert not is_stale(run, now)
    assert is_stale(run, now + dt.timedelta(seconds=1))


def test_transaction_rolls_back_existing_and_created_files(tmp_path: Path):
    existing = tmp_path / "task.md"
    created = tmp_path / "run.md"
    existing.write_text("before", encoding="utf-8")
    tx = VaultTransaction()
    tx.stage(existing, "after")
    tx.stage(created, "new")
    calls = 0

    def fail_second(source: str, destination: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected failure")
        Path(source).replace(destination)

    with pytest.raises(OawError, match="rolled back"):
        tx.commit(replace=fail_second)
    assert existing.read_text(encoding="utf-8") == "before"
    assert not created.exists()
