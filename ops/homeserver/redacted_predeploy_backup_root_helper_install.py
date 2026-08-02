#!/usr/bin/env python3
"""No-clobber installer contract for the root-owned backup helper.

It is intentionally not a command-line installer.  A later, separately
authorised host action may call :func:`install` with a reviewed operation
adapter.  All normal imports and the default entrypoint are inert.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from typing import Any, Mapping


SCHEMA_ID = "odysseus.predeploy_backup_root_helper_install.v1"
HELPER_SHA256 = "dbcbac4c5a4b65edcc4d4facd9204674a8e9114179f406c88d067c7f96185a97"
HELPER_PATH = "/usr/local/libexec/odysseus-predeploy-backup-root-helper.py"
READBACK_PATH = "/usr/local/libexec/odysseus-predeploy-backup-root-helper-readback.py"
READBACK_SHA256 = "8201653d392d1556a81f6ca236e9f5dd94b6d425dc03ae699f74280b4ae9b722"
UNIT_PATH = "/etc/systemd/system/odysseus-predeploy-backup-root-helper.service"
SUDOERS_PATH = "/etc/sudoers.d/odysseus-predeploy-backup-root-helper"
STATE_DIR = "/var/lib/odysseus-predeploy-backup-root-helper"
RUNTIME_DIR = "/run/odysseus-predeploy-backup-root-helper"
READBACK_EXEC = "/usr/bin/python3 -I " + READBACK_PATH
_MAX_SOURCE = 400_000
_HEX = __import__("re").compile(r"^[0-9a-f]{64}$")
_KEYS = frozenset({"schema_id", "status", "error_code", "helper_installed", "unit_installed", "sudo_policy_installed", "rollback_attempted", "rollback_succeeded", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible", "evidence_sha256"})
_CODES = frozenset({"execution_disabled", "source_mismatch", "preflight_failed", "conflict", "write_failed", "rollback_failed"})


class Failure(Exception):
    def __init__(self, code: str) -> None: self.code = code


class PublicationUncertain(Exception):
    """A final link may exist; automatic recovery must not delete it."""


SERVICE_TEXT = """[Unit]
Description=Odysseus one-shot descriptor-bound predeploy backup
ConditionPathIsMountPoint=/mnt/backup
RefuseManualStop=yes

