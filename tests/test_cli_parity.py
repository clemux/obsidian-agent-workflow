import subprocess
import sys
import tempfile
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from shutil import copytree

from scripts import check_cli_parity as parity

from .assertions import Assertions

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_cli_parity.py"
BIN = ROOT / "bin" / "oaw"


class TestCliParity(Assertions):
    def write_package_launcher(self, root: Path, shebang: str) -> Path:
        installed = root / "bin" / "oaw"
        installed.parent.mkdir()
        copytree(ROOT / "src", root / "src")
        installed.write_text(
            f"{shebang}\n"
            "import sys\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n"
            "from oaw.cli import main\n"
            "raise SystemExit(main())\n",
            encoding="utf-8",
        )
        installed.chmod(0o755)
        return installed

    def write_minimal_cli(self, path: Path, parser_body: str) -> Path:
        path.write_text(
            "#!/usr/bin/env python3\n"
            "import argparse\n"
            "parser = argparse.ArgumentParser(prog='oaw')\n"
            f"{parser_body}\n"
            "parser.parse_args()\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path

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
            installed = self.write_package_launcher(root, "#!/usr/bin/env python3")
            installed_source = parity.source_path(
                parity.command_prefix(str(installed), "installed")
            )

        self.assertEqual(installed_source, (root / "src" / "oaw" / "cli.py").resolve())

    def test_absolute_python_shebang_aligns_checkout_interpreter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            installed = self.write_package_launcher(root, f"#!{sys.executable}")
            checkout_prefix = parity.command_prefix(str(BIN), "checkout")
            installed_prefix = parity.command_prefix(str(installed), "installed")
            aligned = parity.align_checkout_interpreter(
                checkout_prefix,
                installed_prefix,
            )

        self.assertEqual(aligned, [sys.executable, str(BIN.resolve())])

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
            root = Path(tmp)
            checkout = self.write_minimal_cli(
                root / "checkout_oaw.py",
                "parser.add_subparsers(dest='command', required=True).add_parser('resolve')",
            )
            stale = self.write_minimal_cli(root / "stale_oaw.py", "")
            _, failures = parity.compare_surfaces(
                [sys.executable, str(checkout)],
                [sys.executable, str(stale)],
            )

        self.assertTrue(failures)
        self.assertIn("Mismatch: oaw --help", failures[0])

    def test_wrong_launcher_entry_point_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkout = self.write_minimal_cli(root / "checkout_oaw.py", "")
            installed = root / "oaw"
            installed.write_text(
                "#!/usr/bin/env python3\nfrom oaw.missing import main\nraise SystemExit(main())\n",
                encoding="utf-8",
            )
            installed.chmod(0o755)
            stderr = StringIO()
            with redirect_stderr(stderr):
                returncode = parity.main(
                    ["--checkout", str(checkout), "--installed", str(installed)]
                )

        self.assertNotEqual(returncode, 0)
        self.assertIn("Mismatch: oaw --help", stderr.getvalue())
        self.assertIn("Parity: failed", stderr.getvalue())

    def test_matching_help_with_stale_source_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stale = self.write_package_launcher(root, "#!/usr/bin/env python3")
            cli = root / "src" / "oaw" / "cli.py"
            cli.write_text(
                cli.read_text(encoding="utf-8") + "\n# stale installed source\n",
                encoding="utf-8",
            )
            checkout_prefix = parity.command_prefix(str(BIN), "checkout")
            installed_prefix = parity.command_prefix(str(stale), "installed")
            checkout_prefix = parity.align_checkout_interpreter(
                checkout_prefix,
                installed_prefix,
            )
            checkout_help = parity.help_result(checkout_prefix, ())
            installed_help = parity.help_result(installed_prefix, ())
            mismatch = parity.source_failure(checkout_prefix, installed_prefix)

        self.assertEqual(checkout_help.returncode, installed_help.returncode)
        self.assertEqual(checkout_help.stdout, installed_help.stdout)
        self.assertEqual(checkout_help.stderr, installed_help.stderr)
        self.assertTrue(mismatch)
        self.assertIn("Source mismatch: installed artifact does not match checkout", mismatch or "")
