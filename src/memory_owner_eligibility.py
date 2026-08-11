"""Bounded, read-only owner eligibility snapshots for canonical Memory JSON.

The reader operates on one explicitly supplied ``memory.json`` path.  It does
not discover runtime data, construct a Memory service, or call a writer.  Only
records carrying a complete v1 eligibility stamp are returned; every legacy or
ambiguous record is counted as ineligible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType
from typing import Any, Mapping


MEMORY_ELIGIBILITY_SCHEMA = "odysseus.memory_eligibility.v1"
MEMORY_OWNER_ELIGIBILITY_SNAPSHOT_SCHEMA = (
    "odysseus.memory_owner_eligibility_snapshot.v1"
)

DEFAULT_MAX_SOURCE_BYTES = 8 * 1024 * 1024
HARD_MAX_SOURCE_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_RECORDS = 10_000
HARD_MAX_RECORDS = 25_000
DEFAULT_MAX_DEPTH = 24
HARD_MAX_DEPTH = 32
DEFAULT_MAX_JSON_NODES = 100_000
HARD_MAX_JSON_NODES = 250_000

_STAMP_KEYS = frozenset(
    {
        "schema",
        "source_status",
        "acceptance_status",
        "incognito",
        "policy_status",
        "policy_evidence_ref",
        "review_status",
        "review_evidence_ref",
    }
)
_SOURCE_STATUSES = frozenset({"active", "deleted"})
_ACCEPTANCE_STATUSES = frozenset({"accepted", "rejected"})
_POLICY_STATUSES = frozenset({"go", "review", "blocked"})
_REVIEW_STATUSES = frozenset({"accepted", "rejected", "pending", "not_required"})
_LEGACY_MEMORY_STATUSES_COMPATIBLE_WITH_ELIGIBILITY = frozenset(
    {
        "accepted",
        "active",
        "approved",
        "available",
        "current",
        "current_source_of_truth",
        "supporting_plan_source",
    }
)
_EVIDENCE_REF_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_WINDOWS_REPARSE_POINT_ATTRIBUTE = 0x400
_REJECTION_CODES = (
    "owner_mismatch",
    "legacy_or_unstamped",
    "invalid_record",
    "invalid_stamp",
    "contradictory_state",
    "inactive",
    "not_accepted",
    "incognito",
    "policy_not_go",
    "review_not_accepted",
)
_ERROR_CODES = frozenset(
    {
        "invalid_capture_request",
        "source_missing",
        "source_not_regular",
        "source_symlinked",
        "source_too_large",
        "source_replaced",
        "source_read_failed",
        "source_not_utf8",
        "invalid_json",
        "duplicate_json_key",
        "nonfinite_json_number",
        "source_too_deep",
        "source_too_complex",
        "source_not_record_list",
        "too_many_records",
        "duplicate_record_id",
        "memory_eligibility_capture_failed",
    }
)
_CONCRETE_PATH_TYPE = type(Path())


class MemoryOwnerEligibilityError(ValueError):
    """A bounded error whose text never contains source or record content."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        safe_code = (
            code
            if type(code) is str and code in _ERROR_CODES
            else "memory_eligibility_capture_failed"
        )
        self.code = safe_code
        super().__init__(safe_code)

    def __repr__(self) -> str:
        return f"MemoryOwnerEligibilityError(code={self.code!r})"


class _DuplicateJsonKey(Exception):
    pass


class _NonfiniteJsonNumber(Exception):
    pass


@dataclass(frozen=True, slots=True, repr=False)
class EligibleMemoryRecord:
    """A detached immutable record from one captured source byte snapshot."""

    _record: Mapping[str, Any] = field(repr=False)
    record_digest: str
    source_status: str
    acceptance_status: str
    policy_status: str
    review_status: str

    @property
    def record(self) -> Mapping[str, Any]:
        return self._record

    @property
    def memory_id(self) -> str:
        return self._record["id"]

    @property
    def owner(self) -> str:
        return self._record["owner"]

    @property
    def text(self) -> str:
        return self._record["text"]

    def __repr__(self) -> str:
        return f"EligibleMemoryRecord(record_digest={self.record_digest!r})"


