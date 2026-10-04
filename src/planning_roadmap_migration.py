"""Deterministic, read-only adapters for the Harbour One roadmap migration."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
import os

from src.planning_definition_contract import (
    compute_roadmap_content_hash,
    validate_planning_definition,
    validate_repository_path,
)
from src.planning_revision_store import (
    DurablePlanningRevisionStore,
    DurablePlanningStoreError,
    PlanningMigrationReceipt,
)


MIGRATION_MANIFEST_SCHEMA_V1 = "odysseus.harbour.planning_migration_manifest.v1"
MIGRATION_MANIFEST_SCHEMA_V2 = "odysseus.harbour.planning_migration_manifest.v2"
MIGRATION_MANIFEST_SCHEMA = MIGRATION_MANIFEST_SCHEMA_V2
HISTORICAL_AUTHORITY_MAP_ID = "h1r03-historical-v1"
SUCCESSOR_AUTHORITY_MAP_ID = "h1r03-successor-v2"
DOCS_ONLY_AUTHORITY_MAP_ID = "h1r04-docs-only-v3"
REPO_ONLY_AUTHORITY_MAP_ID = "h1r05-repo-only-v4"
H1R04_ACCEPTED_DOCUMENT_PATH = "docs/plans/harbour-one-h1r-04-current-boundary-reconciliation-2026-08-30.json"
H1R04_ACCEPTED_DOCUMENT_RAW_BLOB = "5248817e49784cf363ff40c6c9996caa9272493d"
DOCS_ONLY_PRODUCT_RAW_BLOB = "ba7914f8174361d7d1528418748c4852345f0cd8"
H1R05_ACCEPTANCE_RECEIPT_PATH = "docs/plans/evidence/harbour-one-h1r05-repo-acceptance-20261003.receipt"
REPO_ONLY_PRODUCT_RAW_BLOB = "65e88e5775f6ca4b721e0c0906577d0a16bc7113"
H1R05_ACCEPTANCE_RECEIPT_RAW_BLOB = "4e9bc7fb6c84f6c01f169e145539caf33bc7b2bf"
OWNER_ID = "fuzzy"
PROJECT_ID = "harbour-one"
SUPPORTING_PATH = "specs/roadmaps/roadmap-reconciliation-report-reduction.v1.json"

FROZEN_MEMBERS = (
    ("specs/roadmaps/abc-roadmap-continuation-runtime.v1.json", "legacy_slices_v1", "abc-roadmap-continuation-runtime", "891d366032cf8e4e80f664bf14dde4428fbb99e0", "9d1e6a876f9a4ada5f60f20c2cb2f0cdb078a987d88c81ed50920410c8085f59"),
    ("specs/roadmaps/abc-roadmap-reconciliation-import.v1.json", "legacy_slices_v1", "abc-roadmap-reconciliation-import", "0176451f6ad0a9843fd813345817e98d95089db7", "381d0dfd2950875a2d3186a82c7ab3f2e12adf64a5029b8b50a584a0da891d74"),
    ("specs/roadmaps/odysseus-multiagent-roadmap.v1.json", "legacy_graph_v1", "odysseus-multiagent-roadmap", "1a994940febb7a7ea2b06247ec2ae88742f4ddb0", "8fd864ac34bf2df34d1f2b3411b68bb3efe6e5e349a2d22dccd06685082cd2b0"),
    ("specs/roadmaps/orca-memory-graph-migration-roadmap.v1.json", "legacy_graph_v1", "orca-memory-graph-migration", "1215ecb2be52d33695577ea1a0c25bd328bc732b", "97732698bfdb201f344c3a165b8781a3bf9225ed8be23043880840d8079eaf80"),
    ("specs/roadmaps/orca-memory-live-activation-roadmap.v1.json", "legacy_graph_v1", "orca-memory-live-activation", "1d30f115ae228dc90c5c7ce2bb0df6bffd538ec2", "3338e6f44539884001b3614795d90411d7df6ef45a88ed5f373c2fe79625d786"),
    ("specs/roadmaps/private-data-memory-ingestion-roadmap.v1.json", "legacy_graph_v1", "private-data-memory-ingestion", "2b6dd5220f94e4394ef4cbb19df6bc15dcb2824d", "5b2959ec52e2159822d4cbd7a111081bed12eb1987a9eeb0e0bc58964a9c9c81"),
)

# Frozen raw bytes remain the manifest identity.  These literal alternatives
# admit only the known all-LF checkout representation of historical text
# sources; they do not derive or accept arbitrary newline transformations.
FROZEN_MEMBER_SHA256_VARIANTS = {
    "specs/roadmaps/abc-roadmap-continuation-runtime.v1.json": frozenset({
        "9d1e6a876f9a4ada5f60f20c2cb2f0cdb078a987d88c81ed50920410c8085f59",
    }),
    "specs/roadmaps/abc-roadmap-reconciliation-import.v1.json": frozenset({
        "381d0dfd2950875a2d3186a82c7ab3f2e12adf64a5029b8b50a584a0da891d74",
    }),
    "specs/roadmaps/odysseus-multiagent-roadmap.v1.json": frozenset({
        "8fd864ac34bf2df34d1f2b3411b68bb3efe6e5e349a2d22dccd06685082cd2b0",
        "1432fe063b6747be4780960943871daf57868680c98653ed6b7646ee23b21973",
    }),
    "specs/roadmaps/orca-memory-graph-migration-roadmap.v1.json": frozenset({
        "97732698bfdb201f344c3a165b8781a3bf9225ed8be23043880840d8079eaf80",
    }),
    "specs/roadmaps/orca-memory-live-activation-roadmap.v1.json": frozenset({
        "3338e6f44539884001b3614795d90411d7df6ef45a88ed5f373c2fe79625d786",
        "c769b3a0a8dd3d77ef3c45dbabd2d84199bc1734cb1ff3a863dc6c973f7bf536",
    }),
    "specs/roadmaps/private-data-memory-ingestion-roadmap.v1.json": frozenset({
        "5b2959ec52e2159822d4cbd7a111081bed12eb1987a9eeb0e0bc58964a9c9c81",
        "5cdb145b8ad6fdab079bebff6ae843d02a796556e177f200a52778311344a8cf",
    }),
}

SUPPORTING_PIN = (SUPPORTING_PATH, "ddf2bc2a5a04017f0616d7bb0857431a70687489de0a9ed0b00a8d393f7954f3")
AUTHORITY_PINS = {
    "docs/plans/harbour-one-h1r-03-source-census-freeze-2026-08-29.json": "2bcded5358bb2915f4feea8ce5d2f91ccace781b",
    "docs/plans/harbour-one-h1r-03-source-role-census-2026-08-29.json": "845328bddcf92820f432ae09aef84c7b3ec28b88",
    "docs/plans/harbour-one-h1r-03-duplicate-identity-disposition-2026-08-29.json": "da073d585168f11bde3dfccca5db2c30028efc0d",
    "docs/plans/harbour-one-h1r-03-target-binding-2026-08-29.json": "f1c9a4165a939ddf354e8ff0c44f2ee14a351630",
    "src/planning_definition_contract.py": "53cf0d871ffb3ffaf51a76f47734045d8a4509df",
    "specs/planning_definition.v2.schema.json": "ae73b54ce93eb9448024d6237f3c2a10debd9fcb",
    "docs/plans/planning-definition-v2-contract.md": "1ec60d0849a56812b439c9811e8707243e46f348",
    # Historical pre-review pin: 7b59b0097a3a847d6d9e1669cb9970c04abdbb7b.
    # The reviewed current product roadmap is the authority for this migration.
    "docs/plans/harbour-one-product-roadmap.json": "e4faf54dcc3a0c971e3f4c8152bbf61b99a35bbf",
    "src/harbour_authority.py": "ff90d569869d026f806a8c6c98d0701f597f8ff1",
    "tests/test_harbour_authority.py": "b7f87b698f80185bc433d3612c3bd84b423869ea",
}
HISTORICAL_AUTHORITY_PINS = AUTHORITY_PINS
SUCCESSOR_AUTHORITY_PINS = {
    **HISTORICAL_AUTHORITY_PINS,
    "docs/plans/harbour-one-product-roadmap.json": "8aa1aecf4985c1696430654ed299819f0ae193e5",
}
DOCS_ONLY_AUTHORITY_PINS = {
    **SUCCESSOR_AUTHORITY_PINS,
    "docs/plans/harbour-one-product-roadmap.json": DOCS_ONLY_PRODUCT_RAW_BLOB,
    H1R04_ACCEPTED_DOCUMENT_PATH: H1R04_ACCEPTED_DOCUMENT_RAW_BLOB,
}
REPO_ONLY_AUTHORITY_PINS = {
    **DOCS_ONLY_AUTHORITY_PINS,
    "docs/plans/harbour-one-product-roadmap.json": REPO_ONLY_PRODUCT_RAW_BLOB,
    H1R05_ACCEPTANCE_RECEIPT_PATH: H1R05_ACCEPTANCE_RECEIPT_RAW_BLOB,
}
AUTHORITY_MAP_REGISTRY = {
    HISTORICAL_AUTHORITY_MAP_ID: (MIGRATION_MANIFEST_SCHEMA_V1, HISTORICAL_AUTHORITY_PINS),
    SUCCESSOR_AUTHORITY_MAP_ID: (MIGRATION_MANIFEST_SCHEMA_V2, SUCCESSOR_AUTHORITY_PINS),
    DOCS_ONLY_AUTHORITY_MAP_ID: (MIGRATION_MANIFEST_SCHEMA_V2, DOCS_ONLY_AUTHORITY_PINS),
    REPO_ONLY_AUTHORITY_MAP_ID: (MIGRATION_MANIFEST_SCHEMA_V2, REPO_ONLY_AUTHORITY_PINS),
}

_V1_IDENTITY_FIELDS = frozenset({
    "schema", "owner_id", "project_id", "source_items", "supporting_relations",
    "normalized_payloads", "authority_pins", "structural_census",
})
_V2_IDENTITY_FIELDS = _V1_IDENTITY_FIELDS | {"authority_map_id"}


def resolve_migration_manifest_identity(identity: object) -> tuple[str, dict[str, str]]:
    """Resolve one exact historical-v1 or registered v2 authority identity.

    Callers share this closed schema/map check so persisted batches and the
    read-only source inventory cannot silently accept hybrid identities.
    """
    if type(identity) is not dict:
        raise ValueError("migration manifest identity must be an exact object")
    schema = identity.get("schema")
    if schema == MIGRATION_MANIFEST_SCHEMA_V1:
        if set(identity) != _V1_IDENTITY_FIELDS:
            raise ValueError("historical migration identity shape")
        map_id = HISTORICAL_AUTHORITY_MAP_ID
    elif schema == MIGRATION_MANIFEST_SCHEMA_V2:
        if set(identity) != _V2_IDENTITY_FIELDS:
            raise ValueError("successor migration identity shape")
        map_id = identity.get("authority_map_id")
    else:
        raise ValueError("unknown migration manifest schema")
    if type(map_id) is not str:
        raise ValueError("unknown migration authority map")
    registered = AUTHORITY_MAP_REGISTRY.get(map_id)
    if registered is None or registered[0] != schema:
        raise ValueError("unknown migration authority map")
    pins = registered[1]
    if identity.get("authority_pins") != pins:
        raise ValueError("migration authority map mismatch")
    return map_id, pins


def current_authority_map(repo_root: str | Path) -> str | None:
    """Return the registered map whose exact pinned bytes are present now."""
    root = Path(repo_root).expanduser().resolve(strict=True)
    for map_id in (
        REPO_ONLY_AUTHORITY_MAP_ID, DOCS_ONLY_AUTHORITY_MAP_ID, SUCCESSOR_AUTHORITY_MAP_ID,
        HISTORICAL_AUTHORITY_MAP_ID,
    ):
        _schema, pins = AUTHORITY_MAP_REGISTRY[map_id]
        try:
            if all(
                _authority_blob(path, (root / path).read_bytes(), map_id) == expected
                for path, expected in pins.items()
            ):
                return map_id
        except OSError:
            continue
    return None


def authority_map_relation(batch_map_id: object, current_map_id: object) -> str:
    if (
        type(batch_map_id) is not str or type(current_map_id) is not str
        or batch_map_id not in AUTHORITY_MAP_REGISTRY
        or current_map_id not in AUTHORITY_MAP_REGISTRY
    ):
        return "unknown"
    if batch_map_id == current_map_id:
        return "same"
    known_order = (
        HISTORICAL_AUTHORITY_MAP_ID, SUCCESSOR_AUTHORITY_MAP_ID,
        DOCS_ONLY_AUTHORITY_MAP_ID, REPO_ONLY_AUTHORITY_MAP_ID,
    )
    if known_order.index(batch_map_id) < known_order.index(current_map_id):
        return "successor"
    return "unknown"


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def strict_json_object(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes:
        raise ValueError("source bytes required")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if type(key) is not str or key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    value = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=pairs,
        parse_constant=lambda _item: (_ for _ in ()).throw(ValueError("non-finite number")),
    )
    if type(value) is not dict:
        raise ValueError("source root must be an object")
    return value


def normalize_legacy_timestamp(value: object) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError("invalid timestamp")
    text = value
    if len(text) == 10:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            raise ValueError("invalid timestamp") from None
    else:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError("invalid timestamp") from None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed.microsecond != 0:
        raise ValueError("fractional timestamps are not accepted")
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def map_legacy_roadmap(source: Mapping[str, Any], *, source_path: str, adapter: str) -> dict[str, Any]:
    if type(source) is not dict:
        raise ValueError("source must be an exact object")
    roadmap_id = _identifier(source.get("plan_id"))
    title = _nonempty(source.get("title"), "title")
    objective = source.get("goal")
    if objective is None or (type(objective) is str and not objective.strip()):
        objective = title
    objective = _nonempty(objective, "objective")
    constraints = _stable_texts(source.get("non_goals"), source.get("stop_rules"), source.get("global_stop_rules"))
    created = normalize_legacy_timestamp(source.get("created_at"))
    updated = normalize_legacy_timestamp(source.get("updated_at", source.get("created_at")))
    if adapter == "legacy_slices_v1":
        nodes, edges, rules = _map_slices(source)
    elif adapter == "legacy_graph_v1":
        nodes, edges, rules = _map_graph(source, source_path=source_path)
    else:
        raise ValueError("unknown adapter")
    refs = [validate_repository_path(source_path, path="source_path")]
    if roadmap_id == "abc-roadmap-reconciliation-import":
        refs.append(SUPPORTING_PATH)
    roadmap = {
        "roadmap_id": roadmap_id,
        "project_id": PROJECT_ID,
        "revision": 1,
        "content_hash": "sha256:" + ("0" * 64),
        "revision_state": "approved",
        "title": title,
        "objective": objective,
        "assumptions": [],
        "constraints": constraints,
        "nodes": nodes,
        "edges": edges,
        "gates": [],
        "done_contract": {
            "required_node_ids": [node["node_id"] for node in nodes],
            "required_gate_ids": [],
            "verification_rules": rules,
            "completion_rule": "all_required_nodes_and_gates",
        },
        "source_refs": refs,
        "created_at": created,
        "updated_at": updated,
    }
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    document = {
        "schema_id": "odysseus.planning.definition.v2",
        "project": {
            "project_id": PROJECT_ID,
            "title": "Harbour One",
            "objective": "Consolidate pinned legacy roadmap definitions under canonical Planning authority.",
            "scope": {
                "in": ["Canonical Planning storage for the bound legacy roadmap."],
                "out": ["Legacy source mutation and live execution."],
            },
            "constraints": [
                "Owner, project, and roadmap identity are explicitly bound.",
                "Legacy source bytes remain immutable evidence.",
            ],
            "roadmap_refs": [roadmap_id],
            "latest_approved_revision": {roadmap_id: {"revision": 1, "content_hash": roadmap["content_hash"]}},
            "draft_refs": [],
        },
        "roadmaps": [roadmap],
    }
    validate_planning_definition(document)
    return document


def build_h1r03_migration_manifest(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).expanduser().resolve(strict=True)
    for path, expected_blob in REPO_ONLY_AUTHORITY_PINS.items():
        try:
            raw = (root / path).read_bytes()
        except OSError:
            raise DurablePlanningStoreError("stale_authority") from None
        if _authority_blob(path, raw, REPO_ONLY_AUTHORITY_MAP_ID) != expected_blob:
            raise DurablePlanningStoreError("stale_authority")
    census, supporting_observation = build_h1r03_structural_census(root)
    items: list[dict[str, Any]] = []
    normalized: dict[str, str] = {}
    for path, adapter, roadmap_id, blob, sha in FROZEN_MEMBERS:
        raw = (root / path).read_bytes()
        if (
            _git_blob(raw) != blob
            or hashlib.sha256(raw).hexdigest() not in FROZEN_MEMBER_SHA256_VARIANTS[path]
        ):
            raise DurablePlanningStoreError("stale_authority")
        source = strict_json_object(raw)
        if source.get("plan_id") != roadmap_id:
            raise DurablePlanningStoreError("validation_failed")
        document = map_legacy_roadmap(source, source_path=path, adapter=adapter)
        payload = canonical_json_bytes(document)
        normalized[roadmap_id] = payload.decode("utf-8")
        items.append({
            "path": path, "role": "primary", "adapter": adapter,
            "roadmap_id": roadmap_id, "blob": blob, "sha256": sha,
            "payload_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "content_hash": document["roadmaps"][0]["content_hash"],
        })
    supporting_raw = (root / SUPPORTING_PATH).read_bytes()
    if hashlib.sha256(supporting_raw).hexdigest() != SUPPORTING_PIN[1]:
        raise DurablePlanningStoreError("stale_authority")
    strict_json_object(supporting_raw)
    body = {
        "schema": MIGRATION_MANIFEST_SCHEMA_V2,
        "authority_map_id": REPO_ONLY_AUTHORITY_MAP_ID,
        "owner_id": OWNER_ID,
        "project_id": PROJECT_ID,
        "authority_pins": deepcopy(REPO_ONLY_AUTHORITY_PINS),
        "structural_census": census,
        "supporting_observation": supporting_observation,
        "source_items": items,
        "supporting_relations": [{
            "path": SUPPORTING_PATH,
            "role": "supporting_relation_only",
            "target_roadmap_id": "abc-roadmap-reconciliation-import",
            "sha256": SUPPORTING_PIN[1],
        }],
        "normalized_payloads": normalized,
    }
    identity = {key: value for key, value in body.items() if key not in {"supporting_observation"}}
    digest = "sha256:" + hashlib.sha256(canonical_json_bytes(identity)).hexdigest()
    return {**body, "manifest_digest": digest, "batch_id": "planning-migration-batch:" + digest[7:]}


def build_h1r03_structural_census(repo_root: str | Path) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Return path/role-only membership identity and a separate support hash observation."""
    root = Path(repo_root).expanduser().resolve(strict=True)
    primary_by_path = {path: roadmap_id for path, _adapter, roadmap_id, _blob, _sha in FROZEN_MEMBERS}
    relation_paths = {SUPPORTING_PATH}
    exact_authorities = set(HISTORICAL_AUTHORITY_PINS)
    rows: list[dict[str, str]] = []
    support_observations: list[tuple[str, str]] = []
    seen_plan_ids: dict[str, list[str]] = {}
    for relative_root in ("specs/roadmaps", "docs/plans"):
        directory = root / relative_root
        if not directory.is_dir():
            raise DurablePlanningStoreError("stale_authority")
        for dirpath, dirnames, filenames in os.walk(directory):
            dirnames[:] = sorted(name for name in dirnames if name not in {".git", "__pycache__"})
            for filename in filenames:
                ext = Path(filename).suffix.lower()
                if ext not in {".json", ".md", ".markdown"}:
                    continue
                absolute = Path(dirpath) / filename
                path = absolute.relative_to(root).as_posix()
                kind = (
                    "roadmap_json" if path.startswith("specs/roadmaps/") and ext == ".json"
                    else "roadmap_doc" if path.startswith("specs/roadmaps/")
                    else "planning_doc_json" if ext == ".json"
                    else "planning_doc"
                )
                if path in primary_by_path:
                    role = "primary_import_candidate"
                elif path in relation_paths:
                    role = "supporting_relation"
                elif ext in {".md", ".markdown"}:
                    role = "historical_evidence"
                elif path in exact_authorities:
                    role = "control_plane_authority"
                elif path.startswith("specs/roadmaps/"):
                    role = "primary_import_candidate"
                else:
                    role = "supporting_plan_source"
                rows.append({
                    "source_id": _stable_source_id(path), "path": path,
                    "kind": kind, "extension": ext.lstrip("."),
                    "structural_role": role,
                })
                if role in {"supporting_plan_source", "control_plane_authority"}:
                    try:
                        raw = absolute.read_bytes()
                    except OSError:
                        raise DurablePlanningStoreError("stale_authority") from None
                    support_observations.append((path, hashlib.sha256(raw).hexdigest()))
                if path.startswith("specs/roadmaps/") and ext == ".json":
                    try:
                        parsed = strict_json_object(absolute.read_bytes())
                        plan_id = parsed.get("plan_id")
                        if type(plan_id) is not str or not plan_id.strip():
                            raise ValueError
                        seen_plan_ids.setdefault(plan_id, []).append(path)
                    except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
                        raise DurablePlanningStoreError("stale_authority") from None
    rows.sort(key=lambda item: (item["source_id"], item["path"], item["kind"], item["extension"], item["structural_role"]))
    expected_specs = set(primary_by_path) | relation_paths
    actual_specs = {row["path"] for row in rows if row["path"].startswith("specs/roadmaps/") and row["extension"] == "json"}
    if actual_specs != expected_specs:
        raise DurablePlanningStoreError("stale_authority")
    expected_duplicate = {"abc-roadmap-reconciliation-import": {
        "specs/roadmaps/abc-roadmap-reconciliation-import.v1.json", SUPPORTING_PATH,
    }}
    for plan_id, paths in seen_plan_ids.items():
        if len(paths) > 1 and set(paths) != expected_duplicate.get(plan_id):
            raise DurablePlanningStoreError("stale_authority")
    if set(seen_plan_ids) != {roadmap_id for roadmap_id in primary_by_path.values()}:
        raise DurablePlanningStoreError("stale_authority")
    support_observations.sort()
    observation = {
        "count": len(support_observations),
        "digest": "sha256:" + hashlib.sha256(canonical_json_bytes(support_observations)).hexdigest(),
    }
    return rows, observation


