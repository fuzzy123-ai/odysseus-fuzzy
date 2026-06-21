import pytest

from src.claim_lease_store import ClaimLeaseStore, ClaimLeaseStoreError
from src.plan_runtime import PlanRuntimeState


def _runtime(*, first_source="src/a.py", second_source="src/b.py", first_dependency_live=True):
    return PlanRuntimeState.from_dict(
        {
            "schema_version": 1,
            "plan_id": "odysseus-multiagent-roadmap",
            "title": "Odysseus Multiagent Runtime Roadmap",
            "format_decision": {"source_of_truth": "json"},
            "recommended_active_node": "claim-lease-locks",
            "version_horizons": [{"id": "v0-9"}],
            "graph_nodes": [
                {
                    "id": "subagent-plan-binding",
                    "kind": "runtime",
                    "priority_rank": 1,
                    "title": "Bind SubagentRuntime to PlanRuntime slices",
                    "horizon": "v0-9",
                    "target_version": "0.9",
                    "status": "done",
                    "depends_on": [],
                    "unlocks": ["claim-lease-locks"],
                    "gates": ["context_capsule_required"],
                    "source_refs": ["src/subagent_plan_binding.py"],
                    "deliverables": ["binding"],
                    "completion_state": {"status": "live_installed" if first_dependency_live else "local_only"},
                },
                {
                    "id": "claim-lease-locks",
                    "kind": "coordination",
                    "priority_rank": 2,
                    "title": "Implement slice claims, leases, file locks, and budgets",
                    "horizon": "v0-9",
                    "target_version": "0.9",
                    "status": "planned",
                    "depends_on": ["subagent-plan-binding"],
                    "unlocks": ["real-gate-runners"],
                    "gates": ["one_owner_per_active_slice", "file_lock_blocks_overlap"],
                    "source_refs": [first_source],
                    "deliverables": ["Claim model", "Lease model"],
                },
                {
                    "id": "other-slice",
                    "kind": "coordination",
                    "priority_rank": 3,
                    "title": "Other slice",
                    "horizon": "v0-9",
                    "target_version": "0.9",
                    "status": "planned",
                    "depends_on": ["subagent-plan-binding"],
                    "unlocks": [],
                    "gates": ["file_lock_blocks_overlap"],
                    "source_refs": [second_source],
                    "deliverables": ["other"],
                },
            ],
            "plan_graph_projection": {"status_mapping": {"done": "done", "planned": "pending"}},
        }
    )


def _store():
    return ClaimLeaseStore.create(known_agent_ids=["bob", "alice"])


def test_claim_node_creates_active_claim_and_file_lease():
    claim = _store().claim_node(
        _runtime(),
        node_id="claim-lease-locks",
        owner_agent_id="Bob",
        claimed_at="2026-06-21T09:10:00Z",
        lease_expires_at="2026-06-21T09:40:00Z",
        reason="start focused slice",
    )

    assert claim.claim_id == "claim-lease-locks-bob-claim"
    assert claim.files == ("src/a.py",)


def test_unknown_agent_is_blocked():
    with pytest.raises(ClaimLeaseStoreError, match="unknown agent"):
        _store().claim_node(
            _runtime(),
            node_id="claim-lease-locks",
            owner_agent_id="mallory",
            claimed_at="2026-06-21T09:10:00Z",
            lease_expires_at="2026-06-21T09:40:00Z",
            reason="not registered",
        )


def test_non_claimable_node_is_blocked_until_dependencies_live():
    with pytest.raises(ClaimLeaseStoreError, match="not claimable"):
        _store().claim_node(
            _runtime(first_dependency_live=False),
            node_id="claim-lease-locks",
            owner_agent_id="bob",
            claimed_at="2026-06-21T09:10:00Z",
            lease_expires_at="2026-06-21T09:40:00Z",
            reason="dependency missing",
        )


def test_file_lock_blocks_overlapping_claims():
    store = _store()
    runtime = _runtime(first_source="src/shared.py", second_source="src/shared.py")
    store.claim_node(
        runtime,
        node_id="claim-lease-locks",
        owner_agent_id="bob",
        claimed_at="2026-06-21T09:10:00Z",
        lease_expires_at="2026-06-21T09:40:00Z",
        reason="first claim",
    )

    with pytest.raises(ClaimLeaseStoreError, match="file locked by bob"):
        store.claim_node(
            runtime,
            node_id="other-slice",
            owner_agent_id="alice",
            claimed_at="2026-06-21T09:20:00Z",
            lease_expires_at="2026-06-21T09:50:00Z",
            reason="overlapping claim",
        )


def test_expired_lease_does_not_block_but_remains_visible():
    store = _store()
    runtime = _runtime(first_source="src/shared.py", second_source="src/shared.py")
    store.claim_node(
        runtime,
        node_id="claim-lease-locks",
        owner_agent_id="bob",
        claimed_at="2026-06-21T09:10:00Z",
        lease_expires_at="2026-06-21T09:20:00Z",
        reason="first claim",
    )
    second = store.claim_node(
        runtime,
        node_id="other-slice",
        owner_agent_id="alice",
        claimed_at="2026-06-21T09:21:00Z",
        lease_expires_at="2026-06-21T09:50:00Z",
        reason="after visible timeout",
    )

    assert second.owner_agent_id == "alice"
    assert store.audit_summary()["claim_count"] == 2
    assert store.audit_summary()["file_lease_count"] == 1


def test_current_roadmap_next_claimable_node_can_be_claimed():
    runtime = PlanRuntimeState.load_json("specs/roadmaps/odysseus-multiagent-roadmap.v1.json")
    store = ClaimLeaseStore.create(known_agent_ids=["charlie"])

    claim = store.claim_node(
        runtime,
        node_id=runtime.next_claimable_node_id(),
        owner_agent_id="charlie",
        claimed_at="2026-06-21T09:10:00Z",
        lease_expires_at="2026-06-21T09:40:00Z",
        reason="current roadmap claim",
    )

    assert claim.node_id == runtime.next_claimable_node_id()
    assert store.audit_summary()["active_claim_count"] == 1
