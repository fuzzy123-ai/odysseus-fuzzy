import pytest

from src.interactive_runtime_policy import (
    InteractiveRuntimeKind,
    InteractiveRuntimePolicyError,
    classify_interactive_runtime,
)


def test_native_gui_launch_is_classified_and_not_run_on_server():
    decision = classify_interactive_runtime("python mario_game.py")

    assert decision.kind == InteractiveRuntimeKind.INTERACTIVE_NATIVE_GUI_LAUNCH
    assert decision.permitted is False
    assert decision.headless is False
    assert decision.recommended_next_action == "publish_native_download_or_build_browser_preview"


@pytest.mark.parametrize(
    "command",
    [
        "SDL_VIDEODRIVER=dummy python mario_game.py --capture start.png",
        "$env:SDL_VIDEODRIVER='dummy'; .\\.venv\\Scripts\\python.exe .\\mario_game.py",
        "set SDL_VIDEODRIVER=dummy && py mario_game.py",
        "python -c \"import os; os.environ['SDL_VIDEODRIVER']='dummy'; import pygame; pygame.display.set_mode((1,1))\"",
    ],
)
def test_sdl_dummy_launch_is_permitted_as_headless_capture(command):
    decision = classify_interactive_runtime(command)

    assert decision.kind == InteractiveRuntimeKind.HEADLESS_CAPTURE
    assert decision.permitted is True
    assert decision.headless is True
    assert "sdl_dummy_video_driver" in decision.reason_codes


@pytest.mark.parametrize(
    "command",
    [
        "python -m pip show pygame",
        "python -c \"import pygame; print(pygame.version.ver)\"",
        "Get-Command python",
    ],
)
def test_dependency_probes_are_permitted_without_installing(command):
    decision = classify_interactive_runtime(command)

    assert decision.kind == InteractiveRuntimeKind.DEPENDENCY_PROBE
    assert decision.permitted is True
    assert decision.install_detected is False


@pytest.mark.parametrize(
    "command",
    [
        "python -m pip install pygame-ce",
        "sudo apt-get install libsdl2-dev",
        "winget install Python.Python.3.12",
    ],
)
def test_dependency_installs_require_a_separate_gate(command):
    decision = classify_interactive_runtime(command)

    assert decision.kind == InteractiveRuntimeKind.RISKY_INSTALL
    assert decision.permitted is False
    assert decision.requires_separate_gate is True


@pytest.mark.parametrize(
    "command",
    [
        "python -m pip install pygame-ce | tail -n 2",
        "python mario_game.py || true",
        "python mario_game.py; exit 0",
        "python -m pip show pygame | Select-String Version",
    ],
)
def test_adversarial_shell_masking_is_rejected(command):
    decision = classify_interactive_runtime(command)

    assert decision.kind == InteractiveRuntimeKind.PIPELINE_MASKING
    assert decision.permitted is False
    assert decision.pipeline_masking_detected is True
    assert decision.recommended_next_action == "rewrite_command_to_preserve_exit_status"


def test_pipefail_preserves_pipeline_status_but_install_remains_gated():
    decision = classify_interactive_runtime(
        "set -o pipefail; python -m pip install pygame-ce | tail -n 2"
    )

    assert decision.kind == InteractiveRuntimeKind.RISKY_INSTALL
    assert decision.pipeline_masking_detected is False
    assert decision.requires_separate_gate is True


def test_echoing_dummy_driver_does_not_count_as_headless_execution():
    decision = classify_interactive_runtime(
        'echo "SDL_VIDEODRIVER=dummy"; python mario_game.py'
    )

    assert decision.kind == InteractiveRuntimeKind.INTERACTIVE_NATIVE_GUI_LAUNCH
    assert decision.headless is False


def test_native_hint_handles_neutral_script_names():
    decision = classify_interactive_runtime("python main.py", native_gui_hint=True)

    assert decision.kind == InteractiveRuntimeKind.INTERACTIVE_NATIVE_GUI_LAUNCH


def test_runtime_audit_does_not_persist_raw_command_or_secret_values():
    secret_value = "private-token-value"
    decision = classify_interactive_runtime(
        f"python mario_game.py --token {secret_value}"
    )
    payload = decision.audit_summary()
    encoded = repr(payload)

    assert payload["raw_command_visible"] is False
    assert payload["raw_content_visible"] is False
    assert secret_value not in encoded
    assert "mario_game.py" not in encoded
    assert len(payload["reason_codes"]) <= 10


def test_empty_and_unbounded_commands_are_rejected():
    with pytest.raises(InteractiveRuntimePolicyError):
        classify_interactive_runtime("  ")
    with pytest.raises(InteractiveRuntimePolicyError):
        classify_interactive_runtime("x" * 16_001)
