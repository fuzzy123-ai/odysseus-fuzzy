from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "ops" / "homeserver" / "redacted_runtime_probe.py"
SSH_CONFIG_PATH = ROOT / "ops" / "homeserver" / "ssh_config"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("redacted_runtime_probe", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Result:
    def __init__(self, *, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _safe_payload(module: ModuleType) -> dict[str, object]:
    presence = {
        name: index % 2 == 0
        for index, name in enumerate(module.EXPECTED_CREDENTIAL_KEYS)
    }
    presence["TELEGRAM_BOT_TOKEN"] = True
    return {
        "schema_id": module.CONTAINER_SCHEMA_ID,
        "environment_entry_count": 24,
        "credential_presence": presence,
        "unknown_sensitive_key_count": 1,
        "telegram_delivery_readiness": {
            "opaque_target_configured": True,
            "agent_reply_enabled": True,
            "send_ready": True,
            "raw_target_visible": False,
            "secret_values_visible": False,
        },
    }


def test_command_uses_fixed_in_container_projection_without_shell_or_env_inspect() -> None:
    module = _load_module()

    command = module.build_probe_command("odysseus_odysseus_1")
    encoded = " ".join(command).casefold()

    assert command[:3] == ["podman", "exec", "odysseus_odysseus_1"]
    assert command[-2] == "-c"
    assert "config.env" not in encoded
    assert "inspect" not in command
    assert "sh -c" not in encoded
    assert "bash -c" not in encoded


def test_projection_rejects_unknown_fields_and_never_serializes_secret_values() -> None:
    module = _load_module()
    payload = _safe_payload(module)
    payload["unexpected"] = {
        "password": "synthetic-value-that-must-never-survive",
        "nested": ["another-synthetic-value"],
    }

    with pytest.raises(ValueError):
        module.parse_container_projection(
            json.dumps(payload), container="odysseus_odysseus_1"
        )


def test_malformed_or_secret_bearing_subprocess_output_is_fail_closed_and_redacted() -> None:
    module = _load_module()
    raw_marker = "synthetic-credential-material"

    def failed_runner(*_args, **_kwargs):
        return _Result(
            returncode=7,
            stdout=f'{{"token":"{raw_marker}"}}',
            stderr=f"password={raw_marker}",
        )

    projection = module.collect_runtime_projection(
        container="odysseus_odysseus_1", runner=failed_runner
    )
    encoded = json.dumps(projection, sort_keys=True)

    assert projection == {
        "schema_id": module.HOST_SCHEMA_ID,
        "status": "blocked",
        "error_code": "container_probe_failed",
        "raw_environment_visible": False,
        "secret_values_visible": False,
        "telegram_delivery_readiness": {
            "opaque_target_configured": False,
            "agent_reply_enabled": False,
            "send_ready": False,
            "raw_target_visible": False,
            "secret_values_visible": False,
        },
    }
    assert raw_marker not in encoded


def test_secret_value_in_expected_presence_field_is_rejected_not_coerced() -> None:
    module = _load_module()
    payload = _safe_payload(module)
    raw_marker = "synthetic-value-in-boolean-field"
    payload["credential_presence"][module.EXPECTED_CREDENTIAL_KEYS[0]] = raw_marker

    def malicious_runner(*_args, **_kwargs):
        return _Result(returncode=0, stdout=json.dumps(payload), stderr="")

    projection = module.collect_runtime_projection(
        container="odysseus_odysseus_1", runner=malicious_runner
    )
    encoded = json.dumps(projection, sort_keys=True)

    assert projection["status"] == "blocked"
    assert projection["error_code"] == "invalid_probe_payload"
    assert raw_marker not in encoded


def test_legitimate_probe_preserves_presence_and_runtime_readiness() -> None:
    module = _load_module()
    payload = _safe_payload(module)

    def successful_runner(command, **kwargs):
        assert command == module.build_probe_command("odysseus_odysseus_1")
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["timeout"] == module.DEFAULT_TIMEOUT_SECONDS
        assert kwargs["check"] is False
        return _Result(returncode=0, stdout=json.dumps(payload), stderr="")

    projection = module.collect_runtime_projection(
        container="odysseus_odysseus_1", runner=successful_runner
    )

    assert projection["status"] == "ok"
    assert projection["container"] == "odysseus_odysseus_1"
    assert projection["environment_entry_count"] == 24
    assert projection["unknown_sensitive_key_count"] == 1
    assert projection["credential_presence"] == payload["credential_presence"]
    assert projection["telegram_delivery_readiness"] == payload["telegram_delivery_readiness"]


def test_nested_telegram_readiness_is_exact_and_token_bound() -> None:
    module = _load_module()
    payload = _safe_payload(module)
    payload["telegram_delivery_readiness"]["send_ready"] = False
    with pytest.raises(ValueError):
        module.parse_container_projection(
            json.dumps(payload), container="odysseus_odysseus_1"
        )

    payload = _safe_payload(module)
    payload["credential_presence"]["TELEGRAM_BOT_TOKEN"] = False
    with pytest.raises(ValueError):
        module.parse_container_projection(
            json.dumps(payload), container="odysseus_odysseus_1"
        )

    payload = _safe_payload(module)
    payload["telegram_delivery_readiness"]["extra"] = True
    with pytest.raises(ValueError):
        module.parse_container_projection(
            json.dumps(payload), container="odysseus_odysseus_1"
        )


def _container_program_payload(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
) -> dict[str, object]:
    stream = io.StringIO()
    monkeypatch.setattr(os, "environ", environment)
    with contextlib.redirect_stdout(stream):
        exec(module._CONTAINER_PROGRAM, {})
    return json.loads(stream.getvalue())


@pytest.mark.parametrize(
    "target_marker",
    ["TELEGRAM_NOTIFICATION_CHAT_ID", "TELEGRAM_ALLOWED_CHAT_IDS", "TELEGRAM_CHAT_ID"],
)
def test_container_target_readiness_requires_token_without_leaking_values(
    monkeypatch: pytest.MonkeyPatch,
    target_marker: str,
) -> None:
    module = _load_module()
    payload = _container_program_payload(
        module,
        monkeypatch,
        {
            target_marker: "configured-target",
            "TELEGRAM_AGENT_REPLY_ENABLED": "true",
            "TELEGRAM_BOT_TOKEN": "synthetic-token-value",
        },
    )
    assert payload["telegram_delivery_readiness"] == {
        "opaque_target_configured": True,
        "agent_reply_enabled": True,
        "send_ready": True,
        "raw_target_visible": False,
        "secret_values_visible": False,
    }
    encoded = json.dumps(payload, sort_keys=True)
    assert payload["unknown_sensitive_key_count"] == 0
    assert "configured-target" not in encoded
    assert "synthetic-token-value" not in encoded


def test_container_send_readiness_is_false_without_target_or_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    no_target = _container_program_payload(
        module,
        monkeypatch,
        {"TELEGRAM_AGENT_REPLY_ENABLED": "true", "TELEGRAM_BOT_TOKEN": "configured"},
    )
    assert no_target["telegram_delivery_readiness"]["send_ready"] is False

    no_token = _container_program_payload(
        module,
        monkeypatch,
        {
            "TELEGRAM_NOTIFICATION_CHAT_ID": "configured",
            "TELEGRAM_AGENT_REPLY_ENABLED": "true",
        },
    )
    assert no_token["telegram_delivery_readiness"]["send_ready"] is False
    assert no_target["unknown_sensitive_key_count"] == 0
    assert no_token["unknown_sensitive_key_count"] == 0


def test_invalid_container_name_is_rejected_without_starting_a_process() -> None:
    module = _load_module()

    def forbidden_runner(*_args, **_kwargs):
        raise AssertionError("runner must not be called")

    projection = module.collect_runtime_projection(
        container="odysseus; printenv", runner=forbidden_runner
    )

    assert projection["status"] == "blocked"
    assert projection["error_code"] == "invalid_container_name"


def test_repository_instructions_require_the_safe_probe_and_forbid_raw_sources() -> None:
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    context = (ROOT / "ops" / "homeserver" / "CONTEXT.md").read_text(
        encoding="utf-8"
    )
    combined = agents + "\n" + context

    assert "redacted_runtime_probe.py" in combined
    assert "podman inspect … .Config.Env" in combined
    assert "systemctl show Environment" in combined
    assert "No credential value, prefix, suffix, length, or hash" in combined


def test_probe_only_ssh_alias_is_fixed_and_admin_alias_remains_unrestricted() -> None:
    config = SSH_CONFIG_PATH.read_text(encoding="utf-8")
    probe_marker = "Host odysseus-homeserver-probe\n"
    admin_marker = "Host odysseus-homeserver\n"

    assert config.count(probe_marker) == 1
    assert config.count(admin_marker) == 1
    probe_block = config.split(probe_marker, 1)[1].split(admin_marker, 1)[0]
    admin_block = config.split(admin_marker, 1)[1]

    assert (
        "RemoteCommand cd /opt/odysseus && exec python3 "
        "ops/homeserver/redacted_runtime_probe.py"
    ) in probe_block
    assert "RequestTTY no" in probe_block
    assert "ClearAllForwardings yes" in probe_block
    assert "ForwardAgent no" in probe_block
    assert "StdinNull yes" in probe_block
    assert "RemoteCommand" not in admin_block
    assert "ClearAllForwardings" not in admin_block
    assert "RequestTTY" not in admin_block
