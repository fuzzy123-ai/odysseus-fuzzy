import pytest

from src.clarification_policy import (
    build_deterministic_questions_for_missing_fields,
    evaluate_clarification_completeness,
    is_duplicate_question,
)


def test_vague_project_intent_requires_material_clarification():
    review = evaluate_clarification_completeness(
        intent_text="Build an autonomous document review service.",
        candidate_questions=[
            {
                "key": "outcome",
                "prompt": "What outcome should the first release achieve?",
                "required": True,
                "reason": "The outcome changes the plan.",
            }
        ],
    )

    assert review["schema"] == "odysseus.clarification_policy.review.v1"
    assert review["requires_clarification"] is True
    assert review["ready_for_understanding_review"] is False
    assert "outcome" in set(review["missing_required_fields"])
    assert review["accepted_questions"][0]["key"] == "outcome"
    assert review["policy"]["model_is_not_sole_judge"] is True
    assert review["raw_content_visible"] is False


def test_complete_known_answers_can_skip_questions_and_enter_understanding_review():
    review = evaluate_clarification_completeness(
        intent_text="Build a local document review service for my team.",
        known_answers={
            "outcome": "Flag risky clauses in PDFs.",
            "target_users": "Internal reviewers.",
            "scope": "PDF only, no OCR.",
            "data_privacy": "Local only.",
            "acceptance_criteria": ["pytest passes", "sample PDF report generated"],
        },
        candidate_questions=[
            {
                "key": "outcome",
                "prompt": "What result do you want?",
                "required": True,
                "reason": "Outcome affects the plan.",
            }
        ],
    )

    assert review["requires_clarification"] is False
    assert review["ready_for_understanding_review"] is True
    assert review["missing_required_fields"] == []
    assert review["accepted_questions"] == []
    assert review["rejected_questions"][0]["reason"] == "already_answered"


def test_candidate_questions_reject_duplicates_non_material_and_unsafe_content():
    review = evaluate_clarification_completeness(
        intent_text="Build a document review service.",
        candidate_questions=[
            {
                "key": "scope",
                "prompt": "Which document types are in scope?",
                "required": True,
                "reason": "Scope changes implementation.",
            },
            {
                "key": "scope",
                "prompt": "What document types are in scope?",
                "required": True,
                "reason": "Scope changes implementation.",
            },
            {
                "key": "favorite_color",
                "prompt": "What color do you like?",
                "required": False,
                "reason": "Cosmetic curiosity.",
            },
            {
                "key": "data_privacy",
                "prompt": "Should I use token=SECRET?",
                "required": True,
                "reason": "Unsafe question.",
            },
        ],
    )

    assert [item["key"] for item in review["accepted_questions"]] == ["scope"]
    rejected = {(item["key"], item["reason"]) for item in review["rejected_questions"]}
    assert ("scope", "duplicate") in rejected
    assert ("favorite_color", "non_material") in rejected
    assert ("data_privacy", "unsafe_content") in rejected


def test_duplicate_question_detector_uses_key_or_prompt_similarity():
    assert is_duplicate_question(
        {"key": "scope", "prompt": "Which document types are in scope?"},
        [{"key": "scope", "prompt": "Something else"}],
    )
    assert is_duplicate_question(
        {"key": "data_privacy", "prompt": "Where may documents be processed?"},
        [{"key": "processing_location", "prompt": "Where may documents be processed?"}],
    )


def test_deterministic_fallback_questions_are_bounded_and_material():
    questions = build_deterministic_questions_for_missing_fields(
        ["outcome", "target_users", "scope", "data_privacy", "acceptance_criteria", "runtime_constraints", "design_direction", "live_permissions"]
    )

    assert len(questions) == 7
    assert questions[0]["key"] == "outcome"
    assert questions[0]["required"] is True
    assert all(question["category"] == question["key"] for question in questions)


def test_policy_rejects_private_path_in_intent():
    with pytest.raises(ValueError, match="unsafe content"):
        evaluate_clarification_completeness(intent_text="Review C:/Users/me/private.pdf")
