import pytest

from oaw import links, resolver
from tests import support
from tests.support import write


@pytest.fixture
def vault(tmp_path):
    """Minimal vault with just the two task notes obs: reference tests resolve against."""
    root = support.make_vault(tmp_path)
    support.add_task(
        root,
        "Obsidian Agent Workflow",
        "Resolver CLI.md",
        "OAW-TSK-cli",
        project="obsidian-agent-workflow",
        status="todo",
        tags=("projects",),
        body="# Resolver CLI\n\n## Goal\n\nBuild it.\n\n## Agent sessions\n\n",
    )
    support.add_task(
        root,
        "Obsidian Agent Workflow",
        "Archived task.md",
        "OAW-TSK-archived",
        project="obsidian-agent-workflow",
        status="archived",
        body="# Archived task\n",
    )
    return root


def test_obs_materialization_caches_repeated_resolution(vault, monkeypatch):
    references = resolver.scan_note_references(vault)
    original = links.resolve_id_from_references
    calls = []

    def recording_resolve(target, root, cached_references):
        calls.append(target)
        return original(target, root, cached_references)

    monkeypatch.setattr(links, "resolve_id_from_references", recording_resolve)
    rendered, replacements = links.materialize_obs_references(
        "obs:OAW-TSK-cli and obs:OAW-TSK-cli", vault, references
    )

    assert calls == ["OAW-TSK-cli"]
    assert len(replacements) == 2
    assert rendered.count("[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI") == 2


def test_obs_materialization_preserves_bytes_and_complex_protected_spans(vault):
    write(
        vault / "Projects/Legacy/Tasks/Underscore.md",
        "---\nid: OAW-TSK-legacy_v2\n---\n\n# Legacy\n",
    )
    durable = "[[Projects/Legacy/Tasks/Underscore|OAW-TSK-legacy_v2]]"
    source = (
        "  obs:OAW-TSK-legacy_v2  \r\n"
        "[[Existing|alias]] and obs:OAW-TSK-cli\r\n"
        "````text\r\n"
        "obs:OAW-TSK-cli\r\n"
        "```\r\n"
        "obs:OAW-TSK-archived\r\n"
        "````\r\n"
    )

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert rendered.startswith(f"  {durable}  \r\n")
    assert (
        "[[Existing|alias]] and "
        "[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]\r\n"
    ) in rendered
    assert ("````text\r\nobs:OAW-TSK-cli\r\n```\r\nobs:OAW-TSK-archived\r\n````\r\n") in rendered
    assert [item.reference for item in replacements] == [
        "obs:OAW-TSK-legacy_v2",
        "obs:OAW-TSK-cli",
    ]


def test_obs_materialization_protects_bare_uri_and_query_values(vault):
    source = (
        "https://example.test/?ref=obs:OAW-TSK-cli\n"
        "mailto:agent@example.test?subject=obs:OAW-TSK-archived\n"
        "obsidian://open?vault=example&file=obs:OAW-TSK-cli\n"
        "urn:example:item?related=obs:OAW-TSK-archived\n"
        "/relative/path?ref=obs:OAW-TSK-cli\n"
        "data:text/plain,obs:OAW-TSK-archived\n"
    )

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert rendered == source
    assert replacements == []


def test_obs_materialization_keeps_standalone_prose_references_eligible(vault):
    source = "See obs:OAW-TSK-cli, (obs:OAW-TSK-archived), and value=obs:OAW-TSK-cli in prose.\n"

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert len(replacements) == 3
    assert ("See [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]") in rendered
    assert (
        "([[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]])"
    ) in rendered
    assert ("value=[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]") in rendered


def test_obs_materialization_protects_container_nested_fenced_code(vault):
    source = (
        "> ~~~text\n"
        "> obs:OAW-TSK-cli\n"
        "> ~~~\n"
        "\n"
        "- ~~~text\n"
        "  obs:OAW-TSK-archived\n"
        "  ~~~\n"
        "\n"
        "> ```text\n"
        "> literal ``` here\n"
        "> obs:OAW-TSK-cli\n"
        "> ```\n"
        "\n"
        "```text\n"
        "- ```\n"
        "> ```\n"
        "obs:OAW-TSK-archived\n"
        "```\n"
    )

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert rendered == source
    assert replacements == []


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(">     obs:OAW-TSK-cli\n", id="blockquote-indented-code"),
        pytest.param("-     obs:OAW-TSK-archived\n", id="list-item-indented-code"),
        pytest.param("- item\n\n      obs:OAW-TSK-cli\n", id="list-item-nested-indented-code"),
    ],
)
def test_obs_materialization_protects_container_nested_indented_code(vault, source):
    rendered, replacements = links.materialize_obs_references(source, vault)
    assert rendered == source
    assert replacements == []


