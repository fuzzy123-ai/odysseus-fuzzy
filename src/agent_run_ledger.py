"""Append-only ledger for detached agent/chat runs.

The in-memory ``agent_runs`` buffer is the live replay path. This module is a
small durable audit trail: enough to inspect what happened and build restart
recovery on top, without copying full chat content or tool output.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.constants import AGENT_RUN_LEDGER_DIR
from src.shell_policy import classify_shell_command

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_MAX_PREVIEW_CHARS = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_session_id(session_id: str) -> str:
    safe = _SAFE_ID_RE.sub("_", str(session_id or "").strip()).strip("._")
    return safe[:120] or "session"


def ledger_path(session_id: str) -> Path:
    return Path(AGENT_RUN_LEDGER_DIR) / f"{_safe_session_id(session_id)}.jsonl"


def append_event(session_id: str, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Append a single JSONL ledger record and return it."""

    record = {
        "ts": _now_iso(),
        "session_id": str(session_id),
        "event": str(event_type),
        "payload": payload or {},
    }
    path = ledger_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def append_status(session_id: str, status: str) -> dict[str, Any]:
    return append_event(session_id, "run_status", {"status": status})


def append_run_started(session_id: str) -> dict[str, Any]:
    return append_event(session_id, "run_started", {})


def summarize_sse_event(sse: str) -> dict[str, Any] | None:
    """Return a compact, non-verbatim summary for one SSE event string."""

    if not isinstance(sse, str) or not sse:
        return None

    event_name = None
    data_lines: list[str] = []
    for raw_line in sse.splitlines():
        if raw_line.startswith("event: "):
            event_name = raw_line[len("event: "):].strip() or None
        elif raw_line.startswith("data: "):
            data_lines.append(raw_line[len("data: "):])

    if not data_lines:
        return {"event_name": event_name or "message", "raw_chars": len(sse)}

    data = "\n".join(data_lines)
    if data == "[DONE]":
        return {"event_name": event_name or "message", "type": "done"}

    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return {
            "event_name": event_name or "message",
            "type": "text",
            "data_chars": len(data),
        }

    if not isinstance(payload, dict):
        return {
            "event_name": event_name or "message",
            "type": type(payload).__name__,
            "data_chars": len(data),
        }

    kind = payload.get("type")
    summary: dict[str, Any] = {
        "event_name": event_name or "message",
        "type": kind or ("delta" if "delta" in payload else "json"),
    }

    if "delta" in payload:
        summary["delta_chars"] = len(str(payload.get("delta") or ""))

    if kind in {"tool_start", "tool_progress", "tool_output"}:
        summary["tool"] = payload.get("tool")
        summary["round"] = payload.get("round")
        if kind == "tool_start" and payload.get("command"):
            command = str(payload.get("command"))
            summary["command_preview"] = command[:_MAX_PREVIEW_CHARS]
            policy = classify_shell_command(command).to_dict()
            summary["command_policy"] = {
                key: policy[key]
                for key in ("tier", "reason", "requires_confirmation", "blocked", "audit")
            }
        if kind == "tool_output":
            summary["exit_code"] = payload.get("exit_code")
            summary["blocked"] = bool(payload.get("blocked", False))
            if payload.get("screenshot"):
                summary["has_screenshot"] = True
        output = payload.get("output")
        if output is not None:
            summary["output_chars"] = len(str(output))
            readiness_signals = _readiness_signals_from_output(output, tool=payload.get("tool"))
            if readiness_signals:
                summary["readiness_signal"] = readiness_signals[0]
                summary["readiness_signals"] = readiness_signals

    if kind in {"agent_step", "rounds_exhausted", "budget_exceeded"}:
        for key in ("round", "rounds", "limit", "used"):
            if key in payload:
                summary[key] = payload.get(key)

    if kind == "metrics" and isinstance(payload.get("data"), dict):
        metrics = payload["data"]
        for key in ("input_tokens", "output_tokens", "total_tokens", "tokens_per_second", "usage_source"):
            if key in metrics:
                summary[key] = metrics.get(key)

    if kind == "error":
        summary["status"] = payload.get("status")
        if payload.get("error"):
            summary["error_preview"] = str(payload.get("error"))[:_MAX_PREVIEW_CHARS]

    return summary


