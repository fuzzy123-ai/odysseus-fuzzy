from src.orchestration_activation_bundle import (
    build_current_orchestration_activation_bundle,
    build_orchestration_activation_bundle,
)
from src.orchestration_activation_bundle_digest import (
    digest_activation_bundle,
    render_activation_bundle_canonical_json,
)
from src.orchestration_operator_activation import OperatorActivationPolicy
from src.orchestration_runtime_readiness import RuntimeCapability, build_runtime_readiness_report


def test_digest_is_deterministic_for_same_bundle():
    bundle = build_current_orchestration_activation_bundle(
        label="Morning AUTO Bundle",
        generated_at="2026-06-17T12:30:00Z",
    )

    digest_a = digest_activation_bundle(bundle)
    digest_b = digest_activation_bundle(bundle)

    assert digest_a == digest_b
    assert len(digest_a) == 64


def test_digest_ignores_generated_at_by_default_but_full_digest_can_include_it():
    first = build_current_orchestration_activation_bundle(
        label="Morning AUTO Bundle",
        generated_at="2026-06-17T12:30:00Z",
    )
    second = build_current_orchestration_activation_bundle(
        label="Morning AUTO Bundle",
        generated_at="2026-06-17T12:45:00Z",
    )

    assert digest_activation_bundle(first) == digest_activation_bundle(second)
    assert digest_activation_bundle(first, include_generated_at=True) != digest_activation_bundle(
        second, include_generated_at=True
    )


def test_digest_changes_when_relevant_bundle_content_changes():
    current_bundle = build_current_orchestration_activation_bundle(
        label="Morning AUTO Bundle",
        generated_at="2026-06-17T12:30:00Z",
    )
    clean_report = build_runtime_readiness_report(
        capabilities=(
            RuntimeCapability.create(
                capability_id="registry-model",
                category="dashboard",
                status="ready",
                live_hook=False,
                summary="ready metadata",
            ),
        ),
        gaps=(),
    )
    clean_bundle = build_orchestration_activation_bundle(
        readiness_report=clean_report,
        policy=OperatorActivationPolicy.create(
            requested_mode="live_dispatch_limited",
            operator_approved=True,
            allow_live_dispatch=True,
        ),
        label="Clean AUTO Bundle",
        generated_at="2026-06-17T12:35:00Z",
    )

    assert digest_activation_bundle(current_bundle) != digest_activation_bundle(clean_bundle)


def test_canonical_json_snapshot_is_stable():
    bundle = build_current_orchestration_activation_bundle(
        label="Morning AUTO Bundle",
        generated_at="2026-06-17T12:30:00Z",
    )

    assert render_activation_bundle_canonical_json(bundle) == (
        '{"activation_plan":{"allowed_actions":[{"action":"prepare_dispatch_plan","decision":"prepare_only","reason":"Dispatch plans can be assembled without executing live hooks."},{"action":"prepare_mailbox_draft","decision":"prepare_only","reason":"Mailbox drafts can be prepared without sending."},{"action":"review_registry","decision":"allow","reason":"Registry review stays read-only and does not trigger hooks."},{"action":"view_dashboard","decision":"allow","reason":"Dashboard inspection remains read-only and safe."}],"blocked_actions":[{"action":"confirm_dispatch","decision":"block","reason":"Prepare-dispatch mode does not allow send confirmation."},{"action":"execute_live_dispatch","decision":"block","reason":"Prepare-dispatch mode never executes live dispatch."}],"mode":"prepare_dispatch","next_safe_action":"Keep scheduler/heartbeat execution operator-controlled and in dry-run mode for now.","ok":false,"open_gap_count":4},"label":"Morning AUTO Bundle","readiness_report":{"blocked":false,"capabilities":[{"capability_id":"git-command-runner","category":"git","live_hook":false,"status":"requires_operator","summary":"Git command runner is intentionally operator-gated and not live-wired."},{"capability_id":"registry-model","category":"dashboard","live_hook":false,"status":"ready","summary":"AUTO registry metadata and dashboard-facing models are prepared."},{"capability_id":"scheduler-heartbeat-execution","category":"scheduler","live_hook":false,"status":"dry_run_only","summary":"Heartbeat scheduler logic is prepared, but live execution stays dry-run only."},{"capability_id":"test-command-runner","category":"testing","live_hook":false,"status":"requires_operator","summary":"Test execution hooks remain operator-required and are not auto-fired."},{"capability_id":"thread-send-hook","category":"threading","live_hook":false,"status":"dry_run_only","summary":"Thread send path is modeled, but live dispatch remains dry-run only."}],"gaps":[{"category":"git","gap_id":"git-runner-operator-gate","next_safe_action":"Maintain operator approval for git actions until a safe audited command runner exists.","status":"requires_operator","summary":"Git command execution still depends on explicit operator approval."},{"category":"scheduler","gap_id":"scheduler-live-execution","next_safe_action":"Keep scheduler/heartbeat execution operator-controlled and in dry-run mode for now.","status":"requires_operator","summary":"Heartbeat scheduling is intentionally blocked from unattended execution."},{"category":"testing","gap_id":"test-runner-operator-gate","next_safe_action":"Run tests only through explicit operator flows until sandboxed live hooks are approved.","status":"requires_operator","summary":"Automated test command execution is not yet live-safe for unattended orchestration."},{"category":"threading","gap_id":"thread-send-live-hook","next_safe_action":"Keep thread dispatch in dry-run mode and require operator confirmation before live sends.","status":"dry_run_only","summary":"Thread send integration is modeled only and not safe for unattended live sends yet."}],"next_safe_action":"Keep scheduler/heartbeat execution operator-controlled and in dry-run mode for now.","ok":false,"open_gap_count":4},"summary":{"allowed_actions":["prepare_dispatch_plan","prepare_mailbox_draft","review_registry","view_dashboard"],"blocking_reasons":["Git command execution still depends on explicit operator approval.","Heartbeat scheduling is intentionally blocked from unattended execution.","Automated test command execution is not yet live-safe for unattended orchestration.","Thread send integration is modeled only and not safe for unattended live sends yet."],"live_dispatch_allowed":false,"mode":"prepare_dispatch","next_safe_action":"Keep scheduler/heartbeat execution operator-controlled and in dry-run mode for now.","open_gap_count":4,"operator_required":true,"status_label":"prepare_only"}}'
    )
