from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time

from plugins.telegram.plugin import run_telegram_polling_cycle
from plugins.telegram.stores import TelegramSessionBridgeStore
from src.telegram_session_rollover import (
    TelegramRolloverConfig,
    begin_telegram_turn,
    consume_continuity,
    continuity_binding,
    end_telegram_turn,
    execute_telegram_session_rollover,
)


DAY_ONE = datetime(2026, 7, 22, 8, 0, tzinfo=timezone.utc)
DAY_TWO = datetime(2026, 7, 23, 8, 0, tzinfo=timezone.utc)
CONFIG = TelegramRolloverConfig(
    enabled=True,
    timezone_name="Europe/Berlin",
    boundary_hour=4,
)


def _bind(store: TelegramSessionBridgeStore, chat_id: str = "123", scope: str = "normal"):
    return store.bind_chat(
        chat_id=chat_id,
        session_alias=f"telegram:{chat_id}",
        recommended_session_name="Telegram Test",
        scope=scope,
        creator=lambda **_kwargs: {"session_id": f"old-{scope}"},
    )


def _initialize(store: TelegramSessionBridgeStore, chat_id: str = "123", scope: str = "normal"):
    result = execute_telegram_session_rollover(
        store=store,
        chat_id=chat_id,
        scope=scope,
        creator=lambda **kwargs: {"session_id": kwargs["rollover_session_id"]},
        config=CONFIG,
        now=DAY_ONE,
    )
    assert result["status"] == "initialized"


def test_rollover_is_default_off_and_does_not_touch_bridge_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_SESSION_ROLLOVER_ENABLED", raising=False)
    store = TelegramSessionBridgeStore(tmp_path)
    _bind(store)
    before = store.path.read_bytes()

    result = execute_telegram_session_rollover(
        store=store,
        chat_id="123",
        scope="normal",
        creator=lambda **_kwargs: {"session_id": "must-not-run"},
    )

    assert result["status"] == "disabled"
    assert store.path.read_bytes() == before


def test_daily_rollover_binds_then_archives_and_keeps_old_session_readable(tmp_path):
    store = TelegramSessionBridgeStore(tmp_path)
    _bind(store)
    _initialize(store)
    created = []
    archived = []

    def creator(**kwargs):
        created.append(dict(kwargs))
        return {"session_id": kwargs["rollover_session_id"]}

    result = execute_telegram_session_rollover(
        store=store,
        chat_id="123",
        scope="normal",
        creator=creator,
        archiver=lambda session_id: archived.append(session_id) or {"archived": True},
        config=CONFIG,
        now=DAY_TWO,
    )
    mapping = store.get("123")

    assert result["status"] == "rolled_over"
    assert result["archive_status"] == "archived"
    assert len(created) == 1
    assert created[0]["previous_session_id"] == "old-normal"
    assert created[0]["local_only_required"] is False
    assert mapping["normal_session_id"] == created[0]["rollover_session_id"]
    assert mapping["normal_rollover_day"] == "2026-07-23"
    assert archived == ["old-normal"]
    assert mapping["rollovers"]["normal"]["previous_session_id"] == "old-normal"
    assert mapping["rollovers"]["normal"]["archive_status"] == "archived"
    assert '"123"' not in store.path.read_text(encoding="utf-8")


def test_parallel_polls_create_exactly_one_new_session(tmp_path):
    store = TelegramSessionBridgeStore(tmp_path)
    _bind(store)
    _initialize(store)
    calls = []
    calls_lock = threading.Lock()

    def creator(**kwargs):
        with calls_lock:
            calls.append(kwargs["rollover_session_id"])
        time.sleep(0.03)
        return {"session_id": kwargs["rollover_session_id"]}

    def run_once():
        return execute_telegram_session_rollover(
            store=TelegramSessionBridgeStore(tmp_path),
            chat_id="123",
            scope="normal",
            creator=creator,
            archiver=lambda _session_id: {"archived": True},
            config=CONFIG,
            now=DAY_TWO,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: run_once(), range(2)))

    assert len(calls) == 1
    assert {item["status"] for item in results} == {"rolled_over", "already_current"}
    assert store.get("123")["normal_session_id"] == calls[0]


