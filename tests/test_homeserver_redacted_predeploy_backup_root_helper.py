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


def test_ok_envelope_rejects_recomputed_noncanonical_reference_and_boolean_age() -> None:
    value = {"schema_id": subject.SCHEMA_ID, "status": "ok", "repository_identity": "restic_homeserver_backup_v1", "protected_source_identity": "odysseus_protected_source_v1", "backup_effect": "created", "action_provenance_ref": "predeploy_backup_root_helper_v1:" + "a" * 64, "snapshot_id": "b" * 64, "source_included": True, "snapshot_created_after_start": True, "snapshot_age_seconds": 0, "snapshot_fresh": True, "concurrent_lock_held": True, "partial_snapshot_detected": False, "raw_stdout_visible": False, "raw_stderr_visible": False, "exception_text_visible": False, "environment_visible": False, "file_contents_visible": False, "paths_visible": False, "hostnames_visible": False, "secret_values_visible": False}
    value["evidence_sha256"] = subject._digest(value)
    assert subject.validate_envelope(value)
    for key, bad in (("action_provenance_ref", "none"), ("snapshot_age_seconds", False)):
        candidate = dict(value); candidate[key] = bad; candidate["evidence_sha256"] = subject._digest(candidate)
        assert not subject.validate_envelope(candidate)


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
    assert "TimeoutStartSec=1860s" in unit and "RuntimeMaxSec=" not in unit
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
    result = subject._blocked("not_armed")
    diagnostic = subject._diagnostic(result, failed_phase="pre_arm", outcome=None, arm_consumed=False)
    monkeypatch.setattr(subject, "_execute_under_lock", lambda digest, now: (result, diagnostic))
    monkeypatch.setattr(subject, "_write_public_diagnostic", lambda value: events.append("diagnostic") or True)
    monkeypatch.setattr(subject, "_write_public_receipt", lambda value: events.append("receipt") or True)
    monkeypatch.setattr(subject.os, "close", lambda fd: events.append("release") if fd == 77 else None)
    assert subject.run_root_helper(helper_sha256="a" * 64, publish=True)["status"] == "blocked"
    assert events == ["diagnostic", "receipt", "release"]


