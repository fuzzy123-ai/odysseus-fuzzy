# Planning Definition v2 contract

`odysseus.planning.definition.v2` is the immutable definition boundary between the Planning and Agent surfaces. Planning owns projects, versioned roadmap structure, gate definitions, verification rules, and completion criteria. Agent and Temporal own every mutable execution fact.

## Canonical document

A document is one closed JSON object with exactly `schema_id`, `project`, and `roadmaps`. Unknown fields fail validation. The normative structural schema is `specs/planning_definition.v2.schema.json`; the normative semantic validator is `src/planning_definition_contract.py`.

The project contains identity, objective, scope, constraints, the declared roadmap IDs, exact approved-revision references, and draft base references. Each roadmap revision contains its project reference, revision lifecycle, immutable content hash, definition DAG, gate definitions, done contract, sources, and timezone-qualified timestamps.

Nodes may be `work`, `gate`, `milestone`, or `group`. Edges may be `depends_on`, `blocks`, or `unlocks`. A `depends_on` edge means the `from` node depends on the `to` node. Gate definitions may be `design`, `operator`, `repo`, `live`, `security`, or `dependency`; their `blocks` values must name nodes in the same revision. The completion rule is exactly `all_required_nodes_and_gates`.

All identifiers used as lookup keys are unique within their declared scope. Every project, draft, dependency, edge, gate, verification, and completion reference must resolve. Dependency cycles are invalid. Repository paths are relative, forward-slash paths and cannot traverse or address private repository segments.

## Deterministic revision identity

`content_hash` is `sha256:` followed by the lowercase SHA-256 digest of the roadmap object after removing only `content_hash`. Canonical JSON uses UTF-8, sorted object keys, no insignificant whitespace, non-ASCII characters preserved, and non-finite numbers forbidden. Array order remains significant.

An approved revision can be checked against an immutable persistence boundary by passing `approved_hashes[(roadmap_id, revision)]`. A different hash fails with `approved_revision_immutable`; changed content with a stale embedded hash fails with `content_hash_mismatch`. New revisions receive new revision numbers and hashes. An approved revision is never edited in place.

## Runtime exclusion

The validator recursively rejects every key in `RUNTIME_FIELD_DENYLIST`, every Agent execution value in `FORBIDDEN_EXECUTION_STATES` when it occurs under a state/status field, and every gate-decision field in `GATE_RUNTIME_FIELD_DENYLIST`. This includes workflow and run IDs, activities and attempts, retries, heartbeats, signals, commands, claims and leases, workers, progress, evidence receipts, commits, and execution timestamps.

Gate definitions state the decision needed, safe default, approval-scope schema, blocked nodes, and required verification rules. They never contain a decision, actor, runtime state, expiry, or evidence receipt. Verification rules describe evidence semantically; they do not embed executable commands.

The validator is a pure, standard-library-only boundary. It registers no routes, performs no persistence, imports no Agent or Temporal modules, starts no run, and returns only a bounded receipt containing the project ID and validated revision hashes.

## Stable failure contract

Validation errors expose `reason_code`, JSON-style `path`, and a bounded detail. Callers branch on `reason_code`, never on detail text. The stable codes are:

- `approved_revision_immutable`
- `content_hash_mismatch`
- `dependency_cycle`
- `duplicate_id`
- `execution_state_forbidden`
- `invalid_approved_reference`
- `invalid_completion_reference`
- `invalid_gate_target`
- `invalid_literal`
- `invalid_repo_path`
- `invalid_type`
- `invalid_value`
- `missing_field`
- `missing_reference`
- `non_canonical_value`
- `runtime_field_forbidden`
- `unknown_field`
- `unreferenced_roadmap`

The published JSON Schema provides portable structural validation. The Python validator remains authoritative for cross-reference integrity, acyclicity, recursive runtime exclusion, deterministic hashing, and approved-revision immutability.
