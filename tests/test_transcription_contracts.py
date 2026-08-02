from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import math

import pytest

from src.transcription_contracts import (
    AudioArtifact,
    BackupReceipt,
    ContractError,
    CorrectionProposal,
    ProtocolClaim,
    ProtocolDocument,
    ProtocolEvidence,
    RawTranscriptSegment,
    RecordingAuthorizationRef,
    RetentionPolicyRef,
    TranscriptionJob,
    TranscriptionRecord,
)


OWNER = "owner_0123456789abcdef"
OTHER_OWNER = "owner_fedcba9876543210"
DIGEST = "a" * 64
NOW = "2026-07-26T19:10:00Z"
RECEIPT_STATES = {"backup_protected", "transcribing", "transcribed", "correcting", "corrected", "protocol_ready", "needs_review"}
OUTPUT_STATES = {"transcribed", "correcting", "corrected", "protocol_ready", "needs_review"}
CORRECTION_STATES = {"corrected", "protocol_ready", "needs_review"}


def _record(*, state: str = "needs_review", critical: bool = False) -> TranscriptionRecord:
    artifact = AudioArtifact("artifact_a", OWNER, DIGEST, 42, "audio/wav", "blobs/ar/artifact_a.audio", NOW, {"language": "de"})
    authorization = RecordingAuthorizationRef("auth_a", OWNER, "policy_a", True, None)
    retention = RetentionPolicyRef("retention_a", OWNER, 30, "version_a")
    receipt = BackupReceipt("receipt_a", "artifact_a", OWNER, DIGEST, "snapshot_a", NOW, True) if state in RECEIPT_STATES else None
    job = TranscriptionJob("job_a", "artifact_a", OWNER, "auth_a", "retention_a", state, "receipt_a" if receipt else None)
    segment = RawTranscriptSegment("segment_a", "artifact_a", OWNER, DIGEST, 0, 500, "Klaus speaks on 12 March.", 0, -0.3, 0.02, 1.1, "de", 0.98)
    corrected_text = "Klaus speaks on 12 March!"
    correction = CorrectionProposal("correction_a", "segment_a", "artifact_a", OWNER, DIGEST, segment.text_sha256, corrected_text, sha256(corrected_text.encode("utf-8")).hexdigest(), ("punctuation",), "asr_punctuation", 900, False)
    evidence = ProtocolEvidence("evidence_a", "artifact_a", OWNER, ("segment_a",), DIGEST)
    claim = ProtocolClaim("claim_a", OWNER, "statement", "Klaus speaks on 12 March.", ("evidence_a",), critical_uncertainty=critical)
    protocol = ProtocolDocument("protocol_a", "artifact_a", OWNER, (evidence,), (claim,))
    segments = (segment,) if state in OUTPUT_STATES else ()
    corrections = (correction,) if state in CORRECTION_STATES else ()
    return TranscriptionRecord(artifact, authorization, retention, job, segments, corrections, receipt, protocol if state in {"protocol_ready", "needs_review"} else None)


def test_deterministic_roundtrip_and_immutability() -> None:
    record = _record()
    encoded = record.to_json()
    assert encoded == record.to_json()
    assert TranscriptionRecord.from_json(encoded) == record
    with pytest.raises(FrozenInstanceError):
        record.artifact.source_sha256 = "b" * 64  # type: ignore[misc]


def test_strict_deserialization_rejects_unknown_missing_malformed_and_duplicate_fields() -> None:
    data = _record().to_dict()
    data["extra"] = "no"
    with pytest.raises(ContractError):
        TranscriptionRecord.from_dict(data)
    malformed = _record().to_dict()
    del malformed["job"]
    with pytest.raises(ContractError):
        TranscriptionRecord.from_dict(malformed)
    with pytest.raises(ContractError):
        TranscriptionRecord.from_json('{"job":{},"job":{}}')
    with pytest.raises(ContractError):
        TranscriptionRecord.from_json("[]")


