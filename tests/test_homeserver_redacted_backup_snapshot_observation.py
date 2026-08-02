import hashlib
import json
import stat
import subprocess
import sys
from types import SimpleNamespace

from ops.homeserver import redacted_backup_snapshot_observation as observation


SNAPSHOT_ID = "a" * 64


class _Result:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout.encode("utf-8") if isinstance(stdout, str) else stdout
        self.returncode = returncode
        self.stdout_oversized = False


def _digest(payload):
    body = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _stat(
    path, *, mode=0o600, uid=1000, kind=stat.S_IFREG, nlink=1,
    dev=1, ino=10, mtime_ns=100, ctime_ns=200, size=1,
):
    return SimpleNamespace(
        st_mode=kind | mode, st_uid=uid, st_nlink=nlink, st_dev=dev, st_ino=ino,
        st_mtime_ns=mtime_ns, st_ctime_ns=ctime_ns, st_size=size,
    )


IDENTITIES = observation._OpenedIdentities(
    config=101, password=102, repository=103, restic=104, source=105, mount=106,
)
SEALED_PASSWORD_FD = 107
PATH_FDS = {
    observation.CONFIG_PATH: IDENTITIES.config,
    observation.PASSWORD_FILE: IDENTITIES.password,
    observation.REPOSITORY: IDENTITIES.repository,
    observation.RESTIC_BINARY: IDENTITIES.restic,
    observation.SOURCE: IDENTITIES.source,
    observation.BACKUP_MOUNT: IDENTITIES.mount,
}


def _dependencies(
    *, config=None, credential=b"synthetic-password", stats=None, fstat_sequences=None,
    result=None, mounted=True, now=1_700_000_000.0, process_environment=None,
    seal_failure=None,
):
    config = observation.CONFIG_PATH if config is None else config
    content = "RESTIC_PASSWORD_FILE=" + observation.PASSWORD_FILE + "\n" if config == observation.CONFIG_PATH else config
    path_stats = {
        observation.CONFIG_PATH: _stat(observation.CONFIG_PATH, size=len(content.encode("utf-8"))),
        observation.PASSWORD_FILE: _stat(observation.PASSWORD_FILE, size=len(credential)),
        observation.REPOSITORY: _stat(observation.REPOSITORY, mode=0o700, kind=stat.S_IFDIR),
        observation.RESTIC_BINARY: _stat(observation.RESTIC_BINARY, mode=0o755, uid=0),
        observation.SOURCE: _stat(observation.SOURCE, mode=0o700, kind=stat.S_IFDIR),
        observation.BACKUP_MOUNT: _stat(observation.BACKUP_MOUNT, mode=0o755, uid=0, kind=stat.S_IFDIR),
    }
    path_stats.update(stats or {})
    fd_stats = {PATH_FDS[path]: value for path, value in path_stats.items()}
    sequences = {PATH_FDS[path]: list(values) for path, values in (fstat_sequences or {}).items()}
    latest = {
        "id": SNAPSHOT_ID, "time": "2023-11-14T22:13:20+00:00",
        "tags": ["homeserver", "odysseus-pre-update"], "paths": [observation.SOURCE],
        "hostname": "private-host", "username": "private-user",
    }
    result = _Result(json.dumps([latest])) if result is None else result
    calls, closed, seal_calls = [], [], []
    def runner(command, **kwargs):
        calls.append((tuple(command), kwargs))
        if isinstance(result, BaseException):
            raise result
        return result
    def injected_fstat(descriptor):
        sequence = sequences.get(descriptor)
        if sequence:
            return sequence.pop(0) if len(sequence) > 1 else sequence[0]
        return fd_stats[descriptor]

    def injected_seal(value):
        seal_calls.append(True)
        if seal_failure is not None:
            raise seal_failure
        return SEALED_PASSWORD_FD

    return {
        "runner": runner, "read_config": lambda descriptor: content,
        "read_credential": lambda descriptor, expected_size: bytearray(credential),
        "seal_credential": injected_seal,
        "identity_opener": lambda: IDENTITIES, "fstat": injected_fstat,
        "close_fd": lambda descriptor: closed.append(descriptor),
        "mount_prover": lambda descriptor: mounted and descriptor == IDENTITIES.mount,
        "owner_lookup": lambda owner: SimpleNamespace(pw_uid=1000) if owner == "homebase" else None,
        "clock": lambda: now, "process_environment": {} if process_environment is None else process_environment,
        "calls": calls, "closed": closed, "path_stats": path_stats, "seal_calls": seal_calls,
    }


