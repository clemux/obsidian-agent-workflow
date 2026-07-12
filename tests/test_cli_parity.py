import subprocess
import sys
import tempfile
from pathlib import Path
from shutil import copytree

from .assertions import Assertions

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_cli_parity.py"
BIN = ROOT / "bin" / "oaw"


class TestCliParity(Assertions):
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
            capture_output=True,
            check=False,
        )

    def test_matching_cli_surfaces_pass(self):
        proc = self.run_check(BIN)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertRegex(proc.stdout, r"Parity: ok \(\d+ help surfaces\)")

    def test_current_installed_launcher_resolves_package_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            installed = root / "bin" / "oaw"
            installed.parent.mkdir()
            copytree(ROOT / "src", root / "src")
            installed.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "from pathlib import Path\n"
                "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
                "from oaw.cli import main\n"
                "raise SystemExit(main())\n",
                encoding="utf-8",
            )
            installed.chmod(0o755)
            proc = self.run_check(installed)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertRegex(proc.stdout, r"Parity: ok \(\d+ help surfaces\)")

    def test_absolute_python_shebang_aligns_checkout_interpreter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            installed = root / "bin" / "oaw"
            installed.parent.mkdir()
            copytree(ROOT / "src", root / "src")
            installed.write_text(
                f"#!{sys.executable}\n"
                "import sys\n"
                "from pathlib import Path\n"
                "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
                "from oaw.cli import main\n"
                "raise SystemExit(main())\n",
                encoding="utf-8",
            )
            installed.chmod(0o755)
            proc = self.run_check(installed)

        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_non_python_launcher_shebang_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            installed = Path(tmp) / "oaw"
            installed.write_text(
                "#!/usr/bin/env bash\nfrom oaw.cli import main\n",
                encoding="utf-8",
            )
            installed.chmod(0o755)
            proc = self.run_check(installed)

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("launcher shebang is not Python", proc.stderr)

    def test_malformed_env_shebang_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            installed = Path(tmp) / "oaw"
            installed.write_text(
                "#!/usr/bin/env -S\nfrom oaw.cli import main\n",
                encoding="utf-8",
            )
            installed.chmod(0o755)
            proc = self.run_check(installed)

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("malformed env shebang", proc.stderr)

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

    def test_wrong_launcher_entry_point_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            installed = Path(tmp) / "oaw"
            installed.write_text(
                "#!/usr/bin/env python3\nfrom oaw.missing import main\nraise SystemExit(main())\n",
                encoding="utf-8",
            )
            installed.chmod(0o755)
            proc = self.run_check(installed)

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Mismatch: oaw --help", proc.stderr)
        self.assertIn("Parity: failed", proc.stderr)

    def test_matching_help_with_stale_source_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale = root / "bin" / "oaw"
            stale.parent.mkdir()
            copytree(ROOT / "src", root / "src")
            stale.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "from pathlib import Path\n"
                "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
                "from oaw.cli import main\n"
                "raise SystemExit(main())\n",
                encoding="utf-8",
            )
            stale.chmod(0o755)
            cli = root / "src" / "oaw" / "cli.py"
            cli.write_text(
                cli.read_text(encoding="utf-8") + "\n# stale installed source\n",
                encoding="utf-8",
            )
            proc = self.run_check(stale)

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Source mismatch: installed artifact does not match checkout", proc.stderr)
        self.assertNotIn("Mismatch: oaw --help", proc.stderr)
