from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace


MODULE_PATH = Path(__file__).parents[1] / "ops" / "homeserver" / "redacted_predeploy_backup_creation.py"
SPEC = importlib.util.spec_from_file_location("predeploy_creation", MODULE_PATH)
creation = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(creation)

SNAPSHOT_ID = "a" * 64


class Result:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout, self.returncode = stdout, returncode


def _stat(path: str, *, mode: int = 0o700, uid: int = 1000, kind: int = stat.S_IFREG):
    return SimpleNamespace(st_mode=kind | mode, st_uid=uid)


def _snapshot(*, snapshot_id: str = SNAPSHOT_ID, timestamp: str = "2023-11-14T22:13:25+00:00", tags=None, paths=None):
    return json.dumps([{
        "id": snapshot_id, "time": timestamp,
        "tags": ["odysseus-pre-update"] if tags is None else tags,
        "paths": [creation.SOURCE] if paths is None else paths,
    }])


def _dependencies(*, result=None, process_environment=None, stats=None, mounted=True, lock_result="lock", clock=None):
    defaults = {
        creation.RESTIC_BINARY: _stat(creation.RESTIC_BINARY, mode=0o755, uid=0),
        creation.LOCK_PARENT: _stat(creation.LOCK_PARENT, kind=stat.S_IFDIR, mode=0o700),
        creation.SOURCE: _stat(creation.SOURCE, kind=stat.S_IFDIR, mode=0o700),
        creation.REPOSITORY: _stat(creation.REPOSITORY, kind=stat.S_IFDIR, mode=0o700),
        creation.CONFIG_PATH: _stat(creation.CONFIG_PATH, mode=0o600),
        creation.PASSWORD_FILE: _stat(creation.PASSWORD_FILE, mode=0o600),
    }
    defaults.update(stats or {})
    calls, releases, binds, identity_releases = [], [], [], []
    bound = creation.BoundIdentities(10, 11, 12, 13, 14, 15)
    results = list(result if isinstance(result, list) else [Result(), Result(_snapshot())] if result is None else [result])

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        response = results.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    return {
        "runner": runner, "lstat": lambda path: defaults[path], "mount_checker": lambda path: mounted,
        "owner_lookup": lambda owner: SimpleNamespace(pw_uid=1000),
        "config_reader": lambda: "RESTIC_PASSWORD_FILE=" + creation.PASSWORD_FILE + "\n",
        "process_environment": {} if process_environment is None else process_environment,
        "lock_acquire": lambda uid: lock_result if not isinstance(lock_result, BaseException) else (_ for _ in ()).throw(lock_result),
        "lock_release": lambda handle: releases.append(handle),
        "identity_bind": lambda uid: binds.append(uid) or bound,
        "identity_release": lambda value: identity_releases.append(value),
        "clock": clock or iter((1_700_000_000.5, 1_700_000_010.0)).__next__,
        "calls": calls, "releases": releases, "binds": binds,
        "identity_releases": identity_releases, "bound": bound,
    }


def _collect(deps):
    return creation.collect_predeploy_backup_creation(**{
        key: value for key, value in deps.items()
        if key not in {"calls", "releases", "binds", "identity_releases", "bound"}
    })


