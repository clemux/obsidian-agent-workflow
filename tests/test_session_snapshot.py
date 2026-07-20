import json
import shutil

import pytest

from tests import support
from tests.support import FIXTURES, write

# These tests copy harness artifacts into an output root computed from the vault
# path. They never read or resolve vault notes, so a bare vault is sufficient.


@pytest.fixture
def vault(tmp_path):
    return support.make_vault(tmp_path)


@pytest.fixture
def base_env(vault):
    return support.cli_env(vault)


@pytest.fixture
def run_oaw(vault):
    return support.make_runner(vault)


def test_session_snapshot_copies_artifacts_and_writes_manifest(run_oaw, vault, base_env):
    session_id = "73550790-5af5-4efc-828c-72e6e1053d8f"
    codex_thread = "019f3e73-029f-7ea2-9772-fdfa1e25fb8f"
    task_codex_thread = "019f3e8d-8307-7052-b367-57e78f3316ae"
    fork_session_id = "019f3ef0-1111-7222-8333-c26aa5d38893"
    claude_root = vault / "harness/claude/projects"
    codex_root = vault / "harness/codex/sessions"
    plugin_root = vault / "harness/claude/plugins/data"
    output_root = vault / "Agents/Retrospectives/attachments"

    parent = claude_root / "-tmp-project" / f"{session_id}.jsonl"
    write(
        parent,
        f'{{"timestamp":"2026-07-07T21:18:45.572Z","sessionId":"{session_id}",'
        f'"content":"Codex thread CODEX_THREAD_ID={codex_thread}; '
        'plugin job task-mrb5j4y9-7k3yjy"}}\n',
    )
    write(
        claude_root / "-tmp-project" / session_id / "subagents/agent-a8fbf333b1df5e1e9.jsonl",
        '{"timestamp":"2026-07-07T21:19:00.413Z","content":"delegated"}\n',
    )
    write(
        claude_root / "-tmp-project" / session_id / "subagents/nested/agent-nested.jsonl",
        '{"content":"nested delegated transcript"}\n',
    )
    write(
        claude_root / "-tmp-project" / session_id / "tasks/background.output",
        f"background transcript references codex_thread={task_codex_thread}\n",
    )
    write(
        claude_root / "-tmp-project" / session_id / "subagents/workflows/wf-123/run.jsonl",
        '{"content":"workflow run journal"}\n',
    )
    write(
        claude_root / "-tmp-project" / session_id / "workflows/scripts/nightly.md",
        "# Workflow script\n",
    )
    fork_parent = claude_root / "-tmp-project" / f"{fork_session_id}.jsonl"
    write(
        fork_parent,
        f'{{"timestamp":"2026-07-07T22:00:00.000Z","sessionId":"{fork_session_id}",'
        '"content":"forked context"}}\n',
    )
    matching_rollout = (
        codex_root / "2026/07/07" / f"rollout-2026-07-07T23-19-12-{codex_thread}.jsonl"
    )
    write(matching_rollout, '{"event":"turn_aborted"}\n')
    task_rollout = (
        codex_root / "2026/07/07" / f"rollout-2026-07-07T23-30-00-{task_codex_thread}.jsonl"
    )
    write(task_rollout, '{"content":"referenced from task output"}\n')
    grep_rollout = (
        codex_root
        / "2026/07/07"
        / "rollout-2026-07-07T23-48-09-019f3e8d-8307-7052-b367-57e78f3316ae.jsonl"
    )
    write(grep_rollout, '{"content":"session-inspection-claude-codex other"}\n')
    write(
        plugin_root / "codex-openai-codex/state/example/jobs/task-mrb5j4y9-7k3yjy.log",
        "running\n",
    )

    proc = run_oaw(
        "session",
        "snapshot",
        session_id,
        "--slug",
        "SR dogfood zombie Codex",
        "--partial",
        "--grep",
        "session-inspection-claude-codex",
        "--claude-session",
        fork_session_id,
        "--output-root",
        str(output_root),
        "--claude-root",
        str(claude_root),
        "--codex-root",
        str(codex_root),
        "--plugin-data-root",
        str(plugin_root),
    )
    assert proc.returncode == 0, proc.stderr
    snapshot = output_root / "2026-07-07-sr-dogfood-zombie-codex"
    manifest_path = snapshot / "manifest.json"
    assert (snapshot / "claude/parent-73550790-PARTIAL.jsonl").exists()
    assert (snapshot / "claude/agent-a8fbf333b1df5e1e9.jsonl").exists()
    assert (snapshot / "claude/subagents/nested/agent-nested.jsonl").exists()
    assert (snapshot / "claude/tasks/background.output").exists()
    assert (snapshot / "claude/workflows/wf-123/run.jsonl").exists()
    assert (snapshot / "claude/workflow-scripts/nightly.md").exists()
    assert (snapshot / "claude/forks/parent-019f3ef0.jsonl").exists()
    assert (snapshot / "codex" / matching_rollout.name).exists()
    assert (snapshot / "codex" / task_rollout.name).exists()
    assert (snapshot / "codex" / grep_rollout.name).exists()
    assert (snapshot / "plugin-logs/task-mrb5j4y9-7k3yjy.log").exists()
    assert f"Manifest: {manifest_path}" in proc.stdout

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "oaw-session-snapshot-v1"
    assert manifest["session_id"] == session_id
    assert manifest["snapshot"]["mode"] == "claude-parent"
    assert manifest["snapshot"]["parent_completeness"] == "partial"
    sources = {entry["source"] for entry in manifest["files"]}
    assert str(parent) in sources
    assert str(matching_rollout) in sources
    assert str(task_rollout) in sources
    assert str(fork_parent) in sources
    categories = {entry["category"] for entry in manifest["files"]}
    assert "claude-task-output" in categories
    assert "claude-workflow-artifact" in categories
    assert "claude-workflow-script" in categories
    assert "claude-fork-parent" in categories
    assert all(entry["sha256"] for entry in manifest["files"])


