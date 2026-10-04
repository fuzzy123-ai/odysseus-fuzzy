#!/usr/bin/env python3
"""Fixed, one-shot stdin transport for the reviewed root-helper installer.

This file deliberately does not consult a checkout on the remote host.  The
only remote command is :data:`SSH_COMMAND`; both authority probing and the
separately authorised install use its byte-identical argv and immutable Python
bootstrap.  Nothing is retried after dispatch.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping


SCHEMA_ID = "odysseus.predeploy_backup_root_helper_install_transport.v2"
PUBLISHED_REF = "refs/remotes/fuzzy/dev"
INSTALLER_PATH = "ops/homeserver/redacted_predeploy_backup_root_helper_install.py"
HELPER_PATH = "ops/homeserver/redacted_predeploy_backup_root_helper.py"
READBACK_PATH = "ops/homeserver/redacted_predeploy_backup_root_helper_readback.py"
INSTALL_READBACK_PATH = "ops/homeserver/redacted_predeploy_backup_root_helper_install_readback.py"
PUBLISHED_INSTALLER_SHA256 = "35a05876b90addbc6102aee45995d334fc0e04ce68a7a1bef8e0633f41dd0941"
PUBLISHED_HELPER_SHA256 = "f5e6f90169c59daee07dbb82880912b8c959d2bf991748e3363b6dbbc7b57952"
PUBLISHED_READBACK_SHA256 = "8d01a6e5ec7593f57136b7bbd2af8b35a9a94f428ec623a14dd2a0e870ab516e"
PUBLISHED_INSTALL_READBACK_SHA256 = "c11ac244ebccfb54520b0e152e24a7f9cfd7002d668dd8901e5d6af0f2e93f60"
# Published pins describe the immutable, already-reviewed deployment bundle.
# Candidate pins describe this local timeout repair and must never rewrite the
# historical published record before a separately authorised publication.
CANDIDATE_INSTALLER_SHA256 = "141842f5f3e2f394215ff9c4849bd39392c35b71666f87507ad981791d0ecdd5"
CANDIDATE_HELPER_SHA256 = PUBLISHED_HELPER_SHA256
CANDIDATE_READBACK_SHA256 = PUBLISHED_READBACK_SHA256
CANDIDATE_INSTALL_READBACK_SHA256 = "052b644be24277d2cfc5e017dcc447f161ee16561fd95221b0b843797258a933"
MAX_BLOB_BYTES = 400_000
MAX_STDIN_BYTES = 1_800_000
MAX_STDOUT_BYTES = 8_192
TRANSPORT_TIMEOUT_SECONDS = 35
SSH_PREFIX = ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver")

# This source contains its own pins and has no checkout imports, path changes,
# shell evaluation, or service-management command.  The installer itself has
# the reviewed no-start/no-enable contract.
STATIC_BOOTSTRAP = r'''import base64,hashlib,json,os,sys,types
MAX=400000
PINS={"installer_sha256":"35a05876b90addbc6102aee45995d334fc0e04ce68a7a1bef8e0633f41dd0941","helper_sha256":"f5e6f90169c59daee07dbb82880912b8c959d2bf991748e3363b6dbbc7b57952","readback_sha256":"8d01a6e5ec7593f57136b7bbd2af8b35a9a94f428ec623a14dd2a0e870ab516e","install_readback_sha256":"c11ac244ebccfb54520b0e152e24a7f9cfd7002d668dd8901e5d6af0f2e93f60"}
KEYS=frozenset(("installer_sha256","installer_source","helper_sha256","helper_source","readback_sha256","readback_source","install_readback_sha256","install_readback_source"))
def die(): raise SystemExit(70)
def emit(v): sys.stdout.write(json.dumps(v,ensure_ascii=True,sort_keys=True,separators=(",",":"))+"\n")
line=sys.stdin.buffer.readline(32)
if line==b"mode=probe\n":
    if sys.stdin.buffer.read(1): die()
    if os.geteuid()!=0: die()
    emit({"euid_root":True,"mode":"probe","ssh_argv_authority":True})
elif line==b"mode=install\n":
    if os.geteuid()!=0: die()
    raw=sys.stdin.buffer.readline(1800001)
    if not raw or len(raw)>1800000 or not raw.endswith(b"\n") or b"\n" in raw[:-1]: die()
    if sys.stdin.buffer.read(1): die()
    try: bundle=json.loads(raw[:-1].decode("ascii"))
    except Exception: die()
    if type(bundle) is not dict or set(bundle)!=KEYS or any(bundle.get(k)!=v for k,v in PINS.items()): die()
    try:
        decoded={k:base64.b64decode(bundle[k],validate=True) for k in ("installer_source","helper_source","readback_source","install_readback_source")}
    except Exception: die()
    if any(not 0<len(v)<=MAX for v in decoded.values()): die()
    if any(hashlib.sha256(decoded[name+"_source"]).hexdigest()!=PINS[name+"_sha256"] for name in ("installer","helper","readback","install_readback")): die()
    installer_name="_odysseus_pinned_installer_"
    ns=types.ModuleType(installer_name); ns.__file__="<pinned-installer>"; sys.modules[installer_name]=ns
    readback_name="_odysseus_pinned_install_readback_"
    readback=types.ModuleType(readback_name); readback.__file__="<pinned-install-readback>"; sys.modules[readback_name]=readback
    try:
        exec(compile(decoded["installer_source"],"<pinned-installer>","exec"),ns.__dict__,ns.__dict__)
        exec(compile(decoded["install_readback_source"],"<pinned-install-readback>","exec"),readback.__dict__,readback.__dict__)
        receipt=ns.install(execute=True,helper_source=decoded["helper_source"],readback_source=decoded["readback_source"],operations=ns.SecureHostOperations(root="/"))
        readback_value=readback.collect_diagnostic_readback_v2()
        if not readback.validate_diagnostic(readback_value): die()
    except Exception: die()
    finally:
        sys.modules.pop(installer_name,None); sys.modules.pop(readback_name,None)
    emit({"mode":"install","receipt":receipt,"readback":readback_value})
else: die()
'''


def _bootstrap_with_pins(pins: Mapping[str, str]) -> str:
    """Bind a reviewed bootstrap to an exact, four-asset local candidate."""
    replacements = {
        PUBLISHED_INSTALLER_SHA256: pins["installer_sha256"],
        PUBLISHED_HELPER_SHA256: pins["helper_sha256"],
        PUBLISHED_READBACK_SHA256: pins["readback_sha256"],
        PUBLISHED_INSTALL_READBACK_SHA256: pins["install_readback_sha256"],
    }
    value = STATIC_BOOTSTRAP
    for published, candidate in replacements.items():
        value = value.replace(published, candidate)
    return value


CANDIDATE_PINS = {
    "installer_sha256": CANDIDATE_INSTALLER_SHA256,
    "helper_sha256": CANDIDATE_HELPER_SHA256,
    "readback_sha256": CANDIDATE_READBACK_SHA256,
    "install_readback_sha256": CANDIDATE_INSTALL_READBACK_SHA256,
}
CANDIDATE_STATIC_BOOTSTRAP = _bootstrap_with_pins(CANDIDATE_PINS)
REMOTE_COMMAND = "/usr/bin/timeout 35s /usr/bin/sudo -n /usr/bin/python3 -I -c " + shlex.quote(STATIC_BOOTSTRAP)
SSH_COMMAND = SSH_PREFIX + (REMOTE_COMMAND,)
CANDIDATE_REMOTE_COMMAND = "/usr/bin/timeout 35s /usr/bin/sudo -n /usr/bin/python3 -I -c " + shlex.quote(CANDIDATE_STATIC_BOOTSTRAP)
CANDIDATE_SSH_COMMAND = SSH_PREFIX + (CANDIDATE_REMOTE_COMMAND,)

_HEX = __import__("re").compile(r"^[0-9a-f]{64}$")
_KEYS = frozenset({"schema_id", "status", "error_code", "installation_invoked", "effect_may_have_occurred", "manual_recovery_required", "retry_permitted", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible", "evidence_sha256"})


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def _receipt(status: str, code: str, *, invoked: bool, effect: bool, recovery: bool) -> dict[str, Any]:
    value = {"schema_id": SCHEMA_ID, "status": status, "error_code": code, "installation_invoked": invoked, "effect_may_have_occurred": effect, "manual_recovery_required": recovery, "retry_permitted": False, "raw_output_visible": False, "environment_visible": False, "paths_visible": False, "secret_values_visible": False}
    value["evidence_sha256"] = _digest(value)
    return value


def blocked(code: str) -> dict[str, Any]:
    return _receipt("blocked", code if code in {"invalid_invocation", "published_blob_mismatch", "candidate_blob_mismatch"} else "invalid_invocation", invoked=False, effect=False, recovery=False)


def unknown(code: str = "transport_ambiguous") -> dict[str, Any]:
    return _receipt("unknown", code if code in {"transport_timeout", "transport_nonzero", "transport_invalid", "transport_oversize", "transport_ambiguous"} else "transport_ambiguous", invoked=True, effect=True, recovery=True)


def probe_unknown(code: str = "transport_ambiguous") -> dict[str, Any]:
    return _receipt("probe_unknown", code if code in {"transport_timeout", "transport_nonzero", "transport_invalid", "transport_oversize", "transport_ambiguous"} else "transport_ambiguous", invoked=False, effect=False, recovery=False)


def validate_envelope(value: Any) -> bool:
    flags = ("installation_invoked", "effect_may_have_occurred", "manual_recovery_required", "retry_permitted", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible")
    try:
        if type(value) is not dict or set(value) != _KEYS: return False
        if type(value.get("schema_id")) is not str or value["schema_id"] != SCHEMA_ID: return False
        if type(value.get("status")) is not str or type(value.get("error_code")) is not str: return False
        if any(type(value.get(key)) is not bool for key in flags): return False
        if type(value.get("evidence_sha256")) is not str or value["evidence_sha256"] != _digest(value): return False
        if value["retry_permitted"] is not False or any(value.get(key) is not False for key in ("raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible")): return False
        if value["status"] == "blocked":
            return value["error_code"] in {"invalid_invocation", "published_blob_mismatch", "candidate_blob_mismatch"} and all(value.get(key) is False for key in ("installation_invoked", "effect_may_have_occurred", "manual_recovery_required"))
        if value["status"] == "probe_ok":
            return value["error_code"] == "none" and all(value.get(key) is False for key in ("installation_invoked", "effect_may_have_occurred", "manual_recovery_required"))
        if value["status"] == "probe_unknown":
            return value["error_code"] in {"transport_timeout", "transport_nonzero", "transport_invalid", "transport_oversize", "transport_ambiguous"} and all(value.get(key) is False for key in ("installation_invoked", "effect_may_have_occurred", "manual_recovery_required"))
        if value["status"] == "installed":
            return value["error_code"] == "none" and value["installation_invoked"] is True and value["effect_may_have_occurred"] is True and value["manual_recovery_required"] is False
        return value["status"] == "unknown" and value["error_code"] in {"transport_timeout", "transport_nonzero", "transport_invalid", "transport_oversize", "transport_ambiguous"} and all(value.get(key) is True for key in ("installation_invoked", "effect_may_have_occurred", "manual_recovery_required"))
    except Exception:
        return False


def _published_blob(path: str, digest: str, runner: Callable[..., Any]) -> bytes | None:
    if not _HEX.fullmatch(digest): return None
    try:
        result = runner(["git", "cat-file", "blob", f"{PUBLISHED_REF}:{path}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=False, shell=False)
        data = getattr(result, "stdout", None)
        return data if getattr(result, "returncode", None) == 0 and type(data) is bytes and 0 < len(data) <= MAX_BLOB_BYTES and hashlib.sha256(data).hexdigest() == digest else None
    except Exception:
        return None


def prepare_published_install_bundle(*, runner: Callable[..., Any] = subprocess.run) -> dict[str, str] | None:
    installer = _published_blob(INSTALLER_PATH, PUBLISHED_INSTALLER_SHA256, runner)
    helper = _published_blob(HELPER_PATH, PUBLISHED_HELPER_SHA256, runner)
    readback = _published_blob(READBACK_PATH, PUBLISHED_READBACK_SHA256, runner)
    install_readback = _published_blob(INSTALL_READBACK_PATH, PUBLISHED_INSTALL_READBACK_SHA256, runner)
    if installer is None or helper is None or readback is None or install_readback is None: return None
    return {"installer_sha256": PUBLISHED_INSTALLER_SHA256, "installer_source": base64.b64encode(installer).decode("ascii"), "helper_sha256": PUBLISHED_HELPER_SHA256, "helper_source": base64.b64encode(helper).decode("ascii"), "readback_sha256": PUBLISHED_READBACK_SHA256, "readback_source": base64.b64encode(readback).decode("ascii"), "install_readback_sha256": PUBLISHED_INSTALL_READBACK_SHA256, "install_readback_source": base64.b64encode(install_readback).decode("ascii")}


def _candidate_blob(path: str, digest: str) -> bytes | None:
    """Read only a local candidate asset after its exact digest binds it."""
    try:
        data = (Path(__file__).parents[2] / path).read_bytes()
        return data if 0 < len(data) <= MAX_BLOB_BYTES and hashlib.sha256(data).hexdigest() == digest else None
    except Exception:
        return None


def prepare_candidate_install_bundle() -> dict[str, str] | None:
    """Prepare the local candidate without changing the published asset route."""
    installer = _candidate_blob(INSTALLER_PATH, CANDIDATE_INSTALLER_SHA256)
    helper = _candidate_blob(HELPER_PATH, CANDIDATE_HELPER_SHA256)
    readback = _candidate_blob(READBACK_PATH, CANDIDATE_READBACK_SHA256)
    install_readback = _candidate_blob(INSTALL_READBACK_PATH, CANDIDATE_INSTALL_READBACK_SHA256)
    if installer is None or helper is None or readback is None or install_readback is None: return None
    return {"installer_sha256": CANDIDATE_INSTALLER_SHA256, "installer_source": base64.b64encode(installer).decode("ascii"), "helper_sha256": CANDIDATE_HELPER_SHA256, "helper_source": base64.b64encode(helper).decode("ascii"), "readback_sha256": CANDIDATE_READBACK_SHA256, "readback_source": base64.b64encode(readback).decode("ascii"), "install_readback_sha256": CANDIDATE_INSTALL_READBACK_SHA256, "install_readback_source": base64.b64encode(install_readback).decode("ascii")}


def _bounded_popen(argv: tuple[str, ...], payload: bytes, *, popen: Callable[..., Any]) -> tuple[str, bytes | None]:
    """Thread-free, nonblocking duplex exchange with a bounded transcript."""
    try:
        process = popen(list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=False, bufsize=0)
    except Exception:
        return "ambiguous", None

    def cleanup() -> None:
        try: process.kill()
        except Exception: pass
        try: process.wait(timeout=1)
        except Exception: pass
        for stream in (getattr(process, "stdin", None), getattr(process, "stdout", None)):
            try: stream.close()
            except Exception: pass

    try:
        stdin, stdout = process.stdin, process.stdout
        input_fd, output_fd = stdin.fileno(), stdout.fileno()
        if type(input_fd) is not int or type(output_fd) is not int: raise OSError("fd")
        os.set_blocking(input_fd, False)
        os.set_blocking(output_fd, False)
    except Exception:
        cleanup()
        return "ambiguous", None

    output = bytearray(); offset = 0; eof = False; input_closed = False
    deadline = time.monotonic() + TRANSPORT_TIMEOUT_SECONDS
    while True:
        while not eof:
            if len(output) >= MAX_STDOUT_BYTES + 1:
                cleanup(); return "oversize", None
            try:
                piece = stdout.read(min(4096, MAX_STDOUT_BYTES + 1 - len(output)))
                if piece is None: break
                if type(piece) is not bytes: raise OSError("read")
                if piece == b"": eof = True; break
                output.extend(piece)
            except (BlockingIOError, InterruptedError):
                break
            except Exception:
                cleanup(); return "ambiguous", None
        if len(output) > MAX_STDOUT_BYTES:
            cleanup(); return "oversize", None
        if offset < len(payload):
            try:
                count = stdin.write(memoryview(payload)[offset:offset + 4096])
                if count is not None:
                    if type(count) is not int or count <= 0 or count > min(4096, len(payload) - offset): raise OSError("write")
                    offset += count
            except (BlockingIOError, InterruptedError):
                pass
            except Exception:
                cleanup(); return "ambiguous", None
        if offset == len(payload) and not input_closed:
            try: stdin.close(); input_closed = True
            except Exception:
                cleanup(); return "ambiguous", None
        try:
            code = process.poll()
        except Exception:
            cleanup(); return "ambiguous", None
        if code is not None:
            if offset != len(payload) or not eof:
                cleanup(); return "ambiguous", None
            if type(code) is not int:
                cleanup(); return "ambiguous", None
            if code != 0:
                cleanup(); return "nonzero", None
            try: stdout.close()
            except Exception: pass
            return "ok", bytes(output)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            cleanup(); return "timeout", None
        time.sleep(min(0.01, remaining))


def _one_json_line(output: bytes | None) -> Any | None:
    if type(output) is not bytes or not output.endswith(b"\n") or output.count(b"\n") != 1 or b"\r" in output:
        return None
    try:
        return json.loads(output[:-1].decode("ascii"))
    except Exception:
        return None


def _valid_install_receipt(value: Any) -> bool:
    required = {"schema_id", "status", "error_code", "helper_installed", "unit_installed", "sudo_policy_installed", "rollback_attempted", "rollback_succeeded", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible", "evidence_sha256"}
    return bool(type(value) is dict and set(value) == required and value.get("schema_id") == "odysseus.predeploy_backup_root_helper_install.v1" and value.get("status") == "installed" and value.get("error_code") == "execution_disabled" and all(value.get(k) is True for k in ("helper_installed", "unit_installed", "sudo_policy_installed")) and all(value.get(k) is False for k in ("rollback_attempted", "rollback_succeeded", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible")) and isinstance(value.get("evidence_sha256"), str) and value["evidence_sha256"] == hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest())


def _valid_install_readback(value: Any) -> bool:
    required = {"schema_id", "status", "assets_valid", "safe_parents", "state_dir_safe", "unit_disabled", "arm_present", "unit_terminal_state", "main_pid_present", "control_pid_present", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible", "evidence_sha256"}
    boolean_fields = {"assets_valid", "safe_parents", "state_dir_safe", "unit_disabled", "arm_present", "main_pid_present", "control_pid_present", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible"}
    try:
        if type(value) is not dict or set(value) != required: return False
        if value.get("schema_id") != "odysseus.predeploy_backup_root_helper_install_diagnostic_readback.v1" or value.get("status") != "available": return False
        if type(value.get("unit_terminal_state")) is not str or value["unit_terminal_state"] not in {"inactive", "failed"}: return False
        if any(type(value.get(key)) is not bool for key in boolean_fields): return False
        if not all(value.get(key) is True for key in ("assets_valid", "safe_parents", "state_dir_safe", "unit_disabled")): return False
        if not all(value.get(key) is False for key in ("arm_present", "main_pid_present", "control_pid_present", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible")): return False
        evidence = value.get("evidence_sha256")
        return type(evidence) is str and evidence == hashlib.sha256(json.dumps({key: item for key, item in value.items() if key != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()
    except Exception:
        return False


def _dispatch(mode: bytes, payload: bytes, *, popen: Callable[..., Any], command: tuple[str, ...] = SSH_COMMAND) -> tuple[str, Any | None]:
    state, output = _bounded_popen(command, mode + b"\n" + payload, popen=popen)
    return state, _one_json_line(output) if state == "ok" else None


def probe_authority(*, execute: bool = False, popen: Callable[..., Any] = subprocess.Popen) -> dict[str, Any]:
    if execute is not True: return blocked("invalid_invocation")
    state, response = _dispatch(b"mode=probe", b"", popen=popen)
    if state != "ok": return probe_unknown("transport_" + state)
    if response != {"euid_root": True, "mode": "probe", "ssh_argv_authority": True}: return probe_unknown("transport_invalid")
    return _receipt("probe_ok", "none", invoked=False, effect=False, recovery=False)


def request_installation(*, execute: bool = False, runner: Callable[..., Any] = subprocess.run, popen: Callable[..., Any] = subprocess.Popen) -> dict[str, Any]:
    if execute is not True: return blocked("invalid_invocation")
    bundle = prepare_published_install_bundle(runner=runner)
    if bundle is None: return blocked("published_blob_mismatch")
    payload = json.dumps(bundle, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
    if len(payload) > MAX_STDIN_BYTES: return blocked("published_blob_mismatch")
    state, response = _dispatch(b"mode=install", payload, popen=popen)
    if state != "ok": return unknown("transport_" + state)
    if not (type(response) is dict and set(response) == {"mode", "receipt", "readback"} and response.get("mode") == "install" and _valid_install_receipt(response.get("receipt")) and _valid_install_readback(response.get("readback"))): return unknown("transport_invalid")
    return _receipt("installed", "none", invoked=True, effect=True, recovery=False)


def request_candidate_installation(*, execute: bool = False, popen: Callable[..., Any] = subprocess.Popen) -> dict[str, Any]:
    """Dispatch the separately pinned candidate only after an explicit live GO."""
    if execute is not True: return blocked("invalid_invocation")
    bundle = prepare_candidate_install_bundle()
    if bundle is None: return blocked("candidate_blob_mismatch")
    payload = json.dumps(bundle, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
    if len(payload) > MAX_STDIN_BYTES: return blocked("candidate_blob_mismatch")
    state, response = _dispatch(b"mode=install", payload, popen=popen, command=CANDIDATE_SSH_COMMAND)
    if state != "ok": return unknown("transport_" + state)
    if not (type(response) is dict and set(response) == {"mode", "receipt", "readback"} and response.get("mode") == "install" and _valid_install_receipt(response.get("receipt")) and _valid_install_readback(response.get("readback"))): return unknown("transport_invalid")
    return _receipt("installed", "none", invoked=True, effect=True, recovery=False)


def main() -> int:
    print(json.dumps(blocked("invalid_invocation"), ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__": raise SystemExit(main())
