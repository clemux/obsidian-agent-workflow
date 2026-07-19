"""Session environment detection and lookup command support."""

from __future__ import annotations

import glob
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import OawError

SESSION_ENV = (
    ("Codex", "CODEX_THREAD_ID"),
    ("Claude Code", "CLAUDE_SESSION_ID"),
    ("Claude Code", "CLAUDE_CODE_SESSION_ID"),
    ("OpenCode", "OPENCODE_SESSION_ID"),
    ("Gemini", "GEMINI_SESSION_ID"),
)


@dataclass(frozen=True)
class SessionArtifact:
    kind: str
    path: Path


def find_session_artifacts(
    session_id: str,
    codex_root: Path,
    claude_root: Path,
) -> list[SessionArtifact]:
    artifacts: list[SessionArtifact] = []
    escaped_id = glob.escape(session_id)
    if codex_root.exists():
        for path in sorted(codex_root.rglob(f"rollout-*-{escaped_id}.jsonl")):
            if path.is_file():
                artifacts.append(SessionArtifact("codex-rollout", path))
    if claude_root.exists():
        for path in sorted(claude_root.rglob(f"{escaped_id}.jsonl")):
            if path.is_file():
                artifacts.append(SessionArtifact("claude-transcript", path))
        for path in sorted(claude_root.rglob(f"subagents/agent-{escaped_id}.jsonl")):
            if path.is_file():
                artifacts.append(SessionArtifact("claude-subagent", path))
    return artifacts


def text_from_json(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        parts = [part for item in value if (part := text_from_json(item))]
        return "\n".join(parts) if parts else None
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"].strip() or None
        if isinstance(value.get("content"), str):
            return value["content"].strip() or None
        if isinstance(value.get("message"), str):
            return value["message"].strip() or None
        for key in ("content", "message", "messages", "parts"):
            if key in value and (text := text_from_json(value[key])):
                return text
    return None


def iter_json_values(value: object):
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from iter_json_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_json_values(item)


def cwd_from_record(record: object) -> str | None:
    for value in iter_json_values(record):
        if isinstance(value, dict) and isinstance(value.get("cwd"), str):
            return value["cwd"]
    return None


def user_message_from_record(record: object) -> str | None:
    if not isinstance(record, dict):
        return None
    payload = record.get("payload")
    if isinstance(payload, dict) and (text := user_message_from_record(payload)):
        return text
    role = str(record.get("role", "")).lower()
    record_type = str(record.get("type", "")).lower()
    if role != "user" and "user" not in record_type:
        nested_message = record.get("message")
        if (
            not isinstance(nested_message, dict)
            or str(nested_message.get("role", "")).lower() != "user"
        ):
            return None
        text = text_from_json(nested_message)
    else:
        text = text_from_json(record)
    if not text:
        return None
    first_line = " ".join(text.split())
    if first_line.startswith("# AGENTS.md instructions") or first_line.startswith(
        "<environment_context>"
    ):
        return None
    return first_line[:240]


def vault_paths_from_text(text: str) -> list[str]:
    pattern = re.compile(
        r"\b(?:Projects|Agents|References|Captures|Research)/"
        r"[^\n\r\t`\"'<>|]+?\.md\b"
    )
    seen: set[str] = set()
    paths: list[str] = []
    for match in pattern.finditer(text):
        path = match.group(0).strip()
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
        if len(paths) >= 10:
            break
    return paths


def summarize_artifact(path: Path) -> tuple[str | None, str | None, list[str]]:
    cwd: str | None = None
    first_user: str | None = None
    raw_text_parts: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                raw_text_parts.append(line)
                if cwd and first_user:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if cwd is None:
                    cwd = cwd_from_record(record)
                if first_user is None:
                    first_user = user_message_from_record(record)
    except OSError:
        return None, None, []
    return cwd, first_user, vault_paths_from_text("".join(raw_text_parts))


def session_lookup(
    note_hits: Sequence[tuple[str, str | None]],
    session_id: str,
    verbose: bool,
    codex_root: Path,
    claude_root: Path,
) -> None:
    session_id = session_id.strip()
    if not session_id:
        raise OawError("empty session ID")

    # Preserve the fast, output-compatible path for the common non-verbose
    # lookup.  Verbose lookups intentionally continue into harness discovery
    # so a session recorded in vault frontmatter can still show its artifact
    # metrics.
    if note_hits and not verbose:
        print(f"Session: {session_id}")
        print("Vault matches:")
        for relpath, hit_id in note_hits:
            note_id = hit_id or "(no id)"
            print(f"- {relpath} | id: {note_id}")
        return

    artifacts = find_session_artifacts(
        session_id,
        codex_root.expanduser(),
        claude_root.expanduser(),
    )
    print(f"Session: {session_id}")
    if note_hits:
        print("Vault matches:")
        for relpath, hit_id in note_hits:
            note_id = hit_id or "(no id)"
            print(f"- {relpath} | id: {note_id}")

    if not artifacts:
        if note_hits:
            return
        print("Status: not logged")
        print("No vault note or harness artifact found.")
        return

    print("Harness artifacts:")
    for artifact in artifacts:
        cwd, first_user, vault_paths = summarize_artifact(artifact.path)
        print(f"- {artifact.kind}: {artifact.path}")
        print(f"  cwd: {cwd or '(unknown)'}")
        print(f"  first user: {first_user or '(unknown)'}")
        if vault_paths:
            print("  vault paths:")
            for vault_path in vault_paths:
                print(f"    - {vault_path}")
        else:
            print("  vault paths: (none)")
        if verbose:
            from .session_metrics import (
                SessionMetrics,
                codex_rollout_metrics,
                format_duration,
                format_timestamp,
                format_tokens,
            )

            metrics = (
                codex_rollout_metrics(artifact.path)
                if artifact.kind == "codex-rollout"
                else SessionMetrics()
            )
            user_turns = (
                str(metrics.user_turns) if metrics.user_turns is not None else "unavailable"
            )
            assistant_turns = (
                str(metrics.assistant_turns)
                if metrics.assistant_turns is not None
                else "unavailable"
            )
            print(f"  Started: {format_timestamp(metrics.started)}")
            print(f"  Ended: {format_timestamp(metrics.ended)}")
            print(f"  Duration: {format_duration(metrics.duration)}")
            print(f"  Turns: user={user_turns}, assistant={assistant_turns}")
            print(f"  Tokens: {format_tokens(metrics)}")


def default_claude_projects_root() -> Path:
    return Path(os.environ.get("OAW_CLAUDE_PROJECTS_ROOT", "~/.claude/projects")).expanduser()


def default_codex_sessions_root() -> Path:
    return Path(os.environ.get("OAW_CODEX_SESSIONS_ROOT", "~/.codex/sessions")).expanduser()


def session_lookup_codex_root() -> Path:
    return default_codex_sessions_root()


def session_lookup_claude_root() -> Path:
    return default_claude_projects_root()


def default_plugin_data_root() -> Path:
    return Path("~/.claude/plugins/data").expanduser()


def detect_session(allow_missing: bool) -> tuple[str, str]:
    from .runs import detect_identity

    try:
        identity = detect_identity()
    except OawError:
        if allow_missing:
            return "Unknown", "session_id=unavailable"
        raise
    return identity.provider_label, f"{identity.env}={identity.session_id}"
