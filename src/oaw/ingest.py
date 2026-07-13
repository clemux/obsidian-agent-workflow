"""Safe-export ingestion command implementation."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .errors import OawError
from .frontmatter import read_frontmatter_only

SAFE_EXPORT_DESTINATION = Path("Imports/Safe export")
SAFE_EXPORT_QUARANTINE = Path(".rejected")
SAFE_EXPORT_TAG = "safe-export-personal"
SAFE_EXPORT_SCOPE = "personal"


@dataclass(frozen=True)
class ExportCandidate:
    source: Path
    relative_source: Path
    safe: bool
    marker: str
    reason: str
    destination: Path | None = None


def default_ingestion_root() -> Path:
    return Path(os.environ.get("OAW_INGESTION_ROOT", "~/obsidian-ingestion")).expanduser()


def safe_export_destination(destination: str) -> Path:
    raw = Path(destination)
    if raw.is_absolute():
        raise OawError("--destination must be vault-relative")
    if ".." in raw.parts:
        raise OawError("--destination must not contain '..'")
    return raw


def frontmatter_tags(data: dict[str, object]) -> set[str]:
    value = data.get("tags", [])
    tags: set[str] = set()
    if isinstance(value, list):
        tags.update(str(item).lstrip("#") for item in value)
    elif isinstance(value, str):
        tags.update(part.strip().lstrip("#") for part in re.split(r"[,\s]+", value) if part.strip())
    return tags


def trueish(value: object) -> bool:
    return str(value).strip().lower() in {"true", "yes", "1", "personal"}


def safe_export_marker(data: dict[str, object]) -> tuple[bool, str, str]:
    scope = data.get("export-scope")
    if isinstance(scope, str) and scope.strip().lower() == SAFE_EXPORT_SCOPE:
        return True, "export-scope: personal", "accepted"
    approved = data.get("export-approved")
    if isinstance(approved, str) and approved.strip().lower() == SAFE_EXPORT_SCOPE:
        return True, "export-approved: personal", "accepted"
    legacy_property = data.get(SAFE_EXPORT_TAG)
    if legacy_property is not None and trueish(legacy_property):
        return True, f"{SAFE_EXPORT_TAG}: true", "accepted compatibility property"
    if SAFE_EXPORT_TAG in frontmatter_tags(data):
        return True, f"tag: {SAFE_EXPORT_TAG}", "accepted compatibility tag"
    return False, "", "missing safe export marker"


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise OawError(f"could not find unique destination for {path}")


def iter_ingestion_markdown(root: Path) -> list[Path]:
    if not root.exists():
        return []
    quarantine = root / SAFE_EXPORT_QUARANTINE
    paths: list[Path] = []
    for path in sorted(root.rglob("*.md")):
        try:
            path.relative_to(quarantine)
            continue
        except ValueError:
            pass
        if path.is_file():
            paths.append(path)
    return paths


def classify_export_candidate(
    path: Path, ingestion_root: Path, destination_root: Path
) -> ExportCandidate:
    relative = path.relative_to(ingestion_root)
    try:
        _, data = read_frontmatter_only(path)
    except UnicodeDecodeError as exc:
        return ExportCandidate(path, relative, False, "", f"frontmatter is not UTF-8: {exc}")
    except OawError as exc:
        return ExportCandidate(path, relative, False, "", str(exc))
    safe, marker, reason = safe_export_marker(data)
    destination = destination_root / relative if safe else None
    return ExportCandidate(path, relative, safe, marker, reason, destination)


def classify_export_candidates(
    ingestion_root: Path,
    destination_root: Path,
) -> list[ExportCandidate]:
    return [
        classify_export_candidate(path, ingestion_root, destination_root)
        for path in iter_ingestion_markdown(ingestion_root)
    ]


def move_to_quarantine(candidate: ExportCandidate, ingestion_root: Path) -> Path:
    quarantine = ingestion_root / SAFE_EXPORT_QUARANTINE / candidate.relative_source
    quarantine.parent.mkdir(parents=True, exist_ok=True)
    destination = unique_destination(quarantine)
    shutil.move(str(candidate.source), str(destination))
    return destination


def ingest_candidate(candidate: ExportCandidate) -> Path:
    if candidate.destination is None:
        raise OawError("safe candidate is missing a destination")
    destination = unique_destination(candidate.destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidate.source, destination)
    candidate.source.unlink()
    return destination


def safe_export_ingest(root: Path, ingestion_root: Path, destination_value: str, mode: str) -> None:
    ingestion_root = ingestion_root.expanduser().resolve()
    destination_root = (root / safe_export_destination(destination_value)).resolve()
    if not destination_root.is_relative_to(root):
        raise OawError("safe export destination must remain inside the vault")
    if root.is_relative_to(ingestion_root):
        raise OawError("ingestion root must not be or contain the vault")
    if destination_root.is_relative_to(ingestion_root):
        raise OawError("safe export destination must not be inside the ingestion root")
    candidates = classify_export_candidates(ingestion_root, destination_root)
    accepted = [candidate for candidate in candidates if candidate.safe]
    rejected = [candidate for candidate in candidates if not candidate.safe]
    print(f"Mode: {mode}")
    print(f"Ingestion: {ingestion_root}")
    print(f"Destination: {destination_root.relative_to(root).as_posix()}")
    print(f"Candidates: {len(candidates)}")
    for candidate in candidates:
        rel = candidate.relative_source.as_posix()
        if candidate.safe:
            marker = candidate.marker
            candidate_destination = candidate.destination
            if candidate_destination is None:
                raise OawError(f"safe candidate has no destination: {rel}")
            if mode == "write":
                written = ingest_candidate(candidate)
                print(
                    f"ACCEPT {rel} [{marker}] -> {written.relative_to(root).as_posix()}; removed source"
                )
            else:
                target = unique_destination(candidate_destination).relative_to(root).as_posix()
                print(f"ACCEPT {rel} [{marker}] -> {target}; dry-run")
        else:
            if mode == "write":
                quarantined = move_to_quarantine(candidate, ingestion_root)
                print(
                    f"REJECT {rel} [{candidate.reason}] -> quarantine "
                    f"{quarantined.relative_to(ingestion_root).as_posix()}"
                )
            else:
                print(f"REJECT {rel} [{candidate.reason}] -> quarantine; dry-run")
    print(f"Accepted: {len(accepted)}")
    print(f"Rejected: {len(rejected)}")
