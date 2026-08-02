from __future__ import annotations

import importlib.util
import errno
import json
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace


PATH = Path(__file__).parents[1] / "ops" / "homeserver" / "redacted_predeploy_backup_capability.py"
SPEC = importlib.util.spec_from_file_location("backup_capability", PATH)
capability = importlib.util.module_from_spec(SPEC); assert SPEC and SPEC.loader; SPEC.loader.exec_module(capability)


def _stat(directory: bool, mode: int) -> SimpleNamespace:
    return SimpleNamespace(st_mode=(stat.S_IFDIR if directory else stat.S_IFREG) | mode, st_uid=0)


def _deps(result=None):
    calls, releases = [], []
    identities = capability.Identities(10, 11)
    def runner(command, **kwargs):
        calls.append((command, kwargs))
        if isinstance(result, BaseException): raise result
        return SimpleNamespace(returncode=0 if result is None else result)
    stats = {capability.SOURCE: _stat(True, 0o755), capability.EXECUTABLE: _stat(False, 0o755)}
    return dict(runner=runner, lstat=lambda path: stats[path], identity_open=lambda: identities,
                identity_release=lambda value: releases.append(value), calls=calls, releases=releases, identities=identities)


def _collect(deps, execute=True):
    return capability.collect_predeploy_backup_capability(execute=execute, **{k: v for k, v in deps.items() if k not in {"calls", "releases", "identities"}})


def test_default_is_inert_and_supported_probe_uses_only_fixed_devnull_contract():
    deps = _deps()
    inert = _collect(deps, execute=False)
    assert inert["status"] == "blocked" and inert["error_code"] == "invalid_invocation"
    assert deps["calls"] == [] and deps["releases"] == [] and capability.validate_envelope(inert)

    payload = _collect(deps)
    assert payload["status"] == "supported" and payload["error_code"] == "none"
    assert capability.validate_envelope(payload) and deps["releases"] == [deps["identities"]]
    assert all(payload[key] is True for key in capability._BOOL_KEYS if not key.endswith("_visible"))
    assert all(payload[key] is False for key in capability._BOOL_KEYS if key.endswith("_visible"))
    command, kwargs = deps["calls"][0]
    assert command == ("/proc/self/fd/11",) and kwargs["pass_fds"] == (10, 11)
    assert kwargs["stdin"] is kwargs["stdout"] is kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["env"] == {"PATH": "/usr/bin:/bin"} and kwargs["timeout"] == 15
    assert kwargs["shell"] is False and callable(kwargs["preexec_fn"])


def test_preflight_and_postdispatch_failures_are_redacted_terminal_and_release_once():
    unsafe = _deps(); unsafe["lstat"] = lambda path: _stat(path == capability.SOURCE, 0o777)
    blocked = _collect(unsafe)
    assert blocked["status"] == "blocked" and blocked["probe_invoked"] is False and unsafe["calls"] == []

    for result, error in ((2, "capability_unavailable"), (subprocess.TimeoutExpired(("true",), 15), "timeout"), (RuntimeError("sensitive-exception-token"), "capability_unavailable")):
        deps = _deps(result)
        payload = _collect(deps)
        assert payload["status"] == "unsupported" and payload["error_code"] == error
        assert payload["retry_permitted"] is False and deps["releases"] == [deps["identities"]]
        assert "sensitive-exception-token" not in json.dumps(payload)


def test_self_map_sequence_is_exact_and_failure_is_fail_closed():
    events = []
    assert capability._enter_user_mount_namespace(
        1000, 1001, unshare=lambda flags: events.append(("unshare", flags)) or 0,
        writer=lambda path, value: events.append((path, value)),
        setgid=lambda *args: events.append(("gid", args)), setuid=lambda *args: events.append(("uid", args)),
    )
    assert events == [
        ("unshare", 0x10000000 | 0x00020000), ("/proc/self/setgroups", "deny\n"),
        ("/proc/self/uid_map", "0 1000 1\n"), ("/proc/self/gid_map", "0 1001 1\n"),
        ("gid", (0, 0, 0)), ("uid", (0, 0, 0)),
    ]
    assert not capability._enter_user_mount_namespace(1000, 1000, unshare=lambda flags: -1)


