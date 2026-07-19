"""Safe outbound export and bundle validation."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

from .errors import OawError
from .frontmatter import parse_frontmatter
from .notes import read_note, split_note
from .resolver import NoteMatch, resolve_id

DEFAULT_EXPORT_ROOT = Path("~/obsidian-export")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def frontmatter_bool(data: dict[str, object], key: str) -> bool:
    value = data.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def frontmatter_strings(data: dict[str, object], key: str) -> list[str]:
    value = data.get(key, [])
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def require_safe_export(match: NoteMatch, target: str) -> None:
    scope = match.frontmatter.get("export-scope")
    if isinstance(scope, str) and scope.strip().lower() == target.lower():
        return
    export_target = match.frontmatter.get("export_target")
    if (
        frontmatter_bool(match.frontmatter, "safe_for_export")
        and isinstance(export_target, str)
        and export_target == target
    ):
        return
    raise OawError(
        f"export requires export-scope: {target} in note frontmatter "
        f"(legacy safe_for_export: true plus export_target: {target} is also accepted)"
    )


def resolve_export_artifact(root: Path, note_path: Path, raw_value: str) -> Path:
    raw_path = Path(raw_value)
    if raw_path.is_absolute():
        raise OawError(f"export artifact must be vault-relative or note-relative: {raw_value}")
    note_relative = (note_path.parent / raw_path).resolve()
    vault_relative = (root / raw_path).resolve()
    candidates = [note_relative, vault_relative]
    for candidate in candidates:
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise OawError(f"export artifact escapes vault: {raw_value}") from exc
        if candidate.is_file():
            return candidate
    raise OawError(f"export artifact not found: {raw_value}")


def export_note_text(match: NoteMatch, target: str) -> str:
    text, _, body = read_note(match.path)
    frontmatter_block, _, _ = split_note(text)
    relpath = match.relpath
    banner = (
        "> [!IMPORTANT]\n"
        f"> This note was intentionally exported for `{target}` with "
        f"`export-scope: {target}`. Return edits or results through the export bundle, "
        "not by pasting private vault paths.\n"
        f"> Source: `{relpath}`" + (f" (`{match.note_id}`)" if match.note_id else "") + "\n\n"
    )
    if frontmatter_block:
        return f"{frontmatter_block}\n{banner}{body.lstrip()}"
    return f"{banner}{text.lstrip()}"


def export_bundle_name(match: NoteMatch) -> str:
    raw = match.note_id or match.title
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip(".-")
    if not name:
        raise OawError("could not derive a safe export bundle name")
    return name


def write_export_bundle(
    root: Path, note_id: str, target: str, output_root: Path | None, force: bool
) -> None:
    target = target.strip()
    if not target:
        raise OawError("export target must not be empty")
    match = resolve_id(note_id, root)
    require_safe_export(match, target)
    output_root = output_root.expanduser() if output_root else DEFAULT_EXPORT_ROOT.expanduser()
    output_root.mkdir(parents=True, exist_ok=True)
    bundle_name = export_bundle_name(match)
    bundle = output_root / bundle_name
    if bundle.exists():
        if not force:
            raise OawError(f"export bundle already exists: {bundle}")
        if not bundle.is_dir():
            raise OawError(f"export bundle path is not a directory: {bundle}")
    staging = Path(tempfile.mkdtemp(prefix=f".{bundle_name}.tmp-", dir=output_root))
    try:
        note_path = staging / "note.md"
        note_path.write_text(export_note_text(match, target), encoding="utf-8")

        artifact_entries = []
        for raw_artifact in frontmatter_strings(match.frontmatter, "export_artifacts"):
            source = resolve_export_artifact(root, match.path, raw_artifact)
            rel_source = source.relative_to(root).as_posix()
            destination = staging / "artifacts" / rel_source
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            artifact_entries.append(
                {
                    "source_path": rel_source,
                    "path": destination.relative_to(staging).as_posix(),
                    "sha256": sha256_file(destination),
                    "size_bytes": destination.stat().st_size,
                }
            )

        manifest = {
            "schema": "oaw-safe-export-v1",
            "target": target,
            "exported_at": dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat(),
            "source": {
                "id": match.note_id,
                "path": match.relpath,
                "title": match.title,
            },
            "note": {
                "path": note_path.relative_to(staging).as_posix(),
                "sha256": sha256_file(note_path),
                "size_bytes": note_path.stat().st_size,
            },
            "return_ingest": frontmatter_bool(match.frontmatter, "return_ingest"),
            "artifacts": artifact_entries,
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if bundle.exists():
            shutil.rmtree(bundle)
        staging.rename(bundle)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    print(f"Export: {bundle}")
    print(f"Manifest: {bundle / 'manifest.json'}")
    print(f"Artifacts: {len(artifact_entries)}")


def manifest_bundle_path(bundle: Path, raw_path: str) -> Path:
    relative = Path(raw_path)
    if relative.is_absolute():
        raise OawError(f"manifest path must be bundle-relative: {raw_path}")
    candidate = (bundle / relative).resolve()
    if not candidate.is_relative_to(bundle):
        raise OawError(f"manifest path escapes bundle: {raw_path}")
    return candidate


def validate_export_bundle(bundle: Path, target: str | None) -> None:
    bundle = bundle.expanduser().resolve()
    manifest_path = bundle / "manifest.json"
    if not manifest_path.exists():
        raise OawError(f"manifest not found: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise OawError(f"manifest is not valid JSON: {manifest_path}") from exc
    if manifest.get("schema") != "oaw-safe-export-v1":
        raise OawError("manifest schema is not oaw-safe-export-v1")
    target = target or manifest.get("target")
    if not isinstance(target, str) or not target:
        raise OawError("manifest target is missing")
    if manifest.get("target") != target:
        raise OawError(f"manifest target does not match {target}")
    source = manifest.get("source")
    source_path = Path(str(source.get("path", ""))) if isinstance(source, dict) else Path()
    if not isinstance(source, dict) or source_path.is_absolute() or ".." in source_path.parts:
        raise OawError("manifest source path must be vault-relative")

    note = manifest.get("note")
    if not isinstance(note, dict) or not isinstance(note.get("path"), str):
        raise OawError("manifest note entry is missing")
    note_path = manifest_bundle_path(bundle, note["path"])
    if not note_path.is_file():
        raise OawError(f"exported note not found: {note_path}")
    if note.get("sha256") != sha256_file(note_path):
        raise OawError("exported note checksum mismatch")
    _, frontmatter, _ = read_note(note_path)
    data = parse_frontmatter(frontmatter)
    temp_match = NoteMatch(
        path=note_path,
        relpath=note["path"],
        note_id=str(source.get("id")) if source.get("id") else None,
        matched_by="export",
        title=str(source.get("title", note_path.stem)),
        frontmatter_text="",
        frontmatter=data,
    )
    require_safe_export(temp_match, target)

    artifacts = manifest.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise OawError("manifest artifacts must be a list")
    for artifact in artifacts:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
            raise OawError("manifest artifact entry is invalid")
        artifact_path = manifest_bundle_path(bundle, artifact["path"])
        if not artifact_path.is_file():
            raise OawError(f"artifact not found: {artifact_path}")
        if artifact.get("sha256") != sha256_file(artifact_path):
            raise OawError(f"artifact checksum mismatch: {artifact['path']}")

    print("Export: valid")
    print(f"Target: {target}")
    print(f"Artifacts: {len(artifacts)}")