def test_normal_and_secure_slots_roll_independently(tmp_path):
    store = TelegramSessionBridgeStore(tmp_path)
    _bind(store, scope="normal")
    _bind(store, scope="secure")
    _initialize(store, scope="normal")
    _initialize(store, scope="secure")
    archived = []
    created = {}

    def creator(**kwargs):
        created[kwargs["session_scope"]] = kwargs["rollover_session_id"]
        return {"session_id": kwargs["rollover_session_id"]}

    normal = execute_telegram_session_rollover(
        store=store,
        chat_id="123",
        scope="normal",
        creator=creator,
        archiver=lambda sid: archived.append(sid) or True,
        config=CONFIG,
        now=DAY_TWO,
    )
    secure = execute_telegram_session_rollover(
        store=store,
        chat_id="123",
        scope="secure",
        creator=creator,
        archiver=lambda sid: archived.append(sid) or True,
        config=CONFIG,
        now=DAY_TWO,
    )
    mapping = store.get("123")

    assert normal["session_ref"] != secure["session_ref"]
    assert mapping["normal_session_id"] == created["normal"]
    assert mapping["secure_session_id"] == created["secure"]
    assert set(archived) == {"old-normal", "old-secure"}


def test_crash_after_creation_recovers_deterministically_without_empty_binding(tmp_path):
    store = TelegramSessionBridgeStore(tmp_path)
    _bind(store)
    _initialize(store)
    created_sessions = set()
    attempts = 0

    def creator(**kwargs):
        nonlocal attempts
        attempts += 1
        target = kwargs["rollover_session_id"]
        if target not in created_sessions:
            created_sessions.add(target)
            raise RuntimeError("synthetic crash after durable session creation")
        return {"session_id": target, "recovered": True}

    first = execute_telegram_session_rollover(
        store=store,
        chat_id="123",
        scope="normal",
        creator=creator,
        config=CONFIG,
        now=DAY_TWO,
    )
    assert first["status"] == "create_failed"
    assert store.get("123")["normal_session_id"] == "old-normal"

    second = execute_telegram_session_rollover(
        store=store,
        chat_id="123",
        scope="normal",
        creator=creator,
        archiver=lambda _sid: True,
        config=CONFIG,
        now=DAY_TWO,
    )

    assert second["status"] == "rolled_over"
    assert attempts == 2
    assert len(created_sessions) == 1
    assert store.get("123")["normal_session_id"] in created_sessions


def test_active_turn_defers_rollover_until_next_attempt(tmp_path):
    store = TelegramSessionBridgeStore(tmp_path)
    _bind(store)
    _initialize(store)
    begin_telegram_turn(tmp_path, "123", "normal")
    try:
        deferred = execute_telegram_session_rollover(
            store=store,
            chat_id="123",
            scope="normal",
            creator=lambda **kwargs: {"session_id": kwargs["rollover_session_id"]},
            config=CONFIG,
            now=DAY_TWO,
        )
    finally:
        end_telegram_turn(tmp_path, "123", "normal")

    assert deferred["status"] == "deferred_active_turn"
    assert store.get("123")["normal_session_id"] == "old-normal"
    retried = execute_telegram_session_rollover(
        store=store,
        chat_id="123",
        scope="normal",
        creator=lambda **kwargs: {"session_id": kwargs["rollover_session_id"]},
        archiver=lambda _sid: True,
        config=CONFIG,
        now=DAY_TWO,
    )
    assert retried["status"] == "rolled_over"


def test_archive_failure_retries_without_second_session(tmp_path):
    store = TelegramSessionBridgeStore(tmp_path)
    _bind(store)
    _initialize(store)
    creates = []
    archive_attempts = []

    first = execute_telegram_session_rollover(
        store=store,
        chat_id="123",
        scope="normal",
        creator=lambda **kwargs: creates.append(kwargs["rollover_session_id"]) or {
            "session_id": kwargs["rollover_session_id"]
        },
        archiver=lambda sid: archive_attempts.append(sid) or False,
        config=CONFIG,
        now=DAY_TWO,
    )
    second = execute_telegram_session_rollover(
        store=store,
        chat_id="123",
        scope="normal",
        creator=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not create again")),
        archiver=lambda sid: archive_attempts.append(sid) or True,
        config=CONFIG,
        now=DAY_TWO,
    )

    assert first["status"] == "rolled_over_archive_pending"
    assert second["status"] == "already_current"
    assert second["archive_status"] == "archived"
    assert len(creates) == 1
    assert archive_attempts == ["old-normal", "old-normal"]


def test_continuity_is_single_use_and_contains_no_raw_chat_id(tmp_path):
    store = TelegramSessionBridgeStore(tmp_path)
    _bind(store)
    _initialize(store)
    execute_telegram_session_rollover(
        store=store,
        chat_id="123",
        scope="normal",
        creator=lambda **kwargs: {"session_id": kwargs["rollover_session_id"]},
        archiver=lambda _sid: True,
        config=CONFIG,
        now=DAY_TWO,
    )

    continuity = continuity_binding(store, "123", "normal")
    assert continuity["previous_session_id"] == "old-normal"
    assert continuity["trusted"] is False
    assert "123" not in json.dumps(continuity)
    assert consume_continuity(store, "123", "normal") is True
    assert continuity_binding(store, "123", "normal") is None


