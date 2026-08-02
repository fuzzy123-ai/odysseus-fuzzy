from __future__ import annotations

import hashlib

from ops.homeserver import redacted_security_incident_delivery_activation_readback as r


def test_readback_fixed_proofs_are_redacted_and_digest_bound():
    expectation = r.ReadbackExpectation("a" * 40, "b" * 64, True)
    dependencies = tuple(hashlib.sha256((name + ":" + "c" * 64 + " true").encode()).hexdigest() for name in r._DEPENDENCIES)
    baseline = r.RuntimeBaseline("a" * 40, "b" * 64, dependencies, True, True)
    def run(command, **_kw):
        command = tuple(command)
        if command[:2] == ("git", "-C"): output = "a" * 40 + "\n"
        elif command[:3] == ("podman", "inspect", "--format"):
            output = "/opt/odysseus/data:/app/data;/opt/odysseus/logs:/app/logs;/opt/odysseus/data/universal-inbox:/app/universal-inbox;\n" if "Mounts" in command[3] else "c" * 64 + " true\n"
        elif command[:3] == ("podman", "exec", r.APP_CONTAINER): output = "enabled\n" if command[-1] == r._DELIVERY_PROGRAM else "ok\n"
        else: output = ""
        return type("R", (), {"stdout": output, "returncode": 0})()
    value = r.collect_host_readback(expectation, baseline, runner=run, sleeper=lambda _: None)
    assert r.validate_envelope(value) and value["status"] == "ok" and all(value[key] is False for key in r._VISIBILITY)


def test_readback_dependency_mount_revision_manifest_or_delivery_drift_is_not_ok():
    expectation = r.ReadbackExpectation("a" * 40, "b" * 64, True)
    baseline = r.RuntimeBaseline("a" * 40, "b" * 64, ("c" * 64,) * 4, True, True)
    def run(command, **_kw):
        command = tuple(command)
        if command[:2] == ("git", "-C"): output = "0" * 40 + "\n"
        elif command[:3] == ("podman", "inspect", "--format"): output = "c" * 64 + " true\n"
        elif command[:3] == ("podman", "exec", r.APP_CONTAINER): output = "disabled\n" if command[-1] == r._DELIVERY_PROGRAM else "bad\n"
        else: output = ""
        return type("R", (), {"stdout": output, "returncode": 0})()
    value = r.collect_host_readback(expectation, baseline, runner=run, sleeper=lambda _: None)
    assert r.validate_envelope(value) and value["status"] == "observed"
