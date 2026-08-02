from __future__ import annotations

from io import BytesIO
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

from ops.homeserver import redacted_security_access_alert_live_readback as readback
from ops.homeserver import redacted_security_access_alert_live_smoke_transport as transport


REVISION = "a" * 40
MANIFEST = "b" * 64
SOURCE_IP = "8.8.8.8"


class _RejectedLogin:
    def open(self, request, timeout):
        assert request.full_url == transport.LOGIN_ENDPOINT
        assert timeout == 10
        raise HTTPError(
            transport.LOGIN_ENDPOINT,
            401,
            "synthetic provider text",
            {},
            BytesIO(b"body-must-not-cross-boundary"),
        )


def _packet():
    return {
        "revision": REVISION,
        "manifest_sha256": MANIFEST,
        "expires_at": 220.0,
    }


def test_exact_published_readback_pin_and_literal_remote_bootstrap():
    root = Path.cwd()
    source = (root / transport.READBACK_PATH).read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(source).hexdigest() == transport.PUBLISHED_READBACK_SHA256
    assert transport.PUBLISHED_READBACK_SHA256 in transport._BOOTSTRAP
    assert transport.SSH_COMMAND[:4] == (
        "ssh",
        "-F",
        "ops/homeserver/ssh_config",
        "odysseus-homeserver",
    )
    encoded = " ".join(transport.SSH_COMMAND)
    assert "printenv" not in encoded
    assert " inspect " not in encoded
    assert ".Config.Env" not in encoded


def test_one_rejected_login_reaches_one_read_only_remote_observation(monkeypatch):
    source = Path(transport.READBACK_PATH).read_bytes().replace(b"\r\n", b"\n")
    calls = []
    ok = readback._envelope("ok", {key: True for key in readback._PROOFS})
    assert transport.LiveSmokePacket.from_mapping(_packet()).valid(now=100.0)
    assert transport._trigger_login(_RejectedLogin()) is True

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        if command[:3] == ["git", "cat-file", "blob"]:
            return SimpleNamespace(returncode=0, stdout=source)
        assert tuple(command) == transport.SSH_COMMAND
        bundle = json.loads(kwargs["input"])
        assert bundle["execute"] is True
        assert bundle["packet"]["source_ip"] == SOURCE_IP
        assert bundle["packet"]["synthetic_login_rejected"] is True
        return SimpleNamespace(
            returncode=0,
            stdout=(json.dumps(ok, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )

    snapshot = SimpleNamespace(addresses=(SOURCE_IP,))
    result = transport.collect_published_live_smoke(
        _packet(),
        execute=True,
        runner=runner,
        discoverer=lambda **_kwargs: snapshot,
        opener=_RejectedLogin(),
        clock=lambda: 100.0,
    )

    assert result["status"] == "ok"
    assert result["retry_permitted"] is False
    assert len(calls) == 2
    assert SOURCE_IP not in json.dumps(result)
    assert readback.validate_envelope(result)


def test_default_and_unknown_trigger_paths_are_inert_without_retry(monkeypatch):
    touched = []
    assert transport.collect_published_live_smoke(
        _packet(),
        execute=False,
        runner=lambda *_args, **_kwargs: touched.append(True),
    )["status"] == "blocked"
    assert touched == []

    source = Path(transport.READBACK_PATH).read_bytes().replace(b"\r\n", b"\n")
    monkeypatch.setattr(transport, "_blob", lambda _runner: source)
    monkeypatch.setattr(transport, "_trigger_login", lambda _opener: False)
    result = transport.collect_published_live_smoke(
        _packet(),
        execute=True,
        discoverer=lambda **_kwargs: SimpleNamespace(addresses=(SOURCE_IP,)),
        clock=lambda: 100.0,
    )
    assert result["status"] == "blocked"
    assert all(result[key] is False for key in readback._PROOFS)


def test_invalid_packet_and_main_emit_only_fixed_blocked_projection(capsys):
    invalid = _packet()
    invalid["extra"] = True
    assert transport.collect_published_live_smoke(invalid, execute=True)["status"] == "blocked"
    assert transport.main([]) == 1
    value = json.loads(capsys.readouterr().out)
    assert readback.validate_envelope(value)
