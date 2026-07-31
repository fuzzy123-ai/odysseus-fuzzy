from __future__ import annotations

import json
import os
import stat
from types import SimpleNamespace

import pytest

from ops.homeserver import redacted_backup_credential_migration as migration


SOURCE_PATH = "/srv/private/restic-source"
SECRET = b"synthetic-credential\n"


class Node:
    def __init__(
        self,
        kind: int,
        mode: int,
        uid: int,
        gid: int,
        inode: int,
        content: bytes = b"",
        nlink: int = 1,
    ) -> None:
        self.kind = kind
        self.mode = mode
        self.uid = uid
        self.gid = gid
        self.inode = inode
        self.content = content
        self.nlink = nlink
        self.mtime_ns = inode * 10
        self.ctime_ns = inode * 10 + 1

    def info(self):
        return SimpleNamespace(
            st_dev=7,
            st_ino=self.inode,
            st_mode=self.kind | self.mode,
            st_uid=self.uid,
            st_gid=self.gid,
            st_nlink=self.nlink,
            st_size=len(self.content),
            st_mtime_ns=self.mtime_ns,
            st_ctime_ns=self.ctime_ns,
        )


class FakeOperations:
    source_fd = 10
    directory_fd = 11

    def __init__(self, *, existing: bool = True) -> None:
        self.directory = Node(stat.S_IFDIR, 0o755, 1000, 1000, 1)
        self.source = Node(stat.S_IFREG, 0o600, 0, 0, 2, SECRET)
        self.entries: dict[str, Node] = {}
        if existing:
            self.entries[migration.DESTINATION_NAME] = Node(
                stat.S_IFREG, 0o640, 0, 0, 3, b"old-password"
            )
            self.entries[migration.CONFIGURATION_NAME] = Node(
                stat.S_IFREG, 0o600, 1000, 1000, 4, b"old-config\n"
            )
        self.descriptors: dict[int, Node] = {
            self.source_fd: self.source,
            self.directory_fd: self.directory,
        }
        self.offsets: dict[int, int] = {}
        self.next_fd = 20
        self.next_inode = 100
        self.closed: list[int] = []
        self.calls: list[tuple] = []
        self.read_chunk: int | None = None
        self.write_chunk: int | None = None
        self.zero_write_for: str | None = None
        self.fail_create_name: str | None = None
        self.fail_replace_before: tuple[str, str] | None = None
        self.fail_replace_after: tuple[str, str] | None = None
        self.fail_unlink_name: str | None = None
        self.restic_returncode: object = 0
        self.restic_call: tuple[tuple[str, ...], dict[str, str]] | None = None
        self.restic_mutation = None
        self.race_target: tuple[str, str] | None = None

    def owner(self):
        return 1000, 1000

    def open_source(self, path, flags):
        self.calls.append(("open_source", path, flags))
        self.offsets[self.source_fd] = 0
        return self.source_fd

    def open_directory(self, flags):
        self.calls.append(("open_directory", flags))
        return self.directory_fd

    def stat_fd(self, descriptor):
        return self.descriptors[descriptor].info()

    def stat_at(self, _directory_fd, name):
        try:
            return self.entries[name].info()
        except KeyError:
            raise FileNotFoundError from None

    def open_at(self, _directory_fd, name, flags, mode=0o600):
        self.calls.append(("open_at", name, flags, mode))
        if flags & os.O_CREAT:
            if name == self.fail_create_name:
                raise OSError("synthetic-private-create-error")
            if name in self.entries:
                raise FileExistsError
            node = Node(
                stat.S_IFREG,
                mode,
                1000,
                1000,
                self.next_inode,
            )
            self.next_inode += 1
            self.entries[name] = node
        else:
            if name not in self.entries:
                raise FileNotFoundError
            node = self.entries[name]
        descriptor = self.next_fd
        self.next_fd += 1
        self.descriptors[descriptor] = node
        self.offsets[descriptor] = 0
        return descriptor

    def read(self, descriptor, maximum):
        node = self.descriptors[descriptor]
        offset = self.offsets.get(descriptor, 0)
        amount = maximum
        if self.read_chunk is not None:
            amount = min(amount, self.read_chunk)
        value = node.content[offset : offset + amount]
        self.offsets[descriptor] = offset + len(value)
        return value

    def write(self, descriptor, value):
        node = self.descriptors[descriptor]
        entry_name = next(
            (name for name, candidate in self.entries.items() if candidate is node),
            None,
        )
        if entry_name == self.zero_write_for:
            return 0
        amount = len(value)
        if self.write_chunk is not None:
            amount = min(amount, self.write_chunk)
        node.content += value[:amount]
        return amount

    def fchown(self, descriptor, uid, gid):
        node = self.descriptors[descriptor]
        node.uid = uid
        node.gid = gid

    def fchmod(self, descriptor, mode):
        self.descriptors[descriptor].mode = mode

    def fsync(self, descriptor):
        self.calls.append(("fsync", descriptor))

    def supports_noreplace(self):
        return True

    def rename_noreplace_at(self, _directory_fd, source, target):
        pair = (source, target)
        self.calls.append(("replace", source, target))
        if pair == self.fail_replace_before:
            raise OSError("synthetic-private-replace-error")
        if pair == self.race_target:
            self.entries[target] = Node(
                stat.S_IFREG,
                0o600,
                1000,
                1000,
                999,
                b"foreign-race-entry",
            )
        if target in self.entries:
            raise FileExistsError
        moved = self.entries.pop(source)
        moved.ctime_ns += 1
        self.entries[target] = moved
        if pair == self.fail_replace_after:
            raise OSError("synthetic-private-post-effect-error")

    def unlink_at(self, _directory_fd, name):
        self.calls.append(("unlink", name))
        if name == self.fail_unlink_name:
            raise OSError("synthetic-private-unlink-error")
        del self.entries[name]

    def run_restic(self, command, environment):
        self.restic_call = (tuple(command), dict(environment))
        if self.restic_mutation is not None:
            self.restic_mutation(self)
        return self.restic_returncode

    def close(self, descriptor):
        self.closed.append(descriptor)


