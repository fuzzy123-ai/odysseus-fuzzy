"""Validate the declarative native-knowledge legacy-boundary inventory.

The audit intentionally reads only its supplied JSON inventory.  It does not
inspect product sources, import runtime code, contact providers, or write files.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "odysseus.native_knowledge_legacy_boundary_inventory.v1"
CONTENT_POLICY = "identifiers_only_no_source_content_or_runtime_data"
BOUNDARY_KINDS = frozenset({"core", "app", "plugin"})
DISPOSITIONS = frozenset({"retire", "excluded"})
REQUIRED_BOUNDARY_KEYS = frozenset(
    {"boundary_kind", "disposition", "legacy_family", "retirement_gate", "source_identifier"}
)
TOP_LEVEL_KEYS = frozenset({"boundaries", "content_policy", "live_effect", "schema_version"})
EXPECTED_BOUNDARIES = {
    "app.ai_lens": {
        "boundary_kind": "app",
        "disposition": "retire",
        "legacy_family": "lens_projection",
        "retirement_gate": "NMG-07-native-runtime-retirement",
        "source_identifier": "app.ai_lens",
    },
    "app.memory_routes": {
        "boundary_kind": "app",
        "disposition": "retire",
        "legacy_family": "memory_route",
        "retirement_gate": "NMG-07-native-runtime-retirement",
        "source_identifier": "app.memory_routes",
    },
    "core.memory_lifecycle": {
        "boundary_kind": "core",
        "disposition": "retire",
        "legacy_family": "memory_lifecycle",
        "retirement_gate": "NMG-07-native-runtime-retirement",
        "source_identifier": "core.memory_lifecycle",
    },
    "core.memory_store": {
        "boundary_kind": "core",
        "disposition": "retire",
        "legacy_family": "memory_store",
        "retirement_gate": "NMG-07-native-runtime-retirement",
        "source_identifier": "core.memory_store",
    },
    "core.progressive_graph": {
        "boundary_kind": "core",
        "disposition": "retire",
        "legacy_family": "graph_query",
        "retirement_gate": "NMG-07-native-runtime-retirement",
        "source_identifier": "core.progressive_graph",
    },
    "core.rag_pipeline": {
        "boundary_kind": "core",
        "disposition": "retire",
        "legacy_family": "rag_pipeline",
        "retirement_gate": "NMG-07-native-runtime-retirement",
        "source_identifier": "core.rag_pipeline",
    },
    "core.raptorgraph_candidate_mapping": {
        "boundary_kind": "core",
        "disposition": "retire",
        "legacy_family": "raptorgraph_projection",
        "retirement_gate": "NMG-07-native-runtime-retirement",
        "source_identifier": "core.raptorgraph_candidate_mapping",
    },
    "plugin.obsidian.memory": {
        "boundary_kind": "plugin",
        "disposition": "excluded",
        "legacy_family": "memory_runtime",
        "retirement_gate": "not_current_product_runtime",
        "source_identifier": "plugin.obsidian.memory",
    },
    "plugin.obsidian.orca": {
        "boundary_kind": "plugin",
        "disposition": "excluded",
        "legacy_family": "orca_compatibility",
        "retirement_gate": "not_current_product_runtime",
        "source_identifier": "plugin.obsidian.orca",
    },
    "plugin.obsidian.raptor": {
        "boundary_kind": "plugin",
        "disposition": "excluded",
        "legacy_family": "raptor_runtime",
        "retirement_gate": "not_current_product_runtime",
        "source_identifier": "plugin.obsidian.raptor",
    },
}


class InventoryError(ValueError):
    """Raised when an inventory is malformed, incomplete, or unsafe."""


def validate_inventory(payload: Any) -> dict[str, Any]:
    """Return a canonical inventory or raise ``InventoryError`` without side effects."""
    if not isinstance(payload, dict) or set(payload) != TOP_LEVEL_KEYS:
        raise InventoryError("inventory must contain exactly the declared top-level keys")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise InventoryError("unsupported schema version")
    if payload["content_policy"] != CONTENT_POLICY or payload["live_effect"] is not False:
        raise InventoryError("inventory must remain content-free and offline")

    boundaries = payload["boundaries"]
    if not isinstance(boundaries, list):
        raise InventoryError("boundaries must be a list")

    canonical_boundaries: list[dict[str, str]] = []
    for boundary in boundaries:
        if not isinstance(boundary, dict) or set(boundary) != REQUIRED_BOUNDARY_KEYS:
            raise InventoryError("each boundary must contain exactly the declared keys")
        if not all(isinstance(value, str) and value for value in boundary.values()):
            raise InventoryError("boundary values must be non-empty identifiers")
        if boundary["boundary_kind"] not in BOUNDARY_KINDS:
            raise InventoryError("unknown boundary kind")
        if boundary["disposition"] not in DISPOSITIONS:
            raise InventoryError("unknown disposition")
        canonical_boundaries.append(dict(sorted(boundary.items())))

    expected_boundaries = [EXPECTED_BOUNDARIES[source_id] for source_id in sorted(EXPECTED_BOUNDARIES)]
    if canonical_boundaries != expected_boundaries:
        raise InventoryError("inventory does not match the declared legacy boundary tuples")
    if canonical_boundaries != sorted(canonical_boundaries, key=lambda item: item["source_identifier"]):
        raise InventoryError("boundaries must be ordered by source identifier")

    return {
        "boundaries": canonical_boundaries,
        "content_policy": CONTENT_POLICY,
        "live_effect": False,
        "schema_version": SCHEMA_VERSION,
    }


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def load_inventory(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryError("inventory is unavailable or invalid JSON") from exc
    return validate_inventory(payload), raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=Path("docs/plans/native-knowledge-runtime-legacy-boundary-inventory.json"))
    parser.add_argument("--check", action="store_true", help="require canonical committed JSON without writing it")
    args = parser.parse_args(argv)

    try:
        payload, raw = load_inventory(args.inventory)
        canonical = canonical_json(payload)
        if args.check and raw != canonical:
            raise InventoryError("inventory is not in deterministic canonical form")
    except InventoryError as exc:
        parser.error(str(exc))

    print(canonical_json(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
