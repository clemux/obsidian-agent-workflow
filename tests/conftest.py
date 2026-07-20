"""Pytest fixtures wrapping tests/support.py.

Fixtures stay intentionally thin: all vault construction, process emulation, and
snapshot logic lives in ``tests.support`` so non-fixture callers can use it too.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tests import support


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A bare, empty vault root under tmp_path."""
    return support.make_vault(tmp_path)


@pytest.fixture
def legacy_vault(tmp_path: Path) -> Path:
    """The full nine-note legacy fixture tree from test_oaw.py's setup_method."""
    return support.build_legacy_vault(tmp_path)


@pytest.fixture
def base_env(legacy_vault: Path) -> dict[str, str]:
    """OAW_VAULT + CODEX_THREAD_ID over the ambient environment, like setup_method."""
    env = os.environ.copy()
    env["OAW_VAULT"] = str(legacy_vault)
    env["CODEX_THREAD_ID"] = "test-thread"
    return env


@pytest.fixture
def run_oaw(
    base_env: dict[str, str],
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """Return ``run(*args, env=None)`` that merges base_env and runs cli.main in-process."""

    def run(*args: object, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        merged = base_env.copy()
        if env:
            merged.update(env)
        return support.run_oaw_in_process([str(arg) for arg in args], merged)

    return run
