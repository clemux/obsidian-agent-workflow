import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_cli_parity.py"
BIN = ROOT / "bin" / "oaw"


class CliParityTests(unittest.TestCase):
    def run_check(self, installed: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--checkout",
                str(BIN),
                "--installed",
                str(installed),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_matching_cli_surfaces_pass(self):
        proc = self.run_check(BIN)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertRegex(proc.stdout, r"Parity: ok \(\d+ help surfaces\)")

    def test_stale_cli_surface_fails_with_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp) / "stale_oaw.py"
            stale.write_text(
                "import argparse\n"
                "parser = argparse.ArgumentParser(prog='oaw')\n"
                "parser.add_subparsers(dest='command', required=True).add_parser('resolve')\n"
                "parser.parse_args()\n",
                encoding="utf-8",
            )
            proc = self.run_check(stale)

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Mismatch: oaw --help", proc.stderr)
        self.assertIn("Parity: failed", proc.stderr)


if __name__ == "__main__":
    unittest.main()
