from src.large_vault_performance_gate import (
    VaultPerformanceEvidence,
    build_large_vault_performance_gate,
)


def _evidence(**overrides):
    payload = {
        "evidence_id": "synthetic-vault-rc",
        "file_count": 120,
        "total_size_mb": 12,
        "link_count": 240,
        "interactive_p95_ms": 120,
        "filter_p95_ms": 160,
        "graph_p95_ms": 420,
        "rebuild_max_seconds": 30,
        "machine_class": "developer-laptop-redacted",
        "workload": "graph-load search filter rebuild synthetic fixture",
    }
    payload.update(overrides)
    return VaultPerformanceEvidence.create(**payload)


def test_small_medium_claim_is_go_when_small_evidence_is_within_budget():
    gate = build_large_vault_performance_gate(
        evidence=_evidence(),
        requested_claim="small-medium",
    )

    assert gate.decision == "go"
    assert gate.supported_claim == "small_medium"
    assert "below the large-vault threshold" in gate.reasons[0]


def test_large_vault_claim_is_no_go_when_only_rc_sized_evidence_exists():
    gate = build_large_vault_performance_gate(
        evidence=_evidence(),
        requested_claim="large_vault",
    )

    assert gate.decision == "no_go"
    assert gate.supported_claim == "small_medium"
    assert "downgraded to the measured scale" in gate.reasons[-1]


def test_large_vault_claim_is_go_with_large_scale_and_green_budgets():
    gate = build_large_vault_performance_gate(
        evidence=_evidence(file_count=10_000, total_size_mb=1024, link_count=25_000),
        requested_claim="large_vault",
    )

    assert gate.decision == "go"
    assert gate.supported_claim == "large_vault"
    assert gate.evidence.satisfies_large_scale is True


def test_budget_overrun_blocks_large_vault_claim():
    gate = build_large_vault_performance_gate(
        evidence=_evidence(
            file_count=10_000,
            total_size_mb=1024,
            interactive_p95_ms=900,
        ),
        requested_claim="large_vault",
    )

    assert gate.decision == "no_go"
    assert gate.supported_claim == "custom"
    assert "exceed threshold" in gate.reasons[1]


def test_gate_payload_is_redacted_scale_summary_not_raw_content():
    gate = build_large_vault_performance_gate(
        evidence=_evidence(machine_class="ci-runner-redacted"),
        requested_claim="small_medium",
    )

    payload = gate.to_dict()

    assert payload["evidence"]["machine_class"] == "ci-runner-redacted"
    assert "content" not in str(payload).lower()
    assert "private" not in str(payload).lower()
