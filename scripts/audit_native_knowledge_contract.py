"""Validate the content-free Native Knowledge / Personal Docs boundary contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


BEGIN = "<!-- NMG-01-CONTRACT:BEGIN -->"
END = "<!-- NMG-01-CONTRACT:END -->"
EXPECTED_CONTRACT = {
    "content_policy": "identifiers_only_no_source_content_or_runtime_data",
    "contract_version": "odysseus.native_knowledge_personal_docs_boundary.v1",
    "current_personal_docs": {
        "exact_read_owner": "personal_docs_owner_reader",
        "truth_owner": "personal_docs_sole_current_truth",
        "write_owner": "personal_docs_sole_current_writer",
    },
    "future_native_knowledge": {
        "adapter": "UDA-05_sole_future_adapter_after_NMG-02_and_explicit_activation_scope",
        "exact_read": "blocked_until_NMG-02_exact_reader",
        "state": "not_selected_no_current_truth",
    },
    "legacy_plugin": {
        "disposition": "excluded",
        "identifiers": [
            "plugin.obsidian.memory",
            "plugin.obsidian.orca",
            "plugin.obsidian.raptor",
        ],
    },
    "migration_cutover": {
        "authorization": "separately_explicit_migration_and_live_gate_required",
        "default_state": "disabled_no_migration_selected",
        "required_evidence": "exact_source_scope_version_locator_owner_policy_parity_tombstone_delete_rollback_cutover_state",
    },
    "operation_gates": {
        "adapter_registration": "prohibited_until_NMG-02_and_UDA-05_dependency",
        "migration_cutover": "disabled_until_separately_explicit_migration_and_USI-LIVE-ACTIVATION",
        "native_knowledge_activation": "disabled_until_NMG-02_and_explicit_activation_scope",
        "productive_indexing": "disabled_until_USI-LIVE-ACTIVATION",
        "productive_source_access": "prohibited_by_this_contract",
    },
    "prohibitions": [
        "automatic_import",
        "dual_write",
        "mirror",
        "mixed_query_peer",
        "native_knowledge_fallback",
        "parallel_truth",
    ],
    "usi": {
        "domain_truth": "prohibited",
        "role": "index_identity_and_provenance_only",
        "writer": "prohibited",
    },
}


class ContractError(ValueError):
    """Raised for malformed, non-canonical, or unsafe contract input."""


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def extract_manifest(document: str) -> tuple[dict[str, Any], str]:
    """Extract the bounded JSON manifest without reading any product source."""
    if document.count(BEGIN) != 1 or document.count(END) != 1:
        raise ContractError("contract manifest markers must occur exactly once")
    begin_index = document.index(BEGIN)
    end_index = document.index(END)
    if begin_index >= end_index:
        raise ContractError("contract manifest markers are out of order")
    section = document[begin_index + len(BEGIN) : end_index].strip()
    if not section.startswith("```json\n") or not section.endswith("\n```"):
        raise ContractError("contract manifest must be a JSON code block")
    raw_json = section[len("```json\n") : -len("\n```")]
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ContractError("contract manifest is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ContractError("contract manifest must be an object")
    return payload, raw_json


def validate_contract(payload: Any) -> dict[str, Any]:
    """Require every contract field to match its approved semantic tuple exactly."""
    if payload != EXPECTED_CONTRACT:
        raise ContractError("contract does not match the approved boundary tuples")
    return EXPECTED_CONTRACT


def load_contract(path: Path) -> tuple[dict[str, Any], str]:
    try:
        document = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError("contract is unavailable") from exc
    payload, raw_json = extract_manifest(document)
    return validate_contract(payload), raw_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=Path("docs/plans/native-knowledge-personal-docs-boundary-contract.md"))
    parser.add_argument("--check", action="store_true", help="require canonical manifest formatting without writing")
    args = parser.parse_args(argv)
    try:
        payload, raw_json = load_contract(args.contract)
        canonical = canonical_json(payload).rstrip("\n")
        if args.check and raw_json != canonical:
            raise ContractError("contract manifest is not in deterministic canonical form")
    except ContractError as exc:
        parser.error(str(exc))
    print(canonical_json(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
