"""Evidence-only, default-off code indexing policy observations.

This module consumes only an explicitly supplied immutable Forge inventory.
It performs no repository selection, IO, Git access, indexing, or exclusion.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
import unicodedata
from typing import Any

from src.repo_git_adapter import (
    ForgeSnapshotAuthorityBinding,
    ForgeSnapshotFile,
    ForgeSnapshotInventory,
)


POLICY_FILE_DECISION_SCHEMA = "odysseus.usi.code_policy_file_decision.v1"
POLICY_OBSERVATION_SCHEMA = "odysseus.usi.code_policy_observation.v1"

MAX_CANONICAL_PAYLOAD_BYTES = 1_000_000
MAX_POLICY_DECISIONS = 512
MAX_POLICY_FILE_BYTES = 16_777_216
MAX_JSON_DEPTH = 6
MAX_JSON_NODES = 8_192
MAX_PATH_CHARS = 1_024
MAX_POLICY_GENERATION_CHARS = 128
MAX_TOTAL_STRING_CHARS = 750_000

_ERROR_CODES = frozenset(
    {"budget_exceeded", "invalid_decision", "invalid_observation", "invalid_payload"}
)
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_POLICY_GENERATION_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_WINDOWS_INVALID_PATH_CHARS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)
_DECISION_FIELDS = {
    "schema",
    "path",
    "content_sha256",
    "byte_count",
    "decision",
    "evidence_ref",
}
_AUTHORITY_FIELDS = {
    "adapter_id",
    "adapter_version",
    "adapter_generation",
    "admission_policy_generation",
}
_OBSERVATION_FIELDS = {
    "schema",
    "owner_scope",
    "repo_id",
    "version_id",
    "commit_sha",
    "manifest_sha256",
    "snapshot_digest",
    "authority_binding",
    "indexing_policy_generation",
    "policy_evidence_ref",
    "decisions",
    "observation_digest",
}


class CodePolicyObservationError(ValueError):
    """Bounded, content-free public exception."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        safe = code if type(code) is str and code in _ERROR_CODES else "invalid_observation"
        self.code = safe
        super().__init__(safe)

    def __str__(self) -> str:
        try:
            code = object.__getattribute__(self, "code")
        except BaseException:
            code = "invalid_observation"
        if type(code) is not str or code not in _ERROR_CODES:
            code = "invalid_observation"
        return code

    def __repr__(self) -> str:
        try:
            code = object.__getattribute__(self, "code")
        except BaseException:
            code = "invalid_observation"
        if type(code) is not str or code not in _ERROR_CODES:
            code = "invalid_observation"
        return f"CodePolicyObservationError(code={code!r})"


class PolicyDecision(StrEnum):
    IN_SCOPE = "in_scope"
    POLICY_OUT_OF_SCOPE = "policy_out_of_scope"


@dataclass(frozen=True, slots=True, repr=False)
class PolicyFileDecision:
    path: str
    content_sha256: str
    byte_count: int
    decision: PolicyDecision
    evidence_ref: str

    @property
    def schema(self) -> str:
        """The schema is fixed and intentionally not caller-configurable."""
        return POLICY_FILE_DECISION_SCHEMA

    def __post_init__(self) -> None:
        try:
            values = _capture_decision(self)
            _validate_decision_values(values)
            for name, value in zip(
                ("path", "content_sha256", "byte_count", "decision", "evidence_ref"),
                values,
                strict=True,
            ):
                object.__setattr__(self, name, value)
        except Exception:
            raise CodePolicyObservationError("invalid_decision") from None

    def to_dict(self) -> dict[str, object]:
        try:
            values = _capture_decision(self)
            _validate_decision_values(values)
            return _decision_projection(values)
        except Exception:
            raise CodePolicyObservationError("invalid_decision") from None

    def __repr__(self) -> str:
        try:
            decision = object.__getattribute__(self, "decision")
        except BaseException:
            return "PolicyFileDecision(invalid)"
        if decision is PolicyDecision.IN_SCOPE:
            value = "in_scope"
        elif decision is PolicyDecision.POLICY_OUT_OF_SCOPE:
            value = "policy_out_of_scope"
        else:
            return "PolicyFileDecision(invalid)"
        return f"PolicyFileDecision(decision={value!r})"


