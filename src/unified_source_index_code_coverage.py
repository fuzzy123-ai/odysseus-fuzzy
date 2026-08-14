"""Closed, content-free Forge code coverage manifests.

The module consumes only accepted FCA-00/FCA-01 value objects.  It never reads
source bytes, scans a repository, invokes a provider, or mutates the canonical
USI store.  Coverage is an independently persisted, canonical-byte projection
bound to one immutable Forge inventory and one accepted USI store/job attempt.
"""

from __future__ import annotations

from enum import StrEnum
import hashlib
import json
import re
import unicodedata
from threading import RLock
from typing import Any

from src.repo_git_adapter import (
    MAX_FORGE_SNAPSHOT_FILES,
    ForgeSnapshotAuthorityBinding,
    ForgeSnapshotError,
    ForgeSnapshotFile,
    ForgeSnapshotInventory,
)
from src.unified_source_index_contract import (
    ChunkRecord,
    CodeRangeLocator,
    CodeOccurrenceRecords,
    ForgeCodeOccurrenceEvidence,
    IndexJobRecord,
    JobStatus,
    SourceRecord,
    SourceVersionRecord,
)
from src.unified_source_index_sources.forge_code import (
    ForgeCodeOccurrence,
    ForgeCodeSourceError,
    validate_forge_code_occurrence_records,
)
from src.unified_source_index_stores import StoreSnapshot
from src.unified_source_index_code_policy import (
    CodePolicyObservationError,
    PolicyDecision,
    PolicyFileDecision,
    PolicyObservation,
)


COVERAGE_SCHEMA = "odysseus.usi.forge_code_coverage.v2"
COVERAGE_LEDGER_SCHEMA = "odysseus.usi.forge_code_coverage_ledger.v2"
MAX_COVERAGE_OCCURRENCES_PER_FILE = 100_000
MAX_COVERAGE_LINE_COUNT = 10_000_000
MAX_COVERAGE_LEDGER_ENTRIES = 10_000
MAX_COVERAGE_CANONICAL_BYTES = 16 * 1024 * 1024
MAX_COVERAGE_LEDGER_TOTAL_BYTES = 64 * 1024 * 1024

_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,255}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_COVERAGE_ID_RE = re.compile(r"^cov_[0-9a-f]{64}$")
_SENSITIVE_REF_RE = re.compile(
    r"(?i)(?:password|passwd|secret|credential|authorization|bearer|api[_-]?key|access[_-]?token)"
)


class CoverageError(ValueError):
    """Bounded, content-free public coverage failure."""

    __slots__ = ("code",)
    _codes = frozenset({"invalid_attempt", "invalid_observation", "invalid_manifest", "budget_exceeded"})

    def __init__(self, code: str) -> None:
        safe = code if type(code) is str and code in CoverageError._codes else "invalid_manifest"
        self.code = safe
        super().__init__(safe)

    def __str__(self) -> str:
        try:
            code = object.__getattribute__(self, "code")
        except BaseException:
            code = "invalid_manifest"
        return code if type(code) is str and code in CoverageError._codes else "invalid_manifest"

    def __repr__(self) -> str:
        return f"CoverageError(code={str(self)!r})"


class CoverageLedgerError(CoverageError):
    """Bounded, content-free immutable-ledger failure."""

    _codes = frozenset({"invalid_ledger", "head_conflict", "supersession_conflict", "budget_exceeded"})

    def __init__(self, code: str) -> None:
        safe = code if type(code) is str and code in CoverageLedgerError._codes else "invalid_ledger"
        self.code = safe
        ValueError.__init__(self, safe)

    def __str__(self) -> str:
        try:
            code = object.__getattribute__(self, "code")
        except BaseException:
            code = "invalid_ledger"
        return code if type(code) is str and code in CoverageLedgerError._codes else "invalid_ledger"

    def __repr__(self) -> str:
        return f"CoverageLedgerError(code={str(self)!r})"


class CoverageStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    STALE = "stale"
    FAILED = "failed"


class FileObservationState(StrEnum):
    TEXT = "text"
    EMPTY_TEXT = "empty_text"
    BINARY = "binary"
    POLICY_EXCLUDED_TEXT = "policy_excluded_text"
    FAILURE = "failure"
    STALE = "stale"


class TextClassification(StrEnum):
    CODE = "code"
    TEXT = "text"
    GENERATED = "generated"
    VENDOR = "vendor"
    MINIFIED = "minified"
    UNKNOWN_LANGUAGE = "unknown_language"
    OVERSIZE = "oversize"


class CoverageFailureCode(StrEnum):
    READ_FAILED = "read_failed"
    DECODE_FAILED = "decode_failed"
    PARSER_AND_FALLBACK_FAILED = "parser_and_fallback_failed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    UNCLASSIFIED_TEXT = "unclassified_text"


class DirectLineDenominator(StrEnum):
    INCLUDED = "included"
    POLICY_OUT_OF_SCOPE = "policy_out_of_scope"
    NON_TEXT = "non_text"


class WholeCodebaseClaim(StrEnum):
    ALLOWED = "allowed"
    NOT_ALLOWED_INCOMPLETE = "not_allowed_incomplete"
    FORBIDDEN_POLICY_EXCLUDED_TEXT = "forbidden_due_to_policy_excluded_source_text"


_ATTEMPT_FIELDS = (
    "job_id", "job_status", "attempt_count", "job_record_digest",
    "source_scope_id", "source_ids", "source_version_ids", "store_revision",
    "store_state_hash", "store_record_count", "store_tombstone_count",
    "store_snapshot_ref", "adapter_id", "adapter_version",
    "adapter_generation", "admission_policy_generation",
    "indexing_policy_generation",
)


class CoverageAttemptBinding:
    """Content-free binding to one accepted store snapshot and job attempt."""

    __slots__ = ("_snapshot",)

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("CoverageAttemptBinding requires from_accepted")

    @classmethod
    def from_accepted(
        cls,
        *,
        job: IndexJobRecord,
        store_snapshot: StoreSnapshot,
        inventory: ForgeSnapshotInventory,
        indexing_policy_generation: str,
    ) -> "CoverageAttemptBinding":
        try:
            if (
                type(job) is not IndexJobRecord
                or type(store_snapshot) is not StoreSnapshot
                or type(inventory) is not ForgeSnapshotInventory
                or type(indexing_policy_generation) is not str
            ):
                raise ValueError
            accepted = _canonical_inventory(inventory)
            job_json = IndexJobRecord.to_json(job)
            if type(job_json) is not str:
                raise ValueError
            canonical_job = IndexJobRecord.from_json(job_json)
            canonical_job_json = IndexJobRecord.to_json(canonical_job)
            if type(canonical_job_json) is not str or canonical_job_json != job_json:
                raise ValueError
            snapshot_values = tuple(
                object.__getattribute__(store_snapshot, name)
                for name in (
                    "revision", "state_hash", "record_count",
                    "tombstone_count", "snapshot_ref",
                )
            )
            snapshot = StoreSnapshot(
                *snapshot_values,
            )
            source_scope = object.__getattribute__(canonical_job, "source_scope")
            authority = object.__getattribute__(accepted, "authority_binding")
            record = (
                object.__getattribute__(canonical_job, "job_id"),
                object.__getattribute__(canonical_job, "status").value,
                object.__getattribute__(canonical_job, "attempt_count"),
                _digest(canonical_job_json.encode("utf-8")),
                object.__getattribute__(source_scope, "scope_id"),
                object.__getattribute__(source_scope, "source_ids"),
                object.__getattribute__(source_scope, "source_version_ids"),
                object.__getattribute__(snapshot, "revision"),
                object.__getattribute__(snapshot, "state_hash"),
                object.__getattribute__(snapshot, "record_count"),
                object.__getattribute__(snapshot, "tombstone_count"),
                object.__getattribute__(snapshot, "snapshot_ref"),
                object.__getattribute__(authority, "adapter_id"),
                object.__getattribute__(authority, "adapter_version"),
                object.__getattribute__(authority, "adapter_generation"),
                object.__getattribute__(authority, "admission_policy_generation"),
                indexing_policy_generation,
            )
            if canonical_job.owner_scope != accepted.owner_scope:
                raise ValueError
            return _attempt_from_record(record)
        except BaseException as error:
            raise CoverageError(_safe_coverage_code(error, "invalid_attempt")) from None

    def _record(self) -> tuple[object, ...]:
        try:
            snapshot = object.__getattribute__(self, "_snapshot")
            return _validated_attempt_snapshot(snapshot)
        except BaseException as error:
            raise CoverageError(_safe_coverage_code(error, "invalid_attempt")) from None

    @property
    def job_id(self) -> str: return self._record()[0]  # type: ignore[return-value]
    @property
    def job_status(self) -> JobStatus: return JobStatus(self._record()[1])  # type: ignore[arg-type,return-value]
    @property
    def attempt_count(self) -> int: return self._record()[2]  # type: ignore[return-value]
    @property
    def job_record_digest(self) -> str: return self._record()[3]  # type: ignore[return-value]
    @property
    def source_scope_id(self) -> str: return self._record()[4]  # type: ignore[return-value]
    @property
    def source_ids(self) -> tuple[str, ...]: return tuple(self._record()[5])  # type: ignore[arg-type,return-value]
    @property
    def source_version_ids(self) -> tuple[str, ...]: return tuple(self._record()[6])  # type: ignore[arg-type,return-value]
    @property
    def store_revision(self) -> int: return self._record()[7]  # type: ignore[return-value]
    @property
    def store_state_hash(self) -> str: return self._record()[8]  # type: ignore[return-value]
    @property
    def store_record_count(self) -> int: return self._record()[9]  # type: ignore[return-value]
    @property
    def store_tombstone_count(self) -> int: return self._record()[10]  # type: ignore[return-value]
    @property
    def store_snapshot_ref(self) -> str: return self._record()[11]  # type: ignore[return-value]
    @property
    def adapter_id(self) -> str: return self._record()[12]  # type: ignore[return-value]
    @property
    def adapter_version(self) -> str: return self._record()[13]  # type: ignore[return-value]
    @property
    def adapter_generation(self) -> str: return self._record()[14]  # type: ignore[return-value]
    @property
    def admission_policy_generation(self) -> str: return self._record()[15]  # type: ignore[return-value]
    @property
    def indexing_policy_generation(self) -> str: return self._record()[16]  # type: ignore[return-value]

    def _assert_integrity(self) -> None:
        self._record()

    def to_dict(self) -> dict[str, object]:
        try:
            return _attempt_record_to_dict(self._record())
        except BaseException as error:
            raise CoverageError(_safe_coverage_code(error, "invalid_attempt")) from None

    def __repr__(self) -> str:
        try:
            self._record()
            return "CoverageAttemptBinding(valid)"
        except BaseException:
            return "CoverageAttemptBinding(invalid)"

    def __eq__(self, other: object) -> bool:
        if type(other) is not CoverageAttemptBinding:
            return NotImplemented
        try:
            return self._record() == other._record()
        except BaseException as error:
            raise CoverageError(_safe_coverage_code(error, "invalid_attempt")) from None

    def __hash__(self) -> int:
        try:
            return hash(self._record())
        except BaseException as error:
            raise CoverageError(_safe_coverage_code(error, "invalid_attempt")) from None


