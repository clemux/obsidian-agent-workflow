from pathlib import Path

import typer
from typer.testing import CliRunner

from oaw import typer_cli


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_temporary_typer_frontend_resolves_with_shared_service(tmp_path: Path) -> None:
    write(
        tmp_path / "Projects/Example/Tasks/Resolver CLI.md",
        """---
type: task
id: EXM-TSK-resolver
aliases:
  - EXM-TSK-resolver
---

# Resolver CLI
""",
    )
    runner = CliRunner()

    result = runner.invoke(
        typer_cli.app,
        ["resolve", "--path", "EXM-TSK-resolver"],
        env={"OAW_VAULT": str(tmp_path)},
    )

    assert isinstance(typer_cli.app, typer.Typer)
    assert result.exit_code == 0, result.output
    assert result.output == f"{tmp_path / 'Projects/Example/Tasks/Resolver CLI.md'}\n"
