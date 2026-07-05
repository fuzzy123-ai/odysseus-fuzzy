"""Date-partitioned JSONL store for redacted model-routing episodes."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from src.constants import DATA_DIR
from src.model_reward_contract import (
    MODEL_REWARD_CONTRACT_SCHEMA,
    ModelEpisode,
    ModelRewardContractError,
)


MODEL_EPISODE_STORE_SCHEMA = "odysseus.model_episode_store.v1"
MODEL_EPISODE_DIR = os.path.join(DATA_DIR, "model_episodes")


class ModelEpisodeStoreError(ValueError):
    """Raised when the redacted episode store receives unsafe input."""


def append_model_episode(episode: ModelEpisode | Mapping[str, Any], *, day: str | None = None) -> dict[str, Any]:
    """Append a redacted model episode and return the persisted payload."""

    record = _episode_record(episode)
    record["stored_at"] = _now_iso()
    _reject_unsafe_record(record)
    path = episode_store_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def read_model_episodes(
    *,
    day: str | None = None,
    limit: int = 100,
    owner: str | None = None,
    surface: str | None = None,
    task_type: str | None = None,
    model: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Read recent redacted episodes with metadata-only filters."""

    capped = max(1, min(int(limit or 100), 1000))
    filters = {
        "owner": str(owner or ""),
        "surface": str(surface or ""),
        "task_type": str(task_type or ""),
        "model": str(model or ""),
        "status": str(status or ""),
    }
    path = episode_store_path(day)
    if not path.exists():
        return _read_response(path=path, limit=capped, filters=filters, records=[], skipped=0)

    records: list[dict[str, Any]] = []
    skipped = 0
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            try:
                record = json.loads(line)
                _reject_unsafe_record(record)
            except Exception:
                skipped += 1
                continue
            if not _matches(record, filters):
                continue
            records.append(_diagnostic_record(record))
    return _read_response(path=path, limit=capped, filters=filters, records=records, skipped=skipped)


def episode_store_path(day: str | None = None) -> Path:
    day_text = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day_text or "")):
        raise ModelEpisodeStoreError("episode day must be YYYY-MM-DD")
    return Path(MODEL_EPISODE_DIR) / f"{day_text}.jsonl"


def _episode_record(episode: ModelEpisode | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(episode, ModelEpisode):
        payload = episode.to_record()
    elif isinstance(episode, Mapping):
        payload = dict(episode)
    else:
        raise ModelEpisodeStoreError("episode must be a ModelEpisode or mapping")
    if payload.get("schema") != MODEL_REWARD_CONTRACT_SCHEMA:
        raise ModelEpisodeStoreError("episode schema is not supported")
    _reject_unsafe_record(payload)
    return payload


def _matches(record: Mapping[str, Any], filters: Mapping[str, str]) -> bool:
    state = record.get("state") if isinstance(record.get("state"), Mapping) else {}
    action = record.get("action") if isinstance(record.get("action"), Mapping) else {}
    outcome = record.get("outcome") if isinstance(record.get("outcome"), Mapping) else {}
    values = {
        "owner": str(state.get("owner_label") or ""),
        "surface": str(state.get("surface") or ""),
        "task_type": str(state.get("task_type") or ""),
        "model": str(action.get("model") or ""),
        "status": str(outcome.get("status") or ""),
    }
    return all(not expected or values.get(key) == expected for key, expected in filters.items())


def _diagnostic_record(record: Mapping[str, Any]) -> dict[str, Any]:
    state = record.get("state") if isinstance(record.get("state"), Mapping) else {}
    action = record.get("action") if isinstance(record.get("action"), Mapping) else {}
    outcome = record.get("outcome") if isinstance(record.get("outcome"), Mapping) else {}
    reward = record.get("reward") if isinstance(record.get("reward"), Mapping) else {}
    return {
        "stored_at": record.get("stored_at", ""),
        "surface": state.get("surface", ""),
        "task_type": state.get("task_type", ""),
        "owner_label": state.get("owner_label", ""),
        "answer_mode": action.get("answer_mode", ""),
        "provider": action.get("provider", ""),
        "model": action.get("model", ""),
        "status": outcome.get("status", ""),
        "citation_count": outcome.get("citation_count", 0),
        "confidence": outcome.get("confidence", 0),
        "total_score": reward.get("total_score"),
        "reward_status": reward.get("status", ""),
        "reason_codes": tuple(reward.get("reason_codes") or ()),
    }


def _read_response(
    *,
    path: Path,
    limit: int,
    filters: Mapping[str, str],
    records: list[dict[str, Any]],
    skipped: int,
) -> dict[str, Any]:
    recent = records[-limit:]
    recent.reverse()
    return {
        "schema": MODEL_EPISODE_STORE_SCHEMA,
        "day": path.stem,
        "limit": limit,
        "filters": {key: value for key, value in filters.items() if value},
        "count": len(recent),
        "total_matches": len(records),
        "skipped": skipped,
        "records": recent,
        "raw_prompt_visible": False,
        "raw_output_visible": False,
        "private_content_visible": False,
    }


def _reject_unsafe_record(record: Mapping[str, Any]) -> None:
    try:
        ModelEpisode.create(
            state=_rebuild_state(record.get("state")),
            action=_rebuild_action(record.get("action")),
            outcome=_rebuild_outcome(record.get("outcome")),
            reward=_rebuild_reward(record.get("reward")),
        )
    except ModelRewardContractError as exc:
        raise ModelEpisodeStoreError(str(exc)) from exc
    forbidden_keys = {"raw_prompt", "raw_output", "prompt_text", "output_text", "messages", "snippets"}
    if forbidden_keys & {str(key) for key in _walk_keys(record)}:
        raise ModelEpisodeStoreError("episode contains forbidden raw-content keys")


def _rebuild_state(value: Any):
    from src.model_reward_contract import ModelEpisodeState

    return ModelEpisodeState.create(**dict(value or {}))


def _rebuild_action(value: Any):
    from src.model_reward_contract import ModelEpisodeAction

    return ModelEpisodeAction.create(**dict(value or {}))


def _rebuild_outcome(value: Any):
    from src.model_reward_contract import ModelEpisodeOutcome

    return ModelEpisodeOutcome.create(**dict(value or {}))


def _rebuild_reward(value: Any):
    from src.model_reward_contract import ModelReward

    return ModelReward.create(**dict(value or {})) if value else None


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
