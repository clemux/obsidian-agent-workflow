from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import check_publication_boundary as boundary


class PublicationBoundaryTests(unittest.TestCase):
    def test_detects_personal_home_paths_and_local_markers(self) -> None:
        content = "\n".join(
            (
                "/home/" + "alice/private/file.txt",
                "/Users/" + "Alice/Documents/private.md",
                "C:\\Users\\" + "Alice\\Documents\\private.md",
                "Work on " + "PRIVATE-TSK-history",
                "vault=" + "alice-personal",
            )
        )
        rules = boundary.BASE_RULES + boundary.build_local_rules(["PRIVATE"], ["alice-personal"])

        matches = boundary.find_matches({"sample.md": content}, rules)

        self.assertEqual(
            [match.rule for match in matches],
            [
                "personal-home",
                "personal-home",
                "personal-home",
                "private-reference-id",
                "private-marker",
            ],
        )

    def test_allows_documented_generic_home_placeholders(self) -> None:
        content = "\n".join(
            (
                "/home/user/project",
                "/home/<username>/project",
                "/Users/example/project",
                "C:\\Users\\User\\project",
            )
        )

        self.assertEqual(boundary.find_matches({"sample.md": content}, boundary.BASE_RULES), [])

    def test_missing_local_config_disables_local_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(boundary.load_local_rules(Path(directory)), ())

    def test_loads_local_rules(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / boundary.LOCAL_CONFIG_NAME).write_text(
                json.dumps(
                    {
                        "version": 1,
                        "private_reference_prefixes": ["PRIVATE"],
                        "private_markers": ["Private Project"],
                    }
                ),
                encoding="utf-8",
            )

            rules = boundary.load_local_rules(root)
            matches = boundary.find_matches(
                {"sample.md": "PRIVATE-TSK-example\nPrivate Project"}, rules
            )

            self.assertEqual(
                [match.rule for match in matches],
                ["private-reference-id", "private-marker"],
            )


if __name__ == "__main__":
    unittest.main()