class PolicyObservation:
    """An immutable semantic snapshot with detached public projections."""

    __slots__ = ("_snapshot", "_observation_digest")

    def __init__(self) -> None:
        raise TypeError("use a PolicyObservation constructor")

    @classmethod
    def from_inventory(
        cls,
        inventory: ForgeSnapshotInventory,
        indexing_policy_generation: str,
        policy_evidence_ref: str,
        decisions: tuple[PolicyFileDecision, ...],
    ) -> "PolicyObservation":
        try:
            detached_inventory = _detach_inventory(inventory)
            if (
                type(indexing_policy_generation) is not str
                or not _POLICY_GENERATION_RE.fullmatch(indexing_policy_generation)
                or type(policy_evidence_ref) is not str
                or not _SHA256_RE.fullmatch(policy_evidence_ref)
                or type(decisions) is not tuple
                or len(decisions) > MAX_POLICY_DECISIONS
                or any(type(item) is not PolicyFileDecision for item in decisions)
            ):
                raise ValueError
            detached_decisions = tuple(_detach_decision(item) for item in decisions)
            detached_decisions = tuple(sorted(detached_decisions, key=lambda item: item.path))
            _validate_bijection(detached_inventory, detached_decisions)
            return cls._from_detached(
                inventory=detached_inventory,
                indexing_policy_generation=indexing_policy_generation,
                policy_evidence_ref=policy_evidence_ref,
                decisions=detached_decisions,
                supplied_digest="",
            )
        except Exception:
            raise CodePolicyObservationError("invalid_observation") from None

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "PolicyObservation":
        try:
            if type(payload) is not bytes:
                raise _InvalidPayload
            if len(payload) > MAX_CANONICAL_PAYLOAD_BYTES:
                raise _BudgetExceeded
            value = _decode_json(payload)
            _enforce_json_budget(value)
            if _canonical_bytes(value) != payload:
                raise _InvalidPayload
            return cls._from_payload(value)
        except _BudgetExceeded:
            raise CodePolicyObservationError("budget_exceeded") from None
        except Exception:
            raise CodePolicyObservationError("invalid_payload") from None

    @classmethod
    def _from_payload(cls, value: object) -> "PolicyObservation":
        if type(value) is not dict or set(value) != _OBSERVATION_FIELDS:
            raise _InvalidPayload
        authority_data = value["authority_binding"]
        decisions_data = value["decisions"]
        if (
            type(authority_data) is not dict
            or set(authority_data) != _AUTHORITY_FIELDS
            or type(decisions_data) is not list
        ):
            raise _InvalidPayload
        if len(decisions_data) > MAX_POLICY_DECISIONS:
            raise _BudgetExceeded
        policy_generation = value["indexing_policy_generation"]
        if (
            type(policy_generation) is str
            and len(policy_generation) > MAX_POLICY_GENERATION_CHARS
        ):
            raise _BudgetExceeded
        decisions: list[PolicyFileDecision] = []
        files: list[ForgeSnapshotFile] = []
        for item in decisions_data:
            if type(item) is not dict or set(item) != _DECISION_FIELDS:
                raise _InvalidPayload
            path = item["path"]
            byte_count = item["byte_count"]
            if type(path) is str and len(path) > MAX_PATH_CHARS:
                raise _BudgetExceeded
            if type(byte_count) is int and byte_count > MAX_POLICY_FILE_BYTES:
                raise _BudgetExceeded
            try:
                decision = PolicyFileDecision(
                    path,
                    item["content_sha256"],
                    byte_count,
                    PolicyDecision(item["decision"]),
                    item["evidence_ref"],
                )
                if item["schema"] != POLICY_FILE_DECISION_SCHEMA:
                    raise ValueError
                file = ForgeSnapshotFile(
                    decision.path, decision.content_sha256, decision.byte_count
                )
            except Exception:
                raise _InvalidPayload from None
            decisions.append(decision)
            files.append(file)
        try:
            authority = ForgeSnapshotAuthorityBinding(
                authority_data["adapter_id"],
                authority_data["adapter_version"],
                authority_data["adapter_generation"],
                authority_data["admission_policy_generation"],
            )
            inventory = ForgeSnapshotInventory(
                value["owner_scope"],
                value["repo_id"],
                value["version_id"],
                value["commit_sha"],
                value["manifest_sha256"],
                authority,
                tuple(files),
                value["snapshot_digest"],
            )
            result = cls._from_detached(
                inventory=inventory,
                indexing_policy_generation=policy_generation,
                policy_evidence_ref=value["policy_evidence_ref"],
                decisions=tuple(decisions),
                supplied_digest=value["observation_digest"],
            )
        except _BudgetExceeded:
            raise
        except Exception:
            raise _InvalidPayload from None
        return result

    @classmethod
    def _from_detached(
        cls,
        *,
        inventory: ForgeSnapshotInventory,
        indexing_policy_generation: str,
        policy_evidence_ref: str,
        decisions: tuple[PolicyFileDecision, ...],
        supplied_digest: str,
    ) -> "PolicyObservation":
        if (
            type(inventory) is not ForgeSnapshotInventory
            or type(inventory.authority_binding) is not ForgeSnapshotAuthorityBinding
            or type(indexing_policy_generation) is not str
            or not _POLICY_GENERATION_RE.fullmatch(indexing_policy_generation)
            or type(policy_evidence_ref) is not str
            or not _SHA256_RE.fullmatch(policy_evidence_ref)
            or type(decisions) is not tuple
            or len(decisions) > MAX_POLICY_DECISIONS
            or any(type(item) is not PolicyFileDecision for item in decisions)
            or type(supplied_digest) is not str
            or (supplied_digest and not _SHA256_RE.fullmatch(supplied_digest))
        ):
            raise ValueError
        _validate_bijection(inventory, decisions)
        snapshot = _snapshot_from_detached(
            inventory, indexing_policy_generation, policy_evidence_ref, decisions
        )
        projection = _snapshot_projection(snapshot, include_digest=False, digest=None)
        _enforce_json_budget(projection)
        encoded = _canonical_bytes(projection)
        if len(encoded) > MAX_CANONICAL_PAYLOAD_BYTES:
            raise _BudgetExceeded
        digest = "sha256:" + hashlib.sha256(encoded).hexdigest()
        if supplied_digest and supplied_digest != digest:
            raise ValueError
        result = object.__new__(cls)
        object.__setattr__(result, "_snapshot", snapshot)
        object.__setattr__(result, "_observation_digest", digest)
        return result

    def _projection(self, *, include_digest: bool) -> dict[str, object]:
        snapshot, digest = self._validated_snapshot()
        return _snapshot_projection(snapshot, include_digest=include_digest, digest=digest)

    def _validated_snapshot(self) -> tuple[tuple[object, ...], str]:
        snapshot = object.__getattribute__(self, "_snapshot")
        digest = object.__getattribute__(self, "_observation_digest")
        if type(digest) is not str:
            raise ValueError
        projection = _snapshot_projection(snapshot, include_digest=False, digest=None)
        _enforce_json_budget(projection)
        encoded = _canonical_bytes(projection)
        if (
            len(encoded) > MAX_CANONICAL_PAYLOAD_BYTES
            or digest != "sha256:" + hashlib.sha256(encoded).hexdigest()
        ):
            raise ValueError
        return snapshot, digest

    def _public_snapshot(self) -> tuple[tuple[object, ...], str]:
        try:
            return self._validated_snapshot()
        except Exception:
            raise CodePolicyObservationError("invalid_observation") from None

    @property
    def schema(self) -> str:
        self._public_snapshot()
        return POLICY_OBSERVATION_SCHEMA

    @property
    def owner_scope(self) -> str:
        return self._public_snapshot()[0][0]  # type: ignore[return-value]

    @property
    def repo_id(self) -> str:
        return self._public_snapshot()[0][1]  # type: ignore[return-value]

    @property
    def version_id(self) -> str:
        return self._public_snapshot()[0][2]  # type: ignore[return-value]

    @property
    def commit_sha(self) -> str:
        return self._public_snapshot()[0][3]  # type: ignore[return-value]

    @property
    def manifest_sha256(self) -> str:
        return self._public_snapshot()[0][4]  # type: ignore[return-value]

    @property
    def snapshot_digest(self) -> str:
        return self._public_snapshot()[0][5]  # type: ignore[return-value]

    @property
    def authority_binding(self) -> ForgeSnapshotAuthorityBinding:
        snapshot, _ = self._public_snapshot()
        authority = snapshot[6]
        assert type(authority) is tuple
        return ForgeSnapshotAuthorityBinding(*authority)

    @property
    def indexing_policy_generation(self) -> str:
        return self._public_snapshot()[0][7]  # type: ignore[return-value]

    @property
    def policy_evidence_ref(self) -> str:
        return self._public_snapshot()[0][8]  # type: ignore[return-value]

    @property
    def decisions(self) -> tuple[PolicyFileDecision, ...]:
        snapshot, _ = self._public_snapshot()
        records = snapshot[9]
        assert type(records) is tuple
        return tuple(
            PolicyFileDecision(record[0], record[1], record[2], PolicyDecision(record[3]), record[4])
            for record in records
        )

    @property
    def observation_digest(self) -> str:
        return self._public_snapshot()[1]

    def to_dict(self) -> dict[str, object]:
        try:
            projection = self._projection(include_digest=True)
            _enforce_json_budget(projection)
            return projection
        except Exception:
            raise CodePolicyObservationError("invalid_observation") from None

    def to_canonical_bytes(self) -> bytes:
        try:
            projection = self._projection(include_digest=True)
            _enforce_json_budget(projection)
            encoded = _canonical_bytes(projection)
            if len(encoded) > MAX_CANONICAL_PAYLOAD_BYTES:
                raise ValueError
            return encoded
        except Exception:
            raise CodePolicyObservationError("invalid_observation") from None

    def __repr__(self) -> str:
        try:
            _, digest = self._validated_snapshot()
        except BaseException:
            return "PolicyObservation(invalid)"
        return f"PolicyObservation(observation_digest={digest!r})"

    def __eq__(self, other: object) -> bool:
        if type(other) is not PolicyObservation:
            return NotImplemented
        try:
            return self._validated_snapshot() == other._validated_snapshot()
        except Exception:
            return False

    def __hash__(self) -> int:
        snapshot, digest = self._public_snapshot()
        return hash((snapshot, digest))


