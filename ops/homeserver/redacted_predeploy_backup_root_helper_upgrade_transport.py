#!/usr/bin/env python3
"""Published-blob transport for the exact v1-to-fixed root-helper upgrade."""
from __future__ import annotations

import base64
import hashlib
import json
import shlex
import subprocess
import threading
from types import SimpleNamespace
from typing import Any, Callable

from ops.homeserver import redacted_predeploy_backup_root_helper_install_readback as install_readback
from ops.homeserver import redacted_predeploy_backup_root_helper_upgrade as upgrade


PUBLISHED_REF = "refs/remotes/fuzzy/dev"
UPGRADE_PATH = "ops/homeserver/redacted_predeploy_backup_root_helper_upgrade.py"
HELPER_PATH = "ops/homeserver/redacted_predeploy_backup_root_helper.py"
READBACK_PATH = "ops/homeserver/redacted_predeploy_backup_root_helper_readback.py"
INSTALL_READBACK_PATH = "ops/homeserver/redacted_predeploy_backup_root_helper_install_readback.py"
PUBLISHED_UPGRADE_SHA256 = "c7b0e7ecc73395fa582bfe294e98b7c523d46abd1458a19493e3d7a9af875560"
PUBLISHED_HELPER_SHA256 = "56119595274556615a3e83e1f637bd2035232180a0a0005aa3938d08ca3efb81"
PUBLISHED_READBACK_SHA256 = "e647b6f1faa409f42cbeb80c74826b730695d9e0fad14cf47aa22c1a59a0a046"
PUBLISHED_INSTALL_READBACK_SHA256 = "497c152c99ed992dbb4ba3db2ddc06a6e7200082cfdfaf8e37988c60fadcc83b"
MAX_BLOB_BYTES = 400_000
MAX_STDIN_BYTES = 1_800_000
MAX_STDOUT_BYTES = 8_192
TRANSPORT_TIMEOUT_SECONDS = 45
SSH_PREFIX = ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver")

_BOOTSTRAP = r'''import base64,hashlib,json,sys,types
raw=sys.stdin.buffer.read(1800001)
if not raw or len(raw)>1800000: raise SystemExit(70)
try: bundle=json.loads(raw.decode("ascii"))
except Exception: raise SystemExit(70)
pins={"upgrade_sha256":"c7b0e7ecc73395fa582bfe294e98b7c523d46abd1458a19493e3d7a9af875560","helper_sha256":"56119595274556615a3e83e1f637bd2035232180a0a0005aa3938d08ca3efb81","readback_sha256":"e647b6f1faa409f42cbeb80c74826b730695d9e0fad14cf47aa22c1a59a0a046","install_readback_sha256":"497c152c99ed992dbb4ba3db2ddc06a6e7200082cfdfaf8e37988c60fadcc83b"}
expected={"execute","upgrade_sha256","upgrade_source","helper_sha256","helper_source","readback_sha256","readback_source","install_readback_sha256","install_readback_source"}
if type(bundle) is not dict or set(bundle)!=expected or bundle.get("execute") is not True or any(bundle.get(k)!=v for k,v in pins.items()): raise SystemExit(70)
decoded={}
for name in ("upgrade","helper","readback","install_readback"):
    try: source=base64.b64decode(bundle[name+"_source"],validate=True)
    except Exception: raise SystemExit(70)
    if not source or len(source)>400000 or hashlib.sha256(source).hexdigest()!=pins[name+"_sha256"]: raise SystemExit(70)
    decoded[name]=source
modules=[]
try:
    for name in ("upgrade","install_readback"):
        module=types.ModuleType("_odysseus_pinned_"+name+"_"); module.__file__="<pinned-"+name+">"; sys.modules[module.__name__]=module; modules.append(module)
        exec(compile(decoded[name],module.__file__,"exec"),module.__dict__,module.__dict__)
    upgrade_module,readback_module=modules
    result=upgrade_module.upgrade(execute=True,helper_source=decoded["helper"],readback_source=decoded["readback"],operations=upgrade_module.SecureHostOperations())
    observed=readback_module.collect()
    if upgrade_module.validate_receipt(result) is not True or readback_module.validate(observed) is not True: raise SystemExit(70)
    print(json.dumps({"readback":observed,"receipt":result},ensure_ascii=True,sort_keys=True,separators=(",",":")))
finally:
    for module in modules: sys.modules.pop(module.__name__,None)
'''
REMOTE_COMMAND = "/usr/bin/timeout --signal=KILL 40s /usr/bin/sudo -n /usr/bin/python3 -I -c " + shlex.quote(_BOOTSTRAP)
SSH_COMMAND = SSH_PREFIX + (REMOTE_COMMAND,)