class FileCoverageObservation:
    """One closed observation corresponding to exactly one Forge inventory file."""

    __slots__ = ("_snapshot",)

    def __init__(
        self, path: str, content_sha256: str, byte_count: int,
        state: FileObservationState, total_line_count: int,
        text_classification: TextClassification | None = None,
        occurrences: tuple[ForgeCodeOccurrence, ...] = (),
        exclusion_evidence_ref: str = "", failure_code: CoverageFailureCode | None = None,
    ) -> None:
        try:
            digest = content_sha256
            line_count = total_line_count
            classification = text_classification
            exclusion = exclusion_evidence_ref
            failure = failure_code
            if type(line_count) is int and line_count > MAX_COVERAGE_LINE_COUNT:
                raise CoverageError("budget_exceeded")
            if type(occurrences) is tuple and len(occurrences) > MAX_COVERAGE_OCCURRENCES_PER_FILE:
                raise CoverageError("budget_exceeded")
            if (
                type(path) is not str or type(digest) is not str
                or type(byte_count) is not int or isinstance(byte_count, bool) or type(state) is not FileObservationState
                or type(line_count) is not int
                or not 0 <= line_count <= MAX_COVERAGE_LINE_COUNT
                or type(occurrences) is not tuple
                or len(occurrences) > MAX_COVERAGE_OCCURRENCES_PER_FILE
                or any(type(item) is not ForgeCodeOccurrence for item in occurrences)
                or type(exclusion) is not str
                or (classification is not None and type(classification) is not TextClassification)
                or (failure is not None and type(failure) is not CoverageFailureCode)
            ):
                raise ValueError
            if unicodedata.normalize("NFC", path) != path:
                raise ValueError
            descriptor = ForgeSnapshotFile(path, digest, byte_count)
            if state is FileObservationState.TEXT:
                if classification is None or line_count < 1 or exclusion or failure is not None:
                    raise ValueError
            elif state is FileObservationState.EMPTY_TEXT:
                if classification is None or line_count != 0 or descriptor.byte_count != 0 or occurrences or exclusion or failure is not None:
                    raise ValueError
            elif state is FileObservationState.BINARY:
                if classification is not None or line_count != 0 or occurrences or exclusion or failure is not None:
                    raise ValueError
            elif state is FileObservationState.POLICY_EXCLUDED_TEXT:
                if classification is None or occurrences or failure is not None:
                    raise ValueError
                _opaque_reference(exclusion, "exclusion_evidence_ref")
            elif state is FileObservationState.FAILURE:
                if classification is None or occurrences or exclusion or failure is None:
                    raise ValueError
            elif state is FileObservationState.STALE:
                if classification is None or occurrences or exclusion or failure is not None:
                    raise ValueError
            else:
                raise ValueError
            # Store only a fresh tuple.  The incoming container is never retained.
            detached = tuple(_occurrence_snapshot(item) for item in occurrences)
            object.__setattr__(self, "_snapshot", (
                descriptor.path, descriptor.content_sha256, descriptor.byte_count,
                state.value, line_count,
                None if classification is None else classification.value,
                detached, exclusion, None if failure is None else failure.value,
            ))
        except BaseException as error:
            raise CoverageError(_safe_coverage_code(error, "invalid_observation")) from None

    def _values(self) -> tuple[object, ...]:
        try:
            value = object.__getattribute__(self, "_snapshot")
            if type(value) is not tuple or len(value) != 9:
                raise ValueError
            if (type(value[0]) is not str or type(value[1]) is not str
                    or type(value[2]) is not int or type(value[3]) is not str
                    or type(value[4]) is not int or value[5] is not None and type(value[5]) is not str
                    or type(value[6]) is not tuple or type(value[7]) is not str
                    or value[8] is not None and type(value[8]) is not str):
                raise ValueError
            return tuple(value)
        except BaseException as error:
            raise CoverageError(_safe_coverage_code(error, "invalid_observation")) from None

    @property
    def path(self) -> str: return self._values()[0]  # type: ignore[return-value]
    @property
    def content_sha256(self) -> str: return self._values()[1]  # type: ignore[return-value]
    @property
    def byte_count(self) -> int: return self._values()[2]  # type: ignore[return-value]
    @property
    def state(self) -> FileObservationState: return FileObservationState(self._values()[3])  # type: ignore[arg-type]
    @property
    def total_line_count(self) -> int: return self._values()[4]  # type: ignore[return-value]
    @property
    def text_classification(self) -> TextClassification | None:
        value = self._values()[5]
        return None if value is None else TextClassification(value)  # type: ignore[arg-type]
    @property
    def occurrences(self) -> tuple[ForgeCodeOccurrence, ...]:
        try:
            raw = self._values()[6]
            return tuple(_occurrence_from_snapshot(item) for item in raw)  # type: ignore[union-attr]
        except BaseException as error:
            raise CoverageError(_safe_coverage_code(error, "invalid_observation")) from None
    @property
    def exclusion_evidence_ref(self) -> str: return self._values()[7]  # type: ignore[return-value]
    @property
    def failure_code(self) -> CoverageFailureCode | None:
        value = self._values()[8]
        return None if value is None else CoverageFailureCode(value)  # type: ignore[arg-type]
    def __repr__(self) -> str:
        try: self._values(); return "FileCoverageObservation(valid)"
        except BaseException: return "FileCoverageObservation(invalid)"
    def __eq__(self, other: object) -> bool:
        if type(other) is not FileCoverageObservation: return NotImplemented
        try: return self._values() == other._values()
        except BaseException as error: raise CoverageError(_safe_coverage_code(error, "invalid_observation")) from None
    def __hash__(self) -> int:
        try: return hash(self._values())
        except BaseException as error: raise CoverageError(_safe_coverage_code(error, "invalid_observation")) from None


class CoverageFileRecord:
    __slots__ = ("_snapshot",)
    def __init__(self, path_ref: str, blob_digest: str, byte_count: int, state: FileObservationState,
                 text_classification: TextClassification | None, total_line_count: int,
                 direct_line_denominator: DirectLineDenominator, eligible_line_count: int,
                 covered_line_ranges: tuple[tuple[int, int], ...], uncovered_eligible_ranges: tuple[tuple[int, int], ...],
                 exclusion_evidence_ref: str, failure_code: CoverageFailureCode | None, occurrence_ids: tuple[str, ...]) -> None:
        try:
            if (type(path_ref) is not str or type(blob_digest) is not str or type(byte_count) is not int or type(state) is not FileObservationState
                    or text_classification is not None and type(text_classification) is not TextClassification
                    or type(total_line_count) is not int or type(direct_line_denominator) is not DirectLineDenominator
                    or type(eligible_line_count) is not int or type(covered_line_ranges) is not tuple
                    or type(uncovered_eligible_ranges) is not tuple or type(exclusion_evidence_ref) is not str
                    or failure_code is not None and type(failure_code) is not CoverageFailureCode or type(occurrence_ids) is not tuple): raise ValueError
            if byte_count > MAX_COVERAGE_CANONICAL_BYTES or total_line_count > MAX_COVERAGE_LINE_COUNT or eligible_line_count > MAX_COVERAGE_LINE_COUNT or len(covered_line_ranges) > MAX_COVERAGE_OCCURRENCES_PER_FILE or len(uncovered_eligible_ranges) > MAX_COVERAGE_OCCURRENCES_PER_FILE + 1 or len(occurrence_ids) > MAX_COVERAGE_OCCURRENCES_PER_FILE:
                raise CoverageError("budget_exceeded")
            if any(type(pair) is not tuple or len(pair) != 2 or any(type(x) is not int for x in pair) for pair in (*covered_line_ranges, *uncovered_eligible_ranges)) or any(type(x) is not str for x in occurrence_ids): raise ValueError
            object.__setattr__(self, "_snapshot", (path_ref, blob_digest, byte_count, state.value, None if text_classification is None else text_classification.value, total_line_count, direct_line_denominator.value, eligible_line_count, tuple(tuple(x) for x in covered_line_ranges), tuple(tuple(x) for x in uncovered_eligible_ranges), exclusion_evidence_ref, None if failure_code is None else failure_code.value, tuple(occurrence_ids)))
            _assert_file_record(self)
        except BaseException as error: raise CoverageError(_safe_coverage_code(error, "invalid_manifest")) from None
    def _values(self) -> tuple[object, ...]:
        try:
            value = object.__getattribute__(self, "_snapshot")
            if type(value) is not tuple or len(value) != 13: raise ValueError
            return tuple(value)
        except BaseException as error: raise CoverageError(_safe_coverage_code(error, "invalid_manifest")) from None
    @property
    def path_ref(self) -> str: return self._values()[0]  # type: ignore[return-value]
    @property
    def blob_digest(self) -> str: return self._values()[1]  # type: ignore[return-value]
    @property
    def byte_count(self) -> int: return self._values()[2]  # type: ignore[return-value]
    @property
    def state(self) -> FileObservationState: return FileObservationState(self._values()[3])  # type: ignore[arg-type]
    @property
    def text_classification(self) -> TextClassification | None:
        x=self._values()[4]; return None if x is None else TextClassification(x)  # type: ignore[arg-type]
    @property
    def total_line_count(self) -> int: return self._values()[5]  # type: ignore[return-value]
    @property
    def direct_line_denominator(self) -> DirectLineDenominator: return DirectLineDenominator(self._values()[6])  # type: ignore[arg-type]
    @property
    def eligible_line_count(self) -> int: return self._values()[7]  # type: ignore[return-value]
    @property
    def covered_line_ranges(self) -> tuple[tuple[int, int], ...]: return tuple(tuple(x) for x in self._values()[8])  # type: ignore[arg-type]
    @property
    def uncovered_eligible_ranges(self) -> tuple[tuple[int, int], ...]: return tuple(tuple(x) for x in self._values()[9])  # type: ignore[arg-type]
    @property
    def exclusion_evidence_ref(self) -> str: return self._values()[10]  # type: ignore[return-value]
    @property
    def failure_code(self) -> CoverageFailureCode | None:
        x=self._values()[11]; return None if x is None else CoverageFailureCode(x)  # type: ignore[arg-type]
    @property
    def occurrence_ids(self) -> tuple[str, ...]: return tuple(list(self._values()[12]))  # type: ignore[arg-type]

    def to_dict(self) -> dict[str, object]:
        try:
            return {
            "path_ref": self.path_ref,
            "blob_digest": self.blob_digest,
            "byte_count": self.byte_count,
            "state": self.state.value,
            "text_classification": None if self.text_classification is None else self.text_classification.value,
            "total_line_count": self.total_line_count,
            "direct_line_denominator": self.direct_line_denominator.value,
            "eligible_line_count": self.eligible_line_count,
            "covered_line_ranges": [list(item) for item in self.covered_line_ranges],
            "uncovered_eligible_ranges": [list(item) for item in self.uncovered_eligible_ranges],
            "exclusion": (
                None
                if not self.exclusion_evidence_ref
                else {
                    "code": "policy_excluded",
                    "evidence_ref": self.exclusion_evidence_ref,
                }
            ),
            "failure_code": None if self.failure_code is None else self.failure_code.value,
            "occurrence_count": len(self.occurrence_ids),
            "occurrence_ids": list(self.occurrence_ids),
            }
        except BaseException as error: raise CoverageError(_safe_coverage_code(error, "invalid_manifest")) from None
    def __repr__(self) -> str:
        try: self.to_dict(); return "CoverageFileRecord(valid)"
        except BaseException: return "CoverageFileRecord(invalid)"
    def __eq__(self, other: object) -> bool:
        if type(other) is not CoverageFileRecord: return NotImplemented
        try: return self._values() == other._values()
        except BaseException as error: raise CoverageError(_safe_coverage_code(error, "invalid_manifest")) from None
    def __hash__(self) -> int:
        try: return hash(self._values())
        except BaseException as error: raise CoverageError(_safe_coverage_code(error, "invalid_manifest")) from None


