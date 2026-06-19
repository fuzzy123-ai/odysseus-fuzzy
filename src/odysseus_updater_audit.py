"""Offline-safe audit ledger models for Odysseus updater attempts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import re
from typing import Any, Mapping

_DECISIONS = {"hold", "partial", "go", "no_go", "deferred"}
_RESULTS = {
    "pending",
    "applied",
    "held",
    "rolled_back",
    "failed",
    "cancelled",
    "deferred",
    "partial",
    "no_go",
}
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|passwd|chat[_-]?id|authorization)\b\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+\S+")
_URL_RE = re.compile(r"(?i)\b(?:https?|ssh)://\S+")
_WINDOWS_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:\\[^\s`]+")
_UNIX_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])/(?:[^\s/`]+/)*[^\s`]+")
_SECRET_KEY_RE = re.compile(r"(?i)(token|secret|password|passwd|api[_-]?key|chat[_-]?id|authorization)$")
_MAX_TEXT_LEN = 240


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_optional_text(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    text = _normalize_text(value, field_name=field_name, allow_empty=True)
    return text or None


def _normalize_id(value: Any, *, field_name: str) -> str:
    text = _normalize_text(value, field_name=field_name)
    if not _SAFE_ID_RE.fullmatch(text):
        raise ValueError(f"{field_name} must be a compact safe identifier")
    return text


def _normalize_decision(value: Any) -> str:
    decision = _normalize_text(value, field_name="operator_decision").lower().replace("-", "_")
    if decision not in _DECISIONS:
        raise ValueError(f"unsupported operator_decision: {value!r}")
    return decision


def _normalize_result(value: Any) -> str:
    result = _normalize_text(value, field_name="result").lower().replace("-", "_")
    if result not in _RESULTS:
        raise ValueError(f"unsupported result: {value!r}")
    return result


def _normalize_iso_datetime(value: Any, *, field_name: str, allow_empty: bool = False) -> str | None:
    if value in (None, ""):
        if allow_empty:
            return None
        raise ValueError(f"{field_name} must not be empty")
    if isinstance(value, datetime):
        dt = value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    text = str(value).strip()
    if not text:
        if allow_empty:
            return None
        raise ValueError(f"{field_name} must not be empty")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime") from exc
    return (dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def redact_audit_text(value: Any, *, limit: int = _MAX_TEXT_LEN) -> str:
    text = str(value or "")
    text = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _URL_RE.sub("[redacted-url]", text)
    text = _WINDOWS_PATH_RE.sub("[redacted-path]", text)
    text = _UNIX_PATH_RE.sub("[redacted-path]", text)
    text = "".join(ch if ord(ch) >= 32 else " " for ch in text)
    text = " ".join(text.strip().split())
    if len(text) > limit:
        return text[: limit - 12].rstrip() + "...[truncated]"
    return text


def redact_notes(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        items = [values]
    else:
        items = list(values)
    redacted: list[str] = []
    for item in items:
        text = redact_audit_text(item)
        if text and text not in redacted:
            redacted.append(text)
    return tuple(redacted)


def redact_gate_statuses(value: Mapping[str, Any] | None) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for raw_key, raw_status in dict(value or {}).items():
        gate_id = _normalize_id(raw_key, field_name="gate_status")
        if _SECRET_KEY_RE.search(gate_id):
            sanitized[gate_id] = "[redacted]"
            continue
        sanitized[gate_id] = redact_audit_text(raw_status, limit=80) or "unknown"
    return dict(sorted(sanitized.items()))


@dataclass(frozen=True, slots=True)
class OdysseusUpdaterAuditRecord:
    plan_id: str
    source_ref: str
    current_ref: str
    target_ref: str
    gate_statuses: dict[str, str]
    operator_decision: str
    result: str
    started_at: str | None = None
    completed_at: str | None = None
    rollback_or_hold_note: str | None = None
    redacted_notes: tuple[str, ...] = ()
    recorded_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _normalize_id(self.plan_id, field_name="plan_id"))
        object.__setattr__(self, "source_ref", _normalize_text(self.source_ref, field_name="source_ref"))
        object.__setattr__(self, "current_ref", _normalize_text(self.current_ref, field_name="current_ref"))
        object.__setattr__(self, "target_ref", _normalize_text(self.target_ref, field_name="target_ref"))
        object.__setattr__(self, "gate_statuses", redact_gate_statuses(self.gate_statuses))
        if not self.gate_statuses:
            raise ValueError("gate_statuses must not be empty")
        object.__setattr__(self, "operator_decision", _normalize_decision(self.operator_decision))
        object.__setattr__(self, "result", _normalize_result(self.result))
        object.__setattr__(
            self,
            "started_at",
            _normalize_iso_datetime(self.started_at, field_name="started_at", allow_empty=True),
        )
        object.__setattr__(
            self,
            "completed_at",
            _normalize_iso_datetime(self.completed_at, field_name="completed_at", allow_empty=True),
        )
        object.__setattr__(
            self,
            "rollback_or_hold_note",
            _normalize_optional_text(
                redact_audit_text(self.rollback_or_hold_note),
                field_name="rollback_or_hold_note",
            ),
        )
        object.__setattr__(self, "redacted_notes", redact_notes(self.redacted_notes))
        object.__setattr__(
            self,
            "recorded_at",
            _normalize_iso_datetime(self.recorded_at or _now_iso(), field_name="recorded_at"),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OdysseusUpdaterAuditRecord":
        return cls(
            plan_id=payload.get("plan_id", ""),
            source_ref=payload.get("source_ref", ""),
            current_ref=payload.get("current_ref", ""),
            target_ref=payload.get("target_ref", ""),
            gate_statuses=dict(payload.get("gate_statuses") or {}),
            operator_decision=payload.get("operator_decision", ""),
            result=payload.get("result", ""),
            started_at=payload.get("started_at"),
            completed_at=payload.get("completed_at"),
            rollback_or_hold_note=payload.get("rollback_or_hold_note"),
            redacted_notes=tuple(payload.get("redacted_notes") or ()),
            recorded_at=payload.get("recorded_at", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "source_ref": self.source_ref,
            "current_ref": self.current_ref,
            "target_ref": self.target_ref,
            "gate_statuses": dict(self.gate_statuses),
            "operator_decision": self.operator_decision,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "rollback_or_hold_note": self.rollback_or_hold_note,
            "redacted_notes": list(self.redacted_notes),
            "recorded_at": self.recorded_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)

    def to_handoff_quote(self) -> str:
        gate_summary = ", ".join(f"{gate}={status}" for gate, status in self.gate_statuses.items())
        started = self.started_at or "not_started"
        completed = self.completed_at or "not_completed"
        return (
            f"plan_id={self.plan_id}; refs={self.source_ref}->{self.current_ref}->{self.target_ref}; "
            f"decision={self.operator_decision}; result={self.result}; "
            f"gates={gate_summary}; started_at={started}; completed_at={completed}"
        )


def build_odysseus_updater_audit_record(
    *,
    plan_id: Any,
    source_ref: Any,
    current_ref: Any,
    target_ref: Any,
    gate_statuses: Mapping[str, Any],
    operator_decision: Any,
    result: Any,
    started_at: Any | None = None,
    completed_at: Any | None = None,
    rollback_or_hold_note: Any | None = None,
    redacted_notes: Any = (),
) -> OdysseusUpdaterAuditRecord:
    return OdysseusUpdaterAuditRecord(
        plan_id=plan_id,
        source_ref=source_ref,
        current_ref=current_ref,
        target_ref=target_ref,
        gate_statuses=dict(gate_statuses),
        operator_decision=operator_decision,
        result=result,
        started_at=started_at,
        completed_at=completed_at,
        rollback_or_hold_note=rollback_or_hold_note,
        redacted_notes=redacted_notes,
    )
