from src.clarification_privacy import (
    build_clarification_privacy_boundary,
    build_memory_candidate_from_answer,
    build_secure_handoff_intent,
    contains_secret_material,
)


def _run():
    return {
        "clarification_id": "clar-demo",
        "owner": "alice",
        "session_id": "sess-1",
        "scope": "project",
        "project_slug": "demo",
        "coding_task_id": "",
    }


def test_privacy_boundary_keeps_answers_out_of_global_memory() -> None:
    boundary = build_clarification_privacy_boundary(_run(), question_id="tone")

    assert boundary["schema"] == "odysseus.clarification_privacy_boundary.v1"
    assert boundary["answer_storage_scope"] == "project"
    assert boundary["global_memory_write_allowed"] is False
    assert boundary["raw_content_visible"] is False


def test_secret_answer_routes_to_secure_handoff_without_value() -> None:
    assert contains_secret_material({"text": "api_key=super-secret"})

    handoff = build_secure_handoff_intent(_run(), question_id="api_key")

    assert handoff["status"] == "secure_handoff_required"
    assert handoff["value_visible"] is False
    assert handoff["value_stored"] is False
    assert "super-secret" not in repr(handoff)


def test_memory_candidate_only_for_reviewed_stable_preferences() -> None:
    candidate = build_memory_candidate_from_answer(
        _run(),
        question={
            "key": "tone",
            "category": "preference",
            "prompt": "Which review tone should be used by default?",
        },
        question_id="tone",
        answer={"answer": "Concise"},
    )
    ordinary = build_memory_candidate_from_answer(
        _run(),
        question={"key": "target_documents", "category": "scope", "prompt": "Which docs?"},
        question_id="target_documents",
        answer={"answer": "Reports"},
    )

    assert candidate is not None
    assert candidate["status"] == "proposed"
    assert candidate["requires_review"] is True
    assert candidate["truth_write_allowed"] is False
    assert ordinary is None
