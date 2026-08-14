"""Strict, local-domain contracts for durable transcription processing.

This module deliberately contains no I/O, logging, provider access, or mutable
global state.  The store and pipeline layers consume these contracts; they must
not infer authority from upload names, request paths, or model output.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from hashlib import sha256
from typing import Any, ClassVar, Mapping, Sequence


class ContractError(ValueError):
    """A deliberately content-free validation failure."""


_OPAQUE_ID = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")
_OWNER_ID = re.compile(r"^owner_[a-z0-9]{16,48}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_WORD = re.compile(r"[A-Za-zÄÖÜäöüß]+")
_NUMBER = re.compile(r"\d+(?:[.,:/-]\d+)*")
_LEXICAL_TOKEN = re.compile(r"\d+(?:[.,:/-]\d+)*|[A-Za-zÄÖÜäöüß]+")
_LANGUAGE = re.compile(r"^[a-z]{2,3}(?:-[a-z]{2,8})?$")
_SAFE_SHARD = re.compile(r"^[a-z0-9]{2}$")
_LEXICAL_TOKEN = re.compile(r"\d+(?:[.,:/-]\d+)*|[A-Za-z\u00c4\u00d6\u00dc\u00e4\u00f6\u00fc\u00df]+")

MAX_TEXT_CHARS = 12_000
MAX_SEGMENT_TEXT_CHARS = 4_000
MAX_METADATA_ITEMS = 16
MAX_METADATA_VALUE_CHARS = 128
MAX_COLLECTION_ITEMS = 256
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024

_ALLOWED_MEDIA_TYPES = frozenset({"audio/wav", "audio/mpeg", "audio/ogg", "audio/webm", "audio/mp4"})
_ALLOWED_JOB_STATES = frozenset(
    {
        "receiving",
        "stored",
        "backup_pending",
        "backup_protected",
        "transcribing",
        "transcribed",
        "correcting",
        "corrected",
        "protocol_ready",
        "needs_review",
        "failed",
        "deletion_pending",
        "deleted",
    }
)
REVIEW_OUTPUT_STATES = frozenset({"corrected", "protocol_ready", "needs_review"})
_TRANSITIONS = {
    "receiving": frozenset({"stored", "failed", "deletion_pending"}),
    "stored": frozenset({"backup_pending", "failed", "deletion_pending"}),
    "backup_pending": frozenset({"backup_protected", "failed", "deletion_pending"}),
    "backup_protected": frozenset({"transcribing", "failed", "deletion_pending"}),
    "transcribing": frozenset({"transcribed", "failed"}),
    "transcribed": frozenset({"correcting", "protocol_ready", "needs_review", "failed", "deletion_pending"}),
    "correcting": REVIEW_OUTPUT_STATES | frozenset({"failed", "deletion_pending"}),
    "corrected": frozenset({"protocol_ready", "needs_review", "failed", "deletion_pending"}),
    "protocol_ready": frozenset({"needs_review", "deletion_pending"}),
    "needs_review": frozenset({"protocol_ready", "deletion_pending"}),
    "failed": frozenset({"deletion_pending"}),
    "deletion_pending": frozenset({"deleted"}),
    "deleted": frozenset(),
}
_CORRECTION_KINDS = frozenset({"casing", "orthography", "punctuation", "whitespace"})
_CORRECTION_REASON_CODES = frozenset({"asr_casing", "asr_orthography", "asr_punctuation", "asr_whitespace"})
_CLAIM_KINDS = frozenset({"summary", "statement", "decision", "action_item", "risk", "open_question"})
_ALLOWED_METADATA_KEYS = frozenset(
    {"channel_count", "language", "media_codec", "model_id", "policy_version", "schema_version"}
)
_STATES_REQUIRING_RECEIPT = frozenset(
    {"backup_protected", "transcribing", "transcribed", "correcting", "corrected", "protocol_ready", "needs_review"}
)


def _fail() -> None:
    raise ContractError("invalid transcription contract")


def _opaque(value: str, *, owner: bool = False) -> str:
    if not isinstance(value, str) or not (1 <= len(value) <= 64):
        _fail()
    if not ( _OWNER_ID.fullmatch(value) if owner else _OPAQUE_ID.fullmatch(value)):
        _fail()
    return value


def _digest(value: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _fail()
    return value


def _timestamp(value: str) -> str:
    if not isinstance(value, str) or not _UTC.fullmatch(value):
        _fail()
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        _fail()
    return value


def _text(value: str, *, limit: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or "\x00" in value:
        _fail()
    return value


def _finite(value: float | None, *, minimum: float, maximum: float) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        _fail()
    number = float(value)
    if not minimum <= number <= maximum:
        _fail()
    return number


def _metadata(value: Mapping[str, str] | Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    if isinstance(value, Mapping):
        items = tuple(value.items())
    elif isinstance(value, (tuple, list)):
        items = tuple(value)
    else:
        _fail()
    if len(items) > MAX_METADATA_ITEMS:
        _fail()
    normalised: list[tuple[str, str]] = []
    for item in items:
        if not isinstance(item, tuple) or len(item) != 2:
            _fail()
        key, item_value = item
        if key not in _ALLOWED_METADATA_KEYS or not isinstance(item_value, str):
            _fail()
        if not item_value or len(item_value) > MAX_METADATA_VALUE_CHARS or "\x00" in item_value:
            _fail()
        normalised.append((key, item_value))
    if len({key for key, _ in normalised}) != len(normalised):
        _fail()
    return tuple(sorted(normalised))


def _unique_ids(values: Sequence[str], *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list)):
        _fail()
    result = tuple(values)
    if (not allow_empty and not result) or len(result) > MAX_COLLECTION_ITEMS:
        _fail()
    if len(set(result)) != len(result):
        _fail()
    return tuple(_opaque(value) for value in result)


def _strict_dict(value: Any, fields: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail()
    return value


def _decode_list(value: Any) -> list[Any]:
    if not isinstance(value, list) or len(value) > MAX_COLLECTION_ITEMS:
        _fail()
    return value


def _instances(values: Sequence[Any], expected: type[Any]) -> tuple[Any, ...]:
    if not isinstance(values, (tuple, list)):
        _fail()
    result = tuple(values)
    if len(result) > MAX_COLLECTION_ITEMS or not all(isinstance(item, expected) for item in result):
        _fail()
    return result


@dataclass(frozen=True, slots=True)
class RecordingAuthorizationRef:
    authorization_id: str
    owner_id: str
    policy_ref: str
    recording_allowed: bool
    expires_at: str | None

    _FIELDS: ClassVar[frozenset[str]] = frozenset({"authorization_id", "owner_id", "policy_ref", "recording_allowed", "expires_at"})

    def __post_init__(self) -> None:
        _opaque(self.authorization_id)
        _opaque(self.owner_id, owner=True)
        _opaque(self.policy_ref)
        if not isinstance(self.recording_allowed, bool) or not self.recording_allowed:
            _fail()
        if self.expires_at is not None:
            _timestamp(self.expires_at)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "RecordingAuthorizationRef":
        item = _strict_dict(value, cls._FIELDS)
        return cls(**item)


@dataclass(frozen=True, slots=True)
class RetentionPolicyRef:
    policy_id: str
    owner_id: str
    retention_days: int
    policy_version: str

    _FIELDS: ClassVar[frozenset[str]] = frozenset({"policy_id", "owner_id", "retention_days", "policy_version"})

    def __post_init__(self) -> None:
        _opaque(self.policy_id)
        _opaque(self.owner_id, owner=True)
        if not isinstance(self.retention_days, int) or isinstance(self.retention_days, bool) or not 1 <= self.retention_days <= 3660:
            _fail()
        _opaque(self.policy_version)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "RetentionPolicyRef":
        item = _strict_dict(value, cls._FIELDS)
        return cls(**item)


@dataclass(frozen=True, slots=True)
class AudioArtifact:
    artifact_id: str
    owner_id: str
    source_sha256: str
    byte_count: int
    media_type: str
    storage_locator: str
    created_at: str
    metadata: tuple[tuple[str, str], ...] = ()

    _FIELDS: ClassVar[frozenset[str]] = frozenset({"artifact_id", "owner_id", "source_sha256", "byte_count", "media_type", "storage_locator", "created_at", "metadata"})

    def __post_init__(self) -> None:
        _opaque(self.artifact_id)
        _opaque(self.owner_id, owner=True)
        _digest(self.source_sha256)
        if not isinstance(self.byte_count, int) or isinstance(self.byte_count, bool) or not 1 <= self.byte_count <= MAX_ARTIFACT_BYTES:
            _fail()
        if self.media_type not in _ALLOWED_MEDIA_TYPES:
            _fail()
        shard = self.artifact_id[:2]
        if not _SAFE_SHARD.fullmatch(shard) or self.storage_locator != f"blobs/{shard}/{self.artifact_id}.audio":
            _fail()
        _timestamp(self.created_at)
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["metadata"] = dict(self.metadata)
        return result

    @classmethod
    def from_dict(cls, value: Any) -> "AudioArtifact":
        item = dict(_strict_dict(value, cls._FIELDS))
        item["metadata"] = _metadata(item["metadata"])
        return cls(**item)


@dataclass(frozen=True, slots=True)
class RawTranscriptSegment:
    segment_id: str
    artifact_id: str
    owner_id: str
    source_sha256: str
    start_ms: int
    end_ms: int
    text: str
    ordinal: int
    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    compression_ratio: float | None = None
    language: str | None = None
    language_probability: float | None = None

    _FIELDS: ClassVar[frozenset[str]] = frozenset({"segment_id", "artifact_id", "owner_id", "source_sha256", "start_ms", "end_ms", "text", "ordinal", "avg_logprob", "no_speech_prob", "compression_ratio", "language", "language_probability"})

    def __post_init__(self) -> None:
        _opaque(self.segment_id)
        _opaque(self.artifact_id)
        _opaque(self.owner_id, owner=True)
        _digest(self.source_sha256)
        if not all(isinstance(value, int) and not isinstance(value, bool) for value in (self.start_ms, self.end_ms, self.ordinal)):
            _fail()
        if not 0 <= self.start_ms < self.end_ms <= 86_400_000 or not 0 <= self.ordinal < MAX_COLLECTION_ITEMS:
            _fail()
        _text(self.text, limit=MAX_SEGMENT_TEXT_CHARS)
        object.__setattr__(self, "avg_logprob", _finite(self.avg_logprob, minimum=-100.0, maximum=10.0))
        object.__setattr__(self, "no_speech_prob", _finite(self.no_speech_prob, minimum=0.0, maximum=1.0))
        object.__setattr__(self, "compression_ratio", _finite(self.compression_ratio, minimum=0.01, maximum=100.0))
        object.__setattr__(self, "language_probability", _finite(self.language_probability, minimum=0.0, maximum=1.0))
        if self.language is not None and (not isinstance(self.language, str) or not _LANGUAGE.fullmatch(self.language)):
            _fail()

    @property
    def text_sha256(self) -> str:
        return sha256(self.text.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "RawTranscriptSegment":
        return cls(**_strict_dict(value, cls._FIELDS))


@dataclass(frozen=True, slots=True)
class CorrectionProposal:
    correction_id: str
    segment_id: str
    artifact_id: str
    owner_id: str
    source_sha256: str
    parent_text_sha256: str
    corrected_text: str
    corrected_text_sha256: str
    correction_kinds: tuple[str, ...]
    reason_code: str
    confidence_milli: int
    requires_review: bool

    _FIELDS: ClassVar[frozenset[str]] = frozenset({"correction_id", "segment_id", "artifact_id", "owner_id", "source_sha256", "parent_text_sha256", "corrected_text", "corrected_text_sha256", "correction_kinds", "reason_code", "confidence_milli", "requires_review"})

    def __post_init__(self) -> None:
        _opaque(self.correction_id)
        _opaque(self.segment_id)
        _opaque(self.artifact_id)
        _opaque(self.owner_id, owner=True)
        _digest(self.source_sha256)
        _digest(self.parent_text_sha256)
        _text(self.corrected_text, limit=MAX_SEGMENT_TEXT_CHARS)
        if self.corrected_text_sha256 != sha256(self.corrected_text.encode("utf-8")).hexdigest():
            _fail()
        kinds = tuple(self.correction_kinds)
        if not kinds or len(kinds) > len(_CORRECTION_KINDS) or len(set(kinds)) != len(kinds) or not set(kinds) <= _CORRECTION_KINDS:
            _fail()
        if self.reason_code not in _CORRECTION_REASON_CODES:
            _fail()
        if not isinstance(self.confidence_milli, int) or isinstance(self.confidence_milli, bool) or not 0 <= self.confidence_milli <= 1000:
            _fail()
        if not isinstance(self.requires_review, bool):
            _fail()
        if "orthography" in kinds and not self.requires_review:
            _fail()
        reason_kind = self.reason_code.removeprefix("asr_")
        if reason_kind not in kinds or ("orthography" in kinds and self.reason_code != "asr_orthography"):
            _fail()
        object.__setattr__(self, "correction_kinds", tuple(sorted(kinds)))

    def validates_against(self, segment: RawTranscriptSegment) -> None:
        if not isinstance(segment, RawTranscriptSegment):
            _fail()
        if self.owner_id != segment.owner_id or self.segment_id != segment.segment_id or self.artifact_id != segment.artifact_id or self.source_sha256 != segment.source_sha256 or self.parent_text_sha256 != segment.text_sha256:
            _fail()
        raw_tokens = tuple(token.casefold() for token in _LEXICAL_TOKEN.findall(segment.text))
        corrected_tokens = tuple(token.casefold() for token in _LEXICAL_TOKEN.findall(self.corrected_text))
        if raw_tokens != corrected_tokens and ("orthography" not in self.correction_kinds or not self.requires_review):
            _fail()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["correction_kinds"] = list(self.correction_kinds)
        return result

    @classmethod
    def from_dict(cls, value: Any) -> "CorrectionProposal":
        item = dict(_strict_dict(value, cls._FIELDS))
        kinds = _decode_list(item["correction_kinds"])
        if not all(isinstance(kind, str) for kind in kinds):
            _fail()
        item["correction_kinds"] = tuple(kinds)
        return cls(**item)


@dataclass(frozen=True, slots=True)
class ProtocolEvidence:
    evidence_id: str
    artifact_id: str
    owner_id: str
    segment_ids: tuple[str, ...]
    source_sha256: str

    _FIELDS: ClassVar[frozenset[str]] = frozenset({"evidence_id", "artifact_id", "owner_id", "segment_ids", "source_sha256"})

    def __post_init__(self) -> None:
        _opaque(self.evidence_id)
        _opaque(self.artifact_id)
        _opaque(self.owner_id, owner=True)
        object.__setattr__(self, "segment_ids", _unique_ids(self.segment_ids))
        _digest(self.source_sha256)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["segment_ids"] = list(self.segment_ids)
        return result

    @classmethod
    def from_dict(cls, value: Any) -> "ProtocolEvidence":
        item = dict(_strict_dict(value, cls._FIELDS))
        item["segment_ids"] = tuple(_decode_list(item["segment_ids"]))
        return cls(**item)


@dataclass(frozen=True, slots=True)
class ProtocolClaim:
    claim_id: str
    owner_id: str
    claim_kind: str
    text: str
    evidence_ids: tuple[str, ...]
    assignee_ref: str | None = None
    assignee_evidence_segment_ids: tuple[str, ...] = ()
    critical_uncertainty: bool = False

    _FIELDS: ClassVar[frozenset[str]] = frozenset({"claim_id", "owner_id", "claim_kind", "text", "evidence_ids", "assignee_ref", "assignee_evidence_segment_ids", "critical_uncertainty"})

    def __post_init__(self) -> None:
        _opaque(self.claim_id)
        _opaque(self.owner_id, owner=True)
        if self.claim_kind not in _CLAIM_KINDS:
            _fail()
        _text(self.text)
        object.__setattr__(self, "evidence_ids", _unique_ids(self.evidence_ids))
        if self.assignee_ref is not None:
            _opaque(self.assignee_ref)
        assignee_segments = _unique_ids(self.assignee_evidence_segment_ids, allow_empty=True)
        object.__setattr__(self, "assignee_evidence_segment_ids", assignee_segments)
        if not isinstance(self.critical_uncertainty, bool):
            _fail()
        if self.claim_kind != "action_item" and (self.assignee_ref is not None or assignee_segments):
            _fail()
        if self.assignee_ref is not None and not assignee_segments:
            _fail()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["evidence_ids"] = list(self.evidence_ids)
        result["assignee_evidence_segment_ids"] = list(self.assignee_evidence_segment_ids)
        return result

    @classmethod
    def from_dict(cls, value: Any) -> "ProtocolClaim":
        item = dict(_strict_dict(value, cls._FIELDS))
        item["evidence_ids"] = tuple(_decode_list(item["evidence_ids"]))
        item["assignee_evidence_segment_ids"] = tuple(_decode_list(item["assignee_evidence_segment_ids"]))
        return cls(**item)


@dataclass(frozen=True, slots=True)
class BackupReceipt:
    receipt_id: str
    artifact_id: str
    owner_id: str
    source_sha256: str
    snapshot_ref: str
    verified_at: str
    verified: bool

    _FIELDS: ClassVar[frozenset[str]] = frozenset({"receipt_id", "artifact_id", "owner_id", "source_sha256", "snapshot_ref", "verified_at", "verified"})

    def __post_init__(self) -> None:
        _opaque(self.receipt_id)
        _opaque(self.artifact_id)
        _opaque(self.owner_id, owner=True)
        _digest(self.source_sha256)
        _opaque(self.snapshot_ref)
        _timestamp(self.verified_at)
        if self.verified is not True:
            _fail()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "BackupReceipt":
        return cls(**_strict_dict(value, cls._FIELDS))


@dataclass(frozen=True, slots=True)
class TranscriptionJob:
    job_id: str
    artifact_id: str
    owner_id: str
    authorization_id: str
    retention_policy_id: str
    state: str
    backup_receipt_id: str | None = None

    _FIELDS: ClassVar[frozenset[str]] = frozenset({"job_id", "artifact_id", "owner_id", "authorization_id", "retention_policy_id", "state", "backup_receipt_id"})

    def __post_init__(self) -> None:
        _opaque(self.job_id)
        _opaque(self.artifact_id)
        _opaque(self.owner_id, owner=True)
        _opaque(self.authorization_id)
        _opaque(self.retention_policy_id)
        if self.state not in _ALLOWED_JOB_STATES:
            _fail()
        if self.backup_receipt_id is not None:
            _opaque(self.backup_receipt_id)
        if self.state in _STATES_REQUIRING_RECEIPT and self.backup_receipt_id is None:
            _fail()
        if self.state in {"receiving", "stored", "backup_pending"} and self.backup_receipt_id is not None:
            _fail()

    def transition_to(self, state: str, *, receipt: BackupReceipt | None = None) -> "TranscriptionJob":
        if state not in _TRANSITIONS.get(self.state, frozenset()):
            _fail()
        if state == "backup_protected":
            if receipt is None or receipt.owner_id != self.owner_id or receipt.artifact_id != self.artifact_id:
                _fail()
            return replace(self, state=state, backup_receipt_id=receipt.receipt_id)
        if receipt is not None:
            _fail()
        return replace(self, state=state)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "TranscriptionJob":
        return cls(**_strict_dict(value, cls._FIELDS))


@dataclass(frozen=True, slots=True)
class ProtocolDocument:
    protocol_id: str
    artifact_id: str
    owner_id: str
    evidence: tuple[ProtocolEvidence, ...]
    claims: tuple[ProtocolClaim, ...]

    _FIELDS: ClassVar[frozenset[str]] = frozenset({"protocol_id", "artifact_id", "owner_id", "evidence", "claims"})

    def __post_init__(self) -> None:
        _opaque(self.protocol_id)
        _opaque(self.artifact_id)
        _opaque(self.owner_id, owner=True)
        evidence = _instances(self.evidence, ProtocolEvidence)
        claims = _instances(self.claims, ProtocolClaim)
        evidence_ids = {item.evidence_id for item in evidence}
        if len(evidence_ids) != len(evidence):
            _fail()
        if len({item.claim_id for item in claims}) != len(claims):
            _fail()
        for item in evidence:
            if item.owner_id != self.owner_id or item.artifact_id != self.artifact_id:
                _fail()
        for item in claims:
            if item.owner_id != self.owner_id or not set(item.evidence_ids) <= evidence_ids:
                _fail()
            cited_segments = set()
            for evidence_id in item.evidence_ids:
                matching = tuple(entry for entry in evidence if entry.evidence_id == evidence_id)
                if len(matching) != 1:
                    _fail()
                cited_segments.update(matching[0].segment_ids)
            if not set(item.assignee_evidence_segment_ids) <= cited_segments:
                _fail()
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "claims", claims)

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_id": self.protocol_id,
            "artifact_id": self.artifact_id,
            "owner_id": self.owner_id,
            "evidence": [item.to_dict() for item in self.evidence],
            "claims": [item.to_dict() for item in self.claims],
        }

    @classmethod
    def from_dict(cls, value: Any) -> "ProtocolDocument":
        item = dict(_strict_dict(value, cls._FIELDS))
        item["evidence"] = tuple(ProtocolEvidence.from_dict(entry) for entry in _decode_list(item["evidence"]))
        item["claims"] = tuple(ProtocolClaim.from_dict(entry) for entry in _decode_list(item["claims"]))
        return cls(**item)


@dataclass(frozen=True, slots=True)
class TranscriptionRecord:
    """Canonical aggregate; validates cross-record ownership and parent links."""

    artifact: AudioArtifact
    authorization: RecordingAuthorizationRef
    retention: RetentionPolicyRef
    job: TranscriptionJob
    segments: tuple[RawTranscriptSegment, ...]
    corrections: tuple[CorrectionProposal, ...] = ()
    receipt: BackupReceipt | None = None
    protocol: ProtocolDocument | None = None

    _FIELDS: ClassVar[frozenset[str]] = frozenset({"artifact", "authorization", "retention", "job", "segments", "corrections", "receipt", "protocol"})

    def __post_init__(self) -> None:
        if not isinstance(self.artifact, AudioArtifact) or not isinstance(self.authorization, RecordingAuthorizationRef) or not isinstance(self.retention, RetentionPolicyRef) or not isinstance(self.job, TranscriptionJob):
            _fail()
        owner = self.artifact.owner_id
        if self.authorization.owner_id != owner or self.retention.owner_id != owner or self.job.owner_id != owner:
            _fail()
        if self.job.artifact_id != self.artifact.artifact_id or self.job.authorization_id != self.authorization.authorization_id or self.job.retention_policy_id != self.retention.policy_id:
            _fail()
        segments = _instances(self.segments, RawTranscriptSegment)
        corrections = _instances(self.corrections, CorrectionProposal)
        segment_map = {item.segment_id: item for item in segments}
        if len(segment_map) != len(segments):
            _fail()
        for item in segments:
            if item.owner_id != owner or item.artifact_id != self.artifact.artifact_id or item.source_sha256 != self.artifact.source_sha256:
                _fail()
        output_states = frozenset({"transcribed", "correcting", "corrected", "protocol_ready", "needs_review"})
        preserving_states = frozenset({"failed", "deletion_pending"})
        correction_states = frozenset({"corrected", "protocol_ready", "needs_review"}) | preserving_states
        if self.job.state in output_states and not segments:
            _fail()
        if self.job.state not in output_states | preserving_states and segments:
            _fail()
        if corrections and self.job.state not in correction_states:
            _fail()
        for item in corrections:
            parent = segment_map.get(item.segment_id)
            if parent is None:
                _fail()
            item.validates_against(parent)
        if self.receipt is not None:
            if not isinstance(self.receipt, BackupReceipt):
                _fail()
            if self.receipt.owner_id != owner or self.receipt.artifact_id != self.artifact.artifact_id or self.receipt.source_sha256 != self.artifact.source_sha256:
                _fail()
            if self.job.backup_receipt_id is not None and self.job.backup_receipt_id != self.receipt.receipt_id:
                _fail()
        if self.job.state in _STATES_REQUIRING_RECEIPT and self.receipt is None:
            _fail()
        if self.protocol is not None:
            if not isinstance(self.protocol, ProtocolDocument):
                _fail()
            if self.protocol.owner_id != owner or self.protocol.artifact_id != self.artifact.artifact_id:
                _fail()
            available_segments = set(segment_map)
            for evidence in self.protocol.evidence:
                if not set(evidence.segment_ids) <= available_segments or evidence.source_sha256 != self.artifact.source_sha256:
                    _fail()
        if self.protocol is not None and self.job.state not in {"protocol_ready", "needs_review"} | preserving_states:
            _fail()
        if self.job.state == "deleted" and (segments or corrections or self.protocol is not None):
            _fail()
        if self.job.state == "protocol_ready" and self.protocol is None:
            _fail()
        if self.job.state == "protocol_ready" and self.protocol is not None and any(claim.critical_uncertainty for claim in self.protocol.claims):
            _fail()
        if any(item.requires_review for item in corrections) and self.job.state not in {"needs_review"} | preserving_states:
            _fail()
        object.__setattr__(self, "segments", segments)
        object.__setattr__(self, "corrections", corrections)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact": self.artifact.to_dict(),
            "authorization": self.authorization.to_dict(),
            "retention": self.retention.to_dict(),
            "job": self.job.to_dict(),
            "segments": [item.to_dict() for item in self.segments],
            "corrections": [item.to_dict() for item in self.corrections],
            "receipt": None if self.receipt is None else self.receipt.to_dict(),
            "protocol": None if self.protocol is None else self.protocol.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, value: Any) -> "TranscriptionRecord":
        item = dict(_strict_dict(value, cls._FIELDS))
        item["artifact"] = AudioArtifact.from_dict(item["artifact"])
        item["authorization"] = RecordingAuthorizationRef.from_dict(item["authorization"])
        item["retention"] = RetentionPolicyRef.from_dict(item["retention"])
        item["job"] = TranscriptionJob.from_dict(item["job"])
        item["segments"] = tuple(RawTranscriptSegment.from_dict(entry) for entry in _decode_list(item["segments"]))
        item["corrections"] = tuple(CorrectionProposal.from_dict(entry) for entry in _decode_list(item["corrections"]))
        if item["receipt"] is not None:
            item["receipt"] = BackupReceipt.from_dict(item["receipt"])
        if item["protocol"] is not None:
            item["protocol"] = ProtocolDocument.from_dict(item["protocol"])
        return cls(**item)

    @classmethod
    def from_json(cls, value: str) -> "TranscriptionRecord":
        if not isinstance(value, str) or len(value) > 2_000_000:
            _fail()
        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, item in pairs:
                if key in result:
                    _fail()
                result[key] = item
            return result
        try:
            decoded = json.loads(value, object_pairs_hook=unique_object)
            return cls.from_dict(decoded)
        except (ContractError, TypeError, ValueError, KeyError, AttributeError, UnicodeError):
            _fail()