def test_success_uses_exact_fixed_argv_environment_lock_and_canonical_packet():
    deps = _dependencies()
    payload = _collect(deps)

    assert set(payload) == creation._OK_KEYS
    assert payload["status"] == "ok" and payload["backup_effect"] == "created"
    assert payload["snapshot_id"] == SNAPSHOT_ID and payload["snapshot_created_after_start"] is True
    assert payload["snapshot_age_seconds"] == 5 and payload["evidence_sha256"] == creation._digest(payload)
    assert payload["concurrent_lock_held"] is True and payload["partial_snapshot_detected"] is False
    assert payload["action_provenance_ref"].startswith("predeploy_backup_creation_v1:")
    assert len(payload["action_provenance_ref"].split(":", 1)[1]) == 64
    assert deps["releases"] == ["lock"] and deps["binds"] == [1000]
    assert deps["identity_releases"] == [deps["bound"]] and len(deps["calls"]) == 2
    expected_commands = (
        creation._bound_command(creation.BACKUP_COMMAND, deps["bound"]),
        creation._bound_command(creation.READBACK_COMMAND, deps["bound"]),
    )
    for (command, kwargs), expected, timeout in zip(deps["calls"], expected_commands, (1800, 20)):
        assert command == expected and kwargs["env"] == creation.FIXED_ENVIRONMENT
        assert kwargs["timeout"] == timeout and kwargs["stderr"] is subprocess.DEVNULL
        assert kwargs["shell"] is False and kwargs["close_fds"] is True
        assert kwargs["pass_fds"] == (10, 12, 14, 15) and callable(kwargs["preexec_fn"])
    assert deps["calls"][0][1]["stdout"] is subprocess.DEVNULL
    assert deps["calls"][1][1]["stdout"] is subprocess.PIPE
    assert not any(token in {"prune", "forget", "restore", "check", "unlock", "backup"} for token in deps["calls"][1][0])
    assert all(payload[key] is False for key in (
        "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible",
        "file_contents_visible", "paths_visible", "hostnames_visible", "secret_values_visible",
    ))


def test_environment_override_rejection_is_pre_dispatch_and_secret_free():
    for environment in (
        {"RESTIC_PASSWORD": "private-token"}, {"RESTIC_PASSWORD_COMMAND": "private-command"},
        {"RESTIC_PASSWORD": ""}, {"RESTIC_PASSWORD_COMMAND": ""},
        {"RESTIC_REPOSITORY": "/private/repo"}, {"RESTIC_BINARY": "/private/bin"},
        {"RESTIC_BIN": "/private/bin"}, {"BACKUP_MOUNT": "/private/mount"},
        {"ODYSSEUS_ROOT": "/private/root"}, {"SOURCE": "/private/source"},
        {"NEXTCLOUD_ROOT": "/private/nextcloud"}, {"HOMEBASE_HOME": "/private/home"},
        {"DB_DUMP_ROOT": "/private/dumps"}, {"DB_DUMP_STAGING": "/private/staging"},
        {"RESTIC_USE_SUDO": "1"}, {"RESTIC_REPAIR_REPO_OWNER": "1"},
        {"RESTIC_PASSWORD_FILE": "/private/password"},
    ):
        deps = _dependencies(process_environment=environment)
        payload = _collect(deps)
        assert payload["status"] == "blocked" and payload["error_code"] == "config_invalid"
        assert deps["calls"] == [] and deps["releases"] == [] and "private" not in json.dumps(payload)


def test_all_lstat_type_owner_mode_and_symlink_gates_block_before_lock_or_dispatch():
    cases = (
        (creation.RESTIC_BINARY, _stat(creation.RESTIC_BINARY, mode=0o777, uid=0), "restic_unavailable"),
        (creation.RESTIC_BINARY, _stat(creation.RESTIC_BINARY, mode=0o755, uid=1000), "restic_unavailable"),
        (creation.RESTIC_BINARY, _stat(creation.RESTIC_BINARY, mode=0o755, uid=0, kind=stat.S_IFLNK), "restic_unavailable"),
        (creation.SOURCE, _stat(creation.SOURCE, mode=0o722, kind=stat.S_IFDIR), "source_path_missing"),
        (creation.SOURCE, _stat(creation.SOURCE, mode=0o700, uid=1001, kind=stat.S_IFDIR), "source_path_missing"),
        (creation.SOURCE, _stat(creation.SOURCE, mode=0o700, kind=stat.S_IFLNK), "source_path_missing"),
        (creation.REPOSITORY, _stat(creation.REPOSITORY, mode=0o700, uid=1001, kind=stat.S_IFDIR), "repository_unsafe"),
        (creation.REPOSITORY, _stat(creation.REPOSITORY, mode=0o722, kind=stat.S_IFDIR), "repository_unsafe"),
        (creation.REPOSITORY, _stat(creation.REPOSITORY, mode=0o700, kind=stat.S_IFLNK), "repository_unsafe"),
        (creation.CONFIG_PATH, _stat(creation.CONFIG_PATH, mode=0o640), "config_invalid"),
        (creation.CONFIG_PATH, _stat(creation.CONFIG_PATH, mode=0o600, uid=1001), "config_invalid"),
        (creation.CONFIG_PATH, _stat(creation.CONFIG_PATH, mode=0o600, kind=stat.S_IFLNK), "config_invalid"),
        (creation.PASSWORD_FILE, _stat(creation.PASSWORD_FILE, mode=0o600, kind=stat.S_IFLNK), "password_file_unsafe"),
        (creation.PASSWORD_FILE, _stat(creation.PASSWORD_FILE, mode=0o644), "password_file_unsafe"),
        (creation.PASSWORD_FILE, _stat(creation.PASSWORD_FILE, mode=0o600, uid=1001), "password_file_unsafe"),
    )
    for path, value, error in cases:
        deps = _dependencies(stats={path: value})
        payload = _collect(deps)
        assert payload["error_code"] == error and deps["calls"] == [] and deps["releases"] == []


