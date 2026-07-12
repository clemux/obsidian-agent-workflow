"""Durable agent-run registry and transactional vault writes."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import OawError
from .frontmatter import parse_frontmatter, set_frontmatter_scalar
from .notes import split_note

RUNS_DIR = Path("Agents/Runs")
RUN_STATES = {"running", "paused", "completed", "closed"}
TERMINAL_TASK_STATES = {"done", "superseded", "archived"}
STALE_AFTER = dt.timedelta(hours=24)
SESSION_ENV = (
    ("codex", "Codex", "CODEX_THREAD_ID"),
    ("claude-code", "Claude Code", "CLAUDE_SESSION_ID"),
    ("claude-code", "Claude Code", "CLAUDE_CODE_SESSION_ID"),
    ("opencode", "OpenCode", "OPENCODE_SESSION_ID"),
    ("gemini", "Gemini", "GEMINI_SESSION_ID"),
)


@dataclass(frozen=True)
class Identity:
    provider: str
    provider_label: str
    session_id: str
    env: str


@dataclass(frozen=True)
class Run:
    path: Path
    data: dict[str, object]
    body: str

    @property
    def id(self) -> str:
        return str(self.data.get("id", ""))

    @property
    def state(self) -> str:
        return str(self.data.get("run_state", ""))


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def format_utc(value: dt.datetime) -> str:
    return (
        value.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def parse_utc(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def is_stale(run: Run, now: dt.datetime) -> bool:
    last = parse_utc(run.data.get("last_event_at"))
    return last is not None and now - last > STALE_AFTER


def detect_identity(environ: dict[str, str] | None = None) -> Identity:
    values = os.environ if environ is None else environ
    for provider, label, env in SESSION_ENV:
        session_id = values.get(env, "").strip()
        if session_id:
            return Identity(provider, label, session_id, env)
    names = ", ".join(item[2] for item in SESSION_ENV)
    raise OawError(f"run lifecycle requires a real session ID; set one of: {names}")


def run_id(task_id: str, identity: Identity) -> str:
    digest = hashlib.sha256(identity.session_id.encode()).hexdigest()[:12]
    return f"AGT-RUN-{task_id}-{identity.provider}-{digest}"


def run_path(root: Path, identifier: str) -> Path:
    return root / RUNS_DIR / f"{identifier}.md"


def durable_task_link(task_path: Path, root: Path, task_id: str) -> str:
    target = task_path.relative_to(root).with_suffix("").as_posix()
    return f"[[{target}|{task_id}]]"


def load_run(path: Path) -> Run:
    text = path.read_text(encoding="utf-8")
    _, fm, body = split_note(text)
    return Run(path, parse_frontmatter(fm), body)


def iter_runs(root: Path) -> list[Run]:
    directory = root / RUNS_DIR
    if not directory.exists():
        return []
    return [load_run(path) for path in sorted(directory.glob("AGT-RUN-*.md"))]


def find_run(root: Path, identifier: str) -> Run:
    path = run_path(root, identifier)
    if not path.exists():
        raise OawError(f"run not found: {identifier}")
    return load_run(path)


def runs_for_task(root: Path, task_id: str) -> list[Run]:
    return [run for run in iter_runs(root) if run.data.get("task_id") == task_id]


def matching_run(root: Path, task_id: str, identity: Identity) -> Run | None:
    expected = run_id(task_id, identity)
    path = run_path(root, expected)
    return load_run(path) if path.exists() else None


def running_others(root: Path, task_id: str, current_run_id: str) -> list[Run]:
    return [
        run
        for run in runs_for_task(root, task_id)
        if run.state == "running" and run.id != current_run_id
    ]


def yaml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def new_run_text(
    root: Path,
    task_path: Path,
    task_data: dict[str, object],
    identity: Identity,
    now: dt.datetime,
    state: str = "running",
    event: str = "start",
    note: str | None = None,
) -> tuple[str, str]:
    task_id = str(task_data["id"])
    identifier = run_id(task_id, identity)
    stamp = format_utc(now)
    lines = [
        "---",
        "type: agent-run",
        f"id: {identifier}",
        f"task: {yaml_quote(durable_task_link(task_path, root, task_id))}",
        f"task_id: {yaml_quote(task_id)}",
    ]
    project = task_data.get("project")
    if isinstance(project, str) and project:
        lines.append(f"project: {yaml_quote(project)}")
    lines += [
        f"provider: {identity.provider}",
        f"agent_session_id: {yaml_quote(identity.session_id)}",
        f"agent_session_env: {identity.env}",
        "session-ids:",
        f"  - {yaml_quote(identity.session_id)}",
        f"run_state: {state}",
        f"started_at: {yaml_quote(stamp)}",
        f"last_event_at: {yaml_quote(stamp)}",
    ]
    if state in {"completed", "closed"}:
        lines += [f"ended_at: {yaml_quote(stamp)}", f"ended_reason: {state}"]
    detail = f" — {note.strip()}" if note and note.strip() else ""
    lines += ["---", "", f"# {identifier}", "", "## Events", "", f"- {stamp} — {event}{detail}", ""]
    return identifier, "\n".join(lines)


def append_event(
    text: str,
    event: str,
    now: dt.datetime,
    note: str | None = None,
    checks: str | None = None,
) -> str:
    stamp = format_utc(now)
    detail = f" — {note.strip()}" if note and note.strip() else ""
    if checks and checks.strip():
        detail += f" — verification: {checks.strip()}"
    line = f"- {stamp} — {event}{detail}\n"
    if "## Events" not in text:
        return text.rstrip() + "\n\n## Events\n\n" + line
    return text.rstrip() + "\n" + line


def transition_run_text(
    run: Run,
    state: str,
    event: str,
    now: dt.datetime,
    note: str | None = None,
    checks: str | None = None,
    ended_reason: str | None = None,
    closer: Identity | None = None,
) -> str:
    if state not in RUN_STATES:
        raise OawError(f"invalid run state: {state}")
    text = run.path.read_text(encoding="utf-8")
    text = set_frontmatter_scalar(text, "run_state", state)
    text = set_frontmatter_scalar(text, "last_event_at", yaml_quote(format_utc(now)))
    if state in {"completed", "closed"}:
        text = set_frontmatter_scalar(text, "ended_at", yaml_quote(format_utc(now)))
        text = set_frontmatter_scalar(text, "ended_reason", ended_reason or state)
    if state == "running":
        text = remove_frontmatter_keys(text, {"ended_at", "ended_reason"})
    if closer:
        text = append_session_id(text, closer.session_id)
    return append_event(text, event, now, note, checks)


def remove_frontmatter_keys(text: str, keys: set[str]) -> str:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise OawError("run record has no YAML frontmatter")
    end = next((idx for idx in range(1, len(lines)) if lines[idx].strip() == "---"), None)
    if end is None:
        raise OawError("run record frontmatter is not closed")
    kept = (
        [lines[0]]
        + [line for line in lines[1:end] if line.split(":", 1)[0].strip() not in keys]
        + lines[end:]
    )
    return "".join(kept)


def append_session_id(text: str, session_id: str) -> str:
    _, fm, _ = split_note(text)
    data = parse_frontmatter(fm)
    existing = data.get("session-ids", [])
    if isinstance(existing, list) and session_id in existing:
        return text
    lines = text.splitlines(keepends=True)
    end = next(idx for idx in range(1, len(lines)) if lines[idx].strip() == "---")
    key = next((idx for idx in range(1, end) if lines[idx].startswith("session-ids:")), None)
    if key is None:
        lines[end:end] = ["session-ids:\n", f"  - {yaml_quote(session_id)}\n"]
    else:
        insert = key + 1
        while insert < end and (lines[insert].startswith("  - ") or not lines[insert].strip()):
            insert += 1
        lines.insert(insert, f"  - {yaml_quote(session_id)}\n")
    return "".join(lines)


class VaultTransaction:
    """Atomically replace a group of files, restoring all originals on failure."""

    def __init__(self) -> None:
        self.changes: dict[Path, str] = {}

    def stage(self, path: Path, text: str) -> None:
        self.changes[path] = text

    def commit(self, replace: Callable[[str, str], None] = os.replace) -> None:
        originals = {path: path.read_bytes() if path.exists() else None for path in self.changes}
        written: list[Path] = []
        temps: list[Path] = []
        try:
            for path, text in self.changes.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    "w", encoding="utf-8", dir=path.parent, delete=False
                ) as handle:
                    handle.write(text)
                    handle.flush()
                    os.fsync(handle.fileno())
                    temp = Path(handle.name)
                temps.append(temp)
                replace(str(temp), str(path))
                written.append(path)
            touched_dirs = {path.parent for path in self.changes}
            for directory in touched_dirs:
                fd = os.open(directory, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        except Exception as exc:
            for path in reversed(written):
                original = originals[path]
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(original)
            raise OawError(f"transaction failed and was rolled back: {exc}") from exc
        finally:
            for temp in temps:
                temp.unlink(missing_ok=True)


def audit_runs(root: Path, resolve_task: Callable[[str], Any], now: dt.datetime) -> list[str]:
    findings: list[str] = []
    live_keys: dict[tuple[str, str, str], list[str]] = {}
    link_pattern = re.compile(r"^\[\[([^]|]+)\|([^]|]+)\]\]$")
    for run in iter_runs(root):
        prefix = run.id or run.path.stem
        required = {
            "task",
            "task_id",
            "provider",
            "agent_session_id",
            "agent_session_env",
            "run_state",
            "started_at",
            "last_event_at",
        }
        missing = sorted(key for key in required if not run.data.get(key))
        if missing:
            findings.append(f"{prefix}: malformed: missing {', '.join(missing)}")
            continue
        task_id = str(run.data["task_id"])
        match = link_pattern.fullmatch(str(run.data["task"]))
        if not match:
            findings.append(f"{prefix}: malformed task link")
            continue
        target, label = match.groups()
        try:
            task = resolve_task(task_id)
        except OawError:
            findings.append(f"{prefix}: dangling task id {task_id}")
            continue
        expected = task.path.relative_to(root).with_suffix("").as_posix()
        if target != expected or label != task_id or getattr(task, "note_id", None) != task_id:
            findings.append(f"{prefix}: task-link/id mismatch")
        if run.state not in RUN_STATES:
            findings.append(f"{prefix}: malformed run_state {run.state}")
        if is_stale(run, now):
            findings.append(f"{prefix}: stale")
        if (
            run.state == "running"
            and str(task.frontmatter.get("status", "")) in TERMINAL_TASK_STATES
        ):
            findings.append(f"{prefix}: running on terminal task")
        if run.state == "running":
            key = (task_id, str(run.data["provider"]), str(run.data["agent_session_id"]))
            live_keys.setdefault(key, []).append(prefix)
    for key, identifiers in live_keys.items():
        if len(identifiers) > 1:
            findings.append(f"duplicate live key {key}: {', '.join(sorted(identifiers))}")
    return sorted(findings)
