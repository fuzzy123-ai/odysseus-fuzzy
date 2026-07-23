import pytest

from src.interactive_deliverable_policy import (
    InteractiveDeliverablePolicyError,
    InteractiveDeliverableTarget,
    decide_interactive_deliverable,
)


@pytest.mark.parametrize(
    "request_text",
    [
        "Build a Pygame platformer and give me the Python file.",
        "Erstelle ein natives Desktop-Spiel zum Herunterladen.",
        "Bitte eine GUI als herunterladbare Python-Datei bauen.",
    ],
)
def test_explicit_native_requests_choose_native_download(request_text):
    decision = decide_interactive_deliverable(request_text)

    assert decision.target == InteractiveDeliverableTarget.NATIVE_DOWNLOAD
    assert decision.native_requested is True
    assert decision.browser_requested is False


@pytest.mark.parametrize(
    "request_text",
    [
        "Make a game I can play here.",
        "Baue das Spiel so, dass ich es im Browser testen kann.",
        "Create a maze and send a playable link.",
        "Erstelle ein Puzzle mit Testlink.",
    ],
)
def test_explicit_browser_requests_choose_browser_preview(request_text):
    decision = decide_interactive_deliverable(request_text)

    assert decision.target == InteractiveDeliverableTarget.BROWSER_PREVIEW
    assert decision.browser_requested is True


def test_native_and_browser_requests_choose_dual_delivery():
    decision = decide_interactive_deliverable(
        "Build the Pygame download and a browser version I can play here."
    )

    assert decision.target == InteractiveDeliverableTarget.DUAL
    assert decision.native_requested is True
    assert decision.browser_requested is True
    assert "dual_delivery_required" in decision.reason_codes


@pytest.mark.parametrize(
    "request_text",
    [
        "Create an interactive platformer.",
        "Mach mir ein spielbares Jump and Run.",
    ],
)
def test_ambiguous_interactive_game_defaults_to_browser_preview(request_text):
    decision = decide_interactive_deliverable(request_text)

    assert decision.target == InteractiveDeliverableTarget.BROWSER_PREVIEW
    assert decision.browser_requested is False
    assert "ambiguous_interactive_defaults_to_browser" in decision.reason_codes


def test_download_link_does_not_turn_pygame_request_into_dual_delivery():
    decision = decide_interactive_deliverable(
        "Create a Pygame game and provide a download link for the .py file."
    )

    assert decision.target == InteractiveDeliverableTarget.NATIVE_DOWNLOAD


def test_negated_native_target_is_not_selected():
    decision = decide_interactive_deliverable(
        "Do not use Pygame; make a browser game I can play here."
    )

    assert decision.target == InteractiveDeliverableTarget.BROWSER_PREVIEW
    assert decision.native_requested is False


def test_unrelated_request_is_not_classified_as_an_interactive_deliverable():
    decision = decide_interactive_deliverable("Summarize this meeting note.")

    assert decision.target == InteractiveDeliverableTarget.NOT_APPLICABLE


def test_audit_summary_is_bounded_and_does_not_persist_raw_prompt():
    secret_value = "super-private-value"
    decision = decide_interactive_deliverable(
        f"Create a Pygame game; token={secret_value}."
    )
    payload = decision.audit_summary()
    encoded = repr(payload)

    assert payload["raw_prompt_visible"] is False
    assert payload["raw_content_visible"] is False
    assert secret_value not in encoded
    assert "Create a Pygame" not in encoded
    assert len(payload["reason_codes"]) <= 8


def test_empty_and_unbounded_requests_are_rejected():
    with pytest.raises(InteractiveDeliverablePolicyError):
        decide_interactive_deliverable("  ")
    with pytest.raises(InteractiveDeliverablePolicyError):
        decide_interactive_deliverable("x" * 12_001)
