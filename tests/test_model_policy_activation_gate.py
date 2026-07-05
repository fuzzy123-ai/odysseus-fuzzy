from src.model_policy_activation_gate import (
    ActivationGateStatus,
    ModelPolicyActivationEvidence,
    evaluate_policy_activation,
)


def test_activation_gate_allows_go_with_sufficient_clean_evidence():
    decision = evaluate_policy_activation(
        ModelPolicyActivationEvidence.create(
            episode_count=50,
            min_episodes_required=20,
            offline_pass_rate=0.93,
            min_offline_pass_rate=0.85,
        )
    )

    assert decision.status == ActivationGateStatus.GO
    assert decision.active_allowed is True
    assert "activation_evidence_satisfied" in decision.reason_codes


def test_activation_gate_blocks_privacy_violations():
    decision = evaluate_policy_activation(
        {
            "episode_count": 50,
            "offline_pass_rate": 0.95,
            "privacy_violation_count": 1,
        }
    )

    assert decision.status == ActivationGateStatus.BLOCKED
    assert decision.active_allowed is False
    assert "privacy_violations_present" in decision.reason_codes


def test_activation_gate_requires_enough_episodes():
    decision = evaluate_policy_activation({"episode_count": 3, "min_episodes_required": 20, "offline_pass_rate": 0.95})

    assert decision.status == ActivationGateStatus.NEEDS_REVIEW
    assert decision.active_allowed is False
    assert "insufficient_episode_count" in decision.reason_codes


def test_activation_gate_falls_back_on_low_offline_pass_rate():
    decision = evaluate_policy_activation({"episode_count": 30, "offline_pass_rate": 0.4})

    assert decision.status == ActivationGateStatus.FALLBACK_REQUIRED
    assert decision.active_allowed is False
    assert "offline_pass_rate_below_threshold" in decision.reason_codes
    assert decision.audit_summary()["raw_prompt_visible"] is False
