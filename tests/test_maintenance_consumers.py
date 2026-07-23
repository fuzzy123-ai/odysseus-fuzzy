import inspect
import json
import re

import pytest

from src.builtin_actions import (
    _call_builtin_maintenance_runtime,
    action_local_maintenance_dry_run,
)
from src.local_model_scheduler import LocalModelAdmissionRegistry
from src.maintenance_llm_runtime import MaintenanceLLMUpstreamResponse
from src.maintenance_model_policy import MaintenanceModelProfile
from src.sensitive_local_worker import (
    _call_sensitive_maintenance_runtime,
    execute_sensitive_local_analysis,
)
from src.universal_inbox_worker import (
    _call_universal_inbox_maintenance_runtime,
    run_universal_inbox_dry_run,
)


ENDPOINT = "http://127.0.0.1:11434"
SECRET_INPUT = "private-consumer-input-7d90"
SECRET_OUTPUT = '{"action":"review","secret":"private-output-c491"}'
RULES = {
    "schema": "odysseus.universal_inbox.routing_rules.v1",
    "version": 1,
    "policy_name": "maintenance_consumer_test",
    "defaults": {
        "incoming_root": "AI Inbox/Incoming",
        "review_root": "AI Inbox/Needs Review",
        "metadata_root": "AI Inbox/Metadata",
        "documents_root": "Documents",
        "min_auto_route_confidence": 0.82,
        "copy_only": True,
        "no_delete": True,
        "no_overwrite": True,
        "allowed_domains": ["private"],
        "fallback_document_type": "reference",
    },
    "review_triggers": ["partial_extraction", "low_confidence"],
    "routes": [
        {
            "domain": "private",
            "document_type": "reference",
            "target_template": "Documents/Private/Reference/{safe_title}{ext}",
        }
    ],
}


def _enabled_profile() -> MaintenanceModelProfile:
    return MaintenanceModelProfile.create(runtime_enabled=True)


def _valid_output(upstream, *, memory: bool = False) -> str:
    prompt = upstream.payload["messages"][1]["content"]
    match = re.search(r"sha256:[0-9a-f]{64}", prompt)
    assert match is not None
    payload = {
        "status": "ready",
        "classification": "private",
        "document_type": "reference",
        "confidence": 0.91,
        "review_reason": "",
        "provenance": {"source_hash": match.group(0)},
    }
    if memory:
        payload["memory_write_intent_status"] = "ready"
        payload["should_remember"] = True
    return json.dumps(payload, sort_keys=True)


def _assert_content_free_evidence(
    evidence: dict,
    *,
    consumer: str,
    review_required: bool,
) -> None:
    encoded = json.dumps(evidence, sort_keys=True)
    assert evidence["schema"] == "odysseus.maintenance_consumer_evidence.v1"
    assert evidence["consumer"] == consumer
    assert evidence["request"]["model_scope"] == "gemma3_4b"
    assert evidence["request"]["provider_scope"] == "local_ollama"
    assert evidence["request"]["role_scope"] == "maintenance"
    assert evidence["request"]["streaming_allowed"] is False
    assert evidence["request"]["fallback_allowed"] is False
    assert evidence["request"]["truth_write_allowed"] is False
    assert evidence["streaming_used"] is False
    assert evidence["fallback_used"] is False
    assert evidence["truth_write_performed"] is False
    assert evidence["output_retained"] is False
    assert evidence["review_required"] is review_required
    assert SECRET_INPUT not in encoded
    assert SECRET_OUTPUT not in encoded
    assert ENDPOINT not in encoded


@pytest.mark.asyncio
async def test_builtin_consumer_calls_only_typed_async_lane(monkeypatch) -> None:
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1")
    captured = []

    async def attempt(upstream):
        captured.append(upstream)
        return MaintenanceLLMUpstreamResponse(
            200,
            {"message": {"content": _valid_output(upstream, memory=True)}},
        )

    encoded, ok = await action_local_maintenance_dry_run(
        "owner-private",
        surface="memory",
        workload="memory_write_intent",
        source_refs=("source:private",),
        _maintenance_profile=_enabled_profile(),
        _maintenance_endpoint=ENDPOINT,
        _maintenance_excerpt=SECRET_INPUT,
        _maintenance_attempt=attempt,
        _maintenance_registry=LocalModelAdmissionRegistry(),
    )

    assert ok is True
    payload = json.loads(encoded)
    evidence = payload["runtime_evidence"]
    assert payload["model_called"] is True
    assert len(captured) == 1
    assert captured[0].payload["model"] == "gemma3:4b"
    assert captured[0].payload["stream"] is False
    assert captured[0].payload["options"]["num_predict"] == 1200
    assert SECRET_INPUT in captured[0].payload["messages"][1]["content"]
    assert evidence["status"] == "validated_candidate"
    assert evidence["result"]["retry_count"] == 0
    _assert_content_free_evidence(
        evidence,
        consumer="builtin_action",
        review_required=False,
    )


