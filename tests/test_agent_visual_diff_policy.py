from src.agent_visual_diff_policy import decide_visual_diff


def test_visual_diff_policy_flags_console_errors_first():
    decision = decide_visual_diff(pixel_delta_ratio=0.0, console_error_delta=1)

    assert decision.verdict == "failed"
    assert decision.reason == "new_console_errors"


def test_visual_diff_policy_flags_no_observable_expected_change():
    decision = decide_visual_diff(pixel_delta_ratio=0.0, dom_changed_nodes=0, expected_change=True)

    assert decision.verdict == "warning"
    assert decision.reason == "no_observable_change"


def test_visual_diff_policy_passes_expected_change():
    decision = decide_visual_diff(pixel_delta_ratio=0.1, dom_changed_nodes=4, expected_change=True)

    assert decision.verdict == "passed"