class CoverageTotals:
    __slots__ = ("_snapshot",)
    _names = ("discovered_files", "eligible_files", "excluded_files", "non_text_files", "unreadable_files", "stale_files", "eligible_lines", "covered_eligible_lines", "uncovered_eligible_lines", "occurrence_count", "deleted_sources_observed")
    def __init__(self, discovered_files: int, eligible_files: int, excluded_files: int, non_text_files: int, unreadable_files: int, stale_files: int, eligible_lines: int, covered_eligible_lines: int, uncovered_eligible_lines: int, occurrence_count: int, deleted_sources_observed: int = 0) -> None:
        try:
            values=(discovered_files, eligible_files, excluded_files, non_text_files, unreadable_files, stale_files, eligible_lines, covered_eligible_lines, uncovered_eligible_lines, occurrence_count, deleted_sources_observed)
            if any(type(x) is not int or x < 0 for x in values): raise ValueError
            if discovered_files > MAX_FORGE_SNAPSHOT_FILES or occurrence_count > 1_000_000 or any(x > 5_120_000_000 for x in values): raise CoverageError("budget_exceeded")
            if eligible_files + excluded_files + non_text_files != discovered_files or covered_eligible_lines + uncovered_eligible_lines != eligible_lines: raise ValueError
            object.__setattr__(self, "_snapshot", tuple(values))
        except BaseException as error: raise CoverageError(_safe_coverage_code(error, "invalid_manifest")) from None
    def _values(self) -> tuple[int, ...]:
        try:
            value=object.__getattribute__(self, "_snapshot")
            if type(value) is not tuple or len(value)!=11 or any(type(x) is not int or x < 0 for x in value): raise ValueError
            return tuple(value)  # type: ignore[return-value]
        except BaseException as error: raise CoverageError(_safe_coverage_code(error, "invalid_manifest")) from None
    def __getattr__(self, name: str) -> int:
        try: return self._values()[self._names.index(name)]
        except ValueError: raise AttributeError(name) from None

    def to_dict(self) -> dict[str, int]:
        try: return dict(zip(self._names, self._values(), strict=True))
        except BaseException as error: raise CoverageError(_safe_coverage_code(error, "invalid_manifest")) from None
    def __repr__(self) -> str:
        try: self._values(); return "CoverageTotals(valid)"
        except BaseException: return "CoverageTotals(invalid)"
    def __eq__(self, other: object) -> bool:
        if type(other) is not CoverageTotals: return NotImplemented
        try: return self._values() == other._values()
        except BaseException as error: raise CoverageError(_safe_coverage_code(error, "invalid_manifest")) from None
    def __hash__(self) -> int:
        try: return hash(self._values())
        except BaseException as error: raise CoverageError(_safe_coverage_code(error, "invalid_manifest")) from None


class CoverageManifest:
    """One validated detached manifest snapshot.

    The only retained domain state is an exact tuple of built-in values.  Every
    public projection is reconstructed from that tuple, so neither a caller's
    input objects nor a previous projection can become authority later.
    """

    __slots__ = ("_snapshot",)

    @classmethod
    def _create(
        cls,
        *,
        inventory: ForgeSnapshotInventory,
        attempt: CoverageAttemptBinding,
        files: tuple[CoverageFileRecord, ...],
        totals: CoverageTotals,
        status: CoverageStatus,
        whole_codebase_claim: WholeCodebaseClaim,
        policy_observation_digest: str,
    ) -> "CoverageManifest":
        authority = inventory.authority_binding
        values: dict[str, object] = {
            "owner_scope": inventory.owner_scope,
            "repo_id": inventory.repo_id,
            "forge_revision_id": inventory.version_id,
            "commit_sha": inventory.commit_sha,
            "forge_snapshot_digest": inventory.snapshot_digest,
            "forge_manifest_digest": inventory.manifest_sha256,
            "authority_binding": (
                authority.adapter_id,
                authority.adapter_version,
                authority.adapter_generation,
                authority.admission_policy_generation,
            ),
            "attempt": attempt,
            "policy_observation_digest": policy_observation_digest,
            "files": files,
            "totals": totals,
            "status": status,
            "whole_codebase_claim": whole_codebase_claim,
        }
        identity = _manifest_identity(values)
        digest = _digest(_canonical_bytes(identity))
        payload = dict(identity)
        payload["coverage_id"] = "cov_" + digest.removeprefix("sha256:")
        payload["manifest_digest"] = digest
        return cls.from_canonical_bytes(_canonical_bytes(payload))

    @classmethod
    def from_canonical_bytes(cls, payload: bytes) -> "CoverageManifest":
        try:
            value = _decode_manifest_object(payload)
            _validate_manifest_dict(value)
            semantic = _semantic_manifest_snapshot(value)
            if _canonical_bytes(_manifest_payload_from_semantic(semantic)) != payload:
                raise ValueError
            instance = object.__new__(cls)
            object.__setattr__(instance, "_snapshot", semantic)
            instance._payload()
            return instance
        except BaseException as error:
            raise CoverageError(_safe_coverage_code(error, "invalid_manifest")) from None

    def _payload(self) -> dict[str, object]:
        try:
            snapshot = object.__getattribute__(self, "_snapshot")
            value = _manifest_payload_from_semantic(snapshot)
            _validate_manifest_dict(value)
            return value
        except BaseException as error:
            raise CoverageError(_safe_coverage_code(error, "invalid_manifest")) from None

    @property
    def owner_scope(self) -> str: return self._payload()["owner_scope_ref"]  # type: ignore[return-value]
    @property
    def repo_id(self) -> str: return self._payload()["repo_id"]  # type: ignore[return-value]
    @property
    def forge_revision_id(self) -> str: return self._payload()["forge_revision_id"]  # type: ignore[return-value]
    @property
    def commit_sha(self) -> str: return self._payload()["commit_sha"]  # type: ignore[return-value]
    @property
    def forge_snapshot_digest(self) -> str: return self._payload()["forge_snapshot_digest"]  # type: ignore[return-value]
    @property
    def forge_manifest_digest(self) -> str: return self._payload()["forge_manifest_digest"]  # type: ignore[return-value]
    @property
    def authority_binding(self) -> tuple[str, str, str, str]:
        value = self._payload()["authority_binding"]
        assert type(value) is list
        return tuple(list(value))  # type: ignore[return-value]
    @property
    def attempt(self) -> CoverageAttemptBinding:
        return _attempt_from_dict(self._payload()["attempt"])
    @property
    def policy_observation_digest(self) -> str: return self._payload()["policy_observation_digest"]  # type: ignore[return-value]
    @property
    def files(self) -> tuple[CoverageFileRecord, ...]:
        value = self._payload()["files"]
        assert type(value) is list
        return tuple(_file_record_from_dict(item) for item in value)
    @property
    def totals(self) -> CoverageTotals:
        return _totals_from_dict(self._payload()["totals"])
    @property
    def status(self) -> CoverageStatus: return CoverageStatus(self._payload()["status"])  # type: ignore[arg-type]
    @property
    def whole_codebase_claim(self) -> WholeCodebaseClaim: return WholeCodebaseClaim(self._payload()["whole_codebase_claim"])  # type: ignore[arg-type]
    @property
    def coverage_id(self) -> str: return self._payload()["coverage_id"]  # type: ignore[return-value]
    @property
    def manifest_digest(self) -> str: return self._payload()["manifest_digest"]  # type: ignore[return-value]

    def _assert_integrity(self) -> None:
        self._payload()

    def to_dict(self) -> dict[str, object]:
        return self._payload()

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.to_dict())

    def scope_digest(self) -> str:
        return _scope_digest_from_manifest(self.to_dict())

    def __repr__(self) -> str:
        try:
            return f"CoverageManifest(manifest_digest={self.manifest_digest!r})"
        except BaseException:
            return "CoverageManifest(invalid)"

    def __eq__(self, other: object) -> bool:
        if type(other) is not CoverageManifest:
            return NotImplemented
        try:
            return self.canonical_bytes() == other.canonical_bytes()
        except BaseException as error:
            raise CoverageError(_safe_coverage_code(error, "invalid_manifest")) from None

    def __hash__(self) -> int:
        try:
            return hash(self.canonical_bytes())
        except BaseException as error:
            raise CoverageError(_safe_coverage_code(error, "invalid_manifest")) from None


def build_coverage_manifest(
    *,
    inventory: ForgeSnapshotInventory,
    attempt: CoverageAttemptBinding,
    policy_observation: PolicyObservation,
    observations: tuple[FileCoverageObservation, ...],
) -> CoverageManifest:
    """Build one exact current-inventory coverage projection without reading bytes."""

    try:
        accepted = _canonical_inventory(inventory)
        if type(attempt) is not CoverageAttemptBinding or type(policy_observation) is not PolicyObservation:
            raise ValueError
        policy_bytes = policy_observation.to_canonical_bytes()
        if type(policy_bytes) is not bytes:
            raise ValueError
        policy = PolicyObservation.from_canonical_bytes(policy_bytes)
        detached_attempt = _attempt_from_record(_capture_attempt_record(attempt))
        detached_observations = _capture_observation_snapshots(observations)
        authority_values = (
            accepted.authority_binding.adapter_id,
            accepted.authority_binding.adapter_version,
            accepted.authority_binding.adapter_generation,
            accepted.authority_binding.admission_policy_generation,
        )
        if authority_values != (
            detached_attempt.adapter_id,
            detached_attempt.adapter_version,
            detached_attempt.adapter_generation,
            detached_attempt.admission_policy_generation,
        ):
            raise ValueError
        _validate_policy_binding(policy, accepted, detached_attempt, detached_observations)

        inventory_by_path = {item.path: item for item in accepted.files}
        observation_by_path: dict[str, tuple[object, ...]] = {}
        for observation in detached_observations:
            path = observation[0]
            if type(path) is not str or path in observation_by_path:
                raise ValueError
            observation_by_path[path] = observation
        if set(observation_by_path) != set(inventory_by_path):
            raise ValueError

        records: list[CoverageFileRecord] = []
        for path in sorted(inventory_by_path):
            descriptor = inventory_by_path[path]
            observation = observation_by_path[path]
            if (
            observation[0],
            observation[1],
            observation[2],
        ) != (
            descriptor.path,
            descriptor.content_sha256,
            descriptor.byte_count,
            ):
                raise ValueError
            records.append(_file_record(
                inventory=accepted,
                descriptor=descriptor,
                attempt=detached_attempt,
                observation_snapshot=observation,
            ))

        files = tuple(records)
        totals = _coverage_totals(files)
        status = _derived_status(detached_attempt, totals)
        return CoverageManifest._create(inventory=accepted, attempt=detached_attempt, files=files, totals=totals, status=status, whole_codebase_claim=_whole_codebase_claim(status, totals), policy_observation_digest=policy.observation_digest)
    except BaseException as error:
        raise CoverageError(_safe_coverage_code(error, "invalid_observation")) from None


