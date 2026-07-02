"""Contract for sampled frame observation and optional human watch mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LiveWatchPlan:
    session_id: str
    sample_interval_ms: int
    max_frames: int
    headed_browser: bool
    novnc_enabled: bool
    gpu_required: bool = False
    raw_video_to_model: bool = False

    @classmethod
    def create(
        cls,
        *,
        session_id: Any,
        sample_interval_ms: Any = 1000,
        max_frames: Any = 60,
        headed_browser: bool = False,
        novnc_enabled: bool = False,
    ) -> "LiveWatchPlan":
        interval = max(250, min(10_000, int(sample_interval_ms or 1000)))
        frames = max(1, min(600, int(max_frames or 60)))
        return cls(
            session_id=str(session_id or "session")[:80],
            sample_interval_ms=interval,
            max_frames=frames,
            headed_browser=bool(headed_browser),
            novnc_enabled=bool(novnc_enabled),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.agent.live_watch_plan.v1",
            "session_id": self.session_id,
            "sample_interval_ms": self.sample_interval_ms,
            "max_frames": self.max_frames,
            "headed_browser": self.headed_browser,
            "novnc_enabled": self.novnc_enabled,
            "gpu_required": False,
            "raw_video_to_model": False,
        }
