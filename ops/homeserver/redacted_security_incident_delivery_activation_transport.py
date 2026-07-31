#!/usr/bin/env python3
"""Fixed stdin-only transport for the published delivery-activation transaction."""
from __future__ import annotations

import base64
import hashlib
import json
import shlex
import subprocess
import sys
from typing import Any, Callable, Mapping

from ops.homeserver import redacted_security_incident_delivery_activation as activation

SCHEMA_ID = "odysseus.redacted_security_incident_delivery_activation_transport.v1"
PUBLISHED_REF = "refs/remotes/fuzzy/dev"
ACTIVATION_PATH = "ops/homeserver/redacted_security_incident_delivery_activation.py"
READBACK_PATH = "ops/homeserver/redacted_security_incident_delivery_activation_readback.py"
PUBLISHED_ACTIVATION_SHA256 = "32591f0f1dc1027232e041be3676abf604f530fcd10a1c2c513edd4e07446b8b"
PUBLISHED_READBACK_SHA256 = "f0334e42fa4dc12146b6ba1208443da903c8b404fde80f648028d6bbacbc1e50"
_CODES = frozenset({"published_blob_unavailable", "published_blob_mismatch", "transport_timeout", "transport_failed", "transport_invalid", "invalid_invocation"})
_KEYS = frozenset({"schema_id", "status", "error_code", "retry_permitted", "evidence_sha256"})
_BOOTSTRAP = """import base64,hashlib,json,sys,types
sys.path.insert(0,'/opt/odysseus')
raw=sys.stdin.buffer.read(800001)
if len(raw)>800000: raise SystemExit(2)
b=json.loads(raw.decode('utf-8'))
if type(b) is not dict or set(b)!={'packet','execute','activation','readback'} or type(b['execute']) is not bool: raise SystemExit(2)
EXPECTED={'activation':'32591f0f1dc1027232e041be3676abf604f530fcd10a1c2c513edd4e07446b8b','readback':'f0334e42fa4dc12146b6ba1208443da903c8b404fde80f648028d6bbacbc1e50'}
for name,module_name in (('readback','ops.homeserver.redacted_security_incident_delivery_activation_readback'),('activation','ops.homeserver.redacted_security_incident_delivery_activation')):
 item=b[name]
 if type(item) is not dict or set(item)!={'sha256','source'}: raise SystemExit(2)
 source=base64.b64decode(item['source'],validate=True)
 if item['sha256']!=EXPECTED[name] or hashlib.sha256(source).hexdigest()!=EXPECTED[name]: raise SystemExit(2)
 module=types.ModuleType(module_name);module.__file__='<published>';sys.modules[module_name]=module;exec(compile(source,module.__file__,'exec'),module.__dict__)
p=sys.modules['ops.homeserver.redacted_security_incident_delivery_activation'].production_entrypoint(b['packet'],execute=b['execute'])
print(json.dumps(p,ensure_ascii=True,sort_keys=True,separators=(',',':')))"""
SSH_COMMAND = ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver", "cd /opt/odysseus && exec /usr/bin/timeout --signal=KILL 300s /usr/bin/python3 -I -c " + shlex.quote(_BOOTSTRAP))


def _digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in payload.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def blocked(code: str) -> dict[str, Any]:
    payload = {"schema_id": SCHEMA_ID, "status": "blocked", "error_code": code if code in _CODES else "transport_invalid", "retry_permitted": False}; payload["evidence_sha256"] = _digest(payload); return payload


def validate_envelope(value: Any) -> bool:
    return type(value) is dict and set(value) == _KEYS and value.get("schema_id") == SCHEMA_ID and value.get("status") == "blocked" and value.get("error_code") in _CODES and value.get("retry_permitted") is False and type(value.get("evidence_sha256")) is str and value["evidence_sha256"] == _digest(value)


def _blob(path: str, expected: str, runner: Callable[..., Any]) -> bytes | None:
    try: result = runner(["git", "cat-file", "blob", f"{PUBLISHED_REF}:{path}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=False, timeout=5, check=False, shell=False)
    except Exception: return None
    output = getattr(result, "stdout", None)
    return output if getattr(result, "returncode", None) == 0 and type(output) is bytes and 0 < len(output) <= 400000 and hashlib.sha256(output).hexdigest() == expected else None


def invoke_bundle(bundle: Any, *, production_entrypoint: Callable[..., Mapping[str, Any]] = activation.production_entrypoint) -> dict[str, Any]:
    try:
        if type(bundle) is not dict or set(bundle) != {"packet", "execute", "activation", "readback"} or bundle["execute"] is not True or activation.ActivationPacket.from_mapping(bundle["packet"]) is None: raise ValueError
        for key, expected in (("activation", PUBLISHED_ACTIVATION_SHA256), ("readback", PUBLISHED_READBACK_SHA256)):
            item = bundle[key]
            if type(item) is not dict or set(item) != {"sha256", "source"} or item["sha256"] != expected or hashlib.sha256(base64.b64decode(item["source"], validate=True)).hexdigest() != expected: raise ValueError
        result = production_entrypoint(bundle["packet"], execute=True)
        return dict(result) if activation.validate_envelope(result) else blocked("transport_invalid")
    except Exception: return blocked("transport_invalid")


def collect_published_delivery_activation(packet: Any = None, *, execute: bool = False, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    if not execute or activation.ActivationPacket.from_mapping(packet) is None: return blocked("invalid_invocation")
    source = _blob(ACTIVATION_PATH, PUBLISHED_ACTIVATION_SHA256, runner); readback = _blob(READBACK_PATH, PUBLISHED_READBACK_SHA256, runner)
    if source is None or readback is None: return blocked("published_blob_mismatch")
    bundle = {"packet": packet, "execute": True, "activation": {"sha256": PUBLISHED_ACTIVATION_SHA256, "source": base64.b64encode(source).decode("ascii")}, "readback": {"sha256": PUBLISHED_READBACK_SHA256, "source": base64.b64encode(readback).decode("ascii")}}
    try: result = runner(list(SSH_COMMAND), input=json.dumps(bundle, ensure_ascii=True, separators=(",", ":")).encode(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=False, timeout=330, check=False, shell=False)
    except subprocess.TimeoutExpired: return blocked("transport_timeout")
    except Exception: return blocked("transport_failed")
    raw = getattr(result, "stdout", None)
    try:
        if type(raw) is not bytes or len(raw) > 8192 or raw.count(b"\n") != 1 or not raw.endswith(b"\n"): raise ValueError
        envelope = json.loads(raw.decode("utf-8"))
    except Exception: return blocked("transport_invalid")
    return dict(envelope) if getattr(result, "returncode", None) in {0, 1} and activation.validate_envelope(envelope) else blocked("transport_invalid")


def main(argv: list[str] | None = None) -> int:
    print(json.dumps(blocked("invalid_invocation"), ensure_ascii=True, sort_keys=True, separators=(",", ":"))); return 1
