import json

import pytest

from src.project_intake import ProjectIntakeError, apply_project_intake_proposal, build_project_intake_preview
from src.server_project_registry import ServerProjectRegistry


def _registry() -> ServerProjectRegistry:
    registry = ServerProjectRegistry()
    registry.create_project(
        project_title="Kundenportal MVP",
        project_type="app",
        created_at="2026-06-29T10:00:00Z",
    )
    registry.create_project(
        project_title="GameDev Arena",
        project_type="game",
        created_at="2026-06-29T10:01:00Z",
    )
    registry.attach_chat_session(
        project_slug="gamedev-arena",
        session_id="telegram-thread-1",
        updated_at="2026-06-29T10:02:00Z",
    )
    return registry


def test_project_intake_matches_explicit_project_and_extracts_review_proposal():
    proposal = build_project_intake_preview(
        registry=_registry(),
        text=(
            "#project:kundenportal-mvp\n"
            "TODO: Login implementieren fuer MVP.\n"
            "Roadmap: Cloudflare Deploy als Release-Slice aufnehmen.\n"
            "Risiko: DSGVO Review vor Livegang."
        ),
        source_channel="telegram",
    )
    payload = proposal.to_dict()

    assert payload["status"] == "review"
    assert payload["candidate_project"]["project_slug"] == "kundenportal-mvp"
    assert payload["candidate_project"]["confidence"] == 0.98
    assert payload["requires_review"] is True
    assert payload["ready_for_apply"] is False
    assert [task["kind"] for task in payload["tasks"]] == ["task", "release"]
    assert payload["risks"] == ("DSGVO Review vor Livegang.",)
    assert payload["raw_content_visible"] is False
    assert payload["raw_content_persisted"] is False
    assert "Login implementieren" in json.dumps(payload, ensure_ascii=False)


def test_project_intake_uses_bound_chat_session_as_strong_candidate():
    proposal = build_project_intake_preview(
        registry=_registry(),
        text="TODO: Gegner-KI fuer das MVP bauen.",
        chat_session_id="telegram-thread-1",
    )

    payload = proposal.to_dict()
    assert payload["candidate_project"]["project_slug"] == "gamedev-arena"
    assert payload["candidate_project"]["confidence"] == 0.9
    assert "chat_session_bound" in payload["candidate_project"]["reasons"]


def test_project_intake_blocks_without_project_candidate():
    proposal = build_project_intake_preview(
        registry=_registry(),
        text="TODO: Irgendeine Idee ohne Projektkontext ausarbeiten.",
    )

    payload = proposal.to_dict()
    assert payload["status"] == "blocked"
    assert payload["reason"] == "project_choice_required"
    assert payload["candidate_project"] is None
    assert payload["recommended_next_action"] == "choose_project_before_merge"


def test_project_intake_accepts_ai_planner_but_keeps_review_boundary():
    def planner(payload):
        assert payload["candidate_project"]["project_slug"] == "kundenportal-mvp"
        assert "input_text" in payload
        return {
            "tasks": [{"title": "Backend Intake Apply Gate bauen", "kind": "task", "priority": "high"}],
            "decisions": ["Apply bleibt bis Review gesperrt"],
            "risks": ["Falsches Projekt-Matching vermeiden"],
            "roadmap_updates": ["Project Intake Phase 2"],
        }

    proposal = build_project_intake_preview(
        registry=_registry(),
        text="#project:kundenportal-mvp Bitte smart in die Roadmap aufnehmen.",
        ai_merge_planner=planner,
    ).to_dict()

    assert proposal["ai_planner_used"] is True
    assert proposal["tasks"][0]["title"] == "Backend Intake Apply Gate bauen"
    assert proposal["decisions"] == ("Apply bleibt bis Review gesperrt",)
    assert proposal["requires_review"] is True
    assert proposal["ready_for_apply"] is False


def test_project_intake_apply_writes_reviewed_redacted_ledger(tmp_path):
    registry = _registry()
    proposal = build_project_intake_preview(
        registry=registry,
        text="#project:kundenportal-mvp TODO: Login als MVP Slice aufnehmen.",
        source_channel="telegram",
    ).to_dict()
    ledger_path = tmp_path / "project_intake_ledger.json"

    report = apply_project_intake_proposal(
        registry=registry,
        project_slug="kundenportal-mvp",
        proposal=proposal,
        ledger_path=ledger_path,
        applied_at="2026-06-29T12:00:00Z",
        applied_by="telegram",
        review_confirmed=True,
    )

    payload = report.to_dict()
    assert payload["status"] == "applied"
    assert payload["applied"] is True
    assert payload["task_count"] == 1
    assert payload["ledger_path"] == "project_intake_ledger.json"
    stored = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert stored["schema"] == "odysseus.project_intake.ledger.v1"
    assert stored["events"][0]["project_slug"] == "kundenportal-mvp"
    assert stored["events"][0]["tasks"][0]["title"] == "Login als MVP Slice aufnehmen."
    assert stored["events"][0]["project_state_write_performed"] is False
    assert stored["events"][0]["raw_content_persisted"] is False
    assert str(tmp_path) not in json.dumps(payload)


def test_project_intake_apply_requires_review_confirmation(tmp_path):
    registry = _registry()
    proposal = build_project_intake_preview(
        registry=registry,
        text="#project:kundenportal-mvp TODO: Login als MVP Slice aufnehmen.",
    ).to_dict()

    report = apply_project_intake_proposal(
        registry=registry,
        project_slug="kundenportal-mvp",
        proposal=proposal,
        ledger_path=tmp_path / "project_intake_ledger.json",
        applied_at="2026-06-29T12:00:00Z",
        review_confirmed=False,
    ).to_dict()

    assert report["status"] == "blocked"
    assert report["applied"] is False
    assert "review_not_confirmed" in report["blockers"]
    assert not (tmp_path / "project_intake_ledger.json").exists()


def test_project_intake_rejects_secret_and_host_path_material():
    registry = _registry()

    with pytest.raises(ProjectIntakeError, match="secret material"):
        build_project_intake_preview(
            registry=registry,
            text="#project:kundenportal-mvp token=abc123456789012345",
        )

    with pytest.raises(ProjectIntakeError, match="host-local absolute paths"):
        build_project_intake_preview(
            registry=registry,
            text=r"#project:kundenportal-mvp Bitte C:\Users\nkatz\Private lesen",
        )


def test_project_intake_source_has_no_live_runtime():
    source = open("src/project_intake.py", encoding="utf-8").read()

    forbidden = ("subprocess", "requests", "httpx", "paramiko", "podman", "docker", "systemctl")
    for fragment in forbidden:
        assert fragment not in source
