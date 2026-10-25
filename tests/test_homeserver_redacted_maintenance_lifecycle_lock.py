from __future__ import annotations

import errno
import os
import stat
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from ops.homeserver import redacted_maintenance_lifecycle_lock as lifecycle


POSIX_ROOT = os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0 and lifecycle.fcntl is not None


class FakeAccounts:
    def __init__(self, users: list[object]):
        self.users = users

    def getpwnam(self, name: str) -> object:
        return next(user for user in self.users if user.pw_name == name)

    def getpwall(self) -> list[object]:
        return self.users


class FakeGroups:
    def __init__(self, group: object):
        self.group = group

    def getgrgid(self, gid: int) -> object:
        if gid != self.group.gr_gid:
            raise KeyError(gid)
        return self.group


@unittest.skipUnless(os.name == "posix" and lifecycle.fcntl is not None, "POSIX flock required")
class LifecycleLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="odysseus-lifecycle-lock-")
        self.root = self.temp.name
        os.chown(self.root, 0, 0)
        os.chmod(self.root, 0o755)
        self.uid = 1000
        self.gid = 65534
        self._mkdir("/var", 0, 0, 0o755)
        self._mkdir("/var/lib", 0, 0, 0o755)
        self._mkdir(lifecycle.SHARED_DIRECTORY, 0, self.gid, 0o750)

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

    def _lock_file(self, mode: int = 0o660, gid: int | None = None) -> str:
        target = self._path(lifecycle.SHARED_LOCK)
        descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_RDWR, mode)
        os.fchown(descriptor, 0, self.gid if gid is None else gid)
        os.fchmod(descriptor, mode)
        os.close(descriptor)
        return target

    def _acquire(self) -> int:
        with mock.patch.object(lifecycle, "homebase_private_gid", return_value=(self.uid, self.gid)):
            return lifecycle.acquire_shared_lock(root=self.root)

    def test_private_group_requires_supplementary_membership_and_no_other_primary_user(self) -> None:
        owner = SimpleNamespace(pw_name="homebase", pw_uid=1200, pw_gid=2400)
        other = SimpleNamespace(pw_name="operator", pw_uid=1201, pw_gid=2401)
        group = SimpleNamespace(gr_gid=2400, gr_mem=["homebase"])
        self.assertEqual(lifecycle.homebase_private_gid(users=FakeAccounts([owner, other]), groups=FakeGroups(group)), (1200, 2400))

        group.gr_mem = []
        self.assertEqual(lifecycle.homebase_private_gid(users=FakeAccounts([owner, other]), groups=FakeGroups(group)), (1200, 2400))

        group.gr_mem = ["operator"]
        with self.assertRaises(lifecycle.LifecycleLockError):
            lifecycle.homebase_private_gid(users=FakeAccounts([owner, other]), groups=FakeGroups(group))

        group.gr_mem = ["homebase", "operator"]
        with self.assertRaises(lifecycle.LifecycleLockError):
            lifecycle.homebase_private_gid(users=FakeAccounts([owner, other]), groups=FakeGroups(group))

        group.gr_mem = ["homebase"]
        other.pw_gid = 2400
        with self.assertRaises(lifecycle.LifecycleLockError):
            lifecycle.homebase_private_gid(users=FakeAccounts([owner, other]), groups=FakeGroups(group))

        other.pw_gid = 2401
        owner.pw_uid = 0
        with self.assertRaises(lifecycle.LifecycleLockError):
            lifecycle.homebase_private_gid(users=FakeAccounts([owner]), groups=FakeGroups(group))

    def test_normal_acquire_does_not_create_missing_shared_directory_or_lock(self) -> None:
        os.unlink(self._path(lifecycle.SHARED_LOCK)) if os.path.exists(self._path(lifecycle.SHARED_LOCK)) else None
        os.rmdir(self._path(lifecycle.SHARED_DIRECTORY))
        with self.assertRaises(OSError):
            self._acquire()
        self.assertFalse(os.path.lexists(self._path(lifecycle.SHARED_DIRECTORY)))

    def test_rejects_wrong_group_mode_symlink_hardlink_and_nonregular_lock(self) -> None:
        target = self._lock_file(gid=12345)
        with self.assertRaises(OSError):
            self._acquire()
        os.unlink(target)

        target = self._lock_file(mode=0o664)
        with self.assertRaises(OSError):
            self._acquire()
        os.unlink(target)

        target = self._lock_file()
        os.link(target, target + ".alias")
        with self.assertRaises(OSError):
            self._acquire()
        os.unlink(target + ".alias")
        os.unlink(target)

        os.symlink("missing-target", target)
        with self.assertRaises(OSError):
            self._acquire()
        os.unlink(target)

        os.mkdir(target)
        with self.assertRaises(OSError):
            self._acquire()
        os.rmdir(target)

    def test_rejects_symlinked_ancestor_and_caller_uid(self) -> None:
        target = self._path("/var/lib")
        os.rmdir(self._path(lifecycle.SHARED_DIRECTORY))
        os.rmdir(target)
        os.mkdir(self._path("/var/lib-real"), 0o755)
        os.symlink("lib-real", target)
        self._lock_file() if False else None
        with self.assertRaises(OSError):
            self._acquire()

        os.unlink(target)
        os.mkdir(target, 0o755)
        os.chown(target, 0, 0)
        self._mkdir(lifecycle.SHARED_DIRECTORY, 0, self.gid, 0o750)
        self._lock_file()
        with mock.patch.object(lifecycle, "homebase_private_gid", return_value=(self.uid, self.gid)), mock.patch("os.geteuid", return_value=5555):
            with self.assertRaises(lifecycle.LifecycleLockError):
                lifecycle.acquire_shared_lock(root=self.root)

    def test_detects_inode_swap_between_open_and_after_flock(self) -> None:
        target = self._lock_file()
        original_flock = lifecycle.fcntl.flock
        swapped = False

        def swap_then_lock(descriptor: int, operation: int) -> None:
            nonlocal swapped
            if not swapped and operation & lifecycle.fcntl.LOCK_EX:
                replacement = target + ".replacement"
                fd = os.open(replacement, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o660)
                os.fchown(fd, 0, self.gid)
                os.fchmod(fd, 0o660)
                os.close(fd)
                os.replace(replacement, target)
                swapped = True
            original_flock(descriptor, operation)

        with mock.patch.object(lifecycle.fcntl, "flock", side_effect=swap_then_lock):
            with self.assertRaises(lifecycle.LifecycleLockError):
                self._acquire()

    def test_component_walk_closes_the_new_fd_when_fstat_fails(self) -> None:
        class TrackingAPI:
            def __init__(self) -> None:
                self.opened: set[int] = set()
                self.open_count = 0

            def __getattr__(self, name: str) -> object:
                return getattr(os, name)

            def open(self, *args: object, **kwargs: object) -> int:
                descriptor = os.open(*args, **kwargs)
                self.opened.add(descriptor)
                self.open_count += 1
                return descriptor

            def fstat(self, descriptor: int) -> object:
                if self.open_count == 2:
                    raise OSError(errno.EIO, "injected fstat failure")
                return os.fstat(descriptor)

            def close(self, descriptor: int) -> None:
                self.opened.discard(descriptor)
                os.close(descriptor)

        api = TrackingAPI()
        with self.assertRaises(OSError):
            lifecycle._open_components("/var", root=self.root, api=api)
        self.assertEqual(api.opened, set())

    def test_component_walk_does_not_retry_close_that_raised_after_closing(self) -> None:
        class CloseThenRaiseAPI:
            def __init__(self) -> None:
                self.opened: set[int] = set()
                self.close_attempts: list[int] = []
                self.failed = False

            def __getattr__(self, name: str) -> object:
                return getattr(os, name)

            def open(self, *args: object, **kwargs: object) -> int:
                descriptor = os.open(*args, **kwargs)
                self.opened.add(descriptor)
                return descriptor

            def close(self, descriptor: int) -> None:
                self.close_attempts.append(descriptor)
                os.close(descriptor)
                self.opened.discard(descriptor)
                if not self.failed:
                    self.failed = True
                    raise OSError(errno.EIO, "injected close after success")

        api = CloseThenRaiseAPI()
        with self.assertRaises(OSError):
            lifecycle._open_components("/var", root=self.root, api=api)
        self.assertEqual(api.opened, set())
        self.assertEqual(len(api.close_attempts), len(set(api.close_attempts)))

    def test_parent_descriptor_closes_when_post_walk_fstat_fails(self) -> None:
        wanted_gid = self.gid

        class FailingParentStatAPI:
            def __init__(self) -> None:
                self.opened: set[int] = set()

            def __getattr__(self, name: str) -> object:
                return getattr(os, name)

            def open(self, *args: object, **kwargs: object) -> int:
                descriptor = os.open(*args, **kwargs)
                self.opened.add(descriptor)
                return descriptor

            def fstat(self, descriptor: int) -> object:
                if descriptor in self.opened and stat.S_ISDIR(os.fstat(descriptor).st_mode) and os.fstat(descriptor).st_gid == wanted_gid:
                    raise OSError(errno.EIO, "injected parent fstat failure")
                return os.fstat(descriptor)

            def close(self, descriptor: int) -> None:
                os.close(descriptor)
                self.opened.discard(descriptor)

        api = FailingParentStatAPI()
        with self.assertRaises(OSError):
            lifecycle._open_shared_parent(root=self.root, api=api, gid=self.gid)
        self.assertEqual(api.opened, set())

    def test_shared_lock_fd_is_closed_if_parent_close_fails(self) -> None:
        self._lock_file()

        class ParentCloseAPI:
            def __init__(self) -> None:
                self.opened: set[int] = set()

            def __getattr__(self, name: str) -> object:
                return getattr(os, name)

            def open(self, *args: object, **kwargs: object) -> int:
                descriptor = os.open(*args, **kwargs)
                self.opened.add(descriptor)
                return descriptor

            def close(self, descriptor: int) -> None:
                info = os.fstat(descriptor)
                os.close(descriptor)
                self.opened.discard(descriptor)
                if stat.S_ISDIR(info.st_mode) and info.st_gid == self_gid and stat.S_IMODE(info.st_mode) == 0o750:
                    raise OSError(errno.EIO, "injected parent close failure")

        self_gid = self.gid
        api = ParentCloseAPI()
        with mock.patch.object(lifecycle, "homebase_private_gid", return_value=(self.uid, self.gid)):
            with self.assertRaises(OSError):
                lifecycle.acquire_shared_lock(root=self.root, api=api)
        self.assertEqual(api.opened, set())

    def test_legacy_and_root_lock_fds_close_if_parent_close_fails(self) -> None:
        self._mkdir("/tmp", 0, 0, 0o1777)
        self._mkdir(lifecycle.BACKUP_STATE_DIRECTORY, 0, 0, 0o700)

        class ParentCloseAPI:
            def __init__(self, parent_path: str) -> None:
                self.opened: set[int] = set()
                self.failed = False
                target = os.stat(parent_path)
                self.parent_identity = (target.st_dev, target.st_ino)

            def __getattr__(self, name: str) -> object:
                return getattr(os, name)

            def geteuid(self) -> int:
                return self_uid

            def open(self, *args: object, **kwargs: object) -> int:
                descriptor = os.open(*args, **kwargs)
                self.opened.add(descriptor)
                return descriptor

            def close(self, descriptor: int) -> None:
                info = os.fstat(descriptor)
                os.close(descriptor)
                self.opened.discard(descriptor)
                if not self.failed and (info.st_dev, info.st_ino) == self.parent_identity:
                    self.failed = True
                    raise OSError(errno.EIO, "injected parent close failure")

        self_uid = self.uid
        with mock.patch.object(lifecycle, "homebase_private_gid", return_value=(self.uid, self.gid)):
            legacy_api = ParentCloseAPI(self._path("/tmp"))
            with self.assertRaises(OSError):
                lifecycle.acquire_legacy_lock(root=self.root, api=legacy_api)
            self.assertEqual(legacy_api.opened, set())
            self.assertTrue(os.path.exists(self._path(lifecycle.LEGACY_LOCK)))

            root_api = ParentCloseAPI(self._path(lifecycle.BACKUP_STATE_DIRECTORY))
            with self.assertRaises(OSError):
                lifecycle.acquire_root_state_lock(lifecycle.BACKUP_LIFECYCLE_LOCK, root=self.root, api=root_api)
            self.assertEqual(root_api.opened, set())
            self.assertTrue(os.path.exists(self._path(lifecycle.BACKUP_LIFECYCLE_LOCK)))

    def test_root_lock_existing_and_create_race_close_if_parent_close_fails(self) -> None:
        self._mkdir(lifecycle.BACKUP_STATE_DIRECTORY, 0, 0, 0o700)
        name = lifecycle.BACKUP_EXECUTION_LOCK.rsplit("/", 1)[-1]
        target = self._path(lifecycle.BACKUP_EXECUTION_LOCK)
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.fchown(fd, 0, 0)
        os.fchmod(fd, 0o600)
        os.close(fd)

        class RaceCloseAPI:
            def __init__(self, race: bool) -> None:
                self.opened: set[int] = set()
                self.race = race
                self.failed = False
                self.stat_raced = False
                parent_info = os.stat(parent_path)
                self.parent_identity = (parent_info.st_dev, parent_info.st_ino)

            def __getattr__(self, name: str) -> object:
                return getattr(os, name)

            def stat(self, path: str, *args: object, **kwargs: object) -> object:
                if self.race and path == name and not self.stat_raced:
                    self.stat_raced = True
                    raise FileNotFoundError(errno.ENOENT, "injected missing-before-create")
                return os.stat(path, *args, **kwargs)

            def open(self, path: str, flags: int, *args: object, **kwargs: object) -> int:
                if self.race and path == name and flags & os.O_EXCL:
                    self.race = False
                    raise FileExistsError(errno.EEXIST, "injected create race")
                descriptor = os.open(path, flags, *args, **kwargs)
                self.opened.add(descriptor)
                return descriptor

            def close(self, descriptor: int) -> None:
                info = os.fstat(descriptor)
                os.close(descriptor)
                self.opened.discard(descriptor)
                if not self.failed and (info.st_dev, info.st_ino) == self.parent_identity:
                    self.failed = True
                    raise OSError(errno.EIO, "injected parent close failure")

        parent_path = self._path(lifecycle.BACKUP_STATE_DIRECTORY)
        for race in (False, True):
            api = RaceCloseAPI(race)
            with self.assertRaises(OSError):
                lifecycle.acquire_root_state_lock(lifecycle.BACKUP_EXECUTION_LOCK, root=self.root, api=api)
            self.assertEqual(api.opened, set())

    def test_root_helper_state_and_both_locks_safe_create_when_absent(self) -> None:
        parent = self._path(lifecycle.BACKUP_STATE_DIRECTORY)
        self.assertFalse(os.path.lexists(parent))
        lifecycle_fd = lifecycle.acquire_root_state_lock(lifecycle.BACKUP_LIFECYCLE_LOCK, root=self.root)
        try:
            info = os.stat(parent, follow_symlinks=False)
            self.assertTrue(lifecycle._safe_directory(info, uid=0, gid=0, mode=0o700))
            lock = os.stat(self._path(lifecycle.BACKUP_LIFECYCLE_LOCK), follow_symlinks=False)
            self.assertTrue(lifecycle._safe_regular(lock, uid=0, gid=0, mode=0o600))
        finally:
            lifecycle.close_lock(lifecycle_fd)
        execution_fd = lifecycle.acquire_root_state_lock(lifecycle.BACKUP_EXECUTION_LOCK, root=self.root)
        lifecycle.close_lock(execution_fd)
        execution = os.stat(self._path(lifecycle.BACKUP_EXECUTION_LOCK), follow_symlinks=False)
        self.assertTrue(lifecycle._safe_regular(execution, uid=0, gid=0, mode=0o600))

    def test_root_helper_state_wrong_existing_directory_blocks_without_repair(self) -> None:
        target = self._mkdir(lifecycle.BACKUP_STATE_DIRECTORY, 0, 0, 0o750)
        with self.assertRaises(lifecycle.LifecycleLockError):
            lifecycle.acquire_root_state_lock(lifecycle.BACKUP_LIFECYCLE_LOCK, root=self.root)
        self.assertEqual(stat.S_IMODE(os.stat(target, follow_symlinks=False).st_mode), 0o750)

    def test_held_lock_cleanup_continues_after_one_close_failure(self) -> None:
        first_path = os.path.join(self.root, "first.lock")
        second_path = os.path.join(self.root, "second.lock")
        first = os.open(first_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        second = os.open(second_path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        lifecycle.fcntl.flock(first, lifecycle.fcntl.LOCK_EX | lifecycle.fcntl.LOCK_NB)
        lifecycle.fcntl.flock(second, lifecycle.fcntl.LOCK_EX | lifecycle.fcntl.LOCK_NB)

        class CloseAPI:
            def close(self, descriptor: int) -> None:
                os.close(descriptor)
                if descriptor == second:
                    raise OSError(errno.EIO, "injected close failure")

        held = lifecycle.HeldLocks([first, second], CloseAPI())
        with self.assertRaises(OSError):
            held.close()
        for descriptor in (first, second):
            with self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_descriptor_is_cloexec_and_unlock_allows_a_contender(self) -> None:
        self._lock_file()
        held = self._acquire()
        self.assertFalse(os.get_inheritable(held))
        contender = os.open(self._path(lifecycle.SHARED_LOCK), os.O_RDWR | os.O_CLOEXEC)
        try:
            with self.assertRaises(BlockingIOError):
                lifecycle.fcntl.flock(contender, lifecycle.fcntl.LOCK_EX | lifecycle.fcntl.LOCK_NB)
        finally:
            os.close(contender)
            lifecycle.close_lock(held)
        second = self._acquire()
        lifecycle.close_lock(second)

    @unittest.skipUnless(POSIX_ROOT, "root privileges required for real setuid permission check")
    def test_homebase_equivalent_can_flock_but_cannot_create_rename_or_unlink(self) -> None:
        self._lock_file()
        reader, writer = os.pipe()
        release_reader, release_writer = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(reader)
            os.close(release_writer)
            try:
                os.setgroups([self.gid])
                os.setgid(self.gid)
                os.setuid(self.uid)
                with mock.patch.object(lifecycle, "homebase_private_gid", return_value=(self.uid, self.gid)):
                    held = lifecycle.acquire_shared_lock(root=self.root)
                parent = os.open(self._path(lifecycle.SHARED_DIRECTORY), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                outcomes = []
                try:
                    for operation in (
                        lambda: os.open("new.lock", os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600, dir_fd=parent),
                        lambda: os.unlink("lifecycle.lock", dir_fd=parent),
                        lambda: os.rename("lifecycle.lock", "renamed.lock", src_dir_fd=parent, dst_dir_fd=parent),
                    ):
                        try:
                            operation()
                            outcomes.append(b"unexpected")
                        except PermissionError:
                            outcomes.append(b"denied")
                    os.write(writer, b"".join(outcomes))
                    os.read(release_reader, 1)
                finally:
                    os.close(parent)
                    lifecycle.close_lock(held)
                os._exit(0)
            except BaseException as exc:
                os.write(writer, (type(exc).__name__ + ":" + str(getattr(exc, "errno", None))).encode("ascii"))
                os._exit(1)
        os.close(writer)
        os.close(release_reader)
        try:
            self.assertEqual(os.read(reader, 64), b"denieddenieddenied")
            with self.assertRaises(BlockingIOError):
                contender = self._acquire()
            os.write(release_writer, b"x")
            _, status = os.waitpid(pid, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)
            released = self._acquire()
            lifecycle.close_lock(released)
        finally:
            os.close(reader)
            os.close(release_writer)


if __name__ == "__main__":
    unittest.main()
