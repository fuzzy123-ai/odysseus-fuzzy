from __future__ import annotations

from dataclasses import dataclass
import json

import pytest

from src.maintenance_llm_runtime import MaintenanceLLMResult
from src.maintenance_model_policy import MaintenanceModelProfile
from src.transcription_contracts import (
    AudioArtifact,
    BackupReceipt,
    RawTranscriptSegment,
    RecordingAuthorizationRef,
    RetentionPolicyRef,
    TranscriptionJob,
    TranscriptionRecord,
)
from src.transcription_local_model import (
    Gemma3LocalReviewTransport,
    LocalTranscriptionReviewTransportError,
)
from src.transcription_review import (
    REVIEW_RESULT_SCHEMA,
    TranscriptionReviewer,
)


OWNER = "owner_0123456789abcdef"
DIGEST = "a" * 64
NOW = "2026-07-26T20:45:00Z"


def _record(
    *,
    texts: tuple[str, ...] = ("Klaus übernimmt den Bericht bis 12. März.",),
) -> TranscriptionRecord:
    artifact = AudioArtifact(
        "artifact_a",
        OWNER,
        DIGEST,
        42,
        "audio/wav",
        "blobs/ar/artifact_a.audio",
        NOW,
        {"language": "de"},
    )
    authorization = RecordingAuthorizationRef(
        "auth_a", OWNER, "policy_a", True, None
    )
    retention = RetentionPolicyRef("retention_a", OWNER, 30, "version_a")
    receipt = BackupReceipt(
        "receipt_a", "artifact_a", OWNER, DIGEST, "snapshot_a", NOW, True
    )
    job = TranscriptionJob(
        "job_a",
        "artifact_a",
        OWNER,
        "auth_a",
        "retention_a",
        "correcting",
        "receipt_a",
    )
    segments = tuple(
        RawTranscriptSegment(
            f"segment_{index}",
            "artifact_a",
            OWNER,
            DIGEST,
            index * 1000,
            (index + 1) * 1000,
            text,
            index,
            -0.2,
            0.01,
            1.0,
            "de",
            0.99,
        )
        for index, text in enumerate(texts)
    )
    return TranscriptionRecord(
        artifact,
        authorization,
        retention,
        job,
        segments,
        (),
        receipt,
        None,
    )


def _valid_payload(*, chunk_index: int = 0) -> dict[str, object]:
    return {
        "schema": REVIEW_RESULT_SCHEMA,
        "chunk_index": chunk_index,
        "corrections": [
            {
                "segment_id": "segment_0",
                "corrected_text": "Klaus übernimmt den Bericht bis 12. März!",
                "correction_kinds": ["punctuation"],
                "reason_code": "asr_punctuation",
                "confidence_milli": 920,
                "requires_review": False,
            }
        ],
        "claims": [
            {
                "claim_kind": "action_item",
                "segment_ids": ["segment_0"],
                "assignee": {
                    "display_text": "Klaus",
                    "segment_id": "segment_0",
                },
                "critical_uncertainty": False,
            }
        ],
    }


@dataclass
class _Transport:
    response: object
    calls: list[dict[str, object]]

    def review(self, request_json: str) -> str:
        request = json.loads(request_json)
        self.calls.append(request)
        value = self.response(request) if callable(self.response) else self.response
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def test_valid_review_is_deterministic_and_derived_from_immutable_segments() -> None:
    record = _record()
    transport = _Transport(_valid_payload(), [])
    reviewer = TranscriptionReviewer(transport)

    first = reviewer.review(record)
    second = reviewer.review(record)

    assert first == second
    assert first.target_state == "protocol_ready"
    assert first.corrections[0].parent_text_sha256 == record.segments[0].text_sha256
    assert first.corrections[0].corrected_text.endswith("!")
    assert first.protocol is not None
    claim = first.protocol.claims[0]
    assert claim.text == first.corrections[0].corrected_text
    assert claim.assignee_ref is not None
    assert claim.assignee_evidence_segment_ids == ("segment_0",)
    assert first.protocol.evidence[0].segment_ids == ("segment_0",)
    assert record.segments[0].text.endswith(".")
    assert set(transport.calls[0]) == {
        "schema",
        "chunk_index",
        "chunk_count",
        "artifact_id",
        "source_sha256",
        "segments",
    }
    assert "owner_id" not in json.dumps(transport.calls[0], sort_keys=True)