def _collect(dependencies):
    calls = dependencies.pop("calls")
    closed = dependencies.pop("closed")
    path_stats = dependencies.pop("path_stats")
    seal_calls = dependencies.pop("seal_calls")
    result = observation.collect_backup_snapshot_observation(**dependencies)
    dependencies["calls"] = calls
    dependencies["closed"] = closed
    dependencies["path_stats"] = path_stats
    dependencies["seal_calls"] = seal_calls
    return result


def test_fixed_read_only_snapshot_observation_is_digest_bound_and_never_uses_backup_commands():
    deps = _dependencies()
    payload = _collect(deps)

    assert payload["status"] == "ok" and set(payload) == observation._OK_KEYS
    assert payload["snapshot_id"] == SNAPSHOT_ID and payload["snapshot_fresh"] is True
    assert payload["source_included"] is True
    assert payload["snapshot_age_seconds"] == 0
    assert payload["repository_identity"] == "restic_homeserver_backup_v1"
    assert payload["protected_source_identity"] == "odysseus_protected_source_v1"
    assert all(payload[key] is False for key in (
        "raw_stdout_visible", "raw_stderr_visible", "exception_text_visible", "environment_visible",
        "file_contents_visible", "paths_visible", "hostnames_visible", "secret_values_visible",
    ))
    assert payload["evidence_sha256"] == _digest(payload)
    assert "private-host" not in json.dumps(payload) and "private-user" not in json.dumps(payload)
    command, kwargs = deps["calls"][0]
    assert command == (
        f"/proc/self/fd/{IDENTITIES.restic}", "-r", f"/proc/self/fd/{IDENTITIES.repository}",
        "--no-lock", "snapshots", "--tag", "odysseus-pre-update", "--latest", "1", "--json",
    )
    assert kwargs["env"] == {"RESTIC_PASSWORD_FILE": f"/proc/self/fd/{SEALED_PASSWORD_FD}", "PATH": "/usr/bin:/bin"}
    assert kwargs["pass_fds"] == IDENTITIES.dispatch_fds(SEALED_PASSWORD_FD)
    assert kwargs["timeout"] <= 20 and kwargs["maximum_stdout"] == observation.MAX_OUTPUT_BYTES
    assert not any(token in {"check", "backup", "restore", "forget", "lock", "unlock"} for token in command)


def test_fixed_binary_source_config_and_password_lstat_contracts_fail_closed_before_dispatch():
    base = _dependencies()
    safe_stats = dict(base["path_stats"])
    cases = (
        (observation.RESTIC_BINARY, _stat(observation.RESTIC_BINARY, mode=0o775, uid=0), "restic_unavailable"),
        (observation.SOURCE, _stat(observation.SOURCE, mode=0o722, kind=stat.S_IFDIR), "source_path_missing"),
        (observation.CONFIG_PATH, _stat(observation.CONFIG_PATH, mode=0o640), "config_invalid"),
        (observation.PASSWORD_FILE, _stat(observation.PASSWORD_FILE, mode=0o600, uid=1001), "password_file_unsafe"),
        (observation.PASSWORD_FILE, _stat(observation.PASSWORD_FILE, mode=0o777, kind=stat.S_IFLNK), "password_file_unsafe"),
    )
    for path, replacement, error_code in cases:
        stats = dict(safe_stats); stats[path] = replacement
        deps = _dependencies(stats=stats)
        payload = _collect(deps)
        assert payload["error_code"] == error_code and deps["calls"] == []


def test_snapshot_age_is_integer_and_bounded_without_emitting_time_or_path():
    payload = _collect(_dependencies(now=1_700_000_017.0))
    encoded = json.dumps(payload)

    assert payload["status"] == "ok" and payload["snapshot_age_seconds"] == 17
    assert "2023-11-14" not in encoded and observation.SOURCE not in encoded


