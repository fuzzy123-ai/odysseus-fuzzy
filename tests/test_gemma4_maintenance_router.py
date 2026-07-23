import json

from src.gemma4_maintenance_router import (
    GemmaMaintenanceSurface,
    GemmaOutputStatus,
    get_prompt_capsule,
    list_prompt_capsules,
    plan_gemma4_maintenance_route,
    validate_gemma4_maintenance_output,
)
from src.maintenance_model_policy import MaintenanceRouteAction, MaintenanceWorkload


def test_router_maps_surfaces_to_capsules_and_bounded_route_without_raw_content():
    plan = plan_gemma4_maintenance_route(
        surface=GemmaMaintenanceSurface.UNIVERSAL_INBOX,
        classification="private",
        excerpt="Private worksheet raw text must not persist.",
        source_refs=("telegram-attachment:abc",),
    )
    payload = plan.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["surface"] == "universal_inbox"
    assert payload["prompt_capsule"]["capsule_id"] == "gemma4.inbox_triage.v1"
    assert payload["route"]["action"] == MaintenanceRouteAction.STAY_ON_MAINTENANCE_MODEL.value
    assert payload["route"]["model_ref"] == "gemma3:4b"
    assert payload["route"]["api_escalation_allowed"] is False
    assert payload["queue_policy"]["max_queue_concurrency"] == 1
    assert payload["queue_policy"]["token_budget"] == 1200
    assert payload["raw_content_persisted"] is False
    assert "Private worksheet raw text" not in encoded


def test_sensitive_route_blocks_api_but_allows_redacted_external_plan_metadata():
    plan = plan_gemma4_maintenance_route(
        surface="memory",
        workload=MaintenanceWorkload.MEMORY_WRITE_INTENT,
        classification="sensitive",
        dsgvo_mode=True,
        api_escalation_allowed=True,
    )

    assert plan.route["local_only_required"] is True
    assert plan.route["api_escalation_allowed"] is False
    assert plan.route["raw_content_allowed"] is False
    assert plan.route["truth_write_allowed"] is False


def test_capsule_prompt_is_runtime_only_and_schema_bound():
    capsule = get_prompt_capsule(MaintenanceWorkload.RAPTORGRAPH_ABSTRACTION)
    prompt = capsule.build_prompt(
        metadata={"surface": "raptorgraph", "host_path": "C:/private/file.pdf"},
        excerpt="bounded private excerpt",
    )

    assert "Return JSON only" in prompt
    assert "candidate_facts" in prompt
    assert "host_path" not in prompt
    assert "bounded private excerpt" in prompt


def test_validation_accepts_schema_and_rejects_raw_content_keys():
    valid = validate_gemma4_maintenance_output(
        {
            "status": "ready",
            "classification": "private",
            "document_type": "worksheet",
            "confidence": 0.9,
            "review_reason": "",
            "provenance": {"source_hash": "sha256:abc"},
        },
        capsule=MaintenanceWorkload.INBOX_TRIAGE,
    )

    assert valid.status is GemmaOutputStatus.VALID
    assert valid.schema_valid is True

    blocked = validate_gemma4_maintenance_output(
        {
            "status": "ready",
            "classification": "private",
            "document_type": "worksheet",
            "confidence": 0.9,
            "review_reason": "",
            "provenance": {"source_hash": "sha256:abc"},
            "raw_text": "do not expose",
        },
        capsule=MaintenanceWorkload.INBOX_TRIAGE,
    )

    assert blocked.status is GemmaOutputStatus.BLOCKED
    assert blocked.review_required is True
    assert any(reason.startswith("output_forbidden_key") for reason in blocked.failure_reasons)


def test_invalid_json_is_retry_not_memory_write():
    result = validate_gemma4_maintenance_output(
        "not json",
        capsule=MaintenanceWorkload.MEMORY_WRITE_INTENT,
    )

    assert result.status is GemmaOutputStatus.RETRY
    assert result.retry_allowed is True
    assert result.schema_valid is False


def test_prompt_capsule_library_covers_roadmap_maintenance_jobs():
    capsule_ids = {capsule.capsule_id for capsule in list_prompt_capsules()}

    assert "gemma4.inbox_triage.v1" in capsule_ids
    assert "gemma4.sensitivity_classification.v1" in capsule_ids
    assert "gemma4.memory_write_intent.v1" in capsule_ids
    assert "gemma4.raptorgraph_candidate.v1" in capsule_ids
    assert "gemma4.voice_transcript.v1" in capsule_ids
    assert "gemma4.export_conversion_preflight.v1" in capsule_ids
