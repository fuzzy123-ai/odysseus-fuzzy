"""Redacted preference export preparation for later DPO/GRPO gates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.model_reward_contract import ModelEpisode


MODEL_PREFERENCE_EXPORT_SCHEMA = "odysseus.model_preference_export.v1"


class ModelPreferenceExportError(ValueError):
    """Raised when a preference export would expose unsafe data."""


@dataclass(frozen=True, slots=True)
class RedactedPreferencePair:
    pair_id: str
    task_type: str
    winner_model: str
    loser_model: str
    winner_score: int
    loser_score: int
    synthetic_or_redacted: bool = True

    def to_record(self) -> dict[str, Any]:
        return {
            "schema": MODEL_PREFERENCE_EXPORT_SCHEMA,
            "pair_id": self.pair_id,
            "task_type": self.task_type,
            "winner_model": self.winner_model,
            "loser_model": self.loser_model,
            "winner_score": self.winner_score,
            "loser_score": self.loser_score,
            "synthetic_or_redacted": self.synthetic_or_redacted,
            "raw_prompt_visible": False,
            "raw_output_visible": False,
            "private_content_visible": False,
        }


def build_redacted_preference_pair(
    *,
    pair_id: str,
    winner: ModelEpisode,
    loser: ModelEpisode,
    synthetic_or_redacted: bool = True,
) -> RedactedPreferencePair:
    if not synthetic_or_redacted:
        raise ModelPreferenceExportError("real preference exports require a separate live/privacy gate")
    if winner.reward is None or loser.reward is None:
        raise ModelPreferenceExportError("preference pair requires scored episodes")
    winner_record = winner.to_record()
    loser_record = loser.to_record()
    if winner_record.get("raw_prompt_visible") or loser_record.get("raw_prompt_visible"):
        raise ModelPreferenceExportError("raw prompts must not be visible")
    if winner.reward.total_score <= loser.reward.total_score:
        raise ModelPreferenceExportError("winner score must be greater than loser score")
    return RedactedPreferencePair(
        pair_id=_label(pair_id),
        task_type=winner.state.task_type,
        winner_model=winner.action.model,
        loser_model=loser.action.model,
        winner_score=winner.reward.total_score,
        loser_score=loser.reward.total_score,
        synthetic_or_redacted=True,
    )


def _label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ModelPreferenceExportError("label must not be empty")
    if any(marker in text.lower() for marker in ("authorization", "bearer ", "api_key", "token", "secret", "password")):
        raise ModelPreferenceExportError("label contains forbidden secret marker")
    return text[:120]