def _environment(**extra):
    return {"RESTIC_PASSWORD_FILE": SOURCE_PATH, **extra}


def _entry_state(operations: FakeOperations, name: str):
    node = operations.entries.get(name)
    if node is None:
        return None
    return (
        node.inode,
        node.kind,
        node.mode,
        node.uid,
        node.gid,
        node.nlink,
        node.content,
    )


def _original_state(operations: FakeOperations):
    return (
        operations.directory.mode,
        _entry_state(operations, migration.DESTINATION_NAME),
        _entry_state(operations, migration.CONFIGURATION_NAME),
    )


def _assert_redacted(payload):
    encoded = json.dumps(payload, sort_keys=True)
    assert SOURCE_PATH not in encoded
    assert SECRET.decode().strip() not in encoded
    assert migration.DESTINATION_PATH not in encoded
    assert migration.DESTINATION_NAME not in encoded
    assert "synthetic-private" not in encoded
    assert all(payload[key] is False for key in migration._VISIBILITY)
    assert payload["retry_permitted"] is False
    assert migration.validate_envelope(payload)


def test_success_uses_fd_bound_atomic_install_and_fixed_redacted_readback():
    operations = FakeOperations()
    old_password = operations.entries[migration.DESTINATION_NAME]
    old_configuration = operations.entries[migration.CONFIGURATION_NAME]

    payload = migration.migrate_backup_credential(
        execute=True,
        environment=_environment(UNRELATED="allowed"),
        operations=operations,
    )

    assert payload["status"] == "succeeded"
    assert payload["credential_installed"] is True
    assert payload["configuration_installed"] is True
    assert payload["post_change_readback_succeeded"] is True
    assert operations.directory.mode == 0o700
    destination = operations.entries[migration.DESTINATION_NAME]
    configuration = operations.entries[migration.CONFIGURATION_NAME]
    assert destination.content == SECRET
    assert destination.uid == destination.gid == 1000
    assert destination.mode == 0o600
    assert configuration.content == migration.CONFIGURATION_BYTES
    assert configuration.mode == 0o600
    assert operations.entries[migration.PASSWORD_ROLLBACK_NAME] is old_password
    assert (
        operations.entries[migration.CONFIGURATION_ROLLBACK_NAME]
        is old_configuration
    )
    assert migration.PASSWORD_TEMPORARY_NAME not in operations.entries
    assert migration.CONFIGURATION_TEMPORARY_NAME not in operations.entries
    assert operations.restic_call == (
        migration.RESTIC_COMMAND,
        migration.RESTIC_ENVIRONMENT,
    )
    assert operations.calls[0] == (
        "open_source",
        SOURCE_PATH,
        os.O_RDONLY | migration._O_NOFOLLOW | migration._O_CLOEXEC,
    )
    assert operations.calls[1] == (
        "open_directory",
        os.O_RDONLY
        | migration._O_DIRECTORY
        | migration._O_NOFOLLOW
        | migration._O_CLOEXEC,
    )
    _assert_redacted(payload)