def test_universal_inbox_consumer_calls_only_typed_sync_lane(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1")
    inbox = tmp_path / "Incoming"
    inbox.mkdir()
    (inbox / "reference.md").write_text(SECRET_INPUT, encoding="utf-8")
    captured = []

    def attempt(upstream):
        captured.append(upstream)
        return MaintenanceLLMUpstreamResponse(
            200,
            {"message": {"content": _valid_output(upstream)}},
        )

    report = run_universal_inbox_dry_run(
        inbox,
        rules=RULES,
        settings={"maintenance_runtime_enabled": True},
        maintenance_endpoint=ENDPOINT,
        maintenance_attempt=attempt,
        maintenance_registry=LocalModelAdmissionRegistry(),
    ).to_dict()

    evidence = report["items"][0]["maintenance_route"]["runtime_evidence"]
    assert len(captured) == 1
    assert captured[0].payload["model"] == "gemma3:4b"
    assert captured[0].payload["stream"] is False
    assert SECRET_INPUT in captured[0].payload["messages"][1]["content"]
    assert evidence["status"] == "validated_candidate"
    assert evidence["result"]["retry_count"] == 0
    _assert_content_free_evidence(
        evidence,
        consumer="universal_inbox",
        review_required=False,
    )
    assert SECRET_INPUT not in json.dumps(report, sort_keys=True)


@pytest.mark.asyncio
async def test_sensitive_worker_consumer_calls_only_typed_async_lane(monkeypatch) -> None:
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1")
    captured = []

    async def attempt(upstream):
        captured.append(upstream)
        return MaintenanceLLMUpstreamResponse(
            200,
            {"message": {"content": _valid_output(upstream)}},
        )

    result = await execute_sensitive_local_analysis(
        json.dumps(
            {
                "source_ref": "inbox:opaque-private",
                "classification": "sensitive",
                "task": "Build a safe local classification.",
                "redacted_context": SECRET_INPUT,
                "local_only_required": True,
            }
        ),
        owner="owner-private",
        maintenance_profile=_enabled_profile(),
        maintenance_endpoint=ENDPOINT,
        maintenance_attempt=attempt,
        maintenance_registry=LocalModelAdmissionRegistry(),
    )

    evidence = result["local_job_request"]["runtime_evidence"]
    assert len(captured) == 1
    assert captured[0].payload["model"] == "gemma3:4b"
    assert captured[0].payload["stream"] is False
    assert SECRET_INPUT in captured[0].payload["messages"][1]["content"]
    assert evidence["status"] == "validated_candidate"
    assert evidence["result"]["retry_count"] == 0
    _assert_content_free_evidence(
        evidence,
        consumer="sensitive_local_worker",
        review_required=False,
    )


@pytest.mark.asyncio
async def test_all_consumers_are_default_off_before_transport(tmp_path) -> None:
    calls = 0

    def sync_attempt(_upstream):
        nonlocal calls
        calls += 1
        raise AssertionError("disabled consumer reached sync transport")

    async def async_attempt(_upstream):
        nonlocal calls
        calls += 1
        raise AssertionError("disabled consumer reached async transport")

    builtin_payload, ok = await action_local_maintenance_dry_run(
        "owner",
        _maintenance_attempt=async_attempt,
    )
    assert ok is True
    assert json.loads(builtin_payload)["model_called"] is False

    inbox = tmp_path / "Incoming"
    inbox.mkdir()
    (inbox / "reference.md").write_text("safe fixture", encoding="utf-8")
    inbox_report = run_universal_inbox_dry_run(
        inbox,
        rules=RULES,
        maintenance_attempt=sync_attempt,
    ).to_dict()
    assert "runtime_evidence" not in inbox_report["items"][0]["maintenance_route"]

    sensitive = await execute_sensitive_local_analysis(
        json.dumps(
            {
                "source_ref": "inbox:opaque",
                "classification": "sensitive",
                "task": "classify",
                "redacted_context": "safe fixture",
                "maintenance_runtime_enabled": True,
            }
        ),
        maintenance_attempt=async_attempt,
    )
    assert "runtime_evidence" not in sensitive["local_job_request"]
    assert calls == 0


def test_consumer_helpers_bypass_generic_llm_and_agent_prompt_paths() -> None:
    for helper in (
        _call_builtin_maintenance_runtime,
        _call_universal_inbox_maintenance_runtime,
        _call_sensitive_maintenance_runtime,
    ):
        source = inspect.getsource(helper)
        assert "maintenance_llm_runtime" in source
        assert "llm_core" not in source
        assert "llm_call(" not in source
        assert "stream_llm(" not in source
        assert "tool_rag" not in source
        assert "agent_loop" not in source


@pytest.mark.asyncio
async def test_builtin_consumer_failure_evidence_is_content_free(monkeypatch) -> None:
    monkeypatch.setenv("ODYSSEUS_LOCAL_MODEL_QUEUE", "1")

    async def attempt(_upstream):
        raise RuntimeError(f"transport leaked {SECRET_INPUT} {SECRET_OUTPUT}")

    encoded, ok = await action_local_maintenance_dry_run(
        "owner-private",
        _maintenance_profile=_enabled_profile(),
        _maintenance_endpoint=ENDPOINT,
        _maintenance_excerpt=SECRET_INPUT,
        _maintenance_attempt=attempt,
        _maintenance_registry=LocalModelAdmissionRegistry(),
    )

    assert ok is True
    evidence = json.loads(encoded)["runtime_evidence"]
    assert evidence["status"] == "review_required"
    assert evidence["result"]["reason"] == "transport_exception"
    assert evidence["model_called"] is False
    _assert_content_free_evidence(
        evidence,
        consumer="builtin_action",
        review_required=True,
    )
