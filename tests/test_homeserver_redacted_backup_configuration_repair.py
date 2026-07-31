from __future__ import annotations

import json
import stat
from types import SimpleNamespace

import pytest

from ops.homeserver import redacted_backup_configuration_repair as repair


def _info(
    kind: int,
    mode: int,
    uid: int,
    gid: int = 1000,
    *,
    nlink: int = 1,
    size: int = 32,
):
    return SimpleNamespace(
        st_mode=kind | mode,
        st_uid=uid,
        st_gid=gid,
        st_nlink=nlink,
        st_size=size,
    )


class FakeOperations:
    directory_fd = 10
    password_fd = 11
    temporary_fd = 12
    configuration_fd = 13

    def __init__(self) -> None:
        self.directory = _info(stat.S_IFDIR, 0o700, 1000)
        self.password = _info(stat.S_IFREG, 0o640, 0, 0)
        self.configuration = _info(stat.S_IFREG, 0o600, 1000)
        self.temporary = _info(stat.S_IFREG, 0o600, 1000)
        self.temporary_exists = False
        self.configuration_replaced = False
        self.written = b""
        self.closed: list[int] = []
        self.fail: str | None = None

    def owner(self):
        return 1000, 1000

    def open_directory(self):
        if self.fail == "open_directory":
            raise OSError("synthetic-private-error")
        return self.directory_fd

    def stat_fd(self, descriptor):
        if self.fail == "post_password_stat" and descriptor == self.password_fd:
            self.fail = "post_password_stat_consumed"
            raise OSError("synthetic-private-error")
        return {
            self.directory_fd: self.directory,
            self.password_fd: self.password,
            self.temporary_fd: self.temporary,
            self.configuration_fd: self.configuration,
        }[descriptor]

    def stat_at(self, _directory_fd, name):
        if name == repair.TEMPORARY_NAME:
            if not self.temporary_exists:
                raise FileNotFoundError
            return self.temporary
        if name == repair.CONFIG_NAME:
            return self.configuration
        raise AssertionError(name)

    def open_at(self, _directory_fd, name, _flags, mode=0o600):
        if name == repair.PASSWORD_NAME:
            return self.password_fd
        if name == repair.TEMPORARY_NAME:
            if self.fail == "create_temporary":
                raise OSError("synthetic-private-error")
            assert mode == 0o600
            self.temporary_exists = True
            return self.temporary_fd
        if name == repair.CONFIG_NAME:
            return self.configuration_fd
        raise AssertionError(name)

    def fchown(self, descriptor, uid, gid):
        if self.fail == "rollback" and descriptor == self.password_fd and uid == 0:
            raise OSError("synthetic-private-error")
        target = self.password if descriptor == self.password_fd else self.temporary
        target.st_uid = uid
        target.st_gid = gid

    def fchmod(self, descriptor, mode):
        if descriptor == self.password_fd:
            self.password.st_mode = stat.S_IFREG | mode
        elif descriptor == self.temporary_fd:
            self.temporary.st_mode = stat.S_IFREG | mode
        elif descriptor == self.directory_fd:
            self.directory.st_mode = stat.S_IFDIR | mode
        else:
            raise AssertionError(descriptor)

    def write(self, descriptor, value):
        assert descriptor == self.temporary_fd
        chunk = value[:3]
        self.written += chunk
        return len(chunk)

    def read(self, descriptor, maximum):
        assert descriptor == self.configuration_fd
        return self.written[:maximum]

    def fsync(self, descriptor):
        if (
            self.fail == "post_replace_fsync"
            and descriptor == self.directory_fd
            and self.configuration_replaced
        ):
            raise OSError("synthetic-private-error")

    def replace_at(self, _directory_fd, source, target):
        assert (source, target) == (repair.TEMPORARY_NAME, repair.CONFIG_NAME)
        if self.fail == "replace":
            raise OSError("synthetic-private-error")
        self.temporary_exists = False
        self.configuration_replaced = True

    def unlink_at(self, _directory_fd, name):
        assert name == repair.TEMPORARY_NAME
        self.temporary_exists = False

    def close(self, descriptor):
        self.closed.append(descriptor)


