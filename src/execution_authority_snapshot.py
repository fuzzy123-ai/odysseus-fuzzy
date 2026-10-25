"""Native and digest-only compare execution authority snapshots (R3)."""
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
from src.execution_authority_ledger import (
    ExecutionAuthorityLedgerError as _LedgerError,
    project_execution_authority_ledger as _project_ledger,
)
from src.execution_trust_authority import (
    execution_trust_authority_reference as _execution_trust_reference,
)


EXECUTION_AUTHORITY_SNAPSHOT_SCHEMA_ID = "odysseus.execution_authority_snapshot.v3"
COMPARE_ONLY_AUTHORITY_SOURCE_BINDING_SCHEMA_ID = "odysseus.compare_only_authority_source_binding.v1"
COMPARE_ONLY_AUTHORITY_SNAPSHOT_SCHEMA_ID = "odysseus.compare_only_authority_snapshot.v1"

MAX_SNAPSHOT_BYTES = 524_288
MAX_PROJECTION_VERSIONS = 32
MAX_COMPARE_SOURCE_BYTES_EACH = 4_194_304
MAX_IDENTIFIER_CHARS = 192
MAX_JSON_INTEGER_TOKEN_DIGITS = 128

_MAX_DEPTH = 10
_MAX_NODES = 262_144
_HASH_RE = _re.compile(r"^sha256:[0-9a-f]{64}$")
_ID_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+:-]{0,191}$")
_TIMESTAMP_RE = _re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)

_PROJECTION_DOMAIN = "odysseus.execution_authority.projection.v3"
_NATIVE_SNAPSHOT_DOMAIN = "odysseus.execution_authority.snapshot.v3"
_OPEN_WORK_DOMAIN = "odysseus.compare_only.open_work.v1"
_ROUND7_DOMAIN = "odysseus.compare_only.round7.v1"
_BINDING_DOMAIN = "odysseus.compare_only.binding.v1"
_COMPARE_SNAPSHOT_DOMAIN = "odysseus.compare_only.snapshot.v1"

_NATIVE_FIELDS = frozenset(
    {
        "schema_id", "definition_snapshot_ref", "execution_trust_authority_ref",
        "ledger_ref", "active_claims", "accepted_releases",
        "dispatch_ready_claim_ids", "unresolved_gate_ids", "effect_capability_refs",
        "projection_versions", "projection_digest", "observed_at", "snapshot_hash",
    }
)
_BINDING_FIELDS = frozenset(
    {"schema_id", "mode", "open_work_digest", "round7_digest", "binding_hash"}
)
_COMPARE_FIELDS = frozenset(
    {"schema_id", "definition_snapshot_ref", "source_binding", "projection_versions", "snapshot_hash"}
)
_ERROR_CODES = frozenset(
    {
        "budget_exceeded", "invalid_document", "invalid_fields", "invalid_hash",
        "invalid_integer", "invalid_type", "non_canonical_document",
        "operation_failed", "snapshot_mismatch", "source_binding_mismatch",
        "stale_authority", "wrong_schema",
    }
)


class ExecutionAuthoritySnapshotError(ValueError):
    """A fresh, code-only R3 snapshot boundary failure."""

    def __init__(self, code: str) -> None:
        checked = code if type(code) is str and code in _ERROR_CODES else "operation_failed"
        self.code = checked
        super().__init__(checked)


def _fail(code: str) -> None:
    raise ExecutionAuthoritySnapshotError(code)


def _public(operation: _Any) -> _Any:
    try:
        return operation()
    except ExecutionAuthoritySnapshotError as error:
        code = error.code
    except Exception:
        code = "operation_failed"
    failure = ExecutionAuthoritySnapshotError(code)
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


def _framed_bytes_hash(domain: str, raw: bytes) -> str:
    frame = domain.encode("ascii") + b"\0" + len(raw).to_bytes(8, "big") + raw
    return "sha256:" + _hashlib.sha256(frame).hexdigest()


