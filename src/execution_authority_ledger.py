"""Pure, typed, replay-derived execution authority ledger (R3)."""
from __future__ import annotations

from datetime import datetime as _datetime
import hashlib as _hashlib
import json as _json_module
import math as _math
import re as _re
import unicodedata as _unicodedata
from typing import Any as _Any

from src.definition_authority_snapshot import (
    validate_definition_authority_snapshot_reference as _validate_definition_reference,
)
from src.execution_trust_authority import (
    execution_trust_authority_reference as _execution_trust_reference,
    resolve_execution_capability_refs as _resolve_capabilities,
    resolve_execution_gate_ids as _resolve_gates,
    resolve_execution_independent_reviewer as _resolve_reviewer,
    resolve_execution_principal as _resolve_principal,
    resolve_execution_verification_policy as _resolve_policy,
)


EXECUTION_AUTHORITY_LEDGER_SCHEMA_ID = "odysseus.execution_authority_ledger.v3"
EXECUTION_AUTHORITY_EVENT_SCHEMA_ID = "odysseus.execution_authority_event.v3"
EXECUTION_AUTHORITY_LEDGER_REF_SCHEMA_ID = "odysseus.execution_authority_ledger_ref.v3"
EXECUTION_AUTHORITY_ACTIVE_CLAIM_SCHEMA_ID = "odysseus.execution_authority_active_claim.v3"
EXECUTION_AUTHORITY_ACCEPTED_RELEASE_SCHEMA_ID = "odysseus.execution_authority_accepted_release.v3"
EXECUTION_AUTHORITY_PROJECTION_SCHEMA_ID = "odysseus.execution_authority_projection.v3"

MAX_LEDGER_BYTES = 4_194_304
MAX_LEDGER_EVENTS = 1_024
MAX_LEDGER_DEPTH = 10
MAX_LEDGER_NODES = 262_144
MAX_EVENT_PAYLOAD_BYTES = 16_384
MAX_ACTIVE_CLAIMS = 32
MAX_CLAIMS_TOTAL = 512
MAX_ACCEPTED_RELEASES = 341
MAX_RELEASED_CLAIMS_TOTAL = 512
MAX_PATHS_PER_CLAIM = 64
MAX_ACTIVE_SCOPE_PATHS = 512
MAX_REFS_PER_CLAIM_PER_KIND = 32
MAX_ACTIVE_CAPABILITY_REFS = 256
MAX_ACTIVE_GATE_REFS = 256
MAX_ACTIVE_VERIFICATION_REFS = 256
MAX_EVIDENCE_RECORDS = 1_023
MAX_GATE_DECISIONS = 1_023
MAX_COMMAND_IDS = 1_024
MAX_IDENTIFIER_CHARS = 192
MAX_JSON_INTEGER_TOKEN_DIGITS = 128

_HASH_RE = _re.compile(r"^sha256:[0-9a-f]{64}$")
_ID_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+:-]{0,191}$")
_TIMESTAMP_RE = _re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)

_GENESIS_DOMAIN = "odysseus.execution_authority.genesis.v3"
_GENESIS_HEAD_DOMAIN = "odysseus.execution_authority.genesis_head.v3"
_COMMAND_DOMAIN = "odysseus.execution_authority.command.v3"
_EVENT_DOMAIN = "odysseus.execution_authority.event.v3"
_LEDGER_DOMAIN = "odysseus.execution_authority.ledger.v3"
_EVIDENCE_SET_DOMAIN = "odysseus.execution_authority.evidence_set.v3"
_INDEPENDENT_REVIEW_DOMAIN = "odysseus.execution_authority.independent_review.v3"

_LEDGER_FIELDS = frozenset(
    {
        "schema_id", "ledger_id", "definition_snapshot_ref",
        "execution_trust_authority_ref", "genesis_hash", "events", "cursor",
        "head_hash", "ledger_hash",
    }
)
_EVENT_FIELDS = frozenset(
    {
        "schema_id", "ledger_id", "genesis_hash", "sequence",
        "previous_event_hash", "command_id", "command_hash", "occurred_at",
        "event_type", "payload", "event_hash",
    }
)
_EVENT_TYPES = frozenset(
    {"claim_acquired", "lease_renewed", "gate_decided", "evidence_recorded", "claim_released"}
)
_ACQUIRE_FIELDS = frozenset(
    {
        "claim_id", "owner_principal_id", "write_paths", "read_only_paths",
        "lease_expires_at", "effect_capability_refs", "required_gate_ids",
        "required_verification_rule_ids", "live_actions",
    }
)
_LEASE_FIELDS = frozenset({"claim_id", "lease_expires_at"})
_GATE_FIELDS = frozenset({"claim_id", "gate_id", "decision", "decision_digest"})
_EVIDENCE_FIELDS = frozenset(
    {
        "claim_id", "evidence_id", "verification_rule_id", "result",
        "evidence_digest", "reviewer_principal_id",
    }
)
_RELEASE_ACCEPTED_FIELDS = frozenset({"claim_id", "outcome", "review_evidence_id"})
_RELEASE_ZERO_FIELDS = frozenset({"claim_id", "outcome"})

_ERROR_CODES = frozenset(
    {
        "active_scope_collision", "budget_exceeded", "claim_identity_reused",
        "claim_not_active", "claim_not_dispatch_ready", "duplicate_evidence_id",
        "foreign_event", "forked_ledger", "idempotency_conflict",
        "independent_review_missing", "invalid_document", "invalid_fields",
        "invalid_hash", "invalid_identifier", "invalid_integer",
        "invalid_lease_window", "invalid_literal", "invalid_repo_path",
        "invalid_timestamp", "invalid_type", "live_action_forbidden",
        "non_canonical_document", "non_monotonic_event", "operation_failed",
        "stale_authority", "stale_cursor", "stale_head",
        "unrequired_authority_ref", "wrong_schema",
    }
)


class ExecutionAuthorityLedgerError(ValueError):
    """A fresh, code-only R3 ledger boundary failure."""

    def __init__(self, code: str) -> None:
        checked = code if type(code) is str and code in _ERROR_CODES else "operation_failed"
        self.code = checked
        super().__init__(checked)


def _fail(code: str) -> None:
    raise ExecutionAuthorityLedgerError(code)


def _public(operation: _Any) -> _Any:
    try:
        return operation()
    except ExecutionAuthorityLedgerError as error:
        code = error.code
    except Exception:
        code = "operation_failed"
    failure = ExecutionAuthorityLedgerError(code)
    failure.__cause__ = None
    failure.__context__ = None
    raise failure from None


