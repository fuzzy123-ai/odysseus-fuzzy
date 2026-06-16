from src.plugin_release_gate import PluginReleaseGate
from src.release_evidence_snapshot import (
    AUTOMATED,
    BLOCKED,
    MANUAL,
    PASS,
    PENDING,
    WARN,
    ReleaseGate,
    build_release_evidence_snapshot,
    default_1_0_release_gates,
)
from src.release_readiness_report import build_release_readiness_report


def test_default_readiness_report_preserves_external_no_go():
    snapshot = build_release_evidence_snapshot(default_1_0_release_gates())
    plugin_gate = PluginReleaseGate(
        ok=True,
        registry_ok=True,
        local_plugins_ok=True,
        registry_plugin_count=3,
        local_plugin_count=2,
    )

    report = build_release_readiness_report(snapshot, plugin_gate)

    assert report.status == "blocked"
    assert report.external_release_go is False
    assert report.blocking_reasons == (
        "release:manual_pending:provider-proof",
        "release:manual_pending:export-import-rebuild",
    )
    assert report.next_actions == ("complete_manual_release_evidence",)


def test_plugin_failure_blocks_otherwise_green_release():
    snapshot = build_release_evidence_snapshot(
        [
            ReleaseGate("auto", "Automated", AUTOMATED, PASS, ("pytest",)),
            ReleaseGate("manual", "Manual", MANUAL, PASS, ("manual log",)),
        ]
    )
    plugin_gate = PluginReleaseGate(
        ok=False,
        registry_ok=False,
        local_plugins_ok=True,
        registry_plugin_count=0,
        local_plugin_count=1,
        errors=("registry:plugins[0].download:download_not_https",),
    )

    report = build_release_readiness_report(snapshot, plugin_gate)

    assert report.status == "blocked"
    assert report.external_release_go is False
    assert report.blocking_reasons == ("plugin:registry:plugins[0].download:download_not_https",)
    assert report.next_actions == ("fix_plugin_release_gate",)


def test_all_green_report_prepares_external_review():
    snapshot = build_release_evidence_snapshot(
        [
            ReleaseGate("auto", "Automated", AUTOMATED, PASS, ("pytest",)),
            ReleaseGate("manual", "Manual", MANUAL, PASS, ("manual log",)),
        ]
    )

    report = build_release_readiness_report(snapshot)

    assert report.status == "go"
    assert report.external_release_go is True
    assert report.next_actions == ("prepare_external_release_review",)


def test_warnings_do_not_block_green_release():
    snapshot = build_release_evidence_snapshot(
        [
            ReleaseGate("auto", "Automated", AUTOMATED, PASS, ("pytest",)),
            ReleaseGate("warn", "Warning", AUTOMATED, WARN, required_for_external_release=False),
            ReleaseGate("manual", "Manual", MANUAL, PASS, ("manual log",)),
        ]
    )

    report = build_release_readiness_report(snapshot)

    assert report.status == "go_with_warnings"
    assert report.external_release_go is True
    assert report.warnings == ("release:warn",)


def test_blocking_release_gate_is_reported_before_plugin_actions():
    snapshot = build_release_evidence_snapshot(
        [
            ReleaseGate("auto", "Automated", AUTOMATED, BLOCKED, risk="red tests"),
            ReleaseGate("manual", "Manual", MANUAL, PENDING, risk="needs proof"),
        ]
    )
    plugin_gate = PluginReleaseGate(
        ok=False,
        registry_ok=True,
        local_plugins_ok=False,
        registry_plugin_count=1,
        local_plugin_count=1,
        errors=("local:bad:missing_manifest",),
    )

    report = build_release_readiness_report(snapshot, plugin_gate)

    assert report.status == "blocked"
    assert report.blocking_reasons == (
        "release:blocking:auto",
        "release:manual_pending:manual",
        "plugin:local:bad:missing_manifest",
    )
    assert report.next_actions == (
        "complete_manual_release_evidence",
        "fix_blocking_release_gates",
        "fix_plugin_release_gate",
    )


def test_report_to_dict_is_stable():
    snapshot = build_release_evidence_snapshot(
        [
            ReleaseGate("auto", "Automated", AUTOMATED, PASS, ("pytest",)),
            ReleaseGate("manual", "Manual", MANUAL, PASS, ("manual log",)),
        ]
    )

    report = build_release_readiness_report(snapshot)

    assert report.to_dict() == {
        "status": "go",
        "external_release_go": True,
        "release_gate_status": "go",
        "plugin_gate_ok": True,
        "blocking_reasons": (),
        "warnings": (),
        "next_actions": ("prepare_external_release_review",),
    }
