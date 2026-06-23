from plugins.obsidian.backend.orca_core import (
    LEGACY_PROVIDER_ID,
    ORCA_CORE_SCHEMA,
    ORCA_PROVIDER_ID,
    build_orca_lens_contract,
    build_orca_memory_readiness_contract,
    build_orca_query_contract,
    build_orca_raptor_contract,
    build_legacy_obsidian_deprecation_contract,
    decorate_orca_context_payload,
)


def test_orca_context_payload_decorator_preserves_legacy_payload_and_adds_core_contract():
    payload = {
        "snippets": [{"path": "A.md", "text": "synthetic snippet"}],
        "memory": {
            "readiness_gate": {"state": "blocked"},
            "readiness_by_family": {"raptor": {"ready": False}},
            "retrieval_policy": {"filtering_state": "audit_only"},
            "raptor_lineage_flags": {"dirty": True},
            "raptor_write_gate": {"state": "blocked"},
        },
    }

    decorated = decorate_orca_context_payload(payload)

    assert decorated["snippets"] == payload["snippets"]
    assert decorated["provider"]["id"] == ORCA_PROVIDER_ID
    assert decorated["provider"]["legacy_adapter"] == LEGACY_PROVIDER_ID
    assert decorated["orca_core"]["schema"] == ORCA_CORE_SCHEMA
    assert decorated["orca_core"]["read_only"] is True
    assert decorated["orca_core"]["writes_supported"] is False
    assert decorated["orca_core"]["contracts"]["readiness_gate"] == {"state": "blocked"}
    assert decorated["memory"]["namespace"] == "orca"
    assert payload["memory"].get("namespace") is None


def test_orca_memory_readiness_contract_is_read_only_and_uses_orca_namespace(tmp_path):
    contract = build_orca_memory_readiness_contract(
        str(tmp_path),
        status_loader=lambda _vault_dir: {
            "readiness_gate": {"state": "blocked"},
            "readiness_by_family": {"query_layer": {"ready": False}},
            "retrieval_policy": {"filtering_state": "audit_only"},
            "families": {"query_layer": {"enabled": True}},
            "summary": {
                "readiness_state": "blocked",
                "ready_families": 0,
                "readiness_families": 1,
                "blocked_families": ["query_layer"],
                "readiness_gap_names": ["query_index_missing"],
                "filtering_state": "audit_only",
            },
        },
    )

    assert contract["schema"] == ORCA_CORE_SCHEMA
    assert contract["contract"] == "orca_memory_readiness"
    assert contract["namespace"] == "orca"
    assert contract["read_only"] is True
    assert contract["writes_supported"] is False
    assert contract["summary"]["blocked_families"] == ["query_layer"]
    assert contract["legacy_adapter"]["delete_legacy"] is False


def test_orca_raptor_contract_is_bounded_and_does_not_claim_writes(tmp_path):
    contract = build_orca_raptor_contract(
        str(tmp_path),
        status_loader=lambda _vault_dir: {
            "enabled": False,
            "configured": True,
            "readiness": {"state": "dirty", "ready": False},
            "readiness_gate": {"state": "blocked"},
            "lineage_flags": {"dirty": True},
            "write_gate": {"state": "blocked", "writes_supported": False},
            "summary": {"readiness_gap_names": ["source_hash_changed"]},
        },
        graph_loader=lambda _vault_dir, **_kwargs: {
            "node_count": 3,
            "edge_count": 20,
            "returned_edge_count": 5,
            "clipped": True,
            "cursor": {"next_edge_offset": 5},
        },
    )

    assert contract["contract"] == "orca_raptor"
    assert contract["graph"] == {
        "bounded": True,
        "node_count": 3,
        "edge_count": 20,
        "returned_edge_count": 5,
        "clipped": True,
        "cursor": {"next_edge_offset": 5},
    }
    assert contract["write_gate"]["writes_supported"] is False
    assert contract["summary"]["readiness_gap_names"] == ["source_hash_changed"]


