import pytest

from src.plan_runtime import PlanRuntimeState
from src.subagent_plan_binding import SubagentPlanBindingError, build_subagent_spec_from_plan_runtime


def _runtime(*, dependency_live: bool = True, source_refs=True):
    source = ["src/subagent_plan_binding.py"] if source_refs else []
    return PlanRuntimeState.from_dict(
        {
            "schema_version": 1,
            "plan_id": "odysseus-multiagent-roadmap",
            "title": "Odysseus Multiagent Runtime Roadmap",
            "format_decision": {"source_of_truth": "json"},
            "recommended_active_node": "subagent-plan-binding",
            "version_horizons": [{"id": "v0-9"}],
            "graph_nodes": [
                {
                    "id": "planruntime-source-of-truth",
                    "kind": "runtime",
                    "priority_rank": 1,
                    "title": "Build PlanRuntime as the source of truth",
                    "horizon": "v0-9",
                    "target_version": "0.9",
                    "status": "done",
                    "depends_on": [],
                    "unlocks": ["subagent-plan-binding"],
                    "gates": ["plan_schema_validates"],
                    "source_refs": ["src/plan_runtime.py"],
                    "deliverables": ["PlanRuntime schema"],
                    "completion_state": {"status": "live_installed" if dependency_live else "local_only"},
                },
                {
                    "id": "subagent-plan-binding",
                    "kind": "runtime",
                    "priority_rank": 2,
                    "title": "Bind SubagentRuntime to PlanRuntime slices",
                    "horizon": "v0-9",
                    "target_version": "0.9",
                    "status": "planned",
                    "depends_on": ["planruntime-source-of-truth"],
                    "unlocks": ["claim-lease-locks"],
                    "gates": ["context_capsule_created", "fake_runtime_boundary_preserved"],
                    "source_refs": source,
                    "deliverables": ["PlanRuntime-to-SubagentRunSpec adapter", "ContextCapsule projection"],
                },
            ],
            "plan_graph_projection": {"status_mapping": {"done": "done", "planned": "pending"}},
        }
    )


def test_builds_subagent_spec_from_next_claimable_runtime_node():
    spec = build_subagent_spec_from_plan_runtime(
        _runtime(),
        agent_id="Bob",
        created_at="2026-06-21T09:00:00Z",
    )

    assert spec.agent_run_id == "subagent-plan-binding-bob-run"
    assert spec.plan_id == "odysseus-multiagent-roadmap"
    assert spec.node_id == "subagent-plan-binding"
    assert spec.role_id == "backend"
    assert spec.allowed_files == ("src/subagent_plan_binding.py",)
    assert spec.evidence_required == ("context_capsule_created", "fake_runtime_boundary_preserved")
    assert spec.target_kind.value == "job"
    assert spec.thread_id == ""
    assert spec.to_context_capsule().agent_identity.run_id == "subagent-plan-binding-bob-run"


def test_rejects_node_when_dependency_is_not_live_done():
    with pytest.raises(SubagentPlanBindingError, match="no claimable"):
        build_subagent_spec_from_plan_runtime(
            _runtime(dependency_live=False),
            created_at="2026-06-21T09:00:00Z",
        )


def test_rejects_explicit_non_claimable_node():
    with pytest.raises(SubagentPlanBindingError, match="not claimable"):
        build_subagent_spec_from_plan_runtime(
            _runtime(),
            node_id="planruntime-source-of-truth",
            created_at="2026-06-21T09:00:00Z",
        )


def test_falls_back_to_roadmap_path_when_node_has_no_source_refs():
    spec = build_subagent_spec_from_plan_runtime(
        _runtime(source_refs=False),
        created_at="2026-06-21T09:00:00Z",
    )

    assert spec.allowed_files == ("specs/roadmaps/odysseus-multiagent-roadmap.v1.json",)


def test_current_roadmap_binds_recommended_claimable_node_without_live_spawn():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")

    spec = build_subagent_spec_from_plan_runtime(runtime, created_at="2026-06-21T09:00:00Z")

    assert spec.node_id == runtime.next_claimable_node_id()
    assert spec.target_kind.value == "job"
    assert "live threads" in " ".join(spec.stop_conditions)
