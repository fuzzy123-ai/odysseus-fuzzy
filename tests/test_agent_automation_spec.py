import json
from datetime import datetime, timedelta, timezone

import pytest

from src.agent_automation_spec import (
    AgentAutomationMode,
    AgentAutomationSpec,
    AgentAutomationSpecError,
    AgentAutomationStatus,
    AgentAutomationUnit,
)


def _future_iso(minutes: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).replace(microsecond=0).isoformat()


def test_interval_specs_are_valid_for_minutes_hours_and_days_and_json_compatible():
    minute_spec = AgentAutomationSpec.create(
        agent_id="bob",
        parent_agent_id="charlie",
        mode=AgentAutomationMode.INTERVAL,
        status=AgentAutomationStatus.READY,
        interval_count=15,
        interval_unit=AgentAutomationUnit.MINUTES,
        timezone="Europe/Berlin",
        next_run_hint="every 15 minutes",
    )
    hour_spec = AgentAutomationSpec.create(
        agent_id="alice",
        mode="interval",
        interval_count=2,
        interval_unit="hours",
    )
    day_spec = AgentAutomationSpec.create(
        agent_id="charlie",
        mode="interval",
        interval_count=3,
        interval_unit="days",
    )

    assert minute_spec.to_dict()["interval_unit"] == "minutes"
    assert hour_spec.interval_unit is AgentAutomationUnit.HOURS
    assert day_spec.interval_unit is AgentAutomationUnit.DAYS
    json.dumps(minute_spec.to_overlay_payload(), sort_keys=True)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"interval_count": 0, "interval_unit": "minutes"},
        {"interval_count": -1, "interval_unit": "hours"},
        {"interval_count": 1, "interval_unit": "weeks"},
        {"interval_count": 999, "interval_unit": "days"},
    ],
)
def test_invalid_intervals_and_units_are_rejected(kwargs):
    with pytest.raises(AgentAutomationSpecError):
        AgentAutomationSpec.create(
            agent_id="bob",
            mode="interval",
            **kwargs,
        )


def test_once_and_recurring_are_modeled_safely():
    once_spec = AgentAutomationSpec.create(
        agent_id="alice",
        mode="once",
        scheduled_at=_future_iso(),
        change_effective="after operator review",
    )
    recurring_spec = AgentAutomationSpec.create(
        agent_id="charlie",
        mode="recurring",
        recurrence="weekly:monday-09:00",
        timezone="UTC",
        status="needs_review",
    )

    assert once_spec.scheduled_at.endswith("Z")
    assert recurring_spec.recurrence == "weekly:monday-09:00"
    assert recurring_spec.status.value == "needs_review"


def test_watch_and_manual_remain_live_inactive_without_scheduler_requirements():
    manual_spec = AgentAutomationSpec.create(
        agent_id="bob",
        mode="manual",
        status="inactive",
    )
    watch_spec = AgentAutomationSpec.create(
        agent_id="alice",
        mode="watch",
        status="paused",
        last_handoff="watch for docs changes only",
    )

    assert manual_spec.interval_count is None
    assert manual_spec.scheduled_at is None
    assert watch_spec.recurrence is None
    assert watch_spec.to_overlay_payload()["last_handoff"] == "watch for docs changes only"


def test_secret_and_chat_id_patterns_are_redacted_in_overlay_payload():
    spec = AgentAutomationSpec.create(
        agent_id="charlie",
        mode="recurring",
        recurrence="daily",
        last_handoff="token=abc123 chat_id=7788 keep hidden",
        next_run_hint="secret=xyz before review",
        change_effective="password=hunter2 after approval",
    )

    payload = spec.to_overlay_payload()
    encoded = json.dumps(payload)

    assert "abc123" not in encoded
    assert "7788" not in encoded
    assert "xyz" not in encoded
    assert "hunter2" not in encoded
    assert "[redacted]" in encoded


def test_once_requires_future_iso_datetime():
    with pytest.raises(AgentAutomationSpecError, match="future"):
        AgentAutomationSpec.create(
            agent_id="alice",
            mode="once",
            scheduled_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        )

    with pytest.raises(AgentAutomationSpecError, match="ISO datetime"):
        AgentAutomationSpec.create(
            agent_id="alice",
            mode="once",
            scheduled_at="not-a-date",
        )


def test_invalid_path_like_agent_ids_are_rejected():
    with pytest.raises(AgentAutomationSpecError, match="path-like"):
        AgentAutomationSpec.create(
            agent_id="../bob",
            mode="manual",
        )
