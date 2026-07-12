"""Canonical gate and evidence core models.

The module is intentionally stdlib-only and side-effect free.  It gives later
route or storage adapters a small, deterministic payload shape without touching
any existing gate payloads.
"""

from __future__ import annotations

from dataclasses import dataclass, is_dataclass
from enum import StrEnum
import re
from typing import Any, Iterable, Mapping


_MAX_ID = 96
_MAX_TEXT = 240
_MAX_LONG_TEXT = 500
_NON_TOKEN_CHARS_RE = re.compile(r"[^a-z0-9]+")
_PRIVATE_PATH_RE = re.compile(
    r"(?i)(?:\b[A-Z]:\\Users\\[^\\\s]+\\|/Users/[^/\s]+/|/home/[^/\s]+/)"
)
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:\bsk-[a-z0-9_-]{12,}\b|\bgh[pousr]_[a-z0-9_]{12,}\b|"
    r"\bxox[abprs]-[a-z0-9-]{12,}\b|\bbearer\s+[a-z0-9._-]{12,})"
)
_SENSITIVE_KEY_TOKENS = {
    "api_key",
    "apikey",
    "auth_token",
    "bearer_token",
    "chat_id",
    "chatid",
    "client_secret",
    "password",
    "private_content",
    "private_path",
    "provider_raw",
    "raw_output",
    "raw_provider_output",
    "refresh_token",
    "secret",
    "secrets",
    "telegram_chat_id",
    "token",
}


class GateEvidenceCoreError(ValueError):
    """Raised when a canonical gate/evidence payload is invalid or unsafe."""


class GateFamily(StrEnum):
    DESIGN = "design"
    EVIDENCE = "evidence"
    LIVE = "live"
    OPERATOR = "operator"
    PRIVACY = "privacy"
    QUALITY = "quality"
    RELEASE = "release"
    SCOPE = "scope"
    SECURITY = "security"
    TESTS = "tests"
    GENERIC = "generic"


class GateClass(StrEnum):
    ADVISORY = "advisory"
    EVIDENCE = "evidence"
    EXECUTION = "execution"
    POLICY = "policy"
    PRECHECK = "precheck"
    READINESS = "readiness"
    STOP_RULE = "stop_rule"


class GateStatus(StrEnum):
    GO = "go"
    PARTIAL = "partial"
    DEFERRED = "deferred"
    BLOCKED = "blocked"
    NO_GO = "no_go"


class RedactionFlag(StrEnum):
    NONE = "none"
    PRIVATE_CONTENT_OMITTED = "private_content_omitted"
    RAW_PROVIDER_OUTPUT_OMITTED = "raw_provider_output_omitted"
    SECRET_OMITTED = "secret_omitted"
    SUMMARY_ONLY = "summary_only"


class NextActionType(StrEnum):
    NONE = "none"
    COLLECT_EVIDENCE = "collect_evidence"
    DEFER = "defer"
    FIX_BLOCKER = "fix_blocker"
    PROCEED = "proceed"
    REQUEST_LIVE_GO = "request_live_go"
    REQUEST_OPERATOR_DECISION = "request_operator_decision"


class LiveRequirement(StrEnum):
    NOT_REQUIRED = "not_required"
    DRY_RUN_ONLY = "dry_run_only"
    REQUIRED = "required"
    PROHIBITED = "prohibited"


class OperatorDecision(StrEnum):
    NOT_REQUIRED = "not_required"
    APPROVED = "approved"
    REQUIRED = "required"
    PENDING = "pending"
    DECLINED = "declined"


def _token(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = str(value or "").strip().lower()
    text = _NON_TOKEN_CHARS_RE.sub("_", text).strip("_")
    text = re.sub(r"_{2,}", "_", text)
    if not text:
        if allow_empty:
            return ""
        raise GateEvidenceCoreError(f"{field_name} must not be empty")
    if len(text) > _MAX_ID:
        raise GateEvidenceCoreError(f"{field_name} exceeds max length {_MAX_ID}")
    return text


def _text(value: Any, *, field_name: str, allow_empty: bool = False, limit: int = _MAX_TEXT) -> str:
    assert_redaction_safe({field_name: value})
    text = " ".join(str(value or "").split())
    if not text:
        if allow_empty:
            return ""
        raise GateEvidenceCoreError(f"{field_name} must not be empty")
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _enum(value: Any, enum_type: type[StrEnum], *, field_name: str, aliases: Mapping[str, str] | None = None) -> Any:
    if isinstance(value, enum_type):
        return value
    normalized = _token(value, field_name=field_name)
    canonical = (aliases or {}).get(normalized, normalized)
    try:
        return enum_type(canonical)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise GateEvidenceCoreError(f"{field_name} must be one of: {allowed}") from exc


def _unique_texts(values: Iterable[Any], *, field_name: str, limit: int = _MAX_LONG_TEXT) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value, field_name=field_name, allow_empty=True, limit=limit)
        if text and text not in seen:
            seen.add(text)
            normalized.append(text)
    return tuple(normalized)


