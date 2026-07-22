from __future__ import annotations

from pathlib import Path

import pytest

from scripts.run_telegram_todo_incident_regression import (
    IncidentManifestError,
    load_manifest,
    offline_test_environment,
    run_manifest_suite,
    validate_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "plans" / "telegram-todo-incident-regression-manifest.json"
REQUIRED_CASE_IDS = {
    "single_todo_create",
    "two_todos_one_message",
    "complete_reopen_remove",
    "casing_and_typo_routing",
    "ambiguous_match_no_mutation",
    "memory_task_domain_block",
    "missing_or_wrong_receipt",
    "digest_include_exclude",
    "telegram_tool_event_survival",
    "long_bounded_context",
    "rollover_restart_dst_parallel",
    "single_boundary_followup_continuity",
    "parallel_mutation_no_lost_update",
    "history_metadata_truthful",
}


def test_manifest_covers_every_required_incident_case_with_real_nodes():
    manifest = load_manifest(MANIFEST)
    nodeids = validate_manifest(manifest, root=ROOT)

    assert {case["id"] for case in manifest["required_cases"]} == REQUIRED_CASE_IDS
    assert len(nodeids) >= len(REQUIRED_CASE_IDS)
    assert all(nodeid.startswith("tests/") for nodeid in nodeids)
    assert len(nodeids) == len(set(nodeids))


def test_manifest_is_synthetic_offline_and_live_default_off():
    manifest = load_manifest(MANIFEST)
    execution = manifest["execution"]
    environment = offline_test_environment({
        "OPENAI_API_KEY": "must-be-removed",
        "TELEGRAM_BOT_TOKEN": "must-be-removed",
        "SAFE_VALUE": "preserved",
    })

    assert execution == {
        "network": "forbidden",
        "production_data": "forbidden",
        "live_actions": False,
        "synthetic_data_only": True,
        "runner": "scripts/run_telegram_todo_incident_regression.py",
    }
    assert "OPENAI_API_KEY" not in environment
    assert "TELEGRAM_BOT_TOKEN" not in environment
    assert environment["TELEGRAM_REPLY_ENABLED"] == "false"
    assert environment["TELEGRAM_POLLING_ENABLED"] == "false"
    assert environment["TELEGRAM_SESSION_ROLLOVER_ENABLED"] == "false"
    assert environment["SAFE_VALUE"] == "preserved"


def test_manifest_rejects_live_or_missing_test_targets():
    manifest = load_manifest(MANIFEST)
    manifest["execution"]["live_actions"] = True
    with pytest.raises(IncidentManifestError, match="live actions"):
        validate_manifest(manifest, root=ROOT)

    manifest = load_manifest(MANIFEST)
    manifest["required_cases"][0]["nodeids"] = [
        "tests/test_manage_todos.py::test_missing_incident_node"
    ]
    with pytest.raises(IncidentManifestError, match="pytest node is missing"):
        validate_manifest(manifest, root=ROOT)


def test_runner_rejects_repository_basetemp_before_pytest_can_delete_it():
    unsafe_target = ROOT / ".ttd09-unsafe-basetemp-must-not-exist"

    assert not unsafe_target.exists()
    with pytest.raises(IncidentManifestError, match="outside the repository"):
        run_manifest_suite(MANIFEST, basetemp=unsafe_target)
    assert not unsafe_target.exists()
