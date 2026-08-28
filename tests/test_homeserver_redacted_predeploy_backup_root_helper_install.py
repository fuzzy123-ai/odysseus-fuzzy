from __future__ import annotations

import hashlib
import stat
from types import SimpleNamespace

from ops.homeserver import redacted_predeploy_backup_root_helper_install as subject


class Memory:
    def __init__(self) -> None: self.files: dict[str, tuple[bytes, int]] = {}; self.directories: dict[str, int] = {}; self.synmlinks: set[str] = set(); self.synced: list[str] = []
    def read(self, path): return self.files[path][0]
    def stat(self, path):
        if path in self.files: return SimpleNamespace(st_mode=stat.S_IFREG | self.files[path][1], st_uid=0, st_gid=0, st_nlink=1)
        if path in self.directories: return SimpleNamespace(st_mode=stat.S_IFDIR | self.directories[path], st_uid=0, st_gid=0, st_nlink=2)
        raise FileNotFoundError(path)
    def lstat(self, path):
        if path in self.synmlinks: return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0, st_gid=0, st_nlink=1)
        return self.stat(path)
    def write_new(self, path, content, mode):
        if path in self.files: raise FileExistsError(path)
        self.files[path] = (content, mode)
    def remove(self, path): del self.files[path]
    def remove_exact(self, path, content, mode):
        if self.files.get(path) != (content, mode): raise OSError("foreign replacement")
        del self.files[path]
    def mkdir_new(self, path, mode):
        if path in self.directories: raise FileExistsError(path)
        self.directories[path] = mode
    def remove_dir(self, path): del self.directories[path]
    def fsync(self, path): self.synced.append(path)


def _operations(memory: Memory) -> subject.Operations:
    return subject.Operations(memory.read, memory.write_new, memory.remove, memory.stat, memory.mkdir_new, fsync=memory.fsync, remove_dir=memory.remove_dir, lstat=memory.lstat, remove_exact=memory.remove_exact)


def test_installer_is_inert_default_and_assets_are_fixed() -> None:
    result = subject.install()
    assert subject.validate_receipt(result) and result["status"] == "blocked"
    assert "EnvironmentFile" not in subject.SERVICE_TEXT
    assert "NOSETENV" in subject.SUDOERS_TEXT and "*" not in subject.SUDOERS_TEXT
    assert subject.SERVICE_TEXT == open("ops/homeserver/root-helper/odysseus-predeploy-backup-root-helper.service", encoding="ascii").read()
    assert subject.SUDOERS_TEXT == open("ops/homeserver/root-helper/odysseus-predeploy-backup-root-helper.sudoers", encoding="ascii").read()
    assert subject.READBACK_EXEC in subject.SUDOERS_TEXT and "/usr/bin/env" not in open("ops/homeserver/redacted_predeploy_backup_root_helper_readback.py", encoding="ascii").read()
    helper = open("ops/homeserver/redacted_predeploy_backup_root_helper.py", "rb").read()
    assert hashlib.sha256(helper).hexdigest() == subject.HELPER_SHA256


def test_no_clobber_exact_baseline_and_rollback() -> None:
    memory = Memory(); helper = open("ops/homeserver/redacted_predeploy_backup_root_helper.py", "rb").read()
    readback = open("ops/homeserver/redacted_predeploy_backup_root_helper_readback.py", "rb").read()
    result = subject.install(execute=True, helper_source=helper, readback_source=readback, operations=_operations(memory))
    assert subject.validate_receipt(result) and result["status"] == "installed"
    again = subject.install(execute=True, helper_source=helper, readback_source=readback, operations=_operations(memory))
    assert again["status"] == "installed"
    memory.files[subject.UNIT_PATH] = (b"conflict", 0o644)
    conflict = subject.install(execute=True, helper_source=helper, readback_source=readback, operations=_operations(memory))
    assert conflict["status"] == "blocked" and conflict["error_code"] == "conflict"


def test_digest_mismatch_stops_before_any_write() -> None:
    memory = Memory()
    result = subject.install(execute=True, helper_source=b"wrong", readback_source=b"wrong", operations=_operations(memory))
    assert result["error_code"] == "source_mismatch" and not memory.files