class CoverageLedgerEntry:
    __slots__ = ("_snapshot",)
    def __init__(self, sequence: int, coverage_id: str, scope_digest: str, supersedes_coverage_id: str, previous_entry_digest: str, entry_digest: str, canonical_bytes: bytes) -> None:
        try:
            if type(sequence) is not int or sequence < 1 or type(coverage_id) is not str or type(scope_digest) is not str or type(supersedes_coverage_id) is not str or type(previous_entry_digest) is not str or type(entry_digest) is not str or type(canonical_bytes) is not bytes:
                raise ValueError
            if sequence > MAX_COVERAGE_LEDGER_ENTRIES or len(canonical_bytes) > MAX_COVERAGE_CANONICAL_BYTES: raise CoverageLedgerError("budget_exceeded")
            _coverage_id(coverage_id, "coverage_id"); _sha256(scope_digest, "scope_digest"); _optional_coverage_id(supersedes_coverage_id, "supersedes_coverage_id"); _optional_sha256(previous_entry_digest, "previous_entry_digest"); _sha256(entry_digest, "entry_digest")
            manifest = CoverageManifest.from_canonical_bytes(bytes(canonical_bytes))
            if manifest.coverage_id != coverage_id or manifest.scope_digest() != scope_digest: raise ValueError
            object.__setattr__(self, "_snapshot", (sequence, coverage_id, scope_digest, supersedes_coverage_id, previous_entry_digest, entry_digest, bytes(canonical_bytes)))
        except BaseException as error: raise CoverageLedgerError(_safe_ledger_code(error)) from None
    def _values(self) -> tuple[object, ...]:
        try:
            value=object.__getattribute__(self, "_snapshot")
            if type(value) is not tuple or len(value)!=7 or type(value[0]) is not int or any(type(x) is not str for x in value[1:6]) or type(value[6]) is not bytes: raise ValueError
            return tuple(value)
        except BaseException as error: raise CoverageLedgerError(_safe_ledger_code(error)) from None
    @property
    def sequence(self) -> int: return self._values()[0]  # type: ignore[return-value]
    @property
    def coverage_id(self) -> str: return self._values()[1]  # type: ignore[return-value]
    @property
    def scope_digest(self) -> str: return self._values()[2]  # type: ignore[return-value]
    @property
    def supersedes_coverage_id(self) -> str: return self._values()[3]  # type: ignore[return-value]
    @property
    def previous_entry_digest(self) -> str: return self._values()[4]  # type: ignore[return-value]
    @property
    def entry_digest(self) -> str: return self._values()[5]  # type: ignore[return-value]
    @property
    def canonical_bytes(self) -> bytes: return bytes(self._values()[6])  # type: ignore[arg-type]
    def __repr__(self) -> str:
        try: self._values(); return "CoverageLedgerEntry(valid)"
        except BaseException: return "CoverageLedgerEntry(invalid)"
    def __eq__(self, other: object) -> bool:
        if type(other) is not CoverageLedgerEntry: return NotImplemented
        try: return self._values() == other._values()
        except BaseException as error: raise CoverageLedgerError(_safe_ledger_code(error)) from None
    def __hash__(self) -> int:
        try: return hash(self._values())
        except BaseException as error: raise CoverageLedgerError(_safe_ledger_code(error)) from None


