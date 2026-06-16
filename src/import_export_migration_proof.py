"""Small backend contract for import/export migration proof validation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any


_MAX_ID = 80
_MAX_TEXT = 160
_MAX_LONG_TEXT = 240
_NON_SLUG_CHARS_RE = re.compile(r"[^a-z0-9]+")
_FORBIDDEN_RUNTIME_CLAIMS = ("dual-write", "dual write", "cutover", "runtime switch", "live switch")


class MigrationProofError(ValueError):
    """Raised when a migration proof payload is invalid or unsafe."""


class MigrationProofStatus(StrEnum):
    DRAFT = "draft"
    READY_FOR_REVIEW = "ready_for_review"
    GO = "go"
    NO_GO = "no_go"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


def _normalize_slug(value: Any, *, field_name: str) -> str:
    raw = str(value or "")
    if not raw.strip():
        raise MigrationProofError(f"{field_name} must not be empty")
    normalized = _NON_SLUG_CHARS_RE.sub("-", raw.strip().lower()).strip("-")
    normalized = re.sub(r"-{2,}", "-", normalized)
    if not normalized:
        raise MigrationProofError(f"{field_name} must contain slug characters")
    if len(normalized) > _MAX_ID:
        raise MigrationProofError(f"{field_name} exceeds max length {_MAX_ID}")
    return normalized


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool, limit: int = _MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if not allow_empty and not text:
        raise MigrationProofError(f"{field_name} must not be empty")
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text


def _normalize_count(value: Any, *, field_name: str) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        raise MigrationProofError(f"{field_name} must be an int") from None
    if normalized < 0:
        raise MigrationProofError(f"{field_name} must be >= 0")
    return normalized


def _normalize_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no", ""}:
            return False
    raise MigrationProofError(f"{field_name} must be a bool")


def _normalize_status(value: Any) -> MigrationProofStatus:
    if isinstance(value, MigrationProofStatus):
        return value
    normalized = _normalize_slug(value, field_name="go_no_go_status").replace("-", "_")
    try:
        return MigrationProofStatus(normalized)
    except ValueError as exc:
        raise MigrationProofError("go_no_go_status is not supported") from exc


def _reject_runtime_claims(*values: str) -> None:
    haystack = " ".join(values).lower()
    if any(term in haystack for term in _FORBIDDEN_RUNTIME_CLAIMS):
        raise MigrationProofError("runtime cutover or dual-write claims are not allowed in migration proof")


@dataclass(frozen=True, slots=True)
class MigrationManifest:
    manifest_ref: str
    run_id: str
    store_ref: str
    schema_version: str
    source_count: int
    chunk_count: int
    embedding_count: int
    entity_count: int
    relation_count: int
    provenance_count: int
    evidence_ref: str

    @classmethod
    def create(
        cls,
        *,
        manifest_ref: Any,
        run_id: Any,
        store_ref: Any,
        schema_version: Any,
        source_count: Any,
        chunk_count: Any,
        embedding_count: Any,
        entity_count: Any,
        relation_count: Any,
        provenance_count: Any,
        evidence_ref: Any,
    ) -> "MigrationManifest":
        normalized_evidence = _normalize_text(evidence_ref, field_name="evidence_ref", allow_empty=False, limit=_MAX_LONG_TEXT)
        _reject_runtime_claims(normalized_evidence)
        return cls(
            manifest_ref=_normalize_slug(manifest_ref, field_name="manifest_ref"),
            run_id=_normalize_slug(run_id, field_name="run_id"),
            store_ref=_normalize_slug(store_ref, field_name="store_ref"),
            schema_version=_normalize_slug(schema_version, field_name="schema_version"),
            source_count=_normalize_count(source_count, field_name="source_count"),
            chunk_count=_normalize_count(chunk_count, field_name="chunk_count"),
            embedding_count=_normalize_count(embedding_count, field_name="embedding_count"),
            entity_count=_normalize_count(entity_count, field_name="entity_count"),
            relation_count=_normalize_count(relation_count, field_name="relation_count"),
            provenance_count=_normalize_count(provenance_count, field_name="provenance_count"),
            evidence_ref=normalized_evidence,
        )


@dataclass(frozen=True, slots=True)
class CountComparison:
    counts_match: bool
    source_manifest_ref: str
    target_manifest_ref: str
    reason: str
    next_action: str

    @classmethod
    def create(
        cls,
        *,
        counts_match: Any,
        source_manifest_ref: Any,
        target_manifest_ref: Any,
        reason: Any = "",
        next_action: Any = "",
    ) -> "CountComparison":
        normalized_reason = _normalize_text(reason, field_name="reason", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_next_action = _normalize_text(next_action, field_name="next_action", allow_empty=True, limit=_MAX_LONG_TEXT)
        _reject_runtime_claims(normalized_reason, normalized_next_action)
        normalized_match = _normalize_bool(counts_match, field_name="counts_match")
        if not normalized_match and not (normalized_reason or normalized_next_action):
            raise MigrationProofError("count mismatches require reason or next_action")
        return cls(
            counts_match=normalized_match,
            source_manifest_ref=_normalize_slug(source_manifest_ref, field_name="source_manifest_ref"),
            target_manifest_ref=_normalize_slug(target_manifest_ref, field_name="target_manifest_ref"),
            reason=normalized_reason,
            next_action=normalized_next_action,
        )


@dataclass(frozen=True, slots=True)
class SampleComparison:
    samples_match: bool
    sample_size: int
    reason: str
    next_action: str

    @classmethod
    def create(
        cls,
        *,
        samples_match: Any,
        sample_size: Any,
        reason: Any = "",
        next_action: Any = "",
    ) -> "SampleComparison":
        normalized_reason = _normalize_text(reason, field_name="reason", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_next_action = _normalize_text(next_action, field_name="next_action", allow_empty=True, limit=_MAX_LONG_TEXT)
        _reject_runtime_claims(normalized_reason, normalized_next_action)
        normalized_match = _normalize_bool(samples_match, field_name="samples_match")
        normalized_size = _normalize_count(sample_size, field_name="sample_size")
        if not normalized_match and not (normalized_reason or normalized_next_action):
            raise MigrationProofError("sample mismatches require reason or next_action")
        return cls(
            samples_match=normalized_match,
            sample_size=normalized_size,
            reason=normalized_reason,
            next_action=normalized_next_action,
        )


@dataclass(frozen=True, slots=True)
class MigrationProof:
    export_run_id: str
    import_run_id: str
    source_manifest_ref: str
    target_manifest_ref: str
    backup_ref: str
    restore_ref: str
    count_comparison: CountComparison
    sample_comparison: SampleComparison
    read_only_compare: bool
    rollback_plan: str
    go_no_go_status: MigrationProofStatus
    proof_evidence_ref: str
    reason: str
    next_action: str

    @classmethod
    def create(
        cls,
        *,
        export_run_id: Any,
        import_run_id: Any,
        source_manifest_ref: Any,
        target_manifest_ref: Any,
        backup_ref: Any,
        restore_ref: Any,
        count_comparison: CountComparison,
        sample_comparison: SampleComparison,
        read_only_compare: Any,
        rollback_plan: Any,
        go_no_go_status: MigrationProofStatus | str,
        proof_evidence_ref: Any,
        reason: Any = "",
        next_action: Any = "",
    ) -> "MigrationProof":
        if not isinstance(count_comparison, CountComparison):
            raise MigrationProofError("count_comparison must be a CountComparison")
        if not isinstance(sample_comparison, SampleComparison):
            raise MigrationProofError("sample_comparison must be a SampleComparison")
        normalized_backup = _normalize_text(backup_ref, field_name="backup_ref", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_restore = _normalize_text(restore_ref, field_name="restore_ref", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_rollback = _normalize_text(rollback_plan, field_name="rollback_plan", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_evidence = _normalize_text(
            proof_evidence_ref,
            field_name="proof_evidence_ref",
            allow_empty=False,
            limit=_MAX_LONG_TEXT,
        )
        normalized_reason = _normalize_text(reason, field_name="reason", allow_empty=True, limit=_MAX_LONG_TEXT)
        normalized_next_action = _normalize_text(next_action, field_name="next_action", allow_empty=True, limit=_MAX_LONG_TEXT)
        _reject_runtime_claims(
            normalized_backup,
            normalized_restore,
            normalized_rollback,
            normalized_evidence,
            normalized_reason,
            normalized_next_action,
            count_comparison.reason,
            count_comparison.next_action,
            sample_comparison.reason,
            sample_comparison.next_action,
        )
        normalized_status = _normalize_status(go_no_go_status)
        normalized_compare = _normalize_bool(read_only_compare, field_name="read_only_compare")
        if normalized_status == MigrationProofStatus.GO and not (
            normalized_backup
            and normalized_restore
            and normalized_rollback
            and normalized_compare
            and count_comparison.counts_match
            and sample_comparison.samples_match
        ):
            raise MigrationProofError(
                "go requires backup_ref, restore_ref, rollback_plan, read_only_compare=True, and matching counts and samples"
            )
        if normalized_status in {MigrationProofStatus.NO_GO, MigrationProofStatus.FAILED} and not (
            normalized_reason or normalized_next_action
        ):
            raise MigrationProofError("no_go and failed proofs require reason or next_action")
        return cls(
            export_run_id=_normalize_slug(export_run_id, field_name="export_run_id"),
            import_run_id=_normalize_slug(import_run_id, field_name="import_run_id"),
            source_manifest_ref=_normalize_slug(source_manifest_ref, field_name="source_manifest_ref"),
            target_manifest_ref=_normalize_slug(target_manifest_ref, field_name="target_manifest_ref"),
            backup_ref=normalized_backup,
            restore_ref=normalized_restore,
            count_comparison=count_comparison,
            sample_comparison=sample_comparison,
            read_only_compare=normalized_compare,
            rollback_plan=normalized_rollback,
            go_no_go_status=normalized_status,
            proof_evidence_ref=normalized_evidence,
            reason=normalized_reason,
            next_action=normalized_next_action,
        )

    def audit_summary(self) -> dict[str, Any]:
        return {
            "export_run_id": self.export_run_id,
            "import_run_id": self.import_run_id,
            "source_manifest_ref": self.source_manifest_ref,
            "target_manifest_ref": self.target_manifest_ref,
            "go_no_go_status": self.go_no_go_status.value,
            "counts_match": self.count_comparison.counts_match,
            "samples_match": self.sample_comparison.samples_match,
            "sample_size": self.sample_comparison.sample_size,
            "read_only_compare": self.read_only_compare,
            "has_backup_ref": bool(self.backup_ref),
            "has_restore_ref": bool(self.restore_ref),
            "has_rollback_plan": bool(self.rollback_plan),
        }
