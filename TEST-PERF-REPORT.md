# Test Suite Performance: Analysis & Fixes

**Date:** 2026-07-20
**Result:** 44.14s → 44.14s (no committed changes — see Recommendations for a measured 6.6x lever that is out of scope for this run)

## Baseline

- Test command: `uv run pytest tests --durations=20 -q`
- Total tests: 472 passed, 0 failed, 0 skipped
- Wall time: 44.14s (second of two runs; first run was 44.37s — consistent, no warm-up effect worth noting)

## Methodology

1. `pytest tests --durations=20 -q` (run twice; second run used as baseline)
2. `pytest tests --collect-only -q` + `time` — collection/import overhead
3. `pytest tests --durations=0 --durations-min=0.0` — full per-phase breakdown, aggregated by file and phase with a small script
4. `pytest -k <test> --setup-show` — fixture chain trace on the slowest test
5. `pyinstrument -r text -m pytest -k <test> -p no:xdist` — CPU/wait call tree on the slowest test
6. `python -X importtime bin/oaw --help` — import cost breakdown of the CLI entry point
7. Direct repeated timing of `python bin/oaw --help` (bare subprocess spawn cost)
8. Micro-benchmark script comparing subprocess spawn cost with full vs. minimal environment
9. Ephemeral measurement only: `uv run --with pytest-xdist pytest tests -n auto` (not installed as a dependency; run twice to confirm)

## Profiling Findings

| Component | Time | Scope | Notes |
|---|---|---|---|
| Collection | 0.24s elapsed | one-time | 472 tests collected in 0.08s; negligible relative to 44s wall time |
| Coverage tax | n/a | — | `pytest-cov` is a declared dev dependency but is **not** in `addopts`; plain `pytest` pays no coverage overhead, so the `--no-cov` comparison step didn't apply |
| `tests/test_oaw.py` (231 tests) | 32.57s call time | 74% of suite | Nearly every test calls `self.run_oaw()`, which does `subprocess.run([sys.executable, bin/oaw, ...])`; 250 call sites in this file, several tests call it 3–7 times |
| Per-subprocess spawn cost | ~90ms (90.1–90.5ms across 3 runs) | fixed, paid per call | Measured directly; confirmed with `pyinstrument`: 0.973s of a 1.186s single-test run is `TestOaw.run_oaw → subprocess.run → poll.poll` (process wait, not CPU) |
| `python -X importtime bin/oaw --help` | ~45ms cumulative | paid on every spawn | `oaw.cli` 36.7ms (includes `typer` 20.6ms nested), `site`/`_virtualenv` ~6.5ms — inherent interpreter+import cost of the CLI itself |
| `tests/test_cli_parity.py` (8 tests) | 2.71s | — | `test_matching_cli_surfaces_pass` alone is 1.5–1.56s: it walks the entire CLI help-surface tree via `scripts/check_cli_parity.py`, spawning one subprocess per subcommand |
| `tests/test_captures.py` / `test_relations.py` / `test_feedback.py` | 1.74s / 1.28s / 0.78s | in-process (`tmp_path`) | No subprocess use; per-test cost is real disk I/O + YAML/frontmatter parsing, not a fixture bottleneck — aggregate `setup` phase time across all 472 tests is 0.00s |
| Env size vs. spawn cost | 90.5ms (84 vars) vs. 91.2ms (3 vars) | ruled out | Micro-benchmark shows environment-dict size has no measurable effect on subprocess spawn time |
| `pytest-xdist -n auto` (24 cores, ephemeral `uv run --with`) | 6.63s, then 6.70s | measured, **not applied** | ~6.6x speedup, same 472 passed both times; blocked because `pytest-xdist` is not a declared project dependency |

## Bottleneck Breakdown (estimated)

- **Subprocess spawn overhead** (≈280 `subprocess.run` calls × ~90ms fixed interpreter/import cost, across `test_oaw.py`, `test_cli_parity.py`, `test_catalog.py`, `test_claude_session_title_hook.py`): ~25s (~57%)
- **Actual CLI logic inside those same subprocesses** (wall time beyond a bare `--help`): ~10s (~23%)
- **In-process tests** (`test_captures.py`, `test_relations.py`, `test_feedback.py`, `test_typer_cli.py`, etc.): ~3.8s (~9%)
- **Collection, teardown, misc**: <1s (~2%)
- **Identified waste:** effectively none. No coverage tax, no bcrypt/RSA/DB/network hotspots, `setup_method` fixture cost is 0.00s in aggregate.

## Fixes

**No fixes were applied.**

This suite does not exhibit any of the profiling skill's common hotspots: no `--cov` in `addopts`, no `bcrypt`/RSA key generation, no database `TRUNCATE`/`create_all`, no web-framework `TestClient`, no slow module-level imports beyond the CLI's own unavoidable `typer` import, and no widenable function-scoped fixture (the `setup_method` vault-fixture cost is 0.00s in aggregate across all 472 tests — file writes to a tmpfs-backed tempdir are essentially free).