def test_exact_job_states_and_conservative_transitions() -> None:
    states = {"receiving", "stored", "backup_pending", "backup_protected", "transcribing", "transcribed", "correcting", "corrected", "protocol_ready", "needs_review", "failed", "deletion_pending", "deleted"}
    for state in states - RECEIPT_STATES:
        TranscriptionJob("job_a", "artifact_a", OWNER, "auth_a", "retention_a", state)
    for state in RECEIPT_STATES:
        TranscriptionJob("job_a", "artifact_a", OWNER, "auth_a", "retention_a", state, "receipt_a")
    for forbidden in {"accepted", "queued", "completed", "backup_done"}:
        with pytest.raises(ContractError):
            TranscriptionJob("job_a", "artifact_a", OWNER, "auth_a", "retention_a", forbidden)
    job = TranscriptionJob("job_a", "artifact_a", OWNER, "auth_a", "retention_a", "stored")
    with pytest.raises(ContractError):
        job.transition_to("transcribing")
    pending = job.transition_to("backup_pending")
    receipt = BackupReceipt("receipt_a", "artifact_a", OWNER, DIGEST, "snapshot_a", NOW, True)
    protected = pending.transition_to("backup_protected", receipt=receipt)
    assert protected.transition_to("transcribing").backup_receipt_id == "receipt_a"
    assert protected.transition_to("deletion_pending").state == "deletion_pending"
    correcting = TranscriptionJob(
        "job_a",
        "artifact_a",
        OWNER,
        "auth_a",
        "retention_a",
        "correcting",
        "receipt_a",
    )
    assert correcting.transition_to("protocol_ready").state == "protocol_ready"


def test_locator_shape_and_asr_evidence_are_strict_and_finite() -> None:
    with pytest.raises(ContractError):
        AudioArtifact("artifact_a", OWNER, DIGEST, 42, "audio/wav", "uploads/Alice-recording.wav", NOW)
    with pytest.raises(ContractError):
        AudioArtifact("artifact_a", OWNER, DIGEST, 42, "audio/wav", f"blobs/ar/{DIGEST}.audio", NOW)
    with pytest.raises(ContractError):
        RawTranscriptSegment("segment_a", "artifact_a", OWNER, DIGEST, 0, 1, "x", 0, math.nan, None, None, None, None)
    with pytest.raises(ContractError):
        RawTranscriptSegment("segment_a", "artifact_a", OWNER, DIGEST, 0, 1, "x", 0, None, 1.1, None, None, None)


def test_cross_owner_references_and_uncited_claims_are_rejected() -> None:
    record = _record()
    with pytest.raises(ContractError):
        replace(record, authorization=replace(record.authorization, owner_id=OTHER_OWNER))
    evidence = ProtocolEvidence("evidence_a", "artifact_a", OWNER, ("segment_a",), DIGEST)
    with pytest.raises(ContractError):
        ProtocolClaim("claim_a", OWNER, "decision", "Decided.", ())
    uncited = ProtocolClaim("claim_a", OWNER, "decision", "Decided.", ("evidence_missing",))
    with pytest.raises(ContractError):
        ProtocolDocument("protocol_a", "artifact_a", OWNER, (evidence,), (uncited,))
    with pytest.raises(ContractError):
        ProtocolClaim("claim_b", OWNER, "action_item", "Action.", ("evidence_a",), "principal_a")
    mismatched_assignee = ProtocolClaim("claim_c", OWNER, "action_item", "Action.", ("evidence_a",), "principal_a", ("segment_missing",))
    with pytest.raises(ContractError):
        ProtocolDocument("protocol_b", "artifact_a", OWNER, (evidence,), (mismatched_assignee,))


def test_mechanical_correction_rejects_semantic_lowercase_and_parent_changes() -> None:
    record = _record()
    segment = record.segments[0]
    correction = record.corrections[0]
    with pytest.raises(ContractError):
        replace(correction, corrected_text="Klaus writes on 12 March.").validates_against(segment)
    with pytest.raises(ContractError):
        replace(correction, corrected_text="Klaus speaks on 13 March.").validates_against(segment)
    with pytest.raises(ContractError):
        replace(correction, source_sha256="b" * 64).validates_against(segment)
    with pytest.raises(ContractError):
        CorrectionProposal("correction_b", "segment_a", "artifact_a", OWNER, DIGEST, segment.text_sha256, segment.text, sha256(segment.text.encode("utf-8")).hexdigest(), ("orthography",), "asr_orthography", 900, False)


