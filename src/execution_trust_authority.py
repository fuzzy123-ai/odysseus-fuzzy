"""Stateless, content-addressed trust receipts for execution authority.

The module consumes trust; it does not authenticate people, verify live
signatures, or mint trusted principals.  Callers must obtain the expected
receipt, authority, and definition hashes from independent durable state and
must supply a current observation time on every resolution.

There is intentionally no authorizing Python object.  A validation result is
an exact built-in ``bytes`` receipt.  The receipt is immutable but is not a
bearer credential: every resolver reparses it and revalidates every external
pin, schema, budget, validity window, and monotonic observation boundary.

Trust assumption: the pinned repository module and independently supplied
hash/time inputs are trusted.  An actor able to rewrite this module's globals
or bytecode, or to forge all external pins and time inputs, already controls
the in-process trust boundary and is outside this contract.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
import re
import unicodedata
from typing import Any, Mapping, Sequence


EXECUTION_TRUST_AUTHORITY_SCHEMA_ID = "odysseus.execution_trust_authority.v1"
EXECUTION_TRUST_AUTHORITY_REF_SCHEMA_ID = (
    "odysseus.execution_trust_authority_ref.v1"
)
EXECUTION_TRUST_AUTHORITY_RECEIPT_SCHEMA_ID = (
    "odysseus.execution_trust_authority_receipt.v1"
)
ATTESTATION_AUTHORITY_REF_SCHEMA_ID = (
    "odysseus.reviewer_attestation_authority_ref.v1"
)

PRINCIPAL_ROLES = frozenset({"claim_owner", "independent_qa", "deep_review"})
REVIEWER_ROLES = frozenset({"independent_qa", "deep_review"})

MAX_AUTHORITY_BYTES = 256_000
MAX_AUTHORITY_DEPTH = 8
MAX_AUTHORITY_NODES = 4_096
MAX_COLLECTION_ITEMS = 512
MAX_CAPABILITY_REFS = 256
MAX_GATE_IDS = 256
MAX_VERIFICATION_POLICIES = 256
MAX_PRINCIPALS = 128
MAX_AUTHORITY_REVISION = 2_147_483_647
MAX_RECEIPT_BYTES = MAX_AUTHORITY_BYTES + 1_024
MAX_RECEIPT_DEPTH = MAX_AUTHORITY_DEPTH + 2
MAX_RECEIPT_NODES = MAX_AUTHORITY_NODES + 16

_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+:-]{0,191}$")
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
_AUTHORITY_FIELDS = frozenset(
    {
        "schema_id",
        "authority_id",
        "authority_revision",
        "definition_snapshot_hash",
        "capability_catalog",
        "gate_catalog",
        "verification_policies",
        "principals",
        "attestation_authority_ref",
        "valid_from",
        "valid_until",
        "authority_hash",
    }
)
_REFERENCE_FIELDS = frozenset(
    {
        "schema_id",
        "authority_id",
        "authority_revision",
        "definition_snapshot_hash",
        "authority_hash",
    }
)
_RECEIPT_FIELDS = frozenset(
    {
        "schema_id",
        "authority_payload",
        "authority_hash",
        "definition_snapshot_hash",
        "validated_observed_at",
        "receipt_hash",
    }
)
_ATTESTATION_FIELDS = frozenset(
    {"schema_id", "authority_id", "authority_revision", "verifier_digest"}
)
_VERIFICATION_POLICY_FIELDS = frozenset(
    {"verification_rule_id", "required_reviewer_role"}
)
_PRINCIPAL_FIELDS = frozenset(
    {
        "principal_id",
        "subject_digest",
        "role",
        "verifier_binding_digest",
        "valid_from",
        "valid_until",
    }
)


class ExecutionTrustAuthorityError(ValueError):
    """A closed trust-root, receipt, identity, budget, or pin failure."""

    def __init__(self, code: str, path: str, detail: str) -> None:
        self.code = code
        self.path = path
        self.detail = detail
        super().__init__(f"{code} at {path}: {detail}")


def _build_execution_trust_authority_payload_impl(
    *,
    authority_id: Any,
    authority_revision: Any,
    definition_snapshot_hash: Any,
    capability_catalog: Sequence[Any],
    gate_catalog: Sequence[Any],
    verification_policies: Sequence[Mapping[str, Any]],
    principals: Sequence[Mapping[str, Any]],
    attestation_authority_ref: Mapping[str, Any],
    valid_from: Any,
    valid_until: Any,
) -> dict[str, Any]:
    """Build an isolated untrusted candidate; it has no resolver surface."""

    unsigned = _bounded_capture(
        {
            "schema_id": EXECUTION_TRUST_AUTHORITY_SCHEMA_ID,
            "authority_id": authority_id,
            "authority_revision": authority_revision,
            "definition_snapshot_hash": definition_snapshot_hash,
            "capability_catalog": capability_catalog,
            "gate_catalog": gate_catalog,
            "verification_policies": verification_policies,
            "principals": principals,
            "attestation_authority_ref": attestation_authority_ref,
            "valid_from": valid_from,
            "valid_until": valid_until,
        },
        "$",
    )
    _validate_unsigned(unsigned)
    payload = {**unsigned, "authority_hash": _digest(unsigned)}
    _assert_budget(payload, "$")
    return _canonical_copy(payload, "$")


def _validate_execution_trust_authority_impl(
    value: Mapping[str, Any],
    *,
    expected_authority_hash: Any,
    expected_definition_snapshot_hash: Any,
    observed_at: Any,
) -> bytes:
    """Validate a mapping candidate and issue a canonical immutable receipt."""

    payload = _validated_authority_payload(
        value,
        expected_authority_hash=expected_authority_hash,
        expected_definition_snapshot_hash=expected_definition_snapshot_hash,
        observed_at=observed_at,
    )
    return _issue_receipt(payload, observed_at)


def _validate_execution_trust_authority_json_impl(
    value: bytes,
    *,
    expected_authority_hash: Any,
    expected_definition_snapshot_hash: Any,
    observed_at: Any,
) -> bytes:
    """Duplicate-safe JSON boundary that returns an immutable receipt."""

    payload = _load_json(value, maximum=MAX_AUTHORITY_BYTES, path="$")
    return _validate_execution_trust_authority_impl(
        payload,
        expected_authority_hash=expected_authority_hash,
        expected_definition_snapshot_hash=expected_definition_snapshot_hash,
        observed_at=observed_at,
    )


def _execution_trust_authority_reference_impl(
    receipt: bytes,
    *,
    expected_receipt_hash: Any,
    expected_authority_hash: Any,
    expected_definition_snapshot_hash: Any,
    observed_at: Any,
) -> dict[str, Any]:
    payload = _validated_receipt_authority(
        receipt,
        expected_receipt_hash=expected_receipt_hash,
        expected_authority_hash=expected_authority_hash,
        expected_definition_snapshot_hash=expected_definition_snapshot_hash,
        observed_at=observed_at,
    )
    return {
        "schema_id": EXECUTION_TRUST_AUTHORITY_REF_SCHEMA_ID,
        "authority_id": payload["authority_id"],
        "authority_revision": payload["authority_revision"],
        "definition_snapshot_hash": payload["definition_snapshot_hash"],
        "authority_hash": payload["authority_hash"],
    }


def _resolve_execution_capability_refs_impl(
    receipt: bytes,
    values: Sequence[Any],
    *,
    expected_receipt_hash: Any,
    expected_authority_hash: Any,
    expected_definition_snapshot_hash: Any,
    observed_at: Any,
) -> tuple[str, ...]:
    payload = _validated_receipt_authority(
        receipt,
        expected_receipt_hash=expected_receipt_hash,
        expected_authority_hash=expected_authority_hash,
        expected_definition_snapshot_hash=expected_definition_snapshot_hash,
        observed_at=observed_at,
    )
    checked = _identifiers(
        values,
        "$.effect_capability_refs",
        maximum=MAX_CAPABILITY_REFS,
    )
    if not set(checked).issubset(payload["capability_catalog"]):
        _fail(
            "unknown_capability",
            "$.effect_capability_refs",
            "capability is absent from the pinned execution trust root",
        )
    return checked


def _resolve_execution_gate_ids_impl(
    receipt: bytes,
    values: Sequence[Any],
    *,
    expected_receipt_hash: Any,
    expected_authority_hash: Any,
    expected_definition_snapshot_hash: Any,
    observed_at: Any,
) -> tuple[str, ...]:
    payload = _validated_receipt_authority(
        receipt,
        expected_receipt_hash=expected_receipt_hash,
        expected_authority_hash=expected_authority_hash,
        expected_definition_snapshot_hash=expected_definition_snapshot_hash,
        observed_at=observed_at,
    )
    checked = _identifiers(values, "$.gate_ids", maximum=MAX_GATE_IDS)
    if not set(checked).issubset(payload["gate_catalog"]):
        _fail(
            "unknown_gate",
            "$.gate_ids",
            "gate is absent from the pinned execution trust root",
        )
    return checked


def _resolve_execution_verification_policy_impl(
    receipt: bytes,
    verification_rule_id: Any,
    *,
    expected_receipt_hash: Any,
    expected_authority_hash: Any,
    expected_definition_snapshot_hash: Any,
    observed_at: Any,
) -> dict[str, str]:
    payload = _validated_receipt_authority(
        receipt,
        expected_receipt_hash=expected_receipt_hash,
        expected_authority_hash=expected_authority_hash,
        expected_definition_snapshot_hash=expected_definition_snapshot_hash,
        observed_at=observed_at,
    )
    return _policy_from_payload(payload, verification_rule_id)


def _resolve_execution_principal_impl(
    receipt: bytes,
    principal_id: Any,
    *,
    expected_receipt_hash: Any,
    expected_authority_hash: Any,
    expected_definition_snapshot_hash: Any,
    observed_at: Any,
    required_role: str | None = None,
) -> dict[str, Any]:
    payload = _validated_receipt_authority(
        receipt,
        expected_receipt_hash=expected_receipt_hash,
        expected_authority_hash=expected_authority_hash,
        expected_definition_snapshot_hash=expected_definition_snapshot_hash,
        observed_at=observed_at,
    )
    observation = _timestamp(observed_at, "$.observed_at")
    return _principal_from_payload(
        payload,
        principal_id,
        observed_at=observation,
        required_role=required_role,
    )


def _policy_from_payload(
    payload: Mapping[str, Any],
    verification_rule_id: Any,
) -> dict[str, str]:
    rule_id = _identifier(verification_rule_id, "$.verification_rule_id")
    for policy in payload["verification_policies"]:
        if policy["verification_rule_id"] == rule_id:
            return dict(policy)
    _fail(
        "unknown_verification_rule",
        "$.verification_rule_id",
        "verification rule is absent from the pinned execution trust root",
    )


def _principal_from_payload(
    payload: Mapping[str, Any],
    principal_id: Any,
    *,
    observed_at: str,
    required_role: str | None,
) -> dict[str, Any]:
    checked_id = _identifier(principal_id, "$.principal_id")
    found = next(
        (item for item in payload["principals"] if item["principal_id"] == checked_id),
        None,
    )
    if found is None:
        _fail("unknown_principal", "$.principal_id", "principal is not trusted")
    _assert_active_window(
        observed_at,
        found["valid_from"],
        found["valid_until"],
        "$.observed_at",
        "principal_inactive",
    )
    if required_role is not None:
        role = _literal(required_role, "$.required_role", PRINCIPAL_ROLES)
        if found["role"] != role:
            _fail(
                "principal_role_mismatch",
                "$.required_role",
                "principal role differs from the trusted assignment",
            )
    return dict(found)


def _resolve_execution_independent_reviewer_impl(
    receipt: bytes,
    *,
    owner_principal_id: Any,
    reviewer_principal_id: Any,
    verification_rule_id: Any,
    expected_receipt_hash: Any,
    expected_authority_hash: Any,
    expected_definition_snapshot_hash: Any,
    observed_at: Any,
) -> dict[str, Any]:
    payload = _validated_receipt_authority(
        receipt,
        expected_receipt_hash=expected_receipt_hash,
        expected_authority_hash=expected_authority_hash,
        expected_definition_snapshot_hash=expected_definition_snapshot_hash,
        observed_at=observed_at,
    )
    observation = _timestamp(observed_at, "$.observed_at")
    policy = _policy_from_payload(payload, verification_rule_id)
    owner = _principal_from_payload(
        payload,
        owner_principal_id,
        required_role="claim_owner",
        observed_at=observation,
    )
    reviewer = _principal_from_payload(
        payload,
        reviewer_principal_id,
        required_role=policy["required_reviewer_role"],
        observed_at=observation,
    )
    if reviewer["subject_digest"] == owner["subject_digest"]:
        _fail(
            "reviewer_not_independent",
            "$.reviewer_principal_id",
            "reviewer and claim owner resolve to the same trusted subject",
        )
    return reviewer


def _issue_receipt(payload: Mapping[str, Any], observed_at: Any) -> bytes:
    observation = _timestamp(observed_at, "$.observed_at")
    unsigned = {
        "schema_id": EXECUTION_TRUST_AUTHORITY_RECEIPT_SCHEMA_ID,
        "authority_payload": _canonical_copy(payload, "$.authority_payload"),
        "authority_hash": payload["authority_hash"],
        "definition_snapshot_hash": payload["definition_snapshot_hash"],
        "validated_observed_at": observation,
    }
    envelope = {**unsigned, "receipt_hash": _digest(unsigned)}
    _assert_receipt_budget(envelope, "$")
    return _canonical_json(envelope, "$").encode("utf-8")


def _validated_receipt_authority(
    receipt: Any,
    *,
    expected_receipt_hash: Any,
    expected_authority_hash: Any,
    expected_definition_snapshot_hash: Any,
    observed_at: Any,
) -> dict[str, Any]:
    if type(receipt) is not bytes:
        _fail("invalid_receipt_bytes", "$", "expected exact immutable receipt bytes")
    envelope = _load_json(receipt, maximum=MAX_RECEIPT_BYTES, path="$")
    if receipt != _canonical_json(envelope, "$").encode("utf-8"):
        _fail("non_canonical_receipt", "$", "receipt bytes are not canonical JSON")
    _exact_fields(envelope, _RECEIPT_FIELDS, "$")
    _assert_receipt_budget(envelope, "$")
    if envelope["schema_id"] != EXECUTION_TRUST_AUTHORITY_RECEIPT_SCHEMA_ID:
        _fail("invalid_literal", "$.schema_id", "receipt schema is invalid")
    supplied_receipt_hash = _hash(envelope["receipt_hash"], "$.receipt_hash")
    unsigned = {key: item for key, item in envelope.items() if key != "receipt_hash"}
    if supplied_receipt_hash != _digest(unsigned):
        _fail("receipt_hash_mismatch", "$.receipt_hash", "receipt bytes differ from their hash")
    trusted_receipt_hash = _hash(expected_receipt_hash, "$.expected_receipt_hash")
    if supplied_receipt_hash != trusted_receipt_hash:
        _fail("untrusted_receipt", "$.receipt_hash", "receipt differs from the durable external pin")
    trusted_authority_hash = _hash(
        expected_authority_hash,
        "$.expected_authority_hash",
    )
    trusted_definition_hash = _hash(
        expected_definition_snapshot_hash,
        "$.expected_definition_snapshot_hash",
    )
    if envelope["authority_hash"] != trusted_authority_hash:
        _fail("untrusted_authority", "$.authority_hash", "receipt authority pin differs")
    if envelope["definition_snapshot_hash"] != trusted_definition_hash:
        _fail("foreign_definition", "$.definition_snapshot_hash", "receipt definition differs")
    validated_at = _timestamp(
        envelope["validated_observed_at"],
        "$.validated_observed_at",
    )
    current = _timestamp(observed_at, "$.observed_at")
    if _timestamp_value(current) < _timestamp_value(validated_at):
        _fail(
            "stale_observation",
            "$.observed_at",
            "authorization observation precedes the pinned receipt observation",
        )
    payload = _validated_authority_payload(
        envelope["authority_payload"],
        expected_authority_hash=trusted_authority_hash,
        expected_definition_snapshot_hash=trusted_definition_hash,
        observed_at=validated_at,
    )
    if payload["authority_hash"] != envelope["authority_hash"]:
        _fail("receipt_authority_mismatch", "$.authority_hash", "receipt authority differs")
    if payload["definition_snapshot_hash"] != envelope["definition_snapshot_hash"]:
        _fail("receipt_definition_mismatch", "$.definition_snapshot_hash", "receipt definition differs")
    _assert_active_window(
        current,
        payload["valid_from"],
        payload["valid_until"],
        "$.observed_at",
        "authority_inactive",
    )
    return payload


def _validated_authority_payload(
    value: Mapping[str, Any],
    *,
    expected_authority_hash: Any,
    expected_definition_snapshot_hash: Any,
    observed_at: Any,
) -> dict[str, Any]:
    payload = _mapping(value, "$")
    _assert_budget(payload, "$")
    _exact_fields(payload, _AUTHORITY_FIELDS, "$")
    if payload["schema_id"] != EXECUTION_TRUST_AUTHORITY_SCHEMA_ID:
        _fail("invalid_literal", "$.schema_id", "authority schema is invalid")
    unsigned = {key: item for key, item in payload.items() if key != "authority_hash"}
    _validate_unsigned(unsigned)
    supplied_hash = _hash(payload["authority_hash"], "$.authority_hash")
    if supplied_hash != _digest(unsigned):
        _fail("authority_hash_mismatch", "$.authority_hash", "authority bytes differ from their hash")
    trusted_hash = _hash(expected_authority_hash, "$.expected_authority_hash")
    if supplied_hash != trusted_hash:
        _fail("untrusted_authority", "$.authority_hash", "authority differs from the durable external pin")
    definition_hash = _hash(
        expected_definition_snapshot_hash,
        "$.expected_definition_snapshot_hash",
    )
    if payload["definition_snapshot_hash"] != definition_hash:
        _fail("foreign_definition", "$.definition_snapshot_hash", "authority belongs to another definition")
    observation = _timestamp(observed_at, "$.observed_at")
    _assert_active_window(
        observation,
        payload["valid_from"],
        payload["valid_until"],
        "$.observed_at",
        "authority_inactive",
    )
    return _canonical_copy(payload, "$")


def _validate_unsigned(value: Mapping[str, Any]) -> None:
    expected = _AUTHORITY_FIELDS - {"authority_hash"}
    _exact_fields(value, expected, "$")
    if value["schema_id"] != EXECUTION_TRUST_AUTHORITY_SCHEMA_ID:
        _fail("invalid_literal", "$.schema_id", "authority schema is invalid")
    _identifier(value["authority_id"], "$.authority_id")
    _integer(
        value["authority_revision"],
        "$.authority_revision",
        minimum=1,
        maximum=MAX_AUTHORITY_REVISION,
    )
    _hash(value["definition_snapshot_hash"], "$.definition_snapshot_hash")
    _identifiers(value["capability_catalog"], "$.capability_catalog", maximum=MAX_CAPABILITY_REFS)
    _identifiers(value["gate_catalog"], "$.gate_catalog", maximum=MAX_GATE_IDS)
    valid_from = _timestamp(value["valid_from"], "$.valid_from")
    valid_until = _timestamp(value["valid_until"], "$.valid_until")
    if _timestamp_value(valid_from) >= _timestamp_value(valid_until):
        _fail("invalid_validity_window", "$", "authority validity window is empty")
    _attestation_reference(value["attestation_authority_ref"])
    policies = _verification_policies(value["verification_policies"])
    principals = _principals(
        value["principals"],
        authority_valid_from=valid_from,
        authority_valid_until=valid_until,
    )
    assigned_roles = {item["role"] for item in principals}
    required_roles = {item["required_reviewer_role"] for item in policies}
    if "claim_owner" not in assigned_roles or not required_roles.issubset(assigned_roles):
        _fail("principal_role_missing", "$.principals", "authority lacks required roles")


def _attestation_reference(value: Any) -> dict[str, Any]:
    reference = _mapping(value, "$.attestation_authority_ref")
    _exact_fields(reference, _ATTESTATION_FIELDS, "$.attestation_authority_ref")
    if reference["schema_id"] != ATTESTATION_AUTHORITY_REF_SCHEMA_ID:
        _fail("invalid_literal", "$.attestation_authority_ref.schema_id", "attestation schema is invalid")
    _identifier(reference["authority_id"], "$.attestation_authority_ref.authority_id")
    _integer(reference["authority_revision"], "$.attestation_authority_ref.authority_revision", minimum=1, maximum=MAX_AUTHORITY_REVISION)
    _hash(reference["verifier_digest"], "$.attestation_authority_ref.verifier_digest")
    return reference


def _verification_policies(value: Any) -> tuple[dict[str, str], ...]:
    values = _array(value, "$.verification_policies")
    if not values or len(values) > MAX_VERIFICATION_POLICIES:
        _fail("authority_budget_exceeded", "$.verification_policies", "policy count outside budget")
    policies: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(values):
        path = f"$.verification_policies[{index}]"
        policy = _mapping(raw, path)
        _exact_fields(policy, _VERIFICATION_POLICY_FIELDS, path)
        rule_id = _identifier(policy["verification_rule_id"], f"{path}.verification_rule_id")
        if rule_id in seen:
            _fail("duplicate_id", f"{path}.verification_rule_id", "rule is duplicated")
        seen.add(rule_id)
        role = _literal(policy["required_reviewer_role"], f"{path}.required_reviewer_role", REVIEWER_ROLES)
        policies.append({"verification_rule_id": rule_id, "required_reviewer_role": role})
    if [item["verification_rule_id"] for item in policies] != sorted(seen):
        _fail("non_canonical_value", "$.verification_policies", "policies must be sorted")
    return tuple(policies)


def _principals(
    value: Any,
    *,
    authority_valid_from: str,
    authority_valid_until: str,
) -> tuple[dict[str, Any], ...]:
    values = _array(value, "$.principals")
    if not values or len(values) > MAX_PRINCIPALS:
        _fail("authority_budget_exceeded", "$.principals", "principal count outside budget")
    principals: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_identity_keys: set[str] = set()
    reviewer_roles_by_subject: dict[str, set[str]] = {}
    subjects_by_verifier_binding: dict[str, str] = {}
    for index, raw in enumerate(values):
        path = f"$.principals[{index}]"
        principal = _mapping(raw, path)
        _exact_fields(principal, _PRINCIPAL_FIELDS, path)
        principal_id = _identifier(principal["principal_id"], f"{path}.principal_id")
        if principal_id in seen_ids:
            _fail("duplicate_id", f"{path}.principal_id", "principal is duplicated")
        seen_ids.add(principal_id)
        identity_key = principal_id.casefold()
        if identity_key in seen_identity_keys:
            _fail("duplicate_principal_identity", f"{path}.principal_id", "principal alias is duplicated")
        seen_identity_keys.add(identity_key)
        subject_digest = _hash(principal["subject_digest"], f"{path}.subject_digest")
        role = _literal(principal["role"], f"{path}.role", PRINCIPAL_ROLES)
        verifier_digest = _hash(principal["verifier_binding_digest"], f"{path}.verifier_binding_digest")
        bound_subject = subjects_by_verifier_binding.get(verifier_digest)
        if bound_subject is not None and bound_subject != subject_digest:
            _fail("verifier_binding_conflict", f"{path}.verifier_binding_digest", "verifier identifies multiple subjects")
        subjects_by_verifier_binding[verifier_digest] = subject_digest
        valid_from = _timestamp(principal["valid_from"], f"{path}.valid_from")
        valid_until = _timestamp(principal["valid_until"], f"{path}.valid_until")
        if not (
            _timestamp_value(authority_valid_from)
            <= _timestamp_value(valid_from)
            < _timestamp_value(valid_until)
            <= _timestamp_value(authority_valid_until)
        ):
            _fail("invalid_validity_window", path, "principal validity must be inside authority validity")
        if role in REVIEWER_ROLES:
            roles = reviewer_roles_by_subject.setdefault(subject_digest, set())
            roles.add(role)
            if len(roles) > 1:
                _fail("principal_role_conflict", f"{path}.role", "subject has conflicting reviewer roles")
        principals.append(
            {
                "principal_id": principal_id,
                "subject_digest": subject_digest,
                "role": role,
                "verifier_binding_digest": verifier_digest,
                "valid_from": valid_from,
                "valid_until": valid_until,
            }
        )
    if [item["principal_id"] for item in principals] != sorted(seen_ids):
        _fail("non_canonical_value", "$.principals", "principals must be sorted")
    return tuple(principals)


class _DuplicateKey(ValueError):
    pass


def _load_json(value: Any, *, maximum: int, path: str) -> dict[str, Any]:
    if type(value) is not bytes or not value or len(value) > maximum:
        _fail("invalid_json_bytes", path, "expected bounded non-empty JSON bytes")

    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise _DuplicateKey
            result[key] = item
        return result

    def finite_float(token: str) -> float:
        parsed = float(token)
        if not math.isfinite(parsed):
            raise ValueError
        return parsed

    def bounded_int(token: str) -> int:
        if len(token.lstrip("-")) > 128:
            raise ValueError
        return int(token)

    try:
        parsed = json.loads(
            value.decode("utf-8"),
            object_pairs_hook=pairs_hook,
            parse_float=finite_float,
            parse_int=bounded_int,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except _DuplicateKey:
        _fail("duplicate_json_key", path, "JSON contains a duplicate object key")
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
        _fail("invalid_json_bytes", path, "value is not bounded finite JSON")
    if type(parsed) is not dict:
        _fail("invalid_type", path, "JSON root must be an object")
    return parsed


def _bounded_capture(value: Any, path: str) -> Any:
    state = {"nodes": 0, "bytes": 0}

    def add_bytes(count: int) -> None:
        state["bytes"] += count
        if state["bytes"] > MAX_RECEIPT_BYTES:
            _fail("authority_budget_exceeded", path, "capture exceeds byte budget")

    def visit(item: Any, current_path: str, depth: int) -> Any:
        state["nodes"] += 1
        if state["nodes"] > MAX_RECEIPT_NODES or depth > MAX_RECEIPT_DEPTH:
            _fail("authority_budget_exceeded", path, "capture exceeds node/depth budget")
        if item is None or type(item) is bool:
            add_bytes(5)
            return item
        if type(item) is str:
            if len(item) > MAX_RECEIPT_BYTES:
                _fail("authority_budget_exceeded", current_path, "string exceeds byte budget")
            add_bytes(len(item.encode("utf-8")) + 2)
            return item
        if type(item) is int:
            if item.bit_length() > MAX_RECEIPT_BYTES * 4:
                _fail("authority_budget_exceeded", current_path, "integer exceeds byte budget")
            try:
                token = str(item)
            except Exception:
                _fail("capture_failed", current_path, "integer could not be isolated")
            add_bytes(len(token))
            return item
        if type(item) is float:
            if not math.isfinite(item):
                _fail("non_canonical_value", current_path, "float is not finite")
            add_bytes(32)
            return item
        if type(item) is dict:
            result: dict[str, Any] = {}
            if len(item) > MAX_COLLECTION_ITEMS:
                _fail("authority_budget_exceeded", current_path, "object exceeds item budget")
            for index, (key, child) in enumerate(dict.items(item)):
                if type(key) is not str:
                    _fail("capture_failed", current_path, "object could not be isolated")
                add_bytes(len(key.encode("utf-8")) + 3)
                result[key] = visit(child, f"{current_path}[{index}]", depth + 1)
            return result
        if type(item) is list or type(item) is tuple:
            result: list[Any] = []
            if len(item) > MAX_COLLECTION_ITEMS:
                _fail("authority_budget_exceeded", current_path, "array exceeds item budget")
            for index in range(len(item)):
                result.append(visit(item[index], f"{current_path}[{index}]", depth + 1))
            return result
        _fail("capture_failed", current_path, "value could not be isolated")

    captured = visit(value, path, 0)
    encoded = _canonical_json(captured, path).encode("utf-8")
    if len(encoded) > MAX_RECEIPT_BYTES:
        _fail("authority_budget_exceeded", path, "capture exceeds canonical byte budget")
    return captured


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("capture_failed", path, "object could not be isolated")
    captured = _bounded_capture(value, path)
    if type(captured) is not dict:
        _fail("capture_failed", path, "object could not be isolated")
    return captured


def _array(value: Any, path: str) -> list[Any]:
    if type(value) is not list and type(value) is not tuple:
        _fail("capture_failed", path, "array could not be isolated")
    captured = _bounded_capture(value, path)
    if type(captured) is not list:
        _fail("capture_failed", path, "array could not be isolated")
    return captured


def _identifiers(value: Any, path: str, *, maximum: int) -> tuple[str, ...]:
    values = _array(value, path)
    if len(values) > maximum:
        _fail("authority_budget_exceeded", path, "identifier collection exceeds budget")
    checked = tuple(_identifier(item, f"{path}[{index}]") for index, item in enumerate(values))
    if checked != tuple(sorted(set(checked))):
        _fail("non_canonical_value", path, "identifiers must be unique and sorted")
    return checked


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str], path: str) -> None:
    if set(value) != expected:
        _fail("invalid_fields", path, "object fields do not match the closed contract")


def _identifier(value: Any, path: str) -> str:
    if type(value) is not str or value != unicodedata.normalize("NFC", value) or not _ID_RE.fullmatch(value):
        _fail("invalid_identifier", path, "identifier is not canonical or bounded")
    return value


def _hash(value: Any, path: str) -> str:
    if type(value) is not str or not _HASH_RE.fullmatch(value):
        _fail("invalid_hash", path, "expected canonical sha256 digest")
    return value


def _literal(value: Any, path: str, allowed: frozenset[str]) -> str:
    if type(value) is not str or value not in allowed:
        _fail("invalid_literal", path, "value is outside the closed enum")
    return value


def _integer(value: Any, path: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail("invalid_integer", path, f"expected integer in [{minimum}, {maximum}]")
    return value


def _timestamp(value: Any, path: str) -> str:
    if type(value) is not str or not _TIMESTAMP_RE.fullmatch(value):
        _fail("invalid_timestamp", path, "expected offset-aware ISO-8601 timestamp")
    try:
        parsed = _timestamp_value(value)
    except (OverflowError, ValueError):
        _fail("invalid_timestamp", path, "timestamp is not a real date-time")
    if parsed.utcoffset() is None:
        _fail("invalid_timestamp", path, "timestamp must include an offset")
    return value


def _timestamp_value(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _assert_active_window(observed_at: str, valid_from: str, valid_until: str, path: str, code: str) -> None:
    observed = _timestamp_value(observed_at)
    if not (_timestamp_value(valid_from) <= observed < _timestamp_value(valid_until)):
        _fail(code, path, "observation is outside the accepted validity window")


def _assert_budget(value: Any, path: str) -> None:
    _assert_tree_budget(
        value,
        path,
        maximum_bytes=MAX_AUTHORITY_BYTES,
        maximum_depth=MAX_AUTHORITY_DEPTH,
        maximum_nodes=MAX_AUTHORITY_NODES,
    )


def _assert_receipt_budget(value: Any, path: str) -> None:
    _assert_tree_budget(
        value,
        path,
        maximum_bytes=MAX_RECEIPT_BYTES,
        maximum_depth=MAX_RECEIPT_DEPTH,
        maximum_nodes=MAX_RECEIPT_NODES,
    )


def _assert_tree_budget(
    value: Any,
    path: str,
    *,
    maximum_bytes: int,
    maximum_depth: int,
    maximum_nodes: int,
) -> None:
    encoded = _canonical_json(value, path).encode("utf-8")
    if len(encoded) > maximum_bytes:
        _fail("authority_budget_exceeded", path, "value exceeds byte budget")
    nodes = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > maximum_nodes or depth > maximum_depth:
            _fail("authority_budget_exceeded", path, "value exceeds node/depth budget")
        if type(item) is dict:
            if len(item) > MAX_COLLECTION_ITEMS:
                _fail("authority_budget_exceeded", path, "object exceeds item budget")
            for child in item.values():
                visit(child, depth + 1)
        elif type(item) is list:
            if len(item) > MAX_COLLECTION_ITEMS:
                _fail("authority_budget_exceeded", path, "array exceeds item budget")
            for child in item:
                visit(child, depth + 1)

    visit(value, 0)


def _canonical_copy(value: Any, path: str) -> Any:
    return json.loads(_canonical_json(value, path))


def _canonical_json(value: Any, path: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except Exception:
        _fail("non_canonical_value", path, "value is not finite canonical JSON")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value, "$").encode("utf-8")).hexdigest()


def _fail(code: str, path: str, detail: str) -> None:
    raise ExecutionTrustAuthorityError(code, path, detail)


def _public_result(operation: Any) -> Any:
    try:
        return operation()
    except ExecutionTrustAuthorityError as error:
        failure = (error.code, error.path, error.detail)
    except Exception:
        failure = ("capture_failed", "$", "public input could not be isolated")
    raise ExecutionTrustAuthorityError(*failure) from None


def build_execution_trust_authority_payload(
    *,
    authority_id: Any,
    authority_revision: Any,
    definition_snapshot_hash: Any,
    capability_catalog: Sequence[Any],
    gate_catalog: Sequence[Any],
    verification_policies: Sequence[Mapping[str, Any]],
    principals: Sequence[Mapping[str, Any]],
    attestation_authority_ref: Mapping[str, Any],
    valid_from: Any,
    valid_until: Any,
) -> dict[str, Any]:
    """Build an isolated untrusted candidate; it has no resolver surface."""

    return _public_result(
        lambda: _build_execution_trust_authority_payload_impl(
            authority_id=authority_id,
            authority_revision=authority_revision,
            definition_snapshot_hash=definition_snapshot_hash,
            capability_catalog=capability_catalog,
            gate_catalog=gate_catalog,
            verification_policies=verification_policies,
            principals=principals,
            attestation_authority_ref=attestation_authority_ref,
            valid_from=valid_from,
            valid_until=valid_until,
        )
    )


def validate_execution_trust_authority(
    value: Mapping[str, Any],
    *,
    expected_authority_hash: Any,
    expected_definition_snapshot_hash: Any,
    observed_at: Any,
) -> bytes:
    """Validate a mapping candidate and issue a canonical immutable receipt."""

    return _public_result(
        lambda: _validate_execution_trust_authority_impl(
            value,
            expected_authority_hash=expected_authority_hash,
            expected_definition_snapshot_hash=expected_definition_snapshot_hash,
            observed_at=observed_at,
        )
    )


def validate_execution_trust_authority_json(
    value: bytes,
    *,
    expected_authority_hash: Any,
    expected_definition_snapshot_hash: Any,
    observed_at: Any,
) -> bytes:
    """Duplicate-safe JSON boundary that returns an immutable receipt."""

    return _public_result(
        lambda: _validate_execution_trust_authority_json_impl(
            value,
            expected_authority_hash=expected_authority_hash,
            expected_definition_snapshot_hash=expected_definition_snapshot_hash,
            observed_at=observed_at,
        )
    )


def execution_trust_authority_reference(
    receipt: bytes,
    *,
    expected_receipt_hash: Any,
    expected_authority_hash: Any,
    expected_definition_snapshot_hash: Any,
    observed_at: Any,
) -> dict[str, Any]:
    return _public_result(
        lambda: _execution_trust_authority_reference_impl(
            receipt,
            expected_receipt_hash=expected_receipt_hash,
            expected_authority_hash=expected_authority_hash,
            expected_definition_snapshot_hash=expected_definition_snapshot_hash,
            observed_at=observed_at,
        )
    )


def resolve_execution_capability_refs(
    receipt: bytes,
    values: Sequence[Any],
    *,
    expected_receipt_hash: Any,
    expected_authority_hash: Any,
    expected_definition_snapshot_hash: Any,
    observed_at: Any,
) -> tuple[str, ...]:
    return _public_result(
        lambda: _resolve_execution_capability_refs_impl(
            receipt,
            values,
            expected_receipt_hash=expected_receipt_hash,
            expected_authority_hash=expected_authority_hash,
            expected_definition_snapshot_hash=expected_definition_snapshot_hash,
            observed_at=observed_at,
        )
    )


def resolve_execution_gate_ids(
    receipt: bytes,
    values: Sequence[Any],
    *,
    expected_receipt_hash: Any,
    expected_authority_hash: Any,
    expected_definition_snapshot_hash: Any,
    observed_at: Any,
) -> tuple[str, ...]:
    return _public_result(
        lambda: _resolve_execution_gate_ids_impl(
            receipt,
            values,
            expected_receipt_hash=expected_receipt_hash,
            expected_authority_hash=expected_authority_hash,
            expected_definition_snapshot_hash=expected_definition_snapshot_hash,
            observed_at=observed_at,
        )
    )


def resolve_execution_verification_policy(
    receipt: bytes,
    verification_rule_id: Any,
    *,
    expected_receipt_hash: Any,
    expected_authority_hash: Any,
    expected_definition_snapshot_hash: Any,
    observed_at: Any,
) -> dict[str, str]:
    return _public_result(
        lambda: _resolve_execution_verification_policy_impl(
            receipt,
            verification_rule_id,
            expected_receipt_hash=expected_receipt_hash,
            expected_authority_hash=expected_authority_hash,
            expected_definition_snapshot_hash=expected_definition_snapshot_hash,
            observed_at=observed_at,
        )
    )


def resolve_execution_principal(
    receipt: bytes,
    principal_id: Any,
    *,
    expected_receipt_hash: Any,
    expected_authority_hash: Any,
    expected_definition_snapshot_hash: Any,
    observed_at: Any,
    required_role: str | None = None,
) -> dict[str, Any]:
    return _public_result(
        lambda: _resolve_execution_principal_impl(
            receipt,
            principal_id,
            expected_receipt_hash=expected_receipt_hash,
            expected_authority_hash=expected_authority_hash,
            expected_definition_snapshot_hash=expected_definition_snapshot_hash,
            observed_at=observed_at,
            required_role=required_role,
        )
    )


def resolve_execution_independent_reviewer(
    receipt: bytes,
    *,
    owner_principal_id: Any,
    reviewer_principal_id: Any,
    verification_rule_id: Any,
    expected_receipt_hash: Any,
    expected_authority_hash: Any,
    expected_definition_snapshot_hash: Any,
    observed_at: Any,
) -> dict[str, Any]:
    return _public_result(
        lambda: _resolve_execution_independent_reviewer_impl(
            receipt,
            owner_principal_id=owner_principal_id,
            reviewer_principal_id=reviewer_principal_id,
            verification_rule_id=verification_rule_id,
            expected_receipt_hash=expected_receipt_hash,
            expected_authority_hash=expected_authority_hash,
            expected_definition_snapshot_hash=expected_definition_snapshot_hash,
            observed_at=observed_at,
        )
    )


__all__ = [
    "ATTESTATION_AUTHORITY_REF_SCHEMA_ID",
    "EXECUTION_TRUST_AUTHORITY_RECEIPT_SCHEMA_ID",
    "EXECUTION_TRUST_AUTHORITY_REF_SCHEMA_ID",
    "EXECUTION_TRUST_AUTHORITY_SCHEMA_ID",
    "MAX_AUTHORITY_BYTES",
    "MAX_AUTHORITY_DEPTH",
    "MAX_AUTHORITY_NODES",
    "MAX_AUTHORITY_REVISION",
    "MAX_CAPABILITY_REFS",
    "MAX_GATE_IDS",
    "MAX_PRINCIPALS",
    "MAX_RECEIPT_BYTES",
    "MAX_VERIFICATION_POLICIES",
    "ExecutionTrustAuthorityError",
    "build_execution_trust_authority_payload",
    "execution_trust_authority_reference",
    "resolve_execution_capability_refs",
    "resolve_execution_gate_ids",
    "resolve_execution_independent_reviewer",
    "resolve_execution_principal",
    "resolve_execution_verification_policy",
    "validate_execution_trust_authority",
    "validate_execution_trust_authority_json",
]
