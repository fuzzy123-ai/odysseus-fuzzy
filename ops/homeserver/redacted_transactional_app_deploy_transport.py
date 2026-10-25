#!/usr/bin/env python3
"""Fixed SSH transport for the published transactional deploy packet."""
from __future__ import annotations

import base64
import hashlib
import json
import shlex
import subprocess
import sys
from typing import Any, Callable, Mapping

from ops.homeserver import redacted_maintenance_lifecycle_bootstrap_transport as local_process_transport
from ops.homeserver import redacted_transactional_app_deploy as executor


SCHEMA_ID = "odysseus.redacted_transactional_app_deploy_transport.v3"
PUBLISHED_REF = "refs/remotes/fuzzy/dev"
LIFECYCLE_PATH = "ops/homeserver/redacted_maintenance_lifecycle_lock.py"
OBSERVER_PATH = "ops/homeserver/redacted_backup_snapshot_observation.py"
EXECUTOR_PATH = "ops/homeserver/redacted_transactional_app_deploy.py"
READBACK_PATH = "ops/homeserver/redacted_transactional_app_deploy_readback.py"
PUBLISHED_LIFECYCLE_SHA256 = "82f5deae8288c94434f3728c7e0791d5af5bb08e3dbaf9f1d7591447ed72a766"
PUBLISHED_OBSERVER_SHA256 = "ce435c9f940722e40df51933beec2e4cd978dafe6aa9fe7418513077731081dd"
PUBLISHED_EXECUTOR_SHA256 = "4826b83ceede55037e7084ab84976fc681a2545e26011bc64d5afde574a5ba50"
PUBLISHED_READBACK_SHA256 = "45650dfc03f3763e2952d7609230765418e6e7705881da3c8aa1768b650e2a65"
_BLOCKED_CODES = frozenset({"invalid_invocation", "published_blob_unavailable", "published_blob_mismatch"})
_UNKNOWN_CODES = frozenset({"transport_timeout", "transport_failed", "transport_invalid", "remote_result_unknown"})
_KEYS = frozenset({"schema_id", "status", "error_code", "effect_may_have_occurred", "manual_recovery_required", "retry_permitted", "raw_output_visible", "secret_values_visible", "evidence_sha256"})
_MAX_BUNDLE_BYTES = 1_200_000


def _digest(payload: Mapping[str, Any]) -> str:
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _envelope(status: str, code: str) -> dict[str, Any]:
    unknown = status == "unknown"
    value = {
        "schema_id": SCHEMA_ID,
        "status": status,
        "error_code": code,
        "effect_may_have_occurred": unknown,
        "manual_recovery_required": unknown,
        "retry_permitted": False,
        "raw_output_visible": False,
        "secret_values_visible": False,
    }
    value["evidence_sha256"] = _digest(value)
    return value


def blocked(code: str) -> dict[str, Any]:
    return _envelope("blocked", code if type(code) is str and code in _BLOCKED_CODES else "published_blob_mismatch")


def unknown(code: str) -> dict[str, Any]:
    return _envelope("unknown", code if type(code) is str and code in _UNKNOWN_CODES else "transport_invalid")


def validate_envelope(payload: Any) -> bool:
    if type(payload) is not dict or set(payload) != _KEYS:
        return False
    if payload.get("schema_id") != SCHEMA_ID:
        return False
    status = payload.get("status")
    code = payload.get("error_code")
    if type(status) is not str or type(code) is not str:
        return False
    for key in ("effect_may_have_occurred", "manual_recovery_required", "retry_permitted", "raw_output_visible", "secret_values_visible"):
        if type(payload.get(key)) is not bool:
            return False
    digest = payload.get("evidence_sha256")
    if type(digest) is not str or digest != _digest(payload):
        return False
    if status == "blocked":
        return code in _BLOCKED_CODES and payload["effect_may_have_occurred"] is False and payload["manual_recovery_required"] is False and payload["retry_permitted"] is False
    if status == "unknown":
        return code in _UNKNOWN_CODES and payload["effect_may_have_occurred"] is True and payload["manual_recovery_required"] is True and payload["retry_permitted"] is False
    return False