def _canonical_bytes(value: _Any) -> bytes:
    try:
        return _json_module.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except Exception:
        _fail("invalid_document")


def _framed_hash(domain: str, payload: _Any) -> str:
    encoded = _canonical_bytes(payload)
    frame = domain.encode("ascii") + b"\0" + len(encoded).to_bytes(8, "big") + encoded
    return "sha256:" + _hashlib.sha256(frame).hexdigest()


def _capture(value: _Any, *, depth: int = 0, state: dict[str, int] | None = None) -> _Any:
    if state is None:
        state = {"nodes": 0}
    state["nodes"] += 1
    if state["nodes"] > MAX_LEDGER_NODES or depth > MAX_LEDGER_DEPTH:
        _fail("budget_exceeded")
    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        if len(value.encode("utf-8")) > MAX_LEDGER_BYTES:
            _fail("budget_exceeded")
        return value
    if type(value) is int:
        if len(str(abs(value))) > MAX_JSON_INTEGER_TOKEN_DIGITS:
            _fail("budget_exceeded")
        return value
    if type(value) is float:
        if not _math.isfinite(value):
            _fail("invalid_document")
        return value
    if type(value) is dict:
        result: dict[str, _Any] = {}
        for key, item in dict.items(value):
            if type(key) is not str:
                _fail("invalid_type")
            result[key] = _capture(item, depth=depth + 1, state=state)
        return result
    if type(value) is list:
        return [
            _capture(list.__getitem__(value, index), depth=depth + 1, state=state)
            for index in range(list.__len__(value))
        ]
    if type(value) is tuple:
        return [
            _capture(tuple.__getitem__(value, index), depth=depth + 1, state=state)
            for index in range(tuple.__len__(value))
        ]
    _fail("invalid_type")


class _DuplicateKey(ValueError):
    pass


def _load_document(value: _Any) -> dict[str, _Any]:
    if type(value) is not bytes:
        _fail("invalid_type")
    if not value:
        _fail("invalid_document")
    if len(value) > MAX_LEDGER_BYTES:
        _fail("budget_exceeded")

    def pairs_hook(pairs: list[tuple[str, _Any]]) -> dict[str, _Any]:
        result: dict[str, _Any] = {}
        for key, item in pairs:
            if key in result:
                raise _DuplicateKey
            result[key] = item
        return result

    def parse_int(token: str) -> int:
        if len(token.lstrip("-")) > MAX_JSON_INTEGER_TOKEN_DIGITS:
            raise ValueError
        return int(token)

    def parse_float(token: str) -> float:
        result = float(token)
        if not _math.isfinite(result):
            raise ValueError
        return result

    try:
        parsed = _json_module.loads(
            value.decode("utf-8"),
            object_pairs_hook=pairs_hook,
            parse_int=parse_int,
            parse_float=parse_float,
            parse_constant=lambda _token: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, _json_module.JSONDecodeError, ValueError, RecursionError):
        _fail("invalid_document")
    if type(parsed) is not dict:
        _fail("invalid_document")
    captured = _capture(parsed)
    if _canonical_bytes(captured) != value:
        _fail("non_canonical_document")
    return captured


def _exact_fields(value: _Any, expected: frozenset[str]) -> dict[str, _Any]:
    if type(value) is not dict:
        _fail("invalid_type")
    if set(value) != expected:
        _fail("invalid_fields")
    return value


def _identifier(value: _Any) -> str:
    if (
        type(value) is not str
        or len(value) > MAX_IDENTIFIER_CHARS
        or value != _unicodedata.normalize("NFC", value)
        or _ID_RE.fullmatch(value) is None
    ):
        _fail("invalid_identifier")
    return value


def _hash(value: _Any) -> str:
    if type(value) is not str or _HASH_RE.fullmatch(value) is None:
        _fail("invalid_hash")
    return value


def _integer(value: _Any, *, minimum: int = 0, maximum: int = 2_147_483_647) -> int:
    if type(value) is not int or value < minimum or value > maximum:
        _fail("invalid_integer")
    return value


def _timestamp(value: _Any) -> str:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail("invalid_timestamp")
    try:
        parsed = _datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("invalid_timestamp")
    if parsed.utcoffset() is None:
        _fail("invalid_timestamp")
    return value


def _time(value: str) -> _datetime:
    return _datetime.fromisoformat(value.replace("Z", "+00:00"))


def _sequence(value: _Any) -> list[_Any]:
    if type(value) is list:
        return [list.__getitem__(value, index) for index in range(list.__len__(value))]
    if type(value) is tuple:
        return [tuple.__getitem__(value, index) for index in range(tuple.__len__(value))]
    _fail("invalid_type")


def _identifiers(value: _Any, *, nonempty: bool = False) -> list[str]:
    items = [_identifier(item) for item in _sequence(value)]
    if nonempty and not items:
        _fail("invalid_fields")
    if items != sorted(items) or len(items) != len(set(items)):
        _fail("invalid_identifier")
    return items


def _device_alias(segment: str) -> bool:
    base = segment.split(".", 1)[0].upper()
    if base in {"CON", "PRN", "AUX", "NUL", "CLOCK$"}:
        return True
    return bool(_re.fullmatch(r"(?:COM|LPT)(?:[1-9]|¹|²|³)", base))


def _repo_path(value: _Any) -> str:
    if type(value) is not str or value != _unicodedata.normalize("NFC", value):
        _fail("invalid_repo_path")
    if (
        not value
        or value.startswith("/")
        or value.startswith("//")
        or _re.match(r"^[A-Za-z]:", value)
        or "\\" in value
        or ":" in value
        or "\0" in value
        or "//" in value
    ):
        _fail("invalid_repo_path")
    parts = value.split("/")
    if len(parts) < 2:
        _fail("invalid_repo_path")
    for part in parts:
        if not part or part in {".", ".."} or part.endswith((".", " ")) or _device_alias(part):
            _fail("invalid_repo_path")
    return value


def _paths(value: _Any, *, nonempty: bool = False) -> list[str]:
    items = [_repo_path(item) for item in _sequence(value)]
    if nonempty and not items:
        _fail("invalid_repo_path")
    folded = [item.casefold() for item in items]
    if folded != sorted(folded) or len(folded) != len(set(folded)):
        _fail("invalid_repo_path")
    return items


def _scope_overlap(left: str, right: str) -> bool:
    a = left.casefold().split("/")
    b = right.casefold().split("/")
    common = min(len(a), len(b))
    return a[:common] == b[:common]


