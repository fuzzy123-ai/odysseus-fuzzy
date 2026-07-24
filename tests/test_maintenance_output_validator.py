import json

import pytest

from src.gemma4_maintenance_router import get_prompt_capsule
from src.local_model_scheduler import LocalModelAdmissionRegistry
from src.maintenance_llm_runtime import (
    MaintenanceLLMMessage,
    MaintenanceLLMRequest,
    MaintenanceLLMUpstreamResponse,
)
from src.maintenance_model_policy import (
    MaintenanceModelProfile,
    MaintenanceWorkload,
)
from src.maintenance_output_validator import (
    MAX_MAINTENANCE_OUTPUT_CHARS,
    MaintenanceOutputStatus,
    call_validated_maintenance_llm,
    call_validated_maintenance_llm_async,
    maintenance_output_schema_instruction,
    validate_maintenance_output,
)


ENDPOINT = "http://127.0.0.1:11434"
SOURCE_HASH = "sha256:" + "a" * 64
OTHER_SOURCE_HASH = "sha256:" + "b" * 64
SECRET_OUTPUT = "private-model-output-98f2"


def _capsule(workload=MaintenanceWorkload.INBOX_TRIAGE):
    return get_prompt_capsule(workload)


def _valid_payload(**overrides):
    payload = {
        "status": "ready",
        "classification": "private",
        "document_type": "reference",
        "confidence": 0.92,
        "review_reason": "",
        "provenance": {"source_hash": SOURCE_HASH},
    }
    payload.update(overrides)
    return payload


def _request() -> MaintenanceLLMRequest:
    return MaintenanceLLMRequest(
        endpoint=ENDPOINT,
        messages=(
            MaintenanceLLMMessage("system", "isolated maintenance"),
            MaintenanceLLMMessage("user", "bounded synthetic fixture"),
        ),
        profile=MaintenanceModelProfile.create(runtime_enabled=True),
        max_attempts=1,
        stream=False,
        fallback_requested=False,
        truth_write_requested=False,
    )


def test_strict_valid_output_returns_content_free_candidate_audit() -> None:
    validation = validate_maintenance_output(
        json.dumps(_valid_payload()),
        capsule=_capsule(),
        allowed_source_hashes=(SOURCE_HASH,),
    )
    audit = validation.audit_dict()
    encoded = json.dumps(audit, sort_keys=True)

    assert validation.status is MaintenanceOutputStatus.VALID
    assert validation.schema_valid is True
    assert validation.parsed["document_type"] == "reference"
    assert audit["matched_source_count"] == 1
    assert audit["review_required"] is False
    assert audit["truth_write_authorized"] is False
    assert SOURCE_HASH not in encoded
    assert "reference" not in encoded


def test_strict_parser_rejects_fences_trailing_text_and_oversized_output() -> None:
    fenced = validate_maintenance_output(
        "```json\n" + json.dumps(_valid_payload()) + "\n```",
        capsule=_capsule(),
        allowed_source_hashes=(SOURCE_HASH,),
    )
    trailing = validate_maintenance_output(
        json.dumps(_valid_payload()) + " explanation",
        capsule=_capsule(),
        allowed_source_hashes=(SOURCE_HASH,),
    )
    oversized = validate_maintenance_output(
        "x" * (MAX_MAINTENANCE_OUTPUT_CHARS + 1),
        capsule=_capsule(),
        allowed_source_hashes=(SOURCE_HASH,),
    )

    assert fenced.status is MaintenanceOutputStatus.RETRY
    assert trailing.status is MaintenanceOutputStatus.RETRY
    assert oversized.status is MaintenanceOutputStatus.RETRY
    assert fenced.failure_reasons == ("json_invalid",)
    assert trailing.failure_reasons == ("json_invalid",)
    assert oversized.failure_reasons == ("output_too_large",)


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({}, "schema_missing_fields"),
        ({"status": "ready"}, "schema_missing_fields"),
        (_valid_payload(confidence="high"), "schema_type"),
        (_valid_payload(confidence=1.5), "schema_bounds"),
        (_valid_payload(status="invented"), "schema_enum"),
        (_valid_payload(extra_field="no"), "schema_unknown_fields"),
    ],
)
def test_repairable_schema_failures_request_one_retry(payload, reason) -> None:
    validation = validate_maintenance_output(
        payload,
        capsule=_capsule(),
        allowed_source_hashes=(SOURCE_HASH,),
    )

    assert validation.status is MaintenanceOutputStatus.RETRY
    assert validation.retry_allowed is True
    assert reason in validation.failure_reasons
    assert validation.parsed == {}


def test_hallucinated_source_is_blocked_without_retry_or_parsed_candidate() -> None:
    validation = validate_maintenance_output(
        _valid_payload(provenance={"source_hash": OTHER_SOURCE_HASH}),
        capsule=_capsule(),
        allowed_source_hashes=(SOURCE_HASH,),
    )

    assert validation.status is MaintenanceOutputStatus.BLOCKED
    assert validation.failure_reasons == ("provenance_hallucinated",)
    assert validation.retry_allowed is False
    assert validation.review_required is True
    assert validation.parsed == {}


