from src.homeserver_ops_readiness import (
    BackupRestorePlan,
    HomeserverOpsReadinessError,
    HomeserverProfile,
    MaintenancePolicy,
    OpsReadinessReport,
    OpsReadinessStatus,
    ResourceBudget,
)


def _make_profile(**overrides) -> HomeserverProfile:
    payload = {
        "homeserver_profile": "homeserver-main",
        "service_ref": "memory-stack",
        "postgres_ref": "postgres-main",
        "data_volume_ref": "data-volume",
        "backup_volume_ref": "backup-volume",
        "cpu_cores": 8,
        "ram_gb": 32,
        "storage_gb": 512,
    }
    payload.update(overrides)
    return HomeserverProfile.create(**payload)


def _make_budget(**overrides) -> ResourceBudget:
    payload = {
        "max_memory_job_concurrency": 2,
        "max_index_job_concurrency": 1,
        "max_cpu_percent": 80,
        "max_ram_percent": 75,
        "storage_warning_percent": 75,
        "storage_block_percent": 90,
        "current_storage_percent": 60,
    }
    payload.update(overrides)
    return ResourceBudget.create(**payload)


def _make_backup_plan(**overrides) -> BackupRestorePlan:
    payload = {
        "backup_ref": "backup-001",
        "restore_ref": "restore-001",
        "restore_drill_status": "ok",
        "last_restore_drill_ref": "drill-001",
    }
    payload.update(overrides)
    return BackupRestorePlan.create(**payload)


def _make_maintenance(**overrides) -> MaintenancePolicy:
    payload = {
        "maintenance_window": "sun-02:00-04:00",
        "vacuum_policy": "weekly vacuum analyze",
        "index_maintenance_policy": "monthly reindex check",
        "retention_policy": "keep 30 daily backups",
    }
    payload.update(overrides)
    return MaintenancePolicy.create(**payload)


def _make_report(**overrides) -> OpsReadinessReport:
    payload = {
        "profile": _make_profile(),
        "resource_budget": _make_budget(),
        "backup_restore_plan": _make_backup_plan(),
        "maintenance_policy": _make_maintenance(),
        "go_no_go_status": "go",
        "risk_evidence_ref": "ops-risk-review",
        "reason": "",
        "next_action": "",
    }
    payload.update(overrides)
    return OpsReadinessReport.create(**payload)


def test_valid_ops_readiness_report_normalizes_stably() -> None:
    report = _make_report(go_no_go_status="ready_for_review")

    assert report.go_no_go_status is OpsReadinessStatus.READY_FOR_REVIEW
    assert report.profile.homeserver_profile == "homeserver-main"
    assert report.resource_budget.max_memory_job_concurrency == 2


def test_go_without_backup_restore_or_restore_drill_is_rejected() -> None:
    try:
        _make_report(
            backup_restore_plan=_make_backup_plan(
                backup_ref="",
                restore_ref="",
                restore_drill_status="failed",
                last_restore_drill_ref="",
            ),
            go_no_go_status="go",
        )
    except HomeserverOpsReadinessError as exc:
        assert "go requires backup_ref" in str(exc)
    else:
        raise AssertionError("expected go backup/restore validation to fail")


def test_unbounded_concurrency_is_rejected() -> None:
    try:
        _make_budget(max_memory_job_concurrency=0)
    except HomeserverOpsReadinessError as exc:
        assert "must be > 0" in str(exc)
    else:
        raise AssertionError("expected concurrency validation to fail")


def test_storage_block_pressure_must_not_allow_go() -> None:
    try:
        _make_report(resource_budget=_make_budget(current_storage_percent=95), go_no_go_status="go")
    except HomeserverOpsReadinessError as exc:
        assert "must not allow go" in str(exc)
    else:
        raise AssertionError("expected storage pressure validation to fail")


def test_missing_maintenance_policy_blocks_go() -> None:
    try:
        _make_report(
            maintenance_policy=_make_maintenance(maintenance_window=" ", vacuum_policy=" "),
            go_no_go_status="go",
        )
    except HomeserverOpsReadinessError as exc:
        assert "must not be empty" in str(exc) or "go requires" in str(exc)
    else:
        raise AssertionError("expected maintenance validation to fail")


def test_blocked_failed_or_no_go_without_reason_or_next_action_are_rejected() -> None:
    for status in ("blocked", "failed", "no_go"):
        try:
            _make_report(go_no_go_status=status, reason=" ", next_action=" ")
        except HomeserverOpsReadinessError as exc:
            assert "require reason or next_action" in str(exc)
        else:
            raise AssertionError("expected blocked/failed/no_go validation to fail")


def test_accelerator_activation_is_rejected() -> None:
    try:
        _make_report(risk_evidence_ref="enable qdrant accelerator next")
    except HomeserverOpsReadinessError as exc:
        assert "accelerator activation is out of scope" in str(exc)
    else:
        raise AssertionError("expected accelerator validation to fail")


def test_audit_summary_contains_profile_status_budgets_restore_and_storage_pressure_without_long_dumps() -> None:
    report = _make_report(
        go_no_go_status="ready_for_review",
        risk_evidence_ref="ops risk " + ("x" * 500),
    )

    summary = report.audit_summary()

    assert summary["homeserver_profile"] == "homeserver-main"
    assert summary["go_no_go_status"] == "ready_for_review"
    assert summary["max_memory_job_concurrency"] == 2
    assert summary["restore_drill_status"] == "ok"
    assert summary["current_storage_percent"] == 60
    assert "risk" not in str(summary).lower()
    assert "x" * 200 not in str(summary)
