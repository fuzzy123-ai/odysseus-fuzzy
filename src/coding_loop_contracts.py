"""Pure, content-free contracts for the production coding-loop controller."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import re
from typing import Any

from src.runtime_event_envelope import stable_payload_hash


CODING_LOOP_INTENT_SCHEMA = "odysseus.coding_loop_intent.v1"
MAX_LOOP_REFS = 64
MAX_LOOP_TURNS = 32
MAX_LOOP_INTENTS = 32
MAX_REPAIR_ATTEMPTS = 2

_ID_RE = re.compile(r"^[A-Za-z0-9_.:@+\-=]{1,180}$")
_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SECRET_RE = re.compile(
    r"(?i)\b(authorization|bearer|chat[_-]?id|cookie|credential|password|passwd|secret|token|api[_-]?key)\b"
)
_PRIVATE_PATH_RE = re.compile(
    r"(^|[\s'\"=])([A-Za-z]:[\\/]|/(home|Users|var|opt|mnt|srv)/|~[\\/])",
    re.IGNORECASE,
)
_RAW_KEYS = {
    "arguments", "body", "bytes", "content", "diff", "env", "log", "message",
    "objective", "output", "patch", "prompt", "raw", "raw_content", "snippet",
    "stderr", "stdout", "text",
}


class CodingLoopContractError(ValueError):
    """Raised when a controller contract is unsafe or structurally invalid."""


class CodingLoopCommandKind(StrEnum):
    ADVANCE = "advance"
    MUTATION_INTENT = "mutation_intent"
    CHECK_INTENT = "check_intent"
    REVIEW = "review"
    REPAIR = "repair"


class CodingLoopIntentKind(StrEnum):
    REQUEST_EXACT_READ = "request_exact_read"
    PROPOSE_SCOPED_PATCH = "propose_scoped_patch"
    REQUEST_BOUNDED_CHECK = "request_bounded_check"


class CodingGateOwner(StrEnum):
    MACHINE_AUTO = "machine_auto"
    AGENT_AUTO = "agent_auto"
    USER_ACCEPTANCE = "user_acceptance"


class CodingGateSubject(StrEnum):
    SCHEMA = "schema"
    SCOPE = "scope"
    CLAIM = "claim"
    EXACT_READ = "exact_read"
    CHECK_RESULT = "check_result"
    REPAIR_BUDGET = "repair_budget"
    ROUTINE_IMPLEMENTATION = "routine_implementation"
    BOUNDED_VERIFICATION = "bounded_verification"
    INDEPENDENT_REVIEW = "independent_review"
    PRODUCT = "product"
    ARCHITECTURE = "architecture"
    SECURITY = "security"
    PRIVACY = "privacy"
    SCOPE_EXPANSION = "scope_expansion"
    DESTRUCTIVE = "destructive"
    EXTERNAL = "external"
    LIVE = "live"


_MACHINE_SUBJECTS = frozenset(
    {
        CodingGateSubject.SCHEMA,
        CodingGateSubject.SCOPE,
        CodingGateSubject.CLAIM,
        CodingGateSubject.EXACT_READ,
        CodingGateSubject.CHECK_RESULT,
        CodingGateSubject.REPAIR_BUDGET,
    }
)
_AGENT_SUBJECTS = frozenset(
    {
        CodingGateSubject.ROUTINE_IMPLEMENTATION,
        CodingGateSubject.BOUNDED_VERIFICATION,
        CodingGateSubject.INDEPENDENT_REVIEW,
    }
)
_USER_SUBJECTS = frozenset(set(CodingGateSubject) - _MACHINE_SUBJECTS - _AGENT_SUBJECTS)


@dataclass(frozen=True, slots=True)
class CodingGateDecision:
    decision_id: str
    owner: CodingGateOwner
    subject: CodingGateSubject
    decision_ref: str
    planning_item_id: str
    planning_revision: str
    claim_id: str
    input_revision: str
    accepted: bool
    source: str = "planning"

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner", _enum(self.owner, CodingGateOwner, "owner"))
        object.__setattr__(self, "subject", _enum(self.subject, CodingGateSubject, "subject"))
        for field_name in (
            "decision_ref", "planning_item_id", "planning_revision", "claim_id",
            "input_revision",
        ):
            _strict_id(getattr(self, field_name), field_name)
        _sha256(self.decision_id, "decision_id")
        if type(self.accepted) is not bool:
            raise CodingLoopContractError("accepted must be boolean")
        if self.source != "planning":
            raise CodingLoopContractError("gate decision source must be planning")
        allowed = {
            CodingGateOwner.MACHINE_AUTO: _MACHINE_SUBJECTS,
            CodingGateOwner.AGENT_AUTO: _AGENT_SUBJECTS,
            CodingGateOwner.USER_ACCEPTANCE: _USER_SUBJECTS,
        }[self.owner]
        if self.subject not in allowed:
            raise CodingLoopContractError("gate owner cannot decide this subject")
        if self.decision_id != stable_payload_hash(self.semantic_dict()):
            raise CodingLoopContractError("decision_id does not match canonical gate facts")

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner.value,
            "subject": self.subject.value,
            "decision_ref": self.decision_ref,
            "planning_item_id": self.planning_item_id,
            "planning_revision": self.planning_revision,
            "claim_id": self.claim_id,
            "input_revision": self.input_revision,
            "accepted": self.accepted,
            "source": "planning",
        }

    def to_dict(self) -> dict[str, Any]:
        return {"decision_id": self.decision_id, **self.semantic_dict()}


@dataclass(frozen=True, slots=True)
class CodingLoopModelCommand:
    command_kind: CodingLoopCommandKind
    command_ref: str
    target_state: str = ""
    intent_kind: CodingLoopIntentKind | None = None
    role: str = ""
    target_graph_ref: str = ""
    exact_read_required_ref: str = ""
    payload_digest: str = ""
    evidence_ref: str = ""
    repair_plan_ref: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "command_kind", _enum(self.command_kind, CodingLoopCommandKind, "command_kind")
        )
        if self.intent_kind is not None:
            object.__setattr__(
                self, "intent_kind", _enum(self.intent_kind, CodingLoopIntentKind, "intent_kind")
            )
        _strict_id(self.command_ref, "command_ref")
        for field_name in (
            "target_state", "role", "target_graph_ref", "exact_read_required_ref",
            "evidence_ref", "repair_plan_ref",
        ):
            value = getattr(self, field_name)
            if value:
                _strict_id(value, field_name)
        if self.payload_digest:
            _sha256(self.payload_digest, "payload_digest")
        if self.command_kind in {CodingLoopCommandKind.MUTATION_INTENT, CodingLoopCommandKind.CHECK_INTENT}:
            if self.intent_kind is None or not self.target_graph_ref:
                raise CodingLoopContractError("intent command requires typed intent and graph ref")
        elif self.intent_kind is not None:
            raise CodingLoopContractError("intent_kind is only valid for intent commands")
        if self.command_kind is CodingLoopCommandKind.ADVANCE and not self.target_state:
            raise CodingLoopContractError("advance command requires target_state")
        if self.command_kind is CodingLoopCommandKind.REVIEW and (
            self.target_state != "review_ready" or not self.evidence_ref
        ):
            raise CodingLoopContractError("review command requires review_ready and evidence_ref")
        if self.command_kind is CodingLoopCommandKind.REPAIR and not self.repair_plan_ref:
            raise CodingLoopContractError("repair command requires repair_plan_ref")

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "command_kind": self.command_kind.value,
            "command_ref": self.command_ref,
            "target_state": self.target_state,
            "intent_kind": self.intent_kind.value if self.intent_kind else "",
            "role": self.role,
            "target_graph_ref": self.target_graph_ref,
            "exact_read_required_ref": self.exact_read_required_ref,
            "payload_digest": self.payload_digest,
            "evidence_ref": self.evidence_ref,
            "repair_plan_ref": self.repair_plan_ref,
        }

    @property
    def digest(self) -> str:
        return stable_payload_hash(self.semantic_dict())


@dataclass(frozen=True, slots=True)
class CodingLoopIntent:
    intent_id: str
    intent_kind: CodingLoopIntentKind
    command_ref: str
    planning_item_id: str
    planning_revision: str
    claim_id: str
    claim_owner: str
    scope_digest: str
    input_revision: str
    parent_envelope_id: str
    capsule_id: str
    role: str
    target_graph_ref: str
    exact_read_required_ref: str
    payload_digest: str = ""
    execution_allowed: bool = False
    edit_allowed: bool = False
    write_allowed: bool = False
    dispatch_allowed: bool = False
    gate_close_allowed: bool = False
    live_effect_allowed: bool = False
    side_effects: tuple[str, ...] = ("none",)
    raw_content_visible: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_kind", _enum(self.intent_kind, CodingLoopIntentKind, "intent_kind"))
        for field_name in (
            "command_ref", "planning_item_id", "planning_revision", "claim_id",
            "claim_owner", "input_revision", "parent_envelope_id", "capsule_id", "role",
            "target_graph_ref", "exact_read_required_ref",
        ):
            _strict_id(getattr(self, field_name), field_name)
        for field_name in ("intent_id", "scope_digest"):
            _sha256(getattr(self, field_name), field_name)
        if self.payload_digest:
            _sha256(self.payload_digest, "payload_digest")
        for flag in (
            "execution_allowed", "edit_allowed", "write_allowed", "dispatch_allowed",
            "gate_close_allowed", "live_effect_allowed",
        ):
            if getattr(self, flag) is not False:
                raise CodingLoopContractError(f"{flag} must remain false")
        if self.side_effects != ("none",) or self.raw_content_visible is not False:
            raise CodingLoopContractError("intent must remain content-free and side-effect free")
        if self.intent_id != stable_payload_hash(self.semantic_dict()):
            raise CodingLoopContractError("intent_id does not match canonical intent facts")

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "intent_kind": self.intent_kind.value,
            "command_ref": self.command_ref,
            "planning_item_id": self.planning_item_id,
            "planning_revision": self.planning_revision,
            "claim_id": self.claim_id,
            "claim_owner": self.claim_owner,
            "scope_digest": self.scope_digest,
            "input_revision": self.input_revision,
            "parent_envelope_id": self.parent_envelope_id,
            "capsule_id": self.capsule_id,
            "role": self.role,
            "target_graph_ref": self.target_graph_ref,
            "exact_read_required_ref": self.exact_read_required_ref,
            "payload_digest": self.payload_digest,
            "execution_allowed": False,
            "edit_allowed": False,
            "write_allowed": False,
            "dispatch_allowed": False,
            "gate_close_allowed": False,
            "live_effect_allowed": False,
            "side_effects": ("none",),
            "raw_content_visible": False,
        }

    def to_dict(self) -> dict[str, Any]:
        payload = {"schema": CODING_LOOP_INTENT_SCHEMA, "intent_id": self.intent_id, **self.semantic_dict()}
        _reject_unsafe_payload(payload)
        return payload


def create_coding_loop_intent(**facts: Any) -> CodingLoopIntent:
    core = dict(facts)
    core.pop("intent_id", None)
    kind = _enum(core.get("intent_kind"), CodingLoopIntentKind, "intent_kind")
    core["intent_kind"] = kind.value
    for field, default in (
        ("payload_digest", ""), ("execution_allowed", False), ("edit_allowed", False),
        ("write_allowed", False), ("dispatch_allowed", False),
        ("gate_close_allowed", False), ("live_effect_allowed", False),
        ("side_effects", ("none",)), ("raw_content_visible", False),
    ):
        core.setdefault(field, default)
    return CodingLoopIntent(intent_id=stable_payload_hash(core), **facts)


def create_coding_gate_decision(**facts: Any) -> CodingGateDecision:
    core = dict(facts)
    core.pop("decision_id", None)
    owner = _enum(core.get("owner"), CodingGateOwner, "owner")
    subject = _enum(core.get("subject"), CodingGateSubject, "subject")
    core["owner"] = owner.value
    core["subject"] = subject.value
    core.setdefault("source", "planning")
    return CodingGateDecision(decision_id=stable_payload_hash(core), **facts)


def validate_budget(value: Any, field: str, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise CodingLoopContractError(f"{field} is outside its bounded range")
    return value


def strict_id(value: Any, field: str) -> str:
    return _strict_id(value, field)


def _strict_id(value: Any, field: str) -> str:
    if (
        not isinstance(value, str) or value != value.strip() or not _ID_RE.fullmatch(value)
        or _SECRET_RE.search(value) or _PRIVATE_PATH_RE.search(value)
    ):
        raise CodingLoopContractError(f"{field} must be a safe bounded identifier")
    return value


def _sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise CodingLoopContractError(f"{field} must be canonical SHA-256")
    return value


def _enum(value: Any, enum_type: type[StrEnum], field: str):
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise CodingLoopContractError(f"{field} is invalid") from exc


def _reject_unsafe_payload(value: Any, *, key: str = "") -> None:
    if key.lower().replace("-", "_") in _RAW_KEYS:
        raise CodingLoopContractError("coding loop payload contains a raw field")
    if isinstance(value, dict):
        for child_key, child in value.items():
            _reject_unsafe_payload(child, key=str(child_key))
    elif isinstance(value, (tuple, list)):
        for child in value:
            _reject_unsafe_payload(child, key=key)
    elif isinstance(value, str) and (_SECRET_RE.search(value) or _PRIVATE_PATH_RE.search(value)):
        raise CodingLoopContractError("coding loop payload contains private material")
    try:
        json.dumps(value)
    except (TypeError, ValueError) as exc:
        raise CodingLoopContractError("coding loop payload must be JSON serializable") from exc


__all__ = [
    "CODING_LOOP_INTENT_SCHEMA", "MAX_LOOP_INTENTS", "MAX_LOOP_REFS",
    "MAX_LOOP_TURNS", "MAX_REPAIR_ATTEMPTS", "CodingGateDecision",
    "CodingGateOwner", "CodingGateSubject", "CodingLoopCommandKind",
    "CodingLoopContractError", "CodingLoopIntent", "CodingLoopIntentKind",
    "CodingLoopModelCommand", "create_coding_loop_intent", "strict_id",
    "create_coding_gate_decision", "validate_budget",
]