@dataclass(frozen=True, slots=True, repr=False)
class MemoryOwnerEligibilitySnapshot:
    """Immutable result plus a content-free evidence projection."""

    owner_ref: str
    eligible_records: tuple[EligibleMemoryRecord, ...]
    source_digest: str
    snapshot_digest: str
    total_records: int
    rejection_counts: tuple[tuple[str, int], ...]
    schema: str = MEMORY_OWNER_ELIGIBILITY_SNAPSHOT_SCHEMA

    @property
    def eligible_count(self) -> int:
        return len(self.eligible_records)

    @property
    def ineligible_count(self) -> int:
        return self.total_records - self.eligible_count

    def contains_exact_owner(self, owner: str) -> bool:
        return type(owner) is str and _valid_unicode(owner) and _digest_text(owner) == self.owner_ref

    def to_evidence_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "eligibility_schema": MEMORY_ELIGIBILITY_SCHEMA,
            "source_digest": self.source_digest,
            "snapshot_digest": self.snapshot_digest,
            "owner_ref": self.owner_ref,
            "total_records": self.total_records,
            "eligible_count": self.eligible_count,
            "ineligible_count": self.ineligible_count,
            "rejection_counts": dict(self.rejection_counts),
            "bounded": True,
            "read_only": True,
            "raw_content_visible": False,
            "owner_visible": False,
            "source_path_visible": False,
            "side_effects": (),
        }

    def __repr__(self) -> str:
        return (
            "MemoryOwnerEligibilitySnapshot("
            f"eligible_count={self.eligible_count}, "
            f"total_records={self.total_records}, "
            f"snapshot_digest={self.snapshot_digest!r})"
        )


def capture_memory_owner_eligibility_snapshot(
    memory_path: str | Path,
    *,
    owner: str,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    max_records: int = DEFAULT_MAX_RECORDS,
    max_depth: int = DEFAULT_MAX_DEPTH,
    max_json_nodes: int = DEFAULT_MAX_JSON_NODES,
) -> MemoryOwnerEligibilitySnapshot:
    """Capture eligible records for one exact owner without mutating the source."""

    path = _capture_request(
        memory_path,
        owner=owner,
        max_source_bytes=max_source_bytes,
        max_records=max_records,
        max_depth=max_depth,
        max_json_nodes=max_json_nodes,
    )
    try:
        raw = _read_stable_bytes(path, max_source_bytes=max_source_bytes)
        payload = _decode_json(raw)
        _assert_json_budget(
            payload,
            max_depth=max_depth,
            max_json_nodes=max_json_nodes,
        )
        if type(payload) is not list:
            raise MemoryOwnerEligibilityError("source_not_record_list")
        if len(payload) > max_records:
            raise MemoryOwnerEligibilityError("too_many_records")
        return _build_snapshot(raw, payload, owner=owner)
    except MemoryOwnerEligibilityError:
        raise
    except Exception:
        raise MemoryOwnerEligibilityError("memory_eligibility_capture_failed") from None


def _capture_request(
    memory_path: str | Path,
    *,
    owner: str,
    max_source_bytes: int,
    max_records: int,
    max_depth: int,
    max_json_nodes: int,
) -> Path:
    if type(memory_path) not in {str, _CONCRETE_PATH_TYPE}:
        raise MemoryOwnerEligibilityError("invalid_capture_request")
    if type(owner) is not str or not owner or len(owner) > 256 or not _valid_unicode(owner):
        raise MemoryOwnerEligibilityError("invalid_capture_request")
    limits = (
        (max_source_bytes, 1, HARD_MAX_SOURCE_BYTES),
        (max_records, 1, HARD_MAX_RECORDS),
        (max_depth, 3, HARD_MAX_DEPTH),
        (max_json_nodes, 1, HARD_MAX_JSON_NODES),
    )
    if any(type(value) is not int or value < lower or value > upper for value, lower, upper in limits):
        raise MemoryOwnerEligibilityError("invalid_capture_request")
    try:
        path = Path(memory_path)
    except (OSError, TypeError, ValueError):
        raise MemoryOwnerEligibilityError("invalid_capture_request") from None
    if path.name != "memory.json":
        raise MemoryOwnerEligibilityError("invalid_capture_request")
    if not path.is_absolute() or ".." in path.parts:
        raise MemoryOwnerEligibilityError("invalid_capture_request")
    return path