def test_mount_config_and_lock_contention_block_before_backup():
    mount = _collect(_dependencies(mounted=False))
    bad_config = _dependencies(); bad_config["config_reader"] = lambda: "RESTIC_PASSWORD_COMMAND=private\n"
    config = _collect(bad_config)
    lock = _dependencies(lock_result=creation.ContractFailure("lock_contended"))
    locked = _collect(lock)

    assert [item["error_code"] for item in (mount, config, locked)] == ["mount_unavailable", "config_invalid", "lock_contended"]
    assert lock["calls"] == [] and lock["releases"] == []
    assert "private" not in json.dumps(config)


def test_lock_parent_type_owner_mode_and_symlink_gates_block_before_lock_acquisition():
    cases = (
        _stat(creation.LOCK_PARENT, kind=stat.S_IFREG, mode=0o700),
        _stat(creation.LOCK_PARENT, kind=stat.S_IFDIR, mode=0o700, uid=1001),
        _stat(creation.LOCK_PARENT, kind=stat.S_IFDIR, mode=0o600),
        _stat(creation.LOCK_PARENT, kind=stat.S_IFLNK, mode=0o700),
    )
    for value in cases:
        deps = _dependencies(stats={creation.LOCK_PARENT: value})
        payload = _collect(deps)
        assert payload["error_code"] == "lock_unavailable" and deps["calls"] == [] and deps["releases"] == []


def test_unsafe_config_metadata_is_never_opened():
    deps = _dependencies(stats={creation.CONFIG_PATH: _stat(creation.CONFIG_PATH, kind=stat.S_IFLNK, mode=0o777)})
    opened = []
    deps["config_reader"] = lambda: opened.append(True) or "private-content"
    payload = _collect(deps)

    assert payload["error_code"] == "config_invalid" and opened == [] and deps["calls"] == []


def test_start_time_is_recorded_immediately_before_backup_and_strictly_older_snapshot_fails_unknown():
    # start = 100.5; even a 0.1-second-old snapshot cannot be reused.
    old = _snapshot(timestamp="1970-01-01T00:01:40.400000+00:00")
    deps = _dependencies(result=[Result(), Result(old)], clock=iter((100.5, 110.0)).__next__)
    payload = _collect(deps)

    assert payload["status"] == "unknown" and payload["error_code"] == "snapshot_not_new"
    assert len(deps["calls"]) == 2 and payload["effect_may_have_occurred"] is True and payload["retry_permitted"] is False


def test_partial_multiple_malformed_and_stale_readbacks_are_unknown_without_partial_facts():
    cases = (
        json.dumps([]), json.dumps([json.loads(_snapshot())[0], json.loads(_snapshot())[0]]), "{private",
        _snapshot(tags=[]), _snapshot(paths=["/private/source"]),
        _snapshot(timestamp="1970-01-01T00:00:00+00:00"),
    )
    for raw in cases:
        deps = _dependencies(result=[Result(), Result(raw)], clock=iter((1_700_000_000.0, 1_700_000_010.0)).__next__)
        payload = _collect(deps)
        assert set(payload) == creation._UNKNOWN_KEYS and payload["status"] == "unknown"
        assert payload["effect_may_have_occurred"] is True and payload["retry_permitted"] is False
        assert payload["manual_recovery_required"] is True and payload["evidence_sha256"] == creation._digest(payload)
        assert __import__("re").fullmatch(r"predeploy_backup_creation_v1:[0-9a-f]{64}", payload["action_provenance_ref"])
        assert "/private/source" not in json.dumps(payload) and "private" not in json.dumps(payload)


