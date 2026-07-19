import re
import subprocess
import sys
from pathlib import Path

from typer.main import get_command

from oaw import cli
from oaw.catalog import (
    SEMANTICS,
    _code_span,
    _escape_cell_text,
    leaf_commands,
    render_cli_catalog,
    stable_id,
)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs/oaw-cli-feature-catalog.md"
GENERATOR = ROOT / "scripts/generate_cli_catalog.py"


def test_catalog_semantics_cover_every_live_leaf_command() -> None:
    leaves = leaf_commands(get_command(cli.app))
    live_paths = {path for path, _ in leaves}

    assert set(SEMANTICS) == live_paths
    assert all(command.help for _, command in leaves)
    assert all(metadata.owner and metadata.mutation_scope for metadata in SEMANTICS.values())


def test_catalog_is_deterministic_and_matches_committed_artifact() -> None:
    first = render_cli_catalog()
    second = render_cli_catalog()

    assert first == second
    assert CATALOG.read_text(encoding="utf-8") == first


def test_catalog_uses_live_typer_metadata_not_migration_usage_goldens(monkeypatch) -> None:
    monkeypatch.setattr(cli, "USAGE_BY_COMMAND", {})

    catalog = render_cli_catalog()

    assert "| `oaw resolve` | resolve obs:&lt;ID&gt; or &lt;ID&gt; |" in catalog
    assert "`--status {backlog\\|todo}`" in catalog


def test_catalog_escapes_prose_but_preserves_code_and_generated_markup() -> None:
    assert _escape_cell_text("A & B < C > D | E\nF") == ("A &amp; B &lt; C &gt; D \\| E F")
    assert _code_span("A & <B> | C\nD") == "`A & <B> \\| C D`"
    assert _code_span("A`B") == "``A`B``"
    assert _code_span("`") == "`` ` ``"

    catalog = render_cli_catalog()
    resolve_row = next(line for line in catalog.splitlines() if "`oaw resolve`" in line)

    assert "resolve obs:&lt;ID&gt; or &lt;ID&gt;" in resolve_row
    assert '<a id="oaw-cli-resolve"></a>' in resolve_row
    assert "`<note-id>` (required)" in resolve_row
    assert "&lt;note-id&gt;" not in resolve_row
    assert "<br>" in resolve_row
    assert "&lt;br&gt;" not in resolve_row


def test_catalog_contains_stable_ids_choices_owners_mutations_and_run_state() -> None:
    catalog = render_cli_catalog()

    assert f'id="{stable_id(("task", "create"))}"' in catalog
    assert "`--status {backlog\\|todo}`" in catalog
    assert "`--priority {1\\|2\\|3}`" in catalog
    assert "`--effort {S\\|M\\|L}`" in catalog
    assert "`oaw.lifecycle`" in catalog
    assert "Creates a task note" in catalog
    assert "Updates a task note" in catalog
    for command in ("list", "close", "audit"):
        row = next(line for line in catalog.splitlines() if f"`oaw run {command}`" in line)
        assert row.endswith("| Active (not deprecated) |")

    command_rows = [line for line in catalog.splitlines() if line.startswith('| <a id="oaw-cli-')]
    assert command_rows
    assert all(len(re.findall(r"(?<!\\)\|", row)) == 8 for row in command_rows)


def test_research_start_catalogs_all_conditional_writes() -> None:
    row = next(line for line in render_cli_catalog().splitlines() if "`oaw research start`" in line)

    assert "provider result note" in row
    assert "prompt note" in row
    assert "Synthesis.md" in row
    assert "Bases/Research packet.base" in row


def test_generator_check_mode_detects_current_and_stale_outputs(tmp_path: Path) -> None:
    output = tmp_path / "catalog.md"
    output.write_text(render_cli_catalog(), encoding="utf-8")
    current = subprocess.run(
        [sys.executable, str(GENERATOR), "--check", "--output", str(output)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert current.returncode == 0, current.stderr
    assert current.stderr == ""
    assert "Catalog current:" in current.stdout

    output.write_text("stale\n", encoding="utf-8")
    stale = subprocess.run(
        [sys.executable, str(GENERATOR), "--check", "--output", str(output)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert stale.returncode == 1
    assert stale.stdout == ""
    assert "catalog check failed: regenerate" in stale.stderr
