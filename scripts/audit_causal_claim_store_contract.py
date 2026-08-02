"""Validate the content-free Causal Claim Store contract without side effects."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


BEGIN = "<!-- NMG-05-CONTRACT:BEGIN -->"
END = "<!-- NMG-05-CONTRACT:END -->"
EXPECTED_CONTRACT = {
    "candidate_sources": {
        "co_occurrence": "cannot_promote_to_supported",
        "correlation": "cannot_promote_to_supported",
        "model_suggestion": "cannot_promote_to_supported",
    },
    "causal_claim_store": {
        "review_owner": "sole_independent_review_owner",
        "truth_owner": "sole_causal_domain_truth_owner",
    },
    "content_policy": "identifiers_only_no_source_content_or_runtime_data",
    "contract_version": "odysseus.causal_claim_store_contract.v1",
    "claim_identity_version_bindings": ["claim_id", "owner", "policy", "source_version", "claim_version", "evidence_refs"],
    "epistemic_transitions": [
        ["candidate", "under_review"], ["under_review", "supported"], ["under_review", "contested"],
        ["under_review", "retracted"], ["supported", "retracted"], ["supported", "superseded"],
        ["contested", "retracted"], ["contested", "superseded"],
    ],
    "terminal_states": ["retracted", "superseded"],
    "operation_gates": {
        "candidate_generation": "not_implemented_by_this_contract",
        "persistence": "prohibited",
        "runtime_query_or_lens": "prohibited",
        "source_or_provider_access": "prohibited",
    },
    "prohibitions": ["graph_or_memory_write", "usi_causal_truth_or_review_write"],
    "relation_bindings": {
        "alternative_cause": ["claim_ref", "alternative_claim_ref", "evidence_refs"],
        "conflict": ["claim_ref", "conflicting_claim_ref", "evidence_refs"],
        "retraction": ["claim_ref", "prior_version", "reason", "evidence_refs", "reviewer"],
        "supersession": ["old_claim_ref", "new_claim_ref", "evidence_refs", "monotonic_version"],
    },
    "supported_review_bindings": ["under_review_to_supported", "reviewer_must_differ_from_candidate_actor", "reviewer_must_differ_from_claim_owner", "evidence_refs_required"],
    "usi": "derived_rebuildable_projection_only",
}


class ContractError(ValueError):
    pass


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def extract_manifest(document: str) -> tuple[dict[str, Any], str]:
    if document.count(BEGIN) != 1 or document.count(END) != 1:
        raise ContractError("contract manifest markers must occur exactly once")
    begin, end = document.index(BEGIN), document.index(END)
    if begin >= end:
        raise ContractError("contract manifest markers are out of order")
    section = document[begin + len(BEGIN) : end].strip()
    if not section.startswith("```json\n") or not section.endswith("\n```"):
        raise ContractError("contract manifest must be a JSON code block")
    raw = section[len("```json\n") : -len("\n```")]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContractError("contract manifest is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ContractError("contract manifest must be an object")
    return payload, raw


def validate_contract(payload: Any) -> dict[str, Any]:
    if payload != EXPECTED_CONTRACT:
        raise ContractError("contract does not match the approved causal tuples")
    return EXPECTED_CONTRACT


def load_contract(path: Path) -> tuple[dict[str, Any], str]:
    try:
        document = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError("contract is unavailable") from exc
    payload, raw = extract_manifest(document)
    return validate_contract(payload), raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=Path("docs/plans/causal-claim-store-contract.md"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        payload, raw = load_contract(args.contract)
        canonical = canonical_json(payload).rstrip("\n")
        if args.check and raw != canonical:
            raise ContractError("contract manifest is not in deterministic canonical form")
    except ContractError as exc:
        parser.error(str(exc))
    print(canonical_json(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
