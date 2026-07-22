"""Durable agent-run registry and transactional vault writes."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import OawError
from .filenames import portable_filename_component
from .frontmatter import parse_frontmatter, set_frontmatter_scalar
from .notes import split_note

RUNS_DIR = Path("Agents/Runs")
RUN_STATES = {"running", "paused", "completed", "closed"}
TERMINAL_TASK_STATES = {"done", "superseded", "archived"}
STALE_AFTER = dt.timedelta(hours=24)
TASK_LINK_PATTERN = re.compile(r"^\[\[([^]|]+)\|([^]|]+)\]\]$")
RUN_REQUIRED_KEYS = {
    "type",
    "id",
    "task",
    "task_id",
    "provider",
    "agent_session_id",
    "agent_session_env",
    "session-ids",
    "run_state",
    "started_at",
    "last_event_at",
}
RUN_OPTIONAL_KEYS = {"project", "ended_at", "ended_reason"}
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
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def format_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.UTC)


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
    raise OawError(
        f"no stable session ID found; run lifecycle requires a real session ID; set one of: {names}"
    )


def run_id(task_id: str, identity: Identity) -> str:
    digest = hashlib.sha256(identity.session_id.encode()).hexdigest()[:12]
    return f"AGT-RUN-{task_id}-{identity.provider}-{digest}"


def registry_directory(root: Path) -> Path:
    """Return the logical registry directory after real-vault confinement checks."""
    try:
        real_root = root.resolve(strict=True)
    except OSError as exc:
        raise OawError(f"vault root cannot be resolved: {root}") from exc
    if not real_root.is_dir():
        raise OawError(f"vault root is not a directory: {root}")

    current = root
    for part in RUNS_DIR.parts:
        current /= part
        if current.is_symlink():
            raise OawError(f"run registry directory must not be a symlink: {current}")
        if not current.exists():
            continue
        try:
            resolved = current.resolve(strict=True)
        except OSError as exc:
            raise OawError(f"run registry path cannot be resolved: {current}") from exc
        if not resolved.is_relative_to(real_root):
            raise OawError(f"run registry path escapes the vault: {current}")
        if not current.is_dir():
            raise OawError(f"run registry path is not a directory: {current}")
    return root / RUNS_DIR


def registry_entries(root: Path) -> list[Path]:
    """List registry files and symlinks without following any symlink."""
    directory = registry_directory(root)
    if not directory.exists():
        return []
    real_root = root.resolve(strict=True)
    entries: list[Path] = []
    pending = [directory]
    while pending:
        parent = pending.pop()
        try:
            children = sorted(parent.iterdir())
        except OSError as exc:
            raise OawError(f"run registry cannot be read: {parent}") from exc
        for child in children:
            if child.is_symlink():
                entries.append(child)
                continue
            try:
                resolved = child.resolve(strict=True)
            except OSError as exc:
                raise OawError(f"run registry path cannot be resolved: {child}") from exc
            if not resolved.is_relative_to(real_root):
                raise OawError(f"run registry path escapes the vault: {child}")
            if child.is_dir():
                pending.append(child)
            else:
                entries.append(child)
    return sorted(entries)


def ensure_registry_has_no_symlinks(root: Path) -> Path:
    directory = registry_directory(root)
    links = [path for path in registry_entries(root) if path.is_symlink()]
    if links:
        rendered = ", ".join(path.relative_to(root).as_posix() for path in links)
        raise OawError(f"run registry contains symlink entries: {rendered}")
    return directory


def run_path(root: Path, identifier: str) -> Path:
    try:
        portable_filename_component(identifier, "run id")
    except OawError as exc:
        raise OawError(f"invalid run id: {identifier!r}; {exc}") from exc
    if (
        not identifier.startswith("AGT-RUN-")
        or Path(identifier).name != identifier
        or "/" in identifier
        or "\\" in identifier
    ):
        raise OawError(f"invalid run id: {identifier}")
    directory = ensure_registry_has_no_symlinks(root)
    path = directory / f"{identifier}.md"
    if path.exists() and path.resolve(strict=True).parent != directory.resolve(strict=True):
        raise OawError(f"run registry entry escapes the vault: {path}")
    return path


def durable_task_link(task_path: Path, root: Path, task_id: str) -> str:
    target = task_path.relative_to(root).with_suffix("").as_posix()
    return f"[[{target}|{task_id}]]"


def load_run(root: Path, path: Path) -> Run:
    directory = ensure_registry_has_no_symlinks(root)
    if path.parent != directory or path.is_symlink():
        raise OawError(f"run registry entry is not confined: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise OawError(f"run registry entry cannot be resolved: {path}") from exc
    if resolved.parent != directory.resolve(strict=True):
        raise OawError(f"run registry entry escapes the vault: {path}")
    text = path.read_text(encoding="utf-8")
    _, fm, body = split_note(text)
    return Run(path, parse_frontmatter(fm), body)


def run_scope_errors(
    run: Run,
    *,
    expected_id: str,
    task_id: str | None = None,
    identity: Identity | None = None,
    task_link: str | None = None,
    require_canonical_id: bool = False,
) -> list[str]:
    """Return deterministic identity and task-scope mismatches for a loaded run."""
    errors: list[str] = []
    if run.id != expected_id:
        errors.append(
            f"id/filename mismatch: {run.id or '<missing>'!r} does not match {expected_id!r}"
        )
    if task_id is not None and run.data.get("task_id") != task_id:
        errors.append(f"task_id does not match {task_id!r}")
    if identity is not None:
        expected_identity = {
            "provider": identity.provider,
            "agent_session_id": identity.session_id,
        }
        for key, expected in expected_identity.items():
            if run.data.get(key) != expected:
                errors.append(f"{key} does not match {expected!r}")
    if task_link is not None and run.data.get("task") != task_link:
        errors.append("task link does not match resolved task path/id")
    if require_canonical_id:
        stored_task_id = run.data.get("task_id")
        provider = run.data.get("provider")
        session_id = run.data.get("agent_session_id")
        identity_values = (stored_task_id, provider, session_id)
        if all(isinstance(value, str) and value for value in identity_values):
            canonical = run_id(
                str(stored_task_id),
                Identity(str(provider), str(provider), str(session_id), ""),
            )
            if canonical != expected_id or canonical != run.id:
                errors.append(f"run-id/identity mismatch: expected {canonical!r}")
        else:
            errors.append("deterministic id fields are missing or invalid")
    return errors


def run_schema_errors(run: Run) -> list[str]:
    """Return mutable-record schema errors without resolving the linked task."""
    errors: list[str] = []
    missing = sorted(key for key in RUN_REQUIRED_KEYS if not run.data.get(key))
    if missing:
        errors.append(f"malformed: missing {', '.join(missing)}")
    extra = sorted(set(run.data) - RUN_REQUIRED_KEYS - RUN_OPTIONAL_KEYS)
    if extra:
        errors.append(f"noncanonical schema keys: {', '.join(extra)}")
    if run.data.get("type") != "agent-run":
        errors.append(f"malformed type {run.data.get('type', '<missing>')}")
    project = run.data.get("project")
    if project is not None and (not isinstance(project, str) or not project):
        errors.append("malformed project")

    provider = run.data.get("provider")
    session_id = run.data.get("agent_session_id")
    session_env = run.data.get("agent_session_env")
    identity_values = (provider, session_id, session_env)
    if not all(isinstance(value, str) and value for value in identity_values):
        errors.append("malformed run identity")
    elif not any(
        provider == supported_provider and session_env == supported_env
        for supported_provider, _, supported_env in SESSION_ENV
    ):
        errors.append("unsupported provider/session environment")

    session_ids = run.data.get("session-ids")
    if not isinstance(session_ids, list) or not all(
        isinstance(value, str) and value for value in session_ids
    ):
        errors.append("malformed session-ids")
    else:
        if len(session_ids) != len(set(session_ids)):
            errors.append("duplicate session-ids")
        if isinstance(session_id, str) and session_id not in session_ids:
            errors.append("agent_session_id missing from session-ids")

    started_at = parse_utc(run.data.get("started_at"))
    last_event_at = parse_utc(run.data.get("last_event_at"))
    if started_at is None:
        errors.append("malformed started_at")
    if last_event_at is None:
        errors.append("malformed last_event_at")
    if started_at and last_event_at and last_event_at < started_at:
        errors.append("last_event_at precedes started_at")

    ended_at_value = run.data.get("ended_at")
    ended_reason = run.data.get("ended_reason")
    ended_at = parse_utc(ended_at_value)
    if run.state not in RUN_STATES:
        errors.append(f"malformed run_state {run.state}")
    elif run.state in {"completed", "closed"}:
        if ended_at is None:
            errors.append("terminal run missing valid ended_at")
        if not isinstance(ended_reason, str) or not ended_reason:
            errors.append("terminal run missing ended_reason")
        if started_at and ended_at and ended_at < started_at:
            errors.append("ended_at precedes started_at")
        if last_event_at and ended_at and ended_at < last_event_at:
            errors.append("ended_at precedes last_event_at")
    elif ended_at_value is not None or ended_reason is not None:
        errors.append("non-terminal run has end metadata")

    task_value = run.data.get("task")
    if not isinstance(task_value, str) or TASK_LINK_PATTERN.fullmatch(task_value) is None:
        errors.append("malformed task link")
    return errors


def validate_run_schema(run: Run) -> None:
    errors = run_schema_errors(run)
    if errors:
        raise OawError(f"run record validation failed for {run.path.stem}: {'; '.join(errors)}")


def load_validated_run(
    root: Path,
    path: Path,
    *,
    expected_id: str,
    task_id: str | None = None,
    identity: Identity | None = None,
    task_link: str | None = None,
    require_canonical_id: bool = False,
) -> Run:
    """Load a run and reject deterministic identity or task-scope mismatches."""
    run = load_run(root, path)
    errors = [
        *run_schema_errors(run),
        *run_scope_errors(
            run,
            expected_id=expected_id,
            task_id=task_id,
            identity=identity,
            task_link=task_link,
            require_canonical_id=require_canonical_id,
        ),
    ]
    if errors:
        raise OawError(f"run record validation failed for {expected_id}: {'; '.join(errors)}")
    return run


def validate_resolved_task_scope(
    run: Run,
    root: Path,
    resolve_task: Callable[[str], Any],
) -> Any:
    """Resolve and validate the canonical task link owned by a run."""
    task_id = run.data.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise OawError(f"run record validation failed for {run.path.stem}: invalid task_id")
    try:
        task = resolve_task(task_id)
    except OawError as exc:
        raise OawError(
            f"run record validation failed for {run.path.stem}: dangling task id {task_id}"
        ) from exc
    expected_link = durable_task_link(task.path, root, task_id)
    if getattr(task, "note_id", None) != task_id or run.data.get("task") != expected_link:
        raise OawError(
            f"run record validation failed for {run.path.stem}: "
            "task link does not match resolved task path/id"
        )
    return task


def noncanonical_registry_artifacts(root: Path) -> list[Path]:
    """Return files or links outside the canonical flat Markdown run layout."""
    directory = registry_directory(root)
    if not directory.exists():
        return []
    return [
        path
        for path in registry_entries(root)
        if (
            path.is_symlink()
            or not path.is_file()
            or path.parent != directory
            or path.suffix != ".md"
            or not path.stem.startswith("AGT-RUN-")
        )
    ]


def validated_registry_runs(
    root: Path,
    resolve_task: Callable[[str], Any],
) -> list[Run]:
    """Load the complete canonical registry, failing closed on any corrupt artifact."""
    artifacts = noncanonical_registry_artifacts(root)
    if artifacts:
        rendered = ", ".join(path.relative_to(root).as_posix() for path in artifacts)
        raise OawError(f"run registry contains noncanonical artifacts: {rendered}")
    runs: list[Run] = []
    for run in iter_runs(root):
        validated = load_validated_run(
            root,
            run.path,
            expected_id=run.path.stem,
            require_canonical_id=True,
        )
        validate_resolved_task_scope(validated, root, resolve_task)
        runs.append(validated)
    return runs


def iter_runs(root: Path) -> list[Run]:
    directory = ensure_registry_has_no_symlinks(root)
    if not directory.exists():
        return []
    artifacts = noncanonical_registry_artifacts(root)
    if artifacts:
        rendered = ", ".join(path.relative_to(root).as_posix() for path in artifacts)
        raise OawError(f"run registry contains noncanonical artifacts: {rendered}")
    return [load_run(root, path) for path in sorted(directory.glob("*.md"))]


def find_run(
    root: Path,
    identifier: str,
    resolve_task: Callable[[str], Any],
) -> Run:
    path = run_path(root, identifier)
    if not path.exists():
        raise OawError(f"run not found: {identifier}")
    run = load_validated_run(root, path, expected_id=identifier, require_canonical_id=True)
    validate_resolved_task_scope(run, root, resolve_task)
    return run


def runs_for_task(
    root: Path,
    task_id: str,
    resolve_task: Callable[[str], Any],
) -> list[Run]:
    return [
        run
        for run in validated_registry_runs(root, resolve_task)
        if run.data.get("task_id") == task_id
    ]


def matching_run(
    root: Path,
    task_id: str,
    identity: Identity,
    task_path: Path | None = None,
) -> Run | None:
    expected = run_id(task_id, identity)
    path = run_path(root, expected)
    if not path.exists():
        return None
    return load_validated_run(
        root,
        path,
        expected_id=expected,
        task_id=task_id,
        identity=identity,
        task_link=durable_task_link(task_path, root, task_id) if task_path else None,
        require_canonical_id=True,
    )


def running_others(
    root: Path,
    task_id: str,
    current_run_id: str,
    resolve_task: Callable[[str], Any],
) -> list[Run]:
    return [
        run
        for run in runs_for_task(root, task_id, resolve_task)
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
    checks: str | None = None,
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
        lines += [f"ended_at: {yaml_quote(stamp)}", f"ended_reason: {yaml_quote(state)}"]
    detail = f" — {note.strip()}" if note and note.strip() else ""
    if checks and checks.strip():
        detail += f" — verification: {checks.strip()}"
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
        text = set_frontmatter_scalar(text, "ended_reason", yaml_quote(ended_reason or state))
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


def audit_runs(root: Path, resolve_task: Callable[[str], Any], now: dt.datetime) -> list[str]:
    findings: list[str] = []
    live_keys: dict[tuple[str, str, str], list[str]] = {}
    directory = registry_directory(root)
    paths = registry_entries(root)
    for path in paths:
        relative = path.relative_to(directory).as_posix()
        if (
            path.is_symlink()
            or not path.is_file()
            or path.parent != directory
            or path.suffix != ".md"
            or not path.stem.startswith("AGT-RUN-")
        ):
            findings.append(f"{relative}: noncanonical registry artifact")
            continue
        prefix = path.stem
        try:
            run = load_run(root, path)
        except (OawError, OSError) as exc:
            findings.append(f"{prefix}: malformed: {exc}")
            continue
        findings.extend(f"{prefix}: {error}" for error in run_schema_errors(run))
        scope_errors = run_scope_errors(
            run,
            expected_id=prefix,
            require_canonical_id=True,
        )
        findings.extend(f"{prefix}: malformed: {error}" for error in scope_errors)

        task_id_value = run.data.get("task_id")
        task_id = task_id_value if isinstance(task_id_value, str) else ""
        provider = run.data.get("provider")
        session_id = run.data.get("agent_session_id")

        task_value = run.data.get("task")
        match = TASK_LINK_PATTERN.fullmatch(task_value) if isinstance(task_value, str) else None
        task = None
        if task_id:
            try:
                task = resolve_task(task_id)
            except OawError:
                findings.append(f"{prefix}: dangling task id {task_id}")
        if match and task is not None:
            target, label = match.groups()
            expected = task.path.relative_to(root).with_suffix("").as_posix()
            if target != expected or label != task_id or getattr(task, "note_id", None) != task_id:
                findings.append(f"{prefix}: task-link/id mismatch")
        if is_stale(run, now):
            findings.append(f"{prefix}: stale")
        if (
            run.state == "running"
            and task is not None
            and str(task.frontmatter.get("status", "")) in TERMINAL_TASK_STATES
        ):
            findings.append(f"{prefix}: running on terminal task")
        if (
            run.state == "running"
            and task_id
            and isinstance(provider, str)
            and isinstance(session_id, str)
        ):
            key = (task_id, provider, session_id)
            live_keys.setdefault(key, []).append(prefix)
    for key, identifiers in live_keys.items():
        if len(identifiers) > 1:
            findings.append(f"duplicate live key {key}: {', '.join(sorted(identifiers))}")
    return sorted(findings)