class _InvalidPayload(Exception):
    pass


class _BudgetExceeded(Exception):
    pass


def _capture_decision(
    decision: object,
) -> tuple[str, str, int, PolicyDecision, str]:
    if type(decision) is not PolicyFileDecision:
        raise ValueError
    return (
        decision.path,
        decision.content_sha256,
        decision.byte_count,
        decision.decision,
        decision.evidence_ref,
    )


def _validate_decision_values(
    values: tuple[str, str, int, PolicyDecision, str],
) -> None:
    path, content_sha256, byte_count, decision, evidence_ref = values
    if (
        type(path) is not str
        or _canonical_path(path) != path
        or type(content_sha256) is not str
        or not _SHA256_RE.fullmatch(content_sha256)
        or type(byte_count) is not int
        or not 0 <= byte_count <= MAX_POLICY_FILE_BYTES
        or type(decision) is not PolicyDecision
        or type(evidence_ref) is not str
        or not _SHA256_RE.fullmatch(evidence_ref)
    ):
        raise ValueError


def _decision_projection(
    values: tuple[str, str, int, PolicyDecision, str],
) -> dict[str, object]:
    return {
        "schema": POLICY_FILE_DECISION_SCHEMA,
        "path": values[0],
        "content_sha256": values[1],
        "byte_count": values[2],
        "decision": values[3].value,
        "evidence_ref": values[4],
    }