def _scope_collision(write_paths: list[str], read_only_paths: list[str]) -> bool:
    for index, left in enumerate(write_paths):
        for right in write_paths[index + 1 :]:
            if _scope_overlap(left, right):
                return True
        for right in read_only_paths:
            if _scope_overlap(left, right):
                return True
    return False


def _definition(value: _Any) -> dict[str, _Any]:
    if type(value) is not dict:
        _fail("invalid_type")
    captured = _capture(value)
    try:
        validated = _validate_definition_reference(captured)
    except Exception:
        _fail("stale_authority")
    return _capture(validated)


def _trust_call(call: _Any, *args: _Any, **kwargs: _Any) -> _Any:
    try:
        return call(*args, **kwargs)
    except Exception:
        _fail("stale_authority")


def _trust_reference(
    receipt: bytes,
    definition: dict[str, _Any],
    pins: dict[str, str],
    observed_at: str,
) -> dict[str, _Any]:
    result = _trust_call(
        _execution_trust_reference,
        receipt,
        expected_receipt_hash=pins["expected_trust_receipt_hash"],
        expected_authority_hash=pins["expected_trust_authority_hash"],
        expected_definition_snapshot_hash=definition["snapshot_hash"],
        observed_at=observed_at,
    )
    return _capture(result)


def _authority_inputs(
    definition_snapshot_ref: _Any,
    trust_receipt: _Any,
    expected_trust_receipt_hash: _Any,
    expected_trust_authority_hash: _Any,
    observed_at: _Any,
) -> tuple[dict[str, _Any], bytes, dict[str, str], str, dict[str, _Any]]:
    definition = _definition(definition_snapshot_ref)
    if type(trust_receipt) is not bytes:
        _fail("invalid_type")
    pins = {
        "expected_trust_receipt_hash": _hash(expected_trust_receipt_hash),
        "expected_trust_authority_hash": _hash(expected_trust_authority_hash),
    }
    observed = _timestamp(observed_at)
    trust_reference = _trust_reference(trust_receipt, definition, pins, observed)
    return definition, trust_receipt, pins, observed, trust_reference


def _genesis_hash(
    ledger_id: str,
    definition: dict[str, _Any],
    trust_reference: dict[str, _Any],
) -> str:
    return _framed_hash(
        _GENESIS_DOMAIN,
        {
            "schema_id": EXECUTION_AUTHORITY_LEDGER_SCHEMA_ID,
            "ledger_id": ledger_id,
            "definition_snapshot_ref": definition,
            "execution_trust_authority_ref": trust_reference,
        },
    )


def _genesis_head(genesis_hash: str) -> str:
    return _framed_hash(_GENESIS_HEAD_DOMAIN, {"genesis_hash": genesis_hash})


def _normalize_acquire(payload: _Any) -> dict[str, _Any]:
    value = _exact_fields(payload, _ACQUIRE_FIELDS)
    write_paths = _paths(value["write_paths"], nonempty=True)
    read_only_paths = _paths(value["read_only_paths"])
    if len(write_paths) + len(read_only_paths) > MAX_PATHS_PER_CLAIM:
        _fail("budget_exceeded")
    if _scope_collision(write_paths, read_only_paths):
        _fail("active_scope_collision")
    capabilities = _identifiers(value["effect_capability_refs"], nonempty=True)
    gates = _identifiers(value["required_gate_ids"])
    rules = _identifiers(value["required_verification_rule_ids"], nonempty=True)
    if any(len(items) > MAX_REFS_PER_CLAIM_PER_KIND for items in (capabilities, gates, rules)):
        _fail("budget_exceeded")
    if type(value["live_actions"]) is not bool:
        _fail("invalid_type")
    if value["live_actions"] is not False:
        _fail("live_action_forbidden")
    result = {
        "claim_id": _identifier(value["claim_id"]),
        "owner_principal_id": _identifier(value["owner_principal_id"]),
        "write_paths": write_paths,
        "read_only_paths": read_only_paths,
        "lease_expires_at": _timestamp(value["lease_expires_at"]),
        "effect_capability_refs": capabilities,
        "required_gate_ids": gates,
        "required_verification_rule_ids": rules,
        "live_actions": False,
    }
    return result


def _normalize_lease(payload: _Any) -> dict[str, _Any]:
    value = _exact_fields(payload, _LEASE_FIELDS)
    return {"claim_id": _identifier(value["claim_id"]), "lease_expires_at": _timestamp(value["lease_expires_at"])}


def _normalize_gate(payload: _Any) -> dict[str, _Any]:
    value = _exact_fields(payload, _GATE_FIELDS)
    decision = value["decision"]
    if type(decision) is not str or decision not in {"approved", "rejected"}:
        _fail("invalid_literal")
    return {
        "claim_id": _identifier(value["claim_id"]),
        "gate_id": _identifier(value["gate_id"]),
        "decision": decision,
        "decision_digest": _hash(value["decision_digest"]),
    }


def _normalize_evidence(payload: _Any) -> dict[str, _Any]:
    value = _exact_fields(payload, _EVIDENCE_FIELDS)
    result = value["result"]
    if type(result) is not str or result not in {"pass", "fail"}:
        _fail("invalid_literal")
    return {
        "claim_id": _identifier(value["claim_id"]),
        "evidence_id": _identifier(value["evidence_id"]),
        "verification_rule_id": _identifier(value["verification_rule_id"]),
        "result": result,
        "evidence_digest": _hash(value["evidence_digest"]),
        "reviewer_principal_id": _identifier(value["reviewer_principal_id"]),
    }


def _normalize_release(payload: _Any) -> dict[str, _Any]:
    if type(payload) is not dict:
        _fail("invalid_type")
    outcome = payload.get("outcome") if type(payload) is dict else None
    if type(outcome) is not str or outcome not in {"accepted", "blocked", "cancelled"}:
        _fail("invalid_literal")
    if outcome == "accepted":
        value = _exact_fields(payload, _RELEASE_ACCEPTED_FIELDS)
        return {
            "claim_id": _identifier(value["claim_id"]),
            "outcome": outcome,
            "review_evidence_id": _identifier(value["review_evidence_id"]),
        }
    value = _exact_fields(payload, _RELEASE_ZERO_FIELDS)
    return {"claim_id": _identifier(value["claim_id"]), "outcome": outcome}


