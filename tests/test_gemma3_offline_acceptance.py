from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE_PATH = ROOT / "docs" / "plans" / "gemma3-memory-ops-offline-acceptance.json"


def _payload() -> dict:
    return json.loads(ACCEPTANCE_PATH.read_text(encoding="utf-8"))


def _canonical_text_sha256(content: bytes) -> str:
    normalized = content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(normalized).hexdigest().upper()


def test_offline_acceptance_schema_verdict_and_safe_state() -> None:
    payload = _payload()

    assert payload["schema"] == "odysseus.gemma3_memory_ops_offline_acceptance.v1"
    assert payload["verdict"] == "offline_go"
    assert payload["verification_state"] == "verified_offline_go_with_gmi15_packet_ready"
    assert payload["scope"]["model_scope"] == "gemma3_4b"
    assert payload["scope"]["role_scope"] == "maintenance"
    assert payload["scope"]["real_model_latency_in_scope"] is False
    assert payload["scope"]["live_runtime_in_scope"] is False
    assert payload["safe_state"] == {
        "runtime_default_enabled": False,
        "activation_authorized": False,
        "deploy_performed": False,
        "live_calls_performed": False,
        "network_io_performed": False,
        "service_changes_performed": False,
        "scrape_enabled": False,
        "grafana_enabled": False,
        "truth_writes_authorized": False,
        "writes_performed": False,
    }


def test_dependency_and_offline_slo_closure_is_complete() -> None:
    payload = _payload()
    dependencies = {item["slice"]: item["status"] for item in payload["dependency_closure"]}
    expected = {
        "GMI-00",
        "GMI-01",
        "GMI-02",
        "GMI-03",
        "GMI-04",
        "GMI-05",
        "GMI-06",
        "GMI-07",
        "GMI-08",
        "GMI-08C",
        "GMI-09A",
        "GMI-09B",
        "GMI-10",
        "GMI-11",
        "GMI-12",
        "GMI-13",
        "GMI-14",
        "GMI-15",
    }
    assert set(dependencies) == expected
    assert dependencies["GMI-15"] == "accepted_repo_packet"
    assert {value for key, value in dependencies.items() if key != "GMI-15"} == {
        "accepted"
    }

    slos = payload["offline_slo_evidence"]
    required = [item for item in slos if item["required_offline"]]
    deferred = [item for item in slos if not item["required_offline"]]
    assert required
    assert {item["status"] for item in required} == {"pass"}
    assert deferred == [
        {
            "id": "real_model_warm_latency",
            "required_offline": False,
            "status": "deferred_live",
            "boundary": "requires_bounded_canary",
        }
    ]


def test_hash_manifest_matches_current_runtime_and_load_suite() -> None:
    payload = _payload()

    for relative_path, expected_hash in payload["hash_manifest"].items():
        path = ROOT / relative_path
        observed_hash = _canonical_text_sha256(path.read_bytes())
        assert observed_hash == expected_hash

    packet_hash_paths = {
        "activation_plan": "ops/homeserver/gemma3-maintenance-activation/activation-plan.json",
        "preflight": "ops/homeserver/gemma3-maintenance-activation/preflight.py",
        "validator": "ops/homeserver/gemma3-maintenance-activation/validate_packet.py",
        "canary": "ops/homeserver/gemma3-maintenance-activation/run_canary.py",
        "dashboard": "ops/homeserver/gemma3-maintenance-activation/grafana/gemma3-maintenance.json",
        "runbook": "ops/homeserver/gemma3-maintenance-activation/LIVE_RUNBOOK.md",
        "tests": "tests/test_gemma3_activation_packet.py",
    }
    for key, relative_path in packet_hash_paths.items():
        observed_hash = _canonical_text_sha256((ROOT / relative_path).read_bytes())
        assert observed_hash == payload["activation_packet"]["artifact_hashes"][key]


def test_acceptance_artifact_is_content_free() -> None:
    payload = _payload()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["privacy"] == {
        "content_free": True,
        "credentials_visible": False,
        "identities_visible": False,
        "raw_provider_targets_visible": False,
        "private_paths_visible": False,
        "model_output_visible": False,
        "high_cardinality_labels_allowed": False,
    }
    assert not re.search(r"https?://", encoded, re.IGNORECASE)
    assert not re.search(r"[A-Za-z]:\\", encoded)
    assert not re.search(r"/(?:home|Users|var/lib|mnt|srv|opt)/", encoded, re.IGNORECASE)
    for forbidden in (
        "bearer ",
        "api_key",
        "password",
        "cookie",
        "raw_prompt",
        "raw_output",
        "private_document_text",
        "chat_id",
        "owner_id",
        "source_ref",
    ):
        assert forbidden not in encoded.lower()


def test_open_risks_are_bound_to_the_two_live_gates_after_repo_packet_completion() -> None:
    payload = _payload()
    risk_ids = {risk["id"] for risk in payload["open_risks"]}

    assert risk_ids == {
        "real_model_latency_unmeasured",
        "live_metrics_visibility_unmeasured",
        "deploy_canary_rollback_unvalidated",
    }
    assert {risk["severity"] for risk in payload["open_risks"]} == {
        "activation_blocking"
    }
    assert payload["next_phase"]["slice"] == "GMI-LIVE-ACTIVATION"
    assert payload["next_phase"]["status"] == "repo_packet_complete_awaiting_separate_GRO_live_validation_and_GMI_live_go"
    assert payload["next_phase"]["live_gate"] == "GMI-LIVE-ACTIVATION"
    assert payload["next_phase"]["required_prior_live_gate"] == "GRO-LIVE-ACTIVATION"
    assert payload["next_phase"]["user_decision_required_now"] is True
    assert payload["activation_packet"]["packet_valid"] is True
    assert payload["activation_packet"]["packet_ready"] is True
    assert payload["activation_packet"]["live_execution_eligible"] is False
    assert payload["activation_packet"]["current_live_blockers"] == [
        "gmi_live_go_not_recorded",
        "gro_live_validation_not_recorded",
    ]
    assert payload["activation_packet"]["single_live_gate"] == "GMI-LIVE-ACTIVATION"
    assert payload["activation_packet"]["safe_default"] == "gemma3_4b_maintenance_runtime_disabled"
    assert payload["completion"]["repository_slices_complete_through_gmi14"] is True
    assert payload["completion"]["repository_slices_complete_through_gmi15"] is True
    assert payload["completion"]["activation_packet_validated"] is True
    assert payload["completion"]["offline_validated"] is True
    assert payload["completion"]["live_validated"] is False
    assert payload["completion"]["product_complete"] is False
