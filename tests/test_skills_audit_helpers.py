from routes.skills_audit_helpers import (
    _audit_flag_text,
    _audit_generic_blocker,
    _should_check_retrieval_precision,
    _skill_test_task,
)


def test_skill_test_task_builds_self_contained_prompt():
    task = _skill_test_task({"name": "invoice-helper", "when_to_use": "invoice review"})

    assert "Do NOT ask the user for input" in task
    assert "invoice review" in task


def test_retrieval_precision_prefilter_flags_broad_metadata():
    assert _should_check_retrieval_precision({"tags": ["document"]})
    assert _should_check_retrieval_precision({
        "name": "server network helper",
        "description": "prepare a python command",
    })
    assert not _should_check_retrieval_precision({"tags": ["vendor-specific"]})
    assert not _should_check_retrieval_precision("bad row")


def test_audit_flag_text_normalizes_mixed_values():
    assert _audit_flag_text({"a": "Generic"}, ["TRIVIAL"], None) == "generic trivial "


def test_generic_blocker_detects_necessity_tags_and_verdict_text():
    assert _audit_generic_blocker(
        None,
        {"necessary": False, "reason": "too generic to save"},
        None,
    ) == "too generic to save"
    assert _audit_generic_blocker({"tags": ["generic"]}, None, None) == "Skill is tagged generic"
    assert _audit_generic_blocker(
        None,
        None,
        {"summary": "fine", "issues": ["unnecessary saved skill"]},
    ) == "Audit flagged the skill as generic or unnecessary"
