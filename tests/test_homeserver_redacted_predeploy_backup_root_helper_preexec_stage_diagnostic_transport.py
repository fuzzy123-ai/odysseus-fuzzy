from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from ops.homeserver import redacted_predeploy_backup_root_helper_preexec_stage_diagnostic as diagnostic
from ops.homeserver import redacted_predeploy_backup_root_helper_preexec_stage_diagnostic_transport as transport


ROOT = Path(__file__).resolve().parents[1]


def _observed():
    return diagnostic.envelope("observed", "stage_failed", "source_move_mount", locked=True, bound=True, invoked=True)


def test_pin_matches_source_and_default_is_inert() -> None:
    source = (ROOT / transport.DIAGNOSTIC_PATH).read_bytes()
    assert hashlib.sha256(source).hexdigest() == transport.PUBLISHED_DIAGNOSTIC_SHA256
    assert transport.collect_published_root_helper_preexec_stage_diagnostic()["error_code"] == "execution_disabled"


def test_transport_sends_only_pinned_source_and_accepts_fixed_envelope() -> None:
    source = (ROOT / transport.DIAGNOSTIC_PATH).read_bytes(); calls = []
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        if command[:3] == ["git", "cat-file", "blob"]: return SimpleNamespace(returncode=0, stdout=source)
        return SimpleNamespace(returncode=0, stdout=(json.dumps(_observed(), separators=(",", ":")) + "\n").encode("ascii"))
    value = transport.collect_published_root_helper_preexec_stage_diagnostic(execute=True, runner=runner)
    assert value == _observed() and len(calls) == 2
    bundle = json.loads(calls[1][1]["input"].decode("ascii"))
    assert set(bundle) == {"execute", "sha256", "source"}
    assert base64.b64decode(bundle["source"], validate=True) == source


def test_predispatch_mismatch_blocks_and_postdispatch_ambiguity_is_unknown() -> None:
    value = transport.collect_published_root_helper_preexec_stage_diagnostic(execute=True, runner=lambda command, **kwargs: SimpleNamespace(returncode=0, stdout=b"wrong"))
    assert value["status"] == "blocked"
    source = (ROOT / transport.DIAGNOSTIC_PATH).read_bytes()
    def runner(command, **kwargs):
        if command[:3] == ["git", "cat-file", "blob"]: return SimpleNamespace(returncode=0, stdout=source)
        return SimpleNamespace(returncode=1, stdout=b"")
    value = transport.collect_published_root_helper_preexec_stage_diagnostic(execute=True, runner=runner)
    assert value["status"] == "unknown" and value["repository_write_invoked"] is False
    assert diagnostic.validate_envelope(value)


def test_remote_bootstrap_is_fixed_root_and_contains_no_checkout_execution() -> None:
    assert transport.SSH_COMMAND[:4] == ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver")
    assert "/usr/bin/sudo -n /usr/bin/python3 -I -c" in transport.REMOTE_COMMAND
    assert "cd /opt/odysseus" not in transport.REMOTE_COMMAND
    assert transport.PUBLISHED_DIAGNOSTIC_SHA256 in transport._BOOTSTRAP
