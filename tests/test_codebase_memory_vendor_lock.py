from __future__ import annotations

import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "config" / "codebase_memory.lock.json"
AUDIT_PATH = ROOT / "docs" / "plans" / "codebase-memory-vendor-audit.md"


def _lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def test_exact_release_commit_license_and_manifest_are_pinned():
    payload = _lock()
    upstream = payload["upstream"]
    release = upstream["release"]

    assert payload["schema"] == "odysseus.codebase_memory.vendor_lock.v1"
    assert payload["verdict"] == "pinned_for_contract_evaluation_only"
    assert release["version"] == "0.9.0"
    assert release["tag"] == "v0.9.0"
    assert release["commit_sha"] == "b637e3330c96cfe452da623db068c241aaa3ec01"
    assert re.fullmatch(r"[0-9a-f]{40}", release["commit_sha"])
    assert release["commit_signature_status"] == "github_verified"
    assert release["immutable_release"] is True
    assert release["tag"] in release["release_page"]
    assert release["commit_sha"] in release["commit_url"]
    assert "/main" not in json.dumps(release, sort_keys=True)
    assert upstream["license"]["spdx"] == "MIT"
    assert "/v0.9.0/LICENSE" in upstream["license"]["source_url"]
    manifest = upstream["release_manifest"]
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["asset_sha256"])
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["sigstore_bundle_sha256"])


def test_no_artifact_install_build_or_execution_is_claimed():
    payload = _lock()
    distribution = payload["distribution"]
    state = payload["execution_state"]

    assert distribution["artifact_selection"] == "deferred_until_platform_and_execution_approval"
    assert distribution["installer_scripts_allowed"] is False
    assert distribution["package_registry_install_allowed"] is False
    assert distribution["self_update_allowed"] is False
    assert distribution["source_build"]["checkout_performed"] is False
    assert distribution["source_build"]["build_performed"] is False
    assert distribution["verified_release_binary"]["download_performed"] is False
    assert all(value is False for value in state.values())
    assert payload["upstream"]["release_manifest"]["downloaded"] is False
    assert payload["upstream"]["release_manifest"]["artifact_selected"] is False


def test_tool_surface_inconsistency_is_explicit_and_fail_closed():
    freeze = _lock()["capability_freeze"]

    assert freeze["advertised_mcp_tool_count"] == 15
    assert freeze["readme_table_tool_count"] == 14
    assert len(freeze["readme_table_tools"]) == 14
    assert set(freeze["additional_named_tools_outside_table"]) == {
        "check_index_coverage",
        "semantic_query",
    }
    assert freeze["documentation_surface_inconsistent"] is True
    assert freeze["exact_runtime_tool_surface"] == "unverified_until_pinned_artifact_protocol_probe"
    assert set(freeze["mutating_or_stateful_tools"]) == {
        "delete_project",
        "index_repository",
        "ingest_traces",
        "manage_adr",
    }
    assert freeze["direct_upstream_tool_exposure_allowed"] is False
    assert freeze["semantic_embedding_model_allowed"] is False
    assert freeze["cypher_write_clauses_allowed"] is False


def test_runtime_network_filesystem_and_watcher_surfaces_default_off():
    payload = _lock()
    runtime = payload["runtime_surfaces"]
    filesystem = payload["filesystem_surfaces"]

    assert runtime["transport"] == "stdio"
    assert runtime["optional_ui"] == {
        "variant": "separate embedded-UI binary",
        "bind": "127.0.0.1",
        "default_port": 9749,
        "allowed": False,
    }
    assert runtime["watcher"]["upstream_auto_watch_default"] is True
    assert runtime["watcher"]["allowed"] is False
    assert runtime["auto_index"]["allowed"] is False
    assert runtime["update_check"]["documented_disable_switch_found"] is False
    assert runtime["update_check"]["allowed"] is False
    assert runtime["update_check"]["destination"].startswith("https://api.github.com/")
    for key in (
        "host_absolute_paths_in_api_allowed",
        "repo_config_writes_allowed",
        "agent_config_writes_allowed",
        "hook_writes_allowed",
        "shared_graph_export_allowed",
        "diagnostics_files_allowed",
    ):
        assert filesystem[key] is False


def test_every_activation_or_mutation_default_is_false_and_ownership_is_preserved():
    defaults = _lock()["odysseus_defaults"]
    boolean_defaults = {key: value for key, value in defaults.items() if isinstance(value, bool)}

    assert boolean_defaults
    assert all(value is False for value in boolean_defaults.values())
    assert defaults["source_truth_owner"] == "USI"
    assert defaults["repository_truth_owner"] == "Repo Registry"
    assert defaults["history_truth_owner"] == "Git and Project Version Store"
    assert defaults["tool_surface_owner"] == "TAX"
    assert defaults["metrics_owner"] == "GRO"
    assert defaults["ui_owner"] == "Lens Code Graph"
    gate = _lock()["activation_gate"]
    assert gate == {
        "id": "CBM-LIVE-ACTIVATION",
        "status": "dormant",
        "authorized": False,
        "productive_process_allowed": False,
        "productive_projection_allowed": False,
    }


def test_risk_register_covers_required_supply_chain_and_runtime_threats():
    risks = {item["id"]: item for item in _lock()["risk_register"]}

    assert set(risks) == {f"CBM-R0{index}" for index in range(1, 8)}
    assert risks["CBM-R01"]["severity"] == "critical"
    assert risks["CBM-R02"]["severity"] == "high"
    encoded = json.dumps(risks, sort_keys=True).lower()
    for required in (
        "installer",
        "egress",
        "watcher",
        "shared graph",
        "documentation",
        "paper",
        "sbom",
    ):
        assert required in encoded


def test_paper_scope_does_not_overclaim_locked_release():
    baseline = _lock()["research_baseline"]

    assert baseline["paper"] == "arXiv:2603.27277v1"
    assert baseline["paper_tested_release"] == "v0.5.5"
    assert baseline["locked_release"] == "v0.9.0"
    assert baseline["locked_release_features_inherited_from_paper"] is False
    assert baseline["current_feature_claims_require_local_validation"] is True


def test_audit_is_primary_source_backed_and_contains_no_private_or_live_claim():
    text = AUDIT_PATH.read_text(encoding="utf-8")
    lower = text.lower()

    assert "b637e3330c96cfe452da623db068c241aaa3ec01" in text
    assert "https://github.com/DeusData/codebase-memory-mcp/releases/tag/v0.9.0" in text
    assert "https://github.com/DeusData/codebase-memory-mcp/blob/v0.9.0/LICENSE" in text
    assert "CBM-R01 critical" in text
    assert "CBM-R07 medium" in text
    assert "no artifact acquired" in text
    assert "CBM-LIVE-ACTIVATION" in text
    assert "no process" in lower
    forbidden = ("owner_id", "user_id", "chat_id", "api key", "access token")
    assert all(fragment not in lower for fragment in forbidden)
