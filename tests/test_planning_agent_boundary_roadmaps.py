from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MASTER_PATH = ROOT / "docs/plans/harbor-planning-integration-master-roadmap.json"
PDE_PATH = ROOT / "docs/plans/planning-definition-editor-roadmap.json"
TLR_PATH = ROOT / "docs/plans/temporal-light-agent-execution-roadmap.json"
PMCP_PATH = ROOT / "docs/plans/planning-mcp-roadmap.json"
HWA_PATH = ROOT / "docs/plans/headless-write-agent-orchestration-roadmap.md"

REQUIRED_SLICE_FIELDS = {
    "id",
    "status",
    "class",
    "owner",
    "depends_on",
    "allowed_paths",
    "hotfiles",
    "objective",
    "deliverables",
    "tests",
    "evidence_required",
    "gate",
    "done_when",
    "stop_rules",
    "forbidden_actions",
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_slice_contract(roadmap: dict, prefix: str) -> set[str]:
    slices = roadmap["slice_queue"]
    ids = [item["id"] for item in slices]
    assert ids, f"{prefix} queue must not be empty"
    assert len(ids) == len(set(ids)), f"{prefix} slice ids must be unique"

    known = set(ids)
    for item in slices:
        missing = REQUIRED_SLICE_FIELDS - item.keys()
        assert not missing, f"{item['id']} missing fields: {sorted(missing)}"
        assert item["id"].startswith(f"{prefix}-")
        assert isinstance(item["depends_on"], list)
        assert item["allowed_paths"]
        assert isinstance(item["hotfiles"], list)
        assert item["objective"].strip()
        assert item["deliverables"]
        assert item["tests"]
        assert item["evidence_required"]
        assert item["gate"]["id"]
        assert item["gate"]["safe_default"]
        assert item["done_when"].startswith("PASS only if")
        assert item["stop_rules"]
        assert item["forbidden_actions"]
        for dependency in item["depends_on"]:
            assert dependency in known, (
                f"{item['id']} has unresolved local dependency {dependency}"
            )

    _assert_acyclic(slices)
    return known


def _assert_acyclic(slices: list[dict]) -> None:
    graph = {item["id"]: tuple(item["depends_on"]) for item in slices}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        assert node not in visiting, f"cycle detected at {node}"
        if node in visited:
            return
        visiting.add(node)
        for dependency in graph[node]:
            visit(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)


def _assert_gate_targets_resolve(roadmap: dict, known: set[str], prefix: str) -> None:
    for gate in roadmap["gate_queue"]:
        for target in gate.get("blocks", []):
            if isinstance(target, str) and target.startswith(f"{prefix}-"):
                assert target in known, f"{gate['id']} blocks unknown slice {target}"
        for dependency in gate.get("depends_on", []):
            if isinstance(dependency, str) and dependency.startswith(f"{prefix}-"):
                assert dependency in known, (
                    f"{gate['id']} depends on unknown slice {dependency}"
                )


def test_master_attaches_definition_and_execution_children() -> None:
    master = _load(MASTER_PATH)
    pde = _load(PDE_PATH)
    tlr = _load(TLR_PATH)

    children = {item["id"]: item for item in master["sub_roadmaps"]}
    assert children["HPIM-PDE"]["roadmap"] == PDE_PATH.relative_to(ROOT).as_posix()
    assert children["HPIM-TLR"]["roadmap"] == TLR_PATH.relative_to(ROOT).as_posix()
    assert children["HPIM-TLR"]["depends_on"] == ["HPIM-PDE", "HPIM-HWA"]
    assert children["HPIM-UI"]["class"] == "reference_only"
    assert children["HPIM-UI"]["status"] == "prototype_reference_only"
    assert pde["master_roadmap"] == MASTER_PATH.relative_to(ROOT).as_posix()
    assert tlr["master_roadmap"] == MASTER_PATH.relative_to(ROOT).as_posix()

    for child in master["sub_roadmaps"]:
        assert (ROOT / child["roadmap"]).is_file(), child["roadmap"]


def test_child_slice_contracts_are_complete_resolved_and_acyclic() -> None:
    pde = _load(PDE_PATH)
    tlr = _load(TLR_PATH)

    pde_ids = _assert_slice_contract(pde, "PDE")
    tlr_ids = _assert_slice_contract(tlr, "TLR")
    _assert_gate_targets_resolve(pde, pde_ids, "PDE")
    _assert_gate_targets_resolve(tlr, tlr_ids, "TLR")

    contract_gate = next(
        gate
        for gate in tlr["gate_queue"]
        if gate["id"] == "TEMPORAL-LIGHT-RUNTIME-CONTRACT-READY"
    )
    assert contract_gate["class"] == "contract_gate"
    assert contract_gate["status"] == "satisfied_TLR_02_through_TLR_06_green"
    assert "TLR-02-abc-manifest-and-run-start" not in contract_gate["blocks"]
    assert set(contract_gate["depends_on"]) == {
        "TLR-02-abc-manifest-and-run-start",
        "TLR-03-deterministic-workflow",
        "TLR-04-activities-claims-heartbeats",
        "TLR-05-signals-updates-idempotency",
    }


def test_planning_contract_is_definition_only_and_cannot_launch() -> None:
    pde = _load(PDE_PATH)
    pmcp = _load(PMCP_PATH)

    required_runtime_keys = {
        "workflow_id",
        "activity_id",
        "activity_attempt",
        "history_event_id",
        "heartbeat_at",
        "heartbeat_age_seconds",
        "retry_count",
        "signal",
        "command_id",
        "claim_id",
        "lease_id",
        "worker_id",
        "runtime_status",
        "run_progress",
        "evidence_receipt",
    }
    assert required_runtime_keys <= set(pde["recursive_runtime_field_denylist"])
    assert pde["state_vocabulary"]["revision_state"] == [
        "draft",
        "in_review",
        "approved",
        "superseded",
        "archived",
        "tombstoned",
    ]
    assert "running" in pde["state_vocabulary"]["explicitly_forbidden_execution_states"]

    handoff = pde["agent_handoff_contract"]
    assert handoff["schema_id"] == "odysseus.agent.plan_handoff.v1"
    assert handoff["requested_entrypoint_literal"] == "/abc"
    assert handoff["launch_authorized"] is False
    assert handoff["composer_text_format"] == (
        "/abc run roadmap:<roadmap_id>@<revision> hash:<content_hash>"
    )
    assert all(
        "/runs" not in endpoint
        for endpoint in pde["planning_api_contract"]["read_endpoints"]
    )
    assert all(
        "/runs" not in endpoint["path"]
        for endpoint in pde["planning_api_contract"]["write_endpoints"]
    )

    target_tools = {
        tool["name"]
        for group in ("read_only_tools", "write_or_mutation_tools")
        for tool in pmcp["mcp_tool_surface"][group]
    }
    assert "planning_read_gate_definitions" in target_tools
    assert "planning_create_agent_handoff" in target_tools
    assert "planning_mark_status" not in target_tools
    assert "planning_gate_status" not in target_tools
    deprecated = {
        item["name"]: item for item in pmcp["mcp_tool_surface"]["deprecated_tools"]
    }
    assert deprecated["planning_mark_status"]["status"] == "deprecated_no_write"
    assert deprecated["planning_gate_status"]["status"] == (
        "deprecated_no_runtime_read"
    )


def test_agent_owns_all_execution_state_and_only_abc_starts_runs() -> None:
    master = _load(MASTER_PATH)
    tlr = _load(TLR_PATH)

    endpoints = tlr["agent_api_contract"]["endpoints"]
    start = next(item for item in endpoints if item["path"] == "/api/agent/runs")
    assert start["method"] == "POST"
    assert start["caller"] == "authenticated /abc handler only"
    assert tlr["route"]["entrypoint"] == "/abc"
    assert tlr["frozen_architecture"]["planning_screen_rule"] == (
        "Planning never receives this projection and never displays whether a run exists."
    )
    assert tlr["long_run_policy"]["supported_requested_duration"] == (
        "one minute through 24 hours"
    )
    assert tlr["long_run_policy"]["unattended_target_window"] == (
        "12 through 24 hours"
    )
    assert tlr["long_run_policy"]["parallelism"]["maximum"] == 3

    planning_owned = set(master["screen_ownership_boundary"]["planning_screen_owns"])
    agent_owned = set(master["screen_ownership_boundary"]["agent_screen_owns"])
    assert "Open in Agent handoff preparation" in planning_owned
    assert "Temporal workflow ids and run ids" in agent_owned
    assert "Signals, Updates and operator commands" in agent_owned
    assert "runtime gate decisions and waits" in agent_owned


def test_obsolete_v2_queue_is_archived_and_hwa_uses_temporal() -> None:
    master = _load(MASTER_PATH)
    tlr_ids = {item["id"] for item in _load(TLR_PATH)["slice_queue"]}
    pde_ids = {item["id"] for item in _load(PDE_PATH)["slice_queue"]}
    child_ids = tlr_ids | pde_ids

    old = {
        item["id"]: item
        for item in master["slice_queue"]
        if item["id"].startswith(tuple(f"HPIM-{number}-" for number in range(5, 12)))
    }
    assert len(old) == 7
    for item in old.values():
        assert item["class"] == "archived"
        assert item["status"] == "superseded"
        assert item["superseded_by"]
        assert set(item["superseded_by"]) <= child_ids

    legacy_gate = master["gate_queue"][0]
    assert legacy_gate["id"] == "HPIM-GATE-v2-planning-ui-integration"
    assert legacy_gate["class"] == "archived"
    assert legacy_gate["status"] == "superseded"

    hwa = HWA_PATH.read_text(encoding="utf-8")
    assert "Temporal Light Adoption Decision" in hwa
    assert "HWA5A is TLR-03, then HWA4 is TLR-04" in hwa
    assert "no custom" in hwa
    assert "scheduler/effect queue is authorized" in hwa


def test_tlr01_authorized_local_runtime_is_closed_through_tlr06_with_process_stopped() -> None:
    tlr = _load(TLR_PATH)
    slices = {item["id"]: item for item in tlr["slice_queue"]}
    gates = {item["id"]: item for item in tlr["gate_queue"]}

    tlr01 = slices["TLR-01-temporal-light-local-runtime"]
    assert tlr01["status"] == "done_local_runtime_focused_and_restart_tested"
    assert tlr01["gate"]["id"] == "TLR-LOCAL-SERVICE-GO"
    assert tlr01["completion_evidence"]["focused_result"] == "20 passed"
    assert tlr01["completion_evidence"]["health_readback"].startswith("SERVING")
    assert "completed exactly once" in tlr01["completion_evidence"]["persistence_result"]
    assert "no 127.0.0.1:7233 listener remained" in tlr01["completion_evidence"]["cleanup_result"]

    local_gate = gates["TLR-LOCAL-SERVICE-GO"]
    assert local_gate["status"] == "used_for_TLR_01_process_stopped"
    assert local_gate["blocks"] == ["real process portions of TLR-08"]
    assert "Keep every Temporal process stopped" in local_gate["safe_default"]
    assert slices["TLR-06-history-agent-projection-api"]["status"] == "complete"
    assert "TLR-01 through TLR-06 are complete" in tlr["recommended_next_step"]
    assert "HPA-AGENT-UX-ACCEPTANCE" in tlr["recommended_next_step"]


def test_pde01_completion_routes_serially_to_pde02() -> None:
    pde = _load(PDE_PATH)
    slices = {item["id"]: item for item in pde["slice_queue"]}

    pde01 = slices["PDE-01-definition-schema-validator"]
    pde02 = slices["PDE-02-definition-read-model-api"]
    assert pde01["status"] == "implemented_focused_tested"
    assert pde01["hotfiles"] == []
    assert pde01["completion_evidence"]["focused_result"].startswith("92 passed")
    assert pde02["status"] == "implemented_registered_focused_tested"
    assert pde02["depends_on"] == ["PDE-01-definition-schema-validator"]
    assert pde02["implementation_evidence"]["focused_result"].startswith("36 passed")
    assert pde02["implementation_evidence"]["remaining"] == "none"
    assert slices["PDE-03-revision-proposal-apply"]["depends_on"] == [
        "PDE-02-definition-read-model-api"
    ]


def test_pde03_temporary_write_boundary_routes_to_pde04_without_real_write() -> None:
    pde = _load(PDE_PATH)
    slices = {item["id"]: item for item in pde["slice_queue"]}

    pde03 = slices["PDE-03-revision-proposal-apply"]
    pde04 = slices["PDE-04-agent-handoff-envelope"]
    assert pde03["status"] == "implemented_temporary_repository_focused_tested"
    assert pde03["completion_evidence"]["focused_result"].startswith("38 passed")
    assert pde03["completion_evidence"]["write_gate_state"].startswith(
        "PLANNING-WRITE-GO remains gated"
    )
    assert pde04["depends_on"] == ["PDE-03-revision-proposal-apply"]
    assert pde04["gate"]["safe_default"].startswith("Navigation envelope only")


def test_pde04_completion_parks_pde05_at_named_design_gate() -> None:
    pde = _load(PDE_PATH)
    slices = {item["id"]: item for item in pde["slice_queue"]}
    gates = {item["id"]: item for item in pde["gate_queue"]}

    pde04 = slices["PDE-04-agent-handoff-envelope"]
    pde05 = slices["PDE-05-v3-planning-surface"]
    design_gate = gates["HPA-PLANNING-UX-ACCEPTANCE"]
    assert pde04["status"] == "implemented_focused_tested"
    assert pde04["completion_evidence"]["focused_result"].startswith("51 passed")
    assert pde05["status"] == "planned"
    assert pde05["gate"]["id"] == "HPA-PLANNING-UX-ACCEPTANCE"
    assert design_gate["status"] == "design_direction_locked_acceptance_pending"
    assert "PDE-05-v3-planning-surface" in design_gate["blocks"]
    assert "park PDE-05" in pde["recommended_next_step"]
    assert "Planning MCP" in pde["recommended_next_step"]