def _redaction_flags(values: Iterable[RedactionFlag | str] | None) -> tuple[RedactionFlag, ...]:
    flags = tuple(
        sorted(
            {_enum(value, RedactionFlag, field_name="redaction_flags") for value in (values or (RedactionFlag.NONE,))},
            key=lambda item: item.value,
        )
    )
    if RedactionFlag.NONE in flags and len(flags) > 1:
        raise GateEvidenceCoreError("redaction_flags must not combine none with omission flags")
    return flags


def _sensitive_key_name(key: Any) -> str | None:
    normalized = _token(key, field_name="payload_key", allow_empty=True)
    if not normalized:
        return None
    parts = set(normalized.split("_"))
    if normalized in _SENSITIVE_KEY_TOKENS:
        return normalized
    if "token" in parts or "secret" in parts:
        return normalized
    if {"chat", "id"}.issubset(parts):
        return normalized
    if {"private", "path"}.issubset(parts) or {"private", "content"}.issubset(parts):
        return normalized
    if {"raw", "provider", "output"}.issubset(parts):
        return normalized
    return None


def assert_redaction_safe(payload: Any, *, path: str = "payload") -> None:
    """Reject obvious secrets, chat IDs, private paths/content, and raw outputs.

    This helper is deliberately conservative about field names and common secret
    value shapes.  It is meant to run before payload persistence or logging.
    """

    if is_dataclass(payload) and not isinstance(payload, type):
        payload = payload.to_dict() if hasattr(payload, "to_dict") else payload.__dict__
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            sensitive_key = _sensitive_key_name(key)
            if sensitive_key:
                raise GateEvidenceCoreError(f"{path}.{key} contains blocked sensitive field {sensitive_key}")
            assert_redaction_safe(value, path=f"{path}.{key}")
        return
    if isinstance(payload, (list, tuple, set, frozenset)):
        for index, item in enumerate(payload):
            assert_redaction_safe(item, path=f"{path}[{index}]")
        return
    if isinstance(payload, str):
        if _SECRET_VALUE_RE.search(payload):
            raise GateEvidenceCoreError(f"{path} contains a blocked secret or token value")
        if _PRIVATE_PATH_RE.search(payload):
            raise GateEvidenceCoreError(f"{path} contains a blocked private path")


assert_no_sensitive_payload = assert_redaction_safe


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    evidence_id: str
    summary: str
    source: str
    redaction_flags: tuple[RedactionFlag, ...]

    @classmethod
    def create(
        cls,
        *,
        evidence_id: Any,
        summary: Any,
        source: Any,
        redaction_flags: Iterable[RedactionFlag | str] | None = None,
    ) -> "EvidenceItem":
        return cls(
            evidence_id=_token(evidence_id, field_name="evidence_id"),
            summary=_text(summary, field_name="summary", limit=_MAX_LONG_TEXT),
            source=_text(source, field_name="source"),
            redaction_flags=_redaction_flags(redaction_flags),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.evidence_id,
            "summary": self.summary,
            "source": self.source,
            "redaction_flags": [flag.value for flag in self.redaction_flags],
        }


