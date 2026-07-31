from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from ops.homeserver import redacted_security_incident_delivery_activation as a
from ops.homeserver import redacted_security_incident_delivery_activation_transport as t


def packet(): return {"schema_id": a.PACKET_SCHEMA_ID, "expected_revision": "a" * 40, "manifest_sha256": "b" * 64, "snapshot_id": "c" * 64, "prior_snapshot_evidence_sha256": "d" * 64, "expires_at": 200, "enable": True}


def test_transport_execute_false_and_invalid_packet_never_load_or_mutate():
    assert t.collect_published_delivery_activation() ["status"] == "blocked"
    assert t.main(["--execute"]) == 1


def test_transport_pins_blob_hashes_uses_fixed_stdin_and_rejects_tamper(monkeypatch):
    source = b"x"; digest = hashlib.sha256(source).hexdigest(); monkeypatch.setattr(t, "PUBLISHED_ACTIVATION_SHA256", digest); monkeypatch.setattr(t, "PUBLISHED_READBACK_SHA256", digest); calls = []
    def run(command, **kwargs):
        calls.append((tuple(command), kwargs))
        if command[:3] == ["git", "cat-file", "blob"]: return type("R", (), {"stdout": source, "returncode": 0})()
        result = a._envelope("blocked", "preflight", "failed", False); return type("R", (), {"stdout": json.dumps(result).encode() + b"\n", "returncode": 1})()
    value = t.collect_published_delivery_activation(packet(), execute=True, runner=run)
    assert a.validate_envelope(value) and len(calls) == 3 and calls[-1][0] == t.SSH_COMMAND and b'"execute":true' in calls[-1][1]["input"]
    assert t.invoke_bundle({"packet": packet(), "execute": True, "activation": {"sha256": digest, "source": base64.b64encode(source).decode()}, "readback": {"sha256": "0" * 64, "source": base64.b64encode(source).decode()} })["status"] == "blocked"


def test_transport_timeout_and_literal_bootstrap_execute_false_are_safe(monkeypatch):
    monkeypatch.setattr(t, "_blob", lambda *_: b"x")
    value = t.collect_published_delivery_activation(packet(), execute=True, runner=lambda *_a, **_k: (_ for _ in ()).throw(subprocess.TimeoutExpired("ssh", 1)))
    assert value["error_code"] == "transport_timeout"
    root = Path.cwd(); source = (root / t.ACTIVATION_PATH).read_bytes().replace(b"\r\n", b"\n"); readback = (root / t.READBACK_PATH).read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(source).hexdigest() == t.PUBLISHED_ACTIVATION_SHA256 and hashlib.sha256(readback).hexdigest() == t.PUBLISHED_READBACK_SHA256
    bootstrap = t._BOOTSTRAP.replace("/opt/odysseus", str(root).replace("\\", "/")).replace("'0' * 64", "")
    bootstrap = bootstrap.replace("0000000000000000000000000000000000000000000000000000000000000000", hashlib.sha256(source).hexdigest(), 1).replace("0000000000000000000000000000000000000000000000000000000000000000", hashlib.sha256(readback).hexdigest(), 1)
    bundle = {"packet": packet(), "execute": False, "activation": {"sha256": hashlib.sha256(source).hexdigest(), "source": base64.b64encode(source).decode()}, "readback": {"sha256": hashlib.sha256(readback).hexdigest(), "source": base64.b64encode(readback).decode()}}
    result = subprocess.run([sys.executable, "-I", "-c", bootstrap], input=json.dumps(bundle).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    assert result.returncode == 0 and result.stderr == b"" and a.validate_envelope(json.loads(result.stdout)) and json.loads(result.stdout)["status"] == "not_executed"