def test_malicious_config_and_path_override_never_dispatch_or_leak():
    malicious = "RESTIC_PASSWORD_COMMAND=cat private-token\n"
    deps = _dependencies(config=malicious)
    payload = _collect(deps)

    assert payload["error_code"] == "config_invalid" and deps["calls"] == []
    assert "private-token" not in json.dumps(payload)
    assert set(payload) == observation._BLOCKED_KEYS and payload["evidence_sha256"] == _digest(payload)


def test_actual_environment_override_attempts_are_rejected_before_dispatch_without_value_leak():
    for environment in (
        {"RESTIC_PASSWORD_COMMAND": "cat private-token"}, {"RESTIC_PASSWORD": "private-token"},
        {"RESTIC_PASSWORD_COMMAND": ""}, {"RESTIC_PASSWORD": ""},
        {"RESTIC_REPOSITORY": "/private/repo"}, {"RESTIC_BINARY": "/private/bin"},
        {"BACKUP_MOUNT": "/private/mount"}, {"ODYSSEUS_ROOT": "/private/root"},
        {"RESTIC_PASSWORD_FILE": "/private/password"},
    ):
        deps = _dependencies(process_environment=environment)
        payload = _collect(deps)
        assert payload["error_code"] == "config_invalid" and deps["calls"] == []
        assert "private" not in json.dumps(payload)


def test_observation_envelope_validator_accepts_canonical_and_rejects_tamper():
    blocked = observation.blocked("snapshot_missing")
    assert observation.validate_envelope(blocked)

    tampered = dict(blocked)
    tampered["secret"] = "synthetic-private-value"
    tampered["evidence_sha256"] = observation._digest(tampered)
    assert observation.validate_envelope(tampered) is False


def test_unsafe_mount_repository_or_password_permissions_fail_closed_without_path_output():
    mount = _collect(_dependencies(mounted=False))
    stats = {
        observation.CONFIG_PATH: _stat(observation.CONFIG_PATH),
        observation.PASSWORD_FILE: _stat(observation.PASSWORD_FILE, mode=0o644),
        observation.REPOSITORY: _stat(observation.REPOSITORY, mode=0o700, kind=stat.S_IFDIR),
        observation.RESTIC_BINARY: _stat(observation.RESTIC_BINARY, mode=0o755, uid=0),
        observation.SOURCE: _stat(observation.SOURCE, mode=0o700, kind=stat.S_IFDIR),
    }
    password = _collect(_dependencies(stats=stats))
    bad_repo = dict(stats); bad_repo[observation.REPOSITORY] = _stat(observation.REPOSITORY, mode=0o700)
    repository = _collect(_dependencies(stats=bad_repo))

    assert mount["error_code"] == "mount_unavailable"
    assert password["error_code"] == "password_file_unsafe"
    assert repository["error_code"] == "repository_unsafe"
    encoded = json.dumps([mount, password, repository])
    assert observation.REPOSITORY not in encoded and observation.PASSWORD_FILE not in encoded


def test_timeout_raw_exception_oversized_malformed_missing_and_stale_snapshots_are_content_free():
    timeout = _collect(_dependencies(result=subprocess.TimeoutExpired(observation.RESTIC_SNAPSHOTS_COMMAND, 20)))
    exception = _collect(_dependencies(result=RuntimeError("secret provider response")))
    oversized = _collect(_dependencies(result=_Result("x" * (observation.MAX_OUTPUT_BYTES + 1))))
    malformed = _collect(_dependencies(result=_Result("{private-json")))
    missing = _collect(_dependencies(result=_Result("[]")))
    stale = _collect(_dependencies(now=1_800_000_000.0))

    assert [item["error_code"] for item in (timeout, exception, oversized, malformed, missing, stale)] == [
        "timeout", "internal_error", "output_too_large", "malformed_output", "snapshot_missing", "snapshot_stale",
    ]
    assert "secret provider response" not in json.dumps(exception)


def test_snapshot_selection_requires_exact_id_pre_update_tag_and_source_path():
    invalid = {"id": "a" * 8, "time": "2023-11-14T22:13:20+00:00", "tags": ["odysseus-pre-update"], "paths": [observation.SOURCE]}
    no_source = {"id": SNAPSHOT_ID, "time": "2023-11-14T22:13:20+00:00", "tags": ["odysseus-pre-update"], "paths": ["/private/source"]}
    invalid_result = _collect(_dependencies(result=_Result(json.dumps([invalid]))))
    source_result = _collect(_dependencies(result=_Result(json.dumps([no_source]))))

    assert invalid_result["error_code"] == "snapshot_id_invalid"
    assert source_result["error_code"] == "source_path_missing"
    assert "/private/source" not in json.dumps(source_result)