The dominant cost (≈75–80% of wall time) is ~280 `subprocess.run([sys.executable, bin/oaw, ...])` calls, used deliberately to exercise the real CLI launcher end-to-end (exit codes, stderr routing, shebang handling). `AGENTS.md` documents this as an intentional split: native in-process Typer-contract coverage lives in `tests/test_typer_cli.py`, while `test_oaw.py`/`test_cli_parity.py` test actual process-level launcher behavior. Two levers with real teeth were identified and measured, but both are out of scope for this run:

1. **pytest-xdist** — requires adding an undeclared dependency (task constraint: recommend only).
2. **Converting some subprocess tests to in-process `cli.main()` calls** — would reduce spawn count, but changes what is being verified for ~250 tests (process-level launcher behavior vs. library-call behavior); this is a test-architecture decision, not a mechanical profiling fix, and needs maintainer review per test.

### Combined Result

| State | Wall time | Delta |
|---|---|---|
| Baseline | 44.14s | — |
| No fixes applied (final state) | 44.14s | — |
| *xdist, measured but not applied (blocked by scope)* | *6.63s / 6.70s* | *~-37.5s (≈6.6x)* |

## Remaining Slow Tests

| Test | Time | Reason |
|---|---|---|
| `test_cli_parity.py::test_matching_cli_surfaces_pass` | 1.5–1.56s | Walks the entire CLI help-surface tree; one subprocess spawn per subcommand |
| `test_oaw.py::test_durable_prose_writes_share_obs_materialization` | 0.94–0.97s | 3 sequential `run_oaw()` subprocess spawns |
| `test_oaw.py::test_research_start_rejects_unsafe_duplicate_and_non_http_sources` | 0.60–0.61s | 7 sequential `run_oaw()` subprocess spawns |
| `test_oaw.py::test_run_list_filters_by_session_and_current_session` | 0.58–0.61s | 7 sequential `run_oaw()` subprocess spawns |
| `test_oaw.py::test_boardless_project_task_lifecycle_never_creates_a_board` | 0.57s | 3 sequential `run_oaw()` subprocess spawns |
| ≈245 more `test_oaw.py::test_*` | ~0.09–0.4s each | Single `run_oaw()` subprocess spawn; cost is fixed interpreter+import overhead, not test logic |

## Test Hygiene (Phase 2.5)

Consolidation candidates were sought by grouping the 250 `run_oaw()` call sites in `test_oaw.py` by shared literal CLI-argument prefixes. Six groups of 3+ tests shared a command prefix (e.g. `session lookup`, `task start ... OAW-TSK-cli --note Start.`, `ingest safe-export --ingestion-root`). The two largest groups were read in full:

- **`session lookup --verbose` (7 tests)** — e.g. `test_session_lookup_verbose_reports_codex_metrics`, `test_session_lookup_verbose_reports_vault_and_codex_matches`, `test_session_lookup_verbose_marks_missing_and_unsupported_metrics_unavailable`. Each uses different fixture data (different `.jsonl` rollout fixtures, different vault frontmatter, presence/absence of a Claude session file). Different preconditions per test — **not a consolidation candidate** (matches the skill's exclusion: "different fixture state, different preconditions").
- **`list --project "Obsidian Agent Workflow"` (3 tests)** — `test_list_tasks_preserves_archived_rows`, `test_list_default_output_unchanged_by_new_flags`, `test_list_accepts_project_aliases`: each verifies a distinct behavioral contract (archived-row inclusion, flag-stability, alias resolution) — **not a consolidation candidate**.

No "same setup, different assertions" or "one atomic function, many tests" duplication was found in the sampled groups. Shared-prefix tests consistently diverge in fixture state or exercise distinct validation branches (test names carry `_rejects_`, `_refuses_`, `_requires_` — each a separate guard clause). **No consolidation is recommended** for this suite as profiled. (No tests were deleted or merged, per task constraints.)

## Recommendations (not applied — out of scope for this run)

1. **Adopt pytest-xdist.** Add as a dev dependency and run local/CI iterations with `-n auto` (or the worker count CI actually has). Measured 6.6x speedup (44.14s → 6.63s/6.70s across two independent runs, same 472 passed) with zero test-code changes — the single highest-leverage change available for this suite. Before adopting: confirm no two tests share global state (they don't appear to — each gets its own `tempfile.TemporaryDirectory()` vault and subprocess), and re-profile without `-n` if `--durations`/`pyinstrument` output is needed again later.
2. **Reconsider subprocess-vs-in-process architecture for CLI tests.** `tests/test_oaw.py` has ~250 tests paying a fixed ~90ms Python-interpreter + `typer`/`oaw.cli` import cost per subprocess spawn (confirmed via `pyinstrument` and `-X importtime`), and several tests spawn 3–7 subprocesses each. Only a handful of tests in `test_cli_parity.py` specifically target launcher/shebang-level concerns that require a real subprocess; if most of `test_oaw.py`'s coverage doesn't require process-level isolation, converting a subset to in-process `cli.main()` calls (as `test_cli_main_accepts_argv_and_returns_status_code` already does) would cut wall time further. This changes what each converted test verifies, so it needs per-test review and maintainer sign-off — not a mechanical fix.