def _read_stable_bytes(path: Path, *, max_source_bytes: int) -> bytes:
    try:
        parents_before = _parent_identities(path)
        before = os.lstat(path)
    except FileNotFoundError:
        raise MemoryOwnerEligibilityError("source_missing") from None
    except OSError:
        raise MemoryOwnerEligibilityError("source_read_failed") from None
    if stat.S_ISLNK(before.st_mode) or _is_windows_reparse_point(before):
        raise MemoryOwnerEligibilityError("source_symlinked")
    if not stat.S_ISREG(before.st_mode):
        raise MemoryOwnerEligibilityError("source_not_regular")
    if before.st_size > max_source_bytes:
        raise MemoryOwnerEligibilityError("source_too_large")

    try:
        with path.open("rb") as handle:
            opened_before = os.fstat(handle.fileno())
            raw = handle.read(max_source_bytes + 1)
            opened_after = os.fstat(handle.fileno())
        after = os.lstat(path)
        parents_after = _parent_identities(path)
    except FileNotFoundError:
        raise MemoryOwnerEligibilityError("source_replaced") from None
    except OSError:
        raise MemoryOwnerEligibilityError("source_read_failed") from None

    if len(raw) > max_source_bytes:
        raise MemoryOwnerEligibilityError("source_too_large")
    if (
        stat.S_ISLNK(after.st_mode)
        or _is_windows_reparse_point(after)
        or not stat.S_ISREG(after.st_mode)
    ):
        raise MemoryOwnerEligibilityError("source_replaced")
    if parents_before != parents_after:
        raise MemoryOwnerEligibilityError("source_replaced")
    identities = tuple(_file_identity(item) for item in (before, opened_before, opened_after, after))
    if len(set(identities)) != 1:
        raise MemoryOwnerEligibilityError("source_replaced")
    path_observations = (_file_observation(before), _file_observation(after))
    handle_observations = (
        _file_observation(opened_before),
        _file_observation(opened_after),
    )
    if (
        path_observations[0] != path_observations[1]
        or handle_observations[0] != handle_observations[1]
        or before.st_size != opened_before.st_size
        or after.st_size != opened_after.st_size
        or len(raw) != opened_after.st_size
    ):
        raise MemoryOwnerEligibilityError("source_replaced")
    return raw


def _file_identity(value: os.stat_result) -> tuple[int, int]:
    return int(value.st_dev), int(value.st_ino)


def _parent_identities(path: Path) -> tuple[tuple[int, int], ...]:
    identities: list[tuple[int, int]] = []
    for parent in reversed(path.parents):
        value = os.lstat(parent)
        if stat.S_ISLNK(value.st_mode) or _is_windows_reparse_point(value):
            raise MemoryOwnerEligibilityError("source_symlinked")
        if not stat.S_ISDIR(value.st_mode):
            raise MemoryOwnerEligibilityError("source_not_regular")
        identities.append(_file_identity(value))
    return tuple(identities)


def _is_windows_reparse_point(value: os.stat_result) -> bool:
    attributes = getattr(value, "st_file_attributes", None)
    reparse_tag = getattr(value, "st_reparse_tag", None)
    # POSIX stat results and deliberately reconstructed os.stat_result values do
    # not expose Windows' optional fields.  A real Windows os.lstat result does,
    # so absence of both fields is not evidence of a reparse point.
    if attributes is None and reparse_tag is None:
        return False
    if type(attributes) is not int or type(reparse_tag) is not int:
        return True
    return bool(
        attributes & _WINDOWS_REPARSE_POINT_ATTRIBUTE
        or reparse_tag != 0
    )


def _file_observation(value: os.stat_result) -> tuple[int, int, int]:
    return int(value.st_size), int(value.st_mtime_ns), int(value.st_ctime_ns)


def _decode_json(raw: bytes) -> Any:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise MemoryOwnerEligibilityError("source_not_utf8") from None
    try:
        return json.loads(
            text,
            object_pairs_hook=_closed_object,
            parse_constant=_reject_nonfinite,
            parse_float=_finite_float,
        )
    except _DuplicateJsonKey:
        raise MemoryOwnerEligibilityError("duplicate_json_key") from None
    except _NonfiniteJsonNumber:
        raise MemoryOwnerEligibilityError("nonfinite_json_number") from None
    except (json.JSONDecodeError, UnicodeError, ValueError, OverflowError, RecursionError):
        raise MemoryOwnerEligibilityError("invalid_json") from None