def _module_specs() -> tuple[tuple[str, str, str], ...]:
    return (
        ("lifecycle", LIFECYCLE_PATH, PUBLISHED_LIFECYCLE_SHA256),
        ("observer", OBSERVER_PATH, PUBLISHED_OBSERVER_SHA256),
        ("readback", READBACK_PATH, PUBLISHED_READBACK_SHA256),
        ("executor", EXECUTOR_PATH, PUBLISHED_EXECUTOR_SHA256),
    )


def _expected_source_map() -> dict[str, str]:
    return {name: digest for name, _path, digest in _module_specs()}


_REMOTE_PROGRAM_TEMPLATE = r'''import base64,hashlib,json,sys,types
raw=sys.stdin.buffer.read(1200001)
if len(raw)>1200000: raise SystemExit(2)
try:
 b=json.loads(raw.decode('utf-8'))
 if type(b) is not dict or set(b)!={'packet','execute','sources'} or type(b['execute']) is not bool: raise ValueError
 expected=__EXPECTED__
 if type(b['sources']) is not dict or set(b['sources'])!=set(expected): raise ValueError
 packages={'ops':types.ModuleType('ops'),'ops.homeserver':types.ModuleType('ops.homeserver')}
 packages['ops'].__path__=[]; packages['ops'].__package__='ops'
 packages['ops.homeserver'].__path__=[]; packages['ops.homeserver'].__package__='ops'
 sys.modules.update(packages)
 names={'lifecycle':'ops.homeserver.redacted_maintenance_lifecycle_lock','observer':'ops.homeserver.redacted_backup_snapshot_observation','readback':'ops.homeserver.redacted_transactional_app_deploy_readback','executor':'ops.homeserver.redacted_transactional_app_deploy'}
 loaded={}
 for key in ('lifecycle','observer','readback','executor'):
  item=b['sources'][key]
  if type(item) is not dict or set(item)!={'sha256','source'} or type(item['sha256']) is not str or type(item['source']) is not str: raise ValueError
  if item['sha256']!=expected[key]: raise ValueError
  source=base64.b64decode(item['source'],validate=True)
  if hashlib.sha256(source).hexdigest()!=expected[key]: raise ValueError
  module=types.ModuleType(names[key]); module.__file__='<published>'; module.__package__='ops.homeserver'
  sys.modules[names[key]]=module; setattr(packages['ops.homeserver'],names[key].rsplit('.',1)[1],module)
  exec(compile(source,module.__file__,'exec'),module.__dict__); loaded[key]=module
 packet=loaded['executor'].DeployPacket.from_mapping(b['packet'])
 if b['execute'] is True and packet is None: raise ValueError
 result=loaded['executor'].production_entrypoint(b['packet'],execute=b['execute'])
 if loaded['executor'].validate_envelope(result) is not True: raise ValueError
 sys.stdout.write(json.dumps(result,ensure_ascii=True,sort_keys=True,separators=(',',':'))+'\n')
except BaseException:
 raise SystemExit(2)
'''


def _render_remote_program() -> str:
    return _REMOTE_PROGRAM_TEMPLATE.replace("__EXPECTED__", repr(_expected_source_map()))


_REMOTE_PROGRAM = _render_remote_program()
SSH_COMMAND = (
    "ssh",
    "-F",
    "ops/homeserver/ssh_config",
    "odysseus-homeserver",
    "cd /opt/odysseus && exec /usr/bin/timeout --signal=TERM --kill-after=10s 1200s /usr/bin/python3 -I -c " + shlex.quote(_REMOTE_PROGRAM),
)


def _out(result: Any) -> bytes | None:
    value = getattr(result, "stdout", None)
    return value if type(value) is bytes else None