def _blob(path: str, digest: str, runner: Callable[..., Any]) -> bytes | None:
    try: result = runner(["git", "cat-file", "blob", f"{PUBLISHED_REF}:{path}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=False, timeout=5, check=False, shell=False)
    except Exception: return None
    source = getattr(result, "stdout", None)
    return source if getattr(result, "returncode", None) == 0 and type(source) is bytes and 0 < len(source) <= MAX_BLOB_BYTES and hashlib.sha256(source).hexdigest() == digest else None


def _bundle(runner: Callable[..., Any]) -> dict[str, Any] | None:
    specifications = (("upgrade", UPGRADE_PATH, PUBLISHED_UPGRADE_SHA256), ("helper", HELPER_PATH, PUBLISHED_HELPER_SHA256), ("readback", READBACK_PATH, PUBLISHED_READBACK_SHA256), ("install_readback", INSTALL_READBACK_PATH, PUBLISHED_INSTALL_READBACK_SHA256))
    value: dict[str, Any] = {"execute": True}
    for name, path, digest in specifications:
        source = _blob(path, digest, runner)
        if source is None: return None
        value[name + "_sha256"] = digest; value[name + "_source"] = base64.b64encode(source).decode("ascii")
    return value


def _bounded_process(command: tuple[str, ...], payload: bytes, timeout: int) -> Any:
    process = subprocess.Popen(list(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False)
    output = bytearray(); oversized = threading.Event(); failed = threading.Event()
    def writer() -> None:
        try:
            assert process.stdin is not None
            process.stdin.write(payload); process.stdin.close()
        except Exception: failed.set()
    def reader() -> None:
        try:
            assert process.stdout is not None
            while True:
                piece = process.stdout.read(min(4096, MAX_STDOUT_BYTES + 1 - len(output)))
                if not piece: return
                output.extend(piece)
                if len(output) > MAX_STDOUT_BYTES: oversized.set(); process.kill(); return
        except Exception:
            failed.set()
            try: process.kill()
            except Exception: pass
    writers=threading.Thread(target=writer,daemon=True); readers=threading.Thread(target=reader,daemon=True); writers.start(); readers.start()
    try: returncode=process.wait(timeout=timeout)
    except subprocess.TimeoutExpired: process.kill(); process.wait(); raise
    finally:
        writers.join(1); readers.join(1)
        for stream in (process.stdin,process.stdout):
            try:
                if stream is not None: stream.close()
            except Exception: pass
    if writers.is_alive() or readers.is_alive() or failed.is_set(): raise RuntimeError("transport ambiguous")
    return SimpleNamespace(returncode=returncode,stdout=bytes(output),stdout_oversized=oversized.is_set())


def _unknown() -> dict[str, Any]:
    return upgrade.receipt("unknown", "postflight_failed", invoked=True, effect=True, recovery=True)


def collect_published_root_helper_upgrade(*, execute: bool = False, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    if execute is not True: return upgrade.receipt("blocked", "execution_disabled")
    bundle = _bundle(runner)
    if bundle is None: return upgrade.receipt("blocked", "source_mismatch")
    payload = json.dumps(bundle, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    if len(payload) > MAX_STDIN_BYTES: return upgrade.receipt("blocked", "source_mismatch")
    try:
        result = _bounded_process(SSH_COMMAND, payload, TRANSPORT_TIMEOUT_SECONDS) if runner is subprocess.run else runner(list(SSH_COMMAND), input=payload, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=False, timeout=TRANSPORT_TIMEOUT_SECONDS, check=False, shell=False)
    except Exception: return _unknown()
    raw = getattr(result, "stdout", None)
    try:
        if getattr(result, "returncode", None) not in {0, 1} or getattr(result, "stdout_oversized", False) is not False or type(raw) is not bytes or len(raw) > MAX_STDOUT_BYTES or raw.count(b"\n") != 1 or not raw.endswith(b"\n") or b"\r" in raw: raise ValueError
        value=json.loads(raw[:-1].decode("ascii"))
        if type(value) is not dict or set(value)!={"receipt","readback"} or not upgrade.validate_receipt(value["receipt"]) or not install_readback.validate(value["readback"]): raise ValueError
        if value["receipt"].get("status") != "upgraded" or value["readback"].get("status") != "available": raise ValueError
    except Exception: return _unknown()
    return dict(value["receipt"])


def main() -> int:
    print(json.dumps(upgrade.receipt("blocked", "execution_disabled"), ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__": raise SystemExit(main())
