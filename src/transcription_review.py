"""Strict local-model review of immutable transcription evidence.

The model is an untrusted classifier and correction proposer.  It never
supplies owner identifiers, source digests, generated identifiers, evidence
identifiers, or free protocol prose.  Every persisted value is reconstructed
from the canonical transcription record.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
import unicodedata
from typing import Any, ClassVar, Mapping, Protocol, Sequence

from src.transcription_contracts import (
    MAX_COLLECTION_ITEMS,
    MAX_SEGMENT_TEXT_CHARS,
    CorrectionProposal,
    ProtocolClaim,
    ProtocolDocument,
    ProtocolEvidence,
    RawTranscriptSegment,
    TranscriptionRecord,
)


REVIEW_REQUEST_SCHEMA = "odysseus.transcription_review_request.v1"
REVIEW_RESULT_SCHEMA = "odysseus.transcription_review_result.v1"
MAX_REVIEW_OUTPUT_BYTES = 64 * 1024
MAX_REVIEW_REQUEST_CHARS = 4_800
MAX_REVIEW_CHUNKS = 4

_LEXICAL = re.compile(r"\d+(?:[.,:/-]\d+)*|[^\W\d_]+", re.UNICODE)
_CLAIM_KINDS = frozenset(
    {"summary", "statement", "decision", "action_item", "risk", "open_question"}
)
_CORRECTION_KINDS = frozenset(
    {"casing", "punctuation", "whitespace"}
)
_REASON_CODES = frozenset(f"asr_{kind}" for kind in _CORRECTION_KINDS)
_CLAIM_RANK = {
    "summary": 0,
    "decision": 1,
    "action_item": 2,
    "risk": 3,
    "open_question": 4,
    "statement": 5,
}


class TranscriptionReviewError(RuntimeError):
    """Content-free review boundary failure."""


class ReviewTransport(Protocol):
    def review(self, request_json: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    corrections: tuple[CorrectionProposal, ...]
    protocol: ProtocolDocument | None
    target_state: str
    reason_code: str

    def __post_init__(self) -> None:
        if self.target_state not in {"protocol_ready", "needs_review"}:
            raise TranscriptionReviewError("invalid review outcome")
        if self.target_state == "protocol_ready" and self.protocol is None:
            raise TranscriptionReviewError("invalid review outcome")
        if self.target_state == "needs_review" and (
            self.corrections or self.protocol is not None
        ):
            raise TranscriptionReviewError("invalid review outcome")
        if not isinstance(self.reason_code, str) or not self.reason_code:
            raise TranscriptionReviewError("invalid review outcome")

    @classmethod
    def needs_review(cls, reason_code: str = "review_rejected") -> "ReviewOutcome":
        return cls((), None, "needs_review", reason_code)


@dataclass(frozen=True, slots=True)
class ReviewChunk:
    index: int
    count: int
    artifact_id: str
    source_sha256: str
    segments: tuple[RawTranscriptSegment, ...]

    def to_json(self) -> str:
        payload = {
            "schema": REVIEW_REQUEST_SCHEMA,
            "chunk_index": self.index,
            "chunk_count": self.count,
            "artifact_id": self.artifact_id,
            "source_sha256": self.source_sha256,
            "segments": [
                {
                    "segment_id": segment.segment_id,
                    "ordinal": segment.ordinal,
                    "start_ms": segment.start_ms,
                    "end_ms": segment.end_ms,
                    "text": segment.text,
                    "text_sha256": segment.text_sha256,
                    "avg_logprob": segment.avg_logprob,
                    "no_speech_prob": segment.no_speech_prob,
                    "language": segment.language,
                    "language_probability": segment.language_probability,
                }
                for segment in self.segments
            ],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class _CorrectionSpec:
    segment_id: str
    corrected_text: str
    correction_kinds: tuple[str, ...]
    reason_code: str
    confidence_milli: int
    requires_review: bool


@dataclass(frozen=True, slots=True)
class _AssigneeSpec:
    display_text: str
    segment_id: str


@dataclass(frozen=True, slots=True)
class _ClaimSpec:
    claim_kind: str
    segment_ids: tuple[str, ...]
    assignee: _AssigneeSpec | None
    critical_uncertainty: bool


@dataclass(frozen=True, slots=True)
class _ChunkResult:
    corrections: tuple[_CorrectionSpec, ...]
    claims: tuple[_ClaimSpec, ...]


class TranscriptionReviewer:
    """Build bounded chunks, call one injected local transport, and fail closed."""

    def __init__(self, transport: ReviewTransport) -> None:
        if not callable(getattr(transport, "review", None)):
            raise TranscriptionReviewError("invalid review transport")
        self._transport = transport

    def review(self, record: TranscriptionRecord) -> ReviewOutcome:
        try:
            chunks = _build_chunks(record)
            results = tuple(
                _parse_chunk_result(
                    self._transport.review(chunk.to_json()),
                    expected_chunk=chunk,
                )
                for chunk in chunks
            )
            return _build_outcome(record, results)
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            return ReviewOutcome.needs_review("review_rejected")


def _build_chunks(record: TranscriptionRecord) -> tuple[ReviewChunk, ...]:
    if (
        not isinstance(record, TranscriptionRecord)
        or record.job.state not in {"transcribed", "correcting"}
        or not record.segments
        or len(record.segments) > MAX_COLLECTION_ITEMS
    ):
        raise TranscriptionReviewError("invalid review input")
    ordered = tuple(sorted(record.segments, key=lambda item: item.ordinal))
    if tuple(item.ordinal for item in ordered) != tuple(range(len(ordered))):
        raise TranscriptionReviewError("invalid review input")
    batches: list[tuple[RawTranscriptSegment, ...]] = []
    current: list[RawTranscriptSegment] = []
    for segment in ordered:
        candidate = tuple((*current, segment))
        probe = ReviewChunk(
            MAX_REVIEW_CHUNKS - 1,
            MAX_REVIEW_CHUNKS,
            record.artifact.artifact_id,
            record.artifact.source_sha256,
            candidate,
        )
        if len(probe.to_json()) > MAX_REVIEW_REQUEST_CHARS:
            if not current:
                raise TranscriptionReviewError("review input exceeds chunk budget")
            batches.append(tuple(current))
            current = [segment]
            single = ReviewChunk(
                MAX_REVIEW_CHUNKS - 1,
                MAX_REVIEW_CHUNKS,
                record.artifact.artifact_id,
                record.artifact.source_sha256,
                (segment,),
            )
            if len(single.to_json()) > MAX_REVIEW_REQUEST_CHARS:
                raise TranscriptionReviewError("review input exceeds chunk budget")
        else:
            current.append(segment)
    if current:
        batches.append(tuple(current))
    if not batches or len(batches) > MAX_REVIEW_CHUNKS:
        raise TranscriptionReviewError("review input exceeds chunk budget")
    chunks = tuple(
        ReviewChunk(
            index,
            len(batches),
            record.artifact.artifact_id,
            record.artifact.source_sha256,
            batch,
        )
        for index, batch in enumerate(batches)
    )
    if any(len(chunk.to_json()) > MAX_REVIEW_REQUEST_CHARS for chunk in chunks):
        raise TranscriptionReviewError("review input exceeds chunk budget")
    return chunks


def _duplicate_safe_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TranscriptionReviewError("invalid review output")
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    raise TranscriptionReviewError("invalid review output")


def _strict_json_object(value: str) -> Mapping[str, Any]:
    if not isinstance(value, str) or not value:
        raise TranscriptionReviewError("invalid review output")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise TranscriptionReviewError("invalid review output") from exc
    if len(encoded) > MAX_REVIEW_OUTPUT_BYTES:
        raise TranscriptionReviewError("invalid review output")
    try:
        parsed = json.loads(
            value,
            object_pairs_hook=_duplicate_safe_object,
            parse_constant=_reject_nonfinite,
        )
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise TranscriptionReviewError("invalid review output") from exc
    if not isinstance(parsed, dict):
        raise TranscriptionReviewError("invalid review output")
    return parsed


def _exact_object(value: Any, fields: frozenset[str]) -> Mapping[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != fields:
        raise TranscriptionReviewError("invalid review output")
    return value


def _exact_int(value: Any, *, minimum: int, maximum: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        raise TranscriptionReviewError("invalid review output")
    return value


def _bounded_text(value: Any, *, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or "\x00" in value
    ):
        raise TranscriptionReviewError("invalid review output")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise TranscriptionReviewError("invalid review output") from exc
    return value


def _strict_string_list(
    value: Any,
    *,
    allowed: frozenset[str] | None = None,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or len(value) > MAX_COLLECTION_ITEMS
        or not all(isinstance(item, str) for item in value)
        or len(set(value)) != len(value)
        or (allowed is not None and not set(value) <= allowed)
    ):
        raise TranscriptionReviewError("invalid review output")
    return tuple(value)


def _parse_chunk_result(value: str, *, expected_chunk: ReviewChunk) -> _ChunkResult:
    payload = _exact_object(
        _strict_json_object(value),
        frozenset({"schema", "chunk_index", "corrections", "claims"}),
    )
    if payload["schema"] != REVIEW_RESULT_SCHEMA:
        raise TranscriptionReviewError("invalid review output")
    if _exact_int(payload["chunk_index"], minimum=0, maximum=MAX_REVIEW_CHUNKS - 1) != expected_chunk.index:
        raise TranscriptionReviewError("invalid review output")
    segment_ids = {item.segment_id for item in expected_chunk.segments}
    raw_corrections = payload["corrections"]
    raw_claims = payload["claims"]
    if (
        not isinstance(raw_corrections, list)
        or not isinstance(raw_claims, list)
        or len(raw_corrections) > MAX_COLLECTION_ITEMS
        or len(raw_claims) > MAX_COLLECTION_ITEMS
    ):
        raise TranscriptionReviewError("invalid review output")
    corrections: list[_CorrectionSpec] = []
    seen_corrections: set[str] = set()
    for value_item in raw_corrections:
        item = _exact_object(
            value_item,
            frozenset(
                {
                    "segment_id",
                    "corrected_text",
                    "correction_kinds",
                    "reason_code",
                    "confidence_milli",
                    "requires_review",
                }
            ),
        )
        segment_id = _bounded_text(item["segment_id"], maximum=64)
        if segment_id not in segment_ids or segment_id in seen_corrections:
            raise TranscriptionReviewError("invalid review output")
        kinds = _strict_string_list(item["correction_kinds"], allowed=_CORRECTION_KINDS)
        reason_code = item["reason_code"]
        if reason_code not in _REASON_CODES or reason_code.removeprefix("asr_") not in kinds:
            raise TranscriptionReviewError("invalid review output")
        if not isinstance(item["requires_review"], bool):
            raise TranscriptionReviewError("invalid review output")
        corrections.append(
            _CorrectionSpec(
                segment_id,
                _bounded_text(item["corrected_text"], maximum=MAX_SEGMENT_TEXT_CHARS),
                tuple(sorted(kinds)),
                reason_code,
                _exact_int(item["confidence_milli"], minimum=0, maximum=1000),
                item["requires_review"],
            )
        )
        seen_corrections.add(segment_id)
    claims: list[_ClaimSpec] = []
    seen_claims: set[tuple[str, tuple[str, ...]]] = set()
    for value_item in raw_claims:
        item = _exact_object(
            value_item,
            frozenset(
                {
                    "claim_kind",
                    "segment_ids",
                    "assignee",
                    "critical_uncertainty",
                }
            ),
        )
        claim_kind = item["claim_kind"]
        if claim_kind not in _CLAIM_KINDS:
            raise TranscriptionReviewError("invalid review output")
        cited = _strict_string_list(item["segment_ids"])
        if not set(cited) <= segment_ids:
            raise TranscriptionReviewError("invalid review output")
        if not isinstance(item["critical_uncertainty"], bool):
            raise TranscriptionReviewError("invalid review output")
        assignee_value = item["assignee"]
        assignee: _AssigneeSpec | None
        if assignee_value is None:
            assignee = None
        else:
            assignee_item = _exact_object(
                assignee_value,
                frozenset({"display_text", "segment_id"}),
            )
            assignee = _AssigneeSpec(
                _bounded_text(assignee_item["display_text"], maximum=128),
                _bounded_text(assignee_item["segment_id"], maximum=64),
            )
        if claim_kind != "action_item" and assignee is not None:
            raise TranscriptionReviewError("invalid review output")
        key = (claim_kind, tuple(cited))
        if key in seen_claims:
            raise TranscriptionReviewError("invalid review output")
        seen_claims.add(key)
        claims.append(
            _ClaimSpec(
                claim_kind,
                cited,
                assignee,
                item["critical_uncertainty"],
            )
        )
    return _ChunkResult(tuple(corrections), tuple(claims))


def _lexical_tokens(value: str) -> tuple[str, ...]:
    return tuple(_LEXICAL.findall(value))


def _mechanical_kinds(before: str, after: str) -> tuple[str, ...]:
    if before == after:
        raise TranscriptionReviewError("invalid review output")
    before_tokens = _lexical_tokens(before)
    after_tokens = _lexical_tokens(after)
    if tuple(token.casefold() for token in before_tokens) != tuple(
        token.casefold() for token in after_tokens
    ):
        raise TranscriptionReviewError("unsafe review output")
    kinds: set[str] = set()
    if before_tokens != after_tokens:
        kinds.add("casing")
    before_skeleton = _LEXICAL.sub("\u25a0", before)
    after_skeleton = _LEXICAL.sub("\u25a0", after)
    whitespace_before = "".join(
        character for character in before_skeleton if character.isspace() or character == "\u25a0"
    )
    whitespace_after = "".join(
        character for character in after_skeleton if character.isspace() or character == "\u25a0"
    )
    punctuation_before = "".join(
        character for character in before_skeleton if not character.isspace()
    )
    punctuation_after = "".join(
        character for character in after_skeleton if not character.isspace()
    )
    if whitespace_before != whitespace_after:
        kinds.add("whitespace")
    if punctuation_before != punctuation_after:
        kinds.add("punctuation")
    if not kinds:
        raise TranscriptionReviewError("unsafe review output")
    return tuple(sorted(kinds))


def _canonical_id(prefix: str, *parts: Any) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{prefix}_{sha256(encoded.encode('utf-8')).hexdigest()[:32]}"


def _token_boundary_contains(source: str, candidate: str) -> bool:
    return re.search(
        rf"(?<!\w){re.escape(candidate)}(?!\w)",
        source,
        flags=re.UNICODE,
    ) is not None


def _valid_assignee_display(source: str, candidate: str) -> bool:
    """Accept only an exact, non-empty lexical span from raw evidence."""

    return (
        candidate == candidate.strip()
        and bool(_LEXICAL.search(candidate))
        and _token_boundary_contains(source, candidate)
    )


def _principal_ref(record: TranscriptionRecord, display_text: str) -> str:
    normalized = " ".join(
        unicodedata.normalize("NFKC", display_text).casefold().split()
    )
    return _canonical_id(
        "principal",
        record.artifact.owner_id,
        record.artifact.artifact_id,
        normalized,
    )


def _build_outcome(
    record: TranscriptionRecord,
    results: tuple[_ChunkResult, ...],
) -> ReviewOutcome:
    correction_specs = tuple(
        item for result in results for item in result.corrections
    )
    claim_specs = tuple(item for result in results for item in result.claims)
    if (
        len(correction_specs) > MAX_COLLECTION_ITEMS
        or not claim_specs
        or len(claim_specs) > MAX_COLLECTION_ITEMS
        or any(item.requires_review for item in correction_specs)
        or any(item.critical_uncertainty for item in claim_specs)
    ):
        raise TranscriptionReviewError("review requires human confirmation")
    segment_map = {item.segment_id: item for item in record.segments}
    ordinal = {item.segment_id: item.ordinal for item in record.segments}
    corrections: list[CorrectionProposal] = []
    corrected_text: dict[str, str] = {}
    for spec in correction_specs:
        segment = segment_map.get(spec.segment_id)
        if segment is None:
            raise TranscriptionReviewError("invalid review output")
        derived_kinds = _mechanical_kinds(segment.text, spec.corrected_text)
        if derived_kinds != spec.correction_kinds:
            raise TranscriptionReviewError("invalid review output")
        correction = CorrectionProposal(
            _canonical_id(
                "correction",
                record.artifact.artifact_id,
                record.artifact.source_sha256,
                segment.segment_id,
                segment.text_sha256,
                spec.corrected_text,
                spec.correction_kinds,
                spec.reason_code,
                spec.confidence_milli,
            ),
            segment.segment_id,
            record.artifact.artifact_id,
            record.artifact.owner_id,
            record.artifact.source_sha256,
            segment.text_sha256,
            spec.corrected_text,
            sha256(spec.corrected_text.encode("utf-8")).hexdigest(),
            spec.correction_kinds,
            spec.reason_code,
            spec.confidence_milli,
            False,
        )
        correction.validates_against(segment)
        corrections.append(correction)
        corrected_text[segment.segment_id] = spec.corrected_text
    corrections.sort(key=lambda item: (ordinal[item.segment_id], item.correction_id))

    evidence_by_segments: dict[tuple[str, ...], ProtocolEvidence] = {}
    claims: list[ProtocolClaim] = []
    seen_claims: set[tuple[str, tuple[str, ...]]] = set()
    for spec in claim_specs:
        cited = tuple(sorted(spec.segment_ids, key=lambda item: ordinal.get(item, -1)))
        if (
            not cited
            or len(set(cited)) != len(cited)
            or not set(cited) <= set(segment_map)
            or (spec.claim_kind, cited) in seen_claims
        ):
            raise TranscriptionReviewError("invalid review output")
        seen_claims.add((spec.claim_kind, cited))
        evidence = evidence_by_segments.get(cited)
        if evidence is None:
            evidence = ProtocolEvidence(
                _canonical_id(
                    "evidence",
                    record.artifact.artifact_id,
                    record.artifact.source_sha256,
                    cited,
                ),
                record.artifact.artifact_id,
                record.artifact.owner_id,
                cited,
                record.artifact.source_sha256,
            )
            evidence_by_segments[cited] = evidence
        text = "\n".join(
            corrected_text.get(segment_id, segment_map[segment_id].text)
            for segment_id in cited
        )
        assignee_ref = None
        assignee_segments: tuple[str, ...] = ()
        if spec.assignee is not None:
            if (
                spec.claim_kind != "action_item"
                or spec.assignee.segment_id not in cited
                or not _valid_assignee_display(
                    segment_map[spec.assignee.segment_id].text,
                    spec.assignee.display_text,
                )
            ):
                raise TranscriptionReviewError("unsupported assignee")
            assignee_ref = _principal_ref(record, spec.assignee.display_text)
            assignee_segments = (spec.assignee.segment_id,)
        claim = ProtocolClaim(
            _canonical_id(
                "claim",
                record.artifact.artifact_id,
                spec.claim_kind,
                text,
                evidence.evidence_id,
                assignee_ref,
                assignee_segments,
            ),
            record.artifact.owner_id,
            spec.claim_kind,
            text,
            (evidence.evidence_id,),
            assignee_ref,
            assignee_segments,
            False,
        )
        claims.append(claim)
    claims.sort(
        key=lambda item: (
            _CLAIM_RANK[item.claim_kind],
            min(
                ordinal[segment_id]
                for evidence_id in item.evidence_ids
                for evidence in evidence_by_segments.values()
                if evidence.evidence_id == evidence_id
                for segment_id in evidence.segment_ids
            ),
            item.claim_id,
        )
    )
    evidence_values = tuple(
        sorted(
            evidence_by_segments.values(),
            key=lambda item: (
                min(ordinal[segment_id] for segment_id in item.segment_ids),
                item.evidence_id,
            ),
        )
    )
    protocol = ProtocolDocument(
        _canonical_id(
            "protocol",
            record.artifact.artifact_id,
            record.artifact.source_sha256,
            tuple(item.to_dict() for item in evidence_values),
            tuple(item.to_dict() for item in claims),
        ),
        record.artifact.artifact_id,
        record.artifact.owner_id,
        evidence_values,
        tuple(claims),
    )
    return ReviewOutcome(tuple(corrections), protocol, "protocol_ready", "review_ready")


__all__ = [
    "MAX_REVIEW_CHUNKS",
    "MAX_REVIEW_OUTPUT_BYTES",
    "REVIEW_REQUEST_SCHEMA",
    "REVIEW_RESULT_SCHEMA",
    "ReviewChunk",
    "ReviewOutcome",
    "ReviewTransport",
    "TranscriptionReviewError",
    "TranscriptionReviewer",
]