def test_main_emits_exactly_one_canonical_json_object(monkeypatch, capsys):
    monkeypatch.setattr(observation, "collect_backup_snapshot_observation", lambda: observation.blocked("timeout"))
    assert observation.main() == 1
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1 and json.loads(captured.out)["error_code"] == "timeout"
    assert captured.err == ""


def test_path_swap_after_identity_open_cannot_change_dispatched_objects():
    deps = _dependencies()
    opener_calls = []
    original_runner = deps["runner"]
    path_state = deps["path_stats"]

    def opener():
        opener_calls.append(True)
        return IDENTITIES

    def runner(command, **kwargs):
        # Model every pathname being replaced after the retained descriptors
        # were opened. The dispatch must use no pathname-derived identity.
        path_state.clear()
        return original_runner(command, **kwargs)

    deps["identity_opener"] = opener
    deps["runner"] = runner
    payload = _collect(deps)

    assert payload["status"] == "ok" and opener_calls == [True]
    command, kwargs = deps["calls"][0]
    assert command[0] == f"/proc/self/fd/{IDENTITIES.restic}"
    assert command[2] == f"/proc/self/fd/{IDENTITIES.repository}"
    assert kwargs["env"]["RESTIC_PASSWORD_FILE"] == f"/proc/self/fd/{SEALED_PASSWORD_FD}"
    assert set(kwargs["pass_fds"]) == set(IDENTITIES.dispatch_fds(SEALED_PASSWORD_FD))
    assert IDENTITIES.password not in kwargs["pass_fds"]


def test_all_retained_descriptors_close_on_success_and_pre_dispatch_failure():
    success = _dependencies()
    assert _collect(success)["status"] == "ok"
    assert success["closed"] == [SEALED_PASSWORD_FD, *reversed(IDENTITIES.all_fds())]

    failed = _dependencies(stats={
        observation.PASSWORD_FILE: _stat(observation.PASSWORD_FILE, mode=0o644),
    })
    assert _collect(failed)["error_code"] == "password_file_unsafe"
    assert failed["calls"] == []
    assert failed["closed"] == list(reversed(IDENTITIES.all_fds()))


def test_component_opener_sets_nofollow_on_every_non_root_open(monkeypatch):
    calls, closed = [], []
    descriptors = iter((10, 11, 12, 13))
    monkeypatch.setattr(observation.os, "O_NOFOLLOW", 0x20000, raising=False)
    monkeypatch.setattr(observation.os, "O_DIRECTORY", 0x10000, raising=False)

    def fake_open(path, flags, mode=0o777, *, dir_fd=None):
        calls.append((path, flags, dir_fd))
        return next(descriptors)

    monkeypatch.setattr(observation.os, "open", fake_open)
    monkeypatch.setattr(observation.os, "close", closed.append)
    assert observation._open_path_no_symlinks("/fixed/private/file", observation.os.O_RDONLY) == 13

    assert calls[0][0] == "/" and calls[0][2] is None
    assert all(flags & observation.os.O_NOFOLLOW for _, flags, _ in calls[1:])
    assert [dir_fd for _, _, dir_fd in calls[1:]] == [10, 11, 12]
    assert closed == [10, 11, 12]


def test_config_or_credential_mutation_during_read_blocks_before_sealing_or_dispatch():
    config_size = len(("RESTIC_PASSWORD_FILE=" + observation.PASSWORD_FILE + "\n").encode("utf-8"))
    stable_config = _stat(observation.CONFIG_PATH, size=config_size)
    changed_config = _stat(observation.CONFIG_PATH, size=config_size, mtime_ns=101)
    config = _dependencies(fstat_sequences={
        observation.CONFIG_PATH: (stable_config, changed_config),
    })
    config_payload = _collect(config)

    credential_size = len(b"synthetic-password")
    stable_password = _stat(observation.PASSWORD_FILE, size=credential_size)
    changed_password = _stat(observation.PASSWORD_FILE, size=credential_size, ctime_ns=201)
    password = _dependencies(fstat_sequences={
        observation.PASSWORD_FILE: (stable_password, changed_password),
    })
    password_payload = _collect(password)

    assert config_payload["error_code"] == "config_invalid"
    assert password_payload["error_code"] == "password_file_unsafe"
    assert config["calls"] == password["calls"] == []
    assert config["seal_calls"] == password["seal_calls"] == []
    assert config["closed"] == password["closed"] == list(reversed(IDENTITIES.all_fds()))


