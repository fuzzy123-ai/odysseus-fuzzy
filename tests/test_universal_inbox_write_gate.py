import json

import pytest

from src.universal_inbox_write_gate import (
    UniversalInboxWriteGateError,
    run_universal_inbox_write_capability_probe,
)


def test_write_probe_requires_explicit_enable(tmp_path):
    root = tmp_path / "scratch"
    root.mkdir()

    report = run_universal_inbox_write_capability_probe(root)

    assert report.status == "no_go"
    assert report.reasons == ("probe_writes_not_enabled",)
    assert report.probe_writes_performed is False
    assert list(root.iterdir()) == []


def test_write_probe_runs_only_in_scratch_scope_and_redacts_host_path(tmp_path):
    root = tmp_path / "uix-scratch"
    root.mkdir()

    report = run_universal_inbox_write_capability_probe(
        root,
        allow_probe_writes=True,
        probe_root_label="uix scratch probe",
    )
    payload = report.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["status"] == "go"
    assert payload["write_ok"] is True
    assert payload["rename_ok"] is True
    assert payload["move_ok"] is True
    assert payload["cleanup_ok"] is True
    assert payload["probe_writes_performed"] is True
    assert payload["live_writes_performed"] is False
    assert payload["inbox_files_touched"] is False
    assert payload["absolute_paths_visible"] is False
    assert str(tmp_path) not in encoded
    assert list(root.iterdir()) == []


def test_write_probe_rejects_unmarked_live_like_root(tmp_path):
    root = tmp_path / "Documents"
    root.mkdir()

    report = run_universal_inbox_write_capability_probe(
        root,
        allow_probe_writes=True,
        probe_root_label="documents",
    )

    assert report.status == "no_go"
    assert report.reasons == ("probe_root_not_marked_scratch_or_staging",)
    assert report.probe_writes_performed is False
    assert list(root.iterdir()) == []


def test_write_probe_rejects_unsafe_label(tmp_path):
    root = tmp_path / "scratch"
    root.mkdir()

    with pytest.raises(UniversalInboxWriteGateError):
        run_universal_inbox_write_capability_probe(
            root,
            allow_probe_writes=True,
            probe_root_label="../scratch",
        )