def test_obs_materialization_protects_container_nested_reference_definitions(vault):
    source = (
        "> [quoted obs:OAW-TSK-cli]: /quote\n"
        ">\n"
        "> [quoted obs:OAW-TSK-cli]\n"
        "\n"
        "- [listed obs:OAW-TSK-archived]: /list\n"
        "\n"
        "  [listed obs:OAW-TSK-archived]\n"
    )

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert rendered == source
    assert replacements == []


def test_obs_materialization_protects_commonmark_indented_code_blocks(vault):
    source = (
        "    obs:OAW-TSK-cli\n"
        "\tobs:OAW-TSK-archived\n"
        "\n"
        "Paragraph continuation:\n"
        "    obs:OAW-TSK-cli\n"
        "\n"
        "    obs:OAW-TSK-archived\n"
        "\tobs:OAW-TSK-cli\n"
        "outside obs:OAW-TSK-archived\n"
    )

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert rendered.startswith("    obs:OAW-TSK-cli\n\tobs:OAW-TSK-archived\n")
    assert ("    [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]\n") in rendered
    assert "    obs:OAW-TSK-archived\n\tobs:OAW-TSK-cli\n" in rendered
    assert (
        "outside [[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]"
    ) in rendered
    assert [item.reference for item in replacements] == [
        "obs:OAW-TSK-cli",
        "obs:OAW-TSK-archived",
    ]


def test_obs_materialization_protects_complex_markdown_link_labels(vault):
    source = (
        "[nested [obs:OAW-TSK-cli] label](https://example.test/a_(b)) "
        "then obs:OAW-TSK-cli.\n"
        "[escaped \\] obs:OAW-TSK-archived](https://example.test/target) "
        "then obs:OAW-TSK-archived.\n"
    )

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert (
        "[nested [obs:OAW-TSK-cli] label](https://example.test/a_(b)) then "
        "[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]."
    ) in rendered
    assert (
        "[escaped \\] obs:OAW-TSK-archived](https://example.test/target) then "
        "[[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]."
    ) in rendered
    assert [item.reference for item in replacements] == [
        "obs:OAW-TSK-cli",
        "obs:OAW-TSK-archived",
    ]


def test_obs_materialization_protects_multiline_markdown_links_and_images(vault):
    source = (
        "[See obs:OAW-TSK-cli\r\nfor details](https://example.test/path) | "
        "obs:OAW-TSK-archived |\r\n"
        "[See obs:OAW-TSK-archived\r\nby reference][details]\r\n"
        "![Alt obs:OAW-TSK-cli\r\ncontinued](image.png)\r\n"
        "Outside obs:OAW-TSK-archived.\r\n"
    )

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert rendered == (
        "[See obs:OAW-TSK-cli\r\nfor details](https://example.test/path) | "
        "[[Projects/Obsidian Agent Workflow/Tasks/Archived task\\|"
        "OAW-TSK-archived]] |\r\n"
        "[See obs:OAW-TSK-archived\r\nby reference][details]\r\n"
        "![Alt obs:OAW-TSK-cli\r\ncontinued](image.png)\r\n"
        "Outside [[Projects/Obsidian Agent Workflow/Tasks/Archived task|"
        "OAW-TSK-archived]].\r\n"
    )
    assert [item.reference for item in replacements] == [
        "obs:OAW-TSK-archived",
        "obs:OAW-TSK-archived",
    ]