def _readiness_signals_from_output(output: Any, *, tool: Any = None) -> list[dict[str, Any]]:
    try:
        payload = json.loads(str(output))
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    family_hint = _readiness_family_from_text(tool)
    signals = _readiness_signals_from_mapping(payload, family_hint=family_hint)
    if signals:
        return signals
    memory = payload.get("memory")
    if isinstance(memory, dict):
        signals = _readiness_signals_from_mapping(memory, family_hint=family_hint)
        if signals:
            return signals
    summary = payload.get("summary")
    if isinstance(summary, dict):
        return _readiness_signals_from_mapping(summary, family_hint=family_hint)
    return []


def _readiness_signals_from_mapping(payload: dict[str, Any], *, family_hint: str = "generic") -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    explicit = payload.get("readiness_signals")
    if isinstance(explicit, list):
        signals.extend(
            _readiness_signal_from_explicit(signal, family_hint=family_hint)
            for signal in explicit
            if isinstance(signal, dict)
        )
        if signals:
            return _dedupe_readiness_signals(signals)
    by_family = payload.get("readiness_by_family")
    if isinstance(by_family, dict):
        signals.extend(
            _readiness_signal_from_explicit({**signal, "family": family}, family_hint=family_hint)
            for family, signal in by_family.items()
            if isinstance(signal, dict)
        )
        if signals:
            return _dedupe_readiness_signals(signals)
    readiness = payload.get("readiness")
    if isinstance(readiness, dict):
        state = str(readiness.get("state") or "unknown")
        gaps = _safe_gap_list(readiness.get("gaps"))
        signals.append({
            "family": _readiness_family_from_payload(payload, family_hint),
            "source": "readiness",
            "state": state,
            "ready": _safe_ready(readiness.get("ready"), default=state == "ready"),
            "gaps": gaps,
            "gap_count": len(gaps),
        })
    summary = payload.get("summary")
    if isinstance(summary, dict):
        signals.extend(_readiness_signals_from_mapping(summary, family_hint=family_hint))
    signals.extend(_summary_readiness_signals(payload, family_hint=family_hint))
    return _dedupe_readiness_signals(signals)


def _readiness_signal_from_explicit(signal: dict[str, Any], *, family_hint: str = "generic") -> dict[str, Any]:
    state = str(signal.get("state") or "unknown")
    gaps = _safe_gap_list(signal.get("gaps"))
    gap_count = int(signal.get("gap_count") or len(gaps))
    family = str(signal.get("family") or family_hint or "generic")
    return {
        "family": family,
        "source": str(signal.get("source") or "readiness"),
        "state": state,
        "ready": _safe_ready(signal.get("ready"), default=state == "ready" and gap_count == 0),
        "gaps": gaps,
        "gap_count": max(gap_count, len(gaps)),
    }


def _summary_readiness_signals(payload: dict[str, Any], *, family_hint: str = "generic") -> list[dict[str, Any]]:
    signals = []
    for family in ("freshness", "raptor"):
        state = payload.get(f"{family}_readiness_state")
        if state:
            gap_count = int(payload.get(f"{family}_readiness_gaps") or 0)
            gaps = _safe_gap_list(payload.get(f"{family}_readiness_gap_names"))
            signals.append({
                "family": family,
                "source": "summary",
                "state": str(state),
                "ready": str(state) == "ready" and gap_count == 0,
                "gaps": gaps,
                "gap_count": max(gap_count, len(gaps)),
            })
    state = payload.get("readiness_state")
    if state:
        gap_count = int(payload.get("readiness_gaps") or 0)
        gaps = _safe_gap_list(payload.get("readiness_gap_names"))
        family = family_hint if family_hint != "generic" else "generic"
        signals.append({
            "family": family,
            "source": "summary",
            "state": str(state),
            "ready": str(state) == "ready" and gap_count == 0,
            "gaps": gaps,
            "gap_count": max(gap_count, len(gaps)),
        })
    return signals


