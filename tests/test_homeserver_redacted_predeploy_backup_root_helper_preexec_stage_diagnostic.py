from __future__ import annotations

import inspect

from ops.homeserver import redacted_predeploy_backup_root_helper_preexec_stage_diagnostic as subject


def test_default_is_inert_redacted_and_never_authorizes_retry() -> None:
    value = subject.collect()
    assert value["status"] == "blocked" and value["error_code"] == "execution_disabled"
    assert subject.validate_envelope(value) and value["retry_permitted"] is False
    assert all(value[key] is False for key in ("raw_stdout_visible", "raw_stderr_visible", "environment_visible", "paths_visible", "hostnames_visible", "secret_values_visible", "repository_write_invoked"))


def test_every_fixed_preexec_stage_validates_without_repository_write() -> None:
    for stage in subject.STAGES - {"none", "restic_read"}:
        value = subject.envelope("observed", "stage_failed", stage, locked=True, bound=True, invoked=True)
        assert subject.validate_envelope(value)
        assert value["repository_write_invoked"] is False


def test_success_restic_failure_and_timeout_are_fixed() -> None:
    values = (
        subject.envelope("observed", "none", "none", locked=True, bound=True, invoked=True),
        subject.envelope("observed", "restic_read_failed", "restic_read", locked=True, bound=True, invoked=True),
        subject.envelope("unknown", "timeout", "none", locked=True, bound=True, invoked=True),
    )
    assert all(subject.validate_envelope(value) for value in values)
    assert subject.collect(execute=True, runner=lambda: {"forged": True})["status"] == "blocked"


def test_child_uses_only_fixed_read_command_and_stage_hooks() -> None:
    source = inspect.getsource(subject._child_result)
    assert '"--no-lock", "snapshots"' in source
    assert '"backup"' not in source and "run_root_helper" not in source
    assert "_mount_setup" in source and "_drop_identity" in source and "EXECVEAT_EMPTY_PATH" in source