def _normalize_payload(event_type: str, payload: _Any) -> dict[str, _Any]:
    if event_type == "claim_acquired":
        result = _normalize_acquire(payload)
    elif event_type == "lease_renewed":
        result = _normalize_lease(payload)
    elif event_type == "gate_decided":
        result = _normalize_gate(payload)
    elif event_type == "evidence_recorded":
        result = _normalize_evidence(payload)
    elif event_type == "claim_released":
        result = _normalize_release(payload)
    else:
        _fail("invalid_literal")
    if len(_canonical_bytes(result)) > MAX_EVENT_PAYLOAD_BYTES:
        _fail("budget_exceeded")
    return result


def _new_state() -> dict[str, _Any]:
    return {
        "active": {},
        "seen_claims": set(),
        "gates": {},
        "latest_evidence": {},
        "evidence_ids": set(),
        "evidence_by_id": {},
        "accepted_releases": [],
        "commands": {},
        "claims_total": 0,
        "released_total": 0,
        "evidence_total": 0,
        "gate_total": 0,
    }


def _active_claim(state: dict[str, _Any], claim_id: str) -> dict[str, _Any]:
    claim = state["active"].get(claim_id)
    if claim is None:
        _fail("claim_not_active")
    return claim


def _require_unexpired(claim: dict[str, _Any], occurred_at: str) -> None:
    if _time(occurred_at) >= _time(claim["lease_expires_at"]):
        _fail("invalid_lease_window")


def _check_active_aggregates(state: dict[str, _Any]) -> None:
    active = list(state["active"].values())
    if len(active) > MAX_ACTIVE_CLAIMS:
        _fail("budget_exceeded")
    if sum(len(item["write_paths"]) + len(item["read_only_paths"]) for item in active) > MAX_ACTIVE_SCOPE_PATHS:
        _fail("budget_exceeded")
    if sum(len(item["effect_capability_refs"]) for item in active) > MAX_ACTIVE_CAPABILITY_REFS:
        _fail("budget_exceeded")
    if sum(len(item["required_gate_ids"]) for item in active) > MAX_ACTIVE_GATE_REFS:
        _fail("budget_exceeded")
    if sum(len(item["required_verification_rule_ids"]) for item in active) > MAX_ACTIVE_VERIFICATION_REFS:
        _fail("budget_exceeded")


