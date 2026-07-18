#!/usr/bin/env python3
"""Preflight and ingest OAW deep-research packets."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

DEFAULT_PROVIDERS = ("ChatGPT", "Gemini", "Claude")
PUA_RE = re.compile(r"[\ue000-\uf8ff]")
PUA_SPAN_RE = re.compile(r"[\ue000-\uf8ff][^\ue000-\uf8ff]{0,400}?[\ue000-\uf8ff]")
CITETURN_RE = re.compile(r"\b(?:file)?citeturn[0-9A-Za-z]*(?:view|search|file)?[0-9A-Za-z]*\b")
HANDOFF_FORBIDDEN_PATTERNS = (
    ("Obsidian wikilink", re.compile(r"\[\[")),
    ("obs: reference", re.compile(r"\bobs:", re.IGNORECASE)),
    ("vault path", re.compile(r"(?<![/A-Za-z0-9])(?:Projects|Agents)/")),
    (
        "internal durable ID",
        re.compile(r"\b(?:AGT|OAW|FAB|CDX|SR|PMX)-[A-Za-z0-9][A-Za-z0-9._-]*"),
    ),
    (
        "local Consumers heading",
        re.compile(
            r"^\s*(?:(?:>\s*|[-*+]\s+|#{1,6}\s+))*"
            r"(?:\*\*|__)?Consumers:(?:\*\*|__)?\s*$",
            re.IGNORECASE,
        ),
    ),
)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    handoff = sub.add_parser("handoff", help="Print copy-only prompt handoff")
    handoff.add_argument("research_dir", type=Path)
    handoff.add_argument("provider")
    handoff.set_defaults(func=cmd_handoff)

    intake = sub.add_parser("intake", help="Ingest a finished provider report")
    intake.add_argument("research_dir", type=Path)
    intake.add_argument("provider")
    source = intake.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="Report file to ingest")
    source.add_argument("--clipboard", action="store_true", help="Read report from X11 clipboard")
    source.add_argument(
        "--latest-download",
        action="store_true",
        help="Use newest ~/Downloads/deep-research-report*.md",
    )
    intake.add_argument("--url", help="Provider share URL to record")
    intake.add_argument("--force", action="store_true", help="Overwrite existing Raw file")
    intake.set_defaults(func=cmd_intake)
    return p


def cmd_handoff(args: argparse.Namespace) -> None:
    prompt = provider_prompt_text(args.research_dir)
    findings = unsafe_handoff_findings(prompt)
    if findings:
        details = "\n".join(
            f"- line {line}: {label}: {excerpt}" for line, label, excerpt in findings
        )
        raise SystemExit(
            f"Refusing unsafe provider handoff from {args.research_dir / 'Prompt.md'}:\n"
            f"{details}\n"
            "Move local metadata before '## Deep research prompt' or replace it with "
            "self-contained provider-facing text."
        )
    print(f"Copy to {canonical_provider(args.provider)}:\n")
    print(prompt.rstrip())


def cmd_intake(args: argparse.Namespace) -> None:
    research_dir = args.research_dir.resolve()
    target = result_path(research_dir, args.provider)
    if not target.is_file():
        raise SystemExit(
            f"Missing running result note: {target}. "
            "Register the provider first with 'oaw research start'."
        )
    provider = target.stem.removeprefix("Results - ")
    report_text, source_path = read_intake_source(args)
    report_text = report_text.strip()
    if not report_text:
        raise SystemExit("Refusing to ingest empty report.")

    raw_path = research_dir / f"Raw - {provider}.md"
    if raw_path.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite existing raw file without --force: {raw_path}")
    raw_path.write_text(report_text + "\n", encoding="utf-8")

    cleaned, pua_count, span_count = clean_report(report_text)
    source_list_present = has_full_source_list(cleaned)
    quality = capture_quality(provider, pua_count, span_count, source_list_present, cleaned)
    updates: dict[str, str | int | bool] = {
        "status": "done",
        "capture_quality": quality,
        "pua_chars_stripped": pua_count,
        "source_list_present": source_list_present,
    }
    if args.url:
        updates["url"] = args.url

    existing = target.read_text(encoding="utf-8")
    fm, _ = split_frontmatter(existing)
    target.write_text(
        join_frontmatter(update_frontmatter_text(fm, updates), cleaned + "\n"), encoding="utf-8"
    )

    src = f" from {source_path}" if source_path else ""
    print(f"ingested {provider}{src}")
    print(f"raw: {raw_path}")
    print(f"result: {target}")
    print(f"capture_quality: {quality}")


def read_intake_source(args: argparse.Namespace) -> tuple[str, Path | None]:
    if args.file:
        return args.file.read_text(encoding="utf-8"), args.file
    if args.latest_download:
        path = latest_download()
        return path.read_text(encoding="utf-8"), path
    proc = subprocess.run(
        ["xclip", "-selection", "clipboard", "-o"],
        capture_output=True,
        check=False,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"xclip clipboard read failed: {proc.stderr.strip()}")
    return proc.stdout, None


def latest_download() -> Path:
    downloads = Path.home() / "Downloads"
    matches = sorted(
        downloads.glob("deep-research-report*.md"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    if not matches:
        raise SystemExit(f"No deep-research-report*.md files found in {downloads}")
    return matches[0]


def clean_report(text: str) -> tuple[str, int, int]:
    pua_count = len(PUA_RE.findall(text))
    text, span_count = PUA_SPAN_RE.subn("", text)
    text = PUA_RE.sub("", text)
    text = CITETURN_RE.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip(), pua_count, span_count


def has_full_source_list(text: str) -> bool:
    return bool(re.search(r"(?im)^## Full source list\s*$", text))


def capture_quality(
    provider: str, pua_count: int, span_count: int, source_list_present: bool, text: str
) -> str:
    if pua_count or span_count:
        return "degraded-citations-stripped"
    if provider.lower() == "gemini" and "Full source list" in text and not source_list_present:
        return "degraded-copy-flattened"
    if not source_list_present:
        return "degraded-source-list-missing"
    return "clean"


def result_path(research_dir: Path, provider: str) -> Path:
    exact_provider = provider.strip()
    exact = research_dir / f"Results - {exact_provider}.md"
    if exact.exists():
        return exact
    return research_dir / f"Results - {canonical_provider(provider)}.md"


def canonical_provider(provider: str) -> str:
    normalized = provider.strip()
    known = {name.lower(): name for name in DEFAULT_PROVIDERS}
    return known.get(normalized.lower(), normalized[:1].upper() + normalized[1:])


def strip_handoff_prompt(body: str) -> str:
    body = body.strip()
    body = re.sub(r"(?s)^# Prompt[^\n]*\n+", "", body).strip()
    deep_prompt = re.search(r"(?m)^## Deep research prompt\s*$", body)
    if deep_prompt:
        remainder = body[deep_prompt.end() :]
        block = re.fullmatch(r"\s*```text[ \t]*\n(.*?)\n```[ \t]*\s*", remainder, re.DOTALL)
        if block is None or not block.group(1).strip():
            raise SystemExit(
                "Deep research prompt must contain exactly one non-empty fenced text block."
            )
        return block.group(1).strip() + "\n"
    return body + "\n"


def provider_prompt_text(research_dir: Path) -> str:
    prompt_text = (research_dir / "Prompt.md").read_text(encoding="utf-8")
    _, body = split_frontmatter(prompt_text)
    return strip_handoff_prompt(body)


def unsafe_handoff_findings(prompt: str) -> list[tuple[int, str, str]]:
    findings: list[tuple[int, str, str]] = []
    for line_number, line in enumerate(prompt.splitlines(), start=1):
        for label, pattern in HANDOFF_FORBIDDEN_PATTERNS:
            if pattern.search(line):
                excerpt = line.strip()
                if len(excerpt) > 160:
                    excerpt = excerpt[:157] + "..."
                findings.append((line_number, label, excerpt))
    return findings


def split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end == -1:
        return "", text
    return text[4:end], text[end + 5 :]


def join_frontmatter(frontmatter: str, body: str) -> str:
    return f"---\n{frontmatter.rstrip()}\n---\n\n{body.lstrip().rstrip()}\n"


def render_frontmatter(data: dict[str, str | list[str]]) -> str:
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {item}" for item in value)
        elif isinstance(value, bool):
            lines.append(f"{key}: {str(value).lower()}")
        elif value == "":
            lines.append(f"{key}:")
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines) + "\n"


def update_frontmatter_text(frontmatter: str, updates: dict[str, str | int | bool]) -> str:
    remaining = dict(updates)
    lines: list[str] = []
    skip_block = False
    for line in frontmatter.splitlines():
        if skip_block and (line.startswith(" ") or line.strip() == ""):
            continue
        skip_block = False
        match = re.match(r"^([A-Za-z0-9_-]+):(.*)$", line)
        if match and match.group(1) in remaining:
            key = match.group(1)
            lines.append(render_frontmatter({key: remaining.pop(key)}).rstrip("\n"))
            if match.group(2).strip() == "":
                skip_block = True
        else:
            lines.append(line)
    for key, value in remaining.items():
        lines.append(render_frontmatter({key: value}).rstrip("\n"))
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