def test_current_attempt_invalidates_old_receipt_before_identity_binding(monkeypatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(subject, "_consume_arm", lambda now, digest: "predeploy_backup_root_helper_v1:" + "a" * 64)
    monkeypatch.setattr(subject, "_invalidate_public_receipts", lambda: events.append("invalidate"))
    monkeypatch.setattr(subject, "_bind_identities", lambda: events.append("bind") or (_ for _ in ()).throw(subject.Failure("identity_unavailable")))
    value, diagnostic = subject._execute_under_lock("a" * 64, lambda: 1.0)
    assert events == ["invalidate", "bind"]
    assert value["status"] == "unknown" and value["error_code"] == "execution_ambiguous"
    assert diagnostic["arm_consumed"] is True and diagnostic["failed_phase"] == "post_arm_pre_backup"


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


def test_credential_order_candidate_is_owned_product_helper() -> None:
    source = open(subject.__file__, encoding="utf-8").read()
    assert "credential_directory_fd" in source
    assert "os.fchown(credential_directory_fd, bound.uid, bound.gid)" in source


def test_diagnostic_is_fixed_bound_and_fail_closed() -> None:
    result = subject._unknown("backup_failed", "predeploy_backup_root_helper_v1:" + "a" * 64)
    outcome = subject.ChildOutcome("confirmed_exec", "exited_nonzero", True, 1, True, True, True, b"")
    value = subject._diagnostic(result, failed_phase="backup_wait", outcome=outcome, arm_consumed=True)
    assert subject.validate_diagnostic(value)
    assert value["retry_permitted"] is False and value["manual_recovery_required"] is True
    value["exit_code"] = 256; value["evidence_sha256"] = subject._digest(value)
    assert not subject.validate_diagnostic(value)


def test_child_outcome_requires_terminal_cleanup_when_started() -> None:
    result = subject._unknown("backup_failed", "predeploy_backup_root_helper_v1:" + "a" * 64)
    outcome = subject.ChildOutcome("unknown", "wait_failed", False, 0, True, False, False, b"")
    value = subject._diagnostic(result, failed_phase="backup_wait", outcome=outcome, arm_consumed=True)
    assert subject.validate_diagnostic(value) and value["cleanup_terminal"] is False


def test_diagnostic_crossfield_contract_rejects_false_success_and_reaped_without_start() -> None:
    result = subject._unknown("backup_failed", "predeploy_backup_root_helper_v1:" + "a" * 64)
    value = subject._diagnostic(result, failed_phase="backup_wait", outcome=subject.ChildOutcome("confirmed_exec", "exited_nonzero", True, 1, True, True, True, b""), arm_consumed=True)
    value["result_status"] = "ok"; value["result_error_code"] = "none"; value["manual_recovery_required"] = False; value["evidence_sha256"] = subject._digest(value)
    assert not subject.validate_diagnostic(value)
    value = subject._diagnostic(result, failed_phase="backup_wait", outcome=subject.ChildOutcome("unknown", "wait_failed", False, 0, True, True, True, b""), arm_consumed=True)
    value["child_started"] = False; value["evidence_sha256"] = subject._digest(value)
    assert not subject.validate_diagnostic(value)


def test_durable_used_grant_failure_remains_consumed_and_requires_manual_recovery(monkeypatch) -> None:
    reference = "predeploy_backup_root_helper_v1:" + "a" * 64
    monkeypatch.setattr(subject, "_consume_arm", lambda now, digest: (_ for _ in ()).throw(subject.Failure("not_armed", arm_consumed=True, reference=reference)))
    result, diagnostic = subject._execute_under_lock("a" * 64, lambda: 1.0)
    assert result["status"] == "unknown" and result["action_provenance_ref"] == reference
    assert diagnostic["arm_consumed"] is True and diagnostic["manual_recovery_required"] is True


def test_terminal_drain_is_bounded_and_keeps_fast_readback_bytes(monkeypatch) -> None:
    output = bytearray()
    reads = iter((b"[]", b""))
    monkeypatch.setattr(subject.select, "select", lambda *args: ([41], [], []))
    monkeypatch.setattr(subject.os, "read", lambda fd, size: next(reads))
    assert subject._drain_terminal_output(41, output) is True
    assert bytes(output) == b"[]"


def test_run_child_parent_fast_terminal_drains_before_classification(monkeypatch) -> None:
    bound = subject.Bound(1, 2, 3, 4, 5, 1000, 1000, "")
    pipes = iter(((10, 11), (12, 13)))
    reads = {12: iter((b"",)), 10: iter((b"[]", b""))}
    fake = SimpleNamespace(WNOHANG=1, pipe=lambda: next(pipes), set_inheritable=lambda *args: None, fork=lambda: 77, close=lambda fd: None, waitpid=lambda pid, options: (77, 0), waitstatus_to_exitcode=lambda status: 0, read=lambda fd, size: next(reads[fd]), kill=lambda pid, signal: (_ for _ in ()).throw(AssertionError("real kill forbidden")))
    monkeypatch.setattr(subject, "os", fake)
    monkeypatch.setattr(subject.select, "select", lambda readers, *args: ([10], [], []))
    outcome = subject._run_child(bound, ("/usr/bin/restic",), 1, True)
    assert outcome.restic_exec_state == "confirmed_exec" and outcome.output == b"[]" and outcome.child_reaped is True


def test_run_child_preexec_marker_and_postfork_failure_reap_only_exact_child(monkeypatch) -> None:
    bound = subject.Bound(1, 2, 3, 4, 5, 1000, 1000, "")
    marker = iter((b"P", b""))
    fake = SimpleNamespace(WNOHANG=1, pipe=lambda: (12, 13), set_inheritable=lambda *args: None, fork=lambda: 77, close=lambda fd: None, waitpid=lambda pid, options: (77, 0), waitstatus_to_exitcode=lambda status: 125, read=lambda fd, size: next(marker), kill=lambda pid, signal: (_ for _ in ()).throw(AssertionError("real kill forbidden")))
    monkeypatch.setattr(subject, "os", fake)
    outcome = subject._run_child(bound, ("/usr/bin/restic",), 1, False)
    assert outcome.restic_exec_state == "preexec_failed" and outcome.exit_class == "not_available"

    killed: list[int] = []
    def fail_close(fd: int) -> None:
        if fd == 13: raise OSError()
    fake.close = fail_close
    fake.kill = lambda pid, signal: killed.append(pid)
    outcome = subject._run_child(bound, ("/usr/bin/restic",), 1, False)
    assert killed == [77] and outcome.child_started is True and outcome.child_reaped is True and outcome.cleanup_terminal is True


def test_candidate_has_no_live_dispatch_entrypoint() -> None:
    assert subject.main() == 1


class _ControlledOS(SimpleNamespace):
    """A namespace-only Unix double: PID 77 is never a host process."""

    def __init__(self, *, pipes: tuple[tuple[int, int], ...] = ((12, 13),), waits: tuple[object, ...] = ((77, 0),), reads: tuple[bytes, ...] = (b"",), exit_codes: tuple[object, ...] = (0,)) -> None:
        super().__init__(
            WNOHANG=1, O_RDONLY=0, O_WRONLY=1, O_CREAT=64, O_EXCL=128,
            O_NOFOLLOW=256, O_CLOEXEC=512, O_DIRECTORY=1024,
        )
        self._pipes, self._waits, self._reads, self._codes = iter(pipes), iter(waits), iter(reads), iter(exit_codes)
        self.closed: list[int] = []; self.killed: list[int] = []; self.writes: list[tuple[int, bytes]] = []

    def pipe(self): return next(self._pipes)
    def set_inheritable(self, *args): return None
    def fork(self): return 77
    def close(self, fd): self.closed.append(fd)
    def waitpid(self, pid, options):
        assert pid == 77
        item = next(self._waits)
        if isinstance(item, BaseException): raise item
        return item
    def waitstatus_to_exitcode(self, status):
        item = next(self._codes)
        if isinstance(item, BaseException): raise item
        return item
    def read(self, fd, size): return next(self._reads)
    def kill(self, pid, signal): assert pid == 77 and signal == 9; self.killed.append(pid)
    def write(self, fd, value): self.writes.append((fd, bytes(value))); return len(value)


def _run_parent_case(monkeypatch, fake: _ControlledOS, *, capture: bool = False, timeout: int = 1, ready: object = None, live: bool = False) -> subject.ChildOutcome:
    monkeypatch.setattr(subject, "os", fake)
    monkeypatch.setattr(subject, "_prove_live_exec", lambda pid, bound: live)
    if ready is not None:
        monkeypatch.setattr(subject.select, "select", lambda *args: (_ for _ in ()).throw(ready) if isinstance(ready, BaseException) else ready)
    return subject._run_child(subject.Bound(1, 2, 3, 4, 5, 1000, 1000, ""), ("/usr/bin/restic",), timeout, capture)


def test_run_child_direct_parent_terminal_matrix_uses_only_fake_pid_77(monkeypatch) -> None:
    for code, expected in ((0, "exited_zero"), (3, "exited_nonzero"), (125, "handshake_ambiguous"), (-9, "signaled")):
        outcome = _run_parent_case(monkeypatch, _ControlledOS(exit_codes=(code,)))
        assert outcome.exit_class == expected and outcome.child_reaped and outcome.cleanup_terminal

    # A proven live identity makes the reserved 125 terminal result an ordinary
    # nonzero execution result; an unproven identity remains ambiguous.
    fake = _ControlledOS(waits=((0, 0), (77, 0)), reads=(b"", b""), exit_codes=(125,))
    outcome = _run_parent_case(monkeypatch, fake, ready=([12], [], []), live=True)
    assert outcome.restic_exec_state == "confirmed_exec" and outcome.exit_class == "exited_nonzero" and outcome.exit_code == 125

    fake = _ControlledOS(waits=((0, 0), (77, 0)), reads=(b"", b""), exit_codes=(-9,))
    outcome = _run_parent_case(monkeypatch, fake, ready=([12], [], []), live=True)
    assert outcome.restic_exec_state == "confirmed_exec" and outcome.exit_class == "signaled" and outcome.child_reaped


def test_run_child_parent_failure_matrix_reaps_only_fake_pid_77(monkeypatch) -> None:
    # wait/select/post-fork close failures all terminate and reap PID 77 only.
    fake = _ControlledOS(waits=(OSError(), (77, 0)))
    outcome = _run_parent_case(monkeypatch, fake)
    assert fake.killed == [77] and outcome.exit_class == "wait_failed" and outcome.child_reaped

    fake = _ControlledOS(waits=((0, 0), (77, 0)))
    outcome = _run_parent_case(monkeypatch, fake, ready=OSError())
    assert fake.killed == [77] and outcome.child_reaped

    fake = _ControlledOS(waits=((77, 0),), exit_codes=(OSError(),))
    outcome = _run_parent_case(monkeypatch, fake)
    assert fake.killed == [] and outcome.exit_class == "wait_failed" and outcome.child_reaped

    fake = _ControlledOS(waits=((77, 0),), reads=(b"P", b""), exit_codes=(125,))
    outcome = _run_parent_case(monkeypatch, fake)
    assert outcome.restic_exec_state == "preexec_failed" and outcome.exit_class == "not_available"

    fake = _ControlledOS(waits=((0, 0), (77, 0)))
    monkeypatch.setattr(subject, "os", fake)
    monkeypatch.setattr(subject.time, "monotonic", lambda: 10.0)
    outcome = subject._run_child(subject.Bound(1, 2, 3, 4, 5, 1000, 1000, ""), ("/usr/bin/restic",), 0, False)
    assert fake.killed == [77] and outcome.exit_class == "timed_out" and outcome.child_reaped

    fake = _ControlledOS(pipes=((10, 11), (12, 13)), waits=((0, 0), (77, 0)), reads=(b"x" * (subject.MAX_OUTPUT_BYTES + 1),))
    outcome = _run_parent_case(monkeypatch, fake, capture=True, ready=([10], [], []))
    assert fake.killed == [77] and outcome.exit_class == "output_limit" and outcome.child_reaped


def test_execveat_any_ctypes_return_is_marked_preexec_failure(monkeypatch) -> None:
    """The child branch is controlled without forking or touching a real PID."""
    class ChildOS(_ControlledOS):
        O_WRONLY = 1
        devnull = "/dev/null"
        def fork(self): return 0
        def open(self, *args, **kwargs): return 99
        def dup2(self, *args): return None
        def _exit(self, status): raise _ChildExit(status)
    class _ChildExit(BaseException):
        pass
    fake = ChildOS()
    monkeypatch.setattr(subject, "os", fake)
    monkeypatch.setattr(subject, "_mount_setup", lambda bound: None)
    monkeypatch.setattr(subject, "_close_verified", lambda fd: None)
    monkeypatch.setattr(subject, "_drop_identity", lambda bound: None)
    monkeypatch.setattr(subject, "_prove_repository_access_after_drop", lambda: None)
    monkeypatch.setattr(subject, "_execveat_syscall", lambda: (lambda *args: 0, 322))
    with pytest.raises(_ChildExit):
        subject._run_child(subject.Bound(1, 2, 3, 4, 5, 1000, 1000, ""), ("/usr/bin/restic",), 1, False)
    assert fake.writes == [(13, b"P")]


def test_terminal_drain_failure_and_eintr_bounds_are_direct_and_controlled(monkeypatch) -> None:
    output = bytearray()
    reads = iter((b"part", b""))
    monkeypatch.setattr(subject.os, "read", lambda fd, amount: next(reads))
    monkeypatch.setattr(subject.select, "select", lambda *args: ([41], [], []))
    assert subject._drain_terminal_output(41, output) is True and bytes(output) == b"part"

    for selector in (OSError(),) + (InterruptedError(),) * 32:
        output = bytearray()
        monkeypatch.setattr(subject.select, "select", lambda *args, item=selector: (_ for _ in ()).throw(item))
        assert subject._drain_terminal_output(41, output) is None
    monkeypatch.setattr(subject.select, "select", lambda *args: ([41], [], []))
    monkeypatch.setattr(subject.os, "read", lambda *args: (_ for _ in ()).throw(OSError()))
    assert subject._drain_terminal_output(41, bytearray()) is None


def test_wait_and_kill_helpers_bound_eintr_and_never_target_other_than_77(monkeypatch) -> None:
    fake = _ControlledOS(waits=(InterruptedError(), InterruptedError(), (77, 9)))
    monkeypatch.setattr(subject, "os", fake)
    assert subject._waitpid_nointr(77, 0) == (77, 9)
    assert subject._kill_and_reap(77) == (False, None)  # the controlled queue is exhausted
    assert fake.killed == [77]

    fake = _ControlledOS(waits=(InterruptedError(),) * 32)
    monkeypatch.setattr(subject, "os", fake)
    with pytest.raises(OSError): subject._waitpid_nointr(77, 0)

    fake = _ControlledOS(waits=((77, 9),))
    monkeypatch.setattr(subject, "os", fake)
    assert subject._kill_and_reap(77) == (True, 9) and fake.killed == [77]


def test_terminal_drain_deadline_and_output_limit_are_bounded(monkeypatch) -> None:
    ticks = iter((0.0, 0.0, 2.0, 2.0))
    monkeypatch.setattr(subject.time, "monotonic", lambda: next(ticks, 2.0))
    monkeypatch.setattr(subject.select, "select", lambda *args: ([], [], []))
    assert subject._drain_terminal_output(41, bytearray()) is None
    monkeypatch.setattr(subject.select, "select", lambda *args: ([41], [], []))
    monkeypatch.setattr(subject.os, "read", lambda *args: b"x" * (subject.MAX_OUTPUT_BYTES + 1))
    assert subject._drain_terminal_output(41, bytearray()) is False


def test_invalidation_matrix_rejects_unsafe_parent_each_unlink_and_fsync_before_dispatch(monkeypatch) -> None:
    good_directory = _directory(1, 1, 0o755, 0, 0)
    for failure in ("parent", "first_unlink", "second_unlink", "fsync"):
        events: list[object] = []
        monkeypatch.setattr(subject.os, "open", lambda *args, **kwargs: 9)
        monkeypatch.setattr(subject.os, "fstat", lambda fd: _directory(1, 1, 0o775 if failure == "parent" else 0o755, 0, 0))
        calls = {"count": 0}
        def unlink(name, **kwargs):
            calls["count"] += 1; events.append(("unlink", name))
            if failure == "first_unlink" and calls["count"] == 1: raise OSError()
            if failure == "second_unlink" and calls["count"] == 2: raise OSError()
        monkeypatch.setattr(subject.os, "unlink", unlink)
        monkeypatch.setattr(subject.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError()) if failure == "fsync" else events.append(("fsync", fd)))
        monkeypatch.setattr(subject.os, "close", lambda fd: None)
        with pytest.raises(subject.Failure): subject._invalidate_public_receipts()
        if failure != "parent": assert events[0] == ("unlink", "receipt.json")


