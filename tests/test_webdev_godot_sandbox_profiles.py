from src.agent_sandbox_contract import (
    SandboxContractError,
    SandboxJobRequest,
    evaluate_sandbox_job,
    list_sandbox_capability_profiles,
)
from src.sandbox_job_templates import build_sandbox_job_from_template, list_sandbox_job_template_specs


def _profiles() -> dict[str, dict]:
    return {profile["profile_id"]: profile for profile in list_sandbox_capability_profiles()}


def test_webdev_playwright_profile_has_acceptance_and_artifact_policy() -> None:
    profile = _profiles()["webdev_playwright"]

    assert profile["status"] == "available"
    assert profile["acceptance_flow"] == ("node_check", "browser_smoke")
    assert profile["artifact_policy"]["screenshot_artifacts"] is True
    assert profile["artifact_policy"]["artifact_integrity_required"] is True
    assert profile["network_allowlist_gate_required"] is True
    assert profile["default_network_mode"] == "none"
    assert profile["secrets_allowed"] is False
    assert profile["fullweb_allowed"] is False


def test_browser_smoke_template_records_artifact_integrity_without_network() -> None:
    specs = {spec["template_id"]: spec for spec in list_sandbox_job_template_specs()}
    spec = specs["browser_smoke"]
    job = build_sandbox_job_from_template("browser_smoke", job_id="browser_smoke")
    decision = evaluate_sandbox_job(job)

    assert spec["network_mode"] == "none"
    assert spec["artifact_policy"]["integrity_required"] is True
    assert "screenshot" in spec["artifact_policy"]["expected_artifacts"]
    assert job.network_mode == "none"
    assert job.secrets_attached is False
    assert decision.allowed is True
    assert "write_mount_requires_scope_review" in decision.warnings


def test_webdev_fullweb_still_requires_separate_gate() -> None:
    try:
        SandboxJobRequest.create(
            job_id="webdev_fullweb",
            argv=("node", "scripts/browser-smoke.js"),
            image="localhost/odysseus_odysseus:latest",
            network_mode="fullweb",
            capabilities=("node", "playwright", "browser_gui"),
        )
    except SandboxContractError as exc:
        assert "fullweb network requires a separate live gate" in str(exc)
    else:  # pragma: no cover - defensive assertion for future policy drift
        raise AssertionError("fullweb webdev job should be blocked")


def test_godot_profile_is_planned_read_only_and_extension_bounded() -> None:
    profile = _profiles()["godot"]

    assert profile["status"] == "planned"
    assert profile["default_template_ids"] == ()
    assert profile["acceptance_flow"] == ("godot_headless_smoke", "screenshot_artifact_review")
    assert profile["test_command_shape"] == ("godot", "--headless", "--path", "<project>", "--quit-after", "<seconds>")
    assert {".gd", ".tscn", ".godot"}.issubset(set(profile["allowed_extensions"]))
    assert profile["artifact_policy"]["artifact_integrity_required"] is True
    assert profile["write_mount_default"] == "ro"
    assert profile["write_action_enabled"] is False
    assert profile["network_modes_allowed"] == ("none",)
    assert profile["live_execution_gated"] is True
