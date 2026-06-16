import pytest

from src.data_classification import (
    AccessDecision,
    ChatAccessMode,
    ClassificationOverride,
    DataClassification,
    DataClassificationError,
    decide_chat_access,
    derive_artifact_classification,
    merge_classifications,
    normalize_classification,
    resolve_classification,
)


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (" public ", DataClassification.PUBLIC),
        ("PRIVATE", DataClassification.PRIVATE),
        ("Sensitive", DataClassification.SENSITIVE),
        (DataClassification.SECRET, DataClassification.SECRET),
    ],
)
def test_normalizes_supported_classifications(raw_value, expected):
    assert normalize_classification(raw_value) == expected


def test_invalid_classification_resolves_to_reviewable_block():
    resolution = resolve_classification("top secret-ish")

    assert resolution.normalized is None
    assert resolution.requires_review is True
    assert resolution.block_reason == "invalid_classification"


def test_merge_uses_strictest_classification():
    merged = merge_classifications(["public", "private", "sensitive"])

    assert merged == DataClassification.SENSITIVE


def test_derived_artifact_cannot_downgrade_without_review_override():
    with pytest.raises(DataClassificationError, match="downgrade requires explicit review override"):
        derive_artifact_classification(
            source_classifications=["sensitive"],
            requested_classification="private",
        )


def test_downgrade_without_reason_fails():
    with pytest.raises(DataClassificationError, match="reason"):
        ClassificationOverride.create(
            reviewed_by="policy-admin",
            reason=" ",
            reviewed_at="2026-06-16",
        )


def test_reviewed_downgrade_is_allowed_when_reason_is_documented():
    override = ClassificationOverride.create(
        reviewed_by="policy-admin",
        reason="Source was overscoped and manually reviewed.",
        reviewed_at="2026-06-16",
    )

    derived = derive_artifact_classification(
        source_classifications=["secret"],
        requested_classification="sensitive",
        override=override,
    )

    assert derived == DataClassification.SENSITIVE


@pytest.mark.parametrize("classification", ["sensitive", "secret"])
def test_normal_chat_blocks_sensitive_and_secret(classification):
    decision = decide_chat_access(classification=classification, mode="normal_chat")

    assert isinstance(decision, AccessDecision)
    assert decision.allowed is False
    assert decision.block_reason == "requires_secure_chat"
    assert decision.required_mode == ChatAccessMode.SECURE
    assert decision.local_only is True


@pytest.mark.parametrize("classification", ["sensitive", "secret"])
def test_secure_chat_allows_sensitive_and_secret_as_local_only(classification):
    decision = decide_chat_access(classification=classification, mode="secure_chat")

    assert decision.allowed is True
    assert decision.block_reason == ""
    assert decision.required_mode == ChatAccessMode.SECURE
    assert decision.local_only is True


def test_mixed_source_policy_uses_highest_classification_for_access():
    merged = merge_classifications(["public", "secret", "private"])
    decision = decide_chat_access(classification=merged, mode="normal_chat")

    assert merged == DataClassification.SECRET
    assert decision.allowed is False
    assert decision.required_mode == ChatAccessMode.SECURE