def test_equal_start_timestamp_is_valid_and_lock_remains_held_through_readback():
    events = []
    clocks = iter((100.5, 110.0))
    deps = _dependencies(result=[Result(), Result(_snapshot(timestamp="1970-01-01T00:01:40.500000+00:00"))], clock=lambda: next(clocks))
    original_runner, original_release = deps["runner"], deps["lock_release"]

    def runner(command, **kwargs):
        events.append(("run", command))
        return original_runner(command, **kwargs)

    def release(handle):
        events.append(("release", handle))
        original_release(handle)

    deps["runner"], deps["lock_release"] = runner, release
    payload = _collect(deps)

    assert payload["status"] == "ok" and payload["snapshot_age_seconds"] == 9
    assert [kind for kind, _ in events] == ["run", "run", "release"]


def test_path_swap_after_binding_cannot_change_dispatched_identities_or_recorded_source():
    deps = _dependencies()
    original_runner = deps["runner"]
    swapped = {creation.SOURCE: "attacker", creation.REPOSITORY: "attacker", creation.PASSWORD_FILE: "attacker"}

    def runner(command, **kwargs):
        # Pathname state may change after the FDs were captured.  Dispatch is
        # nevertheless defined only by the retained descriptors and private
        # namespace, while Restic still receives the canonical recorded name.
        assert all(swapped[path] == "attacker" for path in swapped)
        assert kwargs["pass_fds"] == deps["bound"].pass_fds
        assert command[0] == "/proc/self/fd/15"
        if "backup" in command:
            assert command[0] == "/proc/self/fd/15"
            assert "backup" in command and creation.SOURCE in command
            assert not hasattr(creation, "BACKUP_SCRIPT")
        return original_runner(command, **kwargs)

    deps["runner"] = runner
    payload = _collect(deps)

    assert payload["status"] == "ok"
    assert deps["identity_releases"] == [deps["bound"]]


def test_sealed_credential_copy_failure_is_pre_dispatch_and_all_bound_descriptors_release_once_after_dispatch():
    unavailable = _dependencies()
    unavailable["identity_bind"] = lambda uid: (_ for _ in ()).throw(
        creation.ContractFailure("identity_bind_unavailable")
    )
    blocked = _collect(unavailable)
    assert blocked["status"] == "blocked" and blocked["error_code"] == "identity_bind_unavailable"
    assert unavailable["calls"] == [] and unavailable["identity_releases"] == []
    assert unavailable["releases"] == ["lock"]

    failed = _dependencies(result=RuntimeError("private post-dispatch failure"))
    payload = _collect(failed)
    assert payload["status"] == "unknown" and payload["retry_permitted"] is False
    assert failed["identity_releases"] == [failed["bound"]]
    assert failed["releases"] == ["lock"] and len(failed["calls"]) == 1
    assert "private" not in json.dumps(payload)


