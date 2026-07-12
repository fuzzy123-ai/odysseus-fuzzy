"""Read-only operator review queue package contract.

The queue turns existing gate/readiness payloads into review items. It never
executes the proposed action and never preserves raw private source values.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Iterable, Mapping


OPERATOR_REVIEW_QUEUE_SCHEMA = "odysseus.operator_review_queue.v1"

SUPPORTED_REVIEW_FAMILIES = (
    "nextcloud_copy",
    "memory_write",
    "raptorgraph_write",
    "file_export",
    "security_action",
    "telegram_delivery",
    "coding_approval",
)

_SAFE_TOKEN_RE = re.compile(r"[^a-z0-9._:-]+")
_SENSITIVE_KEY_PARTS = (
    "secret",
    "token",
    "password",
    "credential",
    "chat_id",
    "raw",
    "content",
    "body",
    "prompt",
    "reply",
    "path",
    "url",
    "webdav",
    "command",
    "env",
    "filename",
    "file_name",
    "message",
    "transcript",
)


@dataclass(frozen=True, slots=True)
class OperatorReviewQueueItem:
    item_id: str
    family: str
    status: str
    proposed_action: str
    why: str
    risk: str
    required_gate: str
    safe_default: str
    next_action: str
    source_ref: str = ""
    priority: int = 50

    def to_dict(self) -> dict[str, Any]:
        source_hash = _hash({"source_ref": self.source_ref}) if self.source_ref else ""
        return {
            "item_id": _safe_id(self.item_id),
            "family": _family(self.family),
            "status": _status(self.status),
            "priority": max(0, min(100, int(self.priority))),
            "proposed_action": _safe_phrase(self.proposed_action),
            "why": _safe_phrase(self.why),
            "risk": _safe_phrase(self.risk),
            "required_gate": _safe_id(self.required_gate),
            "safe_default": _safe_phrase(self.safe_default),
            "next_action": _safe_id(self.next_action),
            "source_ref_hash": source_hash,
            "source_ref_visible": False,
            "raw_content_visible": False,
            "private_content_visible": False,
            "path_values_visible": False,
            "url_values_visible": False,
            "command_values_visible": False,
            "token_value_visible": False,
            "chat_id_value_visible": False,
            "write_action_enabled": False,
            "live_action_enabled": False,
        }


def build_operator_review_queue(
    *,
    review_gate_status: Mapping[str, Any] | None = None,
    live_affordance_readiness: Mapping[str, Any] | None = None,
    coding_approvals: Iterable[Mapping[str, Any]] = (),
    security_reviews: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Build a redacted read-only review queue from existing status payloads."""

    items = [
        *_items_from_review_gates(_mapping(review_gate_status)),
        *_items_from_live_affordances(_mapping(live_affordance_readiness)),
        *_items_from_generic(coding_approvals, family="coding_approval", required_gate="CODING-PUBLISH-GO"),
        *_items_from_generic(security_reviews, family="security_action", required_gate="SECURITY-ACTION-GO"),
    ]
    ordered = tuple(sorted(items, key=lambda item: (item.priority, item.item_id)))
    payload_items = tuple(item.to_dict() for item in ordered)
    pending_count = sum(1 for item in payload_items if item["status"] in {"pending_review", "ready_to_execute", "ready_to_write"})
    blocked_count = sum(1 for item in payload_items if item["status"] == "blocked")
    return {
        "schema": OPERATOR_REVIEW_QUEUE_SCHEMA,
        "status": "blocked" if blocked_count else ("pending" if pending_count else "clear"),
        "item_count": len(payload_items),
        "pending_count": pending_count,
        "blocked_count": blocked_count,
        "items": payload_items,
        "supported_families": SUPPORTED_REVIEW_FAMILIES,
        "raw_content_visible": False,
        "private_content_visible": False,
        "path_values_visible": False,
        "url_values_visible": False,
        "command_values_visible": False,
        "token_value_visible": False,
        "chat_id_value_visible": False,
        "live_action_enabled": False,
        "write_action_enabled": False,
    }


