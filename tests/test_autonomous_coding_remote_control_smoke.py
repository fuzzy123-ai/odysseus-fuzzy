from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "homeserver" / "run-autonomous-coding-remote-control-smoke.sh"


def test_remote_control_smoke_uses_telegram_control_and_runner_state():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "_telegram_control_command" in script
    assert "_handle_agent_task_control_command" in script
    assert "record_task_event" in script
    assert "CodingRunnerStateStore" in script
    assert "transition_from_task_control_event" in script
    assert 'surface="workstation"' in script
    assert '"/task status"' in script
    assert '"/task pause"' in script
    assert '"/task weiter"' in script


def test_remote_control_smoke_writes_redacted_report_only():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "odysseus.autonomous_coding_remote_control_live_smoke.v1" in script
    assert "data/reports/${report_ref}" in script
    assert "autonomous_coding_production/workstation-telegram-control-live-smoke.json" in script
    assert '"raw_content_visible": False' in script
    assert '"tokens_visible": False' in script
    assert '"chat_ids_visible": False' in script
    assert '"host_paths_visible": False' in script
    assert '"telegram_network_delivery": False' in script
    assert '"deploy_performed": False' in script


def test_remote_control_smoke_does_not_expose_broad_live_surfaces():
    script = SCRIPT.read_text(encoding="utf-8").lower()

    assert "send_telegram" not in script
    assert "_telegram_http_post" not in script
    assert "curl " not in script
    assert "docker " not in script
    assert "docker compose" not in script
    assert "cloudflare" not in script
    assert "token" not in script.replace("tokens_visible", "")
