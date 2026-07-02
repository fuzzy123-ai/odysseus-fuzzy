from src.visual_frame_sampler import build_frame_sampling_plan, dedupe_sampled_frames


def test_frame_sampling_plan_needs_no_gpu_or_video_stream():
    plan = build_frame_sampling_plan(max_duration_ms=1000, max_frames=3)

    assert plan["gpu_required"] is False
    assert plan["continuous_video_required"] is False


def test_frame_sampler_dedupes_by_perceptual_hash_distance():
    frames = dedupe_sampled_frames(
        [
            {"artifact_ref": "reports/f1.png", "timestamp_ms": 0, "perceptual_hash": "aaaaaaaa"},
            {"artifact_ref": "reports/f2.png", "timestamp_ms": 100, "perceptual_hash": "aaaaaaab"},
            {"artifact_ref": "reports/f3.png", "timestamp_ms": 200, "perceptual_hash": "bbbbbbbb"},
        ],
        min_delta_hash_distance=2,
    )

    assert [frame["artifact_ref"] for frame in frames] == ["reports/f1.png", "reports/f3.png"]
