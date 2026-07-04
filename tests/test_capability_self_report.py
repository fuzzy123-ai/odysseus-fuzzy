from routes import chat_helpers


def test_capability_self_report_answers_from_diagnostics(monkeypatch):
    def fake_diagnostics():
        return {
            "status": "success",
            "snapshot": {
                "id": "tool-capabilities-test",
                "commit": "abc123",
                "builtin_tool_count": 79,
                "index_status": {"status": "ok"},
                "domains": {"filesystem_code": 9, "agent_development": 6},
            },
            "memory_records": {"count": 10},
            "raptorgraph": {"event_present": True},
            "raw_content_visible": False,
        }

    monkeypatch.setattr(
        "src.tool_capability_maintenance.read_tool_capability_diagnostics",
        fake_diagnostics,
    )

    answer = chat_helpers.build_deterministic_capability_self_report(
        "Welche Datei-, Shell-, Grep-, Git- und Sandbox-Faehigkeiten hast du?"
    )

    assert answer is not None
    assert "tool-capabilities-test" in answer
    assert "read_file" in answer
    assert "write_file" in answer
    assert "grep" in answer
    assert "bash" in answer
    assert "manage_repos" in answer
    assert "sandbox-bound Worker/Runner" in answer
    assert "copy-only Universal-Inbox-Transfer" in answer
    assert "keine freie Host-Shell" in answer
    assert "Gated/disabled" in answer
    assert "RaptorGraph-Event: ja" in answer
    assert "raw_content_visible=False" in answer


def test_capability_self_report_handles_missing_capability_wording(monkeypatch):
    def fake_diagnostics():
        return {
            "status": "success",
            "snapshot": {
                "id": "tool-capabilities-missing-wording",
                "commit": "def456",
                "builtin_tool_count": 91,
                "index_status": {"status": "ok"},
                "domains": {"filesystem_code": 9, "agent_development": 7},
            },
            "memory_records": {"count": 10},
            "raptorgraph": {"event_present": True},
            "raw_content_visible": False,
        }

    monkeypatch.setattr(
        "src.tool_capability_maintenance.read_tool_capability_diagnostics",
        fake_diagnostics,
    )

    answer = chat_helpers.build_deterministic_capability_self_report(
        "Was fehlt dir als autonomous coding assistant fuer Sandbox, Terminal und Nextcloud Write?"
    )

    assert answer is not None
    assert "tool-capabilities-missing-wording" in answer
    assert "sandbox-bound Worker/Runner" in answer
    assert "copy-only Universal-Inbox-Transfer" in answer
    assert "Fehlend: nichts davon" in answer
    assert "Kein Tool" not in answer
    assert "kein Upload" not in answer


def test_capability_self_report_ignores_regular_chat():
    assert chat_helpers.build_deterministic_capability_self_report("Guten Morgen") is None
