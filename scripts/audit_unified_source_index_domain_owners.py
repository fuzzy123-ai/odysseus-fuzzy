"""Fail-closed static audit for the UDA-00 domain-owner inventory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence


INVENTORY_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "plans"
    / "unified-source-index-domain-owner-inventory.json"
)

# This is deliberately a closed, content-free recovery contract.  The audit
# must not inspect a domain, provider, private source, index, or runtime.
EXPECTED_PAYLOAD: dict[str, Any] = json.loads(
    r'''{
  "domain_fields": [
    "domain_id",
    "current_selection_state",
    "canonical_truth_owner",
    "write_mutation_owner",
    "exact_reader",
    "delete_lifecycle_owner",
    "provider_seam",
    "review_seam",
    "source_version_tombstone_semantics",
    "usi_role",
    "future_adapter_status",
    "blocker",
    "evidence_refs"
  ],
  "domains": [
    {
      "blocker": "UDA-03 must exclude rejected, deleted, incognito and policy-blocked records, but the current list API does not prove that eligibility filter.",
      "canonical_truth_owner": "src.memory.MemoryManager.memory_file(memory.json)",
      "current_selection_state": "selected_wave_a_core_default_off",
      "delete_lifecycle_owner": "src.memory_provider.NativeMemoryProvider.delete then MemoryManager.save; vector removal is best effort",
      "domain_id": "personal_memory",
      "evidence_refs": [
        "src/memory.py:MemoryManager.load/save/add_entry",
        "src/memory_provider.py:NativeMemoryProvider.remember/list_memories/delete",
        "docs/plans/unified-source-index-domain-adapter-rollout-roadmap.md:UDA-03"
      ],
      "exact_reader": "src.memory_provider.NativeMemoryProvider.list_memories(owner) via src.memory.MemoryManager.load(owner)",
      "future_adapter_status": "UDA-03 after UDA-01 and explicit Memory owner handoff",
      "provider_seam": "NativeMemoryProvider with optional MemoryVectorStore projection",
      "review_seam": "unproven_no_persisted_approved_record_eligibility_reader",
      "source_version_tombstone_semantics": "uuid record id plus owner/source/category/timestamp; no immutable domain revision or domain tombstone; hard delete may only be observed after commit",
      "usi_role": "index_identity_and_provenance_only_no_domain_truth_or_write",
      "write_mutation_owner": "src.memory.MemoryManager.save/add_entry via src.memory_provider.NativeMemoryProvider.remember"
    },
    {
      "blocker": "The current registry persists paths without owner scope and does not prove a source-file mutation/deletion owner.",
      "canonical_truth_owner": "personal_docs_sole_current_truth",
      "current_selection_state": "selected_wave_a_core_default_off",
      "delete_lifecycle_owner": "PersonalDocsManager.exclude_file/remove_directory affect registry and projection only; no physical owner-file delete is proven",
      "domain_id": "personal_docs",
      "evidence_refs": [
        "docs/plans/native-knowledge-personal-docs-boundary-contract.md:current_personal_docs",
        "src/personal_docs.py:PersonalDocsManager",
        "docs/plans/unified-source-index-domain-adapter-rollout-roadmap.md:UDA-04"
      ],
      "exact_reader": "personal_docs_owner_reader",
      "future_adapter_status": "UDA-04 after UDA-01, generic chunk profile and Personal Docs owner handoff",
      "provider_seam": "PersonalDocsManager to RAGManager/VectorRAG compatibility projection",
      "review_seam": "unproven_no_repository_backed_owner_scoped_review_policy_reader",
      "source_version_tombstone_semantics": "file locator, current scan/extraction, directory registry and exclusions; no proven owner-scoped immutable revision or tombstone contract",
      "usi_role": "index_identity_and_provenance_only_no_domain_truth_or_write",
      "write_mutation_owner": "personal_docs_sole_current_writer"
    }
  ],
  "effect_flags": {
    "adapter_registration": false,
    "domain_mutation": false,
    "execution_authorized": false,
    "index_write": false,
    "live_action": false,
    "productive_source_access": false,
    "provider_access": false,
    "synthetic_only": true
  },
  "future_domain_fields": [
    "domain_id",
    "current_selection_state",
    "canonical_truth_owner",
    "exact_reader",
    "future_adapter",
    "fixture_only_source",
    "prohibitions",
    "evidence_refs"
  ],
  "future_domains": [
    {
      "canonical_truth_owner": "none_not_selected",
      "current_selection_state": "not_selected_no_current_truth",
      "domain_id": "native_knowledge",
      "evidence_refs": [
        "docs/plans/native-knowledge-personal-docs-boundary-contract.md:future_native_knowledge",
        "src/native_knowledge_store.py:NativeKnowledgeStore"
      ],
      "exact_reader": "blocked_until_NMG-02_exact_reader_and_explicit_activation_scope",
      "fixture_only_source": "src.native_knowledge_store.NativeKnowledgeStore",
      "future_adapter": "UDA-05_sole_future_adapter_after_NMG-02_and_explicit_activation_scope",
      "prohibitions": [
        "automatic_import",
        "dual_write",
        "mirror",
        "mixed_query_peer",
        "native_knowledge_fallback",
        "parallel_truth"
      ]
    }
  ],
  "kind": "odysseus.unified_source_index.domain_owner_inventory",
  "legacy_excluded_identifiers": [
    "plugin.obsidian.memory",
    "plugin.obsidian.orca",
    "plugin.obsidian.raptor"
  ],
  "schema_version": 1,
  "scope": "selected_wave_a_current_domain_truth_and_mutation_owners_default_off"
}'''
)


class InventoryError(ValueError):
    """The committed static inventory is not the approved recovery contract."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InventoryError("duplicate JSON key")
        result[key] = value
    return result


def load_payload(raw: bytes) -> dict[str, Any]:
    try:
        parsed = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryError("malformed JSON") from exc
    if not isinstance(parsed, dict):
        raise InventoryError("inventory root must be an object")
    return parsed


def canonical_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )


def validate_payload(payload: dict[str, Any]) -> None:
    """Require the exact rows, ordered field lists, exclusions, and false effects."""
    if payload != EXPECTED_PAYLOAD:
        raise InventoryError("inventory differs from the approved recovery contract")


def validate_file(path: Path = INVENTORY_PATH) -> None:
    raw = path.read_bytes()
    payload = load_payload(raw)
    if raw != canonical_bytes(payload):
        raise InventoryError("inventory is not byte-canonical")
    validate_payload(payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="validate the canonical committed inventory read-only"
    )
    args = parser.parse_args(argv)
    if not args.check:
        parser.error("--check is required; this audit never writes inventory data")
    try:
        validate_file()
    except (InventoryError, OSError):
        print("unified-source-index domain owner inventory: FAIL")
        return 1
    print("unified-source-index domain owner inventory: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
