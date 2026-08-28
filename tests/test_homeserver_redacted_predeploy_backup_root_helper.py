from __future__ import annotations

import json
import inspect
import stat
from types import SimpleNamespace

import pytest

from ops.homeserver import redacted_predeploy_backup_root_helper as subject


def test_envelopes_are_fixed_and_redacted() -> None:
    blocked = subject._blocked("not_armed")
    assert subject.validate_envelope(blocked)
    assert blocked["backup_invoked"] is False and blocked["retry_permitted"] is False
    unknown = subject._unknown("backup_failed", "predeploy_backup_root_helper_v1:" + "a" * 64)
    assert subject.validate_envelope(unknown)
    assert "raw" not in json.dumps(unknown).lower()
    malformed = subject._unknown("backup_failed", "predeploy_backup_root_helper_v1:not-a-digest")
    assert not subject.validate_envelope(malformed)


def test_tampering_rejected_and_no_user_namespace_or_descriptor_mount_fallback() -> None:
    value = subject._blocked("not_armed")
    value["backup_invoked"] = True
    value["evidence_sha256"] = subject._digest(value)
    assert not subject.validate_envelope(value)
    source = open(subject.__file__, encoding="utf-8").read()
    assert "CLONE_NEWUSER" not in source
    assert "open_tree" not in source and "move_mount" not in source and "execveat" in source
    assert '"/proc/self/fd/' not in source


