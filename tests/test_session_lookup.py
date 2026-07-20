import shutil

import pytest

from tests import support
from tests.support import FIXTURES, write


@pytest.fixture
def vault(tmp_path):
    """A bare, empty vault root under tmp_path."""
    return support.make_vault(tmp_path)


@pytest.fixture
def run_oaw(vault):
    return support.make_runner(vault)


def _add_resolver_cli_task(vault):
    """Write the 'Resolver CLI' project task most session-lookup tests match against."""
    return support.add_task(
        vault,
        "Obsidian Agent Workflow",
        "Resolver CLI.md",
        "OAW-TSK-cli",
        project="obsidian-agent-workflow",
        status="todo",
        tags=("projects",),
        body="# Resolver CLI\n\n## Goal\n\nBuild it.\n\n## Agent sessions\n\n",
    )


def test_session_lookup_reports_vault_note_hit(run_oaw, vault):
    task = _add_resolver_cli_task(vault)
    support.add_agent_task(
        vault,
        "Distractor.md",
        "AGT-TSK-distractor",
        body="# Distractor\n\nThis note does not contain the requested session.\n",
    )
    task.write_text(
        task.read_text(encoding="utf-8")
        + "- 2026-07-09 - Codex - `CODEX_THREAD_ID=lookup-thread` - Logged.\n",
        encoding="utf-8",
    )

    proc = run_oaw(
        "session",
        "lookup",
        "  lookup-thread  ",
        "--codex-root",
        str(vault / "missing-codex"),
        "--claude-root",
        str(vault / "missing-claude"),
    )

    assert proc.returncode == 0, proc.stderr
    assert "Session: lookup-thread" in proc.stdout
    assert "Vault matches:" in proc.stdout
    assert (
        "- Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md | id: OAW-TSK-cli" in proc.stdout
    )
    assert "AGT-TSK-distractor" not in proc.stdout
    assert "Harness artifacts:" not in proc.stdout


def test_session_lookup_reports_duplicate_note_ids_without_failing(run_oaw, vault):
    task = _add_resolver_cli_task(vault)
    task.write_text(
        task.read_text(encoding="utf-8") + "\nlookup-duplicate-session\n",
        encoding="utf-8",
    )
    write(
        vault / "Projects/Other/Tasks/Duplicate CLI.md",
        """---
type: task
id: OAW-TSK-cli
---

# Duplicate CLI

lookup-duplicate-session
""",
    )

    proc = run_oaw(
        "session",
        "lookup",
        "lookup-duplicate-session",
        "--codex-root",
        str(vault / "missing-codex"),
        "--claude-root",
        str(vault / "missing-claude"),
    )

    assert proc.returncode == 0, proc.stderr
    assert "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md" in proc.stdout
    assert "Projects/Other/Tasks/Duplicate CLI.md" in proc.stdout
    assert proc.stdout.count("id: OAW-TSK-cli") == 2


def test_session_lookup_summarizes_harness_artifacts(run_oaw, vault):
    session_id = "019f43c9-e93a-7052-bac7-1789a6de1df7"
    codex_root = vault / "harness/codex/sessions"
    claude_root = vault / "harness/claude/projects"
    rollout = codex_root / "2026/07/09" / f"rollout-2026-07-09T12-00-00-{session_id}.jsonl"
    parent = claude_root / "-tmp-project" / f"{session_id}.jsonl"
    subagent = (
        claude_root / "-tmp-project" / "parent-session/subagents" / f"agent-{session_id}.jsonl"
    )
    write(
        rollout,
        '{"type":"session_meta","cwd":"/workspace/example"}\n'
        '{"type":"response_item","payload":{"type":"message","role":"user",'
        '"content":[{"type":"input_text","text":"# AGENTS.md instructions for /repo"}]}}\n'
        '{"type":"response_item","payload":{"type":"message","role":"user",'
        '"content":[{"type":"input_text","text":"Find the owning note."}]}}\n'
        '{"type":"tool_output","content":"Read Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md"}\n',
    )
    write(
        parent,
        '{"message":{"role":"user","content":"Parent transcript request."}}\n',
    )
    write(subagent, '{"content":"subagent output"}\n')

    proc = run_oaw(
        "session",
        "lookup",
        session_id,
        "--codex-root",
        str(codex_root),
        "--claude-root",
        str(claude_root),
    )

    assert proc.returncode == 0, proc.stderr
    assert "Harness artifacts:" in proc.stdout
    assert f"- codex-rollout: {rollout}" in proc.stdout
    assert f"- claude-transcript: {parent}" in proc.stdout
    assert f"- claude-subagent: {subagent}" in proc.stdout
    assert "cwd: /workspace/example" in proc.stdout
    assert "first user: Find the owning note." in proc.stdout
    assert "Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md" in proc.stdout