class _PublicationOS(SimpleNamespace):
    """Descriptor-rooted publication fixture; no operation reaches a filesystem."""

    def __init__(self) -> None:
        super().__init__(O_RDONLY=0, O_WRONLY=1, O_CREAT=64, O_EXCL=128, O_NOFOLLOW=256, O_CLOEXEC=512, O_DIRECTORY=1024)
        self.path = subject.posixpath
        self.calls: list[tuple[object, ...]] = []; self.payload = b""; self.foreign = False
        self.names: dict[str, tuple[int, int]] = {}
        self.directory = _directory(1, 1, 0o755, 0, 0)
        self.file = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_gid=0, st_nlink=1, st_dev=2, st_ino=3)
    def open(self, path, flags, mode=None, *, dir_fd=None):
        self.calls.append(("open", path, flags, mode, dir_fd))
        if path == "/run/odysseus-predeploy-backup-root-helper": return 1
        if path == "receipt.json": return 3
        self.names[path] = (2, 3)
        return 2
    def fstat(self, fd): return self.directory if fd == 1 else self.file
    def fchown(self, fd, uid, gid): self.calls.append(("fchown", fd, uid, gid)); self.file.st_uid = uid; self.file.st_gid = gid
    def fchmod(self, fd, mode): self.calls.append(("fchmod", fd, mode)); self.file.st_mode = stat.S_IFREG | mode
    def write(self, fd, value): self.calls.append(("write", fd, bytes(value))); self.payload += bytes(value); return len(value)
    def fsync(self, fd): self.calls.append(("fsync", fd))
    def close(self, fd): self.calls.append(("close", fd))
    def replace(self, old, new, **kwargs):
        self.calls.append(("replace", old, new, kwargs))
        self.names[new] = self.names.pop(old)
    def read(self, fd, amount): self.calls.append(("read", fd, amount)); return self.payload
    def stat(self, name, **kwargs):
        self.calls.append(("stat", name, kwargs))
        if name not in self.names: raise FileNotFoundError()
        device, inode = (9, 9) if self.foreign else self.names[name]
        return SimpleNamespace(st_dev=device, st_ino=inode)
    def unlink(self, name, **kwargs):
        self.calls.append(("unlink", name, kwargs)); del self.names[name]
    def urandom(self, size): return b"x" * size