def _closed_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_nonfinite(_raw: str) -> None:
    raise _NonfiniteJsonNumber


def _finite_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise _NonfiniteJsonNumber
    return value


def _assert_json_budget(payload: Any, *, max_depth: int, max_json_nodes: int) -> None:
    nodes = 0
    stack: list[tuple[Any, int]] = [(payload, 1)]
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > max_json_nodes:
            raise MemoryOwnerEligibilityError("source_too_complex")
        if type(value) is dict:
            if depth > max_depth:
                raise MemoryOwnerEligibilityError("source_too_deep")
            nodes += len(value)
            if nodes > max_json_nodes:
                raise MemoryOwnerEligibilityError("source_too_complex")
            stack.extend((item, depth + 1) for item in value.values())
        elif type(value) is list:
            if depth > max_depth:
                raise MemoryOwnerEligibilityError("source_too_deep")
            stack.extend((item, depth + 1) for item in value)


def _build_snapshot(
    raw: bytes,
    rows: list[Any],
    *,
    owner: str,
) -> MemoryOwnerEligibilitySnapshot:
    seen_ids: set[str] = set()
    for row in rows:
        if type(row) is not dict or type(row.get("id")) is not str:
            continue
        memory_id = row["id"]
        if memory_id in seen_ids:
            raise MemoryOwnerEligibilityError("duplicate_record_id")
        seen_ids.add(memory_id)

    counts = {code: 0 for code in _REJECTION_CODES}
    accepted: list[EligibleMemoryRecord] = []
    for row in rows:
        record, rejection = _eligible_record(row, owner=owner)
        if record is None:
            counts[rejection] += 1
        else:
            accepted.append(record)
    accepted.sort(key=lambda item: (item.memory_id, item.record_digest))
    source_digest = _digest_bytes(raw)
    rejection_counts = tuple((code, counts[code]) for code in _REJECTION_CODES)
    snapshot_digest = _digest_json(
        {
            "schema": MEMORY_OWNER_ELIGIBILITY_SNAPSHOT_SCHEMA,
            "eligibility_schema": MEMORY_ELIGIBILITY_SCHEMA,
            "source_digest": source_digest,
            "owner_ref": _digest_text(owner),
            "total_records": len(rows),
            "record_digests": [item.record_digest for item in accepted],
            "rejection_counts": dict(rejection_counts),
        }
    )
    return MemoryOwnerEligibilitySnapshot(
        owner_ref=_digest_text(owner),
        eligible_records=tuple(accepted),
        source_digest=source_digest,
        snapshot_digest=snapshot_digest,
        total_records=len(rows),
        rejection_counts=rejection_counts,
    )


def _eligible_record(
    value: Any,
    *,
    owner: str,
) -> tuple[EligibleMemoryRecord | None, str]:
    if type(value) is not dict or not _valid_core_record(value):
        return None, "invalid_record"
    if value["owner"] != owner:
        return None, "owner_mismatch"
    metadata = value.get("metadata")
    if type(metadata) is not dict or "memory_eligibility" not in metadata:
        return None, "legacy_or_unstamped"
    stamp = metadata.get("memory_eligibility")
    if not _valid_stamp(stamp):
        return None, "invalid_stamp"
    if _contradictory_alias(value, metadata, stamp):
        return None, "contradictory_state"
    if stamp["source_status"] != "active":
        return None, "inactive"
    if stamp["acceptance_status"] != "accepted":
        return None, "not_accepted"
    if stamp["incognito"] is not False:
        return None, "incognito"
    if stamp["policy_status"] != "go":
        return None, "policy_not_go"
    if stamp["review_status"] not in {"accepted", "not_required"}:
        return None, "review_not_accepted"

    record_digest = _digest_json(value)
    frozen = _freeze_json(value)
    return (
        EligibleMemoryRecord(
            _record=frozen,
            record_digest=record_digest,
            source_status=stamp["source_status"],
            acceptance_status=stamp["acceptance_status"],
            policy_status=stamp["policy_status"],
            review_status=stamp["review_status"],
        ),
        "",
    )


