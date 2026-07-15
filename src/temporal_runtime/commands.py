"""Deterministic Temporal Light command, receipt and Signal contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable, Mapping


COMMANDS = (
    "pause",
    "resume",
    "cancel",
    "retry_activity",
    "decide_gate",
    "steer_run",
)
GATE_DECISIONS = ("approved", "rejected", "expired", "waived")
STRUCTURAL_STEERING_FIELDS = frozenset(
    {
        "allowed_paths",
        "blocked_paths",
        "dag",
        "dependencies",
        "done_contract",
        "gate_definitions",
        "gates",
        "hotfiles",
        "normalized_dag",
        "scope",
    }
)
COMMAND_RECEIPT_SCHEMA_ID = "odysseus.temporal_light.command_receipt.v1"
MAX_COMMAND_RECEIPTS = 1_024
MAX_SIGNAL_RECORDS = 256
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FORBIDDEN_KEY_PARTS = (
    "access_token",
    "api_key",
    "credential",
    "password",
    "private_key",
    "provider_response",
    "raw_output",
    "secret",
)


class CommandContractError(ValueError):
    """Invalid or conflicting command with a stable reason code."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class CommandRequest:
    command_id: str
    command: str
    expected_run_version: int
    idempotency_key: str
    payload: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        command_id: Any,
        command: Any,
        expected_run_version: Any,
        idempotency_key: Any,
        payload: Mapping[str, Any] | None = None,
    ) -> "CommandRequest":
        normalized_command = str(command or "")
        if normalized_command not in COMMANDS:
            _fail("unknown_command", "command is not registered")
        version = _non_negative_int(expected_run_version, "expected_run_version")
        normalized_payload = _validate_payload(normalized_command, payload or {})
        return cls(
            command_id=_identifier(command_id, "command_id"),
            command=normalized_command,
            expected_run_version=version,
            idempotency_key=_identifier(idempotency_key, "idempotency_key"),
            payload=normalized_payload,
        )

    @property
    def binding_digest(self) -> str:
        encoded = _canonical_json(
            {
                "command": self.command,
                "command_id": self.command_id,
                "expected_run_version": self.expected_run_version,
                "idempotency_key": self.idempotency_key,
                "payload": self.payload,
            }
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def normalized(self) -> "CommandRequest":
        return CommandRequest.create(
            command_id=self.command_id,
            command=self.command,
            expected_run_version=self.expected_run_version,
            idempotency_key=self.idempotency_key,
            payload=self.payload,
        )


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    schema_id: str
    command_id: str
    idempotency_key: str
    command: str
    binding_digest: str
    accepted_run_version: int
    result_run_version: int
    result_code: str
    state: str

    @classmethod
    def create(
        cls,
        request: CommandRequest,
        *,
        result_run_version: int,
        result_code: str,
        state: str,
    ) -> "CommandReceipt":
        return cls(
            schema_id=COMMAND_RECEIPT_SCHEMA_ID,
            command_id=request.command_id,
            idempotency_key=request.idempotency_key,
            command=request.command,
            binding_digest=request.binding_digest,
            accepted_run_version=request.expected_run_version,
            result_run_version=_non_negative_int(result_run_version, "result_run_version"),
            result_code=_identifier(result_code, "result_code"),
            state=_identifier(state, "state"),
        )

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> "CommandReceipt":
        required = {
            "schema_id",
            "command_id",
            "idempotency_key",
            "command",
            "binding_digest",
            "accepted_run_version",
            "result_run_version",
            "result_code",
            "state",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            _fail("invalid_receipt", "command receipt fields are not exact")
        if value["schema_id"] != COMMAND_RECEIPT_SCHEMA_ID:
            _fail("invalid_receipt", "command receipt schema is unsupported")
        command = str(value["command"] or "")
        if command not in COMMANDS:
            _fail("invalid_receipt", "command receipt has an unknown command")
        digest = str(value["binding_digest"] or "")
        if not _HASH_RE.fullmatch(digest):
            _fail("invalid_receipt", "binding digest is invalid")
        return cls(
            schema_id=COMMAND_RECEIPT_SCHEMA_ID,
            command_id=_identifier(value["command_id"], "command_id"),
            idempotency_key=_identifier(value["idempotency_key"], "idempotency_key"),
            command=command,
            binding_digest=digest,
            accepted_run_version=_non_negative_int(
                value["accepted_run_version"], "accepted_run_version"
            ),
            result_run_version=_non_negative_int(
                value["result_run_version"], "result_run_version"
            ),
            result_code=_identifier(value["result_code"], "result_code"),
            state=_identifier(value["state"], "state"),
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_id": self.schema_id,
            "command_id": self.command_id,
            "idempotency_key": self.idempotency_key,
            "command": self.command,
            "binding_digest": self.binding_digest,
            "accepted_run_version": self.accepted_run_version,
            "result_run_version": self.result_run_version,
            "result_code": self.result_code,
            "state": self.state,
        }


class CommandLedger:
    """Bounded deterministic receipt index carried through Continue-As-New."""

    def __init__(self, receipts: Iterable[Mapping[str, Any]] = ()) -> None:
        self._receipts: list[CommandReceipt] = []
        self._by_command_id: dict[str, CommandReceipt] = {}
        self._by_idempotency_key: dict[str, CommandReceipt] = {}
        for payload in receipts:
            if len(self._receipts) >= MAX_COMMAND_RECEIPTS:
                _fail("invalid_receipt", "persisted command receipt capacity is exceeded")
            receipt = CommandReceipt.from_payload(payload)
            self._insert(receipt)

    def duplicate_for(self, request: CommandRequest) -> CommandReceipt | None:
        by_command = self._by_command_id.get(request.command_id)
        by_key = self._by_idempotency_key.get(request.idempotency_key)
        if by_command is None and by_key is None:
            return None
        if by_command is None or by_key is None or by_command != by_key:
            _fail("command_conflict", "command id and idempotency key were rebound")
        if by_command.binding_digest != request.binding_digest:
            _fail("command_conflict", "duplicate command payload differs")
        return by_command

    def ensure_capacity(self) -> None:
        if len(self._receipts) >= MAX_COMMAND_RECEIPTS:
            _fail("command_ledger_full", "command receipt capacity is exhausted")

    def record(self, receipt: CommandReceipt) -> CommandReceipt:
        if not isinstance(receipt, CommandReceipt):
            _fail("invalid_receipt", "CommandReceipt is required")
        self.ensure_capacity()
        self._insert(receipt)
        return receipt

    def export(self) -> tuple[dict[str, Any], ...]:
        return tuple(receipt.to_payload() for receipt in self._receipts)

    @property
    def count(self) -> int:
        return len(self._receipts)

    def _insert(self, receipt: CommandReceipt) -> None:
        if (
            receipt.command_id in self._by_command_id
            or receipt.idempotency_key in self._by_idempotency_key
        ):
            _fail("invalid_receipt", "persisted command receipt is duplicated")
        self._receipts.append(receipt)
        self._by_command_id[receipt.command_id] = receipt
        self._by_idempotency_key[receipt.idempotency_key] = receipt


@dataclass(frozen=True, slots=True)
class OperatorNoteSignal:
    note_id: str
    note_ref: str

    @classmethod
    def create(cls, *, note_id: Any, note_ref: Any) -> "OperatorNoteSignal":
        return cls(
            note_id=_identifier(note_id, "note_id"),
            note_ref=_identifier(note_ref, "note_ref"),
        )


@dataclass(frozen=True, slots=True)
class ExternalConditionSignal:
    condition_ref: str

    @classmethod
    def create(cls, *, condition_ref: Any) -> "ExternalConditionSignal":
        return cls(condition_ref=_identifier(condition_ref, "condition_ref"))


def is_structural_steering(request: CommandRequest) -> bool:
    return request.command == "steer_run" and bool(
        STRUCTURAL_STEERING_FIELDS.intersection(request.payload)
    )


def _validate_payload(command: str, value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("invalid_command_payload", "payload must be an object")
    payload = dict(value)
    if command in ("pause", "resume", "cancel"):
        if payload:
            _fail("invalid_command_payload", f"{command} payload must be empty")
        return {}
    if command == "retry_activity":
        if set(payload) != {"node_id"}:
            _fail("invalid_command_payload", "retry_activity requires node_id")
        return {"node_id": _identifier(payload["node_id"], "node_id")}
    if command == "decide_gate":
        if set(payload) != {"gate_id", "decision"}:
            _fail("invalid_command_payload", "decide_gate fields are not exact")
        decision = str(payload["decision"] or "")
        if decision not in GATE_DECISIONS:
            _fail("invalid_command_payload", "gate decision is invalid")
        return {
            "gate_id": _identifier(payload["gate_id"], "gate_id"),
            "decision": decision,
        }
    if command == "steer_run":
        if not payload or len(payload) > 16:
            _fail("invalid_command_payload", "steer_run payload must be bounded")
        structural = STRUCTURAL_STEERING_FIELDS.intersection(payload)
        if structural:
            _assert_bounded_json(payload)
            return payload
        if set(payload) != {"steering_ref"}:
            _fail("invalid_command_payload", "run-scoped steer requires steering_ref only")
        return {"steering_ref": _identifier(payload["steering_ref"], "steering_ref")}
    _fail("unknown_command", "command is not registered")


def _assert_bounded_json(value: Any, *, depth: int = 0) -> None:
    if depth > 4:
        _fail("invalid_command_payload", "structural payload is too deep")
    if isinstance(value, Mapping):
        if len(value) > 32:
            _fail("invalid_command_payload", "structural object is too large")
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _FORBIDDEN_KEY_PARTS):
                _fail("invalid_command_payload", "secret or raw field is forbidden")
            _identifier(key, "payload_key")
            _assert_bounded_json(item, depth=depth + 1)
        return
    if isinstance(value, (list, tuple)):
        if len(value) > 64:
            _fail("invalid_command_payload", "structural array is too large")
        for item in value:
            _assert_bounded_json(item, depth=depth + 1)
        return
    if value is None or type(value) in (bool, int):
        return
    if isinstance(value, str) and len(value) <= 512:
        if value.startswith(("/", "\\\\")) or re.match(r"^[A-Za-z]:[/\\]", value):
            _fail("invalid_command_payload", "absolute path is forbidden")
        return
    _fail("invalid_command_payload", "structural value is unsafe or too large")


def _identifier(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not _ID_RE.fullmatch(text) or ".." in text:
        _fail("invalid_identifier", field)
    return text


def _non_negative_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail("invalid_version", f"{field} must be a non-negative integer")
    return value


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _fail(code: str, detail: str) -> None:
    raise CommandContractError(code, detail)
