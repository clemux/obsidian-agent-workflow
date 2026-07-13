"""Temporary Typer frontend used only to validate the CLI dependency boundary.

This module is intentionally not the installed entry point.  The argparse
adapter in :mod:`oaw.cli` remains authoritative until the Typer migration.
"""

from __future__ import annotations

from typing import Annotated

import typer

from .errors import OawError
from .resolver import output_resolve, resolve_id, vault_root

app = typer.Typer(add_completion=False, help="Temporary Typer frontend for migration tests.")


@app.callback()
def main() -> None:
    """Temporary Typer frontend for migration tests."""


@app.command()
def resolve(
    note_id: Annotated[str, typer.Argument(help="resolve obs:<ID> or <ID>")],
    full: Annotated[bool, typer.Option("--full")] = False,
    path: Annotated[bool, typer.Option("--path")] = False,
    meta: Annotated[bool, typer.Option("--meta")] = False,
    outline: Annotated[bool, typer.Option("--outline")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Resolve an Obsidian note ID through the shared resolver service."""
    try:
        output_resolve(
            resolve_id(note_id, vault_root()),
            full,
            path,
            meta,
            outline,
            json_output,
        )
    except OawError as exc:
        typer.echo(f"oaw: {exc}", err=True)
        raise typer.Exit(code=1) from exc