def _readiness_family_from_payload(payload: dict[str, Any], family_hint: str = "generic") -> str:
    kind = str(payload.get("kind") or payload.get("type") or payload.get("family") or "").lower()
    if family_hint != "generic":
        return family_hint
    return _readiness_family_from_text(kind)


def _readiness_family_from_text(value: Any) -> str:
    kind = str(value or "").lower()
    if "somt" in kind or "memory_tree" in kind or "memory-tree" in kind:
        return "somt"
    if "memory_status" in kind or "memory-status" in kind or "memory status" in kind:
        return "memory"
    if "raptor" in kind:
        return "raptor"
    if "freshness" in kind or "quarantine" in kind:
        return "freshness"
    return "generic"


def _dedupe_readiness_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped = []
    seen = set()
    for signal in signals:
        key = (signal.get("family"), signal.get("source"), signal.get("state"), signal.get("gap_count"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(signal)
    return deduped[:10]


def _safe_gap_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        str(item)[:80]
        for item in value[:10]
        if isinstance(item, (str, int, float))
    ]


def _safe_ready(value: Any, *, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "ready"}:
            return True
        if normalized in {"false", "0", "no", "not_ready", "blocked"}:
            return False
    return bool(default)


def append_sse_event(session_id: str, sse: str) -> dict[str, Any] | None:
    summary = summarize_sse_event(sse)
    if summary is None:
        return None
    return append_event(session_id, "sse_event", summary)


def read_events(session_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    path = ledger_path(session_id)
    if not path.exists():
        return []
    lines: Iterable[str] = path.read_text(encoding="utf-8").splitlines()
    if limit is not None and limit >= 0:
        lines = list(lines)[-limit:]
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def summarize_run(session_id: str, *, tail: int = 20) -> dict[str, Any]:
    """Build a compact inspect snapshot for one session's durable run ledger."""

    events = read_events(session_id)
    event_counts: dict[str, int] = {}
    tools: dict[str, dict[str, Any]] = {}
    status = None
    started_at = None
    updated_at = None
    last_metrics = None

    for event in events:
        event_type = str(event.get("event") or "")
        if event_type:
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
        ts = event.get("ts")
        if ts:
            started_at = started_at or ts
            updated_at = ts
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if event_type == "run_status":
            status = payload.get("status") or status
            continue
        if event_type != "sse_event":
            continue

        payload_type = payload.get("type")
        if payload_type == "metrics":
            last_metrics = {
                key: payload.get(key)
                for key in ("input_tokens", "output_tokens", "total_tokens", "tokens_per_second", "usage_source")
                if key in payload
            }
        tool = payload.get("tool")
        if tool:
            entry = tools.setdefault(str(tool), {"starts": 0, "outputs": 0, "blocked": 0, "last_exit_code": None})
            if payload_type == "tool_start":
                entry["starts"] += 1
            elif payload_type == "tool_output":
                entry["outputs"] += 1
                entry["last_exit_code"] = payload.get("exit_code")
                if payload.get("blocked"):
                    entry["blocked"] += 1

    tail_count = max(0, int(tail or 0))
    return {
        "session_id": str(session_id),
        "exists": bool(events),
        "status": status,
        "started_at": started_at,
        "updated_at": updated_at,
        "event_count": len(events),
        "event_counts": event_counts,
        "tools": tools,
        "last_metrics": last_metrics,
        "tail": events[-tail_count:] if tail_count else [],
    }


def clear_events(session_id: str) -> None:
    """Test/support helper: remove one session's ledger file if present."""

    try:
        os.remove(ledger_path(session_id))
    except FileNotFoundError:
        pass