def _capture(value: _Any, *, depth: int = 0, state: dict[str, int] | None = None) -> _Any:
    if state is None:
        state = {"nodes": 0}
    state["nodes"] += 1
    if state["nodes"] > _MAX_NODES or depth > _MAX_DEPTH:
        _fail("budget_exceeded")
    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        if len(value.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
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
    if len(value) > MAX_SNAPSHOT_BYTES:
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


def _hash(value: _Any) -> str:
    if type(value) is not str or _HASH_RE.fullmatch(value) is None:
        _fail("invalid_hash")
    return value


def _identifier(value: _Any) -> str:
    if (
        type(value) is not str
        or len(value) > MAX_IDENTIFIER_CHARS
        or value != _unicodedata.normalize("NFC", value)
        or _ID_RE.fullmatch(value) is None
    ):
        _fail("invalid_type")
    return value


def _timestamp(value: _Any) -> str:
    if type(value) is not str or _TIMESTAMP_RE.fullmatch(value) is None:
        _fail("stale_authority")
    try:
        parsed = _datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("stale_authority")
    if parsed.utcoffset() is None:
        _fail("stale_authority")
    return value


def _definition(value: _Any) -> dict[str, _Any]:
    if type(value) is not dict:
        _fail("invalid_type")
    captured = _capture(value)
    try:
        result = _validate_definition_reference(captured)
    except Exception:
        _fail("stale_authority")
    return _capture(result)


def _trust_reference(
    receipt: _Any,
    definition: dict[str, _Any],
    expected_receipt_hash: _Any,
    expected_authority_hash: _Any,
    observed_at: _Any,
) -> dict[str, _Any]:
    if type(receipt) is not bytes:
        _fail("invalid_type")
    checked_receipt_hash = _hash(expected_receipt_hash)
    checked_authority_hash = _hash(expected_authority_hash)
    checked_observed = _timestamp(observed_at)
    try:
        result = _execution_trust_reference(
            receipt,
            expected_receipt_hash=checked_receipt_hash,
            expected_authority_hash=checked_authority_hash,
            expected_definition_snapshot_hash=definition["snapshot_hash"],
            observed_at=checked_observed,
        )
    except Exception:
        _fail("stale_authority")
    return _capture(result)


def _projection_versions(value: _Any) -> dict[str, int]:
    if type(value) is not dict:
        _fail("invalid_type")
    if not value:
        _fail("invalid_fields")
    if len(value) > MAX_PROJECTION_VERSIONS:
        _fail("budget_exceeded")
    result: dict[str, int] = {}
    for key, item in dict.items(value):
        checked_key = _identifier(key)
        if type(item) is not int or item < 1 or item > 2_147_483_647:
            _fail("invalid_integer")
        result[checked_key] = item
    if list(result) != sorted(result) or len(result) != len(set(result)):
        _fail("invalid_fields")
    return result


def _opaque_source(value: _Any) -> bytes:
    if type(value) is not bytes:
        _fail("invalid_type")
    if len(value) > MAX_COMPARE_SOURCE_BYTES_EACH:
        _fail("budget_exceeded")
    return value


def _binding(open_work_bytes: bytes, round7_bytes: bytes) -> dict[str, str]:
    unsigned = {
        "schema_id": COMPARE_ONLY_AUTHORITY_SOURCE_BINDING_SCHEMA_ID,
        "mode": "compare_only",
        "open_work_digest": _framed_bytes_hash(_OPEN_WORK_DOMAIN, open_work_bytes),
        "round7_digest": _framed_bytes_hash(_ROUND7_DOMAIN, round7_bytes),
    }
    return {**unsigned, "binding_hash": _framed_hash(_BINDING_DOMAIN, unsigned)}


def _validate_binding(value: _Any) -> dict[str, str]:
    document = _exact_fields(_load_document(value), _BINDING_FIELDS)
    if document["schema_id"] != COMPARE_ONLY_AUTHORITY_SOURCE_BINDING_SCHEMA_ID:
        _fail("wrong_schema")
    if document["mode"] != "compare_only":
        _fail("invalid_fields")
    unsigned = {key: item for key, item in document.items() if key != "binding_hash"}
    _hash(document["open_work_digest"])
    _hash(document["round7_digest"])
    if _hash(document["binding_hash"]) != _framed_hash(_BINDING_DOMAIN, unsigned):
        _fail("invalid_hash")
    return _capture(document)


def _native_snapshot(
    definition_snapshot_ref: _Any,
    ledger_bytes: _Any,
    trust_receipt: _Any,
    *,
    expected_trust_receipt_hash: _Any,
    expected_trust_authority_hash: _Any,
    projection_versions: _Any,
    observed_at: _Any,
) -> bytes:
    definition = _definition(definition_snapshot_ref)
    versions = _projection_versions(projection_versions)
    observed = _timestamp(observed_at)
    trust_reference = _trust_reference(
        trust_receipt,
        definition,
        expected_trust_receipt_hash,
        expected_trust_authority_hash,
        observed,
    )
    try:
        projection = _project_ledger(
            ledger_bytes,
            definition_snapshot_ref=definition,
            trust_receipt=trust_receipt,
            expected_trust_receipt_hash=expected_trust_receipt_hash,
            expected_trust_authority_hash=expected_trust_authority_hash,
            observed_at=observed,
        )
    except _LedgerError as error:
        _fail("stale_authority" if error.code == "stale_authority" else "invalid_document")
    except Exception:
        _fail("invalid_document")
    checked_projection = _capture(projection)
    unsigned = {
        "schema_id": EXECUTION_AUTHORITY_SNAPSHOT_SCHEMA_ID,
        "definition_snapshot_ref": definition,
        "execution_trust_authority_ref": trust_reference,
        "ledger_ref": checked_projection["ledger_ref"],
        "active_claims": checked_projection["active_claims"],
        "accepted_releases": checked_projection["accepted_releases"],
        "dispatch_ready_claim_ids": checked_projection["dispatch_ready_claim_ids"],
        "unresolved_gate_ids": checked_projection["unresolved_gate_ids"],
        "effect_capability_refs": checked_projection["effect_capability_refs"],
        "projection_versions": versions,
        "projection_digest": _framed_hash(_PROJECTION_DOMAIN, checked_projection),
        "observed_at": observed,
    }
    result = _canonical_bytes({**unsigned, "snapshot_hash": _framed_hash(_NATIVE_SNAPSHOT_DOMAIN, unsigned)})
    if len(result) > MAX_SNAPSHOT_BYTES:
        _fail("budget_exceeded")
    return result


def _compare_snapshot(
    definition_snapshot_ref: _Any,
    binding_bytes: _Any,
    *,
    projection_versions: _Any,
) -> bytes:
    definition = _definition(definition_snapshot_ref)
    binding = _validate_binding(binding_bytes)
    versions = _projection_versions(projection_versions)
    unsigned = {
        "schema_id": COMPARE_ONLY_AUTHORITY_SNAPSHOT_SCHEMA_ID,
        "definition_snapshot_ref": definition,
        "source_binding": binding,
        "projection_versions": versions,
    }
    result = _canonical_bytes({**unsigned, "snapshot_hash": _framed_hash(_COMPARE_SNAPSHOT_DOMAIN, unsigned)})
    if len(result) > MAX_SNAPSHOT_BYTES:
        _fail("budget_exceeded")
    return result


def build_execution_authority_snapshot(
    definition_snapshot_ref,
    ledger_bytes,
    trust_receipt,
    *,
    expected_trust_receipt_hash,
    expected_trust_authority_hash,
    projection_versions,
    observed_at,
) -> bytes:
    return _public(
        lambda: _native_snapshot(
            definition_snapshot_ref,
            ledger_bytes,
            trust_receipt,
            expected_trust_receipt_hash=expected_trust_receipt_hash,
            expected_trust_authority_hash=expected_trust_authority_hash,
            projection_versions=projection_versions,
            observed_at=observed_at,
        )
    )


def validate_execution_authority_snapshot(
    value,
    *,
    definition_snapshot_ref,
    ledger_bytes,
    trust_receipt,
    expected_trust_receipt_hash,
    expected_trust_authority_hash,
    projection_versions,
    observed_at,
) -> bytes:
    def operation() -> bytes:
        supplied = _load_document(value)
        if supplied.get("schema_id") != EXECUTION_AUTHORITY_SNAPSHOT_SCHEMA_ID:
            _fail("wrong_schema")
        _exact_fields(supplied, _NATIVE_FIELDS)
        expected = _native_snapshot(
            definition_snapshot_ref,
            ledger_bytes,
            trust_receipt,
            expected_trust_receipt_hash=expected_trust_receipt_hash,
            expected_trust_authority_hash=expected_trust_authority_hash,
            projection_versions=projection_versions,
            observed_at=observed_at,
        )
        if value != expected:
            _fail("snapshot_mismatch")
        return value

    return _public(operation)


def bind_compare_only_authority_sources(open_work_bytes, round7_bytes) -> bytes:
    def operation() -> bytes:
        open_work = _opaque_source(open_work_bytes)
        round7 = _opaque_source(round7_bytes)
        return _canonical_bytes(_binding(open_work, round7))

    return _public(operation)


def build_compare_only_authority_snapshot(
    definition_snapshot_ref,
    binding_bytes,
    *,
    projection_versions,
) -> bytes:
    return _public(
        lambda: _compare_snapshot(
            definition_snapshot_ref,
            binding_bytes,
            projection_versions=projection_versions,
        )
    )


def validate_compare_only_authority_snapshot(
    value,
    *,
    definition_snapshot_ref,
    open_work_bytes,
    round7_bytes,
    projection_versions,
) -> bytes:
    def operation() -> bytes:
        supplied = _load_document(value)
        if supplied.get("schema_id") != COMPARE_ONLY_AUTHORITY_SNAPSHOT_SCHEMA_ID:
            _fail("wrong_schema")
        _exact_fields(supplied, _COMPARE_FIELDS)
        open_work = _opaque_source(open_work_bytes)
        round7 = _opaque_source(round7_bytes)
        expected_binding = _binding(open_work, round7)
        if supplied["source_binding"] != expected_binding:
            _fail("source_binding_mismatch")
        binding_bytes = _canonical_bytes(expected_binding)
        expected = _compare_snapshot(
            definition_snapshot_ref,
            binding_bytes,
            projection_versions=projection_versions,
        )
        if value != expected:
            _fail("snapshot_mismatch")
        return value

    return _public(operation)


__all__ = [
    "COMPARE_ONLY_AUTHORITY_SNAPSHOT_SCHEMA_ID",
    "COMPARE_ONLY_AUTHORITY_SOURCE_BINDING_SCHEMA_ID",
    "EXECUTION_AUTHORITY_SNAPSHOT_SCHEMA_ID",
    "MAX_COMPARE_SOURCE_BYTES_EACH",
    "MAX_IDENTIFIER_CHARS",
    "MAX_JSON_INTEGER_TOKEN_DIGITS",
    "MAX_PROJECTION_VERSIONS",
    "MAX_SNAPSHOT_BYTES",
    "ExecutionAuthoritySnapshotError",
    "bind_compare_only_authority_sources",
    "build_compare_only_authority_snapshot",
    "build_execution_authority_snapshot",
    "validate_compare_only_authority_snapshot",
    "validate_execution_authority_snapshot",
]