def test_session_lookup_verbose_reports_codex_metrics(run_oaw, vault):
    session_id = "019f43c9-e93a-7052-bac7-1789a6de1df7"
    codex_root = vault / "harness/codex/sessions"
    rollout = codex_root / f"rollout-2026-07-09T12-00-00-{session_id}.jsonl"
    rollout.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / "session_lookup/codex-complete.jsonl", rollout)

    default_proc = run_oaw(
        "session",
        "lookup",
        session_id,
        "--codex-root",
        str(codex_root),
        "--claude-root",
        str(vault / "missing-claude"),
    )
    verbose_proc = run_oaw(
        "session",
        "lookup",
        session_id,
        "--verbose",
        "--codex-root",
        str(codex_root),
        "--claude-root",
        str(vault / "missing-claude"),
    )

    assert default_proc.returncode == 0, default_proc.stderr
    assert "Started:" not in default_proc.stdout
    assert "Turns:" not in default_proc.stdout
    assert "Tokens:" not in default_proc.stdout
    assert verbose_proc.returncode == 0, verbose_proc.stderr
    assert "Started: 2026-07-09T12:00:00Z" in verbose_proc.stdout
    assert "Ended: 2026-07-09T12:02:05Z" in verbose_proc.stdout
    assert "Duration: 00:02:05" in verbose_proc.stdout
    assert "Turns: user=2, assistant=2" in verbose_proc.stdout
    assert "Tokens: input=250, output=80, cached=75, total=330" in verbose_proc.stdout


def test_session_lookup_verbose_reports_vault_and_codex_matches(run_oaw, vault):
    session_id = "019f43c9-e93a-7052-bac7-1789a6de1df7"
    task = _add_resolver_cli_task(vault)
    task.write_text(
        task.read_text(encoding="utf-8").replace(
            "---\n\n# Resolver CLI",
            f"session-ids:\n  - {session_id}\n---\n\n# Resolver CLI",
        ),
        encoding="utf-8",
    )
    codex_root = vault / "harness/codex/sessions"
    rollout = codex_root / f"rollout-2026-07-09T12-00-00-{session_id}.jsonl"
    rollout.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / "session_lookup/codex-complete.jsonl", rollout)

    default_proc = run_oaw(
        "session",
        "lookup",
        session_id,
        "--codex-root",
        str(codex_root),
        "--claude-root",
        str(vault / "missing-claude"),
    )
    verbose_proc = run_oaw(
        "session",
        "lookup",
        session_id,
        "--verbose",
        "--codex-root",
        str(codex_root),
        "--claude-root",
        str(vault / "missing-claude"),
    )

    assert default_proc.returncode == 0, default_proc.stderr
    assert "Vault matches:" in default_proc.stdout
    assert "Harness artifacts:" not in default_proc.stdout
    assert "Started:" not in default_proc.stdout
    assert verbose_proc.returncode == 0, verbose_proc.stderr
    assert "Vault matches:" in verbose_proc.stdout
    assert (
        "- Projects/Obsidian Agent Workflow/Tasks/Resolver CLI.md | id: OAW-TSK-cli"
        in verbose_proc.stdout
    )
    assert "Harness artifacts:" in verbose_proc.stdout
    assert f"- codex-rollout: {rollout}" in verbose_proc.stdout
    assert "Started: 2026-07-09T12:00:00Z" in verbose_proc.stdout
    assert "Tokens: input=250, output=80, cached=75, total=330" in verbose_proc.stdout