def test_correction_reason_confidence_review_and_german_lexical_contract() -> None:
    record = _record()
    segment = RawTranscriptSegment("segment_german", "artifact_a", OWNER, DIGEST, 500, 900, "Stra\u00dfe hei\u00dft gro\u00df.", 1)
    corrected = "Stra\u00dfe hei\u00dft gro\u00df!"
    mechanical = CorrectionProposal("correction_german", "segment_german", "artifact_a", OWNER, DIGEST, segment.text_sha256, corrected, sha256(corrected.encode("utf-8")).hexdigest(), ("punctuation",), "asr_punctuation", 750, False)
    mechanical.validates_against(segment)
    with pytest.raises(ContractError):
        replace(mechanical, reason_code="free_text")
    with pytest.raises(ContractError):
        replace(mechanical, confidence_milli=1001)
    semantic_text = "Klaus writes on 12 March."
    semantic = CorrectionProposal("correction_semantic", "segment_a", "artifact_a", OWNER, DIGEST, record.segments[0].text_sha256, semantic_text, sha256(semantic_text.encode("utf-8")).hexdigest(), ("orthography",), "asr_orthography", 700, True)
    semantic.validates_against(record.segments[0])
    review_record = replace(record, corrections=(semantic,))
    assert review_record.job.state == "needs_review"
    with pytest.raises(ContractError):
        replace(review_record, job=replace(review_record.job, state="protocol_ready"))


def test_failure_and_deletion_preserve_existing_evidence_but_deleted_is_empty() -> None:
    transcribed = _record(state="transcribed")
    failed = replace(transcribed, job=replace(transcribed.job, state="failed"))
    assert failed.segments == transcribed.segments
    assert failed.receipt == transcribed.receipt
    ready = _record(state="protocol_ready")
    pending_deletion = replace(ready, job=replace(ready.job, state="deletion_pending"))
    assert pending_deletion.protocol == ready.protocol
    assert pending_deletion.corrections == ready.corrections
    with pytest.raises(ContractError):
        replace(pending_deletion, job=replace(pending_deletion.job, state="deleted"))


def test_direct_from_dict_rejects_non_sequence_and_non_mapping_nested_values() -> None:
    artifact = _record().artifact.to_dict()
    artifact["metadata"] = "not-a-mapping"
    with pytest.raises(ContractError):
        AudioArtifact.from_dict(artifact)
    evidence = ProtocolEvidence("evidence_a", "artifact_a", OWNER, ("segment_a",), DIGEST).to_dict()
    evidence["segment_ids"] = "segment_a"
    with pytest.raises(ContractError):
        ProtocolEvidence.from_dict(evidence)
    with pytest.raises(ContractError):
        ProtocolDocument("protocol_a", "artifact_a", OWNER, {"evidence_a"}, ())  # type: ignore[arg-type]


def test_state_content_consistency_and_critical_uncertainty() -> None:
    complete_record = _record()
    with pytest.raises(ContractError):
        replace(complete_record, job=TranscriptionJob("job_a", "artifact_a", OWNER, "auth_a", "retention_a", "stored"))
    with pytest.raises(ContractError):
        _record(state="protocol_ready", critical=True)
    review = _record(state="needs_review", critical=True)
    assert review.protocol is not None
    with pytest.raises(ContractError):
        replace(_record(state="backup_protected"), receipt=None)


def test_verified_protection_and_protocol_assignee_evidence() -> None:
    record = _record()
    with pytest.raises(ContractError):
        replace(record, receipt=replace(record.receipt, source_sha256="b" * 64))
    evidence = ProtocolEvidence("evidence_a", "artifact_a", OWNER, ("segment_a",), DIGEST)
    claim = ProtocolClaim("claim_action", OWNER, "action_item", "Action.", ("evidence_a",), "principal_a", ("segment_a",))
    assert ProtocolDocument("protocol_b", "artifact_a", OWNER, (evidence,), (claim,)).claims == (claim,)