def _valid_core_record(value: dict[str, Any]) -> bool:
    memory_id = value.get("id")
    owner = value.get("owner")
    text = value.get("text")
    timestamp = value.get("timestamp")
    source = value.get("source")
    category = value.get("category")
    session_id = value.get("session_id")
    return bool(
        type(memory_id) is str
        and memory_id
        and len(memory_id) <= 256
        and _valid_unicode(memory_id)
        and type(owner) is str
        and owner
        and len(owner) <= 256
        and _valid_unicode(owner)
        and type(text) is str
        and text
        and _valid_content_text(text)
        and type(timestamp) is int
        and 0 <= timestamp <= 2**63 - 1
        and type(source) is str
        and source
        and len(source) <= 256
        and _valid_unicode(source)
        and type(category) is str
        and category
        and len(category) <= 256
        and _valid_unicode(category)
        and (session_id is None or (type(session_id) is str and len(session_id) <= 256 and _valid_unicode(session_id)))
    )


def _valid_stamp(value: Any) -> bool:
    if type(value) is not dict or frozenset(value) != _STAMP_KEYS:
        return False
    return bool(
        type(value.get("schema")) is str
        and value["schema"] == MEMORY_ELIGIBILITY_SCHEMA
        and type(value.get("source_status")) is str
        and value["source_status"] in _SOURCE_STATUSES
        and type(value.get("acceptance_status")) is str
        and value["acceptance_status"] in _ACCEPTANCE_STATUSES
        and type(value.get("incognito")) is bool
        and type(value.get("policy_status")) is str
        and value["policy_status"] in _POLICY_STATUSES
        and type(value.get("review_status")) is str
        and value["review_status"] in _REVIEW_STATUSES
        and type(value.get("policy_evidence_ref")) is str
        and _EVIDENCE_REF_RE.fullmatch(value["policy_evidence_ref"])
        and type(value.get("review_evidence_ref")) is str
        and _EVIDENCE_REF_RE.fullmatch(value["review_evidence_ref"])
    )


def _contradictory_alias(
    record: dict[str, Any],
    metadata: dict[str, Any],
    stamp: dict[str, Any],
) -> bool:
    for key in (
        "source_status",
        "acceptance_status",
        "incognito",
        "policy_status",
        "policy_evidence_ref",
        "review_status",
        "review_evidence_ref",
    ):
        for container in (record, metadata):
            if key in container and (type(container[key]) is not type(stamp[key]) or container[key] != stamp[key]):
                return True
    boolean_aliases = {
        "accepted": stamp["acceptance_status"] == "accepted",
        "deleted": stamp["source_status"] == "deleted",
        "policy_blocked": stamp["policy_status"] == "blocked",
        "review_required": stamp["review_status"] not in {"accepted", "not_required"},
    }
    for key, expected in boolean_aliases.items():
        for container in (record, metadata):
            if key in container and (type(container[key]) is not bool or container[key] is not expected):
                return True
    for container in (record, metadata):
        if "memory_status" not in container:
            continue
        legacy_status = container["memory_status"]
        if (
            type(legacy_status) is not str
            or legacy_status
            not in _LEGACY_MEMORY_STATUSES_COMPATIBLE_WITH_ELIGIBILITY
        ):
            return True
    ambiguous_nested_aliases = {
        "analysis_policy",
        "policy",
        "policy_review",
        "review",
        "review_decision",
    }
    if any(key in container for container in (record, metadata) for key in ambiguous_nested_aliases):
        return True
    return False


def _freeze_json(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze_json(item) for item in value)
    return value


def _valid_unicode(value: str) -> bool:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return not any(ord(character) < 32 or ord(character) == 127 for character in value)


def _valid_content_text(value: str) -> bool:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return False
    return "\x00" not in value


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _digest_text(value: str) -> str:
    return _digest_bytes(value.encode("utf-8", errors="strict"))


def _digest_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8", errors="strict")
    return _digest_bytes(encoded)


__all__ = [
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_JSON_NODES",
    "DEFAULT_MAX_RECORDS",
    "DEFAULT_MAX_SOURCE_BYTES",
    "EligibleMemoryRecord",
    "MEMORY_ELIGIBILITY_SCHEMA",
    "MEMORY_OWNER_ELIGIBILITY_SNAPSHOT_SCHEMA",
    "MemoryOwnerEligibilityError",
    "MemoryOwnerEligibilitySnapshot",
    "capture_memory_owner_eligibility_snapshot",
]
