"""Small assertion compatibility helpers for the mechanical pytest migration."""

from __future__ import annotations

import re
from contextlib import nullcontext
from typing import Any


class Assertions:
    def assertEqual(self, left: Any, right: Any, message: str | None = None) -> None:
        assert left == right, message or f"{left!r} != {right!r}"

    def assertNotEqual(self, left: Any, right: Any, message: str | None = None) -> None:
        assert left != right, message or f"{left!r} == {right!r}"

    def assertIn(self, member: Any, container: Any) -> None:
        assert member in container, f"{member!r} not found in {container!r}"

    def assertNotIn(self, member: Any, container: Any) -> None:
        assert member not in container, f"{member!r} unexpectedly found in {container!r}"

    def assertTrue(self, value: Any) -> None:
        assert value

    def assertFalse(self, value: Any) -> None:
        assert not value

    def assertLess(self, left: Any, right: Any) -> None:
        assert left < right, f"{left!r} is not less than {right!r}"

    def assertGreater(self, left: Any, right: Any) -> None:
        assert left > right, f"{left!r} is not greater than {right!r}"

    def assertIsNone(self, value: Any) -> None:
        assert value is None, f"{value!r} is not None"

    def assertRegex(self, value: str, pattern: str) -> None:
        assert re.search(pattern, value), f"{pattern!r} does not match {value!r}"

    def subTest(self, **_context: Any):  # noqa: N802
        return nullcontext()