def test_memfd_sealing_failure_closes_every_opened_identity_without_disclosure(monkeypatch):
    descriptors = iter((20, 21))
    closed = []
    regular = lambda uid, mode, size=0, ino=1: SimpleNamespace(
        st_mode=stat.S_IFREG | mode, st_uid=uid, st_nlink=1, st_size=size,
        st_dev=1, st_ino=ino, st_mtime_ns=1, st_ctime_ns=1,
    )
    directory = lambda ino: SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o700, st_uid=1000, st_nlink=1, st_size=0,
        st_dev=1, st_ino=ino, st_mtime_ns=1, st_ctime_ns=1,
    )
    metadata = {
        20: regular(0, 0o755, ino=20), 21: directory(21), 22: directory(22),
        23: directory(23), 24: directory(24), 25: regular(1000, 0o600, size=4, ino=25),
    }
    reads = iter((4, 0))
    monkeypatch.setattr(creation.os, "O_NOFOLLOW", 0x20000, raising=False)
    monkeypatch.setattr(creation, "_open_nofollow", lambda path, directory: next(descriptors))
    monkeypatch.setattr(creation, "_open_directory_components", lambda path: 22 if path == creation.BACKUP_MOUNT else 24)
    monkeypatch.setattr(creation, "_prove_fixed_backup_mount", lambda descriptor: None)
    monkeypatch.setattr(creation, "_open_relative_components", lambda parent, components: 23)
    def open_credential(path, flags, **kwargs):
        assert path == "restic-password" and kwargs == {"dir_fd": 24}
        assert flags & getattr(creation.os, "O_NOFOLLOW", 0)
        return 25
    monkeypatch.setattr(creation.os, "open", open_credential)
    monkeypatch.setattr(creation.os, "fstat", lambda descriptor: metadata[descriptor])
    def readv(descriptor, buffers):
        count = next(reads)
        if count:
            buffers[0][:count] = b"test"
        return count
    monkeypatch.setattr(creation.os, "readv", readv, raising=False)
    monkeypatch.setattr(creation.os, "close", lambda descriptor: closed.append(descriptor))
    monkeypatch.setattr(creation.os, "MFD_ALLOW_SEALING", 2, raising=False)
    monkeypatch.setattr(creation.os, "MFD_CLOEXEC", 1, raising=False)
    monkeypatch.setattr(
        creation.os, "memfd_create", lambda name, flags: (_ for _ in ()).throw(OSError("sealed copy unavailable")),
        raising=False,
    )

    try:
        creation._production_identity_bind(1000)
        assert False, "binding must fail closed"
    except creation.ContractFailure as exc:
        assert exc.code == "identity_bind_unavailable"
    assert set(closed) == {20, 21, 22, 23, 24, 25}


def test_backup_mount_is_opened_componentwise_and_requires_exact_mount_id_match(monkeypatch):
    opens, closes = [], []
    descriptors = iter((30, 31, 32))
    monkeypatch.setattr(creation.os, "O_NOFOLLOW", 0x20000, raising=False)

    def open_component(path, flags, **kwargs):
        opens.append((path, kwargs.get("dir_fd")))
        return next(descriptors)

    monkeypatch.setattr(creation.os, "open", open_component)
    monkeypatch.setattr(creation.os, "close", lambda descriptor: closes.append(descriptor))
    mount_fd = creation._open_directory_components(creation.BACKUP_MOUNT)
    assert mount_fd == 32
    assert opens == [("/", None), ("mnt", 30), ("backup", 31)]
    assert closes == [30, 31]

    good = {
        f"/proc/self/fdinfo/{mount_fd}": b"pos:\t0\nmnt_id:\t42\n",
        "/proc/self/mountinfo": b"42 1 8:1 / /mnt/backup rw - ext4 /dev/x rw\n",
    }
    monkeypatch.setattr(creation, "_read_proc_bounded", lambda path, maximum: good[path])
    creation._prove_fixed_backup_mount(mount_fd)
    good["/proc/self/mountinfo"] = b"42 1 8:1 / /mnt/attacker rw - ext4 /dev/x rw\n"
    try:
        creation._prove_fixed_backup_mount(mount_fd)
        assert False, "mount alias must fail closed"
    except creation.ContractFailure as exc:
        assert exc.code == "identity_bind_unavailable"


def test_user_namespace_maps_only_current_identity_then_becomes_namespace_root():
    events = []

    assert creation._enter_private_user_mount_namespace(
        uid=1000,
        gid=1001,
        unshare_call=lambda flags: events.append(("unshare", flags)) or 0,
        map_writer=lambda path, value: events.append((path, value)),
        setresgid=lambda real, effective, saved: events.append(("setresgid", (real, effective, saved))),
        setresuid=lambda real, effective, saved: events.append(("setresuid", (real, effective, saved))),
    ) is True
    assert events == [
        ("unshare", 0x10000000 | 0x00020000),
        ("/proc/self/setgroups", "deny\n"),
        ("/proc/self/uid_map", "0 1000 1\n"),
        ("/proc/self/gid_map", "0 1001 1\n"),
        ("setresgid", (0, 0, 0)),
        ("setresuid", (0, 0, 0)),
    ]


