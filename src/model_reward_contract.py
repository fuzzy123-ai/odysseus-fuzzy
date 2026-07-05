"""Redacted RL-lite reward contracts for model-routing episodes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Mapping


MODEL_REWARD_CONTRACT_SCHEMA = "odysseus.model_reward_contract.v1"

_MAX_LABEL = 96
_MAX_REASON = 160
_MAX_TUPLE = 16
_SAFE_LABEL_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{0,96}$")
_SECRET_MARKERS = (
    "authorization",
    "bearer ",
    "api_key",
    "token",
    "secret",
    "password",
    "cookie",
    "chat_id",
    "private key",
)
_RAW_FIELD_MARKERS = (
    "prompt",
    "output",
    "raw",
    "snippet",
    "document_text",
    "message_content",
)
_ALLOWED_RAW_MARKER_KEYS = {
    "prompt_template_id",
    "raw_prompt_visible",
    "raw_output_visible",
}


class ModelRewardContractError(ValueError):
    """Raised when an RL-lite reward contract would persist unsafe data."""


class EpisodeOutcomeStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"
    FALLBACK = "fallback"
    UNKNOWN = "unknown"


class RewardStatus(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class ModelEpisodeState:
    surface: str
    task_type: str
    owner_label: str
    sensitivity_flags: tuple[str, ...] = ()
    retrieval_doc_count: int = 0
    citation_required: bool = False
    local_only_required: bool = False
    context_budget_tokens: int = 0

    @classmethod
    def create(cls, **kwargs: Any) -> "ModelEpisodeState":
        return cls(
            surface=_safe_label(kwargs.get("surface") or "unknown", field="surface"),
            task_type=_safe_label(kwargs.get("task_type") or "unknown", field="task_type"),
            owner_label=_safe_label(kwargs.get("owner_label") or "unknown", field="owner_label"),
            sensitivity_flags=_safe_tuple(kwargs.get("sensitivity_flags"), field="sensitivity_flags"),
            retrieval_doc_count=_safe_nonnegative_int(kwargs.get("retrieval_doc_count"), field="retrieval_doc_count"),
            citation_required=bool(kwargs.get("citation_required", False)),
            local_only_required=bool(kwargs.get("local_only_required", False)),
            context_budget_tokens=_safe_nonnegative_int(kwargs.get("context_budget_tokens"), field="context_budget_tokens"),
        )


@dataclass(frozen=True, slots=True)
class ModelEpisodeAction:
    answer_mode: str
    provider: str
    model: str
    endpoint_ref: str
    prompt_template_id: str
    retrieval_depth: int = 0
    max_tokens: int = 0

    @classmethod
    def create(cls, **kwargs: Any) -> "ModelEpisodeAction":
        return cls(
            answer_mode=_safe_label(kwargs.get("answer_mode") or "unknown", field="answer_mode"),
            provider=_safe_label(kwargs.get("provider") or "unknown", field="provider"),
            model=_safe_label(kwargs.get("model") or "unknown", field="model"),
            endpoint_ref=_safe_ref(kwargs.get("endpoint_ref") or kwargs.get("endpoint_hash") or "", field="endpoint_ref"),
            prompt_template_id=_safe_label(kwargs.get("prompt_template_id") or "unknown", field="prompt_template_id"),
            retrieval_depth=_safe_nonnegative_int(kwargs.get("retrieval_depth"), field="retrieval_depth"),
            max_tokens=_safe_nonnegative_int(kwargs.get("max_tokens"), field="max_tokens"),
        )


@dataclass(frozen=True, slots=True)
class ModelEpisodeOutcome:
    status: EpisodeOutcomeStatus
    duration_ms: int = 0
    citation_count: int = 0
    fallback_reason: str = ""
    warning_codes: tuple[str, ...] = ()
    confidence: float = 0.0
    verifier_refs: tuple[str, ...] = ()

    @classmethod
    def create(cls, **kwargs: Any) -> "ModelEpisodeOutcome":
        return cls(
            status=_status(kwargs.get("status")),
            duration_ms=_safe_nonnegative_int(kwargs.get("duration_ms"), field="duration_ms"),
            citation_count=_safe_nonnegative_int(kwargs.get("citation_count"), field="citation_count"),
            fallback_reason=_safe_reason(kwargs.get("fallback_reason") or "", field="fallback_reason"),
            warning_codes=_safe_tuple(kwargs.get("warning_codes") or kwargs.get("warnings"), field="warning_codes"),
            confidence=_safe_ratio(kwargs.get("confidence"), field="confidence"),
            verifier_refs=_safe_tuple(kwargs.get("verifier_refs"), field="verifier_refs"),
        )


@dataclass(frozen=True, slots=True)
class ModelReward:
    total_score: int
    component_scores: Mapping[str, int]
    status: RewardStatus
    reason_codes: tuple[str, ...] = ()

    @classmethod
    def create(cls, **kwargs: Any) -> "ModelReward":
        components = dict(kwargs.get("component_scores") or {})
        safe_components = {
            _safe_label(key, field="component_name"): _safe_score(value, field=f"component:{key}")
            for key, value in components.items()
        }
        score = _safe_score(kwargs.get("total_score"), field="total_score")
        try:
            status = RewardStatus(str(kwargs.get("status") or _status_from_score(score)))
        except ValueError as exc:
            raise ModelRewardContractError("reward status is not supported") from exc
        return cls(
            total_score=score,
            component_scores=safe_components,
            status=status,
            reason_codes=_safe_tuple(kwargs.get("reason_codes"), field="reason_codes"),
        )


@dataclass(frozen=True, slots=True)
class ModelEpisode:
    state: ModelEpisodeState
    action: ModelEpisodeAction
    outcome: ModelEpisodeOutcome
    reward: ModelReward | None = None

    @classmethod
    def create(
        cls,
        *,
        state: ModelEpisodeState,
        action: ModelEpisodeAction,
        outcome: ModelEpisodeOutcome,
        reward: ModelReward | None = None,
    ) -> "ModelEpisode":
        if not isinstance(state, ModelEpisodeState):
            raise ModelRewardContractError("state must be a ModelEpisodeState")
        if not isinstance(action, ModelEpisodeAction):
            raise ModelRewardContractError("action must be a ModelEpisodeAction")
        if not isinstance(outcome, ModelEpisodeOutcome):
            raise ModelRewardContractError("outcome must be a ModelEpisodeOutcome")
        if reward is not None and not isinstance(reward, ModelReward):
            raise ModelRewardContractError("reward must be a ModelReward")
        episode = cls(state=state, action=action, outcome=outcome, reward=reward)
        _reject_forbidden_payload(episode.to_record())
        return episode

    def to_record(self) -> dict[str, Any]:
        payload = {
            "schema": MODEL_REWARD_CONTRACT_SCHEMA,
            "state": asdict(self.state),
            "action": asdict(self.action),
            "outcome": _enum_values(asdict(self.outcome)),
            "reward": _enum_values(asdict(self.reward)) if self.reward else None,
            "raw_prompt_visible": False,
            "raw_output_visible": False,
            "private_content_visible": False,
        }
        _reject_forbidden_payload(payload)
        return payload

    def audit_summary(self) -> dict[str, Any]:
        return {
            "schema": MODEL_REWARD_CONTRACT_SCHEMA,
            "surface": self.state.surface,
            "task_type": self.state.task_type,
            "answer_mode": self.action.answer_mode,
            "provider": self.action.provider,
            "model": self.action.model,
            "status": self.outcome.status.value,
            "citation_count": self.outcome.citation_count,
            "confidence": self.outcome.confidence,
            "reward_status": self.reward.status.value if self.reward else "",
            "total_score": self.reward.total_score if self.reward else None,
            "reason_codes": tuple(self.reward.reason_codes if self.reward else ()),
            "raw_prompt_visible": False,
            "raw_output_visible": False,
            "private_content_visible": False,
        }


def _safe_label(value: Any, *, field: str) -> str:
    raw = str(value or "")
    _reject_text(raw, field=field)
    text = " ".join(raw.split())
    if not text:
        return ""
    if re.search(r"^[A-Za-z]:[\\/]|^/|^~", text):
        raise ModelRewardContractError(f"{field} must not contain host paths")
    if len(text) > _MAX_LABEL or not _SAFE_LABEL_RE.fullmatch(text):
        return "hash:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    return text


def _safe_ref(value: Any, *, field: str) -> str:
    text = _safe_label(value, field=field)
    if text.startswith(("http://", "https://")):
        return "hash:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    return text


def _safe_reason(value: Any, *, field: str) -> str:
    raw = str(value or "")
    _reject_text(raw, field=field)
    text = " ".join(raw.split())
    if not text:
        return ""
    if len(text) > _MAX_REASON:
        return text[: _MAX_REASON - 3] + "..."
    return text


def _safe_tuple(values: Any, *, field: str) -> tuple[str, ...]:
    if values is None:
        return ()
    items = values if isinstance(values, (list, tuple, set)) else (values,)
    result = tuple(_safe_label(item, field=field) for item in list(items)[:_MAX_TUPLE] if str(item or "").strip())
    return result


def _safe_nonnegative_int(value: Any, *, field: str) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        raise ModelRewardContractError(f"{field} must be an int") from None
    if number < 0:
        raise ModelRewardContractError(f"{field} must be >= 0")
    return number


def _safe_ratio(value: Any, *, field: str) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        raise ModelRewardContractError(f"{field} must be numeric") from None
    if number < 0 or number > 1:
        raise ModelRewardContractError(f"{field} must be between 0 and 1")
    return round(number, 4)


def _safe_score(value: Any, *, field: str) -> int:
    try:
        score = int(value or 0)
    except (TypeError, ValueError):
        raise ModelRewardContractError(f"{field} must be an int") from None
    if score < -100 or score > 100:
        raise ModelRewardContractError(f"{field} must be between -100 and 100")
    return score


def _status(value: Any) -> EpisodeOutcomeStatus:
    try:
        return EpisodeOutcomeStatus(str(value or EpisodeOutcomeStatus.UNKNOWN.value))
    except ValueError as exc:
        raise ModelRewardContractError("outcome status is not supported") from exc


def _status_from_score(score: int) -> str:
    if score < 0:
        return RewardStatus.NEGATIVE.value
    if score > 0:
        return RewardStatus.POSITIVE.value
    return RewardStatus.NEUTRAL.value


def _reject_text(text: str, *, field: str) -> None:
    lowered = text.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise ModelRewardContractError(f"{field} contains forbidden secret marker")
    if any(ord(ch) < 32 for ch in text):
        raise ModelRewardContractError(f"{field} contains control characters")


def _reject_forbidden_payload(payload: Mapping[str, Any]) -> None:
    values = " ".join(_walk_string_values(payload)).lower()
    if any(marker in values for marker in _SECRET_MARKERS):
        raise ModelRewardContractError("episode payload contains forbidden secret marker")
    for key in _walk_keys(payload):
        lowered_key = key.lower()
        if any(marker in lowered_key for marker in _RAW_FIELD_MARKERS):
            if lowered_key in _ALLOWED_RAW_MARKER_KEYS:
                continue
            raise ModelRewardContractError("episode payload contains raw-content field")


def _walk_keys(value: Any) -> tuple[str, ...]:
    keys: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            keys.append(str(key))
            keys.extend(_walk_keys(nested))
    elif isinstance(value, (list, tuple)):
        for item in value:
            keys.extend(_walk_keys(item))
    return tuple(keys)


def _walk_string_values(value: Any) -> tuple[str, ...]:
    values: list[str] = []
    if isinstance(value, Mapping):
        for nested in value.values():
            values.extend(_walk_string_values(nested))
    elif isinstance(value, (list, tuple)):
        for item in value:
            values.extend(_walk_string_values(item))
    elif isinstance(value, str):
        values.append(value)
    return tuple(values)


def _enum_values(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {key: _enum_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return tuple(_enum_values(item) for item in value)
    return value