def _items_from_review_gates(payload: Mapping[str, Any]) -> tuple[OperatorReviewQueueItem, ...]:
    gates = payload.get("gates") if isinstance(payload.get("gates"), list) else []
    items: list[OperatorReviewQueueItem] = []
    for gate in gates:
        if not isinstance(gate, Mapping):
            continue
        state = _status(gate.get("state") or gate.get("status"))
        if state in {"no_pending", "done", "clear", "ok"}:
            continue
        family = _gate_family(gate)
        items.append(
            OperatorReviewQueueItem(
                item_id=f"review-{family}-{_safe_id(gate.get('id') or family)}",
                family=family,
                status=state,
                proposed_action=_proposed_action(family),
                why=_why(family, gate.get("reason")),
                risk=_risk(family),
                required_gate=_required_gate(family),
                safe_default="hold_without_live_write",
                next_action=_next_action(state),
                source_ref=str(gate.get("source_ref") or ""),
                priority=_priority(state, family),
            )
        )
    return tuple(items)


def _items_from_live_affordances(payload: Mapping[str, Any]) -> tuple[OperatorReviewQueueItem, ...]:
    affordances = payload.get("affordances") if isinstance(payload.get("affordances"), list) else []
    items: list[OperatorReviewQueueItem] = []
    for affordance in affordances:
        if not isinstance(affordance, Mapping):
            continue
        state = _status(affordance.get("status") or affordance.get("state") or payload.get("status"))
        if state in {"ok", "clear", "disabled", "done"}:
            continue
        family = _family(affordance.get("family") or affordance.get("action_family") or "security_action")
        items.append(
            OperatorReviewQueueItem(
                item_id=f"live-{family}-{_safe_id(affordance.get('id') or affordance.get('name') or family)}",
                family=family,
                status="pending_review" if state in {"ready", "needs_go", "pending"} else state,
                proposed_action=_proposed_action(family),
                why=_why(family, affordance.get("reason")),
                risk=_risk(family),
                required_gate=str(affordance.get("required_gate") or _required_gate(family)),
                safe_default="preview_only",
                next_action="request_bounded_live_go",
                source_ref=str(affordance.get("source_ref") or affordance.get("id") or ""),
                priority=_priority(state, family),
            )
        )
    return tuple(items)


def _items_from_generic(
    values: Iterable[Mapping[str, Any]],
    *,
    family: str,
    required_gate: str,
) -> tuple[OperatorReviewQueueItem, ...]:
    items: list[OperatorReviewQueueItem] = []
    for value in values or ():
        if not isinstance(value, Mapping):
            continue
        status = _status(value.get("status") or "pending_review")
        if status in {"done", "clear", "ok"}:
            continue
        items.append(
            OperatorReviewQueueItem(
                item_id=f"{family}-{_safe_id(value.get('id') or value.get('source_ref') or len(items) + 1)}",
                family=family,
                status=status,
                proposed_action=_safe_phrase(value.get("proposed_action") or _proposed_action(family)),
                why=_safe_phrase(value.get("why") or _why(family, value.get("reason"))),
                risk=_safe_phrase(value.get("risk") or _risk(family)),
                required_gate=required_gate,
                safe_default=_safe_phrase(value.get("safe_default") or "hold_for_operator_review"),
                next_action=_next_action(status),
                source_ref=str(value.get("source_ref") or value.get("id") or ""),
                priority=_priority(status, family),
            )
        )
    return tuple(items)