def test_success_changes_only_metadata_and_exact_nonsecret_configuration():
    operations = FakeOperations()

    payload = repair.repair_backup_configuration(
        execute=True,
        operations=operations,
    )

    assert payload["status"] == "succeeded"
    assert payload["directory_metadata_repaired"] is True
    assert payload["password_metadata_repaired"] is True
    assert payload["configuration_replaced"] is True
    assert operations.password.st_uid == 1000
    assert operations.password.st_gid == 1000
    assert stat.S_IMODE(operations.password.st_mode) == 0o600
    assert operations.written == repair.CONFIG_BYTES
    assert operations.configuration_replaced is True
    assert operations.password_fd != operations.configuration_fd
    assert repair.validate_envelope(payload)
    encoded = json.dumps(payload, sort_keys=True)
    assert repair.CONFIG_DIRECTORY not in encoded
    assert "synthetic-private-error" not in encoded


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: setattr(
            value,
            "password",
            _info(stat.S_IFLNK, 0o777, 1000),
        ),
        lambda value: setattr(value.password, "st_uid", 2000),
        lambda value: setattr(value.password, "st_nlink", 2),
        lambda value: setattr(value.password, "st_size", 0),
        lambda value: setattr(
            value.password,
            "st_size",
            repair.MAX_SECRET_BYTES + 1,
        ),
        lambda value: setattr(
            value,
            "directory",
            _info(stat.S_IFDIR, 0o722, 1000),
        ),
        lambda value: setattr(
            value,
            "configuration",
            _info(stat.S_IFLNK, 0o777, 1000),
        ),
        lambda value: setattr(value, "temporary_exists", True),
    ],
)
def test_unsafe_preflight_is_terminal_value_free_and_has_no_effect(mutate):
    operations = FakeOperations()
    mutate(operations)

    payload = repair.repair_backup_configuration(
        execute=True,
        operations=operations,
    )

    assert payload["status"] == "blocked"
    assert payload["error_code"] == "preflight_failed"
    assert payload["effect_may_have_occurred"] is False
    assert payload["retry_permitted"] is False
    assert operations.written == b""
    assert repair.validate_envelope(payload)


def test_replace_failure_restores_original_password_metadata_and_removes_temp():
    operations = FakeOperations()
    operations.directory.st_mode = stat.S_IFDIR | 0o755
    operations.fail = "replace"

    payload = repair.repair_backup_configuration(
        execute=True,
        operations=operations,
    )

    assert payload["status"] == "rolled_back"
    assert payload["automatic_rollback_attempted"] is True
    assert payload["automatic_rollback_succeeded"] is True
    assert operations.password.st_uid == 0
    assert operations.password.st_gid == 0
    assert stat.S_IMODE(operations.password.st_mode) == 0o640
    assert operations.temporary_exists is False
    assert operations.configuration_replaced is False
    assert stat.S_IMODE(operations.directory.st_mode) == 0o755
    assert repair.validate_envelope(payload)


def test_safe_nonexact_directory_mode_is_hardened_to_private():
    operations = FakeOperations()
    operations.directory.st_mode = stat.S_IFDIR | 0o755

    payload = repair.repair_backup_configuration(
        execute=True,
        operations=operations,
    )

    assert payload["status"] == "succeeded"
    assert payload["directory_metadata_repaired"] is True
    assert stat.S_IMODE(operations.directory.st_mode) == 0o700
    assert repair.validate_envelope(payload)


def test_failed_rollback_is_unknown_and_never_retryable():
    operations = FakeOperations()
    operations.fail = "replace"
    def fail_replace_then_rollback(*_args):
        operations.fail = "rollback"
        raise OSError("synthetic-private-error")

    operations.replace_at = fail_replace_then_rollback
    payload = repair.repair_backup_configuration(
        execute=True,
        operations=operations,
    )

    assert payload["status"] == "unknown"
    assert payload["effect_may_have_occurred"] is True
    assert payload["retry_permitted"] is False
    assert repair.validate_envelope(payload)


def test_failure_after_atomic_replace_is_unknown_not_false_success():
    operations = FakeOperations()
    operations.fail = "post_replace_fsync"

    payload = repair.repair_backup_configuration(
        execute=True,
        operations=operations,
    )

    assert payload["status"] == "unknown"
    assert payload["configuration_replaced"] is True
    assert payload["effect_may_have_occurred"] is True
    assert payload["retry_permitted"] is False
    assert repair.validate_envelope(payload)


def test_disabled_main_and_envelope_are_inert_and_strict(capsys):
    disabled = repair.repair_backup_configuration()
    assert disabled["status"] == "blocked"
    assert disabled["error_code"] == "execution_disabled"
    assert repair.validate_envelope(disabled)

    tampered = dict(disabled)
    tampered["secret"] = "synthetic-private-error"
    tampered["evidence_sha256"] = repair._digest(tampered)
    assert repair.validate_envelope(tampered) is False

    assert repair.main() == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error_code"] == "invalid_invocation"
    assert repair.validate_envelope(output)
