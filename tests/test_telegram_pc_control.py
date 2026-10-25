import json
from pathlib import Path

import pytest

from plugins.telegram.pc_control import (
    PcControlError,
    TelegramPcControlService,
    TelegramPcControlStore,
    build_agent_request_signature,
    build_agent_response_signature,
    send_magic_packet,
    verify_agent_request,
)


class _Clock:
    def __init__(self, value: int = 1_800_000_000):
        self.value = value

    def __call__(self) -> float:
        return float(self.value)


def _enabled_env() -> dict[str, str]:
    return {
        "TELEGRAM_PC_CONTROL_ENABLED": "true",
        "TELEGRAM_PC_WAKE_ENABLED": "true",
        "TELEGRAM_PC_MAC": "00:11:22:33:44:55",
        "TELEGRAM_PC_BROADCAST": "192.0.2.255",
        "TELEGRAM_PC_AGENT_SECRET": "unit-test-agent-secret-with-enough-entropy",
    }


def test_magic_packet_is_exact_and_invalid_targets_fail_closed():
    sent: list[tuple[bytes, tuple[str, int]]] = []

    class _Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def setsockopt(self, *_args):
            return None

        def sendto(self, payload, target):
            sent.append((payload, target))
            return len(payload)

    result = send_magic_packet(
        "00:11:22:33:44:55",
        "192.0.2.255",
        socket_factory=lambda *_args: _Socket(),
    )

    assert result == {"status": "wake_packet_sent", "bytes_sent": 102, "raw_target_visible": False}
    assert sent == [(b"\xff" * 6 + bytes.fromhex("001122334455") * 16, ("192.0.2.255", 9))]

    for mac, broadcast in (("not-a-mac", "192.0.2.255"), ("00:11:22:33:44:55", "example.test")):
        with pytest.raises(PcControlError) as caught:
            send_magic_packet(mac, broadcast, socket_factory=lambda *_args: _Socket())
        assert caught.value.code in {"invalid_mac", "invalid_broadcast"}
        assert mac not in str(caught.value)
        assert broadcast not in str(caught.value)


def test_store_leases_expires_and_records_only_redacted_metadata(tmp_path: Path):
    clock = _Clock()
    store = TelegramPcControlStore(tmp_path, now_fn=clock, id_factory=lambda: "action-0001")

    queued = store.enqueue("start_codex", ttl_seconds=30)
    assert queued["status"] == "pending"
    leased = store.lease_next(lease_seconds=10)
    assert leased == {
        "action_id": "action-0001",
        "action": "start_codex",
        "expires_at": clock.value + 30,
    }
    assert store.lease_next(lease_seconds=10) is None

    clock.value += 11
    assert store.lease_next(lease_seconds=10)["action_id"] == "action-0001"
    completed = store.record_result("action-0001", outcome="completed", code="codex_started")
    assert completed["status"] == "completed"
    assert store.lease_next() is None

    raw = store.path.read_text(encoding="utf-8")
    assert "00:11:22:33:44:55" not in raw
    assert "unit-test-agent-secret" not in raw
    assert "chat-id" not in raw
    assert json.loads(raw)["raw_content_visible"] is False


def test_service_is_default_off_and_wake_queues_codex_without_persisting_chat(tmp_path: Path):
    clock = _Clock()
    disabled = TelegramPcControlService(tmp_path / "disabled", environ={}, now_fn=clock)
    blocked = disabled.handle_command("pc_wake", chat_id="private-chat-id")
    assert blocked["status"] == "pc_control_disabled"
    assert "private-chat-id" not in json.dumps(blocked, sort_keys=True)

    wakes: list[tuple[str, str]] = []
    enabled = TelegramPcControlService(
        tmp_path / "enabled",
        environ=_enabled_env(),
        now_fn=clock,
        wake_sender=lambda mac, broadcast: wakes.append((mac, broadcast))
        or {"status": "wake_packet_sent", "bytes_sent": 102, "raw_target_visible": False},
        id_factory=lambda: "action-wake-codex",
    )
    result = enabled.handle_command("pc_wake", chat_id="private-chat-id")

    assert result["status"] == "pc_wake_sent"
    assert result["queued_action"] == "start_codex"
    assert wakes == [("00:11:22:33:44:55", "192.0.2.255")]
    assert "private-chat-id" not in enabled.store.path.read_text(encoding="utf-8")
    assert "00:11:22:33:44:55" not in enabled.store.path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("request_command", "confirm_command", "action"),
    [
        ("pc_shutdown_request", "pc_shutdown_confirm", "shutdown"),
        ("pc_hibernate_request", "pc_hibernate_confirm", "hibernate"),
    ],
)
def test_destructive_actions_require_same_chat_confirmation_and_expire(
    tmp_path: Path, request_command: str, confirm_command: str, action: str
):
    clock = _Clock()
    service = TelegramPcControlService(
        tmp_path,
        environ=_enabled_env(),
        now_fn=clock,
        id_factory=lambda: f"action-{action}",
    )

    assert service.handle_command(confirm_command, chat_id="chat-a")["status"] == "pc_confirmation_missing"
    requested = service.handle_command(request_command, chat_id="chat-a")
    assert requested["status"] == "pc_confirmation_required"
    assert service.handle_command(confirm_command, chat_id="chat-b")["status"] == "pc_confirmation_missing"

    confirmed = service.handle_command(confirm_command, chat_id="chat-a")
    assert confirmed["status"] == "pc_action_queued"
    assert confirmed["queued_action"] == action

    service.handle_command(request_command, chat_id="chat-a")
    clock.value += 60
    assert service.handle_command(confirm_command, chat_id="chat-a")["status"] == "pc_confirmation_expired"