def test_polling_integration_rolls_before_turn_and_consumes_used_continuity(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_POLLING_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "123")
    monkeypatch.setenv("TELEGRAM_AGENT_CHAT_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_SESSION_ROLLOVER_ENABLED", "true")
    store = TelegramSessionBridgeStore(tmp_path)
    _bind(store)
    _initialize(store)
    turns = []
    archived = []

    def creator(**kwargs):
        return {"session_id": kwargs["rollover_session_id"]}

    def agent_turn(bridge):
        turns.append(bridge)
        return {
            "status": "accepted",
            "reply_text": "continuity used",
            "telegram_rollover_continuity_used": True,
        }

    result = run_telegram_polling_cycle(
        data_dir=tmp_path,
        fetch_updates=lambda _offset: [
            {
                "update_id": 10,
                "message": {
                    "message_id": 100,
                    "chat": {"id": 123},
                    "from": {"id": 1, "first_name": "Nina"},
                    "text": "Und was war der zweite Punkt?",
                },
            }
        ],
        session_creator=creator,
        session_archiver=lambda sid: archived.append(sid) or True,
        agent_turn_handler=agent_turn,
        rollover_now=DAY_TWO,
    )

    assert result["session_rollovers"] == 1
    assert turns[0]["session_id"].startswith("tg-")
    assert turns[0]["telegram_rollover_continuity"]["trusted"] is False
    assert archived == ["old-normal"]
    assert continuity_binding(store, "123", "normal") is None


def test_invalid_environment_configuration_fails_safe(monkeypatch):
    monkeypatch.setenv("TELEGRAM_SESSION_ROLLOVER_ENABLED", "true")
    monkeypatch.setenv("TELEGRAM_SESSION_ROLLOVER_TIMEZONE", "Not/AZone")
    config = TelegramRolloverConfig.from_environment()
    assert config.enabled is False
    assert config.error == "invalid_rollover_configuration"


def test_rollover_day_handles_the_berlin_boundary():
    before_boundary = datetime(2026, 7, 22, 1, 59, tzinfo=timezone.utc)
    after_boundary = datetime(2026, 7, 22, 2, 0, tzinfo=timezone.utc)
    assert CONFIG.local_rollover_day(before_boundary).isoformat() == "2026-07-21"
    assert CONFIG.local_rollover_day(after_boundary).isoformat() == "2026-07-22"


def test_rollover_day_handles_berlin_dst_transition():
    before_boundary = datetime(2026, 3, 29, 1, 59, tzinfo=timezone.utc)
    after_boundary = datetime(2026, 3, 29, 2, 0, tzinfo=timezone.utc)
    assert CONFIG.local_rollover_day(before_boundary).isoformat() == "2026-03-28"
    assert CONFIG.local_rollover_day(after_boundary).isoformat() == "2026-03-29"


def test_restart_after_multiple_missed_days_rolls_only_once(tmp_path):
    store = TelegramSessionBridgeStore(tmp_path)
    _bind(store)
    _initialize(store)
    creates = []
    much_later = datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc)

    def creator(**kwargs):
        creates.append(kwargs["rollover_session_id"])
        return {"session_id": kwargs["rollover_session_id"]}

    first = execute_telegram_session_rollover(
        store=store,
        chat_id="123",
        scope="normal",
        creator=creator,
        archiver=lambda _sid: True,
        config=CONFIG,
        now=much_later,
    )
    second = execute_telegram_session_rollover(
        store=store,
        chat_id="123",
        scope="normal",
        creator=creator,
        archiver=lambda _sid: True,
        config=CONFIG,
        now=much_later,
    )

    assert first["status"] == "rolled_over"
    assert second["status"] == "already_current"
    assert len(creates) == 1
    assert store.get("123")["normal_rollover_day"] == "2026-07-27"


def test_app_rollover_contract_inherits_config_and_archives_without_delete():
    source = Path("app.py").read_text(encoding="utf-8-sig")
    creator_start = source.index("def _telegram_session_bridge")
    archiver_start = source.index("def _telegram_session_archiver", creator_start)
    next_function = source.index("def _telegram_rebind_local_session", archiver_start)
    creator_body = source[creator_start:archiver_start]
    archiver_body = source[archiver_start:next_function]

    assert 'session_id=rollover_session_id' in creator_body
    assert 'endpoint_url=str(getattr(previous, "endpoint_url"' in creator_body
    assert 'model=str(getattr(previous, "model"' in creator_body
    assert 'owner=getattr(previous, "owner", None)' in creator_body
    assert "created.headers = dict(getattr(previous" in creator_body
    assert "archive_session(session.id)" in archiver_body
    assert "delete_session" not in archiver_body