def test_partial_reads_and_writes_are_completed_without_exposure():
    operations = FakeOperations(existing=False)
    operations.read_chunk = 2
    operations.write_chunk = 3

    payload = migration.migrate_backup_credential(
        execute=True,
        environment=_environment(),
        operations=operations,
    )

    assert payload["status"] == "succeeded"
    assert operations.entries[migration.DESTINATION_NAME].content == SECRET
    assert operations.entries[migration.CONFIGURATION_NAME].content == migration.CONFIGURATION_BYTES
    _assert_redacted(payload)


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"RESTIC_PASSWORD_FILE": SOURCE_PATH, "RESTIC_PASSWORD": ""},
        {"RESTIC_PASSWORD_FILE": SOURCE_PATH, "RESTIC_PASSWORD_COMMAND": ""},
        {"RESTIC_PASSWORD_FILE": "relative"},
        {"RESTIC_PASSWORD_FILE": "/private/../source"},
        {"RESTIC_PASSWORD_FILE": "//private/source"},
        {"RESTIC_PASSWORD_FILE": "/private/source/"},
        {"RESTIC_PASSWORD_FILE": "/private\x00source"},
        {"RESTIC_PASSWORD_FILE": 7},
        {"RESTIC_PASSWORD_FILE": "/" + "x" * migration.MAX_PATH_BYTES},
        {"RESTIC_PASSWORD_FILE": "/private/\ud800"},
    ],
)
def test_invalid_or_ambiguous_environment_is_terminal_before_operations(environment):
    operations = FakeOperations()
    payload = migration.migrate_backup_credential(
        execute=True,
        environment=environment,
        operations=operations,
    )
    assert payload["status"] == "blocked"
    assert payload["error_code"] == "invalid_environment"
    assert operations.calls == []
    _assert_redacted(payload)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: setattr(value.source, "kind", stat.S_IFLNK),
        lambda value: setattr(value.source, "kind", stat.S_IFDIR),
        lambda value: setattr(value.source, "nlink", 2),
        lambda value: setattr(value.source, "content", b""),
        lambda value: setattr(
            value.source, "content", b"x" * (migration.MAX_SECRET_BYTES + 1)
        ),
        lambda value: setattr(value.source, "uid", 2000),
        lambda value: setattr(value.source, "mode", 0o620),
        lambda value: setattr(value.directory, "kind", stat.S_IFREG),
        lambda value: setattr(value.directory, "uid", 0),
        lambda value: setattr(value.directory, "mode", 0o720),
        lambda value: setattr(value.directory, "mode", 0o2750),
    ],
)
def test_unsafe_source_or_directory_is_blocked_without_mutation(mutate):
    operations = FakeOperations()
    before = _original_state(operations)
    mutate(operations)

    payload = migration.migrate_backup_credential(
        execute=True,
        environment=_environment(),
        operations=operations,
    )

    assert payload["status"] == "blocked"
    assert payload["error_code"] == "preflight_failed"
    assert all("replace" not in call for call in operations.calls)
    assert _entry_state(operations, migration.PASSWORD_TEMPORARY_NAME) is None
    assert _entry_state(operations, migration.CONFIGURATION_TEMPORARY_NAME) is None
    _assert_redacted(payload)


@pytest.mark.parametrize(
    "name",
    [
        migration.PASSWORD_TEMPORARY_NAME,
        migration.CONFIGURATION_TEMPORARY_NAME,
        migration.PASSWORD_ROLLBACK_NAME,
        migration.CONFIGURATION_ROLLBACK_NAME,
    ],
)
def test_any_leftover_work_entry_blocks_before_mutation(name):
    operations = FakeOperations()
    operations.entries[name] = Node(stat.S_IFREG, 0o600, 1000, 1000, 80)
    before = _original_state(operations)

    payload = migration.migrate_backup_credential(
        execute=True,
        environment=_environment(),
        operations=operations,
    )

    assert payload["status"] == "blocked"
    assert _original_state(operations) == before
    assert operations.entries[name].inode == 80
    _assert_redacted(payload)


