from dataclasses import replace

import pytest

from src.unified_source_index_contract import Classification, TextRangeLocator, content_hash
from src.unified_source_index_legacy_comparison import (
    LegacyComparisonError,
    LegacyLane,
    MigrationDecision,
    compare_legacy_sources,
    run_synthetic_comparison,
    synthetic_comparison_fixture,
)


def _lane(report, lane):
    return next(item for item in report.lanes if item.plan.lane is lane)


def test_complete_fixture_has_explicit_decisions_and_all_measurable_gates_ready():
    report = run_synthetic_comparison("complete")

    assert report.all_gates_ready is True
    assert {item.plan.lane: item.plan.decision for item in report.lanes} == {
        LegacyLane.PERSONAL_DOCS: MigrationDecision.ADAPT,
        LegacyLane.CURRENT_RAG: MigrationDecision.ADAPT,
        LegacyLane.MEMORY: MigrationDecision.KEEP,
        LegacyLane.OBSIDIAN_LENS: MigrationDecision.RETIRE,
    }
    for lane in report.lanes:
        assert lane.legacy_count == 1
        assert lane.unified_count == 1
        assert lane.matched_count == 1
        assert lane.coverage_ratio == 1.0
        assert lane.locator_parity == 1.0
        assert lane.policy_parity == 1.0
        assert lane.content_hash_parity == 1.0
        assert lane.gate_failures == ()
        assert lane.cutover_ready is True


def test_missing_fixture_names_only_content_free_correlation_and_fails_rag_gate():
    report = run_synthetic_comparison("missing")
    rag = _lane(report, LegacyLane.CURRENT_RAG)

    assert report.all_gates_ready is False
    assert rag.missing_in_unified == ("fixture.current_rag.001",)
    assert rag.coverage_ratio == 0.0
    assert set(rag.gate_failures) == {
        "coverage_ratio",
        "locator_parity",
        "policy_parity",
        "content_hash_parity",
    }
    assert all(
        item.cutover_ready
        for item in report.lanes
        if item.plan.lane is not LegacyLane.CURRENT_RAG
    )


def test_locator_and_policy_mismatches_are_separate_cutover_failures():
    locator_report = run_synthetic_comparison("locator_mismatch")
    policy_report = run_synthetic_comparison("policy_mismatch")
    personal = _lane(locator_report, LegacyLane.PERSONAL_DOCS)
    memory = _lane(policy_report, LegacyLane.MEMORY)

    assert personal.locator_mismatches == ("fixture.personal_docs.001",)
    assert personal.gate_failures == ("locator_parity",)
    assert personal.policy_parity == 1.0
    assert memory.policy_mismatches == ("fixture.memory.001",)
    assert memory.gate_failures == ("policy_parity",)
    assert memory.locator_parity == 1.0


def test_content_hash_mismatch_does_not_get_hidden_by_locator_match():
    fixture = synthetic_comparison_fixture("complete")
    legacy = list(fixture.legacy)
    index = next(i for i, item in enumerate(legacy) if item.lane is LegacyLane.CURRENT_RAG)
    legacy[index] = replace(legacy[index], content_hash=content_hash("different fixture"))

    report = compare_legacy_sources(
        tuple(legacy),
        fixture.unified,
        fixture_profile="hash_mismatch",
    )
    rag = _lane(report, LegacyLane.CURRENT_RAG)

    assert rag.locator_parity == 1.0
    assert rag.policy_parity == 1.0
    assert rag.content_hash_parity == 0.0
    assert rag.gate_failures == ("content_hash_parity",)


def test_policy_parity_includes_owner_classification_and_content_policy():
    fixture = synthetic_comparison_fixture("complete")
    legacy = list(fixture.legacy)
    index = next(i for i, item in enumerate(legacy) if item.lane is LegacyLane.MEMORY)
    legacy[index] = replace(legacy[index], classification=Classification.PUBLIC)

    report = compare_legacy_sources(
        tuple(legacy),
        fixture.unified,
        fixture_profile="policy_check",
    )

    assert _lane(report, LegacyLane.MEMORY).policy_parity == 0.0


def test_report_is_content_free_and_cannot_authorize_live_cutover():
    report = run_synthetic_comparison("complete")
    payload = report.to_dict()
    encoded = report.to_json()

    assert payload["synthetic_evidence"] is True
    assert payload["private_corpus_accessed"] is False
    assert payload["shadow_requests_sent"] is False
    assert payload["dual_write_performed"] is False
    assert payload["active_path_modified"] is False
    assert payload["live_cutover_authorized"] is False
    assert "synthetic-personal_docs-fixture" not in encoded
    assert "synthetic-memory-fixture" not in encoded


def test_duplicate_correlations_and_untyped_locators_fail_closed():
    fixture = synthetic_comparison_fixture("complete")

    with pytest.raises(LegacyComparisonError, match="duplicate correlations"):
        compare_legacy_sources(
            fixture.legacy + (fixture.legacy[0],),
            fixture.unified,
            fixture_profile="duplicates",
        )
    with pytest.raises(LegacyComparisonError, match="legacy locator"):
        replace(fixture.legacy[0], locator={"start": 0, "end": 1})


def test_empty_lane_fails_minimum_count_and_parity_gates():
    fixture = synthetic_comparison_fixture("complete")
    legacy = tuple(item for item in fixture.legacy if item.lane is not LegacyLane.OBSIDIAN_LENS)
    unified = tuple(item for item in fixture.unified if item.lane is not LegacyLane.OBSIDIAN_LENS)

    report = compare_legacy_sources(legacy, unified, fixture_profile="empty_lane")
    lane = _lane(report, LegacyLane.OBSIDIAN_LENS)

    assert lane.cutover_ready is False
    assert set(lane.gate_failures) == {
        "minimum_legacy_records",
        "coverage_ratio",
        "locator_parity",
        "policy_parity",
        "content_hash_parity",
    }


def test_comparison_identity_is_deterministic_for_same_fixture():
    first = run_synthetic_comparison("complete")
    second = run_synthetic_comparison("complete")

    assert first == second
    assert first.comparison_id == second.comparison_id
