from pathlib import Path

import pytest

from src.evidence_storage import EvidenceStorageError, build_evidence_readiness, write_evidence_report


def test_write_evidence_report_uses_safe_relative_json_ref(tmp_path: Path):
    record = write_evidence_report(
        report_ref="autonomous_coding_agent/live-smoke.json",
        payload={"status": "ok", "raw": "not persisted", "summary": "Sandbox smoke passed."},
        root=tmp_path,
    )

    assert record.written is True
    assert record.report_ref == "autonomous_coding_agent/live-smoke.json"
    assert record.content_hash.startswith("sha256:")
    assert (tmp_path / "autonomous_coding_agent" / "live-smoke.json").exists()
    assert "not persisted" not in (tmp_path / "autonomous_coding_agent" / "live-smoke.json").read_text(encoding="utf-8")
    assert build_evidence_readiness(report_ref=record.report_ref, root=tmp_path)["ready"] is True


def test_evidence_report_rejects_unsafe_refs_and_redacts_secret_values(tmp_path: Path):
    with pytest.raises(EvidenceStorageError):
        write_evidence_report(report_ref="C:/Users/private/live.json", payload={"status": "ok"}, root=tmp_path)

    record = write_evidence_report(
        report_ref="autonomous_coding_agent/redacted.json",
        payload={"note": "Authorization: Bearer abcdefghijk"},
        root=tmp_path,
    )
    text = (tmp_path / record.report_ref).read_text(encoding="utf-8")
    assert "Bearer" not in text
    assert "[redacted]" in text