def test_user_namespace_mapping_failure_stops_before_privilege_transition():
    transitions = []
    result = creation._enter_private_user_mount_namespace(
        uid=1000,
        gid=1000,
        unshare_call=lambda flags: 0,
        map_writer=lambda path, value: (_ for _ in ()).throw(OSError("denied")),
        setresgid=lambda *args: transitions.append("gid"),
        setresuid=lambda *args: transitions.append("uid"),
    )
    assert result is False and transitions == []


def test_child_closes_every_binding_fd_and_marks_only_executable_cloexec():
    bound = creation.BoundIdentities(10, 11, 12, 13, 14, 15)
    events = []
    assert creation._close_child_binding_fds(
        bound,
        closer=lambda descriptor: events.append(("close", descriptor)),
        cloexec_setter=lambda descriptor: events.append(("cloexec", descriptor)),
    ) is True
    assert events == [
        ("close", 10), ("close", 12), ("close", 14), ("cloexec", 15),
    ]
    assert bound.pass_fds == (10, 12, 14, 15)
    assert 11 not in bound.pass_fds and 13 not in bound.pass_fds


def test_child_descriptor_close_or_cloexec_failure_is_fail_closed():
    bound = creation.BoundIdentities(10, 11, 12, 13, 14, 15)
    events = []
    assert creation._close_child_binding_fds(
        bound,
        closer=lambda descriptor: (_ for _ in ()).throw(OSError("close failed")),
        cloexec_setter=lambda descriptor: events.append(descriptor),
    ) is False
    assert events == []
    assert creation._close_child_binding_fds(
        bound,
        closer=lambda descriptor: None,
        cloexec_setter=lambda descriptor: (_ for _ in ()).throw(OSError("cloexec failed")),
    ) is False


def test_backup_timeout_nonzero_exception_and_invalid_result_are_unknown_with_one_dispatch_only():
    cases = (
        subprocess.TimeoutExpired(creation.BACKUP_COMMAND, 1800), Result(returncode=1),
        RuntimeError("private backup failure"), SimpleNamespace(returncode="bad"),
    )
    for response in cases:
        deps = _dependencies(result=response)
        payload = _collect(deps)
        assert payload["status"] == "unknown" and len(deps["calls"]) == 1
        assert payload["effect_may_have_occurred"] is True and payload["retry_permitted"] is False
        assert "private" not in json.dumps(payload)


def test_readback_timeout_nonzero_exception_and_oversize_are_unknown_without_backup_retry():
    cases = (
        subprocess.TimeoutExpired(creation.READBACK_COMMAND, 20), Result(returncode=2),
        RuntimeError("private readback failure"), Result("x" * (creation.MAX_READBACK_BYTES + 1)),
    )
    for response in cases:
        deps = _dependencies(result=[Result(), response])
        payload = _collect(deps)
        assert payload["status"] == "unknown" and len(deps["calls"]) == 2
        assert payload["retry_permitted"] is False and "private" not in json.dumps(payload)


def test_streaming_readback_kills_and_reaps_on_overflow_and_timeout():
    class Pipe:
        def __init__(self): self.closed = False
        def fileno(self): return 99
        def close(self): self.closed = True

    class Process:
        def __init__(self): self.stdout, self.killed, self.waits = Pipe(), False, []
        def kill(self): self.killed = True
        def wait(self, timeout=None): self.waits.append(timeout); return 0

    bound = creation.BoundIdentities(10, 11, 12, 13, 14, 15)
    overflow_process = Process()
    overflow = creation._bounded_readback_subprocess(
        creation._bound_command(creation.READBACK_COMMAND, bound), bound=bound, timeout=20,
        maximum_stdout=4, popen=lambda *args, **kwargs: overflow_process,
        wait_for_read=lambda *args: ([overflow_process.stdout], [], []),
        reader=lambda descriptor, amount: b"12345", monotonic=iter((0.0, 0.1)).__next__,
    )
    assert overflow.stdout_oversized is True
    assert overflow_process.killed is True and overflow_process.waits == [None]
    assert overflow_process.stdout.closed is True

    timeout_process = Process()
    try:
        creation._bounded_readback_subprocess(
            creation._bound_command(creation.READBACK_COMMAND, bound), bound=bound, timeout=20,
            maximum_stdout=4, popen=lambda *args, **kwargs: timeout_process,
            wait_for_read=lambda *args: ([], [], []), reader=lambda descriptor, amount: b"",
            monotonic=iter((0.0, 0.1)).__next__,
        )
        assert False, "timeout must fail closed"
    except subprocess.TimeoutExpired:
        pass
    assert timeout_process.killed is True and timeout_process.waits == [None]
    assert timeout_process.stdout.closed is True


