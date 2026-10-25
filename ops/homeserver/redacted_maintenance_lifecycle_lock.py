#!/usr/bin/env python3
"""Descriptor-bound locks shared by the Debian maintenance writers."""
from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from typing import Any

try:
    import grp
except ImportError:
    grp = None
try:
    import pwd
except ImportError:
    pwd = None
try:
    import fcntl
except ImportError:
    fcntl = None


SHARED_DIRECTORY = "/var/lib/odysseus-maintenance"
SHARED_LOCK = SHARED_DIRECTORY + "/lifecycle.lock"
LEGACY_LOCK = "/tmp/odysseus-auto-update.lock"
BACKUP_STATE_DIRECTORY = "/var/lib/odysseus-predeploy-backup-root-helper"
BACKUP_LIFECYCLE_LOCK = BACKUP_STATE_DIRECTORY + "/lifecycle.lock"
BACKUP_EXECUTION_LOCK = BACKUP_STATE_DIRECTORY + "/execution.lock"
LOCK_MODE = 0o660
LEGACY_LOCK_MODE = 0o600
ROOT_LOCK_MODE = 0o600


class LifecycleLockError(OSError):
    """A lock path or its identity failed the fixed safety contract."""


def _identity(info: Any) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        stat.S_IFMT(info.st_mode),
        info.st_uid,
        info.st_gid,
        stat.S_IMODE(info.st_mode),
    )


def _same_lock_metadata(left: Any, right: Any) -> bool:
    return _identity(left) == _identity(right) and left.st_nlink == right.st_nlink == 1


def _safe_directory(info: Any, *, uid: int, gid: int, mode: int | None = None) -> bool:
    return (
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == uid
        and info.st_gid == gid
        and info.st_nlink >= 1
        and (mode is None or stat.S_IMODE(info.st_mode) == mode)
        and (mode is not None or stat.S_IMODE(info.st_mode) & 0o022 == 0)
    )


def _safe_regular(info: Any, *, uid: int, gid: int, mode: int) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == uid
        and info.st_gid == gid
        and stat.S_IMODE(info.st_mode) == mode
        and info.st_nlink == 1
    )


def homebase_private_gid(*, users: Any = pwd, groups: Any = grp) -> tuple[int, int]:
    """Return homebase UID/GID only if its primary group is actually private."""
    if users is None or groups is None:
        raise LifecycleLockError(errno.ENOTSUP, "POSIX account databases unavailable")
    account = users.getpwnam("homebase")
    uid = int(account.pw_uid)
    gid = int(account.pw_gid)
    if uid <= 0 or gid <= 0:
        raise LifecycleLockError(errno.EPERM, "unsafe account identity")
    group = groups.getgrgid(gid)
    members = tuple(group.gr_mem)
    if any(member != "homebase" for member in members):
        raise LifecycleLockError(errno.EPERM, "group has another supplementary member")
    for other in users.getpwall():
        if other.pw_name != "homebase" and int(other.pw_gid) == gid:
            raise LifecycleLockError(errno.EPERM, "shared primary group")
    return uid, gid


