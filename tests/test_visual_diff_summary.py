from src.visual_diff_summary import build_visual_diff_summary


def test_visual_diff_summary_uses_existing_policy():
    payload = build_visual_diff_summary(
        before={"artifact_ref": "reports/before.png"},
        after={"artifact_ref": "reports/after.png"},
        pixel_delta_ratio=0.0,
        dom_changed_nodes=0,
        expected_change=True,
    )

    assert payload["verdict"] == "warning"
    assert payload["reason"] == "no_observable_change"
    assert payload["raw_content_visible"] is False