class CoverageManifestLedger:
    """Bounded append-only hash chain over immutable canonical manifest bytes."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._entries: tuple[bytes, ...] = ()

    @classmethod
    def from_canonical_entries(
        cls, canonical_entries: tuple[bytes, ...]
    ) -> "CoverageManifestLedger":
        try:
            if type(canonical_entries) is not tuple or any(type(item) is not bytes for item in canonical_entries):
                raise ValueError
            ledger = cls()
            ledger._entries = tuple(bytes(item) for item in canonical_entries)
            ledger._replay()
            return ledger
        except BaseException as error:
            raise CoverageLedgerError(_safe_ledger_code(error)) from None

    def __len__(self) -> int:
        with self._lock:
            self._replay()
            return len(self._entries)

    @property
    def head_digest(self) -> str:
        with self._lock:
            replayed = self._replay()
            return "" if not replayed else replayed[-1].entry_digest

    def entries(self) -> tuple[CoverageLedgerEntry, ...]:
        with self._lock:
            return self._replay()

    def append(
        self,
        manifest: CoverageManifest,
        *,
        expected_head_digest: str,
        supersedes_coverage_id: str = "",
    ) -> CoverageLedgerEntry:
        try:
            if type(manifest) is not CoverageManifest:
                raise ValueError
            candidate = manifest.canonical_bytes()
            detached_manifest = CoverageManifest.from_canonical_bytes(candidate)
            expected = _optional_sha256(expected_head_digest, "expected_head_digest")
            supersedes = _optional_coverage_id(supersedes_coverage_id, "supersedes_coverage_id")
            with self._lock:
                prior = self._replay()
                head = "" if not prior else prior[-1].entry_digest
                for entry in prior:
                    if entry.coverage_id == detached_manifest.coverage_id:
                        if entry.canonical_bytes != candidate or entry.supersedes_coverage_id != supersedes or expected not in {entry.previous_entry_digest, head}:
                            raise CoverageLedgerError("supersession_conflict")
                        if entry is not prior[-1]:
                            raise CoverageLedgerError("supersession_conflict")
                        return entry
                if expected != head:
                    raise CoverageLedgerError("head_conflict")
                base = {"schema": COVERAGE_LEDGER_SCHEMA, "sequence": len(prior) + 1, "coverage_id": detached_manifest.coverage_id, "scope_digest": detached_manifest.scope_digest(), "supersedes_coverage_id": supersedes, "previous_entry_digest": head, "canonical_bytes": candidate.hex()}
                record = dict(base)
                record["entry_digest"] = _digest(_canonical_bytes(base))
                raw = _canonical_bytes(record)
                proposed = (*self._entries, raw)
                replayed = self._replay(proposed)
                self._entries = proposed
                return replayed[-1]
        except BaseException as error:
            raise CoverageLedgerError(_safe_ledger_code(error)) from None

    def _replay(self, entries: tuple[bytes, ...] | None = None) -> tuple[CoverageLedgerEntry, ...]:
        raw_entries = self._entries if entries is None else entries
        try:
            if type(raw_entries) is not tuple or len(raw_entries) > MAX_COVERAGE_LEDGER_ENTRIES or sum(len(item) for item in raw_entries if type(item) is bytes) > MAX_COVERAGE_LEDGER_TOTAL_BYTES:
                raise CoverageLedgerError("budget_exceeded")
            previous = ""; seen: set[str] = set(); latest: dict[str, CoverageManifest] = {}; result: list[CoverageLedgerEntry] = []
            for sequence, raw in enumerate(raw_entries, 1):
                item = _decode_canonical_object(raw)
                required = {"schema", "sequence", "coverage_id", "scope_digest", "supersedes_coverage_id", "previous_entry_digest", "canonical_bytes", "entry_digest"}
                if set(item) != required or item["schema"] != COVERAGE_LEDGER_SCHEMA or type(item["sequence"]) is not int or item["sequence"] != sequence or item["previous_entry_digest"] != previous or type(item["canonical_bytes"]) is not str:
                    raise ValueError
                manifest = CoverageManifest.from_canonical_bytes(bytes.fromhex(item["canonical_bytes"]))
                if item["coverage_id"] != manifest.coverage_id or item["scope_digest"] != manifest.scope_digest() or manifest.coverage_id in seen:
                    raise ValueError
                supersedes = _optional_coverage_id(item["supersedes_coverage_id"], "supersedes_coverage_id")
                prior = latest.get(manifest.scope_digest())
                if prior is None:
                    if supersedes: raise CoverageLedgerError("supersession_conflict")
                else:
                    if supersedes != prior.coverage_id or manifest.attempt.attempt_count <= prior.attempt.attempt_count or manifest.attempt.store_revision < prior.attempt.store_revision:
                        raise CoverageLedgerError("supersession_conflict")
                base = dict(item); supplied = _sha256(base.pop("entry_digest"), "entry_digest")
                if supplied != _digest(_canonical_bytes(base)): raise ValueError
                entry = CoverageLedgerEntry(sequence, manifest.coverage_id, manifest.scope_digest(), supersedes, previous, supplied, manifest.canonical_bytes())
                result.append(entry); latest[entry.scope_digest] = manifest; seen.add(entry.coverage_id); previous = supplied
            return tuple(result)
        except BaseException as error:
            raise CoverageLedgerError(_safe_ledger_code(error)) from None


def _file_record(
    *,
    inventory: ForgeSnapshotInventory,
    descriptor: ForgeSnapshotFile,
    attempt: CoverageAttemptBinding,
    observation_snapshot: tuple[object, ...],
) -> CoverageFileRecord:
    if type(observation_snapshot) is not tuple or len(observation_snapshot) != 9:
        raise CoverageError("invalid_observation")
    state_value = observation_snapshot[3]
    line_count = observation_snapshot[4]
    classification_value = observation_snapshot[5]
    occurrence_snapshots = observation_snapshot[6]
    exclusion = observation_snapshot[7]
    failure_value = observation_snapshot[8]
    if (
        type(state_value) is not str
        or type(line_count) is not int
        or classification_value is not None and type(classification_value) is not str
        or type(occurrence_snapshots) is not tuple
        or type(exclusion) is not str
        or failure_value is not None and type(failure_value) is not str
    ):
        raise CoverageError("invalid_observation")
    state = FileObservationState(state_value)
    classification = None if classification_value is None else TextClassification(classification_value)
    failure = None if failure_value is None else CoverageFailureCode(failure_value)
    if state is FileObservationState.BINARY:
        denominator = DirectLineDenominator.NON_TEXT
        eligible = 0
        covered: tuple[tuple[int, int], ...] = ()
        uncovered: tuple[tuple[int, int], ...] = ()
        occurrence_ids: tuple[str, ...] = ()
    elif state is FileObservationState.POLICY_EXCLUDED_TEXT:
        denominator = DirectLineDenominator.POLICY_OUT_OF_SCOPE
        eligible = 0
        covered = ()
        uncovered = ()
        occurrence_ids = ()
    else:
        denominator = DirectLineDenominator.INCLUDED
        eligible = line_count
        intervals: list[tuple[int, int]] = []
        occurrence_ids_list: list[str] = []
        seen_occurrences: set[str] = set()
        for occurrence in occurrence_snapshots:
            primitive = _validated_occurrence_snapshot(occurrence)
            if (
                primitive[0], primitive[1], primitive[2], primitive[3], primitive[4],
                primitive[9], primitive[10],
            ) != (
                inventory.owner_scope,
                inventory.repo_id,
                inventory.version_id,
                inventory.commit_sha,
                inventory.snapshot_digest,
                descriptor.path,
                descriptor.content_sha256,
            ):
                raise CoverageError("coverage occurrence crosses current Forge inventory")
            if (
                primitive[5], primitive[6], primitive[7], primitive[8],
            ) != (
                attempt.adapter_id,
                attempt.adapter_version,
                attempt.adapter_generation,
                attempt.admission_policy_generation,
            ):
                raise CoverageError("coverage occurrence crosses adapter authority")
            source_id = primitive[17]
            source_version_id = primitive[18]
            occurrence_id = primitive[23]
            if type(source_id) is not str or type(source_version_id) is not str or type(occurrence_id) is not str:
                raise CoverageError("invalid_observation")
            if source_id not in attempt.source_ids or source_version_id not in attempt.source_version_ids:
                raise CoverageError("coverage occurrence is outside the accepted job source scope")
            if occurrence_id in seen_occurrences:
                raise CoverageError("coverage repeats one occurrence identity")
            seen_occurrences.add(occurrence_id)
            occurrence_ids_list.append(occurrence_id)
            start_line = primitive[12]
            end_line = primitive[14]
            end_column = primitive[15]
            if type(start_line) is not int or type(end_line) is not int or type(end_column) is not int:
                raise CoverageError("invalid_observation")
            last_line = end_line if end_column > 0 else end_line - 1
            if line_count < 1 or start_line > line_count or last_line < start_line or last_line > line_count:
                raise CoverageError("coverage locator exceeds the physical line denominator")
            intervals.append((start_line, last_line))
        covered = _merge_ranges(tuple(intervals))
        uncovered = _complement_ranges(covered, line_count)
        occurrence_ids = tuple(sorted(occurrence_ids_list))
    return CoverageFileRecord(
        path_ref=descriptor.path,
        blob_digest=descriptor.content_sha256,
        byte_count=descriptor.byte_count,
        state=state,
        text_classification=classification,
        total_line_count=line_count,
        direct_line_denominator=denominator,
        eligible_line_count=eligible,
        covered_line_ranges=covered,
        uncovered_eligible_ranges=uncovered,
        exclusion_evidence_ref=exclusion,
        failure_code=failure,
        occurrence_ids=occurrence_ids,
    )


def _coverage_totals(files: tuple[CoverageFileRecord, ...]) -> CoverageTotals:
    eligible = tuple(item for item in files if item.direct_line_denominator is DirectLineDenominator.INCLUDED)
    return CoverageTotals(
        discovered_files=len(files),
        eligible_files=len(eligible),
        excluded_files=sum(item.direct_line_denominator is DirectLineDenominator.POLICY_OUT_OF_SCOPE for item in files),
        non_text_files=sum(item.direct_line_denominator is DirectLineDenominator.NON_TEXT for item in files),
        unreadable_files=sum(item.state is FileObservationState.FAILURE for item in files),
        stale_files=sum(item.state is FileObservationState.STALE for item in files),
        eligible_lines=sum(item.eligible_line_count for item in eligible),
        covered_eligible_lines=sum(_range_size(item.covered_line_ranges) for item in eligible),
        uncovered_eligible_lines=sum(_range_size(item.uncovered_eligible_ranges) for item in eligible),
        occurrence_count=sum(len(item.occurrence_ids) for item in files),
    )


def _derived_status(
    attempt: CoverageAttemptBinding,
    totals: CoverageTotals,
) -> CoverageStatus:
    if attempt.job_status in {JobStatus.FAILED, JobStatus.BLOCKED, JobStatus.CANCELLED} or totals.unreadable_files:
        return CoverageStatus.FAILED
    if totals.stale_files:
        return CoverageStatus.STALE
    if totals.uncovered_eligible_lines:
        return CoverageStatus.PARTIAL
    return CoverageStatus.COMPLETE


def _whole_codebase_claim(
    status: CoverageStatus,
    totals: CoverageTotals,
) -> WholeCodebaseClaim:
    if totals.excluded_files:
        return WholeCodebaseClaim.FORBIDDEN_POLICY_EXCLUDED_TEXT
    if status is CoverageStatus.COMPLETE:
        return WholeCodebaseClaim.ALLOWED
    return WholeCodebaseClaim.NOT_ALLOWED_INCOMPLETE


def _assert_file_record(record: CoverageFileRecord) -> None:
    try:
        descriptor = ForgeSnapshotFile(
            record.path_ref,
            record.blob_digest,
            record.byte_count,
        )
    except ForgeSnapshotError:
        raise CoverageError("coverage file record descriptor is invalid") from None
    if descriptor.path != record.path_ref or descriptor.content_sha256 != record.blob_digest:
        raise CoverageError("coverage file record descriptor is not canonical")
    if type(record.state) is not FileObservationState:
        raise CoverageError("coverage file state is not canonical")
    if record.text_classification is not None and type(record.text_classification) is not TextClassification:
        raise CoverageError("coverage text classification is not canonical")
    if type(record.direct_line_denominator) is not DirectLineDenominator:
        raise CoverageError("coverage denominator is not canonical")
    if record.failure_code is not None and type(record.failure_code) is not CoverageFailureCode:
        raise CoverageError("coverage failure code is not canonical")
    for name in ("total_line_count", "eligible_line_count"):
        value = getattr(record, name)
        if type(value) is not int or not 0 <= value <= MAX_COVERAGE_LINE_COUNT:
            raise CoverageError(f"coverage {name} is invalid")
    for name in ("covered_line_ranges", "uncovered_eligible_ranges"):
        ranges = getattr(record, name)
        if type(ranges) is not tuple or any(
            type(item) is not tuple
            or len(item) != 2
            or any(type(value) is not int for value in item)
            for item in ranges
        ):
            raise CoverageError(f"coverage {name} is not an immutable range tuple")
        if _merge_ranges(ranges) != ranges:
            raise CoverageError(f"coverage {name} is not canonical")
    if type(record.occurrence_ids) is not tuple or tuple(sorted(set(record.occurrence_ids))) != record.occurrence_ids:
        raise CoverageError("coverage occurrence IDs are not a canonical unique tuple")
    for occurrence_id in record.occurrence_ids:
        _token(occurrence_id, "occurrence_id")
    if type(record.exclusion_evidence_ref) is not str:
        raise CoverageError("coverage exclusion evidence is not exact text")

    if record.direct_line_denominator is DirectLineDenominator.INCLUDED:
        if record.state not in {
            FileObservationState.TEXT,
            FileObservationState.EMPTY_TEXT,
            FileObservationState.FAILURE,
            FileObservationState.STALE,
        }:
            raise CoverageError("coverage included denominator has an invalid state")
        if record.eligible_line_count != record.total_line_count:
            raise CoverageError("coverage eligible-line denominator is inconsistent")
        if _complement_ranges(record.covered_line_ranges, record.total_line_count) != record.uncovered_eligible_ranges:
            raise CoverageError("coverage covered and uncovered ranges are not exact complements")
    elif record.direct_line_denominator is DirectLineDenominator.POLICY_OUT_OF_SCOPE:
        if record.state is not FileObservationState.POLICY_EXCLUDED_TEXT or record.eligible_line_count != 0:
            raise CoverageError("coverage policy exclusion is inconsistent")
        _opaque_reference(record.exclusion_evidence_ref, "exclusion_evidence_ref")
        if record.covered_line_ranges or record.uncovered_eligible_ranges or record.occurrence_ids:
            raise CoverageError("coverage policy exclusion cannot carry occurrence ranges")
    else:
        if (
            record.state is not FileObservationState.BINARY
            or record.total_line_count != 0
            or record.eligible_line_count != 0
            or record.text_classification is not None
            or record.covered_line_ranges
            or record.uncovered_eligible_ranges
            or record.occurrence_ids
        ):
            raise CoverageError("coverage non-text record is inconsistent")

    if record.state is FileObservationState.TEXT:
        if record.text_classification is None or record.failure_code is not None or record.exclusion_evidence_ref:
            raise CoverageError("coverage text record fields are inconsistent")
    elif record.state is FileObservationState.EMPTY_TEXT:
        if (
            record.text_classification is None
            or record.byte_count != 0
            or record.total_line_count != 0
            or record.occurrence_ids
            or record.failure_code is not None
            or record.exclusion_evidence_ref
        ):
            raise CoverageError("coverage empty-text record fields are inconsistent")
    elif record.state is FileObservationState.FAILURE:
        if record.text_classification is None or record.failure_code is None or record.occurrence_ids:
            raise CoverageError("coverage failure record fields are inconsistent")
    elif record.state is FileObservationState.STALE:
        if record.text_classification is None or record.failure_code is not None or record.occurrence_ids:
            raise CoverageError("coverage stale record fields are inconsistent")


def _locator_line_interval(locator: CodeRangeLocator, *, total_line_count: int) -> tuple[int, int]:
    if type(locator) is not CodeRangeLocator:
        raise CoverageError("coverage locator must use the exact CodeRangeLocator type")
    try:
        canonical = CodeRangeLocator(
            locator.path,
            locator.start_line,
            locator.start_column,
            locator.end_line,
            locator.end_column,
        )
    except Exception:
        raise CoverageError("coverage locator is not canonical") from None
    last_line = canonical.end_line if canonical.end_column > 0 else canonical.end_line - 1
    if (
        total_line_count < 1
        or canonical.start_line > total_line_count
        or last_line < canonical.start_line
        or last_line > total_line_count
    ):
        raise CoverageError("coverage locator exceeds the physical line denominator")
    return canonical.start_line, last_line


def _merge_ranges(ranges: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if type(start) is not int or type(end) is not int or start < 1 or end < start:
            raise CoverageError("coverage line range is invalid")
        if not merged or start > merged[-1][1] + 1:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return tuple(merged)


def _complement_ranges(
    covered: tuple[tuple[int, int], ...],
    total_line_count: int,
) -> tuple[tuple[int, int], ...]:
    if total_line_count == 0:
        return ()
    gaps: list[tuple[int, int]] = []
    cursor = 1
    for start, end in covered:
        if cursor < start:
            gaps.append((cursor, start - 1))
        cursor = max(cursor, end + 1)
    if cursor <= total_line_count:
        gaps.append((cursor, total_line_count))
    return tuple(gaps)


def _range_size(ranges: tuple[tuple[int, int], ...]) -> int:
    return sum(end - start + 1 for start, end in ranges)


def _canonical_inventory(value: object) -> ForgeSnapshotInventory:
    if type(value) is not ForgeSnapshotInventory:
        raise CoverageError("coverage inventory must use the exact ForgeSnapshotInventory type")
    try:
        owner_scope, repo_id, version_id, commit_sha, manifest_sha256, raw_authority, raw_files, snapshot_digest = tuple(
            object.__getattribute__(value, name)
            for name in ("owner_scope", "repo_id", "version_id", "commit_sha", "manifest_sha256", "authority_binding", "files", "snapshot_digest")
        )
        if type(raw_authority) is not ForgeSnapshotAuthorityBinding or type(raw_files) is not tuple or any(type(item) is not ForgeSnapshotFile for item in raw_files):
            raise ValueError
        authority_values = tuple(object.__getattribute__(raw_authority, name) for name in ("adapter_id", "adapter_version", "adapter_generation", "admission_policy_generation"))
        if any(type(item) is not str for item in (owner_scope, repo_id, version_id, commit_sha, manifest_sha256, snapshot_digest, *authority_values)):
            raise ValueError
        authority = ForgeSnapshotAuthorityBinding(
            *authority_values,
        )
        file_values = tuple(tuple(object.__getattribute__(item, name) for name in ("path", "content_sha256", "byte_count")) for item in raw_files)
        if any(type(path) is not str or type(digest) is not str or type(size) is not int for path, digest, size in file_values):
            raise ValueError
        files = tuple(ForgeSnapshotFile(*item) for item in file_values)
        canonical = ForgeSnapshotInventory(
            owner_scope=owner_scope,
            repo_id=repo_id,
            version_id=version_id,
            commit_sha=commit_sha,
            manifest_sha256=manifest_sha256,
            authority_binding=authority,
            files=files,
            snapshot_digest=snapshot_digest,
        )
    except BaseException:
        raise CoverageError("coverage inventory is not canonical current Forge evidence") from None
    return canonical


def _canonical_occurrence(value: object) -> ForgeCodeOccurrence:
    if type(value) is not ForgeCodeOccurrence:
        raise CoverageError("coverage occurrence must use the exact FCA-01 type")
    try:
        owner_scope, repo_id, version_id, commit_sha, snapshot_digest, authority, path, file_digest, locator, profile, records, occurrence_id = tuple(
            object.__getattribute__(value, name)
            for name in ("owner_scope", "repo_id", "version_id", "commit_sha", "snapshot_digest", "authority_binding", "path", "file_content_sha256", "locator", "extractor_profile_ref", "records", "occurrence_id")
        )
        if type(authority) is not ForgeSnapshotAuthorityBinding or type(locator) is not CodeRangeLocator:
            raise ValueError
        authority_values = tuple(object.__getattribute__(authority, name) for name in ("adapter_id", "adapter_version", "adapter_generation", "admission_policy_generation"))
        locator_values = tuple(object.__getattribute__(locator, name) for name in ("path", "start_line", "start_column", "end_line", "end_column"))
        if any(type(item) is not str for item in (owner_scope, repo_id, version_id, commit_sha, snapshot_digest, path, file_digest, profile, occurrence_id, *authority_values)) or type(locator_values[0]) is not str or any(type(item) is not int for item in locator_values[1:]):
            raise ValueError
        canonical_records = validate_forge_code_occurrence_records(records)
        canonical = ForgeCodeOccurrence(
            owner_scope=owner_scope, repo_id=repo_id, version_id=version_id,
            commit_sha=commit_sha, snapshot_digest=snapshot_digest,
            authority_binding=ForgeSnapshotAuthorityBinding(*authority_values),
            path=path, file_content_sha256=file_digest,
            locator=CodeRangeLocator(*locator_values),
            extractor_profile_ref=profile, records=canonical_records,
            occurrence_id=occurrence_id,
        )
    except BaseException:
        raise CoverageError("coverage occurrence is not canonical FCA-01 evidence") from None
    return canonical


def _occurrence_snapshot(value: object) -> tuple[object, ...]:
    """Capture one occurrence as recursively exact primitive evidence."""
    try:
        canonical = _canonical_occurrence(value)
        authority = canonical.authority_binding
        locator = canonical.locator
        records = canonical.records
        evidence = records.forge_evidence
        if type(evidence) is not ForgeCodeOccurrenceEvidence:
            raise ValueError
        snapshot = (
            canonical.owner_scope, canonical.repo_id, canonical.version_id,
            canonical.commit_sha, canonical.snapshot_digest,
            authority.adapter_id, authority.adapter_version, authority.adapter_generation,
            authority.admission_policy_generation, canonical.path, canonical.file_content_sha256,
            locator.path, locator.start_line, locator.start_column, locator.end_line, locator.end_column,
            canonical.extractor_profile_ref, records.source.source_id,
            records.source_version.source_version_id, records.source.to_json(),
            records.source_version.to_json(), records.chunk.to_json(), evidence.to_json(),
            canonical.occurrence_id,
        )
        if (type(snapshot) is not tuple or any(type(item) is not str for item in (*snapshot[:12], *snapshot[16:]))
                or any(type(item) is not int for item in snapshot[12:16])):
            raise ValueError
        return tuple(item for item in snapshot)
    except BaseException as error:
        raise CoverageError(_safe_coverage_code(error, "invalid_observation")) from None


def _occurrence_from_snapshot(value: object) -> ForgeCodeOccurrence:
    try:
        snapshot = _validated_occurrence_snapshot(value)
        authority = ForgeSnapshotAuthorityBinding(snapshot[5], snapshot[6], snapshot[7], snapshot[8])
        locator = CodeRangeLocator(snapshot[11], snapshot[12], snapshot[13], snapshot[14], snapshot[15])
        records = CodeOccurrenceRecords(
            SourceRecord.from_json(snapshot[19]), SourceVersionRecord.from_json(snapshot[20]),
            ChunkRecord.from_json(snapshot[21]), ForgeCodeOccurrenceEvidence.from_json(snapshot[22]),
        )
        return ForgeCodeOccurrence(
            snapshot[0], snapshot[1], snapshot[2], snapshot[3], snapshot[4], authority, snapshot[9], snapshot[10],
            locator, snapshot[16], records, snapshot[23],
        )
    except BaseException as error:
        raise CoverageError(_safe_coverage_code(error, "invalid_observation")) from None


def _validated_occurrence_snapshot(value: object) -> tuple[object, ...]:
    """Validate a primitive occurrence snapshot without constructing its domain value."""
    try:
        if type(value) is not tuple or len(value) != 24:
            raise ValueError
        if any(type(item) is not str for item in (*value[:12], *value[16:])) or any(
            type(item) is not int for item in value[12:16]
        ):
            raise ValueError
        source = SourceRecord.from_json(value[19])
        source_version = SourceVersionRecord.from_json(value[20])
        chunk = ChunkRecord.from_json(value[21])
        evidence = ForgeCodeOccurrenceEvidence.from_json(value[22])
        records = validate_forge_code_occurrence_records(
            CodeOccurrenceRecords(source, source_version, chunk, evidence)
        )
        canonical_evidence = records.forge_evidence
        if type(canonical_evidence) is not ForgeCodeOccurrenceEvidence:
            raise ValueError
        if (
            records.source.source_id,
            records.source_version.source_version_id,
        ) != (value[17], value[18]):
            raise ValueError
        if (
            canonical_evidence.owner_scope, canonical_evidence.repo_id,
            canonical_evidence.version_id, canonical_evidence.commit_sha,
            canonical_evidence.snapshot_digest, *canonical_evidence.authority_binding,
            canonical_evidence.path, canonical_evidence.file_content_sha256,
            canonical_evidence.locator.path, canonical_evidence.locator.start_line,
            canonical_evidence.locator.start_column, canonical_evidence.locator.end_line,
            canonical_evidence.locator.end_column,
        ) != (
            *value[:11], value[11], value[12], value[13], value[14], value[15],
        ):
            raise ValueError
        if (
            records.chunk.extractor_profile_ref != value[16]
            or records.chunk.source_id != value[17]
            or records.chunk.source_version_id != value[18]
        ):
            raise ValueError
        return tuple(item for item in value)
    except BaseException as error:
        raise CoverageError(_safe_coverage_code(error, "invalid_observation")) from None


def _capture_attempt_record(value: object) -> tuple[object, ...]:
    try:
        if type(value) is not CoverageAttemptBinding:
            raise ValueError
        record = object.__getattribute__(value, "_snapshot")
        return _validated_attempt_snapshot(record)
    except BaseException as error:
        raise CoverageError(_safe_coverage_code(error, "invalid_attempt")) from None


def _validated_attempt_snapshot(record: object) -> tuple[object, ...]:
    """Validate an exact built-in attempt record without invoking user callbacks."""
    try:
        if type(record) is not tuple or len(record) != len(_ATTEMPT_FIELDS):
            raise ValueError
        # Verify every scalar/container type before any equality, hash, sorting,
        # serialization, or enum operation.  This is the hostile-callback gate.
        if (
            type(record[0]) is not str or type(record[1]) is not str
            or type(record[2]) is not int or type(record[3]) is not str
            or type(record[4]) is not str or type(record[5]) is not tuple
            or type(record[6]) is not tuple
            or any(type(record[index]) is not int for index in (7, 9, 10))
            or any(type(record[index]) is not str for index in (8, 11, 12, 13, 14, 15, 16))
            or any(type(item) is not str for item in record[5])
            or any(type(item) is not str for item in record[6])
        ):
            raise ValueError
        if record[1] not in {item.value for item in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.BLOCKED, JobStatus.CANCELLED)}:
            raise ValueError
        for index in (2, 7, 9, 10):
            if type(record[index]) is not int or record[index] < 0:
                raise ValueError
        if not 1 <= record[2] <= 10_000:
            raise ValueError
        for index in (0, 4, 11, 12, 13, 14, 15, 16):
            if type(record[index]) is not str:
                raise ValueError
            _token(record[index], _ATTEMPT_FIELDS[index])
        for index in (3, 8):
            if type(record[index]) is not str:
                raise ValueError
            _sha256(record[index], _ATTEMPT_FIELDS[index])
        for index in (5, 6):
            values = record[index]
            if type(values) is not tuple or not values or any(type(item) is not str for item in values):
                raise ValueError
            for item in values:
                _token(item, _ATTEMPT_FIELDS[index])
            if tuple(sorted(set(values))) != values:
                raise ValueError
        return record
    except BaseException:
        raise CoverageError("invalid_attempt") from None


def _attempt_from_record(record: tuple[object, ...]) -> CoverageAttemptBinding:
    record = _validated_attempt_snapshot(record)
    result = object.__new__(CoverageAttemptBinding)
    object.__setattr__(result, "_snapshot", tuple(record))
    result._record()
    return result


def _attempt_record_to_dict(record: tuple[object, ...]) -> dict[str, object]:
    return {
        name: (
            list(item) if name in {"source_ids", "source_version_ids"}
            else item
        )
        for name, item in zip(_ATTEMPT_FIELDS, record, strict=True)
    }


def _validated_observation_snapshot(value: object) -> tuple[object, ...]:
    """Normalize one private observation slot to exact primitive evidence."""
    try:
        if type(value) is not tuple or len(value) != 9:
            raise ValueError
        path, digest, byte_count, state_value, line_count, classification_value, raw_occurrences, exclusion, failure_value = value
        if type(line_count) is int and line_count > MAX_COVERAGE_LINE_COUNT:
            raise CoverageError("budget_exceeded")
        if type(raw_occurrences) is tuple and len(raw_occurrences) > MAX_COVERAGE_OCCURRENCES_PER_FILE:
            raise CoverageError("budget_exceeded")
        if (
            type(path) is not str or type(digest) is not str
            or type(byte_count) is not int or type(line_count) is not int
            or type(state_value) is not str
            or classification_value is not None and type(classification_value) is not str
            or type(raw_occurrences) is not tuple or type(exclusion) is not str
            or failure_value is not None and type(failure_value) is not str
        ):
            raise ValueError
        descriptor = ForgeSnapshotFile(path, digest, byte_count)
        state = FileObservationState(state_value)
        classification = None if classification_value is None else TextClassification(classification_value)
        failure = None if failure_value is None else CoverageFailureCode(failure_value)
        if not 0 <= line_count <= MAX_COVERAGE_LINE_COUNT:
            raise ValueError
        occurrences = tuple(_validated_occurrence_snapshot(item) for item in raw_occurrences)
        if state is FileObservationState.TEXT:
            if classification is None or line_count < 1 or exclusion or failure is not None:
                raise ValueError
        elif state is FileObservationState.EMPTY_TEXT:
            if classification is None or line_count != 0 or descriptor.byte_count != 0 or occurrences or exclusion or failure is not None:
                raise ValueError
        elif state is FileObservationState.BINARY:
            if classification is not None or line_count != 0 or occurrences or exclusion or failure is not None:
                raise ValueError
        elif state is FileObservationState.POLICY_EXCLUDED_TEXT:
            if classification is None or occurrences or failure is not None:
                raise ValueError
            _opaque_reference(exclusion, "exclusion_evidence_ref")
        elif state is FileObservationState.FAILURE:
            if classification is None or occurrences or exclusion or failure is None:
                raise ValueError
        elif state is FileObservationState.STALE:
            if classification is None or occurrences or exclusion or failure is not None:
                raise ValueError
        else:
            raise ValueError
        return (
            descriptor.path, descriptor.content_sha256, descriptor.byte_count,
            state.value, line_count, None if classification is None else classification.value,
            tuple(item for item in occurrences), exclusion,
            None if failure is None else failure.value,
        )
    except BaseException as error:
        raise CoverageError(_safe_coverage_code(error, "invalid_observation")) from None


def _capture_observation_snapshots(value: object) -> tuple[tuple[object, ...], ...]:
    """Capture each exact observation's private slot once and discard the object."""
    try:
        if type(value) is not tuple or len(value) > MAX_FORGE_SNAPSHOT_FILES:
            raise ValueError
        captured: list[tuple[object, ...]] = []
        for observation in value:
            if type(observation) is not FileCoverageObservation:
                raise ValueError
            slot = object.__getattribute__(observation, "_snapshot")
            captured.append(_validated_observation_snapshot(slot))
        return tuple(item for item in captured)
    except BaseException as error:
        raise CoverageError(_safe_coverage_code(error, "invalid_observation")) from None


