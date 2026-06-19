import pytest

from src.release_hardening_gates import (
    ReleaseHardeningGate,
    build_release_hardening_index,
)


def test_default_index_keeps_external_release_blocked():
    index = build_release_hardening_index()

    assert index.external_release_ready is False
    assert index.decision == "external_no_go_until_hardening_and_manual_release_evidence_close"
    assert index.blocking_gate_ids == ("large_vault_performance",)
    assert set(index.partial_gate_ids) == {
        "at_rest_security_disclosure",
        "graph_filter_state_isolation",
        "project_apply_conflict_blocking",
        "repository_link_hygiene",
    }


def test_default_index_names_all_required_hardening_gates():
    index = build_release_hardening_index()

    assert [gate.gate_id for gate in index.gates] == [
        "at_rest_security_disclosure",
        "graph_filter_state_isolation",
        "large_vault_performance",
        "project_apply_conflict_blocking",
        "repository_link_hygiene",
    ]
    assert all(gate.evidence_refs for gate in index.gates)
    assert all(gate.missing_evidence for gate in index.gates)
    assert all(gate.recommended_slices for gate in index.gates)


def test_default_index_is_json_compatible_and_operator_readable():
    index = build_release_hardening_index()

    payload = index.to_dict()
    markdown = index.to_markdown()

    assert payload["external_release_ready"] is False
    assert "large_vault_performance" in payload["blocking_gate_ids"]
    assert "# Release Hardening Index" in markdown
    assert "External release ready: `false`" in markdown
    assert "ABC3A-performance-gate" in markdown


def test_non_go_gate_requires_missing_evidence():
    with pytest.raises(ValueError, match="non-go hardening gates"):
        ReleaseHardeningGate(
            gate_id="large_vault_performance",
            status="partial",
            summary="missing threshold",
            evidence_refs=("docs/plans/release-hardening-gates.md",),
            missing_evidence=(),
            recommended_slices=("ABC3A-performance-gate",),
        )


def test_rejects_unknown_gate_or_status():
    with pytest.raises(ValueError, match="unsupported release hardening gate"):
        ReleaseHardeningGate(
            gate_id="unknown",
            status="partial",
            summary="bad gate",
            evidence_refs=("docs/plans/release-hardening-gates.md",),
            missing_evidence=("evidence",),
            recommended_slices=("ABC3X",),
        )

    with pytest.raises(ValueError, match="unsupported release hardening status"):
        ReleaseHardeningGate(
            gate_id="large_vault_performance",
            status="ready",
            summary="bad status",
            evidence_refs=("docs/plans/release-hardening-gates.md",),
            missing_evidence=("evidence",),
            recommended_slices=("ABC3A-performance-gate",),
        )