def test_empty_oversized_or_short_credential_and_sealing_failure_never_dispatch():
    empty = _dependencies(credential=b"")
    oversized_value = b"x" * (observation.MAX_PASSWORD_BYTES + 1)
    oversized = _dependencies(credential=oversized_value)
    short = _dependencies(
        credential=b"short",
        stats={observation.PASSWORD_FILE: _stat(observation.PASSWORD_FILE, size=8)},
    )
    seal_failed = _dependencies(seal_failure=observation.ObservationFailure("password_file_unsafe"))

    results = tuple(_collect(item) for item in (empty, oversized, short, seal_failed))
    assert all(result["error_code"] == "password_file_unsafe" for result in results)
    assert all(item["calls"] == [] for item in (empty, oversized, short, seal_failed))
    assert empty["seal_calls"] == oversized["seal_calls"] == short["seal_calls"] == []
    assert seal_failed["seal_calls"] == [True]
    assert all(item["closed"] == list(reversed(IDENTITIES.all_fds())) for item in (empty, oversized, short, seal_failed))


def test_memfd_credential_uses_allow_sealing_and_requires_all_write_seals(monkeypatch):
    add_seals, get_seals = 1, 2
    write, grow, shrink, seal = 0x01, 0x02, 0x04, 0x08
    required = write | grow | shrink | seal
    fcntl_calls, created, closed = [], [], []
    stored = bytearray()

    def fake_fcntl(descriptor, operation, argument=None):
        fcntl_calls.append((descriptor, operation, argument))
        return required if operation == get_seals else 0

    fake_module = SimpleNamespace(
        F_ADD_SEALS=add_seals, F_GET_SEALS=get_seals, F_SEAL_WRITE=write,
        F_SEAL_GROW=grow, F_SEAL_SHRINK=shrink, F_SEAL_SEAL=seal, fcntl=fake_fcntl,
    )
    monkeypatch.setitem(sys.modules, "fcntl", fake_module)
    monkeypatch.setattr(observation.os, "MFD_CLOEXEC", 0x10, raising=False)
    monkeypatch.setattr(observation.os, "MFD_ALLOW_SEALING", 0x20, raising=False)
    monkeypatch.setattr(
        observation.os, "memfd_create",
        lambda name, flags: created.append((name, flags)) or 500,
        raising=False,
    )
    monkeypatch.setattr(observation.os, "write", lambda descriptor, value: stored.extend(value) or len(value))
    monkeypatch.setattr(observation.os, "lseek", lambda descriptor, offset, whence: 0)
    monkeypatch.setattr(
        observation.os, "fstat",
        lambda descriptor: SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_size=len(stored)),
    )
    monkeypatch.setattr(observation.os, "close", closed.append)

    credential = bytearray(b"synthetic-password")
    assert observation._seal_credential_bytes(credential) == 500
    assert created == [("odysseus-restic-credential", 0x30)]
    assert fcntl_calls == [(500, add_seals, required), (500, get_seals, None)]
    assert closed == []

    fcntl_calls.clear()
    fake_module.fcntl = lambda descriptor, operation, argument=None: 0
    try:
        observation._seal_credential_bytes(credential)
    except observation.ObservationFailure as failure:
        assert failure.code == "password_file_unsafe"
    else:
        raise AssertionError("missing seals must fail closed")
    assert closed == [500]