@pytest.mark.parametrize(
    "response",
    [
        '{"schema":"odysseus.transcription_review_result.v1","chunk_index":0,'
        '"corrections":[],"claims":[],"claims":[]}',
        "```json\n{}\n```",
        "{} trailing",
        "[]",
        '{"schema":"odysseus.transcription_review_result.v1","chunk_index":NaN,'
        '"corrections":[],"claims":[]}',
        '{"schema":"odysseus.transcription_review_result.v1","chunk_index":true,'
        '"corrections":[],"claims":[]}',
        '{"schema":"odysseus.transcription_review_result.v1","chunk_index":0,'
        '"corrections":[],"claims":[],"owner_id":"owner_0123456789abcdef"}',
    ],
)
def test_strict_parser_rejects_duplicates_fences_trailing_nonfinite_and_unknown(
    response: str,
) -> None:
    outcome = TranscriptionReviewer(_Transport(response, [])).review(_record())

    assert outcome.target_state == "needs_review"
    assert outcome.corrections == ()
    assert outcome.protocol is None


def test_oversize_output_and_more_than_four_chunks_fail_without_partial_calls() -> None:
    oversized = "x" * (64 * 1024 + 1)
    transport = _Transport(oversized, [])
    assert TranscriptionReviewer(transport).review(_record()).target_state == "needs_review"

    long_record = _record(texts=tuple("Wort " * 650 for _ in range(5)))
    never = _Transport(_valid_payload(), [])
    outcome = TranscriptionReviewer(never).review(long_record)
    assert outcome.target_state == "needs_review"
    assert never.calls == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["corrections"][0].update(
            corrected_text="Klaus übernimmt den Bericht bis 13. März.",
            correction_kinds=["orthography"],
            reason_code="asr_orthography",
            requires_review=True,
        ),
        lambda payload: payload["corrections"][0].update(
            corrected_text="Maria übernimmt den Bericht bis 12. März.",
            correction_kinds=["orthography"],
            reason_code="asr_orthography",
            requires_review=False,
        ),
        lambda payload: payload["claims"][0].update(critical_uncertainty=True),
        lambda payload: payload["claims"][0].update(segment_ids=["segment_foreign"]),
        lambda payload: payload["claims"][0]["assignee"].update(
            display_text="Maria"
        ),
    ],
)
def test_unsafe_correction_uncertainty_foreign_citation_and_assignee_fail_atomically(
    mutate,
) -> None:
    payload = _valid_payload()
    mutate(payload)

    outcome = TranscriptionReviewer(_Transport(payload, [])).review(_record())

    assert outcome.target_state == "needs_review"
    assert outcome.corrections == ()
    assert outcome.protocol is None


def test_claim_text_is_server_derived_and_model_free_text_is_rejected() -> None:
    payload = _valid_payload()
    payload["claims"][0]["text"] = "Maria erledigt alles morgen."

    outcome = TranscriptionReviewer(_Transport(payload, [])).review(_record())

    assert outcome.target_state == "needs_review"
    assert outcome.protocol is None


def test_local_transport_is_exact_bounded_and_content_free() -> None:
    observed = []
    response = json.dumps(_valid_payload(), ensure_ascii=False)

    def invoke(request):
        observed.append(request)
        return MaintenanceLLMResult(response, 1)

    profile = MaintenanceModelProfile.create(runtime_enabled=True)
    transport = Gemma3LocalReviewTransport(
        "http://ollama:11434/api",
        profile,
        invoke=invoke,
    )

    assert transport.review('{"bounded":true}') == response
    request = observed[0]
    assert request.model == "gemma3:4b"
    assert request.provider == "local_ollama"
    assert request.temperature == 0.0
    assert request.max_attempts == 1
    assert request.stream is False
    assert request.fallback_requested is False
    assert request.truth_write_requested is False

    secret = "private-output-must-not-leak"

    def fail(_request):
        raise RuntimeError(secret)

    failing = Gemma3LocalReviewTransport(
        "http://ollama:11434",
        profile,
        invoke=fail,
    )
    with pytest.raises(LocalTranscriptionReviewTransportError) as captured:
        failing.review("{}")
    assert secret not in str(captured.value)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://ollama:11434/api",
        "http://example.com:11434/api",
        "http://user:pass@ollama:11434/api",
        "http://ollama:11434/v1",
    ],
)
def test_local_transport_rejects_nonexact_endpoint_scope(endpoint: str) -> None:
    with pytest.raises(LocalTranscriptionReviewTransportError):
        Gemma3LocalReviewTransport(
            endpoint,
            MaintenanceModelProfile.create(runtime_enabled=True),
            invoke=lambda _request: MaintenanceLLMResult("{}", 1),
        )
