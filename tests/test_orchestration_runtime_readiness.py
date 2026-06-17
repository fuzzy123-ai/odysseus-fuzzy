from src.orchestration_runtime_readiness import (
    ReadinessStatus,
    RuntimeCapability,
    RuntimeGap,
    build_current_runtime_readiness_report,
    build_runtime_readiness_report,
)


def test_default_report_is_not_live_ready():
    report = build_current_runtime_readiness_report()

    assert report.ok is False
    assert report.blocked is False
    assert report.open_gap_count == 4
    assert "dry-run mode" in report.next_safe_action.lower()


def test_dry_run_capabilities_are_not_counted_as_live_hooks():
    report = build_current_runtime_readiness_report()

    by_id = {capability.capability_id: capability for capability in report.capabilities}
    assert by_id["thread-send-hook"].status == ReadinessStatus.DRY_RUN_ONLY
    assert by_id["thread-send-hook"].live_hook is False
    assert by_id["scheduler-heartbeat-execution"].live_hook is False


def test_operator_required_gap_blocks_live_readiness():
    report = build_runtime_readiness_report(
        capabilities=(
            RuntimeCapability.create(
                capability_id="registry-model",
                category="dashboard",
                status="ready",
                live_hook=False,
                summary="ready metadata",
            ),
        ),
        gaps=(
            RuntimeGap.create(
                gap_id="git-runner-operator-gate",
                category="git",
                status="requires_operator",
                summary="git requires operator",
                next_safe_action="wait for operator",
            ),
            RuntimeGap.create(
                gap_id="live-hook-hard-block",
                category="threading",
                status="blocked",
                summary="live thread hook is blocked",
                next_safe_action="do not enable live orchestration",
            ),
        ),
    )

    assert report.ok is False
    assert report.blocked is True
    assert report.open_gap_count == 2


def test_stable_dict_output():
    report = build_current_runtime_readiness_report()

    assert report.to_dict() == {
        "ok": False,
        "blocked": False,
        "open_gap_count": 4,
        "next_safe_action": "Keep scheduler/heartbeat execution operator-controlled and in dry-run mode for now.",
        "capabilities": (
            {
                "capability_id": "git-command-runner",
                "category": "git",
                "status": "requires_operator",
                "live_hook": False,
                "summary": "Git command runner is intentionally operator-gated and not live-wired.",
            },
            {
                "capability_id": "registry-model",
                "category": "dashboard",
                "status": "ready",
                "live_hook": False,
                "summary": "AUTO registry metadata and dashboard-facing models are prepared.",
            },
            {
                "capability_id": "scheduler-heartbeat-execution",
                "category": "scheduler",
                "status": "dry_run_only",
                "live_hook": False,
                "summary": "Heartbeat scheduler logic is prepared, but live execution stays dry-run only.",
            },
            {
                "capability_id": "test-command-runner",
                "category": "testing",
                "status": "requires_operator",
                "live_hook": False,
                "summary": "Test execution hooks remain operator-required and are not auto-fired.",
            },
            {
                "capability_id": "thread-send-hook",
                "category": "threading",
                "status": "dry_run_only",
                "live_hook": False,
                "summary": "Thread send path is modeled, but live dispatch remains dry-run only.",
            },
        ),
        "gaps": (
            {
                "gap_id": "git-runner-operator-gate",
                "category": "git",
                "status": "requires_operator",
                "summary": "Git command execution still depends on explicit operator approval.",
                "next_safe_action": "Maintain operator approval for git actions until a safe audited command runner exists.",
            },
            {
                "gap_id": "scheduler-live-execution",
                "category": "scheduler",
                "status": "requires_operator",
                "summary": "Heartbeat scheduling is intentionally blocked from unattended execution.",
                "next_safe_action": "Keep scheduler/heartbeat execution operator-controlled and in dry-run mode for now.",
            },
            {
                "gap_id": "test-runner-operator-gate",
                "category": "testing",
                "status": "requires_operator",
                "summary": "Automated test command execution is not yet live-safe for unattended orchestration.",
                "next_safe_action": "Run tests only through explicit operator flows until sandboxed live hooks are approved.",
            },
            {
                "gap_id": "thread-send-live-hook",
                "category": "threading",
                "status": "dry_run_only",
                "summary": "Thread send integration is modeled only and not safe for unattended live sends yet.",
                "next_safe_action": "Keep thread dispatch in dry-run mode and require operator confirmation before live sends.",
            },
        ),
    }