def _detach_decision(value: object) -> PolicyFileDecision:
    values = _capture_decision(value)
    _validate_decision_values(values)
    return PolicyFileDecision(*values)


def _detach_inventory(value: object) -> ForgeSnapshotInventory:
    if type(value) is not ForgeSnapshotInventory:
        raise ValueError
    owner_scope = value.owner_scope
    repo_id = value.repo_id
    version_id = value.version_id
    commit_sha = value.commit_sha
    manifest_sha256 = value.manifest_sha256
    authority = value.authority_binding
    files = value.files
    snapshot_digest = value.snapshot_digest
    if (
        type(owner_scope) is not str
        or type(repo_id) is not str
        or type(version_id) is not str
        or type(commit_sha) is not str
        or type(manifest_sha256) is not str
        or type(snapshot_digest) is not str
        or type(authority) is not ForgeSnapshotAuthorityBinding
        or type(files) is not tuple
        or len(files) > MAX_POLICY_DECISIONS
        or any(type(item) is not ForgeSnapshotFile for item in files)
    ):
        raise ValueError
    adapter_id = authority.adapter_id
    adapter_version = authority.adapter_version
    adapter_generation = authority.adapter_generation
    admission_policy_generation = authority.admission_policy_generation
    if any(
        type(item) is not str
        for item in (
            adapter_id,
            adapter_version,
            adapter_generation,
            admission_policy_generation,
        )
    ):
        raise ValueError
    captured_files: list[tuple[str, str, int]] = []
    for item in files:
        fields = (item.path, item.content_sha256, item.byte_count)
        if (
            type(fields[0]) is not str
            or type(fields[1]) is not str
            or type(fields[2]) is not int
            or fields[2] > MAX_POLICY_FILE_BYTES
        ):
            raise ValueError
        captured_files.append(fields)
    detached_authority = ForgeSnapshotAuthorityBinding(
        adapter_id,
        adapter_version,
        adapter_generation,
        admission_policy_generation,
    )
    detached_files = tuple(ForgeSnapshotFile(*fields) for fields in captured_files)
    return ForgeSnapshotInventory(
        owner_scope,
        repo_id,
        version_id,
        commit_sha,
        manifest_sha256,
        detached_authority,
        detached_files,
        snapshot_digest,
    )


