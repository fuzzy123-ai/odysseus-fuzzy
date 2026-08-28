from __future__ import annotations

import json
import inspect
from types import SimpleNamespace

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


def test_tampering_rejected_and_no_user_namespace_or_procfs_bind_fallback() -> None:
    value = subject._blocked("not_armed")
    value["backup_invoked"] = True
    value["evidence_sha256"] = subject._digest(value)
    assert not subject.validate_envelope(value)
    source = open(subject.__file__, encoding="utf-8").read()
    assert "CLONE_NEWUSER" not in source
    assert "open_tree" in source and "move_mount" in source and "execveat" in source
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
    execution_source = inspect.getsource(subject._execute_under_lock)
    assert 'b"mode=0711,size=1048576"' in mount_source
    assert "os.fchown(credential_directory_fd, bound.uid, bound.gid)" in mount_source
    assert "move(bound.source_fd, SOURCE)" in mount_source
    assert "move(bound.repository_fd, REPOSITORY)" in mount_source
    assert "os.statvfs(REPOSITORY)" in mount_source
    assert "VIEW_REPOSITORY" not in mount_source
    assert "VIEW_SOURCE" not in mount_source + execution_source
    assert '(RESTIC_BINARY, "-r", REPOSITORY, "backup", SOURCE' in execution_source


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