@pytest.mark.parametrize(
    "name",
    [migration.DESTINATION_NAME, migration.CONFIGURATION_NAME],
)
def test_directory_destination_is_rejected_not_replaced(name):
    operations = FakeOperations()
    operations.entries[name] = Node(stat.S_IFDIR, 0o700, 1000, 1000, 81)
    before = _original_state(operations)

    payload = migration.migrate_backup_credential(
        execute=True,
        environment=_environment(),
        operations=operations,
    )

    assert payload["status"] == "blocked"
    assert _original_state(operations) == before
    _assert_redacted(payload)


@pytest.mark.parametrize(
    "configure",
    [
        lambda value: setattr(
            value, "zero_write_for", migration.PASSWORD_TEMPORARY_NAME
        ),
        lambda value: setattr(
            value, "fail_create_name", migration.CONFIGURATION_TEMPORARY_NAME
        ),
        lambda value: setattr(
            value,
            "fail_replace_before",
            (migration.CONFIGURATION_TEMPORARY_NAME, migration.CONFIGURATION_NAME),
        ),
        lambda value: setattr(value, "restic_returncode", 1),
        lambda value: setattr(value, "restic_returncode", True),
    ],
)
def test_every_post_mutation_failure_rolls_back_and_verifies_original_state(configure):
    operations = FakeOperations()
    before = _original_state(operations)
    configure(operations)

    payload = migration.migrate_backup_credential(
        execute=True,
        environment=_environment(),
        operations=operations,
    )

    assert payload["status"] == "rolled_back"
    assert payload["automatic_rollback_attempted"] is True
    assert payload["automatic_rollback_succeeded"] is True
    assert _original_state(operations) == before
    for name in (
        migration.PASSWORD_TEMPORARY_NAME,
        migration.CONFIGURATION_TEMPORARY_NAME,
        migration.PASSWORD_ROLLBACK_NAME,
        migration.CONFIGURATION_ROLLBACK_NAME,
    ):
        assert name not in operations.entries
    _assert_redacted(payload)


def test_exception_after_atomic_install_is_reconciled_and_rolled_back():
    operations = FakeOperations()
    before = _original_state(operations)
    operations.fail_replace_after = (
        migration.CONFIGURATION_TEMPORARY_NAME,
        migration.CONFIGURATION_NAME,
    )

    payload = migration.migrate_backup_credential(
        execute=True,
        environment=_environment(),
        operations=operations,
    )

    assert payload["status"] == "rolled_back"
    assert _original_state(operations) == before
    _assert_redacted(payload)


def test_concurrent_rollback_entry_is_never_overwritten_and_yields_unknown():
    operations = FakeOperations()
    operations.race_target = (
        migration.DESTINATION_NAME,
        migration.PASSWORD_ROLLBACK_NAME,
    )

    payload = migration.migrate_backup_credential(
        execute=True,
        environment=_environment(),
        operations=operations,
    )

    assert payload["status"] == "unknown"
    assert operations.entries[migration.PASSWORD_ROLLBACK_NAME].inode == 999
    assert operations.entries[migration.PASSWORD_ROLLBACK_NAME].content == b"foreign-race-entry"
    _assert_redacted(payload)


@pytest.mark.parametrize(
    "target",
    [migration.DESTINATION_NAME, migration.CONFIGURATION_NAME],
)
def test_post_readback_target_replacement_cannot_return_success(target):
    operations = FakeOperations()

    def replace_after_readback(value):
        value.entries[target] = Node(
            stat.S_IFREG,
            0o600,
            1000,
            1000,
            998,
            b"post-readback-race",
        )

    operations.restic_mutation = replace_after_readback
    payload = migration.migrate_backup_credential(
        execute=True,
        environment=_environment(),
        operations=operations,
    )

    assert payload["status"] == "unknown"
    assert payload["post_change_readback_succeeded"] is False
    _assert_redacted(payload)


