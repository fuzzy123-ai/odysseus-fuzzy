from src.plugin_release_gate import PluginReleaseGate
from src.release_readiness_pipeline import (
    build_current_release_readiness_pipeline,
    current_automated_release_gates,
)


def test_current_pipeline_is_external_go_and_routes_final_review():
    snapshot = build_current_release_readiness_pipeline()

    assert snapshot.external_release_go is True
    assert snapshot.report.status == "go"
    assert snapshot.report.blocking_reasons == ()
    assert [item.slice_id for item in snapshot.followup_slices] == [
        "REL-final-external-review",
    ]
    assert snapshot.followup_matrix.parallel_batch_ids == ()
    assert snapshot.followup_matrix.sequential_gate_ids == (
        "REL-final-external-review",
    )


def test_current_pipeline_accepts_plugin_gate_and_routes_plugin_blocker():
    plugin_gate = PluginReleaseGate(
        ok=False,
        registry_ok=True,
        local_plugins_ok=False,
        registry_plugin_count=3,
        local_plugin_count=2,
        errors=("local:bad:missing_manifest",),
    )

    snapshot = build_current_release_readiness_pipeline(plugin_gate)

    assert "plugin:local:bad:missing_manifest" in snapshot.report.blocking_reasons
    assert "REL-plugin-release-gate-fix" in {item.slice_id for item in snapshot.followup_slices}


def test_current_automated_release_gates_are_all_automated_and_green():
    gates = current_automated_release_gates()

    assert len(gates) == 3
    assert {gate.kind for gate in gates} == {"automated"}
    assert {gate.status for gate in gates} == {"pass"}
    assert {gate.risk for gate in gates} == {"documented_baseline_not_fresh_measurement"}
    assert {gate.evidence_refs for gate in gates} == {("REL1 documented baseline evidence",)}


def test_pipeline_to_dict_is_stable_shape():
    snapshot = build_current_release_readiness_pipeline()
    payload = snapshot.to_dict()

    assert payload["report"]["external_release_go"] is True
    assert payload["report"]["status"] == "go"
    assert payload["automated_gate_evidence_mode"] == "documented_baseline"
    assert payload["automated_gate_is_live_measurement"] is False
    assert payload["automated_gate_summary"]["status"] == "baseline_evidence_green"
    assert "not fresh live measurements" in payload["automated_gate_summary"]["operator_interpretation"]
    assert payload["followup_slices"][0]["slice_id"] == "REL-final-external-review"
    assert payload["followup_slices"][0]["owner"] == "Charlie"
    assert payload["followup_matrix"]["parallel_batch_ids"] == ()


def test_manual_gate_closeout_is_green_with_baseline_language():
    snapshot = build_current_release_readiness_pipeline()

    assert snapshot.report.blocking_reasons == ()
    assert snapshot.external_release_go is True
    assert snapshot.automated_gate_is_live_measurement is False
