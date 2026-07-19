"""Session artifact snapshot command implementation."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import OawError
from .sessions import SESSION_ENV, codex_rollout_paths

RETRO_ATTACHMENTS = Path("Agents/Retrospectives/attachments")


def slugify(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return slug or "session"


@dataclass(frozen=True)
class SnapshotCopy:
    source: Path
    destination: Path
    category: str
    completeness: str = "complete"


def iter_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in suffixes)


def iter_all_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file())


def snapshot_tree_copies(root: Path, destination: Path, category: str) -> list[SnapshotCopy]:
    copies: list[SnapshotCopy] = []
    for path in iter_all_files(root):
        copies.append(SnapshotCopy(path, destination / path.relative_to(root), category))
    return copies


def read_text_lossy(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def first_timestamp_date(path: Path) -> str | None:
    timestamp = re.compile(r'"timestamp"\s*:\s*"(\d{4}-\d{2}-\d{2})T')
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if match := timestamp.search(line):
                    return match.group(1)
    except OSError:
        return None
    return None


def find_claude_parent(session_id: str, claude_root: Path) -> Path:
    if not claude_root.exists():
        raise OawError(f"Claude projects root not found: {claude_root}")
    matches = sorted(claude_root.rglob(f"{session_id}.jsonl"))
    if not matches:
        raise OawError(f"Claude parent transcript not found for session {session_id}")
    if len(matches) > 1:
        paths = "\n".join(f"  {path}" for path in matches)
        raise OawError(f"session {session_id} is not unique under {claude_root}:\n{paths}")
    return matches[0]


def snapshot_date(parent: Path, override: str | None) -> str:
    if override:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", override):
            raise OawError("--date must use YYYY-MM-DD")
        return override
    return first_timestamp_date(parent) or dt.date.today().isoformat()


def detect_parent_completeness(session_id: str, partial: bool, complete: bool) -> str:
    if partial:
        return "partial"
    if complete:
        return "complete"
    env_values = {value for _, env_name in SESSION_ENV if (value := os.environ.get(env_name))}
    return "partial" if session_id in env_values else "complete"


def transcript_text(paths: list[Path]) -> str:
    chunks: list[str] = []
    for path in paths:
        try:
            chunks.append(read_text_lossy(path))
        except OSError:
            continue
    return "\n".join(chunks)


def referenced_codex_threads(text: str, explicit: list[str]) -> set[str]:
    thread_ref = re.compile(
        r"\b(?:CODEX_THREAD_ID|codex[_ -]?thread|codex[_ -]?session)"
        r'["`:\s=]+'
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
        re.IGNORECASE,
    )
    threads = {thread.strip() for thread in explicit if thread.strip()}
    for match in thread_ref.finditer(text):
        threads.add(match.group(1))
    return threads


def referenced_claude_sessions(
    text: str,
    explicit: list[str],
    current_session_id: str,
) -> set[str]:
    session_ref = re.compile(
        r"\b(?:CLAUDE_SESSION_ID|CLAUDE_CODE_SESSION_ID|claude[_ -]?session|"
        r"fork(?:ed)?[_ -]?(?:claude[_ -]?)?session|btw[_ -]?session)"
        r'["`:\s=]+'
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
        re.IGNORECASE,
    )
    sessions = {session.strip() for session in explicit if session.strip()}
    for match in session_ref.finditer(text):
        sessions.add(match.group(1))
    sessions.discard(current_session_id)
    return sessions


def find_extra_claude_parents(
    claude_root: Path,
    transcript: str,
    explicit_sessions: list[str],
    current_session_id: str,
) -> list[Path]:
    explicit = {session.strip() for session in explicit_sessions if session.strip()}
    sessions = referenced_claude_sessions(transcript, list(explicit), current_session_id)
    parents: list[Path] = []
    seen: set[Path] = set()
    for session_id in sorted(sessions):
        try:
            parent = find_claude_parent(session_id, claude_root)
        except OawError:
            if session_id in explicit:
                raise
            continue
        if parent not in seen:
            seen.add(parent)
            parents.append(parent)
    return parents


def find_codex_rollouts(
    codex_roots: Sequence[Path],
    transcript: str,
    explicit_threads: list[str],
    explicit_rollouts: list[str],
    grep_patterns: list[str],
) -> list[Path]:
    if not any(root.exists() for root in codex_roots):
        return []
    matches: dict[str, Path] = {}
    for rollout in explicit_rollouts:
        value = rollout.strip()
        if not value:
            continue
        candidate = Path(value).expanduser()
        rollout_matches = (
            [candidate] if candidate.is_file() else codex_rollout_paths(codex_roots, value)
        )
        if not rollout_matches:
            raise OawError(f"Codex rollout not found: {value}")
        if len(rollout_matches) > 1:
            paths = "\n".join(f"  {path}" for path in rollout_matches)
            raise OawError(f"Codex rollout '{value}' is not unique:\n{paths}")
        matches.setdefault(rollout_matches[0].name, rollout_matches[0])
    for thread_id in referenced_codex_threads(transcript, explicit_threads):
        for path in codex_rollout_paths(codex_roots, f"*{thread_id}*.jsonl"):
            matches.setdefault(path.name, path)
    for pattern in grep_patterns:
        if not pattern:
            continue
        pattern_matches: list[Path] = []
        for path in codex_rollout_paths(codex_roots, "*.jsonl"):
            try:
                if pattern in read_text_lossy(path):
                    pattern_matches.append(path)
            except OSError:
                continue
        if len(pattern_matches) > 1:
            paths = "\n".join(f"  {path}" for path in pattern_matches)
            raise OawError(
                f"--grep {pattern!r} matched multiple Codex rollouts; "
                f"rerun with --codex-thread or an exact rollout filename:\n{paths}"
            )
        for path in pattern_matches:
            matches.setdefault(path.name, path)
    return sorted(matches.values())


def discover_codex_rollouts(
    codex_roots: Sequence[Path],
    scan_paths: list[Path],
    seed_rollouts: list[Path],
    explicit_threads: list[str],
    explicit_rollouts: list[str],
    grep_patterns: list[str],
) -> tuple[list[Path], str]:
    """Expand referenced Codex rollouts until no new lineage is discovered."""
    discovered = {path.name: path for path in seed_rollouts}
    while True:
        text = transcript_text([*scan_paths, *sorted(discovered.values())])
        matches = find_codex_rollouts(
            codex_roots,
            text,
            explicit_threads,
            explicit_rollouts,
            grep_patterns,
        )
        new_matches = [path for path in matches if path.name not in discovered]
        if not new_matches:
            return sorted(discovered.values()), text
        discovered.update((path.name, path) for path in new_matches)


def referenced_plugin_jobs(text: str) -> set[str]:
    return set(re.findall(r"\btask-[a-z0-9]+-[a-z0-9]+\b", text))


def find_plugin_job_files(plugin_root: Path, transcript: str) -> list[Path]:
    if not plugin_root.exists():
        return []
    jobs = referenced_plugin_jobs(transcript)
    if not jobs:
        return []
    matches: set[Path] = set()
    for path in iter_files(plugin_root, (".json", ".log")):
        if path.stem in jobs:
            matches.add(path)
    return sorted(matches)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_snapshot_files(dest_root: Path, copies: list[SnapshotCopy]) -> list[dict[str, object]]:
    copied_at = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()
    entries: list[dict[str, object]] = []
    for item in copies:
        destination = dest_root / item.destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item.source, destination)
        entries.append(
            {
                "category": item.category,
                "source": str(item.source),
                "destination": item.destination.as_posix(),
                "copied_at": copied_at,
                "completeness": item.completeness,
                "size_bytes": destination.stat().st_size,
                "sha256": sha256_file(destination),
            }
        )
    return entries


def remove_stale_snapshot_files(dest_root: Path, current_entries: list[dict[str, object]]) -> None:
    manifest_path = dest_root / "manifest.json"
    if not manifest_path.exists():
        return
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    current = {
        entry["destination"]
        for entry in current_entries
        if isinstance(entry.get("destination"), str)
    }
    for entry in previous.get("files", []):
        destination = entry.get("destination") if isinstance(entry, dict) else None
        if not isinstance(destination, str) or destination in current:
            continue
        stale_path = (dest_root / destination).resolve()
        try:
            stale_path.relative_to(dest_root.resolve())
        except ValueError:
            continue
        if stale_path.is_file():
            stale_path.unlink()


def write_snapshot_manifest(
    dest_root: Path,
    session_id: str,
    parent: Path | None,
    date: str,
    slug: str,
    parent_completeness: str,
    files: list[dict[str, object]],
    mode: str = "claude-parent",
) -> Path:
    manifest = {
        "schema": "oaw-session-snapshot-v1",
        "session_id": session_id,
        "generated_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
        "snapshot": {
            "date": date,
            "slug": slug,
            "path": str(dest_root),
            "mode": mode,
            "parent_transcript": str(parent) if parent else None,
            "parent_completeness": parent_completeness,
        },
        "files": files,
    }
    path = dest_root / "manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def session_snapshot(
    root: Path,
    session_id: str,
    slug_value: str | None,
    date_override: str | None,
    partial: bool,
    complete: bool,
    codex_only: bool,
    codex_threads: list[str] | None,
    codex_rollout_values: list[str] | None,
    claude_sessions: list[str] | None,
    grep_patterns: list[str] | None,
    output_root: Path | None,
    claude_root: Path,
    codex_roots: Sequence[Path],
    plugin_data_root: Path,
) -> None:
    session_id = session_id.strip()
    if not session_id:
        raise OawError("empty session ID")
    codex_roots = tuple(root.expanduser() for root in codex_roots)
    parent = None if codex_only else find_claude_parent(session_id, claude_root.expanduser())
    explicit_threads = codex_threads or []
    primary_codex_rollouts: list[Path] = []
    if codex_only:
        if not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            session_id,
            re.IGNORECASE,
        ):
            raise OawError("--codex-only requires a full Codex thread UUID")
        session_id = session_id.lower()
        primary_codex_rollouts = find_codex_rollouts(codex_roots, "", [session_id], [], [])
        if not primary_codex_rollouts:
            raise OawError(f"Codex rollout not found for thread {session_id}")
        explicit_threads = [session_id, *explicit_threads]
    explicit_rollouts = codex_rollout_values or []
    codex_rollouts: list[Path] = find_codex_rollouts(
        codex_roots,
        "",
        explicit_threads,
        explicit_rollouts,
        grep_patterns or [],
    )
    date = snapshot_date(parent or primary_codex_rollouts[0], date_override)
    slug = slugify(slug_value or session_id[:8])
    output_root = output_root.expanduser() if output_root else root / RETRO_ATTACHMENTS
    destination_root = output_root / f"{date}-{slug}"
    destination_root.mkdir(parents=True, exist_ok=True)

    subagents_dir: Path | None = None
    subagents: list[Path] = []
    task_outputs: list[SnapshotCopy] = []
    workflow_artifacts: list[SnapshotCopy] = []
    workflow_scripts: list[SnapshotCopy] = []
    if parent:
        session_dir = parent.with_suffix("")
        subagents_dir = session_dir / "subagents"
        workflow_root = subagents_dir / "workflows"
        subagents = [
            path
            for path in iter_files(subagents_dir, (".jsonl",))
            if not path.is_relative_to(workflow_root)
        ]
        task_outputs = snapshot_tree_copies(
            session_dir / "tasks",
            Path("claude") / "tasks",
            "claude-task-output",
        )
        workflow_artifacts = snapshot_tree_copies(
            subagents_dir / "workflows",
            Path("claude") / "workflows",
            "claude-workflow-artifact",
        )
        workflow_scripts = snapshot_tree_copies(
            session_dir / "workflows" / "scripts",
            Path("claude") / "workflow-scripts",
            "claude-workflow-script",
        )
    scan_paths = [
        *([parent] if parent else []),
        *subagents,
        *(copy.source for copy in task_outputs),
        *(copy.source for copy in workflow_artifacts),
        *(copy.source for copy in workflow_scripts),
    ]
    codex_rollouts, text = discover_codex_rollouts(
        codex_roots,
        scan_paths,
        codex_rollouts,
        explicit_threads,
        explicit_rollouts,
        grep_patterns or [],
    )
    parent_completeness = detect_parent_completeness(session_id, partial, complete)
    extra_claude_parents = find_extra_claude_parents(
        claude_root.expanduser(),
        text,
        claude_sessions or [],
        session_id,
    )
    if extra_claude_parents:
        scan_paths.extend(extra_claude_parents)
        codex_rollouts, text = discover_codex_rollouts(
            codex_roots,
            scan_paths,
            codex_rollouts,
            explicit_threads,
            explicit_rollouts,
            grep_patterns or [],
        )

    copies: list[SnapshotCopy] = []
    if parent:
        parent_name = f"parent-{session_id[:8]}"
        if parent_completeness == "partial":
            parent_name += "-PARTIAL"
        copies.append(
            SnapshotCopy(
                parent,
                Path("claude") / f"{parent_name}.jsonl",
                "claude-parent",
                parent_completeness,
            )
        )
    for path in subagents:
        assert subagents_dir is not None
        relative = path.relative_to(subagents_dir)
        destination = (
            Path("claude") / path.name
            if len(relative.parts) == 1
            else Path("claude") / "subagents" / relative
        )
        copies.append(SnapshotCopy(path, destination, "claude-subagent"))
    copies.extend(task_outputs)
    copies.extend(workflow_artifacts)
    copies.extend(workflow_scripts)
    copies.extend(
        SnapshotCopy(
            path,
            Path("claude") / "forks" / f"parent-{path.stem[:8]}.jsonl",
            "claude-fork-parent",
        )
        for path in extra_claude_parents
    )
    copies.extend(
        SnapshotCopy(
            path,
            Path("codex") / path.name,
            "codex-rollout",
            parent_completeness if codex_only else "complete",
        )
        for path in codex_rollouts
    )
    copies.extend(
        SnapshotCopy(path, Path("plugin-logs") / path.name, "plugin-job")
        for path in find_plugin_job_files(plugin_data_root.expanduser(), text)
    )

    seen_destinations: set[Path] = set()
    unique_copies: list[SnapshotCopy] = []
    for item in copies:
        if item.destination in seen_destinations:
            continue
        seen_destinations.add(item.destination)
        unique_copies.append(item)

    entries = copy_snapshot_files(destination_root, unique_copies)
    remove_stale_snapshot_files(destination_root, entries)
    manifest_path = write_snapshot_manifest(
        destination_root,
        session_id,
        parent,
        date,
        slug,
        parent_completeness,
        entries,
        "codex-only" if codex_only else "claude-parent",
    )

    print(f"Snapshot: {destination_root}")
    print(f"Manifest: {manifest_path}")
    print(f"Copied: {len(entries)}")
    print(f"Transcript: {parent_completeness}")
