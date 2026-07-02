"""Bounded frame sampling plan for no-GPU visual observation."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping


class VisualFrameSamplerError(ValueError):
    """Raised when frame sampling metadata is unsafe."""


def build_frame_sampling_plan(*, max_duration_ms: Any = 5000, max_frames: Any = 10, min_delta_hash_distance: Any = 4) -> dict[str, Any]:
    return {
        "schema": "odysseus.visual_frame_sampler.plan.v1",
        "max_duration_ms": _bounded_int(max_duration_ms, minimum=100, maximum=60000),
        "max_frames": _bounded_int(max_frames, minimum=1, maximum=120),
        "min_delta_hash_distance": _bounded_int(min_delta_hash_distance, minimum=0, maximum=64),
        "continuous_video_required": False,
        "gpu_required": False,
    }


def dedupe_sampled_frames(frames: Iterable[Mapping[str, Any]], *, min_delta_hash_distance: int = 4) -> tuple[dict[str, Any], ...]:
    threshold = _bounded_int(min_delta_hash_distance, minimum=0, maximum=64)
    result: list[dict[str, Any]] = []
    last_hash = ""
    for frame in frames:
        if not isinstance(frame, Mapping):
            raise VisualFrameSamplerError("frame must be a mapping")
        item = {
            "artifact_ref": _artifact_ref(frame.get("artifact_ref") or ""),
            "timestamp_ms": _bounded_int(frame.get("timestamp_ms") or 0, minimum=0, maximum=86_400_000),
            "perceptual_hash": _hash(frame.get("perceptual_hash") or ""),
            "raw_content_visible": False,
        }
        if last_hash and _hash_distance(last_hash, item["perceptual_hash"]) < threshold:
            continue
        result.append(item)
        last_hash = item["perceptual_hash"]
    return tuple(result)


def _hash_distance(left: str, right: str) -> int:
    return sum(1 for a, b in zip(left, right) if a != b) + abs(len(left) - len(right))


def _hash(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{8,64}", text):
        raise VisualFrameSamplerError("perceptual hash is invalid")
    return text


def _artifact_ref(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,180}", text) or ".." in text.split("/") or text.startswith("/"):
        raise VisualFrameSamplerError("artifact ref is unsafe")
    return text


def _bounded_int(value: Any, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise VisualFrameSamplerError("value must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise VisualFrameSamplerError("value out of range")
    return parsed
