from src.plugin_release_gate import PluginReleaseGate
from src.release_readiness_pipeline import (
    build_current_release_readiness_pipeline,
    current_automated_release_gates,
)


def test_current_pipeline_preserves_external_no_go_and_routes_followups():
    snapshot = build_current_release_readiness_pipeline()

    assert snapshot.external_release_go is False
    assert snapshot.report.status == "blocked"
    assert snapshot.report.blocking_reasons == (
        "manual:partial:provider-proof",
        "manual:partial:export-import-rebuild",
    )
    assert [item.slice_id for item in snapshot.followup_slices] == [
        "REL-provider-proof-evidence",
        "REL-test-vault-rebuild-evidence",
        "REL-partial-manual-evidence-closeout",
    ]
    assert snapshot.followup_matrix.parallel_batch_ids == ("REL-test-vault-rebuild-evidence",)
    assert snapshot.followup_matrix.sequential_gate_ids == (
        "REL-provider-proof-evidence",
        "REL-partial-manual-evidence-closeout",
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


def test_pipeline_to_dict_is_stable_shape():
    snapshot = build_current_release_readiness_pipeline()
    payload = snapshot.to_dict()

    assert payload["report"]["external_release_go"] is False
    assert payload["report"]["status"] == "blocked"
    assert payload["followup_slices"][0]["slice_id"] == "REL-provider-proof-evidence"
    assert payload["followup_slices"][1]["owner"] == "Alice"
    assert payload["followup_matrix"]["parallel_batch_ids"] == ("REL-test-vault-rebuild-evidence",)
