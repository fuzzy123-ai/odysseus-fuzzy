from __future__ import annotations

import inspect
import json

from ops.homeserver import redacted_predeploy_backup_root_helper_read_diagnostic as subject


def test_default_is_inert_and_every_envelope_is_redacted() -> None:
    value = subject.collect()
    assert value["status"] == "blocked" and value["error_code"] == "execution_disabled"
    assert subject.validate_envelope(value)
    assert all(value[key] is False for key in ("raw_stdout_visible", "raw_stderr_visible", "environment_visible", "paths_visible", "hostnames_visible", "secret_values_visible", "repository_write_invoked"))


def test_observed_result_classifies_preexec_restic_and_valid_shape_without_writes() -> None:
    values = (
        subject.envelope("observed", "preexec_failed", locked=True, bound=True, invoked=True),
        subject.envelope("observed", "restic_read_failed", locked=True, bound=True, invoked=True),
        subject.envelope("observed", "none", locked=True, bound=True, invoked=True, shape=True),
    )
    assert all(subject.validate_envelope(value) for value in values)
    assert all(value["repository_write_invoked"] is False for value in values)


def test_timeout_is_terminal_unknown_and_collect_rejects_malformed_runner() -> None:
    timeout = subject.envelope("unknown", "read_timeout", locked=True, bound=True, invoked=True, effect=True, recovery=True)
    assert subject.validate_envelope(timeout) and timeout["retry_permitted"] is False
    assert subject.collect(execute=True, runner=lambda: {"status": "forged"})["status"] == "blocked"


def test_snapshot_shape_is_bounded_and_requires_one_snapshot() -> None:
    good = json.dumps([{"id": "a" * 64, "paths": [], "tags": []}]).encode()
    assert subject._snapshot_shape(good)
    assert not subject._snapshot_shape(b"[]")
    assert not subject._snapshot_shape(b"not-json")


def test_effectful_helper_entrypoint_and_backup_command_are_never_used() -> None:
    source = inspect.getsource(subject._run_read_only)
    assert "run_root_helper" not in source
    assert '"backup"' not in source
    assert '"--no-lock", "snapshots"' in source
