import pytest

from src.mvp_private_data_ingestion_closure import (
    PrivateDataIngestionGate,
    build_private_data_ingestion_report,
)


class _HostileControlBaseException(BaseException):
    pass


class _HostileControl:
    def __init__(self, error_type):
        self.error_type = error_type
        self.callbacks = []

    def __bool__(self):
        self.callbacks.append("bool")
        raise self.error_type("UNTRUSTED-CONTROL-CONTENT")

    def __str__(self):
        self.callbacks.append("str")
        raise _HostileControlBaseException("UNTRUSTED-CONTROL-CONTENT")

    def __repr__(self):
        self.callbacks.append("repr")
        raise _HostileControlBaseException("UNTRUSTED-CONTROL-CONTENT")


def test_default_private_data_ingestion_progress_matches_current_canonical_foundations():
    report = build_private_data_ingestion_report()

    assert report.roadmap_id == "private_data_nextcloud_memory_ingestion"
    assert report.percent_complete == 17

    assert tuple(gate.gate_id for gate in report.gates) == (
        "planning-sources-memory-inventory",
        "planning-sources-memory-ingest-live",
        "bigdata-ledger-contract",
        "nextcloud-transfer-readiness",
        "resumable-transfer-tooling",
        "resumable-scanner-dry-run",
        "live-small-batch-transfer",
        "chunked-extraction-lanes",
        "memory-abstraction-ingest-live",
        "full-transfer-live",
        "full-corpus-analysis-live",
        "ingestion-dashboard-live",
    )
    assert {gate.gate_id for gate in report.gates if gate.status == "go"} == {
        "planning-sources-memory-inventory",
        "bigdata-ledger-contract",
    }


def test_deferred_required_units_remain_incomplete():
    report = build_private_data_ingestion_report()
    gates = {gate.gate_id: gate for gate in report.gates}

    assert gates["full-transfer-live"].status == "deferred"
    assert gates["full-corpus-analysis-live"].status == "deferred"
    assert gates["ingestion-dashboard-live"].status == "deferred"
    assert all(not gate.complete for gate in gates.values() if gate.status != "go")
    assert report.percent_complete == 17


def test_backend_safe_repo_work_is_recommended_before_live_gates():
    report = build_private_data_ingestion_report()
    gates = {gate.gate_id: gate for gate in report.gates}

    assert gates["planning-sources-memory-ingest-live"].status == "repo_open"
    assert "Planning sources memory ingest live" in report.why_not_100
    assert "backend-safe" in report.recommended_next_human_decision
    assert "planning-source memory ingest" in report.recommended_next_human_decision


def test_private_data_ingestion_reaches_100_when_all_gates_are_go():
    report = build_private_data_ingestion_report(
        planning_sources_ingest_go=True,
        nextcloud_transfer_readiness_go=True,
        resumable_transfer_tooling_go=True,
        resumable_scanner_dry_run_go=True,
        live_small_batch_transfer_go=True,
        chunked_extraction_lanes_go=True,
        memory_abstraction_ingest_live_go=True,
        full_transfer_live_go=True,
        full_corpus_analysis_live_go=True,
        ingestion_dashboard_live_go=True,
    )

    assert report.percent_complete == 100
    assert report.why_not_100 == "-"
    assert "System Health Checker Host-Agent" in report.recommended_next_human_decision
    assert report.to_markdown_row() == "| 3 | Private Data / Nextcloud Memory Ingestion | 100 | - |"


def test_private_data_ingestion_live_gate_is_recommended_after_repo_gates():
    report = build_private_data_ingestion_report(
        planning_sources_ingest_go=True,
        nextcloud_transfer_readiness_go=True,
        resumable_transfer_tooling_go=True,
        resumable_scanner_dry_run_go=True,
        live_small_batch_transfer_go=False,
        chunked_extraction_lanes_go=True,
        memory_abstraction_ingest_live_go=False,
        full_transfer_deferred=False,
        full_corpus_analysis_deferred=False,
        ingestion_dashboard_deferred=False,
    )

    assert report.percent_complete == 58
    assert "Live small-batch transfer" in report.why_not_100
    assert "Grant or defer" in report.recommended_next_human_decision


def test_private_data_ingestion_gate_validation_rejects_unknown_values():
    try:
        PrivateDataIngestionGate.create(
            gate_id="bad",
            title="Bad",
            status="maybe",
            slice_class="repo_only",
            reason="invalid status",
        )
    except ValueError as exc:
        assert "unsupported private data closure gate status" in str(exc)
    else:
        raise AssertionError("unknown status should fail closed")

    try:
        PrivateDataIngestionGate.create(
            gate_id="bad",
            title="Bad",
            status="go",
            slice_class="just_do_it_live",
            reason="invalid class",
        )
    except ValueError as exc:
        assert "unsupported private data closure slice class" in str(exc)
    else:
        raise AssertionError("unknown slice class should fail closed")


def test_private_data_ingestion_bool_inputs_are_exact_and_content_free():
    control_names = (
        "planning_sources_inventory_go",
        "planning_sources_ingest_go",
        "bigdata_ledger_contract_go",
        "nextcloud_transfer_readiness_go",
        "resumable_transfer_tooling_go",
        "resumable_scanner_dry_run_go",
        "live_small_batch_transfer_go",
        "chunked_extraction_lanes_go",
        "memory_abstraction_ingest_live_go",
        "full_transfer_live_go",
        "full_transfer_deferred",
        "full_corpus_analysis_live_go",
        "full_corpus_analysis_deferred",
        "ingestion_dashboard_live_go",
        "ingestion_dashboard_deferred",
    )
    expected_error = "private data ingestion controls must be exact booleans"

    for control_name in control_names:
        hostile_values = (
            "UNTRUSTED-CONTROL-CONTENT",
            1,
            _HostileControl(RuntimeError),
            _HostileControl(_HostileControlBaseException),
        )
        for hostile_value in hostile_values:
            with pytest.raises(ValueError) as exc_info:
                build_private_data_ingestion_report(**{control_name: hostile_value})

            assert str(exc_info.value) == expected_error
            assert exc_info.value.__cause__ is None
            assert exc_info.value.__suppress_context__ is True
            assert "UNTRUSTED-CONTROL-CONTENT" not in str(exc_info.value)
            if isinstance(hostile_value, _HostileControl):
                assert hostile_value.callbacks == []
