"""Opt-in performance gates for the resolver extraction ladder."""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from oaw.resolver import (
    NoteMatch,
    resolve_id,
    resolve_id_raw_prefilter,
    resolve_id_unoptimized,
)

from .perf_fixture import TARGET_ID, generate_resolver_vault

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_PATH = ROOT / ".codex-evidence" / "perf-step2.json"
RUNS_PER_RUNG = 5


def median_ms(resolver: Callable[[str, Path], NoteMatch], root: Path) -> float:
    samples: list[float] = []
    for _ in range(RUNS_PER_RUNG):
        started = time.perf_counter()
        match = resolver(TARGET_ID, root)
        elapsed = (time.perf_counter() - started) * 1_000
        assert match.note_id == TARGET_ID
        samples.append(elapsed)
    return statistics.median(samples)


@pytest.mark.perf
def test_resolver_performance_ladder_records_and_gates_each_rung():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        generate_resolver_vault(root)
        rungs = [
            ("baseline", resolve_id_unoptimized),
            ("raw-pre-filter", resolve_id_raw_prefilter),
            ("frontmatter-only-reads", resolve_id),
            ("single-scan-writes", resolve_id),
        ]
        measurements = [
            {"name": name, "median_ms": median_ms(resolver, root)} for name, resolver in rungs
        ]

    EVIDENCE_PATH.parent.mkdir(exist_ok=True)
    EVIDENCE_PATH.write_text(
        json.dumps(
            {"baseline_ms": measurements[0]["median_ms"], "rungs": measurements},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    for previous, current in zip(measurements, measurements[1:], strict=False):
        assert current["median_ms"] <= previous["median_ms"] * 1.10, (
            f"{current['name']} regressed more than 10% from {previous['name']}"
        )
    best = min(rung["median_ms"] for rung in measurements)
    final = measurements[-1]["median_ms"]
    assert final < 150
    assert final <= best * 1.10