def _publish_with(monkeypatch, fake: _PublicationOS) -> bool:
    monkeypatch.setattr(subject, "os", fake)
    return subject._write_public_document(subject.PUBLIC_RECEIPT_PATH, subject._blocked("not_armed"), subject.validate_envelope)


def test_atomic_publication_descriptor_matrix_exercises_every_operation_and_cleanup(monkeypatch) -> None:
    good = _PublicationOS()
    assert _publish_with(monkeypatch, good)
    temporary_open = next(call for call in good.calls if call[0] == "open" and call[3] == 0o600)
    assert temporary_open[2] & good.O_EXCL and temporary_open[2] & good.O_NOFOLLOW and temporary_open[3] == 0o600
    assert ("fchown", 2, 0, 0) in good.calls and ("fchmod", 2, 0o644) in good.calls
    assert [call[0] for call in good.calls].index("replace") < [call[0] for call in good.calls].index("read")

    # A short write is completed by the loop; zero, errors, and every durable
    # publication boundary fail closed before returning success.
    short = _PublicationOS()
    def short_write(fd, value):
        count = max(1, len(value) // 2); short.payload += bytes(value)[:count]; return count
    short.write = short_write
    assert _publish_with(monkeypatch, short)
    for stage in ("fchown", "fchmod", "write", "fsync_file", "replace", "fsync_dir", "reopen"):
        fake = _PublicationOS()
        if stage == "fchown": fake.fchown = lambda *args: (_ for _ in ()).throw(OSError())
        elif stage == "fchmod": fake.fchmod = lambda *args: (_ for _ in ()).throw(OSError())
        elif stage == "write": fake.write = lambda *args: 0
        elif stage == "fsync_file": fake.fsync = lambda fd: (_ for _ in ()).throw(OSError()) if fd == 2 else None
        elif stage == "replace": fake.replace = lambda *args, **kwargs: (_ for _ in ()).throw(OSError())
        elif stage == "fsync_dir": fake.fsync = lambda fd: (_ for _ in ()).throw(OSError()) if fd == 1 else None
        else:
            original_open = fake.open; fake.open = lambda path, *args, **kwargs: (_ for _ in ()).throw(OSError()) if path == "receipt.json" else original_open(path, *args, **kwargs)
        assert not _publish_with(monkeypatch, fake), stage
        assert "receipt.json" not in fake.names, stage
        assert not fake.names, stage

    for mutation in ("parent", "owner", "group", "mode", "link"):
        fake = _PublicationOS()
        if mutation == "parent": fake.directory = _directory(1, 1, 0o775, 0, 0)
        elif mutation == "owner": fake.fchown = lambda *args: None; fake.file.st_uid = 1000
        elif mutation == "group": fake.fchown = lambda *args: None; fake.file.st_gid = 1000
        elif mutation == "mode": fake.fchmod = lambda *args: None; fake.file.st_mode = stat.S_IFREG | 0o666
        else: fake.file.st_nlink = 2
        assert not _publish_with(monkeypatch, fake), mutation

    owned = _PublicationOS(); owned.fchown = lambda *args: (_ for _ in ()).throw(OSError())
    assert not _publish_with(monkeypatch, owned)
    assert any(call[0] == "unlink" for call in owned.calls)
    foreign = _PublicationOS(); foreign.foreign = True; foreign.fchown = lambda *args: (_ for _ in ()).throw(OSError())
    assert not _publish_with(monkeypatch, foreign)
    assert not any(call[0] == "unlink" for call in foreign.calls)


def test_generated_diagnostic_state_matrix_is_exact_for_producer_and_reader() -> None:
    reference = "predeploy_backup_root_helper_v1:" + "a" * 64
    blocked = subject._diagnostic(subject._blocked("not_armed"), failed_phase="pre_arm", outcome=None, arm_consumed=False)
    assert subject.validate_diagnostic(blocked)
    durable = subject._diagnostic(subject._unknown("execution_ambiguous", reference), failed_phase="pre_arm", outcome=None, arm_consumed=True)
    assert subject.validate_diagnostic(durable)
    success = subject._diagnostic({**subject._unknown("backup_failed", reference), "status": "ok", "error_code": "none"}, failed_phase="none", outcome=subject.ChildOutcome("confirmed_exec", "exited_zero", True, 0, True, True, True, b""), arm_consumed=True)
    assert subject.validate_diagnostic(success)
    for key, bad in (("failed_phase", "none"), ("child_started", True), ("arm_consumed", True)):
        value = dict(blocked); value[key] = bad; value["evidence_sha256"] = subject._digest(value)
        assert not subject.validate_diagnostic(value)


def test_controlled_os_missing_posix_namespace_constants_uses_fixed_fallback(monkeypatch) -> None:
    fake = SimpleNamespace(path=subject.posixpath, open=lambda *args, **kwargs: 7, fstat=lambda fd: _directory(1, 1, 0o755, 0, 0), unlink=lambda *args, **kwargs: None, fsync=lambda fd: None, close=lambda fd: None)
    monkeypatch.setattr(subject, "os", fake)
    subject._invalidate_public_receipts()


def test_kill_retries_are_bounded_and_failed_or_foreign_reap_stays_unknown(monkeypatch) -> None:
    for interruptions, failure, waited, expected in (
        (2, False, (77, 9), (True, 9)),
        (32, False, (77, 9), (False, None)),
        (0, True, (77, 9), (False, None)),
        (0, False, (88, 9), (False, None)),
    ):
        calls = []
        def kill(pid, signal):
            assert (pid, signal) == (77, 9)
            calls.append((pid, signal))
            if len(calls) <= interruptions: raise InterruptedError()
            if failure: raise OSError()
        waits = []
        def waitpid(pid, options):
            assert (pid, options) == (77, 0)
            waits.append((pid, options)); return waited
        monkeypatch.setattr(subject, "os", SimpleNamespace(kill=kill, waitpid=waitpid))
        assert subject._kill_and_reap(77) == expected
        assert len(calls) == (32 if interruptions == 32 else interruptions + 1)
        assert len(waits) == (0 if failure or interruptions == 32 else 1)

    fake = _ControlledOS(waits=(OSError(),))
    fake.kill = lambda *args: (_ for _ in ()).throw(OSError())
    outcome = _run_parent_case(monkeypatch, fake)
    assert outcome.child_started and not outcome.child_reaped and not outcome.cleanup_terminal
    assert outcome.exit_class == "wait_failed" and outcome.restic_exec_state == "unknown"


def test_post_reap_conversion_failure_keeps_prior_live_exec_and_captured_bytes(monkeypatch) -> None:
    fake = _ControlledOS(pipes=((10, 11), (12, 13)), waits=((0, 0), (77, 0)),
                         reads=(b"", b"captured", b""), exit_codes=(OSError(),))
    outcome = _run_parent_case(monkeypatch, fake, capture=True, ready=([12, 10], [], []), live=True)
    assert outcome.restic_exec_state == "confirmed_exec" and outcome.output == b"captured"
    assert outcome.exit_class == "wait_failed" and not outcome.exit_code_available
    assert outcome.child_reaped and outcome.cleanup_terminal and fake.killed == []


def test_current_attempt_invalidation_failures_stop_before_binding_or_dispatch(monkeypatch) -> None:
    reference = "predeploy_backup_root_helper_v1:" + "b" * 64
    for failure in ("parent", "first_unlink", "second_unlink", "fsync"):
        events = []
        def unlink(name, **kwargs):
            events.append(name)
            if (failure == "first_unlink" and len(events) == 1) or (failure == "second_unlink" and len(events) == 2):
                raise OSError()
        fake = SimpleNamespace(path=subject.posixpath, open=lambda *a, **k: 9,
            fstat=lambda fd: _directory(1, 1, 0o775 if failure == "parent" else 0o755, 0, 0),
            unlink=unlink, close=lambda fd: None,
            fsync=lambda fd: (_ for _ in ()).throw(OSError()) if failure == "fsync" else None)
        monkeypatch.setattr(subject, "os", fake)
        monkeypatch.setattr(subject, "_consume_arm", lambda now, digest: reference)
        forbidden = []
        monkeypatch.setattr(subject, "_bind_identities", lambda: forbidden.append("bind"))
        monkeypatch.setattr(subject, "_run_child", lambda *a: forbidden.append("dispatch"))
        result, diagnostic = subject._execute_under_lock("a" * 64, lambda: 1.0)
        assert forbidden == []
        assert result["status"] == "unknown" and result["action_provenance_ref"] == reference
        assert diagnostic["failed_phase"] == "post_arm_pre_backup" and diagnostic["arm_consumed"]
        assert diagnostic["manual_recovery_required"] and not diagnostic["retry_permitted"]
        assert not diagnostic["child_started"] and diagnostic["cleanup_terminal"]
        assert events == ([] if failure == "parent" else ["receipt.json"] if failure == "first_unlink" else ["receipt.json", "diagnostic.json"])


def test_post_replace_failures_remove_owned_target_and_preserve_foreign_replacements(monkeypatch) -> None:
    for failure in ("commit_then_raise", "read", "mismatch", "foreign_target", "foreign_temp"):
        fake = _PublicationOS()
        original_replace = fake.replace
        if failure in ("commit_then_raise", "foreign_target"):
            def replace(old, new, **kwargs):
                original_replace(old, new, **kwargs)
                if failure == "foreign_target": fake.names[new] = (9, 9)
                raise OSError()
            fake.replace = replace
        elif failure == "read": fake.read = lambda *args: (_ for _ in ()).throw(OSError())
        elif failure == "mismatch": fake.read = lambda *args: b"mismatch"
        else:
            def fchown(*args):
                temporary = next(iter(fake.names)); fake.names[temporary] = (9, 9)
                raise OSError()
            fake.fchown = fchown
        assert not _publish_with(monkeypatch, fake)
        if failure == "foreign_target": assert fake.names == {"receipt.json": (9, 9)}
        elif failure == "foreign_temp": assert len(fake.names) == 1 and set(fake.names.values()) == {(9, 9)}
        else: assert fake.names == {}


def test_all_public_validators_are_total_and_reject_hostile_or_wrong_schema_values() -> None:
    from ops.homeserver import redacted_predeploy_backup_root_helper_readback as reader
    producers = ((subject.validate_envelope, subject._blocked("not_armed")),
                 (reader.validate_envelope, reader.collect_readback(reader=lambda: None)),
                 (reader.validate_diagnostic_readback_v2, reader._diagnostic_readback("unavailable", False)))
    for validator, valid in producers:
        assert validator(valid)
        for hostile in (None, [], 5, "bad", {"evidence_sha256": object()}):
            assert validator(hostile) is False
        for field, hostile in (("schema_id", "wrong"), ("evidence_sha256", []), ("status", object())):
            value = dict(valid); value[field] = hostile
            if field == "schema_id":
                value["evidence_sha256"] = subject._digest(value)
            assert validator(value) is False


def test_generated_state_positive_table_and_crossed_negative_contract_for_both_validators() -> None:
    from ops.homeserver import redacted_predeploy_backup_root_helper_readback as reader
    validators = (subject.validate_diagnostic, reader._validate_diagnostic)
    reference = "predeploy_backup_root_helper_v1:" + "c" * 64
    rows = [(subject._blocked("not_armed"), "pre_arm", None, False)]
    for phase in ("pre_arm", "post_arm_pre_backup", "backup_start", "readback_start"):
        rows.append((subject._unknown("execution_ambiguous", reference), phase, None, True))
    for prefix in ("backup", "readback"):
        rows.append((subject._unknown(prefix + "_failed", reference), prefix + "_start",
                     subject.ChildOutcome("preexec_failed", "not_available", False, 0, True, True, True, b""), True))
        for state in ("confirmed_exec", "unknown"):
            for exit_class in ("timed_out", "output_limit", "wait_failed", "signaled", "handshake_ambiguous"):
                for reaped in (True, False):
                    if not reaped and exit_class in ("signaled", "handshake_ambiguous"): continue
                    code = prefix + ("_timeout" if exit_class == "timed_out" else "_failed")
                    rows.append((subject._unknown(code, reference), prefix + "_wait",
                                 subject.ChildOutcome(state, exit_class, False, 0, True, reaped, reaped, b""), True))
        rows.append((subject._unknown(prefix + "_failed", reference), prefix + "_wait",
                     subject.ChildOutcome("confirmed_exec", "exited_nonzero", True, 7, True, True, True, b""), True))
    zero = subject.ChildOutcome("confirmed_exec", "exited_zero", True, 0, True, True, True, b"[]")
    rows.append((subject._unknown("readback_invalid", reference), "readback_validate", zero, True))
    rows.append((subject._unknown("execution_ambiguous", reference), "unexpected", zero, True))
    ok = subject._unknown("backup_failed", reference); ok.update(status="ok", error_code="none")
    rows.append((ok, "none", zero, True))
    positives = []
    for result, phase, outcome, consumed in rows:
        value = subject._diagnostic(result, failed_phase=phase, outcome=outcome, arm_consumed=consumed)
        assert all(validate(value) for validate in validators)
        positives.append(value)

    def reject(value, **changes):
        bad = dict(value); bad.update(changes); bad["evidence_sha256"] = subject._digest(bad)
        assert all(validate(bad) is False for validate in validators), changes

    # Every generated row must reject privacy leaks, retries and contradictory
    # consumption/start/reap assertions even when its digest is recomputed.
    for value in positives:
        reject(value, retry_permitted=True)
        reject(value, manual_recovery_required=not value["manual_recovery_required"])
        reject(value, raw_stdout_visible=True)
        reject(value, child_started=False, child_reaped=True)
        if value["result_status"] != "blocked": reject(value, arm_consumed=False)
        if value["result_status"] == "unknown": reject(value, failed_phase="none")
        if value["child_started"]:
            reject(value, restic_exec_state="not_attempted")
            reject(value, child_started=False, child_reaped=False, cleanup_terminal=True)
        elif value["result_status"] == "blocked":
            reject(value, failed_phase="backup_wait")
            reject(value, child_started=True, child_reaped=True)
        else:
            reject(value, restic_exec_state="confirmed_exec")
            reject(value, exit_class="signaled")
        if value["exit_class"] == "exited_zero": reject(value, exit_code=1)
        if value["exit_class"] == "exited_nonzero": reject(value, exit_code=0)
        if value["exit_class"] in ("signaled", "handshake_ambiguous", "exited_zero", "exited_nonzero"):
            reject(value, child_reaped=False, cleanup_terminal=False)
        if value["result_error_code"] in ("backup_timeout", "readback_timeout"):
            reject(value, exit_class="wait_failed")
            reject(value, failed_phase="readback_wait" if value["failed_phase"] == "backup_wait" else "backup_wait")
            reject(value, result_error_code="backup_failed" if value["failed_phase"] == "backup_wait" else "readback_failed")
        if value["exit_class"] == "exited_nonzero":
            reject(value, exit_class="not_available", exit_code_available=False, exit_code=0)
        if value["restic_exec_state"] == "preexec_failed": reject(value, failed_phase="backup_wait")
        if value["result_error_code"] == "readback_invalid": reject(value, failed_phase="readback_wait")
