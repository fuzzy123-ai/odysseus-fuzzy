# SEC152 exact one-use published transport v2 observation packet

Run: `ABC-SEC152-20260729-TRANSPORT-V2-OBSERVATION-PACKET`
Phase: `action_specific_packet_preparation`
Mutation authority for this artifact: `repo_only`

This is a request packet for one possible later, separately authorized,
read-only observation of the published transport v2 diagnostic boundary. It is
not a live grant and does not create, grant, consume, extend, or reuse
execution authority.

SEC146 remains the one-use execution-boundary baseline. SEC148 remains the
strict transport v2 schema, pair-allowlist, canonical-digest, migration, and
non-capability contract. This packet narrows those contracts to one published
revision and retains exactly one validated result from either the strict
transport v2 blocked family or the strict preserved observer family. It does
not weaken either predecessor or mix their schemas.

## Immutable published binding

Any later grant is valid only after a fresh independent preflight confirms
every value below:

- Remote/ref: `fuzzy/dev`
- Published revision:
  `67f0737de5bccdb5b8841e4ad9deee3df0107b74`
- Published tree:
  `05e35c526939ff277ad8d74e276b97f2c782ad98`
- Published observer SHA-256:
  `af8e688ae86e1406a55f51d521f746d15b9300d399caeaa4698e53bd133bd46c`
- Published and required local transport SHA-256:
  `fdbbb0a5103eca34d0a1b96e55f34d45f34ef7e83493fa1f7cafe3c772de44a3`

No different remote, revision, tree, observer, transport, command, local file,
target, result schema, or run is interchangeable with this binding.

## Later exact command and limits

Only after root presents this exact packet context as accepted may the owner
separately approve the single action. A plain `weiter` in that presented
context would be usable only for this exact command:

```text
C:\Users\nkatz\odysseus\venv\Scripts\python.exe ops\homeserver\redacted_podman_compose_capability_transport.py
```

The command has zero arguments. Its complete limits are:

- maximum invocations: `1`;
- maximum retained results: `1`;
- outer timeout: at most `30` seconds;
- follow-on queries or commands: `0`;
- retries: `0`; and
- expiry: `RUN_END`.

The executor must not add a shell wrapper, argument, environment override, help
or source query, public-IP query, alternate transport, host diagnostic, second
observation, or fallback. A timeout, failure, invalid result, or incomplete
result does not replenish the single invocation.

## Fresh preflight before any later grant

Before root may present any later observation grant, it must independently:

1. read back remote `fuzzy/dev` and require the exact published revision and
   tree above;
2. compute the observer blob SHA-256 from that remote revision and require the
   exact observer hash above;
3. compute the transport blob SHA-256 from that remote revision and require
   the exact transport hash above;
4. compute the local transport file SHA-256 and require it to equal the same
   published transport hash;
5. confirm locally, without live invocation, that the repository-owned
   validator accepts both exact result families defined below: the transport
   v2 six-key blocked schema with an exact allowed enum pair and the preserved
   observer schemas with their status-specific exact keys, enums, literal
   types, and canonical digest;
6. confirm extra, missing, mixed-family, crossed-schema, crossed-pair,
   unknown-enum, unknown-schema, invalid-type, malformed, multi-record, and
   digest-mismatch variants all fail closed;
7. confirm the exact no-argument command and the one-invocation, one-result,
   30-second, no-follow-on, no-retry, `RUN_END` limits are enforced; and
8. confirm the action-specific grant is fresh, unconsumed, and bound only to
   this packet.

Local tracking state, a prior push result, cached prose, or packet authorship is
not an independent remote readback. Any failed, unavailable, contradictory, or
unknown preflight is terminal and no grant may be presented.

## Sole accepted result families

The sole retainable result is one repository-owned, strictly validated,
redacted JSON record from exactly one of the two families below. No mixed,
cross-schema, partially valid, or dual record is accepted.

### Family A: transport-generated v2 blocked

A transport-generated v2 record has exactly these six keys and no others:

```text
schema_id
status
error_code
diagnostic_code
retry_permitted
evidence_sha256
```

The value contract is exact:

- `schema_id` is
  `odysseus.redacted_podman_compose_capability_transport.v2`;
- `status` is `blocked`;
- `(error_code, diagnostic_code)` is exactly one allowed pair below;
- `retry_permitted` is the literal Boolean `false`; and
- `evidence_sha256` is the valid canonical digest for the other five keys.

The allowed pairs are exactly:

| `error_code` | `diagnostic_code` |
| --- | --- |
| `published_blob_unavailable` | `published_blob_unavailable` |
| `published_blob_mismatch` | `published_blob_mismatch` |
| `transport_timeout` | `ssh_timeout` |
| `transport_failed` | `ssh_invocation_exception` |
| `transport_failed` | `ssh_stdout_unavailable` |
| `transport_failed` | `ssh_255_no_payload` |
| `transport_failed` | `ssh_255_invalid_payload` |
| `transport_failed` | `valid_payload_returncode_mismatch` |
| `transport_invalid` | `invalid_payload_expected_returncode` |
| `transport_failed` | `ssh_unexpected_returncode` |
| `invalid_invocation` | `invalid_invocation` |
| `transport_invalid` | `internal_contract_violation` |

Pairs must be validated as pairs, not as two independent enums. An extra or
missing key, crossed or unknown pair, wrong literal, wrong schema, malformed
JSON, multiple record, or invalid digest is outside this family and terminal.

### Family B: strict preserved observer envelopes

A preserved observer record has exact `schema_id`
`odysseus.redacted_podman_compose_capability_observation.v1`. The transport
returns it unchanged, including its original digest. It must match exactly one
of the following status schemas.

#### Observer `ok`

The exact keys are:

```text
schema_id
status
podman_compose_version
global_env_file_parser_present
global_project_name_parser_present
service_scoped_build_parser_present
service_scoped_up_parser_present
no_deps_parser_present
no_build_parser_present
rollback_force_recreate_parser_present
service_scoped_dependency_exclusion_proven
rollback_force_recreate_proven
deployment_capability_supported
raw_stdout_visible
raw_stderr_visible
exception_text_visible
environment_visible
source_text_visible
paths_visible
hostnames_visible
secret_values_visible
evidence_sha256
```

`status` is `ok` and `podman_compose_version` is `1.3.0`. Every parser,
selection, rollback, and deployment capability field is the literal Boolean
`true`; every visibility field is the literal Boolean `false`; and the
canonical digest matches. No `retry_permitted`, `error_code`,
`diagnostic_code`, reason, missing-proof, or runtime-shape key is present.

#### Observer `needs_live_observation`

The exact keys are:

```text
schema_id
status
reason_code
missing_proofs
retry_permitted
runtime_shape_profile
evidence_sha256
```

`status` is `needs_live_observation`, `reason_code` is exactly
`semantic_proof_insufficient`, and `retry_permitted` is the literal Boolean
`false`. `missing_proofs` is a nonempty JSON list with no duplicate or unknown
item, in the canonical order induced by this exact allowlist:

```text
global_env_file_parser_missing
global_project_name_parser_missing
build_service_argument_missing
up_service_argument_missing
up_no_deps_parser_missing
up_no_build_parser_missing
up_force_recreate_parser_missing
source_build_service_selection_missing
source_up_service_selection_missing
source_up_no_deps_guard_missing
source_rollback_force_recreate_missing
```

`runtime_shape_profile` is an exact JSON object with keys `help_grammar` and
`source_ast`. `help_grammar` has exactly `build` and `up`; each contains exactly
these literal-Boolean keys:

```text
usage_line_present
uppercase_service_positional_grammar_present
bracketed_lowercase_services_positional_grammar_present
bare_lowercase_services_positional_grammar_present
```

`source_ast` has exactly:

```text
compose_build_handler_present
compose_up_handler_present
get_excluded_handler_present
exclusion_helper
compose_up
```

The first three values are literal Booleans. `exclusion_helper` is an exact
literal-Boolean object with:

```text
exact_signature
empty_set_initialization
args_services_branch
compose_services_set
requested_service_loop
dependency_lookup_subtraction
selected_service_discard
```

`compose_up` is an exact literal-Boolean object with:

```text
exact_exclusion_helper_assignment
compose_containers_loop
excluded_service_continue_guard
no_deps_dependency_control_branch
```

The observer canonical digest must match. No transport-v2 key or enum may be
added.

#### Observer generic `blocked`

The exact keys are:

```text
schema_id
status
error_code
retry_permitted
evidence_sha256
```

`status` is `blocked`, `retry_permitted` is the literal Boolean `false`, and
`error_code` is exactly one of:

```text
version_unavailable
version_mismatch
help_unavailable
source_audit_unavailable
source_audit_invalid
malformed_output
output_too_large
timeout
internal_error
```

