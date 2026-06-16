import pytest

from src.thread_lifecycle_bridge import ThreadRef
from src.thread_registry import ThreadRegistry, ThreadRegistryError


def _ref(
    *,
    thread_id: str = "019-thread",
    agent_id: str = "alice",
    agent_run_id: str = "run-a",
    node_id: str = "node-a",
) -> ThreadRef:
    return ThreadRef.create(
        thread_id=thread_id,
        agent_id=agent_id,
        agent_run_id=agent_run_id,
        plan_id="auto2-plan",
        node_id=node_id,
    )


def test_register_and_resolve_by_run_and_thread():
    registry = ThreadRegistry()
    ref = _ref()
    registry.register(ref)

    assert registry.resolve_run("run-a") == ref
    assert registry.resolve_thread("019-thread") == ref
    assert registry.audit_summary()["thread_count"] == 1


def test_registering_same_ref_is_idempotent():
    registry = ThreadRegistry()
    ref = _ref()

    registry.register(ref)
    registry.register(ref)

    assert registry.audit_summary()["run_count"] == 1


def test_agent_run_cannot_be_assigned_to_second_thread():
    registry = ThreadRegistry()
    registry.register(_ref(thread_id="thread-one"))

    with pytest.raises(ThreadRegistryError, match="agent run already assigned"):
        registry.register(_ref(thread_id="thread-two"))


def test_thread_cannot_be_assigned_to_second_agent_run():
    registry = ThreadRegistry()
    registry.register(_ref(agent_run_id="run-one"))

    with pytest.raises(ThreadRegistryError, match="thread already assigned"):
        registry.register(_ref(agent_run_id="run-two"))


def test_dispatch_target_requires_expected_agent_and_node():
    registry = ThreadRegistry()
    registry.register(_ref(agent_id="bob", node_id="node-b"))

    assert registry.dispatch_target(agent_run_id="run-a", expected_agent_id="bob", expected_node_id="node-b").thread_id == "019-thread"
    with pytest.raises(ThreadRegistryError, match="agent_id mismatch"):
        registry.dispatch_target(agent_run_id="run-a", expected_agent_id="alice", expected_node_id="node-b")
    with pytest.raises(ThreadRegistryError, match="node_id mismatch"):
        registry.dispatch_target(agent_run_id="run-a", expected_agent_id="bob", expected_node_id="node-c")


def test_unknown_run_or_thread_is_rejected():
    registry = ThreadRegistry()

    with pytest.raises(ThreadRegistryError, match="unknown agent run"):
        registry.resolve_run("missing")
    with pytest.raises(ThreadRegistryError, match="unknown thread"):
        registry.resolve_thread("missing")


def test_registry_roundtrips_through_dict():
    registry = ThreadRegistry()
    registry.register(_ref(thread_id="thread-a", agent_id="alice", agent_run_id="run-a", node_id="node-a"))
    registry.register(_ref(thread_id="thread-b", agent_id="bob", agent_run_id="run-b", node_id="node-b"))

    loaded = ThreadRegistry.from_dict(registry.to_dict())

    assert loaded.resolve_run("run-a").thread_id == "thread-a"
    assert loaded.resolve_thread("thread-b").agent_id == "bob"


def test_schema_version_must_match():
    with pytest.raises(ThreadRegistryError, match="schema_version must be 1"):
        ThreadRegistry.from_dict({"schema_version": 2, "thread_refs": []})