def _snapshot_from_detached(
    inventory: ForgeSnapshotInventory,
    indexing_policy_generation: str,
    policy_evidence_ref: str,
    decisions: tuple[PolicyFileDecision, ...],
) -> tuple[object, ...]:
    """Capture only exact built-in scalars; retain no caller-owned objects."""
    authority = inventory.authority_binding
    return (
        inventory.owner_scope,
        inventory.repo_id,
        inventory.version_id,
        inventory.commit_sha,
        inventory.manifest_sha256,
        inventory.snapshot_digest,
        (
            authority.adapter_id,
            authority.adapter_version,
            authority.adapter_generation,
            authority.admission_policy_generation,
        ),
        indexing_policy_generation,
        policy_evidence_ref,
        tuple(
            (
                decision.path,
                decision.content_sha256,
                decision.byte_count,
                decision.decision.value,
                decision.evidence_ref,
            )
            for decision in decisions
        ),
    )


def _snapshot_projection(
    snapshot: object,
    *,
    include_digest: bool,
    digest: str | None,
) -> dict[str, object]:
    """Validate a private snapshot before deriving any public projection."""
    if type(snapshot) is not tuple or len(snapshot) != 10:
        raise ValueError
    (
        owner_scope,
        repo_id,
        version_id,
        commit_sha,
        manifest_sha256,
        snapshot_digest,
        authority_data,
        indexing_policy_generation,
        policy_evidence_ref,
        decision_records,
    ) = snapshot
    if (
        any(
            type(value) is not str
            for value in (
                owner_scope,
                repo_id,
                version_id,
                commit_sha,
                manifest_sha256,
                snapshot_digest,
                indexing_policy_generation,
                policy_evidence_ref,
            )
        )
        or type(authority_data) is not tuple
        or len(authority_data) != 4
        or any(type(value) is not str for value in authority_data)
        or type(decision_records) is not tuple
        or len(decision_records) > MAX_POLICY_DECISIONS
        or not _POLICY_GENERATION_RE.fullmatch(indexing_policy_generation)
        or not _SHA256_RE.fullmatch(policy_evidence_ref)
    ):
        raise ValueError
    decisions: list[PolicyFileDecision] = []
    files: list[ForgeSnapshotFile] = []
    for record in decision_records:
        if (
            type(record) is not tuple
            or len(record) != 5
            or type(record[0]) is not str
            or type(record[1]) is not str
            or type(record[2]) is not int
            or type(record[3]) is not str
            or type(record[4]) is not str
        ):
            raise ValueError
        decision = PolicyFileDecision(
            record[0], record[1], record[2], PolicyDecision(record[3]), record[4]
        )
        decisions.append(decision)
        files.append(ForgeSnapshotFile(decision.path, decision.content_sha256, decision.byte_count))
    authority = ForgeSnapshotAuthorityBinding(*authority_data)
    inventory = ForgeSnapshotInventory(
        owner_scope,
        repo_id,
        version_id,
        commit_sha,
        manifest_sha256,
        authority,
        tuple(files),
        snapshot_digest,
    )
    _validate_bijection(inventory, tuple(decisions))
    result: dict[str, object] = {
        "schema": POLICY_OBSERVATION_SCHEMA,
        "owner_scope": owner_scope,
        "repo_id": repo_id,
        "version_id": version_id,
        "commit_sha": commit_sha,
        "manifest_sha256": manifest_sha256,
        "snapshot_digest": snapshot_digest,
        "authority_binding": {
            "adapter_id": authority.adapter_id,
            "adapter_version": authority.adapter_version,
            "adapter_generation": authority.adapter_generation,
            "admission_policy_generation": authority.admission_policy_generation,
        },
        "indexing_policy_generation": indexing_policy_generation,
        "policy_evidence_ref": policy_evidence_ref,
        "decisions": [decision.to_dict() for decision in decisions],
    }
    if include_digest:
        if type(digest) is not str or not _SHA256_RE.fullmatch(digest):
            raise ValueError
        result["observation_digest"] = digest
    return result


