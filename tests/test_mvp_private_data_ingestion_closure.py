from src.mvp_private_data_ingestion_closure import (
    PrivateDataIngestionGate,
    build_private_data_ingestion_report,
)


def test_default_private_data_ingestion_progress_matches_canonical_done_nodes():
    report = build_private_data_ingestion_report()

    assert report.roadmap_id == "private_data_nextcloud_memory_ingestion"
    assert report.percent_complete == 50
    assert "Nextcloud transfer readiness" in report.why_not_100
    assert "Nextcloud transfer readiness" in report.recommended_next_human_decision

    gates = {gate.gate_id: gate for gate in report.gates}
    assert gates["planning_sources_inventory"].status == "go"
    assert gates["planning_sources_ingest"].status == "go"
    assert gates["bigdata_ledger_contract"].status == "go"
    assert gates["nextcloud_transfer_readiness"].status == "repo_open"
    assert gates["resumable_transfer_tooling"].status == "go"
    assert gates["resumable_scanner_dry_run"].status == "go"
    assert gates["chunked_extraction_lanes"].status == "go"
    assert gates["live_small_batch_transfer"].slice_class == "needs_live_go"
    assert gates["ingestion_dashboard_live"].slice_class == "needs_design"


def test_private_data_ingestion_reaches_100_when_all_gates_are_complete():
    report = build_private_data_ingestion_report(
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
        nextcloud_transfer_readiness_go=True,
        resumable_scanner_dry_run_go=True,
        chunked_extraction_lanes_go=True,
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