def test_installer_rejects_symlinked_parent_before_any_asset_write() -> None:
    memory = Memory(); memory.synmlinks.add("/etc/sudoers.d")
    helper = open("ops/homeserver/redacted_predeploy_backup_root_helper.py", "rb").read()
    readback = open("ops/homeserver/redacted_predeploy_backup_root_helper_readback.py", "rb").read()
    result = subject.install(execute=True, helper_source=helper, readback_source=readback, operations=_operations(memory))
    assert result["status"] == "rolled_back" and result["error_code"] == "preflight_failed"
    assert not memory.files and not memory.directories


def test_partial_or_silent_bad_write_rolls_back_files_and_created_directories() -> None:
    memory = Memory(); helper = open("ops/homeserver/redacted_predeploy_backup_root_helper.py", "rb").read(); readback = open("ops/homeserver/redacted_predeploy_backup_root_helper_readback.py", "rb").read()
    original = memory.write_new
    def silent_bad(path, content, mode):
        original(path, b"not-the-reviewed-asset", mode)
    memory.write_new = silent_bad
    result = subject.install(execute=True, helper_source=helper, readback_source=readback, operations=_operations(memory))
    assert result["status"] == "unknown" and result["error_code"] == "rollback_failed"
    assert memory.files and not memory.directories


def test_durability_and_receipt_cross_product_are_required() -> None:
    memory = Memory(); helper = open("ops/homeserver/redacted_predeploy_backup_root_helper.py", "rb").read(); readback = open("ops/homeserver/redacted_predeploy_backup_root_helper_readback.py", "rb").read()
    result = subject.install(execute=True, helper_source=helper, readback_source=readback, operations=_operations(memory))
    assert result["status"] == "installed" and subject.validate_receipt(result)
    assert subject.HELPER_PATH in memory.synced and subject.STATE_DIR in memory.synced
    forged = dict(result); forged["helper_installed"] = False; forged["evidence_sha256"] = subject._digest(forged)
    assert not subject.validate_receipt(forged)


def test_conflict_after_fresh_directories_rolls_back_instead_of_claiming_blocked() -> None:
    memory = Memory(); memory.files[subject.UNIT_PATH] = (b"foreign", 0o644)
    helper = open("ops/homeserver/redacted_predeploy_backup_root_helper.py", "rb").read(); readback = open("ops/homeserver/redacted_predeploy_backup_root_helper_readback.py", "rb").read()
    result = subject.install(execute=True, helper_source=helper, readback_source=readback, operations=_operations(memory))
    assert result["status"] == "rolled_back" and result["rollback_attempted"] is True and subject.validate_receipt(result)
    assert set(memory.files) == {subject.UNIT_PATH} and not memory.directories


def test_production_adapter_is_fixed_allowlist_and_default_inert() -> None:
    adapter = subject.SecureHostOperations()
    assert adapter._parts(subject.HELPER_PATH)[-1].endswith(".py")
    try:
        adapter._parts("/tmp/not-allowed")
    except PermissionError:
        pass
    else:
        raise AssertionError("untrusted target accepted")


class _PublicationFacade:
    def __init__(self, *, conflict: bool = False, leftover: bool = False, fail_parent_fsync: bool = False) -> None:
        self.conflict, self.leftover, self.fail_parent_fsync, self.links, self.unlinks, self.closed, self.syncs = conflict, leftover, fail_parent_fsync, [], [], [], []
    def open(self, name, flags, mode=None, *, dir_fd=None):
        if name.startswith(".") and self.leftover: raise FileExistsError(name)
        return 12 if name.startswith(".") else 13
    def fchown(self, *args): pass
    def write(self, fd, value): return len(value)
    def fsync(self, fd):
        self.syncs.append(fd)
        if self.fail_parent_fsync and fd == 77: raise OSError("parent sync")
    def close(self, fd): self.closed.append(fd)
    def link(self, source, target, *, src_dir_fd, dst_dir_fd, follow_symlinks):
        self.links.append((source, target, src_dir_fd, dst_dir_fd))
        if self.conflict: raise FileExistsError(target)
    def unlink(self, name, *, dir_fd): self.unlinks.append((name, dir_fd))
    def fstat(self, fd): return SimpleNamespace(st_mode=stat.S_IFREG | 0o700, st_dev=1, st_ino=9)
    def stat(self, name, *, dir_fd, follow_symlinks): return SimpleNamespace(st_mode=stat.S_IFREG | 0o700, st_dev=1, st_ino=9)


