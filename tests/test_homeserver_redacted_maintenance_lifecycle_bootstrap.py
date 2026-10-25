from __future__ import annotations

import errno
import os
import stat
import tempfile
import unittest
from types import SimpleNamespace

try:
    import fcntl
except ImportError:
    fcntl = None

from ops.homeserver import redacted_maintenance_lifecycle_bootstrap as bootstrap
from ops.homeserver import redacted_maintenance_lifecycle_lock as lifecycle


POSIX_ROOT = os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0 and fcntl is not None


@unittest.skipUnless(POSIX_ROOT, "root-owned temporary filesystem required")
class LifecycleBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="odysseus-lifecycle-bootstrap-")
        self.root = self.temp.name
        self.gid = 65534
        self._mkdir("/var", 0, 0, 0o755)
        self._mkdir("/var/lib", 0, 0, 0o755)
        self._mkdir(lifecycle.BACKUP_STATE_DIRECTORY, 0, 0, 0o700)
        self.unit = SimpleNamespace(
            returncode=0,
            stdout=b"LoadState=loaded\nActiveState=inactive\nSubState=dead\nUnitFileState=disabled\n",
        )
        self.ops = bootstrap.BootstrapOperations(
            root_path=self.root,
            euid=0,
            group_identity=lambda: (1200, self.gid),
            unit_runner=lambda *_args, **_kwargs: self.unit,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _path(self, path: str) -> str:
        return os.path.join(self.root, path.lstrip("/"))

    def _mkdir(self, path: str, uid: int, gid: int, mode: int) -> str:
        target = self._path(path)
        os.makedirs(target, mode=0o700, exist_ok=True)
        os.chown(target, uid, gid)
        os.chmod(target, mode)
        return target

    def _lock(self, path: str, uid: int = 0, gid: int = 0, mode: int = 0o600) -> str:
        target = self._path(path)
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_RDWR, mode)
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, mode)
        os.close(descriptor)
        return target

    def _run(self) -> dict[str, object]:
        return bootstrap.bootstrap(execute=True, operations=self.ops)

    def test_default_is_inert_and_cli_never_bootstraps(self) -> None:
        value = bootstrap.bootstrap(execute=False, operations=self.ops)
        self.assertTrue(bootstrap.validate_envelope(value))
        self.assertEqual(value["error_code"], "execution_disabled")
        self.assertFalse(os.path.lexists(self._path(lifecycle.SHARED_DIRECTORY)))

    def test_publication_sets_exact_metadata_and_repeat_is_idempotent(self) -> None:
        first = self._run()
        self.assertTrue(bootstrap.validate_envelope(first))
        self.assertEqual(first["status"], "ready")
        self.assertTrue(first["publication_attempted"])
        directory = os.stat(self._path(lifecycle.SHARED_DIRECTORY), follow_symlinks=False)
        lock = os.stat(self._path(lifecycle.SHARED_LOCK), follow_symlinks=False)
        self.assertEqual((directory.st_uid, directory.st_gid, stat.S_IMODE(directory.st_mode)), (0, self.gid, 0o750))
        self.assertEqual((lock.st_uid, lock.st_gid, stat.S_IMODE(lock.st_mode), lock.st_nlink), (0, self.gid, 0o660, 1))

        second = self._run()
        self.assertTrue(bootstrap.validate_envelope(second))
        self.assertEqual(second["status"], "ready")
        self.assertFalse(second["publication_attempted"])
        for name in ("lifecycle.lock", "execution.lock"):
            path = self._path(lifecycle.BACKUP_STATE_DIRECTORY + "/" + name)
            info = os.stat(path, follow_symlinks=False)
            self.assertEqual((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink), (0, 0, 0o600, 1))

    def test_wrong_existing_shared_objects_block_without_repair(self) -> None:
        directory = self._mkdir(lifecycle.SHARED_DIRECTORY, 0, 0, 0o700)
        result = self._run()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(stat.S_IMODE(os.stat(directory).st_mode), 0o700)
        self.assertFalse(os.path.lexists(self._path(lifecycle.SHARED_LOCK)))

    def test_unit_or_arm_activity_blocks_before_publication(self) -> None:
        self.unit = SimpleNamespace(returncode=0, stdout=b"LoadState=loaded\nActiveState=active\nSubState=running\nUnitFileState=enabled\n")
        result = self._run()
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(os.path.lexists(self._path(lifecycle.SHARED_DIRECTORY)))

        self.unit = SimpleNamespace(returncode=0, stdout=b"LoadState=loaded\nActiveState=inactive\nSubState=dead\nUnitFileState=disabled\n")
        arm = os.open(self._path(lifecycle.BACKUP_STATE_DIRECTORY + "/arm.json"), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(arm)
        result = self._run()
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(os.path.lexists(self._path(lifecycle.SHARED_DIRECTORY)))

    def test_root_lock_busy_fails_closed_before_publication(self) -> None:
        path = self._lock(lifecycle.BACKUP_LIFECYCLE_LOCK)
        held = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            result = self._run()
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["error_code"], "lock_busy")
            self.assertFalse(os.path.lexists(self._path(lifecycle.SHARED_DIRECTORY)))
        finally:
            fcntl.flock(held, fcntl.LOCK_UN)
            os.close(held)

    def test_mutate_then_raise_at_exposure_is_unknown_and_preserves_objects(self) -> None:
        class ExposureAPI:
            def __getattr__(self, name: str) -> object:
                return getattr(os, name)

            def fchmod(self, descriptor: int, mode: int) -> None:
                os.fchmod(descriptor, mode)
                if mode == 0o750:
                    raise OSError(errno.EIO, "publication syscall returned ambiguously")

        self.ops.api = ExposureAPI()
        result = self._run()
        self.assertTrue(bootstrap.validate_envelope(result))
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(result["manual_recovery_required"])
        self.assertTrue(os.path.isdir(self._path(lifecycle.SHARED_DIRECTORY)))
        self.assertTrue(os.path.isfile(self._path(lifecycle.SHARED_LOCK)))
        directory = os.stat(self._path(lifecycle.SHARED_DIRECTORY), follow_symlinks=False)
        self.assertEqual((directory.st_gid, stat.S_IMODE(directory.st_mode)), (self.gid, 0o750))

    def test_changed_unit_on_postflight_is_unknown_and_published_directory_remains(self) -> None:
        calls = 0

        def unit_runner(*_args: object, **_kwargs: object) -> object:
            nonlocal calls
            calls += 1
            output = (
                b"LoadState=loaded\nActiveState=inactive\nSubState=dead\nUnitFileState=disabled\n"
                if calls == 1
                else b"LoadState=loaded\nActiveState=active\nSubState=running\nUnitFileState=enabled\n"
            )
            return SimpleNamespace(returncode=0, stdout=output)

        self.ops.unit_runner = unit_runner
        result = self._run()
        self.assertEqual(calls, 2)
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(os.path.isdir(self._path(lifecycle.SHARED_DIRECTORY)))


if __name__ == "__main__":
    unittest.main()