def _validate_bijection(
    inventory: ForgeSnapshotInventory,
    decisions: tuple[PolicyFileDecision, ...],
) -> None:
    if len(inventory.files) != len(decisions):
        raise ValueError
    seen: set[str] = set()
    for file, decision in zip(inventory.files, decisions, strict=True):
        key = unicodedata.normalize("NFC", decision.path).casefold()
        if (
            key in seen
            or decision.path != file.path
            or decision.content_sha256 != file.content_sha256
            or decision.byte_count != file.byte_count
        ):
            raise ValueError
        seen.add(key)


def _canonical_path(value: str) -> str:
    if not value or len(value) > MAX_PATH_CHARS:
        raise ValueError
    normalized = unicodedata.normalize("NFC", value)
    if normalized != value or any(ord(character) < 32 for character in value):
        raise ValueError
    if "\\" in value or value.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", value):
        raise ValueError
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError
    for part in parts:
        if (
            any(character in _WINDOWS_INVALID_PATH_CHARS for character in part)
            or part.endswith((".", " "))
            or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
        ):
            raise ValueError
    value.encode("utf-8", errors="strict")
    return value


def _decode_json(payload: bytes) -> object:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if type(key) is not str or key in result:
                raise _InvalidPayload
            result[key] = value
        return result

    try:
        return json.loads(
            payload.decode("utf-8", errors="strict"),
            object_pairs_hook=pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(_InvalidPayload()),
        )
    except Exception:
        raise _InvalidPayload from None


def _enforce_json_budget(value: object) -> None:
    state = {"nodes": 0, "strings": 0}

    def visit(item: object, depth: int) -> None:
        state["nodes"] += 1
        if depth > MAX_JSON_DEPTH or state["nodes"] > MAX_JSON_NODES:
            raise _BudgetExceeded
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise _InvalidPayload
                state["strings"] += len(key)
                visit(child, depth + 1)
        elif type(item) is list:
            for child in item:
                visit(child, depth + 1)
        elif type(item) is str:
            state["strings"] += len(item)
            item.encode("utf-8", errors="strict")
        elif type(item) is int:
            if not -(2**63) <= item <= 2**63 - 1:
                raise _BudgetExceeded
        elif type(item) in {bool, type(None)}:
            pass
        else:
            raise _InvalidPayload
        if state["strings"] > MAX_TOTAL_STRING_CHARS:
            raise _BudgetExceeded

    visit(value, 1)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")


__all__ = [
    "CodePolicyObservationError",
    "MAX_CANONICAL_PAYLOAD_BYTES",
    "MAX_JSON_DEPTH",
    "MAX_JSON_NODES",
    "MAX_PATH_CHARS",
    "MAX_POLICY_DECISIONS",
    "MAX_POLICY_FILE_BYTES",
    "MAX_POLICY_GENERATION_CHARS",
    "MAX_TOTAL_STRING_CHARS",
    "POLICY_FILE_DECISION_SCHEMA",
    "POLICY_OBSERVATION_SCHEMA",
    "PolicyDecision",
    "PolicyFileDecision",
    "PolicyObservation",
]
