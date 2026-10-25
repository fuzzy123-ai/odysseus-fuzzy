from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ops.homeserver import redacted_predeploy_backup_root_helper_replace as subject


class FaultApi:
    """Inject syscall faults while retaining real POSIX fd and inode behavior."""

    def __init__(self, *, fail: str | None = None, after_mutation: bool = False, max_write: int | None = None, fail_replace_call: int = 1, fail_candidate_readback: bool = False, on_candidate_fsync=None) -> None:
        self.fail = fail
        self.after_mutation = after_mutation
        self.max_write = max_write
        self.fail_replace_call = fail_replace_call
        self.fail_candidate_readback = fail_candidate_readback
        self.on_candidate_fsync = on_candidate_fsync
        self.failed = False
        self.replace_calls = 0
        self.fd_labels: dict[int, str] = {}
        self.failure: tuple[str, str] | None = None
        self.trace: list[str] = []

    def __getattr__(self, name: str):
        target = getattr(os, name)
        def call(*args, **kwargs):
            self.trace.append(name)
            try:
                if name == "open":
                    fd = target(*args, **kwargs)
                    label = args[0] if args and isinstance(args[0], str) else ""
                    self.fd_labels[fd] = label
                    return fd
                if name == "read" and self.fail_candidate_readback and self.fd_labels.get(args[0], "").endswith("odysseus-replace-new"):
                    self.fail_candidate_readback = False
                    return b""
                if name == "write" and self.max_write is not None:
                    return target(args[0], args[1][:self.max_write])
                if name == "fsync":
                    if self.fail == name and not self.failed:
                        self.failed = True
                        raise OSError("injected syscall failure")
                    result = target(*args, **kwargs)
                    if self.on_candidate_fsync is not None and self.fd_labels.get(args[0], "").endswith("odysseus-replace-new"):
                        callback, self.on_candidate_fsync = self.on_candidate_fsync, None
                        callback()
                    return result
                if name == "replace":
                    self.replace_calls += 1
                    if self.fail == name and not self.failed and self.replace_calls == self.fail_replace_call:
                        self.failed = True
                        if self.after_mutation:
                            target(*args, **kwargs)
                        raise OSError("injected syscall failure")
                if name == self.fail and name != "replace" and not self.failed:
                    self.failed = True
                    if name == "write":
                        return 0
                    raise OSError("injected syscall failure")
                return target(*args, **kwargs)
            except Exception as exc:
                if self.failure is None:
                    self.failure = (name, type(exc).__name__)
                raise

        return call