@dataclass(frozen=True, slots=True)
class NextAction:
    action_type: NextActionType
    summary: str

    @classmethod
    def create(cls, *, action_type: NextActionType | str = NextActionType.NONE, summary: Any = "") -> "NextAction":
        normalized_type = _enum(action_type, NextActionType, field_name="next_action.action_type")
        normalized_summary = _text(
            summary,
            field_name="next_action.summary",
            allow_empty=normalized_type == NextActionType.NONE,
            limit=_MAX_LONG_TEXT,
        )
        if normalized_type == NextActionType.NONE and normalized_summary:
            raise GateEvidenceCoreError("next_action none must not carry a summary")
        return cls(action_type=normalized_type, summary=normalized_summary)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.action_type.value,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class CanonicalGate:
    gate_id: str
    family: GateFamily
    gate_class: GateClass
    status: GateStatus
    evidence: tuple[EvidenceItem, ...]
    redaction_flags: tuple[RedactionFlag, ...]
    next_action: NextAction
    live_requirement: LiveRequirement
    operator_decision: OperatorDecision
    safe_actions: tuple[str, ...]
    blockers: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        gate_id: Any,
        family: GateFamily | str,
        gate_class: GateClass | str,
        status: GateStatus | str,
        evidence: Iterable[EvidenceItem],
        redaction_flags: Iterable[RedactionFlag | str] | None = None,
        next_action: NextAction | None = None,
        live_requirement: LiveRequirement | str = LiveRequirement.NOT_REQUIRED,
        operator_decision: OperatorDecision | str = OperatorDecision.NOT_REQUIRED,
        safe_actions: Iterable[Any] = (),
        blockers: Iterable[Any] = (),
    ) -> "CanonicalGate":
        normalized_status = _enum(
            status,
            GateStatus,
            field_name="status",
            aliases={
                "go": "go",
                "no_go": "no_go",
                "nogo": "no_go",
                "pass": "go",
                "warn": "partial",
                "block": "blocked",
                "fail": "no_go",
            },
        )
        normalized_evidence = tuple(evidence)
        if not all(isinstance(item, EvidenceItem) for item in normalized_evidence):
            raise GateEvidenceCoreError("evidence must contain EvidenceItem instances")
        normalized_next = next_action or NextAction.create()
        normalized_live = _enum(live_requirement, LiveRequirement, field_name="live_requirement")
        normalized_operator = _enum(operator_decision, OperatorDecision, field_name="operator_decision")
        normalized_blockers = _unique_texts(blockers, field_name="blockers")
        normalized_safe_actions = _unique_texts(safe_actions, field_name="safe_actions")
        if normalized_status == GateStatus.GO and not normalized_evidence:
            raise GateEvidenceCoreError("go gates require evidence")
        if normalized_status in {GateStatus.BLOCKED, GateStatus.NO_GO} and not normalized_blockers:
            raise GateEvidenceCoreError("blocked and no_go gates require blockers")
        if normalized_status in {GateStatus.DEFERRED, GateStatus.PARTIAL} and (
            not normalized_blockers and normalized_next.action_type == NextActionType.NONE
        ):
            raise GateEvidenceCoreError("partial and deferred gates require blockers or a next_action")
        return cls(
            gate_id=_token(gate_id, field_name="gate_id"),
            family=_enum(family, GateFamily, field_name="family"),
            gate_class=_enum(gate_class, GateClass, field_name="gate_class"),
            status=normalized_status,
            evidence=tuple(sorted(normalized_evidence, key=lambda item: item.evidence_id)),
            redaction_flags=_redaction_flags(redaction_flags),
            next_action=normalized_next,
            live_requirement=normalized_live,
            operator_decision=normalized_operator,
            safe_actions=normalized_safe_actions,
            blockers=normalized_blockers,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "gate_evidence_core.v1",
            "family": self.family.value,
            "id": self.gate_id,
            "class": self.gate_class.value,
            "status": self.status.value,
            "evidence": [item.to_dict() for item in self.evidence],
            "redaction_flags": [flag.value for flag in self.redaction_flags],
            "next_action": self.next_action.to_dict(),
            "live_requirement": self.live_requirement.value,
            "operator_decision": self.operator_decision.value,
            "safe_actions": list(self.safe_actions),
            "blockers": list(self.blockers),
        }