def test_obs_materialization_protects_only_defined_shortcut_reference_links(vault):
    source = (
        "[obs:OAW-TSK-cli] and [arbitrary obs:OAW-TSK-archived].\n"
        "[See obs:OAW-TSK-archived\nfor details]\n"
        "[fenced obs:OAW-TSK-archived]\n"
        "[obs:OAW-TSK-cli]: https://example.test/cli\n"
        "[See obs:OAW-TSK-archived for details]: https://example.test/details\n"
        "```text\n[fenced obs:OAW-TSK-archived]: https://example.test/fenced\n```\n"
    )

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert "[obs:OAW-TSK-cli]" in rendered
    assert (
        "[arbitrary [[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]]."
    ) in rendered
    assert "[See obs:OAW-TSK-archived\nfor details]\n" in rendered
    assert (
        "[fenced [[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]]"
    ) in rendered
    assert "[obs:OAW-TSK-cli]: https://example.test/cli\n" in rendered
    assert [item.reference for item in replacements] == [
        "obs:OAW-TSK-archived",
        "obs:OAW-TSK-archived",
    ]


def test_fake_definitions_in_multiline_protected_spans_do_not_activate_shortcuts(vault):
    source = (
        "``code starts\n"
        "[code obs:OAW-TSK-cli]: https://example.test/code\n"
        "code ends``\n"
        "[outer label\n"
        "[link obs:OAW-TSK-archived]: https://example.test/link\n"
        "continued](https://example.test/outer)\n"
        "[code obs:OAW-TSK-cli]\n"
        "[link obs:OAW-TSK-archived]\n"
    )

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert "[code obs:OAW-TSK-cli]: https://example.test/code" in rendered
    assert "[link obs:OAW-TSK-archived]: https://example.test/link" in rendered
    assert (
        "[code [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]]"
    ) in rendered
    assert (
        "[link [[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]]"
    ) in rendered
    assert len(replacements) == 2


def test_shortcut_definitions_require_valid_destination_variants(vault):
    source = (
        "[angle obs:OAW-TSK-cli] and [bare obs:OAW-TSK-archived].\n"
        "[empty obs:OAW-TSK-cli]\n"
        "[angle obs:OAW-TSK-cli]: <https://example.test/angle>\n"
        '[bare obs:OAW-TSK-archived]: /docs_(v1) "Documentation"\n'
        "[empty obs:OAW-TSK-cli]:\n"
    )

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert "[angle obs:OAW-TSK-cli] and [bare obs:OAW-TSK-archived]." in rendered
    assert rendered.count("[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|") == 2
    assert "[angle obs:OAW-TSK-cli]: <https://example.test/angle>" in rendered
    assert '[bare obs:OAW-TSK-archived]: /docs_(v1) "Documentation"' in rendered
    assert [item.reference for item in replacements] == [
        "obs:OAW-TSK-cli",
        "obs:OAW-TSK-cli",
    ]


def test_reference_definition_continuation_titles_are_protected(vault):
    source = (
        "[double obs:OAW-TSK-cli] [single obs:OAW-TSK-archived] "
        "[paren obs:OAW-TSK-cli]\n"
        "[double obs:OAW-TSK-cli]: /double\n"
        '  "Double title obs:OAW-TSK-archived"\n'
        "[single obs:OAW-TSK-archived]: <https://example.test/single>\n"
        " 'Single title obs:OAW-TSK-cli'\n"
        "[paren obs:OAW-TSK-cli]: /paren\n"
        "   (Parenthesized title obs:OAW-TSK-archived)\n"
    )

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert rendered == source
    assert replacements == []


def test_invalid_reference_definition_title_continuation_remains_prose(vault):
    source = (
        "[invalid obs:OAW-TSK-cli]\n"
        "[invalid obs:OAW-TSK-cli]: /invalid\n"
        '  "unterminated title obs:OAW-TSK-archived\n'
    )

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert "[invalid obs:OAW-TSK-cli]\n" in rendered
    assert "[invalid obs:OAW-TSK-cli]: /invalid\n" in rendered
    assert (
        '  "unterminated title [[Projects/Obsidian Agent Workflow/Tasks/Archived task|'
        "OAW-TSK-archived]]\n"
    ) in rendered
    assert [item.reference for item in replacements] == ["obs:OAW-TSK-archived"]


def test_reference_definition_title_rejects_tabs_and_nested_parentheses(vault):
    source = (
        "[tab obs:OAW-TSK-cli] [nested obs:OAW-TSK-cli] "
        "[escaped obs:OAW-TSK-cli]\n"
        "[tab obs:OAW-TSK-cli]: /tab\n"
        '\t"tab title obs:OAW-TSK-archived"\n'
        "[nested obs:OAW-TSK-cli]: /nested\n"
        "  (outer (nested title obs:OAW-TSK-archived)\n"
        "[escaped obs:OAW-TSK-cli]: /escaped\n"
        "  (escaped \\( title obs:OAW-TSK-archived)\n"
    )

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert "[tab obs:OAW-TSK-cli] [nested obs:OAW-TSK-cli]" in rendered
    assert (
        '\t"tab title [[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]"\n'
    ) in rendered
    assert (
        "  (outer (nested title "
        "[[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]])\n"
    ) in rendered
    assert "  (escaped \\( title obs:OAW-TSK-archived)\n" in rendered
    assert len(replacements) == 2


