# Causal Claim Store Contract

Status: `NMG-05 safe-offline contract; no persistence or activation`

`CausalClaimStore` is the sole causal domain-truth and review owner. It does
not create a runtime, store records, run models, or project claims. Causal
suggestions remain candidates until independent review supplies required
evidence. USI may later hold only a rebuildable derived projection.

<!-- NMG-05-CONTRACT:BEGIN -->
```json
{
  "candidate_sources": {
    "co_occurrence": "cannot_promote_to_supported",
    "correlation": "cannot_promote_to_supported",
    "model_suggestion": "cannot_promote_to_supported"
  },
  "causal_claim_store": {
    "review_owner": "sole_independent_review_owner",
    "truth_owner": "sole_causal_domain_truth_owner"
  },
  "claim_identity_version_bindings": [
    "claim_id",
    "owner",
    "policy",
    "source_version",
    "claim_version",
    "evidence_refs"
  ],
  "content_policy": "identifiers_only_no_source_content_or_runtime_data",
  "contract_version": "odysseus.causal_claim_store_contract.v1",
  "epistemic_transitions": [
    [
      "candidate",
      "under_review"
    ],
    [
      "under_review",
      "supported"
    ],
    [
      "under_review",
      "contested"
    ],
    [
      "under_review",
      "retracted"
    ],
    [
      "supported",
      "retracted"
    ],
    [
      "supported",
      "superseded"
    ],
    [
      "contested",
      "retracted"
    ],
    [
      "contested",
      "superseded"
    ]
  ],
  "operation_gates": {
    "candidate_generation": "not_implemented_by_this_contract",
    "persistence": "prohibited",
    "runtime_query_or_lens": "prohibited",
    "source_or_provider_access": "prohibited"
  },
  "prohibitions": [
    "graph_or_memory_write",
    "usi_causal_truth_or_review_write"
  ],
  "relation_bindings": {
    "alternative_cause": [
      "claim_ref",
      "alternative_claim_ref",
      "evidence_refs"
    ],
    "conflict": [
      "claim_ref",
      "conflicting_claim_ref",
      "evidence_refs"
    ],
    "retraction": [
      "claim_ref",
      "prior_version",
      "reason",
      "evidence_refs",
      "reviewer"
    ],
    "supersession": [
      "old_claim_ref",
      "new_claim_ref",
      "evidence_refs",
      "monotonic_version"
    ]
  },
  "supported_review_bindings": [
    "under_review_to_supported",
    "reviewer_must_differ_from_candidate_actor",
    "reviewer_must_differ_from_claim_owner",
    "evidence_refs_required"
  ],
  "terminal_states": [
    "retracted",
    "superseded"
  ],
  "usi": "derived_rebuildable_projection_only"
}
```
<!-- NMG-05-CONTRACT:END -->
