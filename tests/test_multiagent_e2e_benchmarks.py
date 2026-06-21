import pytest

from src.multiagent_e2e_benchmarks import (
    MultiagentE2EBenchmarkError,
    run_core_multiagent_e2e_benchmark,
)
from src.plan_runtime import PlanRuntimeState


def _runtime(*, dependency_live: bool = True) -> PlanRuntimeState:
    return PlanRuntimeState.from_dict(
        {
            "schema_version": 1,
            "plan_id": "odysseus-multiagent-roadmap",
            "title": "Odysseus Multiagent Runtime Roadmap",
            "format_decision": {"source_of_truth": "json"},
            "recommended_active_node": "multiagent-e2e-benchmarks",
            "version_horizons": [{"id": "after-v0-9"}],
            "graph_nodes": [
                {
                    "id": "heartbeat-runtime-live-loop",
                    "kind": "runtime",
                    "priority_rank": 1,
                    "title": "Runtime heartbeat loop",
                    "horizon": "after-v0-9",
                    "target_version": "0.10",
                    "status": "done",
                    "depends_on": [],
                    "unlocks": ["multiagent-e2e-benchmarks"],
                    "gates": ["runtime_tick_plans_next_node"],
                    "source_refs": ["src/orchestration_runtime_loop.py"],
                    "deliverables": ["runtime heartbeat planner"],
                    "completion_state": {
                        "status": "live_installed" if dependency_live else "local_only",
                        "commit": "d65e8a38",
                    },
                },
                {
                    "id": "worktree-branch-isolation",
                    "kind": "workspace",
                    "priority_rank": 2,
                    "title": "Worktree isolation",
                    "horizon": "after-v0-9",
                    "target_version": "0.10",
                    "status": "done",
                    "depends_on": [],
                    "unlocks": ["multiagent-e2e-benchmarks"],
                    "gates": ["workspace_integration_gate"],
                    "source_refs": ["src/workspace_policy.py"],
                    "deliverables": ["workspace assignment gate"],
                    "completion_state": {"status": "live_installed", "commit": "64383595"},
                },
                {
                    "id": "memory-capsule-integration",
                    "kind": "memory",
                    "priority_rank": 3,
                    "title": "Memory capsule integration",
                    "horizon": "after-v0-9",
                    "target_version": "0.10",
                    "status": "done",
                    "depends_on": [],
                    "unlocks": ["multiagent-e2e-benchmarks"],
                    "gates": ["accepted_memory_only"],
                    "source_refs": ["src/context_capsule.py"],
                    "deliverables": ["accepted memory items"],
                    "completion_state": {"status": "live_installed", "commit": "28a53b32"},
                },
                {
                    "id": "multiagent-e2e-benchmarks",
                    "kind": "verification",
                    "priority_rank": 4,
                    "title": "Multiagent e2e benchmarks",
                    "horizon": "after-v0-9",
                    "target_version": "0.10",
                    "status": "planned",
                    "depends_on": [
                        "heartbeat-runtime-live-loop",
                        "worktree-branch-isolation",
                        "memory-capsule-integration",
                    ],
                    "unlocks": ["telegram-rich-text-rendering"],
                    "gates": [
                        "benchmark_has_structured_plan",
                        "domain_tools_policy_gated",
                        "no_live_provider_without_go",
                    ],
                    "source_refs": [
                        "src/multiagent_e2e_benchmarks.py",
                        "tests/test_multiagent_e2e_benchmarks.py",
                    ],
                    "deliverables": ["offline core benchmark"],
                },
            ],
            "plan_graph_projection": {"status_mapping": {"done": "done", "planned": "pending"}},
        }
    )


def test_core_multiagent_benchmark_proves_claim_capsule_workspace_and_dashboard_path():
    result = run_core_multiagent_e2e_benchmark(_runtime())
    summary = result.audit_summary()

    assert result.decision == "go"
    assert result.node_id == "multiagent-e2e-benchmarks"
    assert result.checks == (
        "planruntime_claimable",
        "claim_lease_created",
        "subagent_spec_bound_to_capsule",
        "accepted_memory_item_attached",
        "workspace_integration_allowed",
        "fake_subagent_verified_and_gate_blocked",
        "dashboard_snapshot_ready",
    )
    assert "no_live_provider_without_operator_go" in result.blocked_actions
    assert any(ref.startswith("claim:") for ref in result.evidence_refs)
    assert any(ref == "memory_items:1" for ref in result.evidence_refs)
    assert summary["check_count"] == 7


def test_core_multiagent_benchmark_blocks_non_claimable_nodes():
    with pytest.raises(MultiagentE2EBenchmarkError, match="not claimable|no claimable"):
        run_core_multiagent_e2e_benchmark(
            _runtime(dependency_live=False),
            node_id="multiagent-e2e-benchmarks",
        )


def test_core_multiagent_benchmark_serializes_operator_safe_result():
    payload = run_core_multiagent_e2e_benchmark(_runtime()).to_dict()

    assert payload["benchmark_id"].endswith("multiagent-e2e-benchmarks-core-e2e")
    assert payload["decision"] == "go"
    assert payload["blocked_actions"] == [
        "no_live_provider_without_operator_go",
        "no_raw_thread_or_chat_content",
        "no_direct_roadmap_mutation_from_worker_reports",
    ]