def test_streaming_readback_accepts_exact_bound_and_passes_all_bound_fds():
    class Pipe:
        def __init__(self): self.closed = False
        def fileno(self): return 99
        def close(self): self.closed = True
    class Process:
        def __init__(self): self.stdout, self.kwargs = Pipe(), None
        def wait(self, timeout=None): return 0
        def kill(self): assert False, "must not kill exact-bound result"

    bound = creation.BoundIdentities(10, 11, 12, 13, 14, 15)
    process, calls = Process(), []
    chunks = iter((b"1234", b""))
    result = creation._bounded_readback_subprocess(
        creation._bound_command(creation.READBACK_COMMAND, bound), bound=bound, timeout=20,
        maximum_stdout=4,
        popen=lambda command, **kwargs: calls.append((command, kwargs)) or process,
        wait_for_read=lambda *args: ([process.stdout], [], []),
        reader=lambda descriptor, amount: next(chunks),
        monotonic=iter((0.0, 0.1, 0.2, 0.3)).__next__,
    )
    assert result.returncode == 0 and result.stdout == "1234" and result.stdout_oversized is False
    assert calls[0][1]["pass_fds"] == bound.pass_fds and calls[0][1]["stdout"] is subprocess.PIPE
    assert process.stdout.closed is True


def test_snapshot_requires_exact_id_tag_source_and_fresh_future_safe_time():
    invalid_id = _snapshot(snapshot_id="a" * 8)
    future = _snapshot(timestamp="2023-11-14T22:16:40+00:00")
    unbounded_tag = _snapshot(tags=["x" * 65, "odysseus-pre-update"])
    unbounded_path = _snapshot(paths=["x" * 4097, creation.SOURCE])
    for raw, error in ((invalid_id, "snapshot_id_invalid"), (future, "snapshot_stale"), (unbounded_tag, "readback_malformed"), (unbounded_path, "readback_malformed")):
        payload = _collect(_dependencies(result=[Result(), Result(raw)]))
        assert payload["status"] == "unknown" and payload["error_code"] == error


def test_snapshot_freshness_uses_the_fixed_outer_packet_window_boundary():
    boundary = _snapshot(timestamp="1970-01-01T00:01:40+00:00")
    accepted = _collect(_dependencies(result=[Result(), Result(boundary)], clock=iter((100.0, 1960.0)).__next__))
    stale = _collect(_dependencies(result=[Result(), Result(boundary)], clock=iter((100.0, 1960.1)).__next__))

    assert accepted["status"] == "ok" and accepted["snapshot_age_seconds"] == creation.OUTER_PACKET_TIMEOUT_SECONDS
    assert stale["status"] == "unknown" and stale["error_code"] == "snapshot_stale"


def test_invalid_start_clock_is_blocked_before_backup_dispatch():
    for bad_value in (True, -1.0, float("nan"), "100"):
        deps = _dependencies(clock=lambda: bad_value)
        payload = _collect(deps)
        assert payload["status"] == "blocked" and payload["error_code"] == "internal_error" and deps["calls"] == []


def test_main_emits_one_canonical_json_line(monkeypatch, capsys):
    monkeypatch.setattr(creation, "collect_predeploy_backup_creation", lambda: creation.blocked("lock_contended"))
    assert creation.main() == 1
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1 and json.loads(captured.out)["error_code"] == "lock_contended"
    assert captured.err == ""


def test_creation_envelope_validation_rejects_visibility_or_digest_tampering():
    payload = creation._ok("a" * 64, 0, action_provenance_ref=creation._action_provenance_ref(1.0))
    assert creation.validate_envelope(payload)
    payload["paths_visible"] = True; payload["evidence_sha256"] = creation._digest(payload)
    assert not creation.validate_envelope(payload)