def test_cross_line_link_candidate_stops_at_blank_block_boundary(vault):
    source = "[not a link obs:OAW-TSK-cli\n\ncontinued](https://example.test)\n"

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert rendered == (
        "[not a link [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|"
        "OAW-TSK-cli]]\n\ncontinued](https://example.test)\n"
    )
    assert [item.reference for item in replacements] == ["obs:OAW-TSK-cli"]


def test_obs_materialization_protects_balanced_reference_definition_labels(vault):
    source = (
        "[nested [obs:OAW-TSK-cli] label]: https://example.test/obs:OAW-TSK-archived\n"
        "[escaped \\] obs:OAW-TSK-archived]: https://example.test/obs:OAW-TSK-cli\n"
        "Outside obs:OAW-TSK-cli.\n"
    )

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert (
        "[nested [obs:OAW-TSK-cli] label]: https://example.test/obs:OAW-TSK-archived\n"
    ) in rendered
    assert (
        "[escaped \\] obs:OAW-TSK-archived]: https://example.test/obs:OAW-TSK-cli\n"
    ) in rendered
    assert (
        "Outside [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]]."
    ) in rendered
    assert [item.reference for item in replacements] == ["obs:OAW-TSK-cli"]


def test_obs_materialization_protects_complete_multiline_reference_definitions(vault):
    source = (
        "[next-line obs:OAW-TSK-cli]:\n"
        "  <https://example.test/obs:OAW-TSK-archived>\n"
        "[multi\n"
        "label obs:OAW-TSK-archived]: /docs\n"
        "[title obs:OAW-TSK-cli]: /title\n"
        '  "title obs:OAW-TSK-archived"\n'
        "Outside obs:OAW-TSK-cli.\n"
    )

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert rendered == (
        source.removesuffix("Outside obs:OAW-TSK-cli.\n")
        + "Outside [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|OAW-TSK-cli]].\n"
    )
    assert [item.reference for item in replacements] == ["obs:OAW-TSK-cli"]


def test_obs_materialization_rejects_invalid_reference_definition_destinations_and_labels(
    vault,
):
    oversized_label = "x" * 1000
    source = (
        "[invalid destination obs:OAW-TSK-cli]: https://example.test/<bad>\n"
        f"[{oversized_label} obs:OAW-TSK-archived]: /too-long\n"
    )

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert (
        "[invalid destination [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|"
        "OAW-TSK-cli]]]: https://example.test/<bad>"
    ) in rendered
    assert (
        "[[Projects/Obsidian Agent Workflow/Tasks/Archived task|OAW-TSK-archived]]]: /too-long"
    ) in rendered
    assert [item.reference for item in replacements] == [
        "obs:OAW-TSK-cli",
        "obs:OAW-TSK-archived",
    ]


def test_cross_line_link_candidate_stops_at_setext_block_boundary(vault):
    source = "[not a link obs:OAW-TSK-cli\n===\ncontinued](https://example.test)\n"

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert rendered == (
        "[not a link [[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI|"
        "OAW-TSK-cli]]\n===\ncontinued](https://example.test)\n"
    )
    assert [item.reference for item in replacements] == ["obs:OAW-TSK-cli"]


def test_table_pipe_detection_inherits_cross_line_code_span_state(vault):
    source = "``code starts\nobs:OAW-TSK-archived closes`` | obs:OAW-TSK-cli |\n"

    rendered, replacements = links.materialize_obs_references(source, vault)

    assert rendered == (
        "``code starts\nobs:OAW-TSK-archived closes`` | "
        "[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI\\|OAW-TSK-cli]] |\n"
    )
    assert len(replacements) == 1
    assert (
        replacements[0].link
        == "[[Projects/Obsidian Agent Workflow/Tasks/Resolver CLI\\|OAW-TSK-cli]]"
    )
