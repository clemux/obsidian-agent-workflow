#!/usr/bin/env python3
"""Generate or check the repository's Typer-backed CLI feature catalog."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oaw.catalog import render_cli_catalog  # noqa: E402

DEFAULT_OUTPUT = ROOT / "docs/oaw-cli-feature-catalog.md"


def main(
    check: Annotated[bool, typer.Option("--check", help="fail if the output is stale")] = False,
    output: Annotated[
        Path, typer.Option("--output", help="generated Markdown destination")
    ] = DEFAULT_OUTPUT,
) -> None:
    rendered = render_cli_catalog()
    if check:
        try:
            existing = output.read_text(encoding="utf-8")
        except OSError as exc:
            typer.echo(f"catalog check failed: cannot read {output}: {exc}", err=True)
            raise typer.Exit(1) from exc
        if existing != rendered:
            typer.echo(f"catalog check failed: regenerate {output}", err=True)
            raise typer.Exit(1)
        typer.echo(f"Catalog current: {output}")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    typer.echo(f"Generated: {output}")


if __name__ == "__main__":
    typer.run(main)
