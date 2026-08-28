from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from ops.homeserver import redacted_predeploy_backup_root_helper_upgrade as subject


ROOT = Path(__file__).resolve().parents[1]
HELPER = (ROOT / "ops/homeserver/redacted_predeploy_backup_root_helper.py").read_bytes()
READBACK = (ROOT / "ops/homeserver/redacted_predeploy_backup_root_helper_readback.py").read_bytes()


class Memory:
    def __init__(self) -> None:
        self.values = {
            subject.HELPER_PATH: (subject.OLD_HELPER_SHA256, b"old-helper"),
            subject.READBACK_PATH: (subject.OLD_READBACK_SHA256, b"old-readback"),
            subject.UNIT_PATH: (subject.UNIT_SHA256, b"unit"),
            subject.SUDOERS_PATH: (subject.SUDOERS_SHA256, b"sudoers"),
        }
        self.replacements = []
        self.fail_on = None

    def read_exact(self, path, expected, mode):
        assert self.values[path][0] == expected
        return self.values[path][1]

    def replace_exact(self, path, expected, replacement, replacement_digest, mode):
        if self.fail_on == path: raise OSError("fixture failure")
        assert self.values[path][0] == expected
        self.values[path] = (replacement_digest, replacement)
        self.replacements.append((path, expected, replacement_digest))


def _upgrade(memory: Memory, **changes):
    values = {
        "execute": True,
        "helper_source": HELPER,
        "readback_source": READBACK,
        "operations": memory,
        "authority": lambda: True,
        "unit_safe": lambda: True,
        "arm_absent": lambda: True,
    }
    values.update(changes)
    return subject.upgrade(**values)


def test_default_source_mismatch_and_unsafe_host_are_inert() -> None:
    memory = Memory()
    values = (
        subject.upgrade(),
        subject.upgrade(execute=True, helper_source=b"wrong", readback_source=b"wrong", operations=memory, authority=lambda: True),
        _upgrade(memory, unit_safe=lambda: False),
        _upgrade(memory, arm_absent=lambda: False),
        _upgrade(memory, authority=lambda: False),
    )
    assert all(value["status"] == "blocked" for value in values)
    assert memory.replacements == []
    assert all(subject.validate_receipt(value) for value in values)


def test_exact_upgrade_orders_compatible_readback_before_helper() -> None:
    memory = Memory(); value = _upgrade(memory)
    assert value["status"] == "upgraded"
    assert value["helper_upgraded"] is True and value["readback_upgraded"] is True
    assert [item[0] for item in memory.replacements] == [subject.READBACK_PATH, subject.HELPER_PATH]
    assert memory.values[subject.HELPER_PATH][0] == subject.NEW_HELPER_SHA256
    assert memory.values[subject.READBACK_PATH][0] == subject.NEW_READBACK_SHA256
    assert subject.validate_receipt(value)


def test_second_publication_failure_rolls_back_only_exact_first_replacement() -> None:
    memory = Memory(); memory.fail_on = subject.HELPER_PATH
    value = _upgrade(memory)
    assert value["status"] == "rolled_back"
    assert value["rollback_attempted"] is True and value["rollback_succeeded"] is True
    assert memory.values[subject.READBACK_PATH][0] == subject.OLD_READBACK_SHA256
    assert [item[0] for item in memory.replacements] == [subject.READBACK_PATH, subject.READBACK_PATH]
    assert subject.validate_receipt(value)


def test_publication_uncertainty_never_rolls_back_or_authorizes_retry() -> None:
    class Uncertain(Memory):
        def replace_exact(self, path, expected, replacement, replacement_digest, mode):
            raise subject.PublicationUncertain()
    value = _upgrade(Uncertain())
    assert value["status"] == "unknown"
    assert value["manual_recovery_required"] is True and value["retry_permitted"] is False
    assert subject.validate_receipt(value)


def test_postflight_mismatch_is_unknown() -> None:
    memory = Memory(); calls = {"count": 0}
    def unit_safe():
        calls["count"] += 1
        return calls["count"] == 1
    value = _upgrade(memory, unit_safe=unit_safe)
    assert value["status"] == "unknown" and value["error_code"] == "postflight_failed"
    assert subject.validate_receipt(value)


@pytest.mark.skipif(os.name != "posix", reason="descriptor-rooted replacement is Linux-only")
def test_real_replacement_is_exact_atomic_and_preserves_foreign_temp(tmp_path) -> None:
    class RootFacade:
        def __getattr__(self, name): return getattr(os, name)
        @staticmethod
        def fchown(descriptor, uid, gid): return None
        @staticmethod
        def _root(info):
            return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_gid=0, st_nlink=info.st_nlink, st_size=info.st_size, st_dev=info.st_dev, st_ino=info.st_ino, st_mtime_ns=info.st_mtime_ns, st_ctime_ns=info.st_ctime_ns)
        def fstat(self, descriptor): return self._root(os.fstat(descriptor))
        def stat(self, *args, **kwargs): return self._root(os.stat(*args, **kwargs))

    target = tmp_path / "helper.py"; old = b"old-reviewed"; new = b"new-reviewed"
    target.write_bytes(old); target.chmod(0o700)
    subject._replace_exact(str(target), hashlib.sha256(old).hexdigest(), new, hashlib.sha256(new).hexdigest(), 0o700, api=RootFacade())
    assert target.read_bytes() == new and stat.S_IMODE(target.stat().st_mode) == 0o700
    temporary = tmp_path / ".helper.py.odysseus-upgrade"; temporary.write_bytes(b"foreign")
    with pytest.raises(FileExistsError):
        subject._replace_exact(str(target), hashlib.sha256(new).hexdigest(), old, hashlib.sha256(old).hexdigest(), 0o700, api=RootFacade())
    assert temporary.read_bytes() == b"foreign" and target.read_bytes() == new
