from src.memory_diagnostics import (
    DiagnosticMetric,
    DiagnosticSnapshot,
    MemoryDiagnosticsError,
    MetricFamily,
    MetricSeverity,
    MetricStatus,
    MetricUnit,
)


def _make_metric(**overrides) -> DiagnosticMetric:
    payload = {
        "metric_id": "query-latency",
        "family": "query",
        "phase": "serve",
        "value": 125,
        "unit": "ms",
        "budget": 200,
        "status": "healthy",
        "severity": "low",
        "clipped": False,
        "stale": False,
        "evidence_ref": "diag-query-001",
        "next_action": "",
    }
    payload.update(overrides)
    return DiagnosticMetric.create(**payload)


def _make_snapshot(**overrides) -> DiagnosticSnapshot:
    payload = {
        "snapshot_id": "memory-diagnostics",
        "subject_ref": "store-memory",
        "metrics": [_make_metric()],
        "created_at": "2026-06-16T18:10:00Z",
        "summary": "Memory diagnostics for current store lane.",
    }
    payload.update(overrides)
    return DiagnosticSnapshot.create(**payload)


def test_valid_diagnostic_snapshot_normalizes_stably() -> None:
    snapshot = _make_snapshot(
        snapshot_id=" Memory Diagnostics ",
        metrics=[
            _make_metric(metric_id="storage-bytes", family="storage", unit="bytes", value=2048, budget=4096),
            _make_metric(metric_id="query-latency"),
        ],
    )

    assert snapshot.snapshot_id == "memory-diagnostics"
    assert snapshot.subject_ref == "store-memory"
    assert snapshot.metrics[0].family is MetricFamily.QUERY
    assert snapshot.metrics[1].family is MetricFamily.STORAGE


def test_negative_values_are_rejected() -> None:
    try:
        _make_metric(value=-1)
    except MemoryDiagnosticsError as exc:
        assert "negative metric values" in str(exc)
    else:
        raise AssertionError("expected negative metric validation to fail")


def test_clipped_and_stale_without_evidence_or_next_action_are_rejected() -> None:
    for overrides in (
        {"clipped": True, "evidence_ref": " ", "next_action": " "},
        {"stale": True, "evidence_ref": " ", "next_action": " "},
    ):
        try:
            _make_metric(**overrides)
        except MemoryDiagnosticsError as exc:
            assert "require evidence or next_action" in str(exc)
        else:
            raise AssertionError("expected clipped/stale validation to fail")


def test_blocked_and_failed_without_evidence_or_next_action_are_rejected() -> None:
    for status in ("blocked", "failed"):
        try:
            _make_metric(status=status, evidence_ref=" ", next_action=" ")
        except MemoryDiagnosticsError as exc:
            assert "require evidence or next_action" in str(exc)
        else:
            raise AssertionError("expected blocked/failed validation to fail")


def test_critical_unknown_without_evidence_or_next_action_is_rejected() -> None:
    try:
        _make_metric(status="unknown", severity="critical", evidence_ref=" ", next_action=" ")
    except MemoryDiagnosticsError as exc:
        assert "unknown metrics require evidence or next_action" in str(exc)
    else:
        raise AssertionError("expected critical unknown validation to fail")


def test_budget_violation_with_healthy_is_rejected() -> None:
    try:
        _make_metric(value=350, budget=200, status="healthy")
    except MemoryDiagnosticsError as exc:
        assert "budget violations require warning" in str(exc)
    else:
        raise AssertionError("expected budget-violation validation to fail")


def test_snapshot_without_metrics_is_rejected() -> None:
    try:
        _make_snapshot(metrics=())
    except MemoryDiagnosticsError as exc:
        assert "metrics must not be empty" in str(exc)
    else:
        raise AssertionError("expected empty snapshot validation to fail")


def test_audit_summary_contains_families_status_counts_clipped_stale_without_long_dumps() -> None:
    snapshot = _make_snapshot(
        metrics=[
            _make_metric(metric_id="query-latency", status="warning", value=350, budget=200),
            _make_metric(
                metric_id="memory-clipped",
                family="memory",
                phase="inspect",
                unit="count",
                value=50,
                budget=100,
                clipped=True,
                evidence_ref="clipped " + ("x" * 500),
            ),
        ]
    )

    summary = snapshot.audit_summary()

    assert summary["snapshot_id"] == "memory-diagnostics"
    assert summary["metric_count"] == 2
    assert summary["family_counts"]["query"] == 1
    assert summary["family_counts"]["memory"] == 1
    assert summary["status_counts"]["warning"] == 1
    assert summary["clipped_count"] == 1
    assert summary["stale_count"] == 0
    assert "evidence" not in str(summary).lower()
    assert "x" * 200 not in str(summary)
