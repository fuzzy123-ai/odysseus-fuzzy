from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from ops.homeserver import redacted_predeploy_backup_root_helper_incident_diagnostic as diagnostic
from ops.homeserver import redacted_predeploy_backup_root_helper_incident_diagnostic_transport as transport


ROOT = Path(__file__).resolve().parents[1]


def _ok() -> dict[str, object]:
    values = {key: True for key in diagnostic._FLAGS}
    values["recovery_preflight_ready"] = True
    return diagnostic._envelope("ok", "none", values)


def test_pin_matches_source_and_default_is_inert() -> None:
    source = (ROOT / transport.DIAGNOSTIC_PATH).read_bytes()
    assert hashlib.sha256(source).hexdigest() == transport.PUBLISHED_DIAGNOSTIC_SHA256
    assert transport.collect_published_root_helper_incident_diagnostic()["error_code"] == "execution_disabled"


def test_transport_binds_published_source_and_only_accepts_fixed_envelope() -> None:
    source = (ROOT / transport.DIAGNOSTIC_PATH).read_bytes(); calls = []
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        if command[:3] == ["git", "cat-file", "blob"]:
            return SimpleNamespace(returncode=0, stdout=source)
        return SimpleNamespace(returncode=0, stdout=(json.dumps(_ok(), separators=(",", ":")) + "\n").encode("ascii"))
    value = transport.collect_published_root_helper_incident_diagnostic(execute=True, runner=runner)
    assert value == _ok() and len(calls) == 2
    bundle = json.loads(calls[1][1]["input"].decode("ascii"))
    assert set(bundle) == {"execute", "sha256", "source"}
    assert base64.b64decode(bundle["source"], validate=True) == source


def test_pin_or_remote_envelope_mismatch_blocks_without_output_passthrough() -> None:
    source = (ROOT / transport.DIAGNOSTIC_PATH).read_bytes()
    bad_source = transport.collect_published_root_helper_incident_diagnostic(execute=True, runner=lambda command, **kwargs: SimpleNamespace(returncode=0, stdout=b"wrong"))
    bad_remote = transport.collect_published_root_helper_incident_diagnostic(execute=True, runner=lambda command, **kwargs: SimpleNamespace(returncode=0, stdout=source) if command[:3] == ["git", "cat-file", "blob"] else SimpleNamespace(returncode=0, stdout=b"{}\n"))
    assert bad_source["status"] == "blocked" and bad_source["error_code"] == "diagnostic_failed"
    assert bad_remote["status"] == "blocked" and bad_remote["error_code"] == "diagnostic_failed"
