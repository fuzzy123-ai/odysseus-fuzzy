from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from ops.homeserver import redacted_predeploy_backup_root_helper_install_readback as readback
from ops.homeserver import redacted_predeploy_backup_root_helper_upgrade as upgrade
from ops.homeserver import redacted_predeploy_backup_root_helper_upgrade_transport as transport


ROOT = Path(__file__).resolve().parents[1]


def _sources():
    return {
        path: (ROOT / path).read_bytes()
        for path in (transport.UPGRADE_PATH, transport.HELPER_PATH, transport.READBACK_PATH, transport.INSTALL_READBACK_PATH)
    }


def _installed_readback() -> dict[str, object]:
    value = {"schema_id": readback.SCHEMA_ID, "status": "available", "assets_valid": True, "safe_parents": True, "state_dir_safe": True, "unit_disabled": True, "unit_inactive": True, "arm_present": False, "raw_output_visible": False, "environment_visible": False, "paths_visible": False, "secret_values_visible": False}
    value["evidence_sha256"] = readback._digest(value)
    assert readback.validate(value)
    return value


def _upgraded() -> dict[str, object]:
    return upgrade.receipt("upgraded", "none", invoked=True, helper=True, readback=True, effect=True)


def test_all_pins_match_current_sources_and_default_is_inert() -> None:
    sources = _sources()
    specifications = ((transport.UPGRADE_PATH, transport.PUBLISHED_UPGRADE_SHA256), (transport.HELPER_PATH, transport.PUBLISHED_HELPER_SHA256), (transport.READBACK_PATH, transport.PUBLISHED_READBACK_SHA256), (transport.INSTALL_READBACK_PATH, transport.PUBLISHED_INSTALL_READBACK_SHA256))
    assert all(hashlib.sha256(sources[path]).hexdigest() == digest for path, digest in specifications)
    assert all(digest in transport._BOOTSTRAP for _, digest in specifications)
    assert upgrade.NEW_HELPER_SHA256 == transport.PUBLISHED_HELPER_SHA256
    value = transport.collect_published_root_helper_upgrade()
    assert value["status"] == "blocked" and value["error_code"] == "execution_disabled"


def test_transport_sends_only_pinned_sources_and_accepts_exact_readback() -> None:
    sources = _sources(); calls = []
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        if command[:3] == ["git", "cat-file", "blob"]:
            path = command[3].split(":", 1)[1]
            return SimpleNamespace(returncode=0, stdout=sources[path])
        response = {"receipt": _upgraded(), "readback": _installed_readback()}
        return SimpleNamespace(returncode=0, stdout=(json.dumps(response, separators=(",", ":")) + "\n").encode("ascii"))
    value = transport.collect_published_root_helper_upgrade(execute=True, runner=runner)
    assert value == _upgraded() and len(calls) == 5
    bundle = json.loads(calls[-1][1]["input"].decode("ascii"))
    assert bundle["execute"] is True
    for name in ("upgrade", "helper", "readback", "install_readback"):
        assert base64.b64decode(bundle[name + "_source"], validate=True) == sources[getattr(transport, name.upper() + "_PATH")]


def test_pin_mismatch_blocks_before_ssh_and_postdispatch_failure_is_unknown() -> None:
    calls = []
    def mismatch(command, **kwargs): calls.append(command); return SimpleNamespace(returncode=0, stdout=b"wrong")
    value = transport.collect_published_root_helper_upgrade(execute=True, runner=mismatch)
    assert value["status"] == "blocked" and len(calls) == 1
    sources = _sources()
    def ambiguous(command, **kwargs):
        if command[:3] == ["git", "cat-file", "blob"]: return SimpleNamespace(returncode=0, stdout=sources[command[3].split(":", 1)[1]])
        return SimpleNamespace(returncode=1, stdout=b"")
    value = transport.collect_published_root_helper_upgrade(execute=True, runner=ambiguous)
    assert value["status"] == "unknown" and value["manual_recovery_required"] is True
    assert upgrade.validate_receipt(value)


def test_remote_bootstrap_is_fixed_root_and_checkout_independent() -> None:
    assert transport.SSH_COMMAND[:4] == ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver")
    assert "/usr/bin/sudo -n /usr/bin/python3 -I -c" in transport.REMOTE_COMMAND
    assert "cd /opt/odysseus" not in transport.REMOTE_COMMAND
    assert transport.PUBLISHED_UPGRADE_SHA256 in transport._BOOTSTRAP