def _detach_observations(value: object) -> tuple[tuple[object, ...], ...]:
    """Compatibility-private alias; it retains no local observation objects."""
    return _capture_observation_snapshots(value)


def _manifest_identity(values: dict[str, object]) -> dict[str, object]:
    return {
        "schema": COVERAGE_SCHEMA,
        "owner_scope_ref": values["owner_scope"], "repo_id": values["repo_id"],
        "forge_revision_id": values["forge_revision_id"], "commit_sha": values["commit_sha"],
        "forge_snapshot_digest": values["forge_snapshot_digest"], "forge_manifest_digest": values["forge_manifest_digest"],
        "authority_binding": list(values["authority_binding"]),
        "attempt": values["attempt"].to_dict(),  # type: ignore[union-attr]
        "policy_observation_digest": values["policy_observation_digest"],
        "files": [item.to_dict() for item in values["files"]],  # type: ignore[union-attr]
        "totals": values["totals"].to_dict(),  # type: ignore[union-attr]
        "status": values["status"].value,  # type: ignore[union-attr]
        "whole_codebase_claim": values["whole_codebase_claim"].value,  # type: ignore[union-attr]
    }


def _file_record_snapshot(record: CoverageFileRecord) -> tuple[object, ...]:
    try:
        if type(record) is not CoverageFileRecord:
            raise ValueError
        value = object.__getattribute__(record, "_snapshot")
        _file_record_from_snapshot(value)
        return tuple(value)
    except BaseException as error:
        raise CoverageError(_safe_coverage_code(error, "invalid_manifest")) from None