def test_forbidden_raw_content_is_blocked_and_failure_code_is_content_free() -> None:
    validation = validate_maintenance_output(
        _valid_payload(raw_text=SECRET_OUTPUT),
        capsule=_capsule(),
        allowed_source_hashes=(SOURCE_HASH,),
    )
    encoded = json.dumps(validation.audit_dict(), sort_keys=True)

    assert validation.status is MaintenanceOutputStatus.BLOCKED
    assert "forbidden_content" in validation.failure_reasons
    assert SECRET_OUTPUT not in encoded
    assert "raw_text" not in encoded


def test_semantic_memory_conflict_is_blocked() -> None:
    payload = _valid_payload(
        should_remember=False,
        memory_write_intent_status="ready",
    )
    validation = validate_maintenance_output(
        payload,
        capsule=_capsule(MaintenanceWorkload.MEMORY_WRITE_INTENT),
        allowed_source_hashes=(SOURCE_HASH,),
    )

    assert validation.status is MaintenanceOutputStatus.BLOCKED
    assert validation.failure_reasons == ("semantic_conflict",)
    assert validation.review_required is True


def test_graph_candidate_fact_cannot_invent_a_nested_source() -> None:
    payload = _valid_payload(
        status="candidate",
        candidate_facts=[
            {
                "subject": "project",
                "predicate": "uses",
                "object": "podman",
                "source_hash": OTHER_SOURCE_HASH,
            }
        ],
        contradiction_hints=[],
    )
    validation = validate_maintenance_output(
        payload,
        capsule=_capsule(MaintenanceWorkload.RAPTORGRAPH_ABSTRACTION),
        allowed_source_hashes=(SOURCE_HASH,),
    )

    assert validation.status is MaintenanceOutputStatus.BLOCKED
    assert "provenance_hallucinated" in validation.failure_reasons
    assert validation.parsed == {}


def test_sync_runtime_repairs_once_then_returns_validated_candidate(monkeypatch) -> None:
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1")
    calls = []

    def attempt(upstream):
        calls.append(upstream)
        content = "{\"status\":" if len(calls) == 1 else json.dumps(_valid_payload())
        return MaintenanceLLMUpstreamResponse(200, {"message": {"content": content}})

    run = call_validated_maintenance_llm(
        _request(),
        capsule=_capsule(),
        allowed_source_hashes=(SOURCE_HASH,),
        attempt=attempt,
        registry=LocalModelAdmissionRegistry(),
    )
    audit = run.audit_dict()

    assert len(calls) == 2
    assert calls[1].payload["messages"][0]["content"].startswith("Retry once")
    assert calls[1].payload["stream"] is False
    assert run.call_count == 2
    assert run.retry_count == 1
    assert run.validation.status is MaintenanceOutputStatus.VALID
    assert audit["status"] == "validated_candidate"
    assert audit["truth_write_performed"] is False


def test_second_invalid_sync_output_stops_after_one_retry(monkeypatch) -> None:
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1")
    calls = 0

    def attempt(_upstream):
        nonlocal calls
        calls += 1
        return MaintenanceLLMUpstreamResponse(
            200,
            {"message": {"content": "truncated {"}},
        )

    run = call_validated_maintenance_llm(
        _request(),
        capsule=_capsule(),
        allowed_source_hashes=(SOURCE_HASH,),
        attempt=attempt,
        registry=LocalModelAdmissionRegistry(),
    )

    assert calls == 2
    assert run.retry_count == 1
    assert run.validation.status is MaintenanceOutputStatus.REVIEW
    assert run.validation.review_required is True
    assert run.validation.retry_allowed is False
    assert run.validation.parsed == {}
    assert run.audit_dict()["truth_write_performed"] is False


@pytest.mark.asyncio
async def test_async_runtime_uses_same_single_retry_boundary(monkeypatch) -> None:
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1")
    calls = 0

    async def attempt(_upstream):
        nonlocal calls
        calls += 1
        content = "not-json" if calls == 1 else json.dumps(_valid_payload())
        return MaintenanceLLMUpstreamResponse(200, {"message": {"content": content}})

    run = await call_validated_maintenance_llm_async(
        _request(),
        capsule=_capsule(),
        allowed_source_hashes=(SOURCE_HASH,),
        attempt=attempt,
        registry=LocalModelAdmissionRegistry(),
    )

    assert calls == 2
    assert run.call_count == 2
    assert run.retry_count == 1
    assert run.validation.status is MaintenanceOutputStatus.VALID


def test_schema_instruction_is_exact_bounded_and_runtime_only() -> None:
    instruction = maintenance_output_schema_instruction(
        _capsule(),
        allowed_source_hashes=(SOURCE_HASH,),
    )

    assert "exactly these keys" in instruction
    assert "provenance" in instruction
    assert SOURCE_HASH in instruction
    assert len(instruction) < 800
