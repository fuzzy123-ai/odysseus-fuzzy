from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "homeserver" / "run-backup-gate-evidence.sh"


def test_backup_gate_evidence_script_requires_explicit_execute() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "EXECUTE=0" in text
    assert "--execute" in text
    assert "refusing to run without --execute" in text


def test_backup_gate_evidence_script_runs_all_required_gate_steps() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "backup-homeserver.sh\" --mode pre-update" in text
    assert "check-backup-health.sh" in text
    assert "restore-backup-smoke.sh" in text
    assert "pre_update_snapshot" in text
    assert "repository_check" in text
    assert "restore_smoke" in text


def test_backup_gate_evidence_script_emits_safe_structured_packet() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert '"evidence_inputs": [' in text
    assert '"secret_values_visible": false' in text
    assert '"host_output_visible": false' in text
    assert ">&2" in text
    assert "RESTIC_PASSWORD_FILE" in text
    assert "RESTIC_PASSWORD_COMMAND" in text


def test_backup_gate_evidence_script_does_not_embed_secret_values() -> None:
    text = SCRIPT.read_text(encoding="utf-8").lower()

    assert "password=" not in text
    assert "token=" not in text
    assert "chat_id=" not in text