def _gate_family(gate: Mapping[str, Any]) -> str:
    gate_id = _safe_id(gate.get("id") or "")
    family = _family(gate.get("family") or gate_id)
    if gate_id == "nextcloud_copy":
        return "nextcloud_copy"
    if gate_id in SUPPORTED_REVIEW_FAMILIES:
        return gate_id
    if family == "nextcloud":
        return "nextcloud_copy"
    if family == "memory":
        return "memory_write"
    if family == "raptorgraph":
        return "raptorgraph_write"
    if family == "export":
        return "file_export"
    if family == "telegram":
        return "telegram_delivery"
    if family == "coding":
        return "coding_approval"
    return family if family in SUPPORTED_REVIEW_FAMILIES else "security_action"


def _proposed_action(family: str) -> str:
    return {
        "nextcloud_copy": "copy_nextcloud_item_after_review",
        "memory_write": "write_approved_memory_records",
        "raptorgraph_write": "write_approved_graph_events",
        "file_export": "export_file_after_review",
        "security_action": "perform_security_action_after_go",
        "telegram_delivery": "deliver_telegram_message_after_go",
        "coding_approval": "publish_or_merge_after_review",
    }.get(_family(family), "review_operator_action")


def _why(family: str, reason: Any) -> str:
    safe_reason = _safe_id(reason or "")
    if safe_reason:
        return f"{_family(family)} requires {safe_reason}"
    return f"{_family(family)} requires operator review"


def _risk(family: str) -> str:
    return {
        "nextcloud_copy": "unapproved_live_file_mutation",
        "memory_write": "private_data_enters_long_term_memory",
        "raptorgraph_write": "unapproved_graph_mutation",
        "file_export": "unapproved_file_delivery_or_persistence",
        "security_action": "unapproved_security_remediation",
        "telegram_delivery": "unapproved_external_message_delivery",
        "coding_approval": "unreviewed_code_publication",
    }.get(_family(family), "unapproved_operator_action")


def _required_gate(family: str) -> str:
    return {
        "nextcloud_copy": "UIX-NEXTCLOUD-LIVE-WRITE",
        "memory_write": "UIX-MEMORY-WRITE-GO",
        "raptorgraph_write": "UIX-MEMORY-WRITE-GO",
        "file_export": "EXPORT-LIVE-GO",
        "security_action": "OPS-REMEDIATION-GO",
        "telegram_delivery": "TGR-LIVE-SEND-GO",
        "coding_approval": "CODING-PUBLISH-GO",
    }.get(_family(family), "OPERATOR-GO")


def _next_action(status: str) -> str:
    token = _status(status)
    if token == "blocked":
        return "fix_blocker"
    if token in {"ready_to_execute", "ready_to_write"}:
        return "request_bounded_live_go"
    return "operator_review"


def _priority(status: str, family: str) -> int:
    base = 10 if _status(status) == "blocked" else 30
    family_offset = {
        "security_action": 0,
        "coding_approval": 5,
        "nextcloud_copy": 10,
        "memory_write": 12,
        "raptorgraph_write": 14,
        "file_export": 20,
        "telegram_delivery": 25,
    }.get(_family(family), 40)
    return base + family_offset


def _mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _status(value: Any) -> str:
    token = _safe_id(value or "pending_review")
    if token in {"needs_review", "review", "pending"}:
        return "pending_review"
    if token in {"ready", "ready_to_send"}:
        return "ready_to_execute"
    if token in {"ok", "clear", "completed", "sent", "exported"}:
        return "done"
    return token or "pending_review"


def _family(value: Any) -> str:
    token = _safe_id(value or "security_action")
    return token if token in SUPPORTED_REVIEW_FAMILIES else token


def _safe_id(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = _SAFE_TOKEN_RE.sub("_", text).strip("_")
    return text[:80] or "unknown"


def _safe_phrase(value: Any) -> str:
    if _sensitive_value(value):
        return "redacted_operator_review_detail"
    return _safe_id(value)


def _sensitive_value(value: Any) -> bool:
    text = str(value or "").lower()
    return any(part in text for part in _SENSITIVE_KEY_PARTS)


def _hash(value: Any) -> str:
    return hashlib.sha256(repr(value).encode("utf-8", errors="replace")).hexdigest()[:24]