def test_child_closes_bound_directory_and_marks_executable_cloexec_exactly():
    identities = capability.Identities(10, 11)
    events, closed, flags = [], set(), {11: 0}
    def closer(descriptor): events.append(("close", descriptor)); closed.add(descriptor)
    def getfd(descriptor):
        events.append(("get", descriptor))
        if descriptor in closed: raise OSError(errno.EBADF, "closed")
        return flags[descriptor]
    def setfd(descriptor, value): events.append(("set", descriptor, value)); flags[descriptor] = value
    assert capability._finalize_child_fds(identities, closer=closer, getfd=getfd, setfd=setfd, cloexec=1)
    assert events == [("close", 10), ("get", 10), ("get", 11), ("set", 11, 1), ("get", 11)]
    assert flags[11] == 1

    assert not capability._finalize_child_fds(
        identities, closer=lambda descriptor: None,
        getfd=lambda descriptor: (_ for _ in ()).throw(OSError(errno.EIO, "wrong failure")),
        setfd=lambda descriptor, value: None, cloexec=1,
    )


def test_root_mountinfo_requires_exactly_one_parseable_private_root():
    valid = "1 0 0:1 / / rw - tmpfs tmpfs rw\n"
    assert capability._root_mount_is_private(valid)
    assert not capability._root_mount_is_private("")
    assert not capability._root_mount_is_private(valid + "2 0 0:2 / / ro - tmpfs tmpfs ro\n")
    assert not capability._root_mount_is_private("1 0 0:1 / / rw malformed\n")
    assert not capability._root_mount_is_private("1 0 0:1 / / rw shared:7 - tmpfs tmpfs rw\n")


def test_exact_child_unshare_mapping_mount_bind_remount_close_cloexec_sequence():
    identities = capability.Identities(10, 11)
    events = []
    reads = {
        "/proc/self/uid_map": "0 1000 1\n", "/proc/self/gid_map": "0 1001 1\n",
        "/proc/self/setgroups": "deny\n",
        "/proc/self/mountinfo": "1 0 0:1 / / rw - tmpfs tmpfs rw\n",
    }
    info = SimpleNamespace(st_dev=7, st_ino=9)
    assert capability._perform_child_probe(
        identities, uid=1000, gid=1001,
        unshare=lambda flags: events.append(("unshare", flags)) or 0,
        map_writer=lambda path, value: events.append(("map", path, value)),
        setgid=lambda *values: events.append(("setgid", values)),
        setuid=lambda *values: events.append(("setuid", values)),
        mount_call=lambda source, target, fs, flags, data: events.append(("mount", source, target, fs, flags, data)),
        bounded_reader=lambda path, maximum: events.append(("read", path, maximum)) or reads[path],
        mkdir=lambda path, mode: events.append(("mkdir", path, mode)),
        stat_call=lambda path: events.append(("stat", path)) or info,
        fstat_call=lambda descriptor: events.append(("fstat", descriptor)) or info,
        statvfs_call=lambda path: events.append(("statvfs", path)) or SimpleNamespace(f_flag=1),
        finalize=lambda value: events.append(("finalize", value.source_fd, value.executable_fd)) or True,
    )
    assert events == [
        ("unshare", 0x10000000 | 0x00020000),
        ("map", "/proc/self/setgroups", "deny\n"),
        ("map", "/proc/self/uid_map", "0 1000 1\n"),
        ("map", "/proc/self/gid_map", "0 1001 1\n"),
        ("setgid", (0, 0, 0)), ("setuid", (0, 0, 0)),
        ("read", "/proc/self/uid_map", 256), ("read", "/proc/self/gid_map", 256),
        ("read", "/proc/self/setgroups", 32),
        ("mount", None, "/", None, 16384 | (1 << 18), None),
        ("read", "/proc/self/mountinfo", 1_048_576),
        ("mount", "tmpfs", "/tmp", "tmpfs", 2 | 4, "mode=0755,size=1048576"),
        ("mkdir", capability.TARGET, 0o700),
        ("mount", "/proc/self/fd/10", capability.TARGET, None, 4096 | 16384, None),
        ("stat", capability.TARGET), ("fstat", 10),
        ("mount", None, capability.TARGET, None, 4096 | 32 | 1, None),
        ("statvfs", capability.TARGET), ("finalize", 10, 11),
    ]


def test_envelope_rejects_boolean_or_digest_tampering_and_source_has_no_sensitive_contracts():
    payload = capability._packet("supported", "none", invoked=True, ready=True)
    payload["descriptor_directory_bound"] = False; payload["evidence_sha256"] = capability._digest(payload)
    assert not capability.validate_envelope(payload)
    text = PATH.read_text(encoding="utf-8")
    assert "RESTIC_PASSWORD" not in text and "/mnt/backup" not in text and "restic" not in text.lower()
