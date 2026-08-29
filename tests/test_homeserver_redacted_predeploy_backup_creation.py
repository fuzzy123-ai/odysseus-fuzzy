from __future__ import annotations

import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "ops" / "homeserver" / "redacted_predeploy_backup_creation.py"
SPEC = importlib.util.spec_from_file_location("predeploy_creation", MODULE_PATH)
creation = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(creation)


def test_public_executor_is_retired_for_default_and_legacy_call_shapes():
    for kwargs in ({}, {"runner": object(), "clock": object()}):
        payload = creation.collect_predeploy_backup_creation(**kwargs)
        assert creation.validate_envelope(payload)
        assert payload["status"] == "blocked"
        assert payload["error_code"] == "legacy_executor_retired"
        assert payload["backup_invoked"] is False
        assert payload["retry_permitted"] is False


def test_only_the_redacted_terminal_envelope_is_accepted():
    payload = creation.blocked()
    assert creation.validate_envelope(payload)

    for field, value in (
        ("backup_invoked", True),
        ("retry_permitted", True),
        ("error_code", "backup_failed"),
    ):
        changed = dict(payload)
        changed[field] = value
        changed["evidence_sha256"] = creation._digest(changed)
        assert not creation.validate_envelope(changed)


def test_module_contains_no_direct_execution_or_namespace_primitives():
    source = MODULE_PATH.read_text(encoding="utf-8")
    for forbidden in ("subprocess", "CLONE_NEWUSER", "CLONE_NEWNS", "execveat", "restic"):
        assert forbidden not in source


def test_main_emits_one_canonical_json_line(capsys):
    assert creation.main() == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert len(lines) == 1
    assert creation.validate_envelope(json.loads(lines[0]))