def test_session_snapshot_refresh_updates_parent_and_adds_subagents(vault, base_env):
    session_id = "019f3ed8-245c-79f3-8ec6-c1ba30e3646d"
    claude_root = vault / "harness/claude/projects"
    output_root = vault / "attachments"
    parent = claude_root / "-tmp-project" / f"{session_id}.jsonl"
    write(
        parent,
        f'{{"timestamp":"2026-07-08T01:00:00.000Z","sessionId":"{session_id}",'
        '"content":"first"}}\n',
    )
    nested_subagent = (
        claude_root / "-tmp-project" / session_id / "subagents/nested/agent-nested.jsonl"
    )
    write(nested_subagent, '{"content":"nested"}\n')

    base_args = (
        "session",
        "snapshot",
        session_id,
        "--slug",
        "refresh test",
        "--partial",
        "--output-root",
        str(output_root),
        "--claude-root",
        str(claude_root),
        "--codex-root",
        str(vault / "missing-codex"),
        "--plugin-data-root",
        str(vault / "missing-plugin"),
    )
    first = support.run_oaw_subprocess([str(x) for x in base_args], base_env)
    assert first.returncode == 0, first.stderr
    snapshot = output_root / "2026-07-08-refresh-test"
    nested_copy = snapshot / "claude/subagents/nested/agent-nested.jsonl"
    assert nested_copy.exists()
    stale = snapshot / "codex/stale-rollout.jsonl"
    write(stale, "{}\n")
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append(
        {
            "category": "codex-rollout",
            "source": "/tmp/stale-rollout.jsonl",
            "destination": "codex/stale-rollout.jsonl",
            "copied_at": "2026-07-08T01:00:00+00:00",
            "completeness": "complete",
            "size_bytes": 3,
            "sha256": "stale",
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    write(
        parent,
        f'{{"timestamp":"2026-07-08T01:00:00.000Z","sessionId":"{session_id}",'
        '"content":"second"}}\n',
    )
    write(
        claude_root / "-tmp-project" / session_id / "subagents/agent-new.jsonl",
        '{"content":"new subagent"}\n',
    )
    second = support.run_oaw_subprocess([str(x) for x in base_args], base_env)
    assert second.returncode == 0, second.stderr

    parent_copy = snapshot / "claude/parent-019f3ed8-PARTIAL.jsonl"
    assert "second" in parent_copy.read_text(encoding="utf-8")
    assert (snapshot / "claude/agent-new.jsonl").exists()
    assert nested_copy.exists()
    assert not stale.exists()
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    destinations = {entry["destination"] for entry in manifest["files"]}
    assert "claude/agent-new.jsonl" in destinations
    assert "claude/subagents/nested/agent-nested.jsonl" in destinations


def test_session_snapshot_supports_codex_only_thread_and_discovers_references(run_oaw, vault):
    thread_id = "019f48d7-39c2-7043-9c19-5a3565995898"
    child_thread = "019f48d8-1111-7222-8333-c26aa5d38893"
    grandchild_thread = "019f48d9-2222-7333-8444-d37bb6e49904"
    codex_root = vault / "harness/codex/sessions"
    plugin_root = vault / "harness/claude/plugins/data"
    output_root = vault / "attachments"
    rollout = codex_root / "2026/07/10" / f"rollout-2026-07-10T00-00-00-{thread_id}.jsonl"
    child_rollout = codex_root / "2026/07/10" / f"rollout-2026-07-10T00-05-00-{child_thread}.jsonl"
    grandchild_rollout = (
        codex_root / "2026/07/10" / f"rollout-2026-07-10T00-10-00-{grandchild_thread}.jsonl"
    )
    write(
        rollout,
        f'{{"timestamp":"2026-07-10T00:00:00.000Z","content":"codex_thread={child_thread}"}}\n',
    )
    write(
        child_rollout,
        '{"timestamp":"2026-07-10T00:05:00.000Z",'
        f'"content":"codex_thread={grandchild_thread}; '
        'plugin task-abcd1234-efgh5678"}\n',
    )
    write(grandchild_rollout, '{"timestamp":"2026-07-10T00:10:00.000Z"}\n')
    write(
        plugin_root / "example/jobs/task-abcd1234-efgh5678.log",
        "complete\n",
    )

    proc = run_oaw(
        "session",
        "snapshot",
        thread_id.upper(),
        "--codex-only",
        "--slug",
        "codex only",
        "--output-root",
        str(output_root),
        "--codex-root",
        str(codex_root),
        "--claude-root",
        str(vault / "missing-claude"),
        "--plugin-data-root",
        str(plugin_root),
        env={"CODEX_THREAD_ID": thread_id},
    )

    assert proc.returncode == 0, proc.stderr
    snapshot = output_root / "2026-07-10-codex-only"
    assert (snapshot / "codex" / rollout.name).exists()
    assert (snapshot / "codex" / child_rollout.name).exists()
    assert (snapshot / "codex" / grandchild_rollout.name).exists()
    assert (snapshot / "plugin-logs/task-abcd1234-efgh5678.log").exists()
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["snapshot"]["mode"] == "codex-only"
    assert manifest["snapshot"]["parent_transcript"] is None
    assert manifest["snapshot"]["parent_completeness"] == "partial"
    codex_entries = [entry for entry in manifest["files"] if entry["category"] == "codex-rollout"]
    assert all(entry["completeness"] == "partial" for entry in codex_entries)
    assert "Transcript: partial" in proc.stdout


def test_session_snapshot_default_discovers_archived_codex_lineage(run_oaw, vault):
    thread_id = "019f5001-0000-7111-8222-b15aa4c27782"
    child_thread = "019f5002-1111-7222-8333-c26aa5d38893"
    grandchild_thread = "019f5003-2222-7333-8444-d37bb6e49904"
    codex_home = vault / "harness/codex"
    output_root = vault / "attachments"
    parent = codex_home / "archived_sessions" / f"rollout-2026-07-11T10-00-00-{thread_id}.jsonl"
    child = codex_home / "sessions/2026/07/11" / f"rollout-2026-07-11T10-05-00-{child_thread}.jsonl"
    grandchild = (
        codex_home / "archived_sessions" / f"rollout-2026-07-11T10-10-00-{grandchild_thread}.jsonl"
    )
    for destination, fixture in (
        (parent, "codex-archived-parent.jsonl"),
        (child, "codex-active-child.jsonl"),
        (grandchild, "codex-archived-grandchild.jsonl"),
    ):
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / "session_snapshot" / fixture, destination)

    proc = run_oaw(
        "session",
        "snapshot",
        thread_id,
        "--codex-only",
        "--slug",
        "archived lineage",
        "--output-root",
        str(output_root),
        "--claude-root",
        str(vault / "missing-claude"),
        "--plugin-data-root",
        str(vault / "missing-plugin"),
        env={"CODEX_HOME": str(codex_home)},
    )

    assert proc.returncode == 0, proc.stderr
    snapshot = output_root / "2026-07-11-archived-lineage"
    for rollout in (parent, child, grandchild):
        assert (snapshot / "codex" / rollout.name).is_file()
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    sources = {entry["source"] for entry in manifest["files"]}
    assert {str(parent), str(child), str(grandchild)} <= sources

    overridden = run_oaw(
        "session",
        "snapshot",
        thread_id,
        "--codex-only",
        "--output-root",
        str(vault / "override-attachments"),
        env={
            "CODEX_HOME": str(codex_home),
            "OAW_CODEX_SESSIONS_ROOT": str(codex_home / "sessions"),
        },
    )
    assert overridden.returncode == 1
    assert f"Codex rollout not found for thread {thread_id}" in overridden.stderr


def test_session_snapshot_prefers_active_duplicate_rollout(run_oaw, vault):
    thread_id = "019f5004-3333-7444-8555-e48cc7f6bb15"
    codex_home = vault / "harness/codex"
    filename = f"rollout-2026-07-11T11-00-00-{thread_id}.jsonl"
    active = codex_home / "sessions/2026/07/11" / filename
    archived = codex_home / "archived_sessions" / filename
    write(active, '{"timestamp":"2026-07-11T11:00:00.000Z","content":"active winner"}\n')
    write(
        archived,
        '{"timestamp":"2026-07-11T11:00:00.000Z","content":"archived duplicate"}\n',
    )
    output_root = vault / "attachments"

    proc = run_oaw(
        "session",
        "snapshot",
        thread_id,
        "--codex-only",
        "--codex-rollout",
        filename,
        "--slug",
        "active precedence",
        "--output-root",
        str(output_root),
        env={"CODEX_HOME": str(codex_home)},
    )

    assert proc.returncode == 0, proc.stderr
    snapshot = output_root / "2026-07-11-active-precedence"
    copied = snapshot / "codex" / filename
    assert "active winner" in copied.read_text(encoding="utf-8")
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    sources = [
        entry["source"] for entry in manifest["files"] if entry["category"] == "codex-rollout"
    ]
    assert sources == [str(active)]
    assert str(archived) not in sources


def test_session_snapshot_rejects_duplicate_rollout_filename_within_one_root(run_oaw, vault):
    session_id = "019f5005-4444-7555-8666-f59dd806cc26"
    claude_root = vault / "harness/claude/projects"
    codex_root = vault / "harness/codex/sessions"
    filename = "rollout-2026-07-11T12-00-00-019f5006-5555-7666-8777-a60ee917dd37.jsonl"
    write(
        claude_root / "-tmp-project" / f"{session_id}.jsonl",
        f'{{"timestamp":"2026-07-11T12:00:00.000Z","sessionId":"{session_id}"}}\n',
    )
    first = codex_root / "2026/07/11" / filename
    second = codex_root / "restored" / filename
    write(first, '{"content":"first"}\n')
    write(second, '{"content":"second"}\n')

    proc = run_oaw(
        "session",
        "snapshot",
        session_id,
        "--codex-rollout",
        filename,
        "--output-root",
        str(vault / "attachments"),
        "--claude-root",
        str(claude_root),
        "--codex-root",
        str(codex_root),
    )

    assert proc.returncode == 1
    assert f"Codex rollout '{filename}' is not unique" in proc.stderr
    assert str(first) in proc.stderr
    assert str(second) in proc.stderr


def test_session_snapshot_codex_only_requires_the_primary_rollout(run_oaw, vault):
    thread_id = "019f48d7-39c2-7043-9c19-5a3565995898"
    unrelated_id = "019f48d8-1111-7222-8333-c26aa5d38893"
    codex_root = vault / "harness/codex/sessions"
    write(
        codex_root / f"rollout-2026-07-10T00-00-00-{unrelated_id}.jsonl",
        "unrelated marker\n",
    )

    proc = run_oaw(
        "session",
        "snapshot",
        thread_id,
        "--codex-only",
        "--grep",
        "unrelated marker",
        "--output-root",
        str(vault / "attachments"),
        "--codex-root",
        str(codex_root),
    )

    assert proc.returncode != 0
    assert f"Codex rollout not found for thread {thread_id}" in proc.stderr


def test_session_snapshot_codex_only_rejects_non_uuid_thread(run_oaw, vault):
    proc = run_oaw(
        "session",
        "snapshot",
        "*",
        "--codex-only",
        "--codex-root",
        str(vault / "harness/codex/sessions"),
    )

    assert proc.returncode != 0
    assert "requires a full Codex thread UUID" in proc.stderr


def test_session_snapshot_rejects_partial_and_complete_on_stderr(run_oaw):
    proc = run_oaw(
        "session",
        "snapshot",
        "019f48d7-39c2-7043-9c19-5a3565995898",
        "--partial",
        "--complete",
    )

    assert proc.returncode == 1
    assert proc.stdout == ""
    assert proc.stderr == "oaw: --partial and --complete are mutually exclusive\n"


def test_session_snapshot_accepts_repeated_codex_thread_options(run_oaw, vault):
    primary_thread = "019f48d7-39c2-7043-9c19-5a3565995898"
    extra_threads = (
        "019f48d8-1111-7222-8333-c26aa5d38893",
        "019f48d9-2222-7333-8444-d37bb6e49904",
    )
    codex_root = vault / "harness/codex/sessions"
    output_root = vault / "attachments"
    rollouts = []
    for index, thread_id in enumerate((primary_thread, *extra_threads)):
        rollout = codex_root / f"rollout-{index}-{thread_id}.jsonl"
        write(rollout, "{}\n")
        rollouts.append(rollout)

    proc = run_oaw(
        "session",
        "snapshot",
        primary_thread,
        "--codex-only",
        "--partial",
        "--date",
        "2026-07-13",
        "--codex-thread",
        extra_threads[0],
        "--codex-thread",
        extra_threads[1],
        "--output-root",
        str(output_root),
        "--codex-root",
        str(codex_root),
        "--claude-root",
        str(vault / "missing-claude"),
        "--plugin-data-root",
        str(vault / "missing-plugin"),
    )

    assert proc.returncode == 0, proc.stderr
    snapshot = output_root / "2026-07-13-019f48d7"
    assert all((snapshot / "codex" / rollout.name).exists() for rollout in rollouts)


def test_session_snapshot_accepts_other_repeated_options(run_oaw, vault):
    session_id = "019f5ac2-efd4-7171-965a-6e6f8d0a1a27"
    fork_session_ids = (
        "019f5ac3-1111-7222-8333-c26aa5d38893",
        "019f5ac4-2222-7333-8444-d37bb6e49904",
    )
    claude_root = vault / "harness/claude/projects"
    codex_root = vault / "harness/codex/sessions"
    output_root = vault / "attachments"
    write(
        claude_root / "-tmp-project" / f"{session_id}.jsonl",
        f'{{"timestamp":"2026-07-13T10:00:00.000Z","sessionId":"{session_id}"}}\n',
    )
    for session in fork_session_ids:
        write(
            claude_root / "-tmp-project" / f"{session}.jsonl",
            f'{{"timestamp":"2026-07-13T10:01:00.000Z","sessionId":"{session}"}}\n',
        )
    explicit_rollouts = (
        codex_root / "rollout-explicit-one.jsonl",
        codex_root / "rollout-explicit-two.jsonl",
    )
    grep_rollouts = (
        codex_root / "rollout-grep-one.jsonl",
        codex_root / "rollout-grep-two.jsonl",
    )
    for rollout in explicit_rollouts:
        write(rollout, "{}\n")
    write(grep_rollouts[0], '{"content":"first repeated grep marker"}\n')
    write(grep_rollouts[1], '{"content":"second repeated grep marker"}\n')

    proc = run_oaw(
        "session",
        "snapshot",
        session_id,
        "--slug",
        "repeated options",
        "--codex-rollout",
        str(explicit_rollouts[0]),
        "--codex-rollout",
        str(explicit_rollouts[1]),
        "--claude-session",
        fork_session_ids[0],
        "--claude-session",
        fork_session_ids[1],
        "--grep",
        "first repeated grep marker",
        "--grep",
        "second repeated grep marker",
        "--output-root",
        str(output_root),
        "--claude-root",
        str(claude_root),
        "--codex-root",
        str(codex_root),
        "--plugin-data-root",
        str(vault / "missing-plugin"),
    )

    assert proc.returncode == 0, proc.stderr
    snapshot = output_root / "2026-07-13-repeated-options"
    for session in fork_session_ids:
        assert (snapshot / f"claude/forks/parent-{session[:8]}.jsonl").exists()
    for rollout in (*explicit_rollouts, *grep_rollouts):
        assert (snapshot / "codex" / rollout.name).exists()


def test_session_snapshot_does_not_treat_bare_session_id_as_fork_parent(run_oaw, vault):
    session_id = "019f3ed8-245c-79f3-8ec6-c1ba30e3646d"
    unrelated_id = "019f9999-1111-7222-8333-c26aa5d38893"
    claude_root = vault / "harness/claude/projects"
    parent = claude_root / "-tmp-project" / f"{session_id}.jsonl"
    write(
        parent,
        f'{{"timestamp":"2026-07-08T01:00:00.000Z","sessionId":"{session_id}",'
        f'"content":"payload sessionId: {unrelated_id}"}}\n',
    )
    write(
        claude_root / "-tmp-project" / f"{unrelated_id}.jsonl",
        f'{{"timestamp":"2026-07-08T02:00:00.000Z","sessionId":"{unrelated_id}"}}\n',
    )
    output_root = vault / "attachments"

    proc = run_oaw(
        "session",
        "snapshot",
        session_id,
        "--output-root",
        str(output_root),
        "--claude-root",
        str(claude_root),
        "--codex-root",
        str(vault / "missing-codex"),
        "--plugin-data-root",
        str(vault / "missing-plugin"),
    )

    assert proc.returncode == 0, proc.stderr
    snapshot = output_root / "2026-07-08-019f3ed8"
    assert not (snapshot / "claude/forks/parent-019f9999.jsonl").exists()


def test_session_snapshot_grep_fails_on_ambiguous_rollouts(run_oaw, vault):
    session_id = "019f3ed8-245c-79f3-8ec6-c1ba30e3646d"
    claude_root = vault / "harness/claude/projects"
    codex_root = vault / "harness/codex/sessions"
    write(
        claude_root / "-tmp-project" / f"{session_id}.jsonl",
        f'{{"timestamp":"2026-07-08T01:00:00.000Z","sessionId":"{session_id}"}}\n',
    )
    write(codex_root / "2026/07/08/rollout-a.jsonl", "shared marker\n")
    write(codex_root / "2026/07/08/rollout-b.jsonl", "shared marker\n")

    proc = run_oaw(
        "session",
        "snapshot",
        session_id,
        "--grep",
        "shared marker",
        "--output-root",
        str(vault / "attachments"),
        "--claude-root",
        str(claude_root),
        "--codex-root",
        str(codex_root),
        "--plugin-data-root",
        str(vault / "missing-plugin"),
    )
    assert proc.returncode != 0
    assert "matched multiple Codex rollouts" in proc.stderr