def _directory_flags() -> int:
    required = ("O_RDONLY", "O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
    if any(not hasattr(os, flag) for flag in required):
        raise LifecycleLockError(errno.ENOTSUP, "safe directory descriptors unavailable")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_components(
    path: str,
    *,
    root: str = "/",
    api: Any = os,
    final_uid: int = 0,
    final_gid: int = 0,
    final_mode: int | None = None,
) -> int:
    """Open every directory component without following a symlink."""
    if not path.startswith("/") or "//" in path or "/../" in path or "/./" in path:
        raise LifecycleLockError(errno.EINVAL, "invalid fixed path")
    flags = api.O_RDONLY | api.O_DIRECTORY | api.O_NOFOLLOW | api.O_CLOEXEC
    current: int | None = api.open(root, flags)
    following: int | None = None
    try:
        assert current is not None
        root_info = api.fstat(current)
        if not _safe_directory(root_info, uid=0, gid=0):
            raise LifecycleLockError(errno.EPERM, "unsafe filesystem root")
        components = path.split("/")[1:]
        for index, component in enumerate(components):
            if not component or component in {".", ".."}:
                raise LifecycleLockError(errno.EINVAL, "invalid path component")
            following = api.open(component, flags, dir_fd=current)
            info = api.fstat(following)
            final = index == len(components) - 1
            safe = _safe_directory(
                info,
                uid=final_uid if final else 0,
                gid=final_gid if final else 0,
                mode=final_mode if final else None,
            )
            if not safe:
                rejected = following
                following = None
                api.close(rejected)
                raise LifecycleLockError(errno.EPERM, "unsafe ancestor directory")
            previous = current
            current = None
            api.close(previous)
            current = following
            following = None
        assert current is not None
        return current
    except Exception:
        if following is not None:
            owned_following = following
            following = None
            try:
                api.close(owned_following)
            except Exception:
                pass
        if current is not None:
            owned_current = current
            current = None
            try:
                api.close(owned_current)
            except Exception:
                pass
        raise


def _open_shared_parent(*, root: str = "/", api: Any = os, gid: int) -> int:
    parent = _open_components(
        SHARED_DIRECTORY,
        root=root,
        api=api,
        final_uid=0,
        final_gid=gid,
        final_mode=0o750,
    )
    try:
        info = api.fstat(parent)
        if not _safe_directory(info, uid=0, gid=gid, mode=0o750):
            raise LifecycleLockError(errno.EPERM, "unsafe shared directory")
        return parent
    except Exception:
        try:
            api.close(parent)
        except Exception:
            pass
        raise


def _open_tmp_parent(*, root: str = "/", api: Any = os) -> int:
    parent = _open_components("/tmp", root=root, api=api, final_uid=0, final_gid=0, final_mode=0o1777)
    try:
        info = api.fstat(parent)
        if not (
            stat.S_ISDIR(info.st_mode)
            and info.st_uid == 0
            and info.st_gid == 0
            and stat.S_IMODE(info.st_mode) == 0o1777
        ):
            raise LifecycleLockError(errno.EPERM, "unsafe temporary directory")
        return parent
    except Exception:
        try:
            api.close(parent)
        except Exception:
            pass
        raise


def _open_root_state_directory(*, root: str = "/", api: Any = os) -> int:
    """Safe-create the root-only helper state directory below /var/lib."""
    parent = _open_components("/var/lib", root=root, api=api)
    name = BACKUP_STATE_DIRECTORY.rsplit("/", 1)[-1]
    directory: int | None = None
    result: int | None = None
    try:
        created = False
        try:
            api.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            try:
                api.mkdir(name, 0o700, dir_fd=parent)
                created = True
            except FileExistsError:
                pass
        flags = api.O_RDONLY | api.O_DIRECTORY | api.O_NOFOLLOW | api.O_CLOEXEC
        directory = api.open(name, flags, dir_fd=parent)
        if created:
            api.fchown(directory, 0, 0)
            api.fchmod(directory, 0o700)
            api.fsync(directory)
            api.fsync(parent)
        opened = api.fstat(directory)
        named = api.stat(name, dir_fd=parent, follow_symlinks=False)
        if not _safe_directory(opened, uid=0, gid=0, mode=0o700):
            raise LifecycleLockError(errno.EPERM, "unsafe helper state directory")
        if not _safe_directory(named, uid=0, gid=0, mode=0o700) or _identity(opened) != _identity(named):
            raise LifecycleLockError(errno.ESTALE, "helper state directory changed")
        if created:
            _check = api.stat(name, dir_fd=parent, follow_symlinks=False)
            if not _safe_directory(_check, uid=0, gid=0, mode=0o700) or _identity(_check) != _identity(api.fstat(directory)):
                raise LifecycleLockError(errno.ESTALE, "helper state directory changed after creation")
        result = directory
        directory = None
    except Exception:
        if directory is not None:
            owned_directory = directory
            directory = None
            try:
                api.close(owned_directory)
            except Exception:
                pass
        raise
    finally:
        try:
            api.close(parent)
        except Exception:
            if result is not None:
                owned_result = result
                result = None
                try:
                    api.close(owned_result)
                except Exception:
                    pass
            raise
    if result is None:
        raise LifecycleLockError(errno.EIO, "helper state directory returned no descriptor")
    return result


def _check_named_descriptor(
    parent: int,
    name: str,
    descriptor: int,
    *,
    uid: int,
    gid: int,
    mode: int,
    api: Any = os,
) -> Any:
    named = api.stat(name, dir_fd=parent, follow_symlinks=False)
    held = api.fstat(descriptor)
    if not _safe_regular(named, uid=uid, gid=gid, mode=mode):
        raise LifecycleLockError(errno.EPERM, "unsafe lock metadata")
    if not _safe_regular(held, uid=uid, gid=gid, mode=mode):
        raise LifecycleLockError(errno.EPERM, "unsafe lock descriptor")
    if not _same_lock_metadata(named, held):
        raise LifecycleLockError(errno.ESTALE, "lock path changed")
    return held


def _acquire_existing(
    parent: int,
    name: str,
    *,
    uid: int,
    gid: int,
    mode: int,
    api: Any = os,
) -> int:
    flags = api.O_RDWR | api.O_NOFOLLOW | api.O_CLOEXEC | getattr(api, "O_NONBLOCK", 0)
    descriptor: int | None = None
    try:
        before = api.stat(name, dir_fd=parent, follow_symlinks=False)
        if not _safe_regular(before, uid=uid, gid=gid, mode=mode):
            raise LifecycleLockError(errno.EPERM, "unsafe lock path")
        descriptor = api.open(name, flags, dir_fd=parent)
        opened = api.fstat(descriptor)
        if not _same_lock_metadata(before, opened):
            raise LifecycleLockError(errno.ESTALE, "lock changed while opening")
        if fcntl is None:
            raise LifecycleLockError(errno.ENOTSUP, "POSIX flock unavailable")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _check_named_descriptor(parent, name, descriptor, uid=uid, gid=gid, mode=mode, api=api)
        result = descriptor
        descriptor = None
        return result
    finally:
        if descriptor is not None:
            api.close(descriptor)


def acquire_shared_lock(*, root: str = "/", api: Any = os) -> int:
    """Acquire the published shared lifecycle lock without creating anything."""
    if os.name != "posix" or fcntl is None:
        raise LifecycleLockError(errno.ENOTSUP, "POSIX flock unavailable")
    uid, gid = homebase_private_gid()
    caller = int(api.geteuid())
    if caller not in {0, uid}:
        raise LifecycleLockError(errno.EPERM, "unapproved lock caller")
    parent = _open_shared_parent(root=root, api=api, gid=gid)
    descriptor: int | None = None
    try:
        descriptor = _acquire_existing(parent, "lifecycle.lock", uid=0, gid=gid, mode=LOCK_MODE, api=api)
    finally:
        try:
            api.close(parent)
        except Exception:
            if descriptor is not None:
                try:
                    close_lock(descriptor, api=api)
                except Exception:
                    pass
            raise
    if descriptor is None:
        raise LifecycleLockError(errno.EIO, "lock acquisition returned no descriptor")
    return descriptor


def acquire_legacy_lock(*, root: str = "/", api: Any = os, create: bool = True) -> int:
    """Hold the old updater lock while bootstrap crosses the lifecycle boundary."""
    if os.name != "posix" or fcntl is None:
        raise LifecycleLockError(errno.ENOTSUP, "POSIX flock unavailable")
    uid, gid = homebase_private_gid()
    if int(api.geteuid()) != uid:
        raise LifecycleLockError(errno.EPERM, "legacy lock requires homebase")
    parent = _open_tmp_parent(root=root, api=api)
    result: int | None = None
    try:
        try:
            result = _acquire_existing(parent, "odysseus-auto-update.lock", uid=uid, gid=gid, mode=LEGACY_LOCK_MODE, api=api)
        except FileNotFoundError:
            if not create:
                raise
        if result is None:
            try:
                descriptor = api.open(
                    "odysseus-auto-update.lock",
                    api.O_RDWR | api.O_CREAT | api.O_EXCL | api.O_NOFOLLOW | api.O_CLOEXEC,
                    LEGACY_LOCK_MODE,
                    dir_fd=parent,
                )
            except FileExistsError:
                result = _acquire_existing(parent, "odysseus-auto-update.lock", uid=uid, gid=gid, mode=LEGACY_LOCK_MODE, api=api)
            else:
                try:
                    api.fchown(descriptor, uid, gid)
                    api.fchmod(descriptor, LEGACY_LOCK_MODE)
                    api.fsync(descriptor)
                    api.fsync(parent)
                    _check_named_descriptor(parent, "odysseus-auto-update.lock", descriptor, uid=uid, gid=gid, mode=LEGACY_LOCK_MODE, api=api)
                    if fcntl is None:
                        raise LifecycleLockError(errno.ENOTSUP, "POSIX flock unavailable")
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    _check_named_descriptor(parent, "odysseus-auto-update.lock", descriptor, uid=uid, gid=gid, mode=LEGACY_LOCK_MODE, api=api)
                    result = descriptor
                    descriptor = -1
                except Exception:
                    if descriptor >= 0:
                        owned = descriptor
                        descriptor = -1
                        try:
                            api.close(owned)
                        except Exception:
                            pass
                    raise
    finally:
        try:
            api.close(parent)
        except Exception:
            if result is not None:
                owned_result = result
                result = None
                try:
                    close_lock(owned_result, api=api)
                except Exception:
                    pass
            raise
    if result is None:
        raise LifecycleLockError(errno.EIO, "legacy lock acquisition returned no descriptor")
    return result


def acquire_root_state_lock(
    path: str,
    *,
    root: str = "/",
    api: Any = os,
    create: bool = True,
) -> int:
    """Safe-create and acquire one root-owned backup-helper state lock."""
    if path not in {BACKUP_LIFECYCLE_LOCK, BACKUP_EXECUTION_LOCK}:
        raise LifecycleLockError(errno.EPERM, "unexpected root lock")
    parent = _open_root_state_directory(root=root, api=api)
    result: int | None = None
    try:
        state = api.fstat(parent)
        if not _safe_directory(state, uid=0, gid=0, mode=0o700):
            raise LifecycleLockError(errno.EPERM, "unsafe helper state directory")
        name = path.rsplit("/", 1)[-1]
        try:
            result = _acquire_existing(parent, name, uid=0, gid=0, mode=ROOT_LOCK_MODE, api=api)
        except FileNotFoundError:
            if not create:
                raise
        if result is None:
            try:
                descriptor = api.open(
                    name,
                    api.O_RDWR | api.O_CREAT | api.O_EXCL | api.O_NOFOLLOW | api.O_CLOEXEC,
                    ROOT_LOCK_MODE,
                    dir_fd=parent,
                )
            except FileExistsError:
                result = _acquire_existing(parent, name, uid=0, gid=0, mode=ROOT_LOCK_MODE, api=api)
            else:
                try:
                    api.fchown(descriptor, 0, 0)
                    api.fchmod(descriptor, ROOT_LOCK_MODE)
                    api.fsync(descriptor)
                    api.fsync(parent)
                    _check_named_descriptor(parent, name, descriptor, uid=0, gid=0, mode=ROOT_LOCK_MODE, api=api)
                    if fcntl is None:
                        raise LifecycleLockError(errno.ENOTSUP, "POSIX flock unavailable")
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    _check_named_descriptor(parent, name, descriptor, uid=0, gid=0, mode=ROOT_LOCK_MODE, api=api)
                    result = descriptor
                    descriptor = -1
                except Exception:
                    if descriptor >= 0:
                        owned = descriptor
                        descriptor = -1
                        try:
                            api.close(owned)
                        except Exception:
                            pass
                    raise
    finally:
        try:
            api.close(parent)
        except Exception:
            if result is not None:
                owned_result = result
                result = None
                try:
                    close_lock(owned_result, api=api)
                except Exception:
                    pass
            raise
    if result is None:
        raise LifecycleLockError(errno.EIO, "root state lock acquisition returned no descriptor")
    return result


@dataclass
class HeldLocks:
    """Close descriptors in reverse acquisition order."""
    descriptors: list[int]
    api: Any = os

    def close(self) -> None:
        first_error: Exception | None = None
        while self.descriptors:
            descriptor = self.descriptors.pop()
            try:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
            try:
                self.api.close(descriptor)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error


def close_lock(descriptor: int, *, api: Any = os) -> None:
    """Release one held lock descriptor."""
    try:
        if fcntl is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        api.close(descriptor)
