import json

import pytest

from src.pygame_headless_contract import (
    MAX_FRAME_COUNT,
    MAX_SCREENSHOT_BYTES,
    MAX_TIMEOUT_SECONDS,
    PYGAME_HEADLESS_CONTRACT_SCHEMA,
    PygameHeadlessContractError,
    build_pygame_headless_plan,
    evaluate_pygame_headless_evidence,
)


def test_valid_plan_describes_bounded_offline_dummy_sdl_checks_and_artifact():
    plan = build_pygame_headless_plan(
        source_ref="games/mario_game.py",
        screenshot_ref="artifacts/pygame/mario-start.png",
        max_frames=180,
        timeout_seconds=12,
        screenshot_frame=2,
    )

    public = plan.to_redacted_dict()

    assert public["schema"] == PYGAME_HEADLESS_CONTRACT_SCHEMA
    assert public["execution"] == {
        "mode": "planned_only",
        "processes_started": False,
        "network_mode": "none",
        "secrets_attached": False,
    }
    assert public["environment"] == {
        "SDL_VIDEODRIVER": "dummy",
        "SDL_AUDIODRIVER": "dummy",
    }
    assert [check["check_id"] for check in public["checks"]] == [
        "python_syntax_check",
        "pygame_import_probe",
        "bounded_dummy_sdl_frame_run",
        "screenshot_capture",
    ]
    assert public["limits"] == {
        "max_frames": 180,
        "timeout_seconds": 12,
        "screenshot_frame": 2,
    }
    assert public["artifacts"] == [
        {
            "artifact_id": "pygame_headless_screenshot",
            "kind": "screenshot",
            "ref": "artifacts/pygame/mario-start.png",
            "media_type": "image/png",
            "required": True,
            "max_bytes": 5_000_000,
            "digest_algorithm": "sha256",
            "digest_required": True,
            "content_embedded": False,
            "host_path_visible": False,
        }
    ]
    assert public["claim_semantics"]["headless_verified_does_not_imply_interactive_ready"] is True
    assert public["claim_semantics"]["interactive_ready"] is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_ref", "/tmp/game.py"),
        ("source_ref", "C:\\Users\\name\\game.py"),
        ("source_ref", "../game.py"),
        ("source_ref", "games/../../game.py"),
        ("source_ref", "https://example.org/game.py"),
        ("source_ref", "secrets/game.py"),
        ("screenshot_ref", "/tmp/screen.png"),
        ("screenshot_ref", "artifacts/../screen.png"),
        ("screenshot_ref", ".env/screen.png"),
    ],
)
def test_plan_rejects_absolute_traversing_remote_or_sensitive_refs(field, value):
    kwargs = {
        "source_ref": "games/game.py",
        "screenshot_ref": "artifacts/pygame/screen.png",
    }
    kwargs[field] = value

    with pytest.raises(PygameHeadlessContractError):
        build_pygame_headless_plan(**kwargs)


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_frames": 0},
        {"max_frames": MAX_FRAME_COUNT + 1},
        {"max_frames": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": MAX_TIMEOUT_SECONDS + 1},
        {"timeout_seconds": 1.5},
        {"max_frames": 5, "screenshot_frame": 6},
        {"screenshot_frame": 0},
        {"max_screenshot_bytes": 1_023},
        {"max_screenshot_bytes": MAX_SCREENSHOT_BYTES + 1},
    ],
)
def test_plan_rejects_unbounded_or_invalid_values(overrides):
    with pytest.raises(PygameHeadlessContractError):
        build_pygame_headless_plan(source_ref="games/game.py", **overrides)


def test_plan_rejects_secret_attachment_and_non_matching_file_types():
    with pytest.raises(PygameHeadlessContractError):
        build_pygame_headless_plan(source_ref="games/game.py", secrets_attached=True)
    with pytest.raises(PygameHeadlessContractError):
        build_pygame_headless_plan(source_ref="games/game.txt")
    with pytest.raises(PygameHeadlessContractError):
        build_pygame_headless_plan(
            source_ref="games/game.py",
            screenshot_ref="artifacts/pygame/screen.jpg",
        )


def test_headless_success_never_claims_interactive_readiness():
    status = evaluate_pygame_headless_evidence(
        syntax_check_passed=True,
        pygame_import_probe_passed=True,
        bounded_frame_run_passed=True,
        screenshot_artifact_recorded=True,
    ).to_redacted_dict()

    assert status["status"] == "headless_verified"
    assert status["headless_verified"] is True
    assert status["missing_evidence"] == []
    assert status["interactive_ready"] is False
    assert status["semantic_boundary"] == "headless_verified_does_not_imply_interactive_ready"
    assert status["interactive_ready_requires"] == "separate_visible_interactive_validation"


def test_missing_evidence_keeps_headless_status_unverified():
    status = evaluate_pygame_headless_evidence(
        syntax_check_passed=True,
        pygame_import_probe_passed=True,
        bounded_frame_run_passed=True,
        screenshot_artifact_recorded=False,
    ).to_redacted_dict()

    assert status["status"] == "headless_unverified"
    assert status["headless_verified"] is False
    assert status["missing_evidence"] == ["screenshot_artifact_recorded"]
    assert status["interactive_ready"] is False


def test_redacted_plan_is_deterministic_json_data_without_source_content():
    first = build_pygame_headless_plan(source_ref="games/game.py").to_redacted_dict()
    second = build_pygame_headless_plan(source_ref="games/game.py").to_redacted_dict()

    assert first == second
    assert json.loads(json.dumps(first, sort_keys=True)) == first
    assert first["source"] == {
        "ref": "games/game.py",
        "content_embedded": False,
        "host_path_visible": False,
    }
