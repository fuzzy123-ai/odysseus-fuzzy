# SEC135 bounded semantic-proof diagnostic packet

Run: `ABC-SEC135-20260729-SEMANTIC-PROOF-DIAGNOSTIC`

Status: `published_single_observation_terminal_four_missing_proofs_no_retry`.
This packet grants no further SSH, network, probe, deploy, build, pull,
checkout, container, backup, restore, send, or other live action.

## SEC134 terminal boundary

SEC134 was published as commit
`b7baf06bfe70b362c4cc6ef17cc0cf5ad587cb58`. Its separately authorized
read-only observation ran exactly once and returned the validated terminal
envelope `status=needs_live_observation`,
`reason_code=semantic_proof_insufficient`, `retry_permitted=false`, with
evidence digest
`c57a19fbb46b8802d2f3298344026619ba0a2d3deab713d47dbd833b18d6de0d`.
The prior version-output failure did not recur. The SEC134 grant is spent and
cannot be retried.

## Fixed-enum diagnostic contract

SEC135 must return every absent allowlisted semantic proof in one canonical
ordered list. The complete vocabulary is:

- `global_env_file_parser_missing`
- `global_project_name_parser_missing`
- `build_service_argument_missing`
- `up_service_argument_missing`
- `up_no_deps_parser_missing`
- `up_no_build_parser_missing`
- `up_force_recreate_parser_missing`
- `source_build_service_selection_missing`
- `source_up_service_selection_missing`
- `source_up_no_deps_guard_missing`
- `source_rollback_force_recreate_missing`

The list must be non-empty, unique, ordered exactly as above, and bounded by
this vocabulary. It may contain only fixed enum identifiers. It must never
contain or derive raw help text, source text, values, substrings, prefixes,
suffixes, hashes, lengths, counts, paths, hostnames, environment data,
exception text, stderr, or secret material.

The transport must reject empty, duplicate, reordered, unknown, extra-field,
wrong-reason, wrong-digest, or return-code-inconsistent variants. The existing
positive capability envelope remains unchanged, and all terminal envelopes
remain `retry_permitted=false`.

## Repo-only acceptance and publication scope

Acceptance requires focused tests for every individual missing proof,
multi-proof ordering, strict transport rejection, unchanged success behavior,
canonical digest validation, Python compilation, diff checking, and root/Sol
deep review.

The exact publication candidate is limited to:

- `docs/plans/security-incident-response-production-completion-roadmap.json`
- `docs/plans/security-incident-response-version-output-diagnostic-packet.md`
- `docs/plans/security-incident-response-semantic-proof-diagnostic-packet.md`
- `ops/homeserver/redacted_podman_compose_capability_observation.py`
- `ops/homeserver/redacted_podman_compose_capability_transport.py`
- `tests/test_homeserver_redacted_podman_compose_capability_observation.py`
- `tests/test_homeserver_redacted_podman_compose_capability_transport.py`

Publication and a later observation are not executable until deep review binds
the exact revision, tree, path inventory, and observer SHA-256. A later
observation is limited to one no-argument run through the redacted
published-blob transport, expires at `RUN_END`, and permits no retry. It grants
no deploy, delivery, backup, restore, or host mutation.

## Accepted review

The four-path Bob/Terra handoff passed root/Sol deep review. Independent
verification passed 21 focused tests, Python compilation, diff checking, and
all 2,047 possible non-empty subsets of the eleven-code vocabulary against
canonical observer digests and strict transport validation. The observer
worktree SHA-256 is
`1cb419b85206bc4e9d35602ebfb9544acf4722f1ff0ea38b334d54f852f28d30`;
the transport pin matches it. Both SEC135 claims are released.

Next safe action: bind and publish only the exact seven-path candidate. A
single later read-only observation remains unavailable until `fuzzy/dev`
readback confirms the revision and observer hash.

## Terminal live outcome

The accepted seven-path candidate was published as commit
`2c51fd349843d05e200c6d14e717e78a01e7e9f1` with tree
`a07b1323595259e9aaedf1d33b70c410e8c02f95`. `fuzzy/dev` readback confirmed
the revision and observer SHA-256
`1cb419b85206bc4e9d35602ebfb9544acf4722f1ff0ea38b334d54f852f28d30`.

The separately authorized transport ran exactly once. Its validated terminal
envelope was `status=needs_live_observation`,
`reason_code=semantic_proof_insufficient`, `retry_permitted=false`, with
evidence digest
`d4bce89cd5f58eb465a5e232e12cb423bab9f517bffbcf4e80c425b0eafdc5bf`.
It identified exactly:

- `build_service_argument_missing`
- `up_service_argument_missing`
- `source_up_service_selection_missing`
- `source_up_no_deps_guard_missing`

The fixed short-version gate and the other seven proof classes passed. No raw
help or source output was emitted or retained, and no retry occurred.

Next safe action: first create and deep-review a repo-only SEC136 recognizer
repair grounded in public Podman Compose 1.3.0 argparse and `compose_up` source
structure for exactly these four proof classes. A later observation requires
new action-specific authority. Deploy and delivery remain independently gated.
