"""Best-effort metadata extraction from session artifacts."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SessionMetrics:
    started: dt.datetime | None = None
    ended: dt.datetime | None = None
    user_turns: int | None = None
    assistant_turns: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    total_tokens: int | None = None

    @property
    def duration(self) -> dt.timedelta | None:
        if self.started is None or self.ended is None:
            return None
        return self.ended - self.started


def _timestamp(value: object) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _message_role(record: object) -> str | None:
    if not isinstance(record, dict):
        return None
    candidate = record
    payload = record.get("payload")
    if isinstance(payload, dict):
        candidate = payload
    if candidate.get("type") != "message":
        return None
    role = candidate.get("role")
    return role if role in ("user", "assistant") else None


def _nonnegative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _total_usage(record: object) -> dict[str, object] | None:
    """Return a Codex cumulative usage snapshot, when this record contains one."""
    if not isinstance(record, dict):
        return None
    candidates: list[object] = [record]
    payload = record.get("payload")
    if isinstance(payload, dict):
        candidates.append(payload)
        info = payload.get("info")
        if isinstance(info, dict):
            candidates.append(info)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        usage = candidate.get("total_token_usage")
        if isinstance(usage, dict):
            return usage
    return None


def codex_rollout_metrics(path: Path) -> SessionMetrics:
    timestamps: list[dt.datetime] = []
    user_turns = 0
    assistant_turns = 0
    saw_message = False
    usage: dict[str, object] | None = None
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict) and (stamp := _timestamp(record.get("timestamp"))):
                    timestamps.append(stamp)
                role = _message_role(record)
                if role == "user":
                    user_turns += 1
                    saw_message = True
                elif role == "assistant":
                    assistant_turns += 1
                    saw_message = True
                if snapshot := _total_usage(record):
                    usage = snapshot
    except OSError:
        return SessionMetrics()

    return SessionMetrics(
        started=min(timestamps) if timestamps else None,
        ended=max(timestamps) if timestamps else None,
        user_turns=user_turns if saw_message else None,
        assistant_turns=assistant_turns if saw_message else None,
        input_tokens=_nonnegative_int(usage.get("input_tokens")) if usage else None,
        output_tokens=_nonnegative_int(usage.get("output_tokens")) if usage else None,
        cached_input_tokens=(_nonnegative_int(usage.get("cached_input_tokens")) if usage else None),
        total_tokens=_nonnegative_int(usage.get("total_tokens")) if usage else None,
    )


def format_timestamp(value: dt.datetime | None) -> str:
    if value is None:
        return "unavailable"
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def format_duration(value: dt.timedelta | None) -> str:
    if value is None:
        return "unavailable"
    seconds = int(value.total_seconds())
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_tokens(metrics: SessionMetrics) -> str:
    def value_or_unavailable(value: int | None) -> str:
        return str(value) if value is not None else "unavailable"

    return ", ".join(
        (
            f"input={value_or_unavailable(metrics.input_tokens)}",
            f"output={value_or_unavailable(metrics.output_tokens)}",
            f"cached={value_or_unavailable(metrics.cached_input_tokens)}",
            f"total={value_or_unavailable(metrics.total_tokens)}",
        )
    )