def test_session_lookup_verbose_marks_missing_and_unsupported_metrics_unavailable(run_oaw, vault):
    session_id = "019f43c9-e93a-7052-bac7-1789a6de1df7"
    codex_root = vault / "harness/codex/sessions"
    claude_root = vault / "harness/claude/projects"
    rollout = codex_root / f"rollout-2026-07-09T12-00-00-{session_id}.jsonl"
    rollout.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / "session_lookup/codex-missing.jsonl", rollout)
    write(claude_root / "project" / f"{session_id}.jsonl", "{}\n")

    proc = run_oaw(
        "session",
        "lookup",
        session_id,
        "--verbose",
        "--codex-root",
        str(codex_root),
        "--claude-root",
        str(claude_root),
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.count("Started: unavailable") == 2
    assert proc.stdout.count("Ended: unavailable") == 2
    assert proc.stdout.count("Duration: unavailable") == 2
    assert proc.stdout.count("Turns: user=unavailable, assistant=unavailable") == 2
    assert (
        proc.stdout.count(
            "Tokens: input=unavailable, output=unavailable, cached=unavailable, total=unavailable"
        )
        == 2
    )


def test_session_lookup_unknown_exits_successfully(run_oaw, vault):
    proc = run_oaw(
        "session",
        "lookup",
        "not-logged-session",
        "--codex-root",
        str(vault / "missing-codex"),
        "--claude-root",
        str(vault / "missing-claude"),
    )

    assert proc.returncode == 0, proc.stderr
    assert "Session: not-logged-session" in proc.stdout
    assert "Status: not logged" in proc.stdout


def test_session_lookup_treats_glob_metacharacters_literally(run_oaw, vault):
    session_id = "abc[1]"
    codex_root = vault / "harness/codex/sessions"
    rollout = codex_root / f"rollout-2026-07-09T12-00-00-{session_id}.jsonl"
    write(rollout, '{"type":"session_meta","cwd":"/workspace/example"}\n')

    proc = run_oaw(
        "session",
        "lookup",
        session_id,
        "--codex-root",
        str(codex_root),
        "--claude-root",
        str(vault / "missing-claude"),
    )

    assert proc.returncode == 0, proc.stderr
    assert f"- codex-rollout: {rollout}" in proc.stdout


def test_session_lookup_default_finds_archived_codex_rollout(run_oaw, vault):
    session_id = "019f5001-0000-7111-8222-b15aa4c27782"
    codex_home = vault / "harness/codex"
    archived = codex_home / "archived_sessions" / f"rollout-2026-07-11T10-00-00-{session_id}.jsonl"
    archived.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(FIXTURES / "session_lookup/codex-complete.jsonl", archived)

    proc = run_oaw(
        "session",
        "lookup",
        session_id,
        "--claude-root",
        str(vault / "missing-claude"),
        env={"CODEX_HOME": str(codex_home)},
    )

    assert proc.returncode == 0, proc.stderr
    assert f"- codex-rollout: {archived}" in proc.stdout

    explicit_override = run_oaw(
        "session",
        "lookup",
        session_id,
        "--codex-root",
        str(codex_home / "sessions"),
        "--claude-root",
        str(vault / "missing-claude"),
        env={"CODEX_HOME": str(codex_home)},
    )
    env_override = run_oaw(
        "session",
        "lookup",
        session_id,
        "--claude-root",
        str(vault / "missing-claude"),
        env={
            "CODEX_HOME": str(codex_home),
            "OAW_CODEX_SESSIONS_ROOT": str(codex_home / "sessions"),
        },
    )
    for overridden in (explicit_override, env_override):
        assert overridden.returncode == 0, overridden.stderr
        assert "Status: not logged" in overridden.stdout
        assert str(archived) not in overridden.stdout


def test_session_lookup_prefers_active_duplicate_rollout(run_oaw, vault):
    session_id = "019f5004-3333-7444-8555-e48cc7f6bb15"
    codex_home = vault / "harness/codex"
    filename = f"rollout-2026-07-11T11-00-00-{session_id}.jsonl"
    active = codex_home / "sessions/2026/07/11" / filename
    archived = codex_home / "archived_sessions" / filename
    write(active, '{"type":"session_meta","cwd":"/active"}\n')
    write(archived, '{"type":"session_meta","cwd":"/archived"}\n')

    proc = run_oaw(
        "session",
        "lookup",
        session_id,
        "--claude-root",
        str(vault / "missing-claude"),
        env={"CODEX_HOME": str(codex_home)},
    )

    assert proc.returncode == 0, proc.stderr
    assert f"- codex-rollout: {active}" in proc.stdout
    assert "cwd: /active" in proc.stdout
    assert str(archived) not in proc.stdout
    assert proc.stdout.count("- codex-rollout:") == 1


def test_session_lookup_keeps_duplicate_rollouts_within_one_root(run_oaw, vault):
    session_id = "019f5005-4444-7555-8666-f59dd806cc26"
    codex_root = vault / "harness/codex/sessions"
    filename = f"rollout-2026-07-11T12-00-00-{session_id}.jsonl"
    first = codex_root / "2026/07/11" / filename
    second = codex_root / "restored" / filename
    write(first, '{"type":"session_meta","cwd":"/first"}\n')
    write(second, '{"type":"session_meta","cwd":"/second"}\n')

    proc = run_oaw(
        "session",
        "lookup",
        session_id,
        "--codex-root",
        str(codex_root),
        "--claude-root",
        str(vault / "missing-claude"),
    )

    assert proc.returncode == 0, proc.stderr
    assert f"- codex-rollout: {first}" in proc.stdout
    assert f"- codex-rollout: {second}" in proc.stdout
    assert proc.stdout.count("- codex-rollout:") == 2