def test_orca_query_contract_reports_status_without_executing_query(tmp_path):
    calls = []

    def _status_loader(vault_dir, owner=None):
        calls.append((vault_dir, owner))
        return {
            "readiness": {"state": "not_configured", "ready": False},
            "readiness_gate": {"state": "blocked"},
            "model_router": {"roles": {}},
            "cache": {"entries": 0},
            "summary": {
                "source_count": 0,
                "chunk_count": 0,
                "readiness_state": "not_configured",
                "readiness_gap_names": ["query_index_missing"],
            },
        }

    contract = build_orca_query_contract(str(tmp_path), owner="alice", status_loader=_status_loader)

    assert calls == [(str(tmp_path), "alice")]
    assert contract["contract"] == "orca_query_layer"
    assert "extractive" in contract["answer_modes"]
    assert contract["read_only"] is True
    assert contract["writes_supported"] is False
    assert contract["summary"]["readiness_gap_names"] == ["query_index_missing"]


def test_orca_lens_contract_prefers_orca_routes_and_keeps_legacy_routes_as_compatibility(tmp_path):
    contract = build_orca_lens_contract(
        str(tmp_path),
        baseline_loader=lambda _vault_dir: {
            "systems": {
                "derived_graph": {
                    "node_count": 2,
                    "edge_count": 1,
                    "raptor_node_count": 2,
                    "raptor_edge_count": 1,
                    "clipped": False,
                }
            },
            "readiness_gate": {"state": "blocked"},
            "activation_recommendations": [{"node_id": "derived-graph-edges-live"}],
            "evidence_contract": {"raw_note_bodies_included": False},
            "summary": {
                "readiness_state": "blocked",
                "blocked_families": ["derived_index"],
                "readiness_gap_names": ["derived_index_missing"],
            },
        },
    )

    assert contract["contract"] == "orca_lens"
    assert contract["preferred_routes"]["app"] == "/api/plugins/orca/app"
    assert contract["legacy_routes"]["app"] == "/api/plugins/obsidian/app"
    assert contract["graph"]["bounded"] is True
    assert contract["graph"]["node_count"] == 2
    assert contract["evidence_contract"]["raw_note_bodies_included"] is False
    assert contract["evidence_contract"]["legacy_adapter_delete_required"] is False


def test_legacy_obsidian_deprecation_contract_keeps_compatibility_until_all_gates_are_go():
    contract = build_legacy_obsidian_deprecation_contract()

    assert contract["contract"] == "legacy_obsidian_deprecation"
    assert contract["read_only"] is True
    assert contract["writes_supported"] is False
    assert contract["legacy_surfaces_retained"] is True
    assert contract["removal_allowed"] is False
    assert contract["state"] == "compatibility_retained"
    assert contract["migration_map"]["provider"] == {
        "obsidian.vault_context": "orca.vault_context",
    }
    assert contract["migration_map"]["routes"]["/api/plugins/obsidian/memory/status"] == (
        "/api/plugins/orca/memory/status"
    )
    assert contract["migration_map"]["tools"]["obsidian_graph"] == "orca_graph"
    assert "explicit operator Go" in " ".join(contract["warnings"])


def test_legacy_obsidian_deprecation_contract_requires_explicit_removal_go():
    without_removal_go = build_legacy_obsidian_deprecation_contract(
        ui_lens_redesign_live=True,
        data_path_migration_go=True,
        explicit_removal_go=False,
    )
    with_removal_go = build_legacy_obsidian_deprecation_contract(
        ui_lens_redesign_live=True,
        data_path_migration_go=True,
        explicit_removal_go=True,
    )

    assert without_removal_go["removal_allowed"] is False
    assert without_removal_go["state"] == "compatibility_retained"
    assert with_removal_go["removal_allowed"] is True
    assert with_removal_go["state"] == "removal_ready"
