from src.memory_store_interfaces import (
    MemoryStoreInterfaceError,
    StoreBudget,
    StoreCapability,
    StoreInterfaceSpec,
    StoreKind,
    StoreOperationSpec,
    StoreTruthRole,
)


def _make_budget(**overrides) -> StoreBudget:
    payload = {
        "default_limit": 50,
        "max_limit": 200,
        "max_bytes": 4096,
        "max_nodes": 0,
        "max_edges": 0,
        "supports_cursor": True,
        "time_budget_ms": 250,
        "token_budget": 1200,
        "max_depth": 0,
        "stale_after_seconds": 3600,
    }
    payload.update(overrides)
    return StoreBudget.create(**payload)


def _make_operation(**overrides) -> StoreOperationSpec:
    payload = {
        "operation_id": "memory-read",
        "capability": "read",
        "bounded_default": True,
        "budget": _make_budget(),
        "evidence_refs": ["contract ms1"],
        "summary": "Bounded read over memory rows.",
    }
    payload.update(overrides)
    return StoreOperationSpec.create(**payload)


def _make_store(**overrides) -> StoreInterfaceSpec:
    payload = {
        "store_id": "memory-store",
        "store_kind": "memory",
        "truth_role": "truth",
        "capabilities": ["read", "write", "scan"],
        "operations": [
            _make_operation(),
            _make_operation(operation_id="memory-scan", capability="scan"),
        ],
        "dependencies": (),
        "rebuild_evidence": (),
        "summary": "Primary memory truth store.",
    }
    payload.update(overrides)
    return StoreInterfaceSpec.create(**payload)


def test_valid_store_interface_spec_normalizes_stably() -> None:
    store = _make_store(store_id=" Memory Store ")

    assert store.store_id == "memory-store"
    assert store.store_kind is StoreKind.MEMORY
    assert store.truth_role is StoreTruthRole.TRUTH
    assert store.capabilities == (
        StoreCapability.READ,
        StoreCapability.SCAN,
        StoreCapability.WRITE,
    )
    assert store.operations[0].operation_id == "memory-read"


def test_read_search_without_limit_or_budget_is_rejected() -> None:
    try:
        _make_operation(
            bounded_default=False,
            budget=StoreBudget.create(),
        )
    except MemoryStoreInterfaceError as exc:
        assert "require bounded defaults or query budgets" in str(exc)
    else:
        raise AssertionError("expected bounded query validation to fail")


def test_graph_operation_without_node_or_edge_budget_is_rejected() -> None:
    try:
        _make_store(
            store_kind="graph",
            capabilities=["read", "rebuild"],
            operations=[
                _make_operation(
                    operation_id="graph-read",
                    capability="read",
                    budget=_make_budget(default_limit=10, max_limit=20, max_bytes=0, max_nodes=0, max_edges=0),
                )
            ],
            truth_role="derived",
        )
    except MemoryStoreInterfaceError as exc:
        assert "graph operations require max_nodes or max_edges budgets" in str(exc)
    else:
        raise AssertionError("expected graph budget validation to fail")


def test_graph_depth_counts_as_graph_budget() -> None:
    store = _make_store(
        store_kind="graph",
        capabilities=["read", "rebuild"],
        operations=[
            _make_operation(
                operation_id="graph-read",
                capability="read",
                budget=_make_budget(
                    default_limit=10,
                    max_limit=20,
                    max_bytes=0,
                    max_nodes=0,
                    max_edges=0,
                    max_depth=2,
                ),
            )
        ],
        truth_role="derived",
    )

    assert store.operations[0].budget.max_depth == 2


def test_contract_budget_fields_are_preserved_in_summary() -> None:
    store = _make_store()
    budget = store.operations[0].budget

    assert budget.supports_cursor is True
    assert budget.time_budget_ms == 250
    assert budget.token_budget == 1200
    assert budget.stale_after_seconds == 3600
    assert store.audit_summary()["budget_family_count"] >= 5


def test_accelerator_without_rebuild_capability_is_rejected() -> None:
    try:
        _make_store(
            truth_role="accelerator",
            capabilities=["read", "search"],
            operations=[
                _make_operation(capability="search", operation_id="memory-search"),
            ],
        )
    except MemoryStoreInterfaceError as exc:
        assert "require rebuild capability or rebuild evidence" in str(exc)
    else:
        raise AssertionError("expected accelerator rebuild validation to fail")


def test_truth_store_with_accelerator_dependency_is_rejected() -> None:
    try:
        _make_store(dependencies=["accelerator"])
    except MemoryStoreInterfaceError as exc:
        assert "must not depend on accelerator stores" in str(exc)
    else:
        raise AssertionError("expected truth dependency validation to fail")


def test_unbounded_load_all_operation_is_rejected() -> None:
    try:
        _make_operation(operation_id="load_all")
    except MemoryStoreInterfaceError as exc:
        assert "load_all style operations are not allowed" in str(exc)
    else:
        raise AssertionError("expected unbounded operation validation to fail")


def test_audit_summary_contains_store_ids_kinds_budget_counts_without_long_dumps() -> None:
    store = _make_store(
        capabilities=["read", "scan", "write", "rebuild"],
        operations=[
            _make_operation(
                summary="bounded " + ("x" * 500),
            ),
            _make_operation(operation_id="memory-scan", capability="scan"),
        ],
    )

    summary = store.audit_summary()

    assert summary["store_id"] == "memory-store"
    assert summary["store_kind"] == "memory"
    assert summary["capability_count"] == 4
    assert summary["operation_count"] == 2
    assert summary["budgeted_operation_count"] == 2
    assert "summary" not in str(summary).lower()
    assert "x" * 200 not in str(summary)
