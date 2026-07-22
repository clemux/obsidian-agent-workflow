import pytest

from tests import support
from tests.support import snapshot_tree_without_following_symlinks, write


@pytest.fixture
def vault(tmp_path):
    """Minimal vault: the OAW project index (obs:OAW + folder resolution) plus the
    research packet template that every ``research scaffold``/``research start``
    call in this file depends on.
    """
    root = support.make_vault(tmp_path)
    support.add_project_index(root, "Obsidian Agent Workflow", "OAW-index")
    support.add_research_template(root)
    return root


@pytest.fixture
def run_oaw(vault):
    return support.make_runner(vault)


def test_research_scaffold_renders_template_with_audience_boundary(run_oaw, vault):
    proc = run_oaw(
        "research",
        "scaffold",
        "--project",
        "obs:OAW",
        "--track",
        "architecture/provider-choice",
        "--title",
        "Provider choice",
        "--date",
        "2026-07-12",
    )
    assert proc.returncode == 0, proc.stderr
    stdout_lines = proc.stdout.splitlines()
    assert (
        "Created: Projects/Obsidian Agent Workflow/Research/architecture/provider-choice/Prompt.md"
        in stdout_lines
    )
    assert (
        "Synthesis: Projects/Obsidian Agent Workflow/Research/architecture/provider-choice"
        "/Synthesis.md" in stdout_lines
    )
    assert "Base: Bases/Research packet.base" in stdout_lines
    assert "Template: Templates/Research packet.md" in stdout_lines
    assert "Deep research prompt: self-contained provider-visible body" in stdout_lines
    prompt = (
        vault / "Projects/Obsidian Agent Workflow/Research/architecture/provider-choice/Prompt.md"
    ).read_text(encoding="utf-8")
    local, provider = prompt.split("## Deep research prompt", 1)
    assert "project: obsidian-agent-workflow" in local
    assert "track: architecture/provider-choice" in local
    assert "created: 2026-07-12" in local
    assert "# Prompt - Provider choice" in local
    assert "Research Provider choice" in provider
    assert "obsidian-agent-workflow" not in provider
    assert "architecture/provider-choice" not in provider
    assert "```text\nResearch Provider choice" in provider
    synthesis = (
        vault
        / "Projects/Obsidian Agent Workflow/Research/architecture/provider-choice/Synthesis.md"
    )
    synthesis_text = synthesis.read_text(encoding="utf-8")
    assert "type: research-synthesis" in synthesis_text
    assert "![[Bases/Research packet.base#Source reports]]" in synthesis_text
    assert (vault / "Bases/Research packet.base").is_file()


def test_research_scaffold_refuses_existing_prompt_without_force(run_oaw, vault):
    args = (
        "research",
        "scaffold",
        "--project",
        "Obsidian Agent Workflow",
        "--track",
        "provider-choice",
        "--title",
        "Provider choice",
    )
    assert run_oaw(*args).returncode == 0
    proc = run_oaw(*args)
    assert proc.returncode == 1
    assert "research prompt already exists" in proc.stderr


@pytest.mark.parametrize("track", ["architecture/CON", "architecture/trailing.", "a//b"])
def test_research_scaffold_rejects_nonportable_track_without_writing(run_oaw, vault, track):
    before = snapshot_tree_without_following_symlinks(vault)

    result = run_oaw(
        "research",
        "scaffold",
        "--project",
        "obs:OAW",
        "--track",
        track,
        "--title",
        "Portable track",
    )

    assert result.returncode == 1
    assert "research track component" in result.stderr
    assert before == snapshot_tree_without_following_symlinks(vault)


def test_research_scaffold_rejects_template_that_leaks_local_metadata(run_oaw, vault):
    template = vault / "Templates/Research packet.md"
    template.write_text(
        template.read_text(encoding="utf-8") + "\nLocal track: {{track}}\n",
        encoding="utf-8",
    )
    proc = run_oaw(
        "research",
        "scaffold",
        "--project",
        "Obsidian Agent Workflow",
        "--track",
        "provider-choice",
        "--title",
        "Provider choice",
    )
    assert proc.returncode == 1
    assert "places local-only fields" in proc.stderr


def test_research_scaffold_requires_exact_provider_boundary_heading(run_oaw, vault):
    template = vault / "Templates/Research packet.md"
    template.write_text(
        template.read_text(encoding="utf-8").replace(
            "## Deep research prompt", "### Deep research prompt"
        ),
        encoding="utf-8",
    )
    proc = run_oaw(
        "research",
        "scaffold",
        "--project",
        "Obsidian Agent Workflow",
        "--track",
        "provider-choice",
        "--title",
        "Provider choice",
    )
    assert proc.returncode == 1
    assert "must contain exactly one '## Deep research prompt' heading" in proc.stderr


