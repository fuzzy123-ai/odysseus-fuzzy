from __future__ import annotations

import json

from ops.homeserver import redacted_predeploy_backup_creation_transport as transport


def test_legacy_executor_is_retired_without_reading_a_blob_or_contacting_ssh():
    calls = []

    def runner(*args, **kwargs):
        calls.append((args, kwargs))

    result = transport.collect_published_predeploy_backup_creation(
        execute=True,
        runner=runner,
    )
    assert transport.validate_transport_envelope(result)
    assert result["status"] == "blocked"
    assert result["error_code"] == "legacy_executor_retired"
    assert calls == []


def test_no_execute_is_inert_and_execute_is_still_terminal():
    touched = []
    inert = transport.collect_published_predeploy_backup_creation(
        runner=lambda *_args, **_kwargs: touched.append(True)
    )
    assert transport.validate_transport_envelope(inert)
    assert inert["error_code"] == "invalid_invocation"
    assert touched == []

    retired = transport.collect_published_predeploy_backup_creation(
        execute=True,
        runner=lambda *_args, **_kwargs: touched.append(True),
    )
    assert retired["error_code"] == "legacy_executor_retired"
    assert touched == []


def test_main_is_inert(capsys):
    assert transport.main() == 1
    output = json.loads(capsys.readouterr().out)
    assert transport.validate_transport_envelope(output)