def _file_record_from_snapshot(value: object) -> CoverageFileRecord:
    try:
        if type(value) is not tuple or len(value) != 13:
            raise ValueError
        return CoverageFileRecord(
            value[0], value[1], value[2], FileObservationState(value[3]),
            None if value[4] is None else TextClassification(value[4]), value[5],
            DirectLineDenominator(value[6]), value[7], tuple(tuple(item) for item in value[8]),
            tuple(tuple(item) for item in value[9]), value[10],
            None if value[11] is None else CoverageFailureCode(value[11]), tuple(value[12]),
        )
    except BaseException as error:
        raise CoverageError(_safe_coverage_code(error, "invalid_manifest")) from None


def _semantic_manifest_snapshot(value: object) -> tuple[object, ...]:
    """Keep only the ten semantic manifest fields; all other values are derived."""
    try:
        if type(value) is not dict:
            raise ValueError
        attempt = _attempt_from_dict(value["attempt"])
        files = tuple(_file_record_from_dict(item) for item in value["files"])
        snapshot = (
            value["owner_scope_ref"], value["repo_id"], value["forge_revision_id"], value["commit_sha"],
            value["forge_snapshot_digest"], value["forge_manifest_digest"], tuple(value["authority_binding"]),
            _capture_attempt_record(attempt), value["policy_observation_digest"],
            tuple(_file_record_snapshot(item) for item in files),
        )
        _manifest_payload_from_semantic(snapshot)
        return tuple(snapshot)
    except BaseException as error:
        raise CoverageError(_safe_coverage_code(error, "invalid_manifest")) from None


def _manifest_payload_from_semantic(snapshot: object) -> dict[str, object]:
    try:
        if type(snapshot) is not tuple or len(snapshot) != 10:
            raise ValueError
        owner, repo, revision, commit, snap_digest, manifest_digest, authority, attempt_record, policy_digest, records = snapshot
        if any(type(item) is not str for item in (owner, repo, revision, commit, snap_digest, manifest_digest, policy_digest)):
            raise ValueError
        _token(owner, "owner_scope_ref"); _token(repo, "repo_id"); _token(revision, "forge_revision_id"); _token(commit, "commit_sha")
        _sha256(snap_digest, "forge_snapshot_digest"); _sha256(manifest_digest, "forge_manifest_digest"); _sha256(policy_digest, "policy_observation_digest")
        if type(authority) is not tuple or len(authority) != 4 or any(type(item) is not str for item in authority):
            raise ValueError
        for item in authority: _token(item, "authority_binding")
        attempt = _attempt_from_record(attempt_record)
        if type(records) is not tuple or len(records) > MAX_FORGE_SNAPSHOT_FILES:
            raise CoverageError("budget_exceeded")
        files = tuple(_file_record_from_snapshot(item) for item in records)
        if tuple(sorted(item.path_ref for item in files)) != tuple(item.path_ref for item in files) or len({item.path_ref for item in files}) != len(files):
            raise ValueError
        totals = _coverage_totals(files)
        status = _derived_status(attempt, totals)
        claim = _whole_codebase_claim(status, totals)
        identity: dict[str, object] = {
            "schema": COVERAGE_SCHEMA, "owner_scope_ref": owner, "repo_id": repo,
            "forge_revision_id": revision, "commit_sha": commit, "forge_snapshot_digest": snap_digest,
            "forge_manifest_digest": manifest_digest, "authority_binding": list(authority),
            "attempt": attempt.to_dict(), "policy_observation_digest": policy_digest,
            "files": [item.to_dict() for item in files], "totals": totals.to_dict(),
            "status": status.value, "whole_codebase_claim": claim.value,
        }
        digest = _digest(_canonical_bytes(identity))
        return {**identity, "coverage_id": "cov_" + digest.removeprefix("sha256:"), "manifest_digest": digest}
    except BaseException as error:
        raise CoverageError(_safe_coverage_code(error, "invalid_manifest")) from None


def _freeze_json(value: object) -> object:
    if type(value) is dict:
        return ("d", tuple((key, _freeze_json(item)) for key, item in sorted(value.items())))
    if type(value) is list:
        return ("l", tuple(_freeze_json(item) for item in value))
    if type(value) in {str, int, type(None)}:
        return value
    raise ValueError


def _validate_frozen_json(value: object) -> None:
    """Reject private-snapshot tampering before any callback-capable operation."""
    if type(value) in {str, int, type(None)}:
        return
    if type(value) is not tuple or len(value) != 2 or type(value[0]) is not str or type(value[1]) is not tuple:
        raise CoverageError("invalid_manifest")
    marker = value[0]
    if marker == "d":
        for item in value[1]:
            if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str:
                raise CoverageError("invalid_manifest")
            _validate_frozen_json(item[1])
        return
    if marker == "l":
        for item in value[1]:
            _validate_frozen_json(item)
        return
    raise CoverageError("invalid_manifest")


def _thaw_json(value: object) -> object:
    if type(value) is tuple and len(value) == 2 and value[0] == "d" and type(value[1]) is tuple:
        return {item[0]: _thaw_json(item[1]) for item in value[1]}
    if type(value) is tuple and len(value) == 2 and value[0] == "l" and type(value[1]) is tuple:
        return [_thaw_json(item) for item in value[1]]
    if type(value) in {str, int, type(None)}:
        return value
    raise ValueError


def _attempt_from_dict(value: object) -> CoverageAttemptBinding:
    if type(value) is not dict:
        raise CoverageError("invalid_manifest")
    required = ("job_id", "job_status", "attempt_count", "job_record_digest", "source_scope_id", "source_ids", "source_version_ids", "store_revision", "store_state_hash", "store_record_count", "store_tombstone_count", "store_snapshot_ref", "adapter_id", "adapter_version", "adapter_generation", "admission_policy_generation", "indexing_policy_generation")
    if set(value) != set(required) or type(value["source_ids"]) is not list or type(value["source_version_ids"]) is not list:
        raise CoverageError("invalid_manifest")
    record: list[object] = []
    for name in required:
        item = value[name]
        if name in {"source_ids", "source_version_ids"}: item = tuple(item)
        record.append(item)
    return _attempt_from_record(tuple(record))


def _file_record_from_dict(value: object) -> CoverageFileRecord:
    if type(value) is not dict:
        raise CoverageError("invalid_manifest")
    required = {"path_ref", "blob_digest", "byte_count", "state", "text_classification", "total_line_count", "direct_line_denominator", "eligible_line_count", "covered_line_ranges", "uncovered_eligible_ranges", "exclusion", "failure_code", "occurrence_count", "occurrence_ids"}
    if set(value) != required or type(value["covered_line_ranges"]) is not list or type(value["uncovered_eligible_ranges"]) is not list or type(value["occurrence_ids"]) is not list:
        raise CoverageError("invalid_manifest")
    exclusion = value["exclusion"]
    reference = "" if exclusion is None else exclusion.get("evidence_ref") if type(exclusion) is dict and set(exclusion) == {"code", "evidence_ref"} and exclusion.get("code") == "policy_excluded" else None
    if reference is None: raise CoverageError("invalid_manifest")
    record = CoverageFileRecord(value["path_ref"], value["blob_digest"], value["byte_count"], FileObservationState(value["state"]), None if value["text_classification"] is None else TextClassification(value["text_classification"]), value["total_line_count"], DirectLineDenominator(value["direct_line_denominator"]), value["eligible_line_count"], tuple(tuple(item) for item in value["covered_line_ranges"]), tuple(tuple(item) for item in value["uncovered_eligible_ranges"]), reference, None if value["failure_code"] is None else CoverageFailureCode(value["failure_code"]), tuple(value["occurrence_ids"]))
    if value["occurrence_count"] != len(record.occurrence_ids): raise CoverageError("invalid_manifest")
    _assert_file_record(record)
    return record


