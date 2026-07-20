import re
import subprocess
import sys
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from shutil import copytree

from scripts import check_cli_parity as parity

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_cli_parity.py"
BIN = ROOT / "bin" / "oaw"


def write_package_launcher(root: Path, shebang: str) -> Path:
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


def write_minimal_cli(path: Path, parser_body: str) -> Path:
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


def run_check(installed: Path) -> subprocess.CompletedProcess[str]:
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


def test_matching_cli_surfaces_pass():
    proc = run_check(BIN)

    assert proc.returncode == 0, proc.stderr
    assert re.search(r"Parity: ok \(\d+ help surfaces\)", proc.stdout)


def test_current_installed_launcher_resolves_package_source(tmp_path: Path):
    installed = write_package_launcher(tmp_path, "#!/usr/bin/env python3")
    installed_source = parity.source_path(parity.command_prefix(str(installed), "installed"))

    assert installed_source == (tmp_path / "src" / "oaw" / "cli.py").resolve()


def test_absolute_python_shebang_aligns_checkout_interpreter(tmp_path: Path):
    installed = write_package_launcher(tmp_path, f"#!{sys.executable}")
    checkout_prefix = parity.command_prefix(str(BIN), "checkout")
    installed_prefix = parity.command_prefix(str(installed), "installed")
    aligned = parity.align_checkout_interpreter(
        checkout_prefix,
        installed_prefix,
    )

    assert aligned == [sys.executable, str(BIN.resolve())]


def test_non_python_launcher_shebang_fails_clearly(tmp_path: Path):
    installed = tmp_path / "oaw"
    installed.write_text(
        "#!/usr/bin/env bash\nfrom oaw.cli import main\n",
        encoding="utf-8",
    )
    installed.chmod(0o755)
    proc = run_check(installed)

    assert proc.returncode != 0
    assert "launcher shebang is not Python" in proc.stderr


def test_malformed_env_shebang_fails_clearly(tmp_path: Path):
    installed = tmp_path / "oaw"
    installed.write_text(
        "#!/usr/bin/env -S\nfrom oaw.cli import main\n",
        encoding="utf-8",
    )
    installed.chmod(0o755)
    proc = run_check(installed)

    assert proc.returncode != 0
    assert "malformed env shebang" in proc.stderr


def test_stale_cli_surface_fails_with_diff(tmp_path: Path):
    checkout = write_minimal_cli(
        tmp_path / "checkout_oaw.py",
        "parser.add_subparsers(dest='command', required=True).add_parser('resolve')",
    )
    stale = write_minimal_cli(tmp_path / "stale_oaw.py", "")
    _, failures = parity.compare_surfaces(
        [sys.executable, str(checkout)],
        [sys.executable, str(stale)],
        [(), ("resolve",)],
    )

    assert failures
    assert "Mismatch: oaw --help" in failures[0]


def test_wrong_launcher_entry_point_fails(tmp_path: Path):
    checkout = write_minimal_cli(tmp_path / "checkout_oaw.py", "")
    installed = tmp_path / "oaw"
    installed.write_text(
        "#!/usr/bin/env python3\nfrom oaw.missing import main\nraise SystemExit(main())\n",
        encoding="utf-8",
    )
    installed.chmod(0o755)
    stderr = StringIO()
    with redirect_stderr(stderr):
        returncode = parity.main(["--checkout", str(checkout), "--installed", str(installed)])

    assert returncode != 0
    assert "Mismatch: oaw --help" in stderr.getvalue()
    assert "Parity: failed" in stderr.getvalue()


def test_matching_help_with_stale_source_fails(tmp_path: Path):
    checkout = write_minimal_cli(tmp_path / "checkout_oaw.py", "")
    stale = write_minimal_cli(tmp_path / "stale_oaw.py", "")
    stale.write_text(
        stale.read_text(encoding="utf-8") + "\n# stale installed source\n",
        encoding="utf-8",
    )
    stderr = StringIO()
    with redirect_stderr(stderr):
        returncode = parity.main(["--checkout", str(checkout), "--installed", str(stale)])

    assert returncode != 0
    assert "Source mismatch: installed artifact does not match checkout" in stderr.getvalue()
    assert "Mismatch: oaw --help" not in stderr.getvalue()