def test_agent_poll_status_and_result_are_typed_and_content_free(tmp_path: Path):
    clock = _Clock()
    service = TelegramPcControlService(
        tmp_path,
        environ=_enabled_env(),
        now_fn=clock,
        id_factory=lambda: "action-codex",
    )
    service.handle_command("pc_codex", chat_id="chat-a")

    polled = service.agent_poll(
        {"agent_version": "1.0", "interactive_session": True, "codex_running": False}
    )
    assert polled["command"]["action"] == "start_codex"
    assert polled["raw_content_visible"] is False

    result = service.agent_result(
        {"action_id": "action-codex", "outcome": "completed", "code": "codex_started"}
    )
    assert result == {"status": "completed", "raw_content_visible": False}

    status = service.handle_command("pc_status", chat_id="chat-a")
    assert status["pc"]["agent_online"] is True
    assert status["pc"]["interactive_session"] is True
    assert status["pc"]["codex_running"] is False

    with pytest.raises(PcControlError) as caught:
        service.agent_poll({"agent_version": "private/path", "interactive_session": "yes", "codex_running": False})
    assert caught.value.code == "invalid_agent_payload"


def test_hmac_auth_is_time_bound_replay_safe_and_response_bound(tmp_path: Path):
    clock = _Clock()
    secret = _enabled_env()["TELEGRAM_PC_AGENT_SECRET"]
    body = b'{"agent_version":"1.0","codex_running":false,"interactive_session":true}'
    timestamp = str(clock.value)
    nonce = "nonce-abcdefghijklmnop"
    signature = build_agent_request_signature(
        secret,
        timestamp=timestamp,
        nonce=nonce,
        method="POST",
        path="/api/plugins/telegram/pc-agent/poll",
        body=body,
    )
    headers = {
        "x-odysseus-pc-timestamp": timestamp,
        "x-odysseus-pc-nonce": nonce,
        "x-odysseus-pc-signature": signature,
    }
    store = TelegramPcControlStore(tmp_path, now_fn=clock)

    assert verify_agent_request(
        secret,
        headers=headers,
        method="POST",
        path="/api/plugins/telegram/pc-agent/poll",
        body=body,
        store=store,
        now_fn=clock,
    ) == nonce

    with pytest.raises(PcControlError) as replay:
        verify_agent_request(
            secret,
            headers=headers,
            method="POST",
            path="/api/plugins/telegram/pc-agent/poll",
            body=body,
            store=store,
            now_fn=clock,
        )
    assert replay.value.code == "agent_request_replayed"

    bad_headers = dict(headers, **{"x-odysseus-pc-nonce": "nonce-qrstuvwxyz1234"})
    with pytest.raises(PcControlError) as invalid:
        verify_agent_request(
            secret,
            headers=bad_headers,
            method="POST",
            path="/api/plugins/telegram/pc-agent/poll",
            body=body,
            store=store,
            now_fn=clock,
        )
    assert invalid.value.code == "agent_auth_invalid"
    assert secret not in str(invalid.value)

    response_body = b'{"command":null,"raw_content_visible":false,"status":"ok"}'
    assert build_agent_response_signature(
        secret, request_nonce=nonce, status_code=200, body=response_body
    ) == build_agent_response_signature(
        secret, request_nonce=nonce, status_code=200, body=response_body
    )
    assert build_agent_response_signature(
        secret, request_nonce=nonce, status_code=200, body=response_body
    ) != build_agent_response_signature(
        secret, request_nonce=nonce, status_code=200, body=response_body + b" "
    )


def test_windows_agent_contract_is_outbound_only_dpapi_and_allowlisted():
    root = Path(__file__).resolve().parents[1]
    agent = (root / "ops" / "windows" / "telegram-pc-control" / "pc-agent.ps1").read_text(
        encoding="utf-8"
    )
    installer = (
        root / "ops" / "windows" / "telegram-pc-control" / "install-pc-agent.ps1"
    ).read_text(encoding="utf-8")
    combined = agent + "\n" + installer

    assert "/api/plugins/telegram/pc-agent/poll" in agent
    assert "/api/plugins/telegram/pc-agent/result" in agent
    assert "X-Odysseus-Pc-Response-Signature" in agent
    assert "OpenAI.Codex_2p2nqsd0c76g0!App" in agent
    assert "$AllowedActions = @('start_codex', 'lock', 'hibernate', 'shutdown')" in agent
    assert "ConvertFrom-SecureString" in installer
    assert "ConvertTo-SecureString" in agent
    assert "-AtLogOn" in installer and "-RunLevel Limited" in installer
    for forbidden in (
        "HttpListener",
        "New-NetFirewallRule",
        "Enable-PSRemoting",
        "AutoAdminLogon",
        "DefaultPassword",
        "Get-ChildItem Env:",
        "Write-Host $secret",
        "Write-Output $secret",
    ):
        assert forbidden not in combined


def test_compose_passes_pc_control_configuration_default_off():
    root = Path(__file__).resolve().parents[1]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "TELEGRAM_PC_CONTROL_ENABLED=${TELEGRAM_PC_CONTROL_ENABLED:-false}" in compose
    assert "TELEGRAM_PC_WAKE_ENABLED=${TELEGRAM_PC_WAKE_ENABLED:-false}" in compose
    assert "TELEGRAM_PC_MAC=${TELEGRAM_PC_MAC:-}" in compose
    assert "TELEGRAM_PC_BROADCAST=${TELEGRAM_PC_BROADCAST:-}" in compose
    assert "TELEGRAM_PC_AGENT_SECRET=${TELEGRAM_PC_AGENT_SECRET:-}" in compose