def _stable_source_id(path: str) -> str:
    return "repo-plan:" + hashlib.sha256(path.lower().encode("utf-8")).hexdigest()[:16]


def prepare_h1r03_migration(
    store: DurablePlanningRevisionStore, principal: object, *, repo_root: str | Path
) -> PlanningMigrationReceipt:
    return store.prepare_migration(principal, manifest=build_h1r03_migration_manifest(repo_root))


def accept_h1r03_migration(
    store: DurablePlanningRevisionStore, principal: object, *, repo_root: str | Path,
    batch_id: str,
) -> PlanningMigrationReceipt:
    # The locked transition handles exact receipt replay before evaluating this
    # callback, then validates a fresh complete manifest for a new acceptance.
    return store.accept_migration(
        principal,
        batch_id=batch_id,
        current_manifest=lambda: build_h1r03_migration_manifest(repo_root),
    )


def render_planning_definition_markdown(payload: Mapping[str, Any]) -> str:
    """Render a deterministic read-only view; no parser or source save-back exists."""
    document = deepcopy(dict(payload))
    validate_planning_definition(document)
    roadmap = document["roadmaps"][0]
    lines = [
        "<!-- GENERATED READ-ONLY PLANNING VIEW; DO NOT EDIT -->",
        f"# {roadmap['title']}", "", roadmap["objective"], "", "## Nodes", "",
    ]
    for node in roadmap["nodes"]:
        lines.extend((f"- `{node['node_id']}` — {node['title']}", f"  - {node['objective']}"))
    lines.extend(("", "## Source references", ""))
    lines.extend(f"- `{source_ref}`" for source_ref in roadmap["source_refs"])
    return "\n".join(lines) + "\n"