def test_unverifiable_rollback_is_unknown_and_never_retryable():
    operations = FakeOperations()
    operations.restic_returncode = 1
    operations.fail_replace_before = (
        migration.PASSWORD_ROLLBACK_NAME,
        migration.DESTINATION_NAME,
    )

    payload = migration.migrate_backup_credential(
        execute=True,
        environment=_environment(),
        operations=operations,
    )

    assert payload["status"] == "unknown"
    assert payload["error_code"] == "mutation_ambiguous"
    assert payload["automatic_rollback_attempted"] is True
    assert payload["automatic_rollback_succeeded"] is False
    assert payload["retry_permitted"] is False
    _assert_redacted(payload)


@pytest.mark.parametrize(
    "rollback_name",
    [
        migration.PASSWORD_ROLLBACK_NAME,
        migration.CONFIGURATION_ROLLBACK_NAME,
    ],
)
def test_same_size_preserved_content_change_cannot_claim_rollback_success(
    rollback_name,
):
    operations = FakeOperations()
    operations.restic_returncode = 1

    def mutate_preserved_entry(value):
        node = value.entries[rollback_name]
        node.content = b"x" * len(node.content)
        node.mtime_ns += 1
        node.ctime_ns += 1

    operations.restic_mutation = mutate_preserved_entry
    payload = migration.migrate_backup_credential(
        execute=True,
        environment=_environment(),
        operations=operations,
    )

    assert payload["status"] == "unknown"
    assert payload["automatic_rollback_attempted"] is True
    assert payload["automatic_rollback_succeeded"] is False
    _assert_redacted(payload)


def test_production_restic_adapter_discards_all_subprocess_streams(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(migration.subprocess, "run", fake_run)
    operations = migration._ProductionOperations()
    result = operations.run_restic(
        migration.RESTIC_COMMAND,
        migration.RESTIC_ENVIRONMENT,
    )

    assert result == 0
    assert captured == {
        "command": migration.RESTIC_COMMAND,
        "check": False,
        "close_fds": True,
        "env": migration.RESTIC_ENVIRONMENT,
        "stdin": migration.subprocess.DEVNULL,
        "stdout": migration.subprocess.DEVNULL,
        "stderr": migration.subprocess.DEVNULL,
        "timeout": 20,
    }


def test_same_size_source_change_during_read_is_rejected_before_mutation():
    operations = FakeOperations()
    original_stat_fd = operations.stat_fd
    source_stats = 0

    def changing_stat_fd(descriptor):
        nonlocal source_stats
        if descriptor == operations.source_fd:
            source_stats += 1
            if source_stats == 2:
                operations.source.mtime_ns += 1
        return original_stat_fd(descriptor)

    operations.stat_fd = changing_stat_fd
    payload = migration.migrate_backup_credential(
        execute=True,
        environment=_environment(),
        operations=operations,
    )

    assert payload["status"] == "blocked"
    assert payload["error_code"] == "preflight_failed"
    assert operations.directory.mode == 0o755
    assert migration.PASSWORD_TEMPORARY_NAME not in operations.entries
    _assert_redacted(payload)


def test_transport_unknown_without_claimed_rollback_is_strictly_valid():
    payload = migration.envelope(
        "unknown",
        "mutation_ambiguous",
        effect=True,
    )
    assert payload["automatic_rollback_attempted"] is False
    _assert_redacted(payload)


def test_disabled_main_and_envelope_validation_are_inert_strict_and_redacted(capsys):
    disabled = migration.migrate_backup_credential()
    assert disabled["status"] == "blocked"
    assert disabled["error_code"] == "execution_disabled"
    _assert_redacted(disabled)

    for mutate in (
        lambda value: value.update(extra="private"),
        lambda value: value.update(effect_may_have_occurred=1),
        lambda value: value.update(paths_visible=True),
        lambda value: value.update(status="succeeded"),
        lambda value: value.update(evidence_sha256="0" * 64),
    ):
        tampered = dict(disabled)
        mutate(tampered)
        if set(tampered) == migration._KEYS and tampered["evidence_sha256"] == disabled["evidence_sha256"]:
            tampered["evidence_sha256"] = migration._digest(tampered)
        assert migration.validate_envelope(tampered) is False

    assert migration.main() == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error_code"] == "invalid_invocation"
    _assert_redacted(output)