def _blob(path: str, expected: str, runner: Callable[..., Any]) -> bytes | None:
    if type(expected) is not str or len(expected) != 64:
        return None
    try:
        result = runner(
            ["git", "cat-file", "blob", f"{PUBLISHED_REF}:{path}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=False,
            timeout=5,
            check=False,
            shell=False,
        )
    except Exception:
        return None
    value = _out(result)
    return value if getattr(result, "returncode", None) == 0 and value and len(value) <= 300_000 and hashlib.sha256(value).hexdigest() == expected else None


def _source_payload(sources: Mapping[str, bytes]) -> dict[str, dict[str, str]]:
    return {
        key: {"sha256": _expected_source_map()[key], "source": base64.b64encode(sources[key]).decode("ascii")}
        for key in _expected_source_map()
    }


def invoke_bundle(bundle: Any, *, production_entrypoint: Callable[..., dict[str, Any]] = executor.production_entrypoint) -> dict[str, Any]:
    """Pure packet and pin gate retained for focused adversarial tests."""
    try:
        if type(bundle) is not dict or set(bundle) != {"packet", "execute", "sources"}:
            raise ValueError
        if bundle["execute"] is not True or executor.DeployPacket.from_mapping(bundle["packet"]) is None:
            raise ValueError
        source_set = _expected_source_map()
        sources = bundle["sources"]
        if type(sources) is not dict or set(sources) != set(source_set):
            raise ValueError
        for key, expected in source_set.items():
            item = sources[key]
            if type(item) is not dict or set(item) != {"sha256", "source"} or item["sha256"] != expected or type(item["source"]) is not str:
                raise ValueError
            if hashlib.sha256(base64.b64decode(item["source"], validate=True)).hexdigest() != expected:
                raise ValueError
        result = production_entrypoint(bundle["packet"], execute=True)
        return result if executor.validate_envelope(result) else unknown("transport_invalid")
    except Exception:
        return blocked("published_blob_mismatch")


def _run_local_process_group(command: tuple[str, ...], *, input_bytes: bytes, timeout: int) -> subprocess.CompletedProcess[bytes]:
    return local_process_transport._run_local_process_group(command, input_bytes=input_bytes, timeout=timeout)


def collect_published_transactional_app_deploy(
    packet: Any = None,
    *,
    execute: bool = False,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    parsed = executor.DeployPacket.from_mapping(packet)
    if execute is not True or parsed is None:
        return blocked("invalid_invocation")
    sources: dict[str, bytes] = {}
    for key, path, expected in _module_specs():
        source = _blob(path, expected, runner)
        if source is None:
            return blocked("published_blob_mismatch")
        sources[key] = source
    bundle = {
        "packet": packet,
        "execute": True,
        "sources": _source_payload(sources),
    }
    try:
        data = json.dumps(bundle, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    except Exception:
        return blocked("published_blob_mismatch")
    if len(data) > _MAX_BUNDLE_BYTES:
        return blocked("published_blob_mismatch")
    try:
        if runner is subprocess.run:
            result = _run_local_process_group(SSH_COMMAND, input_bytes=data, timeout=1230)
        else:
            result = runner(
                list(SSH_COMMAND),
                input=data,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=False,
                timeout=1230,
                check=False,
                shell=False,
            )
    except subprocess.TimeoutExpired:
        return unknown("transport_timeout")
    except Exception:
        return unknown("transport_failed")
    if getattr(result, "timed_out", False) is True:
        return unknown("transport_timeout")
    raw = _out(result)
    try:
        if raw is None or len(raw) > 8192 or raw.count(b"\n") != 1 or not raw.endswith(b"\n"):
            raise ValueError
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return unknown("transport_invalid")
    if not executor.validate_envelope(payload) or type(getattr(result, "returncode", None)) is not int or result.returncode not in {0, 1}:
        return unknown("transport_invalid")
    return {key: payload[key] for key in sorted(payload)}


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    payload = blocked("invalid_invocation") if not arguments else blocked("invalid_invocation")
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
