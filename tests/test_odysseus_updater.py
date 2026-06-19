import sys
import types

from src.odysseus_updater import build_odysseus_updater


def _build_happy_bundle(**overrides):
    kwargs = {
        "plan_input": {
            "source_ref": "origin/main",
            "current_ref": "codex/updater-offline",
            "target_ref": "origin/release",
            "reason": "offline updater bundle review",
            "risk_level": "medium",
            "required_gates": (
                {
                    "gate_id": "scope_confirmed",
                    "status": "pass",
                    "summary": "slice stays inside the approved offline updater scope",
                },
                {
                    "gate_id": "offline_slice_confirmed",
                    "status": "pass",
                    "summary": "bundle only uses structured offline inputs",
                },
                {
                    "gate_id": "tests_defined",
                    "status": "pass",
                    "summary": "offline updater test scope is defined",
                },
            ),
            "optional_gates": (
                {
                    "gate_id": "audit_report_ready",
                    "status": "waived",
                    "summary": "audit summary is optional for this worker slice",
                },
            ),
            "planned_commands": (
                {
                    "argv": ("python", "-m", "pytest", "tests/test_odysseus_updater.py"),
                    "summary": "review the focused offline updater test command",
                },
            ),
        },
        "preflight_input": {
            "worktree_snapshot": {
                "dirty": False,
                "staged_files": (),
                "allowed_staged_files": (),
                "hotfile_conflict": False,
            },
            "branch_snapshot": {
                "current_branch": "codex/updater-offline",
                "expected_branch": "codex/updater-offline",
                "branch_candidates": ("codex/updater-offline",),
                "detached": False,
                "ahead": 0,
                "behind": 0,
            },
            "env_snapshot": {
                "required_names": ("ODYSSEUS_ENV",),
                "present_names": ("ODYSSEUS_ENV",),
            },
            "backup_snapshot": {
                "mount_ready": True,
            },
        },
        "backup_gate_input": {
            "risk_level": "medium",
            "evaluated_at": "2026-06-19T00:00:00Z",
            "evidence_inputs": (
                {
                    "evidence_id": "pre_update_snapshot",
                    "state": "green",
                    "result_label": "pass",
                    "checked_at": "2026-06-19T00:00:00Z",
                    "summary": "pre-update snapshot evidence is present in the offline packet",
                },
                {
                    "evidence_id": "repository_check",
                    "state": "green",
                    "result_label": "pass",
                    "checked_at": "2026-06-19T00:00:00Z",
                    "summary": "repository review evidence is present in the offline packet",
                },
                {
                    "evidence_id": "restore_smoke",
                    "state": "green",
                    "result_label": "pass",
                    "checked_at": "2026-06-19T00:00:00Z",
                    "summary": "restore smoke evidence is present in the offline packet",
                },
            ),
        },
        "test_gate_input": {
            "allowed_suites": (
                {
                    "suite_id": "updater_bundle_pytest",
                    "required": True,
                    "timeout_seconds": 300,
                    "summary": "focused offline updater pytest slice",
                },
            ),
            "result_snapshots": (
                {
                    "suite_id": "updater_bundle_pytest",
                    "execution_status": "completed",
                    "result_label": "pass",
                    "summary": "focused offline updater pytest snapshot passed",
                    "observed_duration_seconds": 12,
                },
            ),
        },
        "command_plan_inputs": (
            {
                "plan_type": "focused_pytest",
                "focus_label": "tests/test_odysseus_updater.py",
                "note": "review-only command plan for the updater slice",
            },
        ),
        "include_audit_summary": False,
    }
    kwargs.update(overrides)
    return build_odysseus_updater(**kwargs)


def test_default_builder_is_dry_run_and_deferred():
    bundle = build_odysseus_updater()

    assert bundle.mode == "dry_run"
    assert bundle.decision == "deferred"
    assert bundle.live_update_decision == "no_go"
    assert bundle.live_execution_blocked is True
    assert bundle.audit_summary is None


def test_bundle_composes_offline_models_without_live_go():
    bundle = _build_happy_bundle()

    assert bundle.decision == "go"
    assert bundle.live_update_decision == "no_go"
    assert bundle.component_decisions["plan"] == "go"
    assert bundle.component_decisions["preflight"] == "go"
    assert bundle.component_decisions["backup_gate"] == "go"
    assert bundle.component_decisions["test_gate"] == "go"

    payload = bundle.to_dict()

    assert payload["mode"] == "dry_run"
    assert payload["decision"] == "go"
    assert payload["live_update_decision"] == "no_go"
    assert payload["audit_summary_status"] == "omitted"
    assert payload["command_plans"][0]["dry_run_label"] == "plan_only"

    markdown = bundle.to_markdown()
    assert "# Odysseus Offline Updater Bundle" in markdown
    assert "Live Update Decision: `no_go`" in markdown
    assert "not a live-go signal" in markdown


def test_blockers_drive_bundle_to_no_go():
    bundle = _build_happy_bundle(
        preflight_input={
            "worktree_snapshot": {
                "dirty": False,
                "staged_files": ("src/other_file.py",),
                "allowed_staged_files": ("src/odysseus_updater.py",),
                "hotfile_conflict": False,
            },
            "branch_snapshot": {
                "current_branch": "codex/updater-offline",
                "expected_branch": "codex/updater-offline",
                "branch_candidates": ("codex/updater-offline",),
                "detached": False,
                "ahead": 0,
                "behind": 0,
            },
            "env_snapshot": {
                "required_names": ("ODYSSEUS_ENV",),
                "present_names": ("ODYSSEUS_ENV",),
            },
            "backup_snapshot": {
                "mount_ready": True,
            },
        }
    )

    assert bundle.decision == "no_go"
    assert bundle.component_decisions["preflight"] == "no_go"
    assert any("foreign staged files" in item for item in bundle.next_actions)


def test_optional_audit_summary_is_loaded_defensively_when_available():
    module = types.ModuleType("src.odysseus_updater_audit_summary")

    class FakeAuditSummary:
        def to_dict(self):
            return {
                "status": "go",
                "summary": "optional audit snapshot is attached offline",
            }

    module.build_odysseus_updater_audit_summary = lambda: FakeAuditSummary()
    sys.modules[module.__name__] = module
    try:
        bundle = _build_happy_bundle(include_audit_summary=True)
    finally:
        sys.modules.pop(module.__name__, None)

    assert bundle.audit_summary_status == "included"
    assert bundle.audit_summary == {
        "status": "go",
        "summary": "optional audit snapshot is attached offline",
    }