def test_research_scaffold_rejects_rendered_metadata_after_boundary(run_oaw, vault):
    template = vault / "Templates/Research packet.md"
    template.write_text(
        template.read_text(encoding="utf-8").replace(
            "Research {{title}}", "Research obsidian-agent-workflow"
        ),
        encoding="utf-8",
    )
    proc = run_oaw(
        "research",
        "scaffold",
        "--project",
        "Obsidian Agent Workflow",
        "--track",
        "provider-choice",
        "--title",
        "Provider choice",
    )
    assert proc.returncode == 1
    assert "rendered research prompt places local-only metadata" in proc.stderr
    assert "project" in proc.stderr


def test_research_scaffold_allows_short_metadata_characters_inside_words(run_oaw, vault):
    write(
        vault / "Projects/X/Index.md",
        """---
type: project
id: X-index
---

# X
""",
    )
    proc = run_oaw(
        "research",
        "scaffold",
        "--project",
        "X",
        "--track",
        "a/b",
        "--title",
        "T",
        "--date",
        "2026-07-12",
    )
    assert proc.returncode == 0, proc.stderr
    prompt = vault / "Projects/X/Research/a/b/Prompt.md"
    assert prompt.is_file()
    assert "expected output format" in prompt.read_text(encoding="utf-8")


def test_research_scaffold_force_preserves_existing_synthesis(run_oaw, vault):
    args = (
        "research",
        "scaffold",
        "--project",
        "obs:OAW",
        "--track",
        "topic",
        "--title",
        "Topic",
        "--date",
        "2026-07-12",
    )
    assert run_oaw(*args).returncode == 0
    synthesis = vault / "Projects/Obsidian Agent Workflow/Research/topic/Synthesis.md"
    synthesis.write_text("irreplaceable synthesis\n", encoding="utf-8")
    proc = run_oaw(*args, "--force")
    assert proc.returncode == 0, proc.stderr
    assert synthesis.read_text(encoding="utf-8") == "irreplaceable synthesis\n"


def test_research_start_creates_one_running_result_and_updates_prompt(run_oaw, vault):
    scaffold = run_oaw(
        "research",
        "scaffold",
        "--project",
        "obs:OAW",
        "--track",
        "topic",
        "--title",
        "Topic",
        "--date",
        "2026-07-12",
    )
    assert scaffold.returncode == 0, scaffold.stderr
    proc = run_oaw(
        "research",
        "start",
        "--project",
        "obs:OAW",
        "--track",
        "topic",
        "--source",
        "ChatGPT Pro",
        "--url",
        "https://chatgpt.com/share/example",
    )
    assert proc.returncode == 0, proc.stderr
    packet = vault / "Projects/Obsidian Agent Workflow/Research/topic"
    results = sorted(packet.glob("Results - *.md"))
    assert [path.name for path in results] == ["Results - ChatGPT Pro.md"]
    result = results[0].read_text(encoding="utf-8")
    assert 'source: "ChatGPT Pro"' in result
    assert 'url: "https://chatgpt.com/share/example"' in result
    assert "status: running" in result
    prompt = (packet / "Prompt.md").read_text(encoding="utf-8")
    assert "- ChatGPT Pro: [running](https://chatgpt.com/share/example)" in prompt


def test_research_start_rejects_unsafe_duplicate_and_non_http_sources(run_oaw, vault):
    assert (
        run_oaw(
            "research",
            "scaffold",
            "--project",
            "obs:OAW",
            "--track",
            "topic",
            "--title",
            "Topic",
        ).returncode
        == 0
    )
    common = ("research", "start", "--project", "obs:OAW", "--track", "topic")
    unsafe = run_oaw(*common, "--source", "../ChatGPT", "--url", "https://example.com")
    assert unsafe.returncode == 1
    assert "research start --source" in unsafe.stderr
    reserved = run_oaw(*common, "--source", "ChatGPT: Pro", "--url", "https://example.com")
    assert reserved.returncode == 1
    assert "research start --source" in reserved.stderr
    bad_url = run_oaw(*common, "--source", "ChatGPT", "--url", "file:///tmp/report")
    assert bad_url.returncode == 1
    assert "HTTP(S)" in bad_url.stderr
    first = run_oaw(*common, "--source", "ChatGPT", "--url", "https://example.com")
    assert first.returncode == 0, first.stderr
    duplicate = run_oaw(*common, "--source", "ChatGPT", "--url", "https://other.test")
    assert duplicate.returncode == 1
    assert "source already exists" in duplicate.stderr


def test_research_start_rejects_malformed_packet_without_partial_write(run_oaw, vault):
    packet = vault / "Projects/Obsidian Agent Workflow/Research/topic"
    write(packet / "Prompt.md", "---\ntitle: Topic\n---\n\n## Running research sessions\n")
    proc = run_oaw(
        "research",
        "start",
        "--project",
        "obs:OAW",
        "--track",
        "topic",
        "--source",
        "ChatGPT",
        "--url",
        "https://example.com",
    )
    assert proc.returncode == 1
    assert not (packet / "Results - ChatGPT.md").exists()
    assert not (packet / "Synthesis.md").exists()
