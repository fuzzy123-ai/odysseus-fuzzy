"""Closed, content-free postconditions for default Todo digest membership."""

from __future__ import annotations

from hashlib import sha256
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping


TODO_DIGEST_RECEIPT_SCHEMA = "odysseus.todo_digest_membership_receipt.v1"
TODO_DIGEST_RECEIPT_FIELD = "todo_digest_receipt"
_ACTIONS = frozenset({"add", "complete", "reopen", "remove"})
_REF_RE = re.compile(r"^(?:owner|list|item):[a-f0-9]{16}$|^operation:(?:add|complete|reopen|remove)$")
_HASH_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_COUNT = 1_000_000
_MAX_LIMIT = 100


def redact_ref(kind: str, value: Any) -> str | None:
    """Return the established 16-hex redaction without retaining the input."""
    if kind not in {"owner", "list", "item"} or not isinstance(value, str) or not value:
        return None
    return f"{kind}:{sha256(value.encode('utf-8')).hexdigest()[:16]}"


def build_todo_digest_membership_receipt(
    *,
    action: Any,
    evidence_refs: Any,
    current_state: Any,
    included: Any,
    selection_position: Any,
    open_item_count: Any,
    selected_open_item_count: Any,
    limit: Any,
    label_filter_active: Any,
    list_filter_active: Any,
    builder_date: Any,
    snapshot_manifest: Any,
) -> dict[str, Any] | None:
    """Build one receipt from a content-free selection projection.

    Database access and raw Todo text/identifiers deliberately live outside this
    module.  The manifest is canonicalised here only after it has been reduced
    to redacted refs, positions, booleans, and ordering metadata.
    """
    refs = _refs(evidence_refs, action)
    state = _state(current_state)
    if refs is None or state is None or not _counts(open_item_count, selected_open_item_count, limit):
        return None
    if type(included) is not bool or label_filter_active is not False or list_filter_active is not False:
        return None
    if not _valid_date(builder_date):
        return None
    position = _position(selection_position, limit, included)
    if position is _INVALID:
        return None
    expected = _expected(action, state, included)
    if expected is None:
        return None
    digest = _snapshot_hash(
        snapshot_manifest, refs=refs, action=action, state=state, included=included,
        selection_position=position, open_item_count=open_item_count,
        selected_open_item_count=selected_open_item_count, limit=limit,
        builder_date=builder_date, label_filter_active=label_filter_active,
        list_filter_active=list_filter_active,
    )
    if digest is None:
        return None
    receipt = {
        "schema": TODO_DIGEST_RECEIPT_SCHEMA,
        "claim_type": "todo_digest_contains" if included else "todo_digest_excludes",
        "action": str(action),
        "transaction_status": "projected",
        "verified": True,
        "evidence_refs": refs,
        "current_state": {"exists": state[0], "done": state[1]},
        "included": included,
        "selection_position": position,
        "open_item_count": open_item_count,
        "selected_open_item_count": selected_open_item_count,
        "limit": limit,
        "label_filter_active": label_filter_active,
        "list_filter_active": list_filter_active,
        "builder_date": builder_date,
        "builder_clock": "naive_local",
        "snapshot_hash": digest,
        "raw_content_visible": False,
    }
    receipt_ref = _receipt_ref(receipt)
    if receipt_ref is None:
        return None
    receipt["receipt_ref"] = receipt_ref
    return receipt


