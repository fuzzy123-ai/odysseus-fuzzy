from src.agent_live_watch_contract import LiveWatchPlan


def test_live_watch_plan_never_requires_gpu_or_raw_video_to_model():
    plan = LiveWatchPlan.create(
        session_id="watch1",
        sample_interval_ms=10,
        max_frames=10000,
        headed_browser=True,
        novnc_enabled=True,
    )

    payload = plan.to_dict()

    assert payload["sample_interval_ms"] == 250
    assert payload["max_frames"] == 600
    assert payload["gpu_required"] is False
    assert payload["raw_video_to_model"] is False
