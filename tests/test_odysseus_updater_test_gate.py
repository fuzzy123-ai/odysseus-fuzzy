from pathlib import Path

import pytest

from src.odysseus_updater_test_gate import build_odysseus_updater_test_gate


def _allowed_suites():
    return (
        {
            "suite_id": "unit_fast",
            "required": True,
            "timeout_seconds": 120,
            "summary": "Fast offline unit coverage for updater decision logic.",
        },
        {
            "suite_id": "contract_snapshot",
            "required": True,
            "timeout_seconds": 180,
            "summary": "Bounded snapshot validation for updater-facing contracts.",
        },
        {
            "suite_id": "smoke_optional",
            "required": False,
            "timeout_seconds": 60,
            "summary": "Optional smoke-style review snapshot for operator confidence.",
        },
    )


def _passing_snapshots():
    return (
        {
            "suite_id": "unit_fast",
            "execution_status": "completed",
            "result_label": "pass",
            "summary": "Unit-fast snapshot is green in the offline gate record.",
            "observed_duration_seconds": 34,
        },
        {
            "suite_id": "contract_snapshot",
            "execution_status": "completed",
            "result_label": "pass",
            "summary": "Contract snapshot is green in the offline gate record.",
            "observed_duration_seconds": 55,
        },
        {
            "suite_id": "smoke_optional",
            "execution_status": "completed",
            "result_label": "pass",
            "summary": "Optional smoke snapshot is green in the offline gate record.",
            "observed_duration_seconds": 18,
        },
    )


def test_ready_and_go_when_all_allowed_suites_pass():
    report = build_odysseus_updater_test_gate(
        allowed_suites=_allowed_suites(),
        result_snapshots=_passing_snapshots(),
    )

    assert report.status == "ready"
    assert report.decision == "go"
    assert report.blocked_execution_plans == ()
    assert report.to_compact_report() == {
        "status": "ready",
        "decision": "go",
        "required_suite_ids": ["contract_snapshot", "unit_fast"],
        "optional_suite_ids": ["smoke_optional"],
        "result_labels": {
            "contract_snapshot": "pass",
            "smoke_optional": "pass",
            "unit_fast": "pass",
        },
        "execution_statuses": {
            "contract_snapshot": "completed",
            "smoke_optional": "completed",
            "unit_fast": "completed",
        },
        "blocked_execution_plan_ids": [],
    }


def test_partial_and_deferred_when_optional_suite_is_missing():
    report = build_odysseus_updater_test_gate(
        allowed_suites=_allowed_suites(),
        result_snapshots=_passing_snapshots()[:2],
    )

    assert report.status == "partial"
    assert report.decision == "deferred"
    smoke_result = next(result for result in report.results if result.suite_id == "smoke_optional")
    assert smoke_result.result_label == "missing"


def test_deferred_when_required_suite_is_pending():
    report = build_odysseus_updater_test_gate(
        allowed_suites=_allowed_suites(),
        result_snapshots=(
            {
                "suite_id": "unit_fast",
                "execution_status": "pending",
                "result_label": "pending",
                "summary": "Unit-fast snapshot is still pending offline review.",
            },
            _passing_snapshots()[1],
            _passing_snapshots()[2],
        ),
    )

    assert report.status == "deferred"
    assert report.decision == "deferred"
    assert any("unit_fast" in reason for reason in report.reasons)


def test_blocked_and_no_go_when_required_suite_fails():
    report = build_odysseus_updater_test_gate(
        allowed_suites=_allowed_suites(),
        result_snapshots=(
            {
                "suite_id": "unit_fast",
                "execution_status": "completed",
                "result_label": "fail",
                "summary": "Unit-fast snapshot recorded a failing offline result.",
                "observed_duration_seconds": 42,
            },
            _passing_snapshots()[1],
            _passing_snapshots()[2],
        ),
    )

    assert report.status == "blocked"
    assert report.decision == "no_go"
    assert report.blocked_execution_plans == ()


def test_unknown_suite_id_becomes_blocked_execution_plan():
    report = build_odysseus_updater_test_gate(
        allowed_suites=_allowed_suites(),
        result_snapshots=(
            _passing_snapshots()[0],
            _passing_snapshots()[1],
            {
                "suite_id": "network_probe",
                "execution_status": "completed",
                "result_label": "pass",
                "summary": "This suite should be rejected because it is out of scope.",
            },
        ),
    )

    assert report.status == "blocked"
    assert report.decision == "no_go"
    assert report.blocked_execution_plans[0].to_dict() == {
        "suite_id": "network_probe",
        "source": "policy",
        "reason": "test suite is not part of the allowed offline updater gate scope",
    }


def test_blocked_snapshot_produces_blocked_execution_plan():
    report = build_odysseus_updater_test_gate(
        allowed_suites=_allowed_suites(),
        result_snapshots=(
            _passing_snapshots()[0],
            {
                "suite_id": "contract_snapshot",
                "execution_status": "blocked",
                "result_label": "blocked",
                "summary": "Contract snapshot is paused behind an offline review blocker.",
                "blocked_reason": "contract snapshot input is incomplete and cannot be promoted",
            },
            _passing_snapshots()[2],
        ),
    )

    assert report.status == "blocked"
    assert report.decision == "no_go"
    assert report.blocked_execution_plans[0].suite_id == "contract_snapshot"
    assert report.blocked_execution_plans[0].source == "snapshot"


def test_rejects_duplicate_allowed_suite_ids():
    with pytest.raises(ValueError, match="duplicate suite_id: unit_fast"):
        build_odysseus_updater_test_gate(
            allowed_suites=(
                _allowed_suites()[0],
                _allowed_suites()[0],
            ),
            result_snapshots=(),
        )


def test_rejects_duplicate_snapshot_suite_ids():
    with pytest.raises(ValueError, match="duplicate suite_id in result_snapshots: unit_fast"):
        build_odysseus_updater_test_gate(
            allowed_suites=_allowed_suites(),
            result_snapshots=(
                _passing_snapshots()[0],
                _passing_snapshots()[0],
            ),
        )


def test_rejects_invalid_timeout_budget():
    with pytest.raises(ValueError, match="timeout_seconds must be between 1 and 7200"):
        build_odysseus_updater_test_gate(
            allowed_suites=(
                {
                    "suite_id": "unit_fast",
                    "required": True,
                    "timeout_seconds": 0,
                    "summary": "invalid timeout budget",
                },
            ),
            result_snapshots=(),
        )


def test_module_source_stays_offline_and_runtime_free():
    source = Path("src/odysseus_updater_test_gate.py").read_text(encoding="utf-8")

    forbidden_fragments = (
        "import subprocess",
        "from subprocess",
        "import requests",
        "from requests",
        "import telegram",
        "from telegram",
        "import nextcloud",
        "from nextcloud",
        "import git",
        "from git",
        ".run(",
        "os.system",
    )

    for fragment in forbidden_fragments:
        assert fragment not in source