def validate_todo_digest_receipt(receipt: Any, *, semantic_receipt: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """Strictly validate the closed digest receipt and optional mutation bind."""
    try:
        return _validate_todo_digest_receipt_strict(receipt, semantic_receipt=semantic_receipt)
    except Exception:
        return None


def _validate_todo_digest_receipt_strict(receipt: Any, *, semantic_receipt: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    if not isinstance(receipt, Mapping):
        return None
    expected_keys = {
        "schema", "claim_type", "action", "transaction_status", "verified", "evidence_refs",
        "current_state", "included", "selection_position", "open_item_count", "selected_open_item_count",
        "limit", "label_filter_active", "list_filter_active", "builder_date", "builder_clock", "snapshot_hash",
        "raw_content_visible", "receipt_ref",
    }
    if set(receipt) != expected_keys:
        return None
    action = receipt.get("action")
    refs = _refs(receipt.get("evidence_refs"), action)
    state = _state(receipt.get("current_state"))
    included = receipt.get("included")
    if refs is None or state is None or type(included) is not bool:
        return None
    if receipt.get("schema") != TODO_DIGEST_RECEIPT_SCHEMA or receipt.get("transaction_status") != "projected" or receipt.get("verified") is not True:
        return None
    if receipt.get("claim_type") != ("todo_digest_contains" if included else "todo_digest_excludes"):
        return None
    if not _counts(receipt.get("open_item_count"), receipt.get("selected_open_item_count"), receipt.get("limit")):
        return None
    if _position(receipt.get("selection_position"), receipt["limit"], included) is _INVALID:
        return None
    if receipt.get("label_filter_active") is not False or receipt.get("list_filter_active") is not False:
        return None
    if not _valid_date(receipt.get("builder_date")):
        return None
    if receipt.get("builder_clock") != "naive_local" or not isinstance(receipt.get("snapshot_hash"), str) or not _HASH_RE.fullmatch(receipt["snapshot_hash"]):
        return None
    expected_ref = _receipt_ref({key: receipt[key] for key in expected_keys if key != "receipt_ref"})
    if receipt.get("raw_content_visible") is not False or expected_ref is None or receipt.get("receipt_ref") != expected_ref:
        return None
    if _expected(action, state, included) is None:
        return None
    if semantic_receipt is not None and not _binds_semantic(receipt, semantic_receipt):
        return None
    return {key: receipt[key] for key in expected_keys}


def validated_todo_digest_receipt_from_event(event: Any) -> dict[str, Any] | None:
    """Accept a digest postcondition only inside a valid mutation event."""
    try:
        if not isinstance(event, Mapping) or event.get("tool") != "manage_todos":
            return None
        from src.todo_transaction_receipts import validated_todo_semantic_receipt_from_event
        semantic = validated_todo_semantic_receipt_from_event(event)
        if semantic is None:
            return None
        return validate_todo_digest_receipt(event.get(TODO_DIGEST_RECEIPT_FIELD), semantic_receipt=semantic)
    except Exception:
        return None


def digest_receipts_from_tool_events(events: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(events, (tuple, list)):
        return ()
    try:
        receipts = []
        for event in events[:64]:
            try:
                receipt = validated_todo_digest_receipt_from_event(event)
            except Exception:
                return ()
            if receipt is not None:
                receipts.append(receipt)
        return tuple(receipts)
    except Exception:
        return ()


def _refs(value: Any, action: Any) -> tuple[str, ...] | None:
    if action not in _ACTIONS or not isinstance(value, (tuple, list)):
        return None
    refs = tuple(value)
    if len(refs) != 4 or len(set(refs)) != 4 or any(not isinstance(ref, str) or not _REF_RE.fullmatch(ref) for ref in refs):
        return None
    if not refs[0].startswith("owner:") or not refs[1].startswith("list:") or not refs[2].startswith("item:") or refs[3] != f"operation:{action}":
        return None
    return refs


def _state(value: Any) -> tuple[bool, bool | None] | None:
    if not isinstance(value, Mapping) or set(value) != {"exists", "done"} or type(value.get("exists")) is not bool:
        return None
    done = value.get("done")
    if value["exists"]:
        return (True, done) if type(done) is bool else None
    return (False, None) if done is None else None


def _counts(open_count: Any, selected_count: Any, limit: Any) -> bool:
    return all(type(value) is int and 0 <= value <= _MAX_COUNT for value in (open_count, selected_count)) and type(limit) is int and 1 <= limit <= _MAX_LIMIT and selected_count <= min(open_count, limit)


def _position(value: Any, limit: Any, included: bool) -> int | None | object:
    if included:
        return value if type(value) is int and 0 <= value < limit else _INVALID
    return None if value is None else _INVALID


def _expected(action: Any, state: tuple[bool, bool | None], included: bool) -> bool | None:
    if action in {"add", "reopen"}:
        return True if state == (True, False) and included else None
    if action == "complete":
        return True if state == (True, True) and not included else None
    if action == "remove":
        return True if state == (False, None) and not included else None
    return None


def _snapshot_hash(manifest: Any, *, refs: tuple[str, ...], action: Any, state: tuple[bool, bool | None], included: bool, selection_position: int | None, open_item_count: int, selected_open_item_count: int, limit: int, builder_date: str, label_filter_active: bool, list_filter_active: bool) -> str | None:
    if not isinstance(manifest, Mapping) or set(manifest) != {
        "schema", "builder_date", "builder_clock", "limit", "label_filter_active", "list_filter_active", "selected",
    }:
        return None
    if manifest.get("schema") != "odysseus.todo_digest_snapshot.v1" or manifest.get("builder_clock") != "naive_local":
        return None
    if not _valid_date(manifest.get("builder_date")) or manifest["builder_date"] != builder_date:
        return None
    if type(manifest.get("limit")) is not int or not 1 <= manifest["limit"] <= _MAX_LIMIT or manifest["limit"] != limit:
        return None
    if manifest.get("label_filter_active") is not label_filter_active or manifest.get("list_filter_active") is not list_filter_active:
        return None
    selected = manifest.get("selected")
    if not isinstance(selected, list) or len(selected) != selected_open_item_count or len(selected) > manifest["limit"]:
        return None
    seen: set[tuple[str, str]] = set()
    for position, item in enumerate(selected):
        if not isinstance(item, Mapping) or set(item) != {"list_ref", "item_ref", "position", "done"}:
            return None
        pair = (item.get("list_ref"), item.get("item_ref"))
        if not isinstance(pair[0], str) or not isinstance(pair[1], str) or not pair[0].startswith("list:") or not pair[1].startswith("item:") or not _REF_RE.fullmatch(pair[0]) or not _REF_RE.fullmatch(pair[1]):
            return None
        if pair in seen or item.get("position") != position or item.get("done") is not False:
            return None
        seen.add(pair)
    target = (refs[1], refs[2])
    if included:
        if selection_position is None or selection_position >= len(selected) or (selected[selection_position]["list_ref"], selected[selection_position]["item_ref"]) != target:
            return None
    elif target in seen:
        return None
    proof = {
        "manifest": manifest, "action": action, "evidence_refs": refs,
        "current_state": {"exists": state[0], "done": state[1]}, "included": included,
        "selection_position": selection_position, "open_item_count": open_item_count,
        "selected_open_item_count": selected_open_item_count, "limit": limit,
    }
    try:
        canonical = json.dumps(proof, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        return None
    return f"sha256:{sha256(canonical.encode('utf-8')).hexdigest()}"


def _binds_semantic(receipt: Mapping[str, Any], semantic: Mapping[str, Any]) -> bool:
    required = {"action", "operation", "verified", "evidence_refs"}
    if not required.issubset(semantic) or semantic.get("action") != receipt["action"] or semantic.get("operation") != receipt["action"] or semantic.get("verified") is not True:
        return False
    semantic_state = semantic.get("current_state")
    action = receipt["action"]
    expected = {"add": False, "reopen": False, "complete": True, "remove": None}[action]
    if semantic_state is not expected or tuple(semantic.get("evidence_refs", ())) != tuple(receipt["evidence_refs"]):
        return False
    return receipt["current_state"] == {"exists": action != "remove", "done": expected}


def _valid_date(value: Any) -> bool:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _receipt_ref(receipt: Mapping[str, Any]) -> str | None:
    try:
        canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        return f"sha256:{sha256(canonical.encode('utf-8')).hexdigest()}"
    except Exception:
        return None


_INVALID = object()


# Compatibility contract for the earlier Telegram truth-envelope format.  The
# newer closed membership receipt above remains the authoritative mutation
# receipt; these types only parse and carry the already-redacted projection and
# schedule receipts still consumed by the Telegram bridge.
_TELEGRAM_DIGEST_RECEIPT_SCHEMA = "odysseus.todo_digest_receipt.v1"
_TELEGRAM_CLAIM_TYPES = frozenset(
    {
        "todo_digest_contains",
        "todo_digest_excludes",
        "todo_digest_schedule_active",
    }
)
_LIST_REF_RE = re.compile(r"^todo-list:v1:[A-Fa-f0-9]{16}:.{1,1024}$")
_ITEM_REF_RE = re.compile(r"^todo-item:v1:[A-Za-z0-9_-]{8,128}$")
_PROJECTION_REF_RE = re.compile(r"^todo-digest-projection:v1:[a-f0-9]{32}$")
_SCHEDULE_REF_RE = re.compile(r"^todo-digest-schedule:v1:[a-f0-9]{12}$")
_EVIDENCE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,180}$")


class TodoDigestReceiptError(ValueError):
    """Raised when a Telegram digest receipt is malformed."""


@dataclass(frozen=True, slots=True)
class TodoDigestReceipt:
    claim_type: str
    verified: bool
    transaction_status: str
    evidence_refs: tuple[str, ...]
    list_ref: str | None = None
    item_ref: str | None = None
    included: bool | None = None
    current_state: dict[str, bool | None] | None = None
    projection_ref: str | None = None
    schedule_ref: str | None = None
    next_run: str | None = None
    schema: str = _TELEGRAM_DIGEST_RECEIPT_SCHEMA

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "TodoDigestReceipt":
        if payload.get("schema") not in (None, _TELEGRAM_DIGEST_RECEIPT_SCHEMA):
            raise TodoDigestReceiptError("unsupported Todo digest receipt schema")
        claim_type = str(payload.get("claim_type") or "").strip()
        if claim_type not in _TELEGRAM_CLAIM_TYPES:
            raise TodoDigestReceiptError("unknown Todo digest claim type")
        status = str(
            payload.get("transaction_status") or payload.get("status") or ""
        ).strip().lower()
        refs = _telegram_evidence_refs(payload.get("evidence_refs") or ())
        requested_verified = payload.get("verified") is True

        if claim_type == "todo_digest_schedule_active":
            schedule_ref = _telegram_optional_ref(
                payload.get("schedule_ref"), _SCHEDULE_REF_RE
            )
            next_run = _telegram_next_run(payload.get("next_run"))
            verified = bool(
                requested_verified
                and status == "active"
                and schedule_ref
                and next_run
                and any(
                    ref.startswith("scheduled-task-readback:v1:") for ref in refs
                )
            )
            return cls(
                claim_type=claim_type,
                verified=verified,
                transaction_status=status,
                evidence_refs=refs,
                schedule_ref=schedule_ref,
                next_run=next_run,
            )

        list_ref = str(payload.get("list_ref") or "").strip()
        item_ref = str(payload.get("item_ref") or "").strip()
        if not _LIST_REF_RE.fullmatch(list_ref) or not _ITEM_REF_RE.fullmatch(item_ref):
            raise TodoDigestReceiptError("invalid Todo digest item reference")
        projection_ref = _telegram_optional_ref(
            payload.get("projection_ref"), _PROJECTION_REF_RE
        )
        included = payload.get("included")
        if included is not True and included is not False:
            raise TodoDigestReceiptError("included must be boolean")
        current_state = _telegram_state(payload.get("current_state"))
        semantic_match = (
            included is True and current_state == {"exists": True, "done": False}
            if claim_type == "todo_digest_contains"
            else included is False
            and current_state
            in ({"exists": True, "done": True}, {"exists": False, "done": None})
        )
        verified = bool(
            requested_verified
            and status == "projected"
            and projection_ref
            and any(ref.startswith("notes-digest-readback:v1:") for ref in refs)
            and semantic_match
        )
        return cls(
            claim_type=claim_type,
            verified=verified,
            transaction_status=status,
            evidence_refs=refs,
            list_ref=list_ref,
            item_ref=item_ref,
            included=included,
            current_state=current_state,
            projection_ref=projection_ref,
        )

    @property
    def receipt_ref(self) -> str:
        payload = json.dumps(
            {
                "schema": self.schema,
                "claim_type": self.claim_type,
                "verified": self.verified,
                "transaction_status": self.transaction_status,
                "evidence_refs": self.evidence_refs,
                "list_ref": self.list_ref,
                "item_ref": self.item_ref,
                "included": self.included,
                "current_state": self.current_state,
                "projection_ref": self.projection_ref,
                "schedule_ref": self.schedule_ref,
                "next_run": self.next_run,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = sha256(payload.encode("utf-8")).hexdigest()[:32]
        return f"todo-digest-receipt:v1:{digest}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "claim_type": self.claim_type,
            "verified": self.verified,
            "transaction_status": self.transaction_status,
            "evidence_refs": list(self.evidence_refs),
            "list_ref": self.list_ref,
            "item_ref": self.item_ref,
            "included": self.included,
            "current_state": (
                dict(self.current_state) if self.current_state is not None else None
            ),
            "projection_ref": self.projection_ref,
            "schedule_ref": self.schedule_ref,
            "next_run": self.next_run,
            "receipt_ref": self.receipt_ref,
            "raw_content_visible": False,
        }


def todo_digest_receipts_from_postconditions(
    projection: Mapping[str, Any] | None,
    schedule: Mapping[str, Any] | None,
) -> tuple[TodoDigestReceipt, ...]:
    payloads: list[Mapping[str, Any]] = []
    if isinstance(projection, Mapping):
        payloads.append({**projection, "schema": _TELEGRAM_DIGEST_RECEIPT_SCHEMA})
    if isinstance(schedule, Mapping):
        payloads.append(
            {
                "schema": _TELEGRAM_DIGEST_RECEIPT_SCHEMA,
                "claim_type": schedule.get("claim_type"),
                "transaction_status": schedule.get("status"),
                "schedule_ref": schedule.get("schedule_ref"),
                "next_run": schedule.get("next_run"),
                "verified": schedule.get("verified"),
                "evidence_refs": schedule.get("evidence_refs") or (),
            }
        )
    return _telegram_receipts_from_payloads(payloads)


def todo_digest_receipts_from_tool_result(
    result: Mapping[str, Any],
) -> tuple[TodoDigestReceipt, ...]:
    return _telegram_receipts_from_payloads(
        result.get("todo_digest_receipts") or ()
    )


def todo_digest_receipts_from_tool_events(
    tool_events: Iterable[Mapping[str, Any]],
) -> tuple[TodoDigestReceipt, ...]:
    receipts: list[TodoDigestReceipt] = []
    seen: set[str] = set()
    for event in tool_events:
        if not isinstance(event, Mapping) or event.get("tool") != "manage_todos":
            continue
        for receipt in _telegram_receipts_from_payloads(
            event.get("todo_digest_receipts") or ()
        ):
            if event.get("exit_code") not in (0, "0", None) and receipt.verified:
                payload = receipt.to_dict()
                payload["verified"] = False
                receipt = TodoDigestReceipt.from_mapping(payload)
            if receipt.receipt_ref not in seen:
                seen.add(receipt.receipt_ref)
                receipts.append(receipt)
    return tuple(receipts)


def todo_digest_evidence_for_claim(
    receipts: Iterable[TodoDigestReceipt], claim_type: str
) -> tuple[str, ...]:
    refs: list[str] = []
    for receipt in receipts:
        if receipt.claim_type != claim_type or not receipt.verified:
            continue
        refs.extend((receipt.receipt_ref, *receipt.evidence_refs))
        if receipt.projection_ref:
            refs.append(receipt.projection_ref)
        if receipt.schedule_ref:
            refs.append(receipt.schedule_ref)
    return tuple(dict.fromkeys(refs))


def _telegram_receipts_from_payloads(payloads: Any) -> tuple[TodoDigestReceipt, ...]:
    if not isinstance(payloads, (list, tuple)):
        return ()
    receipts: list[TodoDigestReceipt] = []
    for payload in payloads:
        if not isinstance(payload, Mapping):
            continue
        try:
            receipts.append(TodoDigestReceipt.from_mapping(payload))
        except TodoDigestReceiptError:
            continue
    return tuple(receipts)


def _telegram_optional_ref(value: Any, pattern: re.Pattern[str]) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not pattern.fullmatch(text):
        raise TodoDigestReceiptError("invalid Todo digest evidence reference")
    return text


def _telegram_state(value: Any) -> dict[str, bool | None]:
    if not isinstance(value, Mapping) or set(value) != {"exists", "done"}:
        raise TodoDigestReceiptError("invalid Todo digest item state")
    exists = value.get("exists")
    done = value.get("done")
    if type(exists) is not bool or done not in (True, False, None):
        raise TodoDigestReceiptError("invalid Todo digest item state")
    if exists is False and done is not None:
        raise TodoDigestReceiptError("removed Todo cannot retain done state")
    return {"exists": exists, "done": done}


def _telegram_next_run(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TodoDigestReceiptError("invalid next_run") from exc
    return text


def _telegram_evidence_refs(values: Iterable[Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or not _EVIDENCE_REF_RE.fullmatch(text):
            raise TodoDigestReceiptError("unsafe Todo digest evidence reference")
        refs.append(text)
    return tuple(dict.fromkeys(refs))
