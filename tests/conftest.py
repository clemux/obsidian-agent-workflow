from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Keep the perf node visible in the verifier's two-line collection tail."""
    mark_expression = config.getoption("markexpr", default="")
    if config.getoption("collectonly") and mark_expression.strip() == "perf":
        config.option.verbose = -2


def pytest_ignore_collect(collection_path: Path, config: pytest.Config) -> bool:
    """Collect either the default suite or the explicit perf module, never both."""
    mark_expression = config.getoption("markexpr", default="")
    is_test_module = collection_path.name.startswith("test_") and collection_path.suffix == ".py"
    is_perf_module = collection_path.name == "test_perf_resolver.py"
    if mark_expression.strip() == "perf":
        return is_test_module and not is_perf_module
    return is_perf_module