def _totals_from_dict(value: object) -> CoverageTotals:
    if type(value) is not dict or set(value) != set(CoverageTotals(0, 0, 0, 0, 0, 0, 0, 0, 0, 0).to_dict()):
        raise CoverageError("invalid_manifest")
    return CoverageTotals(**value)  # type: ignore[arg-type]


def _decode_manifest_object(value: object) -> dict[str, Any]:
    if type(value) is not bytes or len(value) > MAX_COVERAGE_CANONICAL_BYTES:
        raise CoverageError("budget_exceeded" if type(value) is bytes else "invalid_manifest")
    try:
        decoded = json.loads(value.decode("utf-8"), object_pairs_hook=lambda pairs: _unique_pairs(pairs, CoverageError))
    except CoverageError: raise
    except Exception: raise CoverageError("invalid_manifest") from None
    if type(decoded) is not dict or _canonical_bytes(decoded) != value:
        raise CoverageError("invalid_manifest")
    return decoded


def _unique_pairs(pairs: list[tuple[str, Any]], error_type: type[ValueError]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if type(key) is not str or key in result: raise error_type("invalid_manifest")
        result[key] = item
    return result


def _validate_policy_binding(policy: PolicyObservation, inventory: ForgeSnapshotInventory, attempt: CoverageAttemptBinding, observations: tuple[tuple[object, ...], ...]) -> None:
    policy_values = (policy.owner_scope, policy.repo_id, policy.version_id, policy.commit_sha, policy.manifest_sha256, policy.snapshot_digest, policy.indexing_policy_generation)
    decisions_projection = policy.decisions
    if policy_values != (inventory.owner_scope, inventory.repo_id, inventory.version_id, inventory.commit_sha, inventory.manifest_sha256, inventory.snapshot_digest, attempt.indexing_policy_generation):
        raise ValueError
    authority = policy.authority_binding
    if (authority.adapter_id, authority.adapter_version, authority.adapter_generation, authority.admission_policy_generation) != (attempt.adapter_id, attempt.adapter_version, attempt.adapter_generation, attempt.admission_policy_generation): raise ValueError
    if type(decisions_projection) is not tuple or any(type(item) is not PolicyFileDecision for item in decisions_projection): raise ValueError
    decision_records = tuple((item.path, item.content_sha256, item.byte_count, item.decision, item.evidence_ref) for item in decisions_projection)
    if any(type(path) is not str or type(digest) is not str or type(size) is not int or type(decision) is not PolicyDecision or type(evidence) is not str for path, digest, size, decision, evidence in decision_records): raise ValueError
    decisions = {item[0]: item for item in decision_records}
    if len(decisions) != len(decision_records): raise ValueError
    for observation in observations:
        if type(observation) is not tuple or len(observation) != 9:
            raise ValueError
        path, digest, byte_count, state_value, exclusion = (
            observation[0], observation[1], observation[2], observation[3], observation[7]
        )
        if (type(path) is not str or type(digest) is not str or type(byte_count) is not int
                or type(state_value) is not str or type(exclusion) is not str):
            raise ValueError
        decision = decisions.get(path)
        if decision is None or (decision[1], decision[2]) != (digest, byte_count): raise ValueError
        state = FileObservationState(state_value)
        if state is FileObservationState.POLICY_EXCLUDED_TEXT:
            if decision[3] is not PolicyDecision.POLICY_OUT_OF_SCOPE or exclusion != decision[4]: raise ValueError
        elif state is not FileObservationState.BINARY and decision[3] is not PolicyDecision.IN_SCOPE:
            raise ValueError


def _validate_manifest_dict(value: dict[str, Any]) -> None:
    required = {
        "schema",
        "owner_scope_ref",
        "repo_id",
        "forge_revision_id",
        "commit_sha",
        "forge_snapshot_digest",
        "forge_manifest_digest",
        "authority_binding",
        "attempt",
        "policy_observation_digest",
        "files",
        "totals",
        "status",
        "whole_codebase_claim",
        "coverage_id",
        "manifest_digest",
    }
    if type(value) is not dict or set(value) != required or value.get("schema") != COVERAGE_SCHEMA:
        raise CoverageError("invalid_manifest")
    for name in ("owner_scope_ref", "repo_id", "forge_revision_id", "commit_sha"):
        _token(value[name], name)
    _sha256(value["forge_snapshot_digest"], "forge_snapshot_digest"); _sha256(value["forge_manifest_digest"], "forge_manifest_digest"); _sha256(value["policy_observation_digest"], "policy_observation_digest")
    if type(value["authority_binding"]) is not list or len(value["authority_binding"]) != 4: raise CoverageError("invalid_manifest")
    for item in value["authority_binding"]: _token(item, "authority_binding")
    attempt = _attempt_from_dict(value["attempt"])
    files_data = value["files"]
    if type(files_data) is not list or len(files_data) > MAX_FORGE_SNAPSHOT_FILES: raise CoverageError("budget_exceeded")
    files = tuple(_file_record_from_dict(item) for item in files_data)
    if tuple(sorted(item.path_ref for item in files)) != tuple(item.path_ref for item in files) or len({item.path_ref for item in files}) != len(files): raise CoverageError("invalid_manifest")
    totals = _totals_from_dict(value["totals"])
    if totals != _coverage_totals(files) or type(value["status"]) is not str or type(value["whole_codebase_claim"]) is not str or CoverageStatus(value["status"]) is not _derived_status(attempt, totals) or WholeCodebaseClaim(value["whole_codebase_claim"]) is not _whole_codebase_claim(_derived_status(attempt, totals), totals): raise CoverageError("invalid_manifest")
    coverage_id = _coverage_id(value["coverage_id"], "coverage_id")
    manifest_digest = _sha256(value["manifest_digest"], "manifest_digest")
    identity = dict(value)
    identity.pop("coverage_id")
    identity.pop("manifest_digest")
    expected = _digest(_canonical_bytes(identity))
    if manifest_digest != expected or coverage_id != "cov_" + expected.removeprefix("sha256:"):
        raise CoverageError("invalid_manifest")


def _scope_digest_from_manifest(value: dict[str, Any]) -> str:
    attempt = value.get("attempt")
    if type(attempt) is not dict:
        raise CoverageLedgerError("coverage manifest attempt binding is invalid")
    payload = {
        "schema": COVERAGE_SCHEMA + ".scope",
        "owner_scope_ref": value.get("owner_scope_ref"),
        "repo_id": value.get("repo_id"),
        "forge_revision_id": value.get("forge_revision_id"),
        "commit_sha": value.get("commit_sha"),
        "forge_snapshot_digest": value.get("forge_snapshot_digest"),
        "forge_manifest_digest": value.get("forge_manifest_digest"),
        "authority_binding": value.get("authority_binding"),
        "adapter_generation": attempt.get("adapter_generation"),
        "admission_policy_generation": attempt.get("admission_policy_generation"),
        "indexing_policy_generation": attempt.get("indexing_policy_generation"),
    }
    return _digest(_canonical_bytes(payload))


def _decode_canonical_object(value: object) -> dict[str, Any]:
    if type(value) is not bytes or len(value) > MAX_COVERAGE_CANONICAL_BYTES:
        raise CoverageLedgerError("coverage ledger bytes are invalid or unbounded")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise CoverageLedgerError("coverage ledger contains a duplicate JSON key")
            result[key] = item
        return result

    try:
        decoded = json.loads(value.decode("utf-8"), object_pairs_hook=pairs)
    except CoverageLedgerError:
        raise
    except Exception:
        raise CoverageLedgerError("coverage ledger record is not valid UTF-8 JSON") from None
    if type(decoded) is not dict or _canonical_bytes(decoded) != value:
        raise CoverageLedgerError("coverage ledger record is not canonical JSON")
    return decoded


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise CoverageError("coverage evidence cannot be canonically serialized") from None


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _token(value: object, field_name: str) -> str:
    if type(value) is not str or not _TOKEN_RE.fullmatch(value):
        raise CoverageError(f"{field_name} must be a bounded canonical reference")
    return value


def _opaque_reference(value: object, field_name: str) -> str:
    token = _token(value, field_name)
    if _SENSITIVE_REF_RE.search(token):
        raise CoverageError(f"{field_name} must not contain credential-shaped text")
    return token


def _sha256(value: object, field_name: str) -> str:
    if type(value) is not str or not _SHA256_RE.fullmatch(value):
        raise CoverageError(f"{field_name} must be a canonical SHA-256 digest")
    return value


def _optional_sha256(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise CoverageError("invalid_manifest")
    if value == "":
        return ""
    return _sha256(value, field_name)


def _coverage_id(value: object, field_name: str) -> str:
    if type(value) is not str or not _COVERAGE_ID_RE.fullmatch(value):
        raise CoverageError(f"{field_name} must be a canonical coverage identity")
    return value


def _optional_coverage_id(value: object, field_name: str) -> str:
    if type(value) is not str:
        raise CoverageError("invalid_manifest")
    if value == "":
        return ""
    return _coverage_id(value, field_name)


def _safe_ledger_code(error: BaseException) -> str:
    try:
        if type(error) is CoverageLedgerError:
            code = object.__getattribute__(error, "code")
            if type(code) is str and code in CoverageLedgerError._codes:
                return code
        if type(error) is CoverageError:
            code = object.__getattribute__(error, "code")
            if type(code) is str and code == "budget_exceeded":
                return code
    except BaseException:
        pass
    return "invalid_ledger"


def _safe_coverage_code(error: BaseException, default: str) -> str:
    """Reserialize only exact public classified errors, without touching hostile state."""
    try:
        if type(error) is CoverageError:
            code = object.__getattribute__(error, "code")
            args = BaseException.args.__get__(error)
            if type(code) is str and code == "budget_exceeded" and type(args) is tuple and args == (code,):
                return code
    except BaseException:
        pass
    return default


__all__ = [
    "COVERAGE_LEDGER_SCHEMA",
    "COVERAGE_SCHEMA",
    "CoverageAttemptBinding",
    "CoverageError",
    "CoverageFailureCode",
    "CoverageFileRecord",
    "CoverageLedgerEntry",
    "CoverageLedgerError",
    "CoverageManifest",
    "CoverageManifestLedger",
    "CoverageStatus",
    "CoverageTotals",
    "DirectLineDenominator",
    "FileCoverageObservation",
    "FileObservationState",
    "MAX_COVERAGE_CANONICAL_BYTES",
    "MAX_COVERAGE_LEDGER_TOTAL_BYTES",
    "MAX_COVERAGE_LEDGER_ENTRIES",
    "MAX_COVERAGE_LINE_COUNT",
    "MAX_COVERAGE_OCCURRENCES_PER_FILE",
    "TextClassification",
    "WholeCodebaseClaim",
    "build_coverage_manifest",
]
