#!/usr/bin/env python3
"""One-shot published-blob transport for an end-to-end access-alert smoke."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import math
import shlex
import subprocess
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ops.homeserver import redacted_security_access_alert_live_readback as readback
from src.security_incident_egress import discover_public_egress_snapshot


PUBLISHED_REF = "refs/remotes/fuzzy/dev"
READBACK_PATH = "ops/homeserver/redacted_security_access_alert_live_readback.py"
PUBLISHED_READBACK_SHA256 = "acdca4919b31b5c4c872000c4f885bad98db5eda3c1088d0f916484a99666b5a"
LOGIN_ENDPOINT = "https://odysseus.katzarow.de/api/auth/login"
_DUMMY_PASSWORD = "codex-live-smoke-not-a-credential-7fe3"
_BOOTSTRAP = """import base64,hashlib,json,sys,types
sys.path.insert(0,'/opt/odysseus')
raw=sys.stdin.buffer.read(400001)
if len(raw)>400000: raise SystemExit(2)
b=json.loads(raw.decode('utf-8'))
if type(b) is not dict or set(b)!={'packet','execute','readback'} or b['execute'] is not True: raise SystemExit(2)
item=b['readback'];expected='acdca4919b31b5c4c872000c4f885bad98db5eda3c1088d0f916484a99666b5a'
if type(item) is not dict or set(item)!={'sha256','source'} or item['sha256']!=expected: raise SystemExit(2)
source=base64.b64decode(item['source'],validate=True)
if hashlib.sha256(source).hexdigest()!=expected: raise SystemExit(2)
name='ops.homeserver.redacted_security_access_alert_live_readback'
module=types.ModuleType(name);module.__file__='<published>';sys.modules[name]=module
exec(compile(source,module.__file__,'exec'),module.__dict__)
result=module.production_entrypoint(b['packet'],execute=True)
print(json.dumps(result,ensure_ascii=True,sort_keys=True,separators=(',',':')))"""
SSH_COMMAND = (
    "ssh",
    "-F",
    "ops/homeserver/ssh_config",
    "odysseus-homeserver",
    "cd /opt/odysseus && exec /usr/bin/timeout --signal=KILL 110s /usr/bin/python3 -I -c "
    + shlex.quote(_BOOTSTRAP),
)


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True, slots=True)
class LiveSmokePacket:
    revision: str
    manifest_sha256: str
    expires_at: float

    @classmethod
    def from_mapping(cls, value: Any) -> "LiveSmokePacket | None":
        if not isinstance(value, Mapping) or set(value) != {
            "revision",
            "manifest_sha256",
            "expires_at",
        }:
            return None
        try:
            candidate = cls(
                revision=value["revision"],
                manifest_sha256=value["manifest_sha256"],
                expires_at=float(value["expires_at"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
        return candidate if candidate._static_valid() else None

    def valid(self, *, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        return bool(
            self._static_valid()
            and isinstance(current, (int, float))
            and not isinstance(current, bool)
            and math.isfinite(float(current))
            and 30 <= self.expires_at - float(current) <= 180
        )

    def _static_valid(self) -> bool:
        return bool(
            isinstance(self.revision, str)
            and len(self.revision) == 40
            and all(char in "0123456789abcdef" for char in self.revision)
            and isinstance(self.manifest_sha256, str)
            and len(self.manifest_sha256) == 64
            and all(char in "0123456789abcdef" for char in self.manifest_sha256)
            and type(self.expires_at) is float
            and math.isfinite(self.expires_at)
            and self.expires_at > 0
        )


def _blob(runner: Callable[..., Any]) -> bytes | None:
    try:
        result = runner(
            ["git", "cat-file", "blob", f"{PUBLISHED_REF}:{READBACK_PATH}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=False,
            timeout=5,
            check=False,
            shell=False,
        )
    except Exception:
        return None
    source = getattr(result, "stdout", None)
    if (
        getattr(result, "returncode", None) != 0
        or type(source) is not bytes
        or not 0 < len(source) <= 300000
        or hashlib.sha256(source).hexdigest() != PUBLISHED_READBACK_SHA256
    ):
        return None
    return source


def _trigger_login(opener: Any = None) -> bool:
    payload = json.dumps(
        {
            "username": readback.SYNTHETIC_USERNAME,
            "password": _DUMMY_PASSWORD,
            "remember": False,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    request = Request(
        LOGIN_ENDPOINT,
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Odysseus-Ops-Alert-Live-Smoke/1",
        },
        method="POST",
    )
    selected = opener if opener is not None else build_opener(_NoRedirectHandler())
    try:
        response = selected.open(request, timeout=10)
    except HTTPError as exc:
        try:
            return exc.code == 401 and exc.geturl() == LOGIN_ENDPOINT
        finally:
            exc.close()
    except Exception:
        return False
    try:
        return False
    finally:
        response.close()


def collect_published_live_smoke(
    packet: Any = None,
    *,
    execute: bool = False,
    runner: Callable[..., Any] = subprocess.run,
    discoverer: Callable[..., Any] = discover_public_egress_snapshot,
    opener: Any = None,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    blocked = readback._envelope("blocked")
    if execute is not True:
        return blocked
    try:
        now = float(clock())
    except Exception:
        return blocked
    parsed = LiveSmokePacket.from_mapping(packet)
    if parsed is None or not parsed.valid(now=now):
        return blocked
    source = _blob(runner)
    if source is None:
        return blocked
    try:
        snapshot = discoverer(clock=clock)
        source_ip = snapshot.addresses[0]
    except Exception:
        return blocked
    issued_at = float(clock())
    if not _trigger_login(opener):
        return blocked
    expectation = {
        "revision": parsed.revision,
        "manifest_sha256": parsed.manifest_sha256,
        "source_ip": source_ip,
        "issued_at": issued_at,
        "expires_at": parsed.expires_at,
        "synthetic_login_rejected": True,
    }
    if readback.LiveReadbackExpectation.from_mapping(expectation) is None:
        return blocked
    bundle = {
        "packet": expectation,
        "execute": True,
        "readback": {
            "sha256": PUBLISHED_READBACK_SHA256,
            "source": base64.b64encode(source).decode("ascii"),
        },
    }
    try:
        result = runner(
            list(SSH_COMMAND),
            input=json.dumps(bundle, ensure_ascii=True, separators=(",", ":")).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=False,
            timeout=125,
            check=False,
            shell=False,
        )
    except Exception:
        return blocked
    raw = getattr(result, "stdout", None)
    try:
        if (
            getattr(result, "returncode", None) not in {0, 1}
            or type(raw) is not bytes
            or len(raw) > 8192
            or raw.count(b"\n") != 1
            or not raw.endswith(b"\n")
        ):
            raise ValueError
        envelope = json.loads(raw.decode("utf-8"))
    except Exception:
        return blocked
    return dict(envelope) if readback.validate_envelope(envelope) else blocked


def main(argv: list[str] | None = None) -> int:
    payload = readback._envelope("blocked")
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