def _gate_payload(gate: CanonicalGate | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(gate, CanonicalGate):
        return gate.to_dict()
    if not isinstance(gate, Mapping):
        raise GateEvidenceCoreError("gate payloads must be CanonicalGate instances or mappings")
    assert_redaction_safe(gate)
    payload = dict(gate)
    if "id" not in payload and "gate_id" in payload:
        payload["id"] = payload["gate_id"]
    return payload


def _payload_status(payload: Mapping[str, Any]) -> GateStatus:
    return _enum(
        payload.get("status"),
        GateStatus,
        field_name="status",
        aliases={
            "go": "go",
            "no_go": "no_go",
            "nogo": "no_go",
            "pass": "go",
            "warn": "partial",
            "block": "blocked",
            "fail": "no_go",
        },
    )


def _payload_next_action(payload: Mapping[str, Any]) -> str:
    next_action = payload.get("next_action")
    if isinstance(next_action, Mapping):
        return _text(next_action.get("summary"), field_name="next_action.summary", allow_empty=True)
    return _text(next_action, field_name="next_action", allow_empty=True)


def _payload_texts(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    values = payload.get(key)
    if values is None:
        return ()
    if isinstance(values, str):
        values = (values,)
    if not isinstance(values, Iterable):
        return ()
    return _unique_texts(values, field_name=key)


def what_can_safely_happen_now(gates: Iterable[CanonicalGate | Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize gate payloads into immediate safe actions and blockers."""

    payloads = [_gate_payload(gate) for gate in gates]
    seen_ids: set[str] = set()
    safe_actions: list[str] = []
    blockers: list[dict[str, str]] = []
    partial_gate_ids: list[str] = []
    deferred_gate_ids: list[str] = []
    live_required_gate_ids: list[str] = []
    operator_required_gate_ids: list[str] = []

    status_rank = {
        GateStatus.GO: 0,
        GateStatus.PARTIAL: 1,
        GateStatus.DEFERRED: 2,
        GateStatus.BLOCKED: 3,
        GateStatus.NO_GO: 4,
    }
    worst_status = GateStatus.GO

    for payload in sorted(payloads, key=lambda item: str(item.get("id") or item.get("gate_id") or "")):
        gate_id = _token(payload.get("id") or payload.get("gate_id"), field_name="id")
        if gate_id in seen_ids:
            raise GateEvidenceCoreError("gate ids must be unique within an aggregate")
        seen_ids.add(gate_id)
        status = _payload_status(payload)
        if status_rank[status] > status_rank[worst_status]:
            worst_status = status

        live_requirement = _enum(
            payload.get("live_requirement", LiveRequirement.NOT_REQUIRED),
            LiveRequirement,
            field_name="live_requirement",
        )
        operator_decision = _enum(
            payload.get("operator_decision", OperatorDecision.NOT_REQUIRED),
            OperatorDecision,
            field_name="operator_decision",
        )
        if live_requirement in {LiveRequirement.REQUIRED, LiveRequirement.PROHIBITED}:
            live_required_gate_ids.append(gate_id)
            blockers.append(
                {
                    "id": gate_id,
                    "status": status.value,
                    "reason": f"live_requirement:{live_requirement.value}",
                }
            )
        if operator_decision in {OperatorDecision.REQUIRED, OperatorDecision.PENDING, OperatorDecision.DECLINED}:
            operator_required_gate_ids.append(gate_id)
            blockers.append(
                {
                    "id": gate_id,
                    "status": status.value,
                    "reason": f"operator_decision:{operator_decision.value}",
                }
            )

        payload_blockers = _payload_texts(payload, "blockers")
        if status == GateStatus.PARTIAL:
            partial_gate_ids.append(gate_id)
        if status == GateStatus.DEFERRED:
            deferred_gate_ids.append(gate_id)
        if status in {GateStatus.DEFERRED, GateStatus.BLOCKED, GateStatus.NO_GO}:
            reason = payload_blockers[0] if payload_blockers else _payload_next_action(payload) or status.value
            blockers.append({"id": gate_id, "status": status.value, "reason": reason})
        elif status == GateStatus.PARTIAL and payload_blockers:
            blockers.append({"id": gate_id, "status": status.value, "reason": payload_blockers[0]})

        if (
            status in {GateStatus.GO, GateStatus.PARTIAL}
            and live_requirement in {LiveRequirement.NOT_REQUIRED, LiveRequirement.DRY_RUN_ONLY}
            and operator_decision in {OperatorDecision.NOT_REQUIRED, OperatorDecision.APPROVED}
        ):
            action = _payload_next_action(payload)
            if status in {GateStatus.GO, GateStatus.PARTIAL} and action and action not in safe_actions:
                safe_actions.append(action)
            for action in _payload_texts(payload, "safe_actions"):
                if action not in safe_actions:
                    safe_actions.append(action)

    can_proceed = bool(payloads) and not blockers and worst_status in {GateStatus.GO, GateStatus.PARTIAL}
    return {
        "schema": "gate_evidence_core.safe_now.v1",
        "gate_count": len(payloads),
        "decision": worst_status.value if payloads else "no_go",
        "can_proceed": can_proceed,
        "safe_actions": safe_actions,
        "blockers": blockers,
        "partial_gate_ids": partial_gate_ids,
        "deferred_gate_ids": deferred_gate_ids,
        "live_required_gate_ids": live_required_gate_ids,
        "operator_required_gate_ids": operator_required_gate_ids,
    }