[Service]
Type=oneshot
User=root
Group=root
UMask=0077
ExecStart=/usr/bin/python3 -I /usr/local/libexec/odysseus-predeploy-backup-root-helper.py
RuntimeMaxSec=1860s
TimeoutStopSec=5s
KillMode=control-group
SendSIGKILL=yes
StandardOutput=null
StandardError=null
StandardInput=null
NoNewPrivileges=yes
PrivateTmp=yes
RuntimeDirectory=odysseus-predeploy-backup-root-helper
RuntimeDirectoryMode=0755
RuntimeDirectoryPreserve=yes
PrivateNetwork=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=read-only
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
CapabilityBoundingSet=CAP_SYS_ADMIN CAP_SETUID CAP_SETGID CAP_CHOWN CAP_DAC_READ_SEARCH
ReadWritePaths=/mnt/backup /run/odysseus-predeploy-backup-root-helper /var/lib/odysseus-predeploy-backup-root-helper
"""
SUDOERS_TEXT = """# Exact argv-free backup trigger; no command arguments or environment preservation.
Cmnd_Alias ODYSSEUS_PREDEPLOY_BACKUP = /usr/bin/systemctl start --wait odysseus-predeploy-backup-root-helper.service
Cmnd_Alias ODYSSEUS_PREDEPLOY_READBACK = /usr/bin/python3 -I /usr/local/libexec/odysseus-predeploy-backup-root-helper-readback.py
homebase ALL=(root) NOSETENV: ODYSSEUS_PREDEPLOY_BACKUP, ODYSSEUS_PREDEPLOY_READBACK
"""


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def receipt(status: str, error_code: str, *, helper: bool = False, unit: bool = False, sudo: bool = False, rollback_attempted: bool = False, rollback_succeeded: bool = False) -> dict[str, Any]:
    value = {"schema_id": SCHEMA_ID, "status": status, "error_code": error_code if error_code in _CODES else "preflight_failed", "helper_installed": helper, "unit_installed": unit, "sudo_policy_installed": sudo, "rollback_attempted": rollback_attempted, "rollback_succeeded": rollback_succeeded, "raw_output_visible": False, "environment_visible": False, "paths_visible": False, "secret_values_visible": False}
    value["evidence_sha256"] = _digest(value)
    return value


def validate_receipt(value: Any) -> bool:
    if not (type(value) is dict and set(value) == _KEYS and value.get("schema_id") == SCHEMA_ID and value.get("error_code") in _CODES and all(value.get(key) is False for key in ("raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible")) and isinstance(value.get("evidence_sha256"), str) and value["evidence_sha256"] == _digest(value)):
        return False
    status = value.get("status")
    installed = all(value.get(key) is True for key in ("helper_installed", "unit_installed", "sudo_policy_installed"))
    clean = all(value.get(key) is False for key in ("helper_installed", "unit_installed", "sudo_policy_installed"))
    if status == "installed": return installed and value.get("rollback_attempted") is False and value.get("rollback_succeeded") is False and value.get("error_code") == "execution_disabled"
    if status == "blocked": return clean and value.get("rollback_attempted") is False and value.get("rollback_succeeded") is False
    if status == "rolled_back": return clean and value.get("rollback_attempted") is True and value.get("rollback_succeeded") is True
    return bool(status == "unknown" and clean and value.get("rollback_attempted") is True and value.get("rollback_succeeded") is False and value.get("error_code") == "rollback_failed")


def validate_assets(helper_source: bytes, readback_source: bytes) -> bool:
    return bool(type(helper_source) is bytes and type(readback_source) is bytes and readback_source.startswith(b"#!/usr/bin/python3\n") and b"/usr/bin/env" not in readback_source and 0 < len(helper_source) <= _MAX_SOURCE and 0 < len(readback_source) <= _MAX_SOURCE and _HEX.fullmatch(HELPER_SHA256) and _HEX.fullmatch(READBACK_SHA256) and hashlib.sha256(helper_source).hexdigest() == HELPER_SHA256 and hashlib.sha256(readback_source).hexdigest() == READBACK_SHA256 and SERVICE_TEXT.endswith("\n") and SUDOERS_TEXT.endswith("\n") and "EnvironmentFile" not in SERVICE_TEXT and "ExecStart=/usr/bin/python3 -I " + HELPER_PATH in SERVICE_TEXT and READBACK_EXEC in SUDOERS_TEXT and "systemctl start --wait odysseus-predeploy-backup-root-helper.service" in SUDOERS_TEXT and "ALL" not in SUDOERS_TEXT.replace("homebase ALL=(root)", ""))


@dataclass
class Operations:
    """Small injectable filesystem surface; production callers provide this explicitly."""
    read: Any
    write_new: Any
    remove: Any
    stat: Any
    mkdir_new: Any
    fsync: Any = None
    remove_dir: Any = None
    lstat: Any = None
    remove_exact: Any = None


class SecureHostOperations:
    """Default-inert descriptor-only adapter for a future reviewed host action.

    It is deliberately *not* constructed or selected by :func:`install`.
    Every absolute target must be in this fixed allowlist.  Each operation
    walks from a trusted root descriptor using nofollow directory descriptors,
    so a parent or final pathname swap is rejected instead of followed.
    """
    _TARGETS = frozenset({HELPER_PATH, READBACK_PATH, UNIT_PATH, SUDOERS_PATH, "/usr/local/libexec", "/etc/systemd/system", "/etc/sudoers.d", STATE_DIR, RUNTIME_DIR})

    def __init__(self, *, root: str = "/", facade: Any = os) -> None:
        if root != "/":
            raise ValueError("production root must be fixed")
        self._root = root
        self._api = facade

    @staticmethod
    def _flags(extra: int = 0) -> int:
        return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | extra

    def _parts(self, path: str) -> list[str]:
        if path not in self._TARGETS or not path.startswith("/"):
            raise PermissionError("untrusted target")
        return path.split("/")[1:]

    def _parent(self, path: str) -> tuple[int, str]:
        parts = self._parts(path)
        if not parts: raise PermissionError("root target")
        current = os.open("/", self._flags())
        try:
            for component in parts[:-1]:
                following = os.open(component, self._flags(), dir_fd=current)
                os.close(current); current = following
            info = os.fstat(current)
            if not (stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) in {0o700, 0o755}): raise PermissionError("untrusted parent")
            return current, parts[-1]
        except Exception:
            try: os.close(current)
            except Exception: pass
            raise

    @staticmethod
    def _verify_file(api: Any, descriptor: int, expected: bytes, mode: int) -> None:
        info = api.fstat(descriptor)
        if not (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) == mode and info.st_nlink == 1 and info.st_size == len(expected)):
            raise OSError("published file identity")
        data = bytearray(len(expected)); view = memoryview(data); offset = 0
        api.lseek(descriptor, 0, os.SEEK_SET)
        while offset < len(view):
            count = api.readv(descriptor, [view[offset:]])
            if not isinstance(count, int) or count <= 0: raise OSError("short read")
            offset += count
        if bytes(data) != expected: raise OSError("published content")

    def lstat(self, path: str) -> Any:
        parent, name = self._parent(path)
        try: return os.stat(name, dir_fd=parent, follow_symlinks=False)
        finally: os.close(parent)

    stat = lstat

    def read(self, path: str) -> bytes:
        parent, name = self._parent(path); descriptor = None
        try:
            descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), dir_fd=parent)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_SOURCE: raise OSError("unsafe read")
            result = bytearray(info.st_size); view = memoryview(result); offset = 0
            while offset < len(view):
                count = os.readv(descriptor, [view[offset:]])
                if not isinstance(count, int) or count <= 0: raise OSError("short read")
                offset += count
            return bytes(result)
        finally:
            if isinstance(descriptor, int): os.close(descriptor)
            os.close(parent)

    def mkdir_new(self, path: str, mode: int) -> None:
        parent, name = self._parent(path)
        try: os.mkdir(name, mode, dir_fd=parent); os.fsync(parent)
        finally: os.close(parent)

    def remove_dir(self, path: str) -> None:
        parent, name = self._parent(path)
        try: os.rmdir(name, dir_fd=parent); os.fsync(parent)
        finally: os.close(parent)

    def remove(self, path: str) -> None:
        parent, name = self._parent(path)
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode): raise OSError("unsafe remove")
            os.unlink(name, dir_fd=parent); os.fsync(parent)
        finally: os.close(parent)

    def fsync(self, path: str) -> None:
        parent, name = self._parent(path); descriptor = None
        try:
            descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), dir_fd=parent)
            os.fsync(descriptor)
        finally:
            if isinstance(descriptor, int): os.close(descriptor)
            os.close(parent)

    def write_new(self, path: str, content: bytes, mode: int) -> None:
        parent, name = self._parent(path); temporary = "." + name + ".odysseus-new"; descriptor = None; created_temporary = False; published = False; api = self._api
        try:
            descriptor = api.open(temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), mode, dir_fd=parent); created_temporary = True
            api.fchown(descriptor, 0, 0); view = memoryview(content); offset = 0
            while offset < len(view):
                count = api.write(descriptor, view[offset:])
                if not isinstance(count, int) or count <= 0: raise OSError("short write")
                offset += count
            api.fsync(descriptor); self._verify_file(api, descriptor, content, mode)
            # link is atomic and fails if the final name appeared since the
            # descriptor walk; unlike replace it cannot clobber a swapped file.
            api.link(temporary, name, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False); published = True
            api.unlink(temporary, dir_fd=parent); created_temporary = False; api.fsync(parent)
            final_descriptor = api.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), dir_fd=parent)
            try: self._verify_file(api, final_descriptor, content, mode)
            finally: api.close(final_descriptor)
        except Exception:
            # Once link publication succeeded, never perform a check-then-
            # unlink recovery: another writer could replace the final entry
            # between those operations.  Preserve it and let the caller
            # report an unknown publication state for manual reconciliation.
            if published: raise PublicationUncertain() from None
            raise
        finally:
            if isinstance(descriptor, int): api.close(descriptor)
            if created_temporary:
                try: api.unlink(temporary, dir_fd=parent)
                except FileNotFoundError: pass
            api.close(parent)


def _safe_absent_or_exact(operations: Operations, path: str, expected: bytes, mode: int) -> bool:
    try:
        info = operations.lstat(path)
    except FileNotFoundError:
        return True
    try:
        return bool(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) == mode and operations.read(path) == expected)
    except Exception:
        return False


def _safe_directory(operations: Operations, path: str, mode: int) -> bool:
    try:
        info = operations.lstat(path)
        return bool(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) == mode and info.st_nlink >= 1)
    except Exception: return False


def install(*, execute: bool = False, helper_source: bytes | None = None, readback_source: bytes | None = None, operations: Operations | None = None) -> dict[str, Any]:
    """Install exact bytes or restore the pre-call state on a partial write.

    The caller must supply a root-only operation adapter.  This deliberately
    prevents an import or a normal CLI call from installing anything.
    """
    if execute is not True or operations is None or helper_source is None or readback_source is None:
        return receipt("blocked", "execution_disabled")
    if not validate_assets(helper_source, readback_source):
        return receipt("blocked", "source_mismatch")
    if not (callable(operations.fsync) and callable(operations.remove_dir) and callable(operations.lstat)):
        return receipt("blocked", "preflight_failed")
    assets = ((HELPER_PATH, helper_source, 0o700), (READBACK_PATH, readback_source, 0o700), (UNIT_PATH, SERVICE_TEXT.encode("ascii"), 0o644), (SUDOERS_PATH, SUDOERS_TEXT.encode("ascii"), 0o440))
    created: list[str] = []
    created_dirs: list[str] = []
    try:
        for directory in ("/usr/local/libexec", "/etc/systemd/system", "/etc/sudoers.d", STATE_DIR, RUNTIME_DIR):
            mode = 0o700 if directory in {STATE_DIR, RUNTIME_DIR} else 0o755
            try: operations.mkdir_new(directory, 0o700 if directory in {STATE_DIR, RUNTIME_DIR} else 0o755)
            except FileExistsError: pass
            else: created_dirs.append(directory)
            if not _safe_directory(operations, directory, mode): raise Failure("preflight_failed")
        for path, content, mode in assets:
            if not _safe_absent_or_exact(operations, path, content, mode):
                raise Failure("conflict")
        for path, content, mode in assets:
            try:
                operations.stat(path)
            except FileNotFoundError:
                operations.write_new(path, content, mode); created.append(path); operations.fsync(path)
                if not _safe_absent_or_exact(operations, path, content, mode): raise Failure("write_failed")
        for directory in ("/usr/local/libexec", "/etc/systemd/system", "/etc/sudoers.d", STATE_DIR, RUNTIME_DIR): operations.fsync(directory)
        # Re-read after the parent directory sync.  A write acknowledgement is
        # not accepted as evidence of an exact durable installed asset.
        if not all(_safe_absent_or_exact(operations, path, content, mode) for path, content, mode in assets): raise Failure("write_failed")
        return receipt("installed", "execution_disabled", helper=True, unit=True, sudo=True)
    except PublicationUncertain:
        return receipt("unknown", "rollback_failed", rollback_attempted=True, rollback_succeeded=False)
    except Failure as failure:
        restored = not bool(created)
        if callable(operations.remove_dir):
            for directory in reversed(created_dirs):
                try: operations.remove_dir(directory)
                except Exception: restored = False
        mutated = bool(created or created_dirs)
        return receipt("blocked" if restored and failure.code == "conflict" and not mutated else ("rolled_back" if restored else "unknown"), failure.code if restored else "rollback_failed", rollback_attempted=mutated, rollback_succeeded=restored)
    except Exception:
        restored = not bool(created)
        if callable(operations.remove_dir):
            for directory in reversed(created_dirs):
                try: operations.remove_dir(directory)
                except Exception: restored = False
        return receipt("rolled_back" if restored else "unknown", "write_failed" if restored else "rollback_failed", rollback_attempted=True, rollback_succeeded=restored)


def main() -> int:
    print(json.dumps(receipt("blocked", "execution_disabled"), ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__": raise SystemExit(main())