def test_mount_proof_requires_retained_mnt_id_and_exact_mountinfo_target():
    fdinfo = bytearray(b"pos:\t0\nflags:\t0100000\nmnt_id:\t42\n")
    valid = bytearray(b"42 1 8:1 / /mnt/backup rw,relatime - ext4 /dev/sda rw\n")
    assert observation._mount_proof_from_proc(fdinfo, valid)

    adversarial = (
        bytearray(b"41 1 8:1 / /mnt/backup rw - ext4 /dev/sda rw\n"),
        bytearray(b"42 1 8:1 / /mnt/backup-other rw - ext4 /dev/sda rw\n"),
        bytearray(b"42 1 8:1 / /mnt/backup\\040 rw - ext4 /dev/sda rw\n"),
        valid + valid,
        bytearray(b"42 malformed\n"),
    )
    assert all(not observation._mount_proof_from_proc(fdinfo, value) for value in adversarial)
    assert not observation._mount_proof_from_proc(bytearray(b"mnt_id:\t42\nmnt_id:\t42\n"), valid)
    assert not observation._mount_proof_from_proc(fdinfo, bytearray(observation.MAX_MOUNTINFO_BYTES + 1))


def test_credential_reader_uses_readv_preallocation_eof_probe_and_wipes_on_failure(monkeypatch):
    source = bytearray(b"synthetic-password")
    position = [0]
    readv_calls = []

    def fake_readv(descriptor, buffers):
        view = buffers[0]
        readv_calls.append((descriptor, len(view)))
        if position[0] == len(source):
            return 0
        count = min(3, len(view), len(source) - position[0])
        view[:count] = source[position[0]:position[0] + count]
        position[0] += count
        return count

    monkeypatch.setattr(observation.os, "lseek", lambda descriptor, offset, whence: 0)
    monkeypatch.setattr(observation.os, "readv", fake_readv, raising=False)
    monkeypatch.setattr(
        observation.os, "read",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("immutable read forbidden")),
    )
    result = observation._read_credential_fd(77, len(source))
    assert type(result) is bytearray and result == source
    assert readv_calls[-1] == (77, 1)

    exposed_buffers = []
    def short_read(descriptor, buffers):
        exposed_buffers.append(buffers[0].obj)
        return 0
    monkeypatch.setattr(observation.os, "readv", short_read, raising=False)
    try:
        observation._read_credential_fd(77, len(source))
    except observation.ObservationFailure as failure:
        assert failure.code == "password_file_unsafe"
    else:
        raise AssertionError("short credential read must fail closed")
    assert exposed_buffers and all(byte == 0 for byte in exposed_buffers[0])


class _FakePipe:
    def __init__(self):
        self.closed = False
    def fileno(self):
        return 9
    def close(self):
        self.closed = True


class _FakeProcess:
    def __init__(self):
        self.stdout = _FakePipe()
        self.alive = True
        self.killed = False
        self.waited = 0
        self.returncode = 0
    def poll(self):
        return None if self.alive else self.returncode
    def kill(self):
        self.killed = True
        self.alive = False
        self.returncode = -9
    def wait(self, timeout=None):
        self.waited += 1
        self.alive = False
        return self.returncode


def test_streaming_subprocess_kills_and_reaps_on_overflow_and_timeout():
    overflow_process = _FakeProcess()
    chunks = iter((b"abcd", b"ef"))
    overflow = observation._bounded_restic_subprocess(
        ["fixed"], env={}, pass_fds=(3,), timeout=20, maximum_stdout=5,
        popen=lambda command, **kwargs: overflow_process,
        selector=lambda readable, writable, exceptional, timeout: (readable, [], []),
        reader=lambda descriptor, maximum: next(chunks), monotonic=lambda: 0.0,
    )
    assert overflow.stdout_oversized is True and len(overflow.stdout) == 6
    assert overflow_process.killed and overflow_process.waited == 1 and overflow_process.stdout.closed

    timeout_process = _FakeProcess()
    try:
        observation._bounded_restic_subprocess(
            ["fixed"], env={}, pass_fds=(3,), timeout=20, maximum_stdout=5,
            popen=lambda command, **kwargs: timeout_process,
            selector=lambda readable, writable, exceptional, timeout: ([], [], []),
            reader=lambda descriptor, maximum: b"", monotonic=lambda: 0.0,
        )
    except subprocess.TimeoutExpired:
        pass
    else:
        raise AssertionError("timeout must remain terminal")
    assert timeout_process.killed and timeout_process.waited == 1 and timeout_process.stdout.closed
