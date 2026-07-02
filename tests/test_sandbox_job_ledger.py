from pathlib import Path

import pytest

from src.sandbox_job_ledger import SandboxJobLedger, SandboxJobLedgerError, SandboxJobLedgerEvent


def test_sandbox_job_ledger_records_redacted_events(tmp_path: Path):
    ledger = SandboxJobLedger(tmp_path)

    event = ledger.record(
        job_id="job1",
        status="dry_run",
        event_type="plan_rendered",
        payload={"argv": ["python", "--version"], "secrets_attached": False},
        artifact_refs=("data/reports/autonomous_coding_agent/job1.log",),
        preview="Plan rendered",
    )

    rows = ledger.events(job_id="job1")
    assert event.payload_hash.startswith("sha256:")
    assert rows[-1]["job_id"] == "job1"
    assert rows[-1]["raw_content_visible"] is False
    assert ledger.latest("job1")["status"] == "dry_run"
    assert ledger.artifacts("job1") == ("data/reports/autonomous_coding_agent/job1.log",)


def test_sandbox_job_ledger_rejects_secret_values_and_host_paths(tmp_path: Path):
    ledger = SandboxJobLedger(tmp_path)

    with pytest.raises(SandboxJobLedgerError):
        SandboxJobLedgerEvent.create(
            job_id="job2",
            status="failed",
            event_type="bad",
            payload={"authorization": "Bearer abcdefghijk"},
        )

    with pytest.raises(SandboxJobLedgerError):
        ledger.record(
            job_id="job3",
            status="failed",
            event_type="bad",
            artifact_refs=("C:/Users/private/out.log",),
        )


def test_sandbox_job_ledger_redacts_preview_without_rejecting_event(tmp_path: Path):
    ledger = SandboxJobLedger(tmp_path)
    event = ledger.record(
        job_id="job4",
        status="failed",
        event_type="error",
        payload={"exit_code": 1},
        preview="Authorization: Bearer abcdefghijk",
    )

    assert event.preview == "[redacted]"