def _map_slices(source: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    slices = source.get("slices")
    if type(slices) is not list or not slices:
        raise ValueError("slices must be a non-empty list")
    nodes: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in slices:
        if type(item) is not dict:
            raise ValueError("slice must be object")
        node_id = _identifier(item.get("slice_id"))
        if node_id in seen:
            raise ValueError("duplicate slice id")
        seen.add(node_id)
        done = _flat_strings(item.get("done_when"), "done_when")
        paths = _paths(item.get("allowed_files"))
        own_rules = []
        for index, description in enumerate(done):
            rule_id = f"done:{node_id}:{index:03d}"
            own_rules.append(rule_id)
            rules.append({"rule_id": rule_id, "kind": "static", "description": description})
        nodes.append({
            "node_id": node_id, "kind": "work", "title": node_id,
            "objective": _nonempty(item.get("objective"), "objective"),
            "depends_on": [], "gate_ids": [], "deliverables": done,
            "allowed_paths": paths, "blocked_paths": [], "capability_requirements": [],
            "verification_rule_ids": own_rules,
        })
    return nodes, [], rules


def _map_graph(source: Mapping[str, Any], *, source_path: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    legacy_nodes = source.get("graph_nodes")
    if type(legacy_nodes) is not list or not legacy_nodes:
        raise ValueError("graph_nodes must be a non-empty list")
    nodes: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    ids: list[str] = []
    for item in legacy_nodes:
        if type(item) is not dict:
            raise ValueError("graph node must be object")
        node_id = _identifier(item.get("id"))
        if node_id in ids:
            raise ValueError("duplicate graph node")
        ids.append(node_id)
        title = _nonempty(item.get("title"), "title")
        objective = item.get("objective")
        if objective is None or (type(objective) is str and not objective.strip()):
            objective = title
        descriptions = _stable_texts(item.get("gates"), item.get("verification"))
        if not descriptions:
            descriptions = [f"Verify {node_id} against pinned source {source_path}."]
        own_rules = []
        for index, description in enumerate(descriptions):
            rule_id = f"legacy:{node_id}:{index:03d}"
            own_rules.append(rule_id)
            rules.append({"rule_id": rule_id, "kind": "static", "description": description})
        nodes.append({
            "node_id": node_id, "kind": "work", "title": title,
            "objective": _nonempty(objective, "objective"), "depends_on": [], "gate_ids": [],
            "deliverables": _flat_strings(item.get("deliverables", []), "deliverables"),
            "allowed_paths": [], "blocked_paths": [], "capability_requirements": [],
            "verification_rule_ids": own_rules,
        })
    id_set = set(ids)
    raw_edges = source.get("graph_edges")
    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    if type(raw_edges) is list and raw_edges:
        candidates = [(edge.get("from"), edge.get("to"), edge.get("kind")) if type(edge) is dict else (None, None, None) for edge in raw_edges]
    else:
        candidates = []
        for item in legacy_nodes:
            node_id = item["id"]
            for dependency in _flat_strings(item.get("depends_on", []), "depends_on"):
                candidates.append((node_id, dependency, "depends_on"))
            for target in _flat_strings(item.get("unlocks", []), "unlocks"):
                candidates.append((node_id, target, "unlocks"))
    for left, right, kind in candidates:
        left = _identifier(left); right = _identifier(right)
        if kind not in {"depends_on", "blocks", "unlocks"} or left not in id_set or right not in id_set:
            raise ValueError("invalid graph edge")
        triple = (left, right, kind)
        if triple not in seen_edges:
            edges.append({"from": left, "to": right, "kind": kind})
            seen_edges.add(triple)
    depends = {node_id: [] for node_id in ids}
    for edge in edges:
        if edge["kind"] == "depends_on":
            depends[edge["from"]].append(edge["to"])
    for node in nodes:
        node["depends_on"] = depends[node["node_id"]]
    return nodes, edges, rules


def _flat_strings(value: object, field: str) -> list[str]:
    if type(value) is str:
        values = [value]
    elif type(value) is list and all(type(item) is str for item in value):
        values = value
    else:
        raise ValueError(f"{field} must be a string or flat string list")
    result: list[str] = []
    for item in values:
        if not item.strip():
            continue
        if item not in result:
            result.append(item)
    return result


def _stable_texts(*values: object) -> list[str]:
    result: list[str] = []
    for value in values:
        if value is None:
            continue
        for item in _flat_strings(value, "text list"):
            if item not in result:
                result.append(item)
    return result


def _paths(value: object) -> list[str]:
    result: list[str] = []
    for raw in _flat_strings(value, "allowed_files"):
        repaired = raw[:-1] if raw.endswith("/") else raw
        path = validate_repository_path(repaired, path="allowed_files")
        if path not in result:
            result.append(path)
    return result


def _nonempty(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _identifier(value: object) -> str:
    if type(value) is not str or not value or len(value) > 128:
        raise ValueError("invalid identifier")
    if not value[0].isalnum() or any(not (character.isalnum() or character in "._:-") for character in value):
        raise ValueError("invalid identifier")
    return value


def _git_blob(raw: bytes) -> str:
    # Preserve the historical text=auto pin convention for existing sources.
    content = raw.replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob " + str(len(content)).encode("ascii") + b"\0" + content).hexdigest()


def _raw_git_blob(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def _authority_blob(path: str, raw: bytes, map_id: str) -> str:
    if path == "docs/plans/harbour-one-product-roadmap.json" and map_id in {
        SUCCESSOR_AUTHORITY_MAP_ID, DOCS_ONLY_AUTHORITY_MAP_ID, REPO_ONLY_AUTHORITY_MAP_ID,
    }:
        return _raw_git_blob(raw)
    if map_id in {DOCS_ONLY_AUTHORITY_MAP_ID, REPO_ONLY_AUTHORITY_MAP_ID} and path == H1R04_ACCEPTED_DOCUMENT_PATH:
        return _raw_git_blob(raw)
    if map_id == REPO_ONLY_AUTHORITY_MAP_ID and path == H1R05_ACCEPTANCE_RECEIPT_PATH:
        return _raw_git_blob(raw)
    return _git_blob(raw)


__all__ = [
    "AUTHORITY_MAP_REGISTRY", "AUTHORITY_PINS", "DOCS_ONLY_AUTHORITY_MAP_ID",
    "DOCS_ONLY_AUTHORITY_PINS", "DOCS_ONLY_PRODUCT_RAW_BLOB", "FROZEN_MEMBERS",
    "FROZEN_MEMBER_SHA256_VARIANTS",
    "H1R04_ACCEPTED_DOCUMENT_PATH", "H1R04_ACCEPTED_DOCUMENT_RAW_BLOB",
    "H1R05_ACCEPTANCE_RECEIPT_PATH", "H1R05_ACCEPTANCE_RECEIPT_RAW_BLOB",
    "HISTORICAL_AUTHORITY_MAP_ID", "HISTORICAL_AUTHORITY_PINS",
    "MIGRATION_MANIFEST_SCHEMA", "MIGRATION_MANIFEST_SCHEMA_V1",
    "MIGRATION_MANIFEST_SCHEMA_V2", "OWNER_ID", "PROJECT_ID",
    "REPO_ONLY_AUTHORITY_MAP_ID", "REPO_ONLY_AUTHORITY_PINS", "REPO_ONLY_PRODUCT_RAW_BLOB",
    "SUCCESSOR_AUTHORITY_MAP_ID", "SUCCESSOR_AUTHORITY_PINS",
    "SUPPORTING_PATH", "accept_h1r03_migration", "build_h1r03_migration_manifest",
    "authority_map_relation", "canonical_json_bytes", "current_authority_map",
    "map_legacy_roadmap", "normalize_legacy_timestamp", "resolve_migration_manifest_identity",
    "prepare_h1r03_migration", "render_planning_definition_markdown", "strict_json_object",
]