def _qualifying_records(state: dict[str, _Any], claim: dict[str, _Any]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    claim_id = claim["claim_id"]
    for rule_id in claim["required_verification_rule_ids"]:
        item = state["latest_evidence"].get((claim_id, rule_id))
        if item is None or item["payload"]["result"] != "pass":
            _fail("claim_not_dispatch_ready")
        payload = item["payload"]
        records.append(
            {
                "verification_rule_id": rule_id,
                "evidence_id": payload["evidence_id"],
                "evidence_digest": payload["evidence_digest"],
                "reviewer_principal_id": payload["reviewer_principal_id"],
                "event_hash": item["event_hash"],
            }
        )
    return sorted(records, key=lambda item: (item["verification_rule_id"], item["evidence_id"]))


def _gates_ready(state: dict[str, _Any], claim: dict[str, _Any]) -> bool:
    return all(
        state["gates"].get((claim["claim_id"], gate_id), {}).get("decision") == "approved"
        for gate_id in claim["required_gate_ids"]
    )


def _is_ready(state: dict[str, _Any], claim: dict[str, _Any], observed_at: str) -> bool:
    if _time(observed_at) >= _time(claim["lease_expires_at"]) or not _gates_ready(state, claim):
        return False
    try:
        _qualifying_records(state, claim)
    except ExecutionAuthorityLedgerError as error:
        if error.code == "claim_not_dispatch_ready":
            return False
        raise
    return True


def _resolve_claim_authority(
    payload: dict[str, _Any],
    *,
    occurred_at: str,
    receipt: bytes,
    definition: dict[str, _Any],
    pins: dict[str, str],
) -> None:
    common = {
        "expected_receipt_hash": pins["expected_trust_receipt_hash"],
        "expected_authority_hash": pins["expected_trust_authority_hash"],
        "expected_definition_snapshot_hash": definition["snapshot_hash"],
    }
    _trust_reference(receipt, definition, pins, occurred_at)
    _trust_call(
        _resolve_principal,
        receipt,
        payload["owner_principal_id"],
        required_role="claim_owner",
        observed_at=occurred_at,
        **common,
    )
    _trust_call(_resolve_capabilities, receipt, payload["effect_capability_refs"], observed_at=occurred_at, **common)
    _trust_call(_resolve_gates, receipt, payload["required_gate_ids"], observed_at=occurred_at, **common)
    for rule_id in payload["required_verification_rule_ids"]:
        _trust_call(_resolve_policy, receipt, rule_id, observed_at=occurred_at, **common)
    _trust_reference(receipt, definition, pins, payload["lease_expires_at"])
    _trust_call(
        _resolve_principal,
        receipt,
        payload["owner_principal_id"],
        required_role="claim_owner",
        observed_at=payload["lease_expires_at"],
        **common,
    )


def _apply_event(
    state: dict[str, _Any],
    event: dict[str, _Any],
    payload: dict[str, _Any],
    *,
    receipt: bytes,
    definition: dict[str, _Any],
    pins: dict[str, str],
) -> None:
    kind = event["event_type"]
    occurred_at = event["occurred_at"]
    _trust_reference(receipt, definition, pins, occurred_at)
    if kind == "claim_acquired":
        claim_id = payload["claim_id"]
        if claim_id in state["seen_claims"]:
            _fail("claim_identity_reused")
        if state["claims_total"] >= MAX_CLAIMS_TOTAL or len(state["active"]) >= MAX_ACTIVE_CLAIMS:
            _fail("budget_exceeded")
        if _time(payload["lease_expires_at"]) <= _time(occurred_at):
            _fail("invalid_lease_window")
        _resolve_claim_authority(
            payload,
            occurred_at=occurred_at,
            receipt=receipt,
            definition=definition,
            pins=pins,
        )
        for active in state["active"].values():
            if any(
                _scope_overlap(left, right)
                for left in payload["write_paths"]
                for right in active["write_paths"] + active["read_only_paths"]
            ) or any(
                _scope_overlap(left, right)
                for left in payload["read_only_paths"]
                for right in active["write_paths"]
            ):
                _fail("active_scope_collision")
        state["active"][claim_id] = {
            "schema_id": EXECUTION_AUTHORITY_ACTIVE_CLAIM_SCHEMA_ID,
            **payload,
            "acquired_at": occurred_at,
        }
        state["seen_claims"].add(claim_id)
        state["claims_total"] += 1
        _check_active_aggregates(state)
        return
    if kind == "lease_renewed":
        claim = _active_claim(state, payload["claim_id"])
        _require_unexpired(claim, occurred_at)
        if _time(payload["lease_expires_at"]) <= _time(claim["lease_expires_at"]):
            _fail("invalid_lease_window")
        _trust_reference(receipt, definition, pins, payload["lease_expires_at"])
        common = {
            "expected_receipt_hash": pins["expected_trust_receipt_hash"],
            "expected_authority_hash": pins["expected_trust_authority_hash"],
            "expected_definition_snapshot_hash": definition["snapshot_hash"],
            "observed_at": payload["lease_expires_at"],
        }
        _trust_call(_resolve_principal, receipt, claim["owner_principal_id"], required_role="claim_owner", **common)
        claim["lease_expires_at"] = payload["lease_expires_at"]
        return
    if kind == "gate_decided":
        claim = _active_claim(state, payload["claim_id"])
        _require_unexpired(claim, occurred_at)
        if payload["gate_id"] not in claim["required_gate_ids"]:
            _fail("unrequired_authority_ref")
        if state["gate_total"] >= MAX_GATE_DECISIONS:
            _fail("budget_exceeded")
        state["gates"][(claim["claim_id"], payload["gate_id"])] = payload
        state["gate_total"] += 1
        return
    if kind == "evidence_recorded":
        claim = _active_claim(state, payload["claim_id"])
        _require_unexpired(claim, occurred_at)
        if payload["verification_rule_id"] not in claim["required_verification_rule_ids"]:
            _fail("unrequired_authority_ref")
        if payload["evidence_id"] in state["evidence_ids"]:
            _fail("duplicate_evidence_id")
        if state["evidence_total"] >= MAX_EVIDENCE_RECORDS:
            _fail("budget_exceeded")
        common = {
            "expected_receipt_hash": pins["expected_trust_receipt_hash"],
            "expected_authority_hash": pins["expected_trust_authority_hash"],
            "expected_definition_snapshot_hash": definition["snapshot_hash"],
            "observed_at": occurred_at,
        }
        reviewer = _capture(
            _trust_call(
                _resolve_reviewer,
                receipt,
                owner_principal_id=claim["owner_principal_id"],
                reviewer_principal_id=payload["reviewer_principal_id"],
                verification_rule_id=payload["verification_rule_id"],
                **common,
            )
        )
        record = {
            "payload": payload,
            "event_hash": event["event_hash"],
            "occurred_at": occurred_at,
            "reviewer": reviewer,
        }
        state["evidence_ids"].add(payload["evidence_id"])
        state["evidence_by_id"][payload["evidence_id"]] = record
        state["latest_evidence"][(claim["claim_id"], payload["verification_rule_id"])] = record
        state["evidence_total"] += 1
        return
    claim = _active_claim(state, payload["claim_id"])
    if state["released_total"] >= MAX_RELEASED_CLAIMS_TOTAL:
        _fail("budget_exceeded")
    if payload["outcome"] == "accepted":
        _require_unexpired(claim, occurred_at)
        if not _gates_ready(state, claim):
            _fail("claim_not_dispatch_ready")
        records = _qualifying_records(state, claim)
        review_record = state["evidence_by_id"].get(payload["review_evidence_id"])
        qualifying_ids = {item["evidence_id"] for item in records}
        if review_record is None or payload["review_evidence_id"] not in qualifying_ids:
            _fail("independent_review_missing")
        if len(state["accepted_releases"]) >= MAX_ACCEPTED_RELEASES:
            _fail("budget_exceeded")
        review_payload = review_record["payload"]
        reviewer = review_record["reviewer"]
        evidence_set_digest = _framed_hash(
            _EVIDENCE_SET_DOMAIN,
            {"claim_id": claim["claim_id"], "records": records},
        )
        independent_review_digest = _framed_hash(
            _INDEPENDENT_REVIEW_DOMAIN,
            {
                "claim_id": claim["claim_id"],
                "owner_principal_id": claim["owner_principal_id"],
                "review_evidence_id": payload["review_evidence_id"],
                "verification_rule_id": review_payload["verification_rule_id"],
                "evidence_digest": review_payload["evidence_digest"],
                "reviewer_principal_id": review_payload["reviewer_principal_id"],
                "reviewer_subject_digest": reviewer["subject_digest"],
                "reviewer_role": reviewer["role"],
                "verifier_binding_digest": reviewer["verifier_binding_digest"],
                "event_hash": review_record["event_hash"],
            },
        )
        state["accepted_releases"].append(
            {
                "schema_id": EXECUTION_AUTHORITY_ACCEPTED_RELEASE_SCHEMA_ID,
                "claim_id": claim["claim_id"],
                "outcome": "accepted",
                "review_evidence_id": payload["review_evidence_id"],
                "evidence_set_digest": evidence_set_digest,
                "independent_review_digest": independent_review_digest,
                "released_at": occurred_at,
            }
        )
    del state["active"][claim["claim_id"]]
    state["released_total"] += 1
    _check_active_aggregates(state)


def _replay(
    document: dict[str, _Any],
    *,
    receipt: bytes,
    definition: dict[str, _Any],
    pins: dict[str, str],
    observed_at: str,
) -> dict[str, _Any]:
    events = document["events"]
    if type(events) is not list:
        _fail("invalid_type")
    if len(events) > MAX_LEDGER_EVENTS:
        _fail("budget_exceeded")
    state = _new_state()
    previous = _genesis_head(document["genesis_hash"])
    previous_time: _datetime | None = None
    for index, raw_event in enumerate(events):
        event = _exact_fields(raw_event, _EVENT_FIELDS)
        if event["schema_id"] != EXECUTION_AUTHORITY_EVENT_SCHEMA_ID:
            _fail("wrong_schema")
        if event["ledger_id"] != document["ledger_id"] or event["genesis_hash"] != document["genesis_hash"]:
            _fail("foreign_event")
        if _integer(event["sequence"], minimum=1, maximum=MAX_LEDGER_EVENTS) != index + 1:
            _fail("forked_ledger")
        if _hash(event["previous_event_hash"]) != previous:
            _fail("forked_ledger")
        command_id = _identifier(event["command_id"])
        event_type = event["event_type"]
        if type(event_type) is not str or event_type not in _EVENT_TYPES:
            _fail("invalid_literal")
        occurred_at = _timestamp(event["occurred_at"])
        current_time = _time(occurred_at)
        if previous_time is not None and current_time < previous_time:
            _fail("non_monotonic_event")
        if current_time > _time(observed_at):
            _fail("stale_authority")
        payload = _normalize_payload(event_type, event["payload"])
        if payload != event["payload"]:
            _fail("non_canonical_document")
        expected_command_hash = _framed_hash(
            _COMMAND_DOMAIN,
            {"event_type": event_type, "occurred_at": occurred_at, "payload": payload},
        )
        if _hash(event["command_hash"]) != expected_command_hash:
            _fail("invalid_hash")
        expected_event_hash = _framed_hash(
            _EVENT_DOMAIN,
            {key: item for key, item in event.items() if key != "event_hash"},
        )
        if _hash(event["event_hash"]) != expected_event_hash:
            _fail("invalid_hash")
        if command_id in state["commands"]:
            _fail("idempotency_conflict")
        if len(state["commands"]) >= MAX_COMMAND_IDS:
            _fail("budget_exceeded")
        state["commands"][command_id] = expected_command_hash
        _apply_event(
            state,
            event,
            payload,
            receipt=receipt,
            definition=definition,
            pins=pins,
        )
        previous = event["event_hash"]
        previous_time = current_time
    return state


def _ledger_reference(document: dict[str, _Any]) -> dict[str, _Any]:
    return {
        "schema_id": EXECUTION_AUTHORITY_LEDGER_REF_SCHEMA_ID,
        "ledger_id": document["ledger_id"],
        "definition_snapshot_hash": document["definition_snapshot_ref"]["snapshot_hash"],
        "execution_trust_authority_hash": document["execution_trust_authority_ref"]["authority_hash"],
        "genesis_hash": document["genesis_hash"],
        "cursor": document["cursor"],
        "head_hash": document["head_hash"],
        "ledger_hash": document["ledger_hash"],
    }


def _projection(
    document: dict[str, _Any], state: dict[str, _Any], observed_at: str
) -> dict[str, _Any]:
    active = [_capture(state["active"][claim_id]) for claim_id in sorted(state["active"])]
    accepted = sorted((_capture(item) for item in state["accepted_releases"]), key=lambda item: item["claim_id"])
    ready = [item["claim_id"] for item in active if _is_ready(state, item, observed_at)]
    unresolved = sorted(
        {
            gate_id
            for item in active
            for gate_id in item["required_gate_ids"]
            if state["gates"].get((item["claim_id"], gate_id), {}).get("decision") != "approved"
        }
    )
    capabilities = sorted({capability for item in active for capability in item["effect_capability_refs"]})
    return {
        "schema_id": EXECUTION_AUTHORITY_PROJECTION_SCHEMA_ID,
        "ledger_ref": _ledger_reference(document),
        "active_claims": active,
        "accepted_releases": accepted,
        "dispatch_ready_claim_ids": sorted(ready),
        "unresolved_gate_ids": unresolved,
        "effect_capability_refs": capabilities,
    }


def _validate_impl(
    value: _Any,
    *,
    definition_snapshot_ref: _Any,
    trust_receipt: _Any,
    expected_trust_receipt_hash: _Any,
    expected_trust_authority_hash: _Any,
    observed_at: _Any,
) -> tuple[
    dict[str, _Any], dict[str, _Any], dict[str, _Any], bytes, dict[str, str], str
]:
    definition, receipt, pins, observed, trust_reference = _authority_inputs(
        definition_snapshot_ref,
        trust_receipt,
        expected_trust_receipt_hash,
        expected_trust_authority_hash,
        observed_at,
    )
    document = _exact_fields(_load_document(value), _LEDGER_FIELDS)
    if document["schema_id"] != EXECUTION_AUTHORITY_LEDGER_SCHEMA_ID:
        _fail("wrong_schema")
    ledger_id = _identifier(document["ledger_id"])
    if document["definition_snapshot_ref"] != definition or document["execution_trust_authority_ref"] != trust_reference:
        _fail("stale_authority")
    genesis_hash = _genesis_hash(ledger_id, definition, trust_reference)
    if _hash(document["genesis_hash"]) != genesis_hash:
        _fail("foreign_event")
    state = _replay(
        document,
        receipt=receipt,
        definition=definition,
        pins=pins,
        observed_at=observed,
    )
    cursor = _integer(document["cursor"], minimum=0, maximum=MAX_LEDGER_EVENTS)
    if cursor != len(document["events"]):
        _fail("forked_ledger")
    expected_head = document["events"][-1]["event_hash"] if document["events"] else _genesis_head(genesis_hash)
    if _hash(document["head_hash"]) != expected_head:
        _fail("forked_ledger")
    expected_ledger_hash = _framed_hash(
        _LEDGER_DOMAIN,
        {key: item for key, item in document.items() if key != "ledger_hash"},
    )
    if _hash(document["ledger_hash"]) != expected_ledger_hash:
        _fail("invalid_hash")
    _projection(document, state, observed)
    return document, state, definition, receipt, pins, observed


def _append_command(
    value: _Any,
    *,
    event_type: str,
    command_id: _Any,
    occurred_at: _Any,
    payload: dict[str, _Any],
    expected_cursor: _Any,
    expected_head_hash: _Any,
    definition_snapshot_ref: _Any,
    trust_receipt: _Any,
    expected_trust_receipt_hash: _Any,
    expected_trust_authority_hash: _Any,
    observed_at: _Any,
) -> bytes:
    document, state, definition, receipt, pins, observed = _validate_impl(
        value,
        definition_snapshot_ref=definition_snapshot_ref,
        trust_receipt=trust_receipt,
        expected_trust_receipt_hash=expected_trust_receipt_hash,
        expected_trust_authority_hash=expected_trust_authority_hash,
        observed_at=observed_at,
    )
    checked_command_id = _identifier(command_id)
    checked_occurred_at = _timestamp(occurred_at)
    if _time(checked_occurred_at) > _time(observed):
        _fail("stale_authority")
    checked_payload = _normalize_payload(event_type, payload)
    command_hash = _framed_hash(
        _COMMAND_DOMAIN,
        {"event_type": event_type, "occurred_at": checked_occurred_at, "payload": checked_payload},
    )
    prior = state["commands"].get(checked_command_id)
    if prior is not None:
        if prior == command_hash:
            return value
        _fail("idempotency_conflict")
    cursor = _integer(expected_cursor, minimum=0, maximum=MAX_LEDGER_EVENTS)
    if cursor != document["cursor"]:
        _fail("stale_cursor")
    if _hash(expected_head_hash) != document["head_hash"]:
        _fail("stale_head")
    if len(document["events"]) >= MAX_LEDGER_EVENTS or len(state["commands"]) >= MAX_COMMAND_IDS:
        _fail("budget_exceeded")
    if event_type == "evidence_recorded" and state["evidence_total"] >= MAX_EVIDENCE_RECORDS:
        _fail("budget_exceeded")
    if event_type == "gate_decided" and state["gate_total"] >= MAX_GATE_DECISIONS:
        _fail("budget_exceeded")
    event_unsigned = {
        "schema_id": EXECUTION_AUTHORITY_EVENT_SCHEMA_ID,
        "ledger_id": document["ledger_id"],
        "genesis_hash": document["genesis_hash"],
        "sequence": document["cursor"] + 1,
        "previous_event_hash": document["head_hash"],
        "command_id": checked_command_id,
        "command_hash": command_hash,
        "occurred_at": checked_occurred_at,
        "event_type": event_type,
        "payload": checked_payload,
    }
    event = {**event_unsigned, "event_hash": _framed_hash(_EVENT_DOMAIN, event_unsigned)}
    unsigned = {
        key: item for key, item in document.items() if key != "ledger_hash"
    }
    unsigned["events"] = [*document["events"], event]
    unsigned["cursor"] = document["cursor"] + 1
    unsigned["head_hash"] = event["event_hash"]
    result = _canonical_bytes({**unsigned, "ledger_hash": _framed_hash(_LEDGER_DOMAIN, unsigned)})
    if len(result) > MAX_LEDGER_BYTES:
        _fail("budget_exceeded")
    _validate_impl(
        result,
        definition_snapshot_ref=definition,
        trust_receipt=receipt,
        expected_trust_receipt_hash=pins["expected_trust_receipt_hash"],
        expected_trust_authority_hash=pins["expected_trust_authority_hash"],
        observed_at=observed,
    )
    return result


def build_execution_authority_ledger(
    *,
    ledger_id,
    definition_snapshot_ref,
    trust_receipt,
    expected_trust_receipt_hash,
    expected_trust_authority_hash,
    observed_at,
) -> bytes:
    def operation() -> bytes:
        definition, _receipt, _pins, _observed, trust_reference = _authority_inputs(
            definition_snapshot_ref,
            trust_receipt,
            expected_trust_receipt_hash,
            expected_trust_authority_hash,
            observed_at,
        )
        checked_ledger_id = _identifier(ledger_id)
        genesis_hash = _genesis_hash(checked_ledger_id, definition, trust_reference)
        unsigned = {
            "schema_id": EXECUTION_AUTHORITY_LEDGER_SCHEMA_ID,
            "ledger_id": checked_ledger_id,
            "definition_snapshot_ref": definition,
            "execution_trust_authority_ref": trust_reference,
            "genesis_hash": genesis_hash,
            "events": [],
            "cursor": 0,
            "head_hash": _genesis_head(genesis_hash),
        }
        result = _canonical_bytes({**unsigned, "ledger_hash": _framed_hash(_LEDGER_DOMAIN, unsigned)})
        if len(result) > MAX_LEDGER_BYTES:
            _fail("budget_exceeded")
        return result

    return _public(operation)


def validate_execution_authority_ledger(
    value,
    *,
    definition_snapshot_ref,
    trust_receipt,
    expected_trust_receipt_hash,
    expected_trust_authority_hash,
    observed_at,
) -> bytes:
    def operation() -> bytes:
        _validate_impl(
            value,
            definition_snapshot_ref=definition_snapshot_ref,
            trust_receipt=trust_receipt,
            expected_trust_receipt_hash=expected_trust_receipt_hash,
            expected_trust_authority_hash=expected_trust_authority_hash,
            observed_at=observed_at,
        )
        return value

    return _public(operation)


def acquire_execution_claim(
    value,
    *,
    command_id,
    occurred_at,
    claim_id,
    owner_principal_id,
    write_paths,
    read_only_paths,
    lease_expires_at,
    effect_capability_refs,
    required_gate_ids,
    required_verification_rule_ids,
    live_actions,
    expected_cursor,
    expected_head_hash,
    definition_snapshot_ref,
    trust_receipt,
    expected_trust_receipt_hash,
    expected_trust_authority_hash,
    observed_at,
) -> bytes:
    return _public(
        lambda: _append_command(
            value,
            event_type="claim_acquired",
            command_id=command_id,
            occurred_at=occurred_at,
            payload={
                "claim_id": claim_id,
                "owner_principal_id": owner_principal_id,
                "write_paths": write_paths,
                "read_only_paths": read_only_paths,
                "lease_expires_at": lease_expires_at,
                "effect_capability_refs": effect_capability_refs,
                "required_gate_ids": required_gate_ids,
                "required_verification_rule_ids": required_verification_rule_ids,
                "live_actions": live_actions,
            },
            expected_cursor=expected_cursor,
            expected_head_hash=expected_head_hash,
            definition_snapshot_ref=definition_snapshot_ref,
            trust_receipt=trust_receipt,
            expected_trust_receipt_hash=expected_trust_receipt_hash,
            expected_trust_authority_hash=expected_trust_authority_hash,
            observed_at=observed_at,
        )
    )


def renew_execution_claim_lease(
    value,
    *,
    command_id,
    occurred_at,
    claim_id,
    lease_expires_at,
    expected_cursor,
    expected_head_hash,
    definition_snapshot_ref,
    trust_receipt,
    expected_trust_receipt_hash,
    expected_trust_authority_hash,
    observed_at,
) -> bytes:
    return _public(
        lambda: _append_command(
            value,
            event_type="lease_renewed",
            command_id=command_id,
            occurred_at=occurred_at,
            payload={"claim_id": claim_id, "lease_expires_at": lease_expires_at},
            expected_cursor=expected_cursor,
            expected_head_hash=expected_head_hash,
            definition_snapshot_ref=definition_snapshot_ref,
            trust_receipt=trust_receipt,
            expected_trust_receipt_hash=expected_trust_receipt_hash,
            expected_trust_authority_hash=expected_trust_authority_hash,
            observed_at=observed_at,
        )
    )


def decide_execution_claim_gate(
    value,
    *,
    command_id,
    occurred_at,
    claim_id,
    gate_id,
    decision,
    decision_digest,
    expected_cursor,
    expected_head_hash,
    definition_snapshot_ref,
    trust_receipt,
    expected_trust_receipt_hash,
    expected_trust_authority_hash,
    observed_at,
) -> bytes:
    return _public(
        lambda: _append_command(
            value,
            event_type="gate_decided",
            command_id=command_id,
            occurred_at=occurred_at,
            payload={
                "claim_id": claim_id,
                "gate_id": gate_id,
                "decision": decision,
                "decision_digest": decision_digest,
            },
            expected_cursor=expected_cursor,
            expected_head_hash=expected_head_hash,
            definition_snapshot_ref=definition_snapshot_ref,
            trust_receipt=trust_receipt,
            expected_trust_receipt_hash=expected_trust_receipt_hash,
            expected_trust_authority_hash=expected_trust_authority_hash,
            observed_at=observed_at,
        )
    )


def record_execution_claim_evidence(
    value,
    *,
    command_id,
    occurred_at,
    claim_id,
    evidence_id,
    verification_rule_id,
    result,
    evidence_digest,
    reviewer_principal_id,
    expected_cursor,
    expected_head_hash,
    definition_snapshot_ref,
    trust_receipt,
    expected_trust_receipt_hash,
    expected_trust_authority_hash,
    observed_at,
) -> bytes:
    return _public(
        lambda: _append_command(
            value,
            event_type="evidence_recorded",
            command_id=command_id,
            occurred_at=occurred_at,
            payload={
                "claim_id": claim_id,
                "evidence_id": evidence_id,
                "verification_rule_id": verification_rule_id,
                "result": result,
                "evidence_digest": evidence_digest,
                "reviewer_principal_id": reviewer_principal_id,
            },
            expected_cursor=expected_cursor,
            expected_head_hash=expected_head_hash,
            definition_snapshot_ref=definition_snapshot_ref,
            trust_receipt=trust_receipt,
            expected_trust_receipt_hash=expected_trust_receipt_hash,
            expected_trust_authority_hash=expected_trust_authority_hash,
            observed_at=observed_at,
        )
    )


def release_execution_claim(
    value,
    *,
    command_id,
    occurred_at,
    claim_id,
    outcome,
    review_evidence_id=None,
    expected_cursor,
    expected_head_hash,
    definition_snapshot_ref,
    trust_receipt,
    expected_trust_receipt_hash,
    expected_trust_authority_hash,
    observed_at,
) -> bytes:
    def operation() -> bytes:
        if type(outcome) is not str or outcome not in {"accepted", "blocked", "cancelled"}:
            _fail("invalid_literal")
        if outcome == "accepted":
            if review_evidence_id is None:
                _fail("independent_review_missing")
            payload = {"claim_id": claim_id, "outcome": outcome, "review_evidence_id": review_evidence_id}
        else:
            if review_evidence_id is not None:
                _fail("invalid_fields")
            payload = {"claim_id": claim_id, "outcome": outcome}
        return _append_command(
            value,
            event_type="claim_released",
            command_id=command_id,
            occurred_at=occurred_at,
            payload=payload,
            expected_cursor=expected_cursor,
            expected_head_hash=expected_head_hash,
            definition_snapshot_ref=definition_snapshot_ref,
            trust_receipt=trust_receipt,
            expected_trust_receipt_hash=expected_trust_receipt_hash,
            expected_trust_authority_hash=expected_trust_authority_hash,
            observed_at=observed_at,
        )

    return _public(operation)


def execution_authority_ledger_reference(
    value,
    *,
    definition_snapshot_ref,
    trust_receipt,
    expected_trust_receipt_hash,
    expected_trust_authority_hash,
    observed_at,
) -> dict[str, _Any]:
    def operation() -> dict[str, _Any]:
        document, _state, _definition_value, _receipt, _pins, _observed = _validate_impl(
            value,
            definition_snapshot_ref=definition_snapshot_ref,
            trust_receipt=trust_receipt,
            expected_trust_receipt_hash=expected_trust_receipt_hash,
            expected_trust_authority_hash=expected_trust_authority_hash,
            observed_at=observed_at,
        )
        return _capture(_ledger_reference(document))

    return _public(operation)


def project_execution_authority_ledger(
    value,
    *,
    definition_snapshot_ref,
    trust_receipt,
    expected_trust_receipt_hash,
    expected_trust_authority_hash,
    observed_at,
) -> dict[str, _Any]:
    def operation() -> dict[str, _Any]:
        document, state, _definition_value, _receipt, _pins, observed = _validate_impl(
            value,
            definition_snapshot_ref=definition_snapshot_ref,
            trust_receipt=trust_receipt,
            expected_trust_receipt_hash=expected_trust_receipt_hash,
            expected_trust_authority_hash=expected_trust_authority_hash,
            observed_at=observed_at,
        )
        return _capture(_projection(document, state, observed))

    return _public(operation)


__all__ = [
    "EXECUTION_AUTHORITY_ACCEPTED_RELEASE_SCHEMA_ID",
    "EXECUTION_AUTHORITY_ACTIVE_CLAIM_SCHEMA_ID",
    "EXECUTION_AUTHORITY_EVENT_SCHEMA_ID",
    "EXECUTION_AUTHORITY_LEDGER_REF_SCHEMA_ID",
    "EXECUTION_AUTHORITY_LEDGER_SCHEMA_ID",
    "EXECUTION_AUTHORITY_PROJECTION_SCHEMA_ID",
    "MAX_ACCEPTED_RELEASES",
    "MAX_ACTIVE_CAPABILITY_REFS",
    "MAX_ACTIVE_CLAIMS",
    "MAX_ACTIVE_GATE_REFS",
    "MAX_ACTIVE_SCOPE_PATHS",
    "MAX_ACTIVE_VERIFICATION_REFS",
    "MAX_CLAIMS_TOTAL",
    "MAX_COMMAND_IDS",
    "MAX_EVIDENCE_RECORDS",
    "MAX_EVENT_PAYLOAD_BYTES",
    "MAX_GATE_DECISIONS",
    "MAX_IDENTIFIER_CHARS",
    "MAX_JSON_INTEGER_TOKEN_DIGITS",
    "MAX_LEDGER_BYTES",
    "MAX_LEDGER_DEPTH",
    "MAX_LEDGER_EVENTS",
    "MAX_LEDGER_NODES",
    "MAX_PATHS_PER_CLAIM",
    "MAX_REFS_PER_CLAIM_PER_KIND",
    "MAX_RELEASED_CLAIMS_TOTAL",
    "ExecutionAuthorityLedgerError",
    "acquire_execution_claim",
    "build_execution_authority_ledger",
    "decide_execution_claim_gate",
    "execution_authority_ledger_reference",
    "project_execution_authority_ledger",
    "record_execution_claim_evidence",
    "release_execution_claim",
    "renew_execution_claim_lease",
    "validate_execution_authority_ledger",
]
