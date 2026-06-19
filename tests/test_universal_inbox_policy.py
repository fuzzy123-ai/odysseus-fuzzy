import pytest

from src.universal_inbox_policy import (
    UniversalInboxPolicyError,
    evaluate_universal_inbox_policy,
)


def test_policy_go_allows_automatic_routing():
    decision = evaluate_universal_inbox_policy(
        {
            "domain": "private",
            "document_type": "invoice",
            "confidence": 0.91,
        }
    )

    assert decision.status == "go"
    assert decision.reasons == ()
    assert decision.allows_automatic_routing is True


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"confidence": 0.2}, "low_confidence"),
        ({"domain": "family"}, "unknown_domain"),
        ({"document_type": ""}, "unknown_document_type"),
        ({"duplicate": True}, "duplicate"),
        ({"partial_extraction": True}, "partial_extraction"),
        ({"secret_detected": True}, "secret_detected"),
        ({"sensitive": True}, "sensitive"),
        ({"target_conflict": True}, "target_conflict"),
    ],
)
def test_policy_review_reasons_are_structured(payload, reason):
    base = {"domain": "private", "document_type": "invoice", "confidence": 0.91}
    base.update(payload)

    decision = evaluate_universal_inbox_policy(base)

    assert decision.status == "review"
    assert reason in decision.review_reasons
    assert reason in decision.reasons
    assert decision.no_go_reasons == ()
    assert decision.allows_automatic_routing is False


@pytest.mark.parametrize(
    "reason",
    [
        "unsafe_target_path",
        "destructive_operation",
        "delete_original",
        "overwrite_existing",
        "raw_content_persistence",
    ],
)
def test_policy_no_go_reasons_win_over_review(reason):
    decision = evaluate_universal_inbox_policy(
        {
            "domain": "private",
            "document_type": "invoice",
            "confidence": 0.1,
            reason: True,
        }
    )

    assert decision.status == "no_go"
    assert reason in decision.no_go_reasons
    assert "low_confidence" in decision.review_reasons
    assert decision.allows_automatic_routing is False


def test_policy_rejects_invalid_confidence():
    with pytest.raises(UniversalInboxPolicyError):
        evaluate_universal_inbox_policy(
            {"domain": "private", "document_type": "invoice", "confidence": 2}
        )
