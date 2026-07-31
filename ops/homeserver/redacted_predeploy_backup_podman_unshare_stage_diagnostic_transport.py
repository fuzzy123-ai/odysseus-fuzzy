#!/usr/bin/env python3
"""Pinned one-shot transport and durable local receipt for stage diagnostics."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shlex
import stat
import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

from ops.homeserver import redacted_predeploy_backup_capability_transport as bounded
from ops.homeserver import redacted_predeploy_backup_podman_unshare_stage_diagnostic as core

SCHEMA_ID = "odysseus.redacted_predeploy_backup_podman_unshare_stage_diagnostic_transport.v1"
PUBLISHED_REF = "refs/remotes/fuzzy/dev"
DIAGNOSTIC_PATH = "ops/homeserver/redacted_predeploy_backup_podman_unshare_stage_diagnostic.py"
PUBLISHED_DIAGNOSTIC_SHA256 = "dc1fb28ee88a6dc85066b4c6187b6b28696500ff4f553247c86b7a66a6f224d7"
MAX_SOURCE_BYTES = 300_000
MAX_BUNDLE_BYTES = 500_000
MAX_RESPONSE_BYTES = 8_192
RECEIPT_ROOT = Path(__file__).resolve().parents[2] / ".odysseus-predeploy-podman-unshare-stage-receipts"
SSH_PREFIX = ("ssh", "-F", "ops/homeserver/ssh_config", "odysseus-homeserver")
_ZERO = "0" * 64
_NAME = re.compile(r"^receipt-([0-9a-f]{64})\.json$")
_BOOTSTRAP = """import base64,hashlib,json,sys,types
raw=sys.stdin.buffer.read(BUNDLE_LIMIT)
if len(raw)>BUNDLE_MAX: raise SystemExit(2)
bundle=json.loads(raw.decode('ascii')); expected='PIN'
if type(bundle) is not dict or set(bundle)!={'execute','sha256','source'} or bundle['execute'] is not True or bundle['sha256']!=expected: raise SystemExit(2)
source=base64.b64decode(bundle['source'],validate=True)
if hashlib.sha256(source).hexdigest()!=expected: raise SystemExit(2)
name='ops.homeserver.redacted_predeploy_backup_podman_unshare_stage_diagnostic'; module=types.ModuleType(name); module.__file__='<published>'; sys.modules[name]=module
exec(compile(source,module.__file__,'exec'),module.__dict__)
print(json.dumps(module.collect_podman_unshare_stage_diagnostic(execute=True),ensure_ascii=True,sort_keys=True,separators=(',',':')))""".replace("PIN", PUBLISHED_DIAGNOSTIC_SHA256).replace("BUNDLE_LIMIT", str(MAX_BUNDLE_BYTES + 1)).replace("BUNDLE_MAX", str(MAX_BUNDLE_BYTES))
REMOTE_COMMAND = "exec /usr/bin/timeout --signal=KILL 25s /usr/bin/podman unshare /usr/bin/python3 -I -c " + shlex.quote(_BOOTSTRAP)
SSH_COMMAND = (*SSH_PREFIX, REMOTE_COMMAND)
_SOURCE_FIELDS = frozenset({"source_status", "source_error_code", "source_first_failed_stage", "source_probe_invoked", *{f"source_{stage}" for stage in core.STAGES}})
_SUMMARY_KEYS = frozenset({"schema_id", "status", "error_code", "effect_may_have_occurred", "retry_permitted", "source_evidence_sha256", "receipt_sha256", "summary_sha256", *_SOURCE_FIELDS})
_RECEIPT_KEYS = frozenset({"schema_id", "source_envelope", "receipt_sha256"})


def _json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _digest(value: Mapping[str, Any], omitted: str) -> str:
    return hashlib.sha256(_json({key: item for key, item in value.items() if key != omitted})).hexdigest()


def _project(source: Mapping[str, Any] | None) -> dict[str, Any]:
    if source is None:
        value: dict[str, Any] = {"source_status": "none", "source_error_code": "none",
                                 "source_first_failed_stage": "none", "source_probe_invoked": False}
        value.update({f"source_{stage}": False for stage in core.STAGES})
        return value
    value = {"source_status": source["status"], "source_error_code": source["error_code"],
             "source_first_failed_stage": source["first_failed_stage"], "source_probe_invoked": source["probe_invoked"]}
    value.update({f"source_{stage}": source[stage] for stage in core.STAGES})
    return value


def _summary(status: str, code: str, effect: bool, source: Mapping[str, Any] | None = None,
             receipt: str = _ZERO) -> dict[str, Any]:
    value: dict[str, Any] = {"schema_id": SCHEMA_ID, "status": status, "error_code": code,
                             "effect_may_have_occurred": effect, "retry_permitted": False,
                             "source_evidence_sha256": source["evidence_sha256"] if source else _ZERO,
                             "receipt_sha256": receipt}
    value.update(_project(source))
    value["summary_sha256"] = _digest(value, "summary_sha256")
    return value


def _valid_source_projection(value: Mapping[str, Any]) -> bool:
    source = {"schema_id": core.SCHEMA_ID, "status": value["source_status"], "error_code": value["source_error_code"],
              "first_failed_stage": value["source_first_failed_stage"], "probe_invoked": value["source_probe_invoked"],
              "retry_permitted": False, "evidence_sha256": value["source_evidence_sha256"]}
    source.update({stage: value[f"source_{stage}"] for stage in core.STAGES})
    source.update({key: False for key in core.VISIBILITY_KEYS})
    return core.validate_envelope(source)


def validate_transport_envelope(value: Any) -> bool:
    if (type(value) is not dict or set(value) != _SUMMARY_KEYS or value.get("schema_id") != SCHEMA_ID or
            value.get("retry_permitted") is not False or value.get("summary_sha256") != _digest(value, "summary_sha256")):
        return False
    if not all(isinstance(value.get(key), str) and re.fullmatch(r"[0-9a-f]{64}", value[key])
               for key in {"source_evidence_sha256", "receipt_sha256", "summary_sha256"}):
        return False
    empty = (_project(None) == {key: value[key] for key in _SOURCE_FIELDS} and value["source_evidence_sha256"] == _ZERO)
    status, code = value["status"], value["error_code"]
    if status == "blocked" and code in {"invalid_invocation", "published_blob_mismatch", "receipt_storage_unavailable"}:
        return bool(value["effect_may_have_occurred"] is False and empty and value["receipt_sha256"] == _ZERO)
    if status == "unknown" and code in {"transport_ambiguous", "invalid_core_envelope", "receipt_readback_unavailable"}:
        return bool(value["effect_may_have_occurred"] is True and empty and value["receipt_sha256"] == _ZERO)
    valid = _valid_source_projection(value)
    if status == "unknown" and code == "receipt_persistence_failed":
        return bool(valid and value["effect_may_have_occurred"] is value["source_probe_invoked"] and value["receipt_sha256"] == _ZERO)
    return bool(status == "persisted" and code == "none" and valid and
                value["effect_may_have_occurred"] is value["source_probe_invoked"] and value["receipt_sha256"] != _ZERO)


def _published_blob(runner: Callable[..., Any]) -> bytes | None:
    try:
        result = runner(["git", "cat-file", "blob", f"{PUBLISHED_REF}:{DIAGNOSTIC_PATH}"], stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, text=False, timeout=5, check=False, shell=False)
        raw = getattr(result, "stdout", None)
        if (getattr(result, "returncode", None) == 0 and type(raw) is bytes and 0 < len(raw) <= MAX_SOURCE_BYTES and
                hashlib.sha256(raw).hexdigest() == PUBLISHED_DIAGNOSTIC_SHA256):
            return raw
    except Exception:
        pass
    return None


def _fixed_root(create: bool) -> str | None:
    """Create only the fixed leaf, then prove all ancestors are non-symlinks."""
    try:
        root = os.path.abspath(os.fspath(RECEIPT_ROOT))
        if create and not os.path.exists(root):
            os.mkdir(root, 0o700)
        parts = Path(root).parts
        current = Path(parts[0])
        for part in parts[1:]:
            current /= part
            if stat.S_ISLNK(os.lstat(current).st_mode):
                return None
        info = os.lstat(root)
        private = os.name == "nt" or stat.S_IMODE(info.st_mode) & 0o077 == 0
        return root if stat.S_ISDIR(info.st_mode) and private else None
    except Exception:
        return None


def _persist(source: Mapping[str, Any], root: str) -> str | None:
    receipt: dict[str, Any] = {"schema_id": SCHEMA_ID, "source_envelope": dict(source)}
    receipt["receipt_sha256"] = _digest(receipt, "receipt_sha256")
    raw, name = _json(receipt), "receipt-" + receipt["receipt_sha256"] + ".json"
    if len(raw) > 4096 or not _NAME.fullmatch(name):
        return None
    directory = descriptor = None
    failed = False
    try:
        supports_dir_fd = os.open in os.supports_dir_fd and hasattr(os, "O_DIRECTORY")
        if supports_dir_fd:
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory)
        else:
            if stat.S_ISLNK(os.lstat(root).st_mode):
                raise OSError()
            descriptor = os.open(os.path.join(root, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_nlink", 1) != 1:
            failed = True
        written = 0
        while not failed and written < len(raw):
            count = os.write(descriptor, raw[written:])
            if type(count) is not int or count <= 0:
                failed = True
                break
            written += count
        if written != len(raw):
            failed = True
        if not failed:
            os.fsync(descriptor)
            if directory is not None:
                os.fsync(directory)
    except Exception:
        failed = True
    finally:
        for item in (descriptor, directory):
            if isinstance(item, int):
                try:
                    os.close(item)
                except Exception:
                    failed = True
    return None if failed else receipt["receipt_sha256"]


def _read_receipt(root: str) -> Mapping[str, Any] | None:
    directory = descriptor = None
    try:
        supports_dir_fd = os.open in os.supports_dir_fd and hasattr(os, "O_DIRECTORY")
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)) if supports_dir_fd else None
        names = os.listdir(directory) if directory is not None else os.listdir(root)
        if len(names) != 1 or not (matched := _NAME.fullmatch(names[0])):
            return None
        if directory is not None:
            descriptor = os.open(names[0], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
        else:
            location = os.path.join(root, names[0])
            if stat.S_ISLNK(os.lstat(location).st_mode):
                return None
            descriptor = os.open(location, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_nlink", 1) != 1 or not 0 < info.st_size <= 4096:
            return None
        output = bytearray()
        while len(output) <= 4096:
            block = os.read(descriptor, min(4096, 4097 - len(output)))
            if not block:
                break
            output.extend(block)
        if not output or len(output) > 4096:
            return None
        value = json.loads(bytes(output).decode("ascii"))
        return value if (type(value) is dict and set(value) == _RECEIPT_KEYS and
                         value.get("receipt_sha256") == matched.group(1) and
                         value["receipt_sha256"] == _digest(value, "receipt_sha256") and
                         core.validate_envelope(value.get("source_envelope"))) else None
    except Exception:
        return None
    finally:
        for item in (descriptor, directory):
            if isinstance(item, int):
                try:
                    os.close(item)
                except Exception:
                    pass


def read_stage_diagnostic_receipt() -> dict[str, Any]:
    root = _fixed_root(False)
    receipt = _read_receipt(root) if root else None
    if receipt is None:
        return _summary("unknown", "receipt_readback_unavailable", True)
    source = receipt["source_envelope"]
    return _summary("persisted", "none", bool(source["probe_invoked"]), source, receipt["receipt_sha256"])


def collect_published_stage_diagnostic(*, execute: bool = False,
                                       runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    if execute is not True:
        return _summary("blocked", "invalid_invocation", False)
    source = _published_blob(runner)
    if source is None:
        return _summary("blocked", "published_blob_mismatch", False)
    root = _fixed_root(True)
    if root is None:
        return _summary("blocked", "receipt_storage_unavailable", False)
    bundle = _json({"execute": True, "sha256": PUBLISHED_DIAGNOSTIC_SHA256,
                    "source": base64.b64encode(source).decode("ascii")})
    if len(bundle) > MAX_BUNDLE_BYTES:
        return _summary("blocked", "published_blob_mismatch", False)
    try:
        if runner is subprocess.run:
            result = bounded._bounded_process(SSH_COMMAND, bundle, 30, MAX_RESPONSE_BYTES)
        else:
            result = runner(list(SSH_COMMAND), input=bundle, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=False, timeout=30, check=False, shell=False)
        raw = getattr(result, "stdout", None)
        if (getattr(result, "returncode", None) != 0 or getattr(result, "stdout_oversized", False) is not False or
                type(raw) is not bytes or len(raw) > MAX_RESPONSE_BYTES or raw.count(b"\n") != 1 or not raw.endswith(b"\n")):
            raise ValueError()
        value = json.loads(raw.decode("ascii"))
    except Exception:
        return _summary("unknown", "transport_ambiguous", True)
    if not core.validate_envelope(value):
        return _summary("unknown", "invalid_core_envelope", True)
    receipt = _persist(value, root)
    return (_summary("persisted", "none", bool(value["probe_invoked"]), value, receipt)
            if receipt else _summary("unknown", "receipt_persistence_failed", bool(value["probe_invoked"]), value))


def main() -> int:
    print(json.dumps(collect_published_stage_diagnostic(), sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
