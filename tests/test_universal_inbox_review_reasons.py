from src.universal_inbox_review_reasons import (
    classify_universal_inbox_review_reasons,
    normalize_universal_inbox_review_reasons,
    universal_inbox_review_reason_dicts,
)


def test_review_reason_normalizer_deduplicates_aliases_and_nested_values():
    assert normalize_universal_inbox_review_reasons(
        [
            "needs-review",
            ("routing_needs_review", "partial extract"),
            {"legacy": "failed extractions require review"},
            "partial_extraction",
        ]
    ) == (
        "operator_review_required",
        "partial_extraction",
        "failed_extraction",
    )


def test_review_reason_classifier_marks_stage_category_and_no_go_severity():
    reasons = classify_universal_inbox_review_reasons(
        (
            "low_confidence",
            "dry_run_contains_destructive_token",
            "memory_write_gate_not_open",
            "raptorgraph_write_gate_not_open",
        )
    )
    payload = {reason.code: reason.to_dict() for reason in reasons}

    assert payload["low_confidence"]["stage"] == "classified"
    assert payload["low_confidence"]["category"] == "classification"
    assert payload["low_confidence"]["severity"] == "review"
    assert payload["dry_run_contains_destructive_token"]["stage"] == "copied_exported"
    assert payload["dry_run_contains_destructive_token"]["severity"] == "no_go"
    assert payload["memory_write_gate_not_open"]["stage"] == "memory_intent"
    assert payload["raptorgraph_write_gate_not_open"]["stage"] == "graph_provenance"


def test_review_reason_dicts_can_promote_selected_codes_to_no_go():
    details = universal_inbox_review_reason_dicts(
        ("target_conflict", "unsafe_target_path"),
        no_go_reasons=("unsafe_target_path",),
    )

    assert details[0]["code"] == "target_conflict"
    assert details[0]["severity"] == "review"
    assert details[1]["code"] == "unsafe_target_path"
    assert details[1]["severity"] == "no_go"