The canonical digest must match and no `diagnostic_code` is present.

#### Existing observer diagnostic `blocked`

The exact keys are:

```text
schema_id
status
error_code
diagnostic_code
retry_permitted
evidence_sha256
```

`status` is `blocked`, `retry_permitted` is the literal Boolean `false`,
`error_code` is `malformed_output` or `version_mismatch`, and
`diagnostic_code` is exactly one of:

```text
version_output_empty
version_output_controls
version_output_multiline
version_output_line_shape
version_output_version_mismatch
```

The canonical digest must match. The observer schema ID and observer enums
must never be cross-accepted as transport v2, even though both exact variants
use a six-key shape.

## Canonical digest and redaction boundary

Digest validation is exact:

1. remove only `evidence_sha256`;
2. serialize the remaining mapping with ASCII escaping, lexicographically
   sorted keys, separators exactly `,` and `:`, and no added whitespace;
3. encode as UTF-8;
4. compute SHA-256; and
5. require exactly 64 lowercase hexadecimal characters.

The repository-owned validator is the only permitted retention boundary. It
may retain only one complete validated Family A or Family B canonical record
and digest. Do not retain, print, forward, summarize from, hash, or otherwise
expose raw stdout, stderr, exception text, source, help, environment, journal,
provider response, credential material, public IP, host identity, private
path, actual unexpected return code, value, size, or fragment.

The post-invocation readback may confirm only that exactly one bounded attempt
occurred and record the validated result family, terminal status, allowlisted
enum or pair where present, and canonical digest. It must not cause another
query, invocation, retry, or fallback.

## Terminal interpretation

Every possible outcome is terminal for the single-use grant:

- A strict valid transport v2 six-key result is accepted as bounded diagnostic
  evidence only. It remains `status=blocked` with
  `retry_permitted=false`.
- A strict preserved observer `ok` is accepted as capability evidence only. It
  grants no deploy, send, live, provider, host, or other action authority.
- A strict preserved observer `needs_live_observation`, generic `blocked`, or
  existing observer diagnostic `blocked` is accepted as bounded terminal
  evidence with no retry or follow-on.
- A timeout, preflight mismatch, malformed or multi-record result, extra key,
  invalid schema, mixed or crossed family, crossed or unknown enum pair,
  invalid observer enum or type, digest mismatch, readback failure, process
  failure, or missing result is a terminal stop.

No terminal result permits retry, a follow-on diagnostic, a fallback, another
transport, or reuse of the grant.

## Diagnostic evidence never implies capability

A valid transport v2 enum says only which allowlisted fail-closed branch
executed. It does not prove Podman Compose behavior, observer capability,
provider reachability, deploy readiness, delivery readiness, or permission for
any downstream action. Only a separate strict preserved observer `ok` can be
capability evidence, and even that grants no downstream action. No v2
diagnostic can be promoted to `ok`, and neither family can satisfy
`deploy-live-go` or `OPS-ALERT-DELIVERY-GO`.

## Explicit authority boundary

This packet grants no current execution, Git action, network access, public-IP
query, SSH, probe, observation, retry, provider access, container action,
package action, host action, credential action, deployment, delivery, or send.
It grants no fallback and no permission to inspect or retain raw output.

A plain `weiter` has meaning only after root presents this exact accepted
packet context. It cannot approve another revision, tree, hash, schema,
command, argument, retry, fallback, observation, network action, deployment,
delivery, or run.

## Task and goal completion rule

Packet authorship is handoff-ready only after exact path scope, contract review,
and `git diff --check` pass. A later observation can be reported only as one
terminal attempt after one strict Family A or Family B result and the
one-attempt readback validate. Capability PASS requires strict preserved
observer `ok`; every other accepted result is bounded terminal evidence. No
result is deployment, delivery, or overall-run success.

Root must independently check the enclosing Codex task and goal status before
any completion claim. A blocked, failed, interrupted, cancelled,
contradictory, unavailable, or unknown task/goal state is not overall run
success even when the command process or strict diagnostic validation passes.

## Freeze handoff

Changed path:
`docs/plans/security-incident-response-sec152-compose-v2-observation-packet.md`
only.

Checks before handoff: exact Markdown contract, SEC146/SEC148 non-weakening
review, path-scope review, and `git diff --check` for this file. Not performed:
execution, Git action, network access, public-IP query, SSH, probe, observation,
retry, provider call, deployment, delivery, send, package action, container
action, host change, fallback, or raw-output inspection.