@unittest.skipUnless(os.name == "posix", "requires POSIX dir_fd, flock, and inode semantics")
class ReplacementLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_pins = {
            "OLD_HELPER_SHA256": subject.OLD_HELPER_SHA256,
            "NEW_HELPER_SHA256": subject.NEW_HELPER_SHA256,
            "READBACK_SHA256": subject.READBACK_SHA256,
            "UNIT_SHA256": subject.UNIT_SHA256,
            "SUDOERS_SHA256": subject.SUDOERS_SHA256,
            "ASSET_PINS": subject.ASSET_PINS,
            "ASSET_PATHS": subject.ASSET_PATHS,
        }
        self.old_source = b"isolated old root helper fixture\n"
        self.new_source = b"isolated reviewed successor helper fixture\n"
        self.readback_source = b"isolated readback asset\n"
        self.unit_source = b"isolated static unit asset\n"
        self.sudoers_source = b"isolated sudoers asset\n"
        subject.OLD_HELPER_SHA256 = hashlib.sha256(self.old_source).hexdigest()
        subject.NEW_HELPER_SHA256 = hashlib.sha256(self.new_source).hexdigest()
        subject.READBACK_SHA256 = hashlib.sha256(self.readback_source).hexdigest()
        subject.UNIT_SHA256 = hashlib.sha256(self.unit_source).hexdigest()
        subject.SUDOERS_SHA256 = hashlib.sha256(self.sudoers_source).hexdigest()
        subject.ASSET_PINS = {"helper": subject.OLD_HELPER_SHA256, "readback": subject.READBACK_SHA256, "unit": subject.UNIT_SHA256, "sudoers": subject.SUDOERS_SHA256}
        subject.ASSET_PATHS = (
            (subject.HELPER_PATH, subject.OLD_HELPER_SHA256, 0o700),
            ("/usr/local/libexec/odysseus-predeploy-backup-root-helper-readback.py", subject.READBACK_SHA256, 0o700),
            (subject.UNIT_PATH, subject.UNIT_SHA256, 0o644),
            (subject.SUDOERS_PATH, subject.SUDOERS_SHA256, 0o440),
        )
        self.temp = tempfile.TemporaryDirectory(prefix="odysseus-replace-")
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        for directory in (
            "usr", "usr/local", "usr/local/libexec", "etc", "etc/systemd",
            "etc/systemd/system", "etc/sudoers.d", "var", "var/lib",
            "var/lib/odysseus-predeploy-backup-root-helper",
        ):
            path = self.root / directory
            path.mkdir(exist_ok=True)
            path.chmod(0o700 if directory.endswith("odysseus-predeploy-backup-root-helper") else 0o755)
        self.helper_dir = self.root / "usr/local/libexec"
        self.state_dir = self.root / "var/lib/odysseus-predeploy-backup-root-helper"
        self._put("usr/local/libexec/odysseus-predeploy-backup-root-helper.py", self.old_source, 0o700)
        self._put("usr/local/libexec/odysseus-predeploy-backup-root-helper-readback.py", self.readback_source, 0o700)
        self._put("etc/systemd/system/odysseus-predeploy-backup-root-helper.service", self.unit_source, 0o644)
        self._put("etc/sudoers.d/odysseus-predeploy-backup-root-helper", self.sudoers_source, 0o440)
        self.calls = 0
        self.unit_runner = self._unit_state
        self.operations = self._operations()

    def tearDown(self) -> None:
        self.temp.cleanup()
        for key, value in self.original_pins.items():
            setattr(subject, key, value)

    def _put(self, relative: str, content: bytes, mode: int) -> None:
        path = self.root / relative
        path.write_bytes(content)
        path.chmod(mode)

    def _unit_state(self, *_args, **_kwargs):
        self.calls += 1
        return SimpleNamespace(
            returncode=0,
            stdout=b"LoadState=loaded\nActiveState=inactive\nSubState=dead\nUnitFileState=static\n",
        )

    def _operations(self, api=os, unit_runner=None):
        return subject.SecureHostOperations(
            api=api,
            root_path=str(self.root),
            owner_uid=os.getuid(),
            owner_gid=os.getgid(),
            unit_runner=unit_runner or self.unit_runner,
            old_helper_digest=subject.OLD_HELPER_SHA256,
        )

    def _replace(self, operations=None):
        return subject.replace(execute=True, new_source=self.new_source, operations=operations or self.operations)

    def test_default_is_inert_and_envelopes_are_strict(self) -> None:
        self.assertEqual(subject.replace()["status"], "blocked")
        self.assertEqual(subject.rollback()["status"], "blocked")
        value = subject.envelope("blocked", "execution_disabled")
        self.assertTrue(subject.validate_envelope(value))
        value["rollback_attempted"] = 1
        value["evidence_sha256"] = subject._digest(value)
        self.assertFalse(subject.validate_envelope(value))

    def test_success_replaces_with_verified_candidate_and_preserves_durable_backup(self) -> None:
        old = (self.helper_dir / "odysseus-predeploy-backup-root-helper.py").read_bytes()
        root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        varlib_fd = os.open(self.root / "var/lib", os.O_RDONLY | os.O_DIRECTORY)
        state_fd = os.open("odysseus-predeploy-backup-root-helper", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=varlib_fd)
        try:
            state_parent, state_name = subject._open_parent(subject.STATE_DIR, root_fd=root_fd, uid=os.getuid(), gid=os.getgid())
            try:
                self.assertTrue(subject._safe_dir(os.stat(state_name, dir_fd=state_parent, follow_symlinks=False), uid=os.getuid(), gid=os.getgid()))
            finally:
                os.close(state_parent)
            for path, digest, mode in subject.ASSET_PATHS:
                self.assertTrue(subject._asset_valid(path, digest, mode, root_fd=root_fd, uid=os.getuid(), gid=os.getgid()), path)
            self.assertTrue(subject._safe_dir(os.fstat(state_fd), uid=os.getuid(), gid=os.getgid()))
            with self.assertRaises(FileNotFoundError):
                os.stat("arm.json", dir_fd=state_fd, follow_symlinks=False)
            probe = self.unit_runner(subject.UNIT_STATE_COMMAND)
            self.assertEqual(probe.stdout, b"LoadState=loaded\nActiveState=inactive\nSubState=dead\nUnitFileState=static\n")
            self.assertTrue(self.operations.quiescent(root_fd=root_fd, state_fd=state_fd))
        finally:
            os.close(state_fd)
            os.close(varlib_fd)
            os.close(root_fd)
        trace = FaultApi()
        result = self._replace(self._operations(api=trace))
        target = self.helper_dir / "odysseus-predeploy-backup-root-helper.py"
        backup = self.helper_dir / "odysseus-predeploy-backup-root-helper.py.backup"
        self.assertTrue(subject.validate_envelope(result))
        self.assertEqual(result["status"], "applied", (result, trace.failure, trace.trace[-24:]))
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), subject.NEW_HELPER_SHA256)
        self.assertEqual(backup.read_bytes(), old)
        self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
        self.assertFalse((self.state_dir / "arm.json").exists())

    def test_preflight_rejects_arm_asset_drift_and_noninactive_unit(self) -> None:
        (self.state_dir / "arm.json").write_text("{}", encoding="ascii")
        self.assertEqual(self._replace()["status"], "blocked")
        (self.state_dir / "arm.json").unlink()
        readback = self.root / "usr/local/libexec/odysseus-predeploy-backup-root-helper-readback.py"
        readback.write_bytes(b"changed")
        self.assertEqual(self._replace()["status"], "blocked")
        readback.write_bytes(self.readback_source)
        readback.chmod(0o700)
        failing_unit = lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=b"LoadState=loaded\nActiveState=active\nSubState=running\nUnitFileState=static\n")
        self.assertEqual(self._replace(self._operations(unit_runner=failing_unit))["status"], "blocked")

    def test_backup_and_candidate_temp_conflicts_are_preserved(self) -> None:
        backup = self.helper_dir / "odysseus-predeploy-backup-root-helper.py.backup"
        backup.write_bytes(b"foreign backup")
        backup.chmod(0o600)
        result = self._replace()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(backup.read_bytes(), b"foreign backup")
        backup.unlink()
        temp = self.helper_dir / ".odysseus-predeploy-backup-root-helper.py.odysseus-replace-new"
        temp.write_bytes(b"foreign temp")
        result = self._replace()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(temp.read_bytes(), b"foreign temp")

        self.tearDown()
        original_pins = self.original_pins
        self.setUp()
        self.original_pins = original_pins
        backup_temp = self.helper_dir / ".odysseus-predeploy-backup-root-helper.py.backup.tmp"
        backup_temp.write_bytes(b"foreign backup temp")
        result = self._replace()
        self.assertIn(result["status"], {"blocked", "unknown"})
        self.assertEqual(backup_temp.read_bytes(), b"foreign backup temp")

    def test_lock_contention_and_short_io_are_bounded(self) -> None:
        import fcntl

        lock_path = self.state_dir / "lifecycle.lock"
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self._replace()["error_code"], "lock_busy")
        finally:
            os.close(lock_fd)
        result = self._replace(self._operations(api=FaultApi(max_write=7)))
        self.assertEqual(result["status"], "applied", result)

    def test_syscall_failures_do_not_publish_unverified_data(self) -> None:
        original_pins = self.original_pins
        for operation in ("write", "fsync"):
            with self.subTest(operation=operation):
                if operation != "write":
                    self.tearDown()
                    self.setUp()
                    self.original_pins = original_pins
                result = self._replace(self._operations(api=FaultApi(fail=operation)))
                target = self.helper_dir / "odysseus-predeploy-backup-root-helper.py"
                self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), subject.OLD_HELPER_SHA256)
                self.assertIn(result["status"], {"unknown", "blocked"})
                self.assertFalse((self.helper_dir / ".odysseus-predeploy-backup-root-helper.py.backup.tmp").exists())
                self.assertFalse((self.helper_dir / ".odysseus-predeploy-backup-root-helper.py.odysseus-replace-new").exists())

    def test_replace_that_mutates_then_raises_is_rolled_back_under_held_locks(self) -> None:
        result = self._replace(self._operations(api=FaultApi(fail="replace", after_mutation=True)))
        target = self.helper_dir / "odysseus-predeploy-backup-root-helper.py"
        self.assertEqual(result["status"], "rolled_back", result)
        self.assertTrue(result["rollback_succeeded"])
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), subject.OLD_HELPER_SHA256)

    def test_candidate_readback_failure_cleans_only_owned_temp_and_never_publishes(self) -> None:
        result = self._replace(self._operations(api=FaultApi(fail_candidate_readback=True)))
        target = self.helper_dir / "odysseus-predeploy-backup-root-helper.py"
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), subject.OLD_HELPER_SHA256)
        self.assertFalse((self.helper_dir / ".odysseus-predeploy-backup-root-helper.py.odysseus-replace-new").exists())
        self.assertEqual(result["status"], "unknown")

    def test_replace_failure_before_mutation_and_rollback_failure_remain_unknown(self) -> None:
        target = self.helper_dir / "odysseus-predeploy-backup-root-helper.py"
        before = self._replace(self._operations(api=FaultApi(fail="replace", after_mutation=False)))
        self.assertEqual(before["status"], "unknown")
        self.assertEqual(before["error_code"], "rollback_failed")
        self.assertTrue(before["rollback_attempted"])
        self.assertTrue(subject.validate_envelope(before), before)
        self.assertEqual(target.read_bytes(), self.old_source)

        original_pins = self.original_pins
        self.tearDown()
        self.setUp()
        self.original_pins = original_pins
        target = self.helper_dir / "odysseus-predeploy-backup-root-helper.py"
        calls = {"count": 0}
        def fail_postflight(*_args, **_kwargs):
            calls["count"] += 1
            state = self._unit_state()
            if calls["count"] == 2:
                state.returncode = 1
            return state
        result = self._replace(self._operations(api=FaultApi(fail="replace", after_mutation=False, fail_replace_call=2), unit_runner=fail_postflight))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["error_code"], "rollback_failed")
        self.assertTrue(subject.validate_envelope(result), result)
        self.assertEqual(target.read_bytes(), self.new_source)
        self.assertTrue((self.helper_dir / "odysseus-predeploy-backup-root-helper.py.backup").exists())
        rollback_temp = self.helper_dir / ".odysseus-predeploy-backup-root-helper.py.odysseus-rollback-new"
        self.assertFalse(rollback_temp.exists())

        original_pins = self.original_pins
        self.tearDown()
        self.setUp()
        self.original_pins = original_pins
        target = self.helper_dir / "odysseus-predeploy-backup-root-helper.py"
        rollback_temp = self.helper_dir / ".odysseus-predeploy-backup-root-helper.py.odysseus-rollback-new"
        rollback_temp.write_bytes(b"foreign rollback temp")
        rollback_temp.chmod(0o600)
        calls = {"count": 0}
        def fail_postflight_with_foreign_temp(*_args, **_kwargs):
            calls["count"] += 1
            state = self._unit_state()
            if calls["count"] == 2:
                state.returncode = 1
            return state
        result = self._replace(self._operations(unit_runner=fail_postflight_with_foreign_temp))
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(subject.validate_envelope(result), result)
        self.assertEqual(target.read_bytes(), self.new_source)
        self.assertEqual(rollback_temp.read_bytes(), b"foreign rollback temp")

    def test_postflight_failure_rolls_back_and_foreign_target_is_never_overwritten(self) -> None:
        calls = {"count": 0}
        def fail_postflight(*_args, **_kwargs):
            calls["count"] += 1
            state = self._unit_state()
            if calls["count"] == 2:
                state.returncode = 1
            return state
        result = self._replace(self._operations(unit_runner=fail_postflight))
        self.assertEqual(result["status"], "rolled_back", result)
        target = self.helper_dir / "odysseus-predeploy-backup-root-helper.py"
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(), subject.OLD_HELPER_SHA256)

        original_pins = self.original_pins
        self.tearDown()
        self.setUp()
        self.original_pins = original_pins
        target = self.helper_dir / "odysseus-predeploy-backup-root-helper.py"
        calls = {"count": 0}
        def swap_target_during_postflight(*_args, **_kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                target.write_bytes(b"foreign successor")
                target.chmod(0o700)
                return SimpleNamespace(returncode=1, stdout=b"")
            return self._unit_state()
        result = self._replace(self._operations(unit_runner=swap_target_during_postflight))
        self.assertEqual(result["status"], "unknown", result)
        self.assertEqual(target.read_bytes(), b"foreign successor")

    def test_prepublication_target_swap_and_held_inode_drift_are_preserved(self) -> None:
        target = self.helper_dir / "odysseus-predeploy-backup-root-helper.py"

        def replace_with_foreign_inode():
            foreign = target.with_suffix(".foreign")
            foreign.write_bytes(b"foreign replacement")
            foreign.chmod(0o700)
            os.replace(foreign, target)

        result = self._replace(self._operations(api=FaultApi(on_candidate_fsync=replace_with_foreign_inode)))
        self.assertEqual(result["status"], "unknown", result)
        self.assertEqual(target.read_bytes(), b"foreign replacement")

        original_pins = self.original_pins
        self.tearDown()
        self.setUp()
        self.original_pins = original_pins
        target = self.helper_dir / "odysseus-predeploy-backup-root-helper.py"

        def mutate_held_old_inode():
            with target.open("r+b") as stream:
                stream.write(b"X")
                stream.flush()
                os.fsync(stream.fileno())

        result = self._replace(self._operations(api=FaultApi(on_candidate_fsync=mutate_held_old_inode)))
        self.assertEqual(result["status"], "unknown", result)
        self.assertEqual(target.read_bytes(), b"X" + self.old_source[1:])


if __name__ == "__main__":
    unittest.main()