def _publication_adapter(facade: _PublicationFacade):
    adapter = subject.SecureHostOperations(facade=facade)
    adapter._parent = lambda path: (77, "final")
    adapter._verify_file = lambda *args: None
    return adapter


def test_publication_retains_acquired_parent_fd_across_pathname_swap() -> None:
    facade = _PublicationFacade(); adapter = _publication_adapter(facade)
    adapter.write_new(subject.HELPER_PATH, b"reviewed", 0o700)
    assert facade.links == [(".final.odysseus-new", "final", 77, 77)]


def test_final_appearance_cannot_be_clobbered_and_owned_temp_is_removed() -> None:
    facade = _PublicationFacade(conflict=True); adapter = _publication_adapter(facade)
    try: adapter.write_new(subject.HELPER_PATH, b"reviewed", 0o700)
    except FileExistsError: pass
    else: raise AssertionError("final swap was clobbered")
    assert facade.unlinks == [(".final.odysseus-new", 77)]


def test_preexisting_leftover_temp_is_preserved_and_fails_closed() -> None:
    facade = _PublicationFacade(leftover=True); adapter = _publication_adapter(facade)
    try: adapter.write_new(subject.HELPER_PATH, b"reviewed", 0o700)
    except FileExistsError: pass
    else: raise AssertionError("foreign leftover temp was accepted")
    assert facade.unlinks == []


def test_parent_fsync_failure_after_link_preserves_final_for_manual_reconciliation() -> None:
    facade = _PublicationFacade(fail_parent_fsync=True); adapter = _publication_adapter(facade)
    try: adapter.write_new(subject.HELPER_PATH, b"reviewed", 0o700)
    except subject.PublicationUncertain: pass
    else: raise AssertionError("parent fsync failure accepted")
    assert ("final", 77) not in facade.unlinks


def test_postpublish_verifier_failure_preserves_final_and_never_unlinks_foreign() -> None:
    facade = _PublicationFacade(); adapter = _publication_adapter(facade); calls = {"n": 0}
    def verify(*args):
        calls["n"] += 1
        if calls["n"] == 2: raise OSError("post verify")
    adapter._verify_file = verify
    try: adapter.write_new(subject.HELPER_PATH, b"reviewed", 0o700)
    except subject.PublicationUncertain: pass
    else: raise AssertionError("postpublish verifier failure accepted")
    assert ("final", 77) not in facade.unlinks


def test_later_rollback_does_not_delete_foreign_replacement_and_is_unknown() -> None:
    memory = Memory(); helper = open("ops/homeserver/redacted_predeploy_backup_root_helper.py", "rb").read(); readback = open("ops/homeserver/redacted_predeploy_backup_root_helper_readback.py", "rb").read()
    original, calls = memory.write_new, {"n": 0}
    def replace_then_fail(path, content, mode):
        calls["n"] += 1
        if calls["n"] == 2: raise OSError("later write failure")
        original(path, content, mode)
        memory.files[path] = (b"foreign-replacement", mode)
    memory.write_new = replace_then_fail
    result = subject.install(execute=True, helper_source=helper, readback_source=readback, operations=_operations(memory))
    assert result["status"] == "unknown" and result["error_code"] == "rollback_failed" and not result["rollback_succeeded"]
    assert memory.files[subject.HELPER_PATH][0] == b"foreign-replacement"


def test_postpublication_signal_with_preexisting_dirs_is_unknown_and_preserves_final() -> None:
    memory = Memory()
    for directory in ("/usr/local/libexec", "/etc/systemd/system", "/etc/sudoers.d", subject.STATE_DIR, subject.RUNTIME_DIR):
        memory.directories[directory] = 0o700 if directory in {subject.STATE_DIR, subject.RUNTIME_DIR} else 0o755
    helper = open("ops/homeserver/redacted_predeploy_backup_root_helper.py", "rb").read(); readback = open("ops/homeserver/redacted_predeploy_backup_root_helper_readback.py", "rb").read()
    facade = _PublicationFacade(fail_parent_fsync=True); adapter = _publication_adapter(facade)
    operations = _operations(memory)
    operations.write_new = adapter.write_new
    result = subject.install(execute=True, helper_source=helper, readback_source=readback, operations=operations)
    assert result["status"] == "unknown" and result["error_code"] == "rollback_failed"
    assert facade.links == [(".final.odysseus-new", "final", 77, 77)]
    assert ("final", 77) not in facade.unlinks