def test_nonroot_and_preflight_failure_never_dispatch(monkeypatch) -> None:
    monkeypatch.setattr(subject.os, "geteuid", lambda: 1000, raising=False)
    assert subject.run_root_helper()["status"] == "blocked"
    monkeypatch.setattr(subject.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(subject, "_bind_identities", lambda: (_ for _ in ()).throw(subject.Failure("identity_unavailable")))
    value = subject.run_root_helper(helper_sha256="a" * 64)
    assert value["status"] == "blocked" and value["backup_invoked"] is False


def test_unit_contract_uses_one_fixed_python_exec_and_narrow_capabilities() -> None:
    unit = open("ops/homeserver/root-helper/odysseus-predeploy-backup-root-helper.service", encoding="utf-8").read()
    assert "ExecStart=/usr/bin/python3 -I /usr/local/libexec/odysseus-predeploy-backup-root-helper.py" in unit
    assert "EnvironmentFile" not in unit and "ExecStartPre" not in unit and "bash" not in unit and "sh -c" not in unit
    assert "PrivateNetwork=yes" in unit and "StandardOutput=null" in unit and "StandardError=null" in unit
    assert "CAP_SYS_ADMIN CAP_SETUID CAP_SETGID CAP_CHOWN CAP_DAC_READ_SEARCH" in unit
    assert "StandardInput=null" in unit and "ProtectSystem=strict" in unit


def test_private_mount_view_remains_traversable_after_uid_drop_and_uses_canonical_paths() -> None:
    mount_source = inspect.getsource(subject._mount_setup)
    bind_source = inspect.getsource(subject._bind_private_views)
    execution_source = inspect.getsource(subject._execute_under_lock)
    assert 'b"mode=0711,size=1048576"' in mount_source
    assert "os.fchown(credential_directory_fd, bound.uid, bound.gid)" in mount_source
    assert 'invoke_mount(SOURCE.encode("ascii"), SOURCE, None, MS_BIND, None)' in bind_source
    assert 'invoke_mount(REPOSITORY.encode("ascii"), VIEW_REPOSITORY, None, MS_BIND, None)' in bind_source
    assert "filesystem_stat(SOURCE)" in bind_source and "filesystem_stat(VIEW_REPOSITORY)" in bind_source
    assert "os.mkdir(VIEW_REPOSITORY, 0o700)" in mount_source
    assert "VIEW_SOURCE" not in mount_source + execution_source
    assert '(RESTIC_BINARY, "-r", VIEW_REPOSITORY, "backup", SOURCE' in execution_source
    assert '(RESTIC_BINARY, "-r", VIEW_REPOSITORY, "--no-lock", "snapshots"' in execution_source


def _directory(device: int, inode: int, mode: int, uid: int, gid: int) -> SimpleNamespace:
    return SimpleNamespace(st_mode=stat.S_IFDIR | mode, st_uid=uid, st_gid=gid, st_nlink=2, st_dev=device, st_ino=inode)


def _bound_fixture() -> tuple[subject.Bound, dict[int, SimpleNamespace], dict[str, SimpleNamespace]]:
    bound = subject.Bound(10, 11, 12, 13, 14, 1000, 1000, "")
    descriptors = {
        10: _directory(1, 1, 0o755, 0, 0),
        11: _directory(2, 2, 0o750, 1000, 1000),
        12: _directory(3, 3, 0o700, 1000, 1000),
    }
    paths = {
        "/opt": descriptors[10],
        subject.SOURCE: descriptors[11],
        subject.REPOSITORY: descriptors[12],
        subject.VIEW_REPOSITORY: descriptors[12],
    }
    return bound, descriptors, paths


def test_classic_bind_sequence_proves_descriptor_identity_source_readonly_and_repository_writable() -> None:
    bound, descriptors, paths = _bound_fixture()
    mounts: list[tuple[bytes | None, str, bytes | None, int, bytes | None]] = []
    nested_checks: list[tuple[str, ...]] = []
    subject._bind_private_views(
        bound,
        lambda *args: mounts.append(args),
        statter=paths.__getitem__,
        fstatter=descriptors.__getitem__,
        statvfs=lambda path: SimpleNamespace(f_flag=1 if path == subject.SOURCE else 0),
        reject_nested=lambda *roots: nested_checks.append(roots),
    )
    assert mounts == [
        (subject.SOURCE.encode("ascii"), subject.SOURCE, None, subject.MS_BIND, None),
        (None, subject.SOURCE, None, subject.MS_BIND | subject.MS_REMOUNT | subject.MS_RDONLY | subject.MS_NOSUID | subject.MS_NODEV | subject.MS_NOEXEC, None),
        (subject.REPOSITORY.encode("ascii"), subject.VIEW_REPOSITORY, None, subject.MS_BIND, None),
    ]
    assert nested_checks == [
        (subject.SOURCE, subject.REPOSITORY),
        (subject.SOURCE, subject.REPOSITORY, subject.VIEW_REPOSITORY),
    ]


def test_unsafe_opt_parent_and_source_or_repository_path_swaps_fail_before_mount() -> None:
    for unsafe in ("parent", "source", "repository"):
        bound, descriptors, paths = _bound_fixture()
        if unsafe == "parent":
            paths["/opt"] = _directory(1, 1, 0o775, 0, 0)
        elif unsafe == "source":
            paths[subject.SOURCE] = _directory(9, 9, 0o750, 1000, 1000)
        else:
            paths[subject.REPOSITORY] = _directory(9, 9, 0o700, 1000, 1000)
        mounts: list[object] = []
        with pytest.raises(subject.Failure):
            subject._bind_private_views(
                bound,
                lambda *args: mounts.append(args),
                statter=paths.__getitem__,
                fstatter=descriptors.__getitem__,
                statvfs=lambda path: SimpleNamespace(f_flag=0),
                reject_nested=lambda *roots: None,
            )
        assert mounts == []


def test_nested_mounts_below_either_bound_root_are_rejected_but_exact_roots_are_allowed() -> None:
    exact = (
        f"1 0 0:1 / {subject.SOURCE} rw - ext4 /dev/a rw\n"
        f"2 0 0:2 / {subject.REPOSITORY} rw - ext4 /dev/b rw\n"
        f"3 0 0:3 / {subject.VIEW_REPOSITORY} rw - ext4 /dev/c rw\n"
    ).encode("ascii")
    subject._reject_nested_mounts(subject.SOURCE, subject.REPOSITORY, subject.VIEW_REPOSITORY, reader=lambda path, maximum: exact)
    for nested in (subject.SOURCE + "/nested", subject.REPOSITORY + "/nested", subject.VIEW_REPOSITORY + "/nested"):
        raw = exact + f"4 0 0:4 / {nested} rw - ext4 /dev/d rw\n".encode("ascii")
        with pytest.raises(subject.Failure):
            subject._reject_nested_mounts(subject.SOURCE, subject.REPOSITORY, subject.VIEW_REPOSITORY, reader=lambda path, maximum, raw=raw: raw)


def test_every_classic_bind_or_remount_failure_and_postcondition_failure_is_closed() -> None:
    for failing_call in range(3):
        bound, descriptors, paths = _bound_fixture()
        calls = {"count": 0}
        def mount(*args):
            current = calls["count"]
            calls["count"] += 1
            if current == failing_call: raise subject.Failure("preflight_failed")
        with pytest.raises(subject.Failure):
            subject._bind_private_views(bound, mount, statter=paths.__getitem__, fstatter=descriptors.__getitem__, statvfs=lambda path: SimpleNamespace(f_flag=1 if path == subject.SOURCE else 0), reject_nested=lambda *roots: None)

    bound, descriptors, paths = _bound_fixture()
    with pytest.raises(subject.Failure):
        subject._bind_private_views(bound, lambda *args: None, statter=paths.__getitem__, fstatter=descriptors.__getitem__, statvfs=lambda path: SimpleNamespace(f_flag=0), reject_nested=lambda *roots: None)
    with pytest.raises(subject.Failure):
        subject._bind_private_views(bound, lambda *args: None, statter=paths.__getitem__, fstatter=descriptors.__getitem__, statvfs=lambda path: SimpleNamespace(f_flag=1), reject_nested=lambda *roots: None)


def test_credential_directory_self_bind_precedes_readonly_remount_and_both_fail_closed() -> None:
    credential_directory = subject.os.path.dirname(subject.VIEW_CREDENTIAL)
    mounts: list[tuple[bytes | None, str, bytes | None, int, bytes | None]] = []
    subject._make_credential_directory_readonly(
        credential_directory,
        lambda *args: mounts.append(args),
        statvfs=lambda path: SimpleNamespace(f_flag=1),
    )
    assert mounts == [
        (credential_directory.encode("ascii"), credential_directory, None, subject.MS_BIND, None),
        (None, credential_directory, None, subject.MS_BIND | subject.MS_REMOUNT | subject.MS_RDONLY | subject.MS_NOSUID | subject.MS_NODEV | subject.MS_NOEXEC, None),
    ]

    for failing_call in range(2):
        calls: list[tuple[object, ...]] = []
        def fail_one(*args):
            calls.append(args)
            if len(calls) - 1 == failing_call: raise subject.Failure("preflight_failed")
        with pytest.raises(subject.Failure):
            subject._make_credential_directory_readonly(credential_directory, fail_one, statvfs=lambda path: SimpleNamespace(f_flag=1))
        assert len(calls) == failing_call + 1

    with pytest.raises(subject.Failure):
        subject._make_credential_directory_readonly(credential_directory, lambda *args: None, statvfs=lambda path: SimpleNamespace(f_flag=0))


def test_post_bind_source_or_repository_identity_swap_fails_closed() -> None:
    bound, descriptors, paths = _bound_fixture()
    source_calls = {"count": 0}
    def swapped_source(path):
        if path == subject.SOURCE:
            source_calls["count"] += 1
            if source_calls["count"] > 1: return _directory(9, 9, 0o750, 1000, 1000)
        return paths[path]
    with pytest.raises(subject.Failure):
        subject._bind_private_views(bound, lambda *args: None, statter=swapped_source, fstatter=descriptors.__getitem__, statvfs=lambda path: SimpleNamespace(f_flag=1 if path == subject.SOURCE else 0), reject_nested=lambda *roots: None)

    paths[subject.VIEW_REPOSITORY] = _directory(9, 9, 0o700, 1000, 1000)
    with pytest.raises(subject.Failure):
        subject._bind_private_views(bound, lambda *args: None, statter=paths.__getitem__, fstatter=descriptors.__getitem__, statvfs=lambda path: SimpleNamespace(f_flag=1 if path == subject.SOURCE else 0), reject_nested=lambda *roots: None)


def test_snapshot_parser_rejects_stale_or_unbound_content() -> None:
    assert subject._parse_snapshot(b"not-json", 0.0) is None
    assert subject._parse_snapshot(b"[]", 0.0) is None
    assert subject.MAX_ARM_FUTURE_SECONDS == 600


def test_mount_proof_requires_one_fixed_nonroot_mount_row(monkeypatch) -> None:
    good_fdinfo = b"pos:\t0\nmnt_id:\t42\n"
    good_mountinfo = b"42 1 0:1 / /mnt/backup rw - ext4 /dev/x rw\n"
    monkeypatch.setattr(subject, "_read_proc_bounded", lambda path, maximum: good_fdinfo if "fdinfo" in path else good_mountinfo)
    subject._prove_fixed_mount(7)
    duplicate = good_mountinfo + b"42 1 0:1 / /mnt/backup rw - ext4 /dev/x rw\n"
    monkeypatch.setattr(subject, "_read_proc_bounded", lambda path, maximum: good_fdinfo if "fdinfo" in path else duplicate)
    try:
        subject._prove_fixed_mount(7)
    except subject.Failure as failure:
        assert failure.code == "identity_unavailable"
    else:  # pragma: no cover - documents the fail-closed requirement
        raise AssertionError("duplicate mount identity was accepted")


def test_reusable_view_anchor_is_nofollow_owned_and_safe_for_two_runs(monkeypatch) -> None:
    opened: list[int] = []
    opener = lambda *args, **kwargs: opened.append(91) or 91
    good = lambda fd: SimpleNamespace(st_mode=0o40700, st_uid=0, st_nlink=2)
    subject._open_reusable_view_anchor(opener=opener, statter=good, closer=lambda fd: None)
    subject._open_reusable_view_anchor(opener=opener, statter=good, closer=lambda fd: None)
    assert opened == [91, 91]
    try:
        subject._open_reusable_view_anchor(opener=opener, statter=lambda fd: SimpleNamespace(st_mode=0o40755, st_uid=0, st_nlink=2), closer=lambda fd: None)
    except subject.Failure as failure:
        assert failure.code == "preflight_failed"
    else:
        raise AssertionError("unsafe reusable anchor was accepted")


def test_credential_buffer_is_overwritten_on_seal_failure(monkeypatch) -> None:
    credential = bytearray(b"not-a-secret-fixture")
    monkeypatch.setattr(subject.os, "memfd_create", lambda *args: (_ for _ in ()).throw(OSError()), raising=False)
    try:
        subject._seal_credential(credential)
    except subject.Failure:
        pass
    else:
        raise AssertionError("seal failure was accepted")
    assert credential == bytearray(len(credential))


def test_credential_reader_uses_preallocated_readv_and_wipes_eof_probe(monkeypatch) -> None:
    payload = bytearray(b"fixture-credential")
    cursor = {"offset": 0}
    def readv(fd, buffers):
        target = buffers[0]
        remaining = len(payload) - cursor["offset"]
        count = min(len(target), remaining)
        if count:
            target[:count] = payload[cursor["offset"]:cursor["offset"] + count]
            cursor["offset"] += count
        return count
    monkeypatch.setattr(subject.os, "readv", readv, raising=False)
    assert subject._read_exact(9, len(payload)) == payload
    assert "os.read(" not in inspect.getsource(subject._read_exact)


def test_shared_memfd_is_rewound_for_each_child_copy_and_seek_failure_blocks(monkeypatch) -> None:
    seeks: list[tuple[int, int]] = []
    monkeypatch.setattr(subject.os, "lseek", lambda fd, offset, whence: seeks.append((fd, offset)) or 0)
    monkeypatch.setattr(subject, "_read_exact", lambda fd, size: bytearray(b"full"))
    assert subject._read_credential_from_start(31, 4) == bytearray(b"full")
    assert subject._read_credential_from_start(31, 4) == bytearray(b"full")
    assert seeks == [(31, 0), (31, 0)]
    monkeypatch.setattr(subject.os, "lseek", lambda *args: (_ for _ in ()).throw(OSError()))
    try: subject._read_credential_from_start(31, 4)
    except subject.Failure as failure: assert failure.code == "preflight_failed"
    else: raise AssertionError("shared offset failure was accepted")


def test_lock_remains_held_until_public_receipt_is_durable(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(subject.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(subject, "_acquire_run_lock", lambda: 77)
    monkeypatch.setattr(subject, "_execute_under_lock", lambda digest, now: subject._blocked("not_armed"))
    monkeypatch.setattr(subject, "_write_public_receipt", lambda value: events.append("receipt") or True)
    monkeypatch.setattr(subject.os, "close", lambda fd: events.append("release") if fd == 77 else None)
    assert subject.run_root_helper(helper_sha256="a" * 64, publish=True)["status"] == "blocked"
    assert events == ["receipt", "release"]


def test_current_attempt_invalidates_old_receipt_before_identity_binding(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(subject, "_consume_arm", lambda now, digest: "predeploy_backup_root_helper_v1:" + "a" * 64)
    monkeypatch.setattr(subject, "_invalidate_public_receipt", lambda: events.append("invalidate"))
    monkeypatch.setattr(subject, "_bind_identities", lambda: events.append("bind") or (_ for _ in ()).throw(subject.Failure("identity_unavailable")))
    value = subject._execute_under_lock("a" * 64, lambda: 1.0)
    assert events == ["invalidate", "bind"]
    assert value["status"] == "unknown" and value["error_code"] == "execution_ambiguous"


def test_arm_validation_is_exact_expiring_and_distinct_per_grant() -> None:
    digest, now = "b" * 64, 1000.0
    def arm(grant: str, expiry: int) -> dict[str, object]:
        return {"schema_id": subject.ARM_SCHEMA_ID, "grant_id": grant, "expires_at_epoch": expiry, "helper_sha256": digest}
    assert subject._validate_arm_record(arm("a" * 64, 1001), now, digest) == "a" * 64
    for expiry in (1000, 1601):
        try:
            subject._validate_arm_record(arm("a" * 64, expiry), now, digest)
        except subject.Failure as failure:
            assert failure.code == "arm_expired"
        else:
            raise AssertionError("expired or overlong arm was accepted")
    assert subject._arm_reference("a" * 64, digest) != subject._arm_reference("c" * 64, digest)
    for code in ("arm_replayed", "arm_contended"):
        assert subject.validate_envelope(subject._blocked(code))


def test_repository_mutability_and_all_capability_sets_are_required() -> None:
    assert subject._filesystem_readonly(SimpleNamespace(f_flag=1))
    assert not subject._filesystem_readonly(SimpleNamespace(f_flag=0))
    assert subject._safe_root_parent(_directory(1, 1, 0o755, 0, 0))
    assert not subject._safe_root_parent(_directory(1, 1, 0o775, 0, 0))
    assert subject._safe_bound_root(_directory(1, 1, 0o700, 1000, 1000), 1000, 1000, writable=True)
    assert not subject._safe_bound_root(_directory(1, 1, 0o500, 1000, 1000), 1000, 1000, writable=True)
    access_calls: list[tuple[str, int]] = []
    subject._prove_repository_access_after_drop(accessor=lambda path, mode: access_calls.append((path, mode)) or True)
    assert access_calls == [(subject.VIEW_REPOSITORY, subject.os.R_OK | subject.os.W_OK | subject.os.X_OK)]
    with pytest.raises(subject.Failure):
        subject._prove_repository_access_after_drop(accessor=lambda path, mode: False)
    clear = [SimpleNamespace(effective=0, permitted=0, inheritable=0), SimpleNamespace(effective=0, permitted=0, inheritable=0)]
    assert subject._capability_sets_clear(clear)
    for field in ("effective", "permitted", "inheritable"):
        words = [SimpleNamespace(effective=0, permitted=0, inheritable=0)]
        setattr(words[0], field, 1)
        assert not subject._capability_sets_clear(words)


def test_descriptor_close_precedes_rebinding_and_cannot_reuse_config_fd() -> None:
    closed: list[int] = []
    assert subject._close_owned_descriptor(41, closer=closed.append) is None
    assert closed == [41]
    # The returned sentinel makes accidental reuse as a dir_fd impossible.
    assert subject._close_owned_descriptor(None, closer=closed.append) is None
    child_source = inspect.getsource(subject._run_child)
    close = "for descriptor in (bound.source_parent_fd, bound.source_fd, bound.repository_fd, bound.credential_fd): _close_verified(descriptor)"
    assert child_source.index(close) < child_source.index("_drop_identity(bound)") < child_source.index("_prove_repository_access_after_drop()") < child_source.index("_execveat_syscall()")
