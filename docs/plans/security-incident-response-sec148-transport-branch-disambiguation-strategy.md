# SEC148 fixed-enum transport branch disambiguation strategy

Run: `ABC-SEC148-20260729-TRANSPORT-BRANCH-DISAMBIGUATION-STRATEGY`
Phase: `post_two_terminal_attempts_strategy_gate`
Mutation authority for this artifact: `repo_only`

This strategy follows two separately authorized observations that both ended
with the same strict terminal result: `status=blocked`,
`error_code=transport_failed`, identical canonical evidence digest, and
`retry_permitted=false`. Those results prove neither which internal transport
branch failed nor Compose capability. They cannot authorize a third attempt.

This artifact defines an implementation contract only. It grants no
implementation, publication, Git, network, SSH, public-IP query, probe,
observation, retry, provider, deployment, delivery, send, package, container,
credential, or host action.

## Security outcome

Replace ambiguous transport-generated failures with a strict allowlisted
`diagnostic_code` projection. A diagnostic code identifies only one
repository-defined control-flow branch. It must never contain or derive a raw
value, return-code value other than the fixed `255` class named below, byte
count, size, fragment, prefix, suffix, hash, path, hostname, host identity,
address, environment value, exception type or text, provider response, stdout,
or stderr.

The projection is branch evidence only. Every transport-generated diagnostic
record remains `status=blocked` and `retry_permitted=false`. No diagnostic enum,
alone or in combination, can imply `ok`, capability support, deployment
readiness, provider reachability, or permission to retry.

## Versioned schema and migration

Adding `diagnostic_code` to the existing exact transport v1 key set would break
strict readers without declaring the break. The implementation must therefore
introduce:

```text
odysseus.redacted_podman_compose_capability_transport.v2
```

The new producer emits v2 for every transport-generated blocked result. It
must not emit both versions or two records.

Backward compatibility is evidence compatibility, not dual emission:

1. Historical
   `odysseus.redacted_podman_compose_capability_transport.v1` records retain
   their original exact five-key schema, original digest, and existing
   `error_code`; they are never rewritten or upgraded in place.
2. A schema-specific evidence validator may accept exact historical v1 and
   exact new v2 records, but must reject mixed key sets and unknown schema IDs.
3. The runtime producer emits v2 only after the implementation, tests,
   publication, and a future packet are separately accepted.
4. Valid observer envelopes retain their existing observer schema and digest.
   The transport must not add a transport diagnostic field to them.
5. Existing top-level `error_code` values
   `published_blob_unavailable` and `published_blob_mismatch` remain unchanged.

No existing live grant or published transport may be reinterpreted as using
v2.

## Exact transport v2 blocked schema

Every transport-generated v2 result has exactly these keys and no others:

```text
schema_id
status
error_code
diagnostic_code
retry_permitted
evidence_sha256
```

The value invariants are exact:

- `schema_id` is
  `odysseus.redacted_podman_compose_capability_transport.v2`;
- `status` is `blocked`;
- `(error_code, diagnostic_code)` is one pair from the allowlist below;
- `retry_permitted` is the literal Boolean `false`; and
- `evidence_sha256` is a lowercase 64-character SHA-256 over the canonical
  body defined below.

An extra key, missing key, nonliteral Boolean, unknown enum, invalid pair,
unknown schema, malformed JSON, multiple JSON records, or digest mismatch is a
terminal schema failure. It must never be partially accepted or normalized
into a more favorable result.

## Exact fixed-enum pair allowlist

| Internal branch | Preserved `error_code` | Fixed `diagnostic_code` |
| --- | --- | --- |
| Published blob unavailable for any fail-closed load reason | `published_blob_unavailable` | `published_blob_unavailable` |
| Published blob bytes fail the pinned digest | `published_blob_mismatch` | `published_blob_mismatch` |
| SSH runner raises the bounded timeout class | `transport_timeout` | `ssh_timeout` |
| SSH runner raises any other invocation exception | `transport_failed` | `ssh_invocation_exception` |
| SSH result has no byte-valued stdout member | `transport_failed` | `ssh_stdout_unavailable` |
| SSH return code is 255 and stdout is exactly empty bytes | `transport_failed` | `ssh_255_no_payload` |
| SSH return code is 255 and nonempty bytes fail strict observer validation | `transport_failed` | `ssh_255_invalid_payload` |
| A strict valid observer payload has a return code not allowed for that status | `transport_failed` | `valid_payload_returncode_mismatch` |
| Invalid payload arrives under return code 0 or 1 | `transport_invalid` | `invalid_payload_expected_returncode` |
| Invalid payload arrives under any other return-code class | `transport_failed` | `ssh_unexpected_returncode` |
| Caller supplies any command argument | `invalid_invocation` | `invalid_invocation` |
| An internal caller requests an unknown or invalid diagnostic pair | `transport_invalid` | `internal_contract_violation` |

The implementation must use the complete pair as the allowlist key. Validating
`error_code` and `diagnostic_code` independently is insufficient because it
would accept unintended combinations.

`published_blob_unavailable` intentionally remains one bounded class. The
projection must not reveal whether the internal cause was timeout, exception,
return code, absence, type, or limit enforcement. Likewise,
`ssh_unexpected_returncode` must never include the actual return code.

## Deterministic branch precedence

The producer must classify exactly once using this order:

1. A fail-closed published-blob load result emits the preserved
   `published_blob_unavailable` pair. A loaded blob whose SHA-256 differs from
   the pin emits the preserved `published_blob_mismatch` pair. Neither branch
   invokes SSH.
2. A bounded SSH timeout emits `ssh_timeout`. Any other SSH invocation
   exception emits `ssh_invocation_exception`. No exception metadata is read
   into the envelope.
3. A result whose `stdout` is not bytes emits `ssh_stdout_unavailable`,
   regardless of its return-code value.
4. Strictly validate byte-valued stdout before interpreting it:
   - a valid `ok` payload with literal integer return code 0 is returned
     unchanged;
   - a valid `needs_live_observation` or `blocked` payload with return code 1
     is returned unchanged only when the value is the literal integer 1;
   - a valid `needs_live_observation` or `blocked` payload with return code 255
     is the existing valid fail-closed preservation case and is returned
     unchanged only when the value is the literal integer 255;
   - any other valid-payload/return-code combination emits
     `valid_payload_returncode_mismatch`.
5. For invalid byte-valued payloads:
   - literal integer return code 255 plus exactly empty bytes emits
     `ssh_255_no_payload`;
   - literal integer return code 255 plus any nonempty bytes emits
     `ssh_255_invalid_payload`;
   - literal integer return code 0 or 1 emits
     `invalid_payload_expected_returncode`; and
   - every other value or type emits `ssh_unexpected_returncode`.

Only the fixed enum is retained. The classifier must not retain or expose the
payload emptiness test, payload content, actual return code, exception, or
other branch inputs beyond the selected enum.

## Exact schemas for preserved observer statuses

The transport continues to return a strict valid observer envelope unchanged,
with its original canonical digest. It never adds a transport
`diagnostic_code` to these records.

### `status=ok`

The exact key set remains:

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

All declared capability Booleans must be literal `true`, every visibility
Boolean must be literal `false`, and the pinned version and digest must match.
Only this exact observer status can be capability evidence. It still grants no
downstream action.

### `status=needs_live_observation`

The exact key set remains:

```text
schema_id
status
reason_code
missing_proofs
retry_permitted
runtime_shape_profile
evidence_sha256
```

The existing reason-code, ordered missing-proof allowlist, fixed Boolean-only
runtime-shape schema, `retry_permitted=false`, and digest validation remain
mandatory. An added transport diagnostic key is forbidden.

### Observer-origin `status=blocked`

The generic observer blocked key set remains:

```text
schema_id
status
error_code
retry_permitted
evidence_sha256
```

The existing version-output diagnostic variant remains the only observer
six-key variant:

```text
schema_id
status
error_code
diagnostic_code
retry_permitted
evidence_sha256
```

It is valid only for the existing observer error/diagnostic pair allowlist.
Transport v2 and observer diagnostic records are distinguished by exact
`schema_id` plus exact pair allowlist; their identically named
`diagnostic_code` fields are never cross-accepted.

## Canonical digest and single-result boundary

For every accepted schema, canonical digest calculation remains:

1. remove only `evidence_sha256`;
2. serialize the remaining mapping with ASCII escaping, lexicographically
   sorted keys, and separators exactly `,` and `:` with no added whitespace;
3. encode as UTF-8;
4. compute SHA-256; and
5. encode as exactly 64 lowercase hexadecimal characters.

The command emits exactly one canonical JSON record followed by one newline,
writes nothing to stderr, and returns. No raw subprocess output or alternate
record may be emitted before or after it. Every outcome is terminal for one
invocation; no internal or operator retry is permitted.

## Smallest implementation claim

The smallest proposed implementation and test paths are exactly:

1. `ops/homeserver/redacted_podman_compose_capability_transport.py`
2. `tests/test_homeserver_redacted_podman_compose_capability_transport.py`

The implementation path may add the v2 schema identifier, exact pair mapping,
strict v1/v2 evidence validation boundary, deterministic classifier, and
single-result serialization. The test path may use only fake runners and
synthetic payloads. No observer source change is required.

If implementation review finds a strict downstream consumer outside these two
paths, work must stop and the claim must be explicitly expanded before that
consumer is edited. The roadmap ledger remains root-owned and is not part of
the implementation claim.

## Required fake-runner tests

Focused tests must exercise, without Git, SSH, network, or host access:

1. both preserved published-blob error codes, with no SSH call;
2. SSH timeout and non-timeout invocation exception;
3. non-byte stdout;
4. return code 255 with empty bytes and with invalid nonempty bytes;
5. valid `needs_live_observation` and `blocked` payloads preserved under return
   code 255 with their original schema and digest;
6. every valid payload status under every mismatching return-code class;
7. invalid payload under return codes 0 and 1;
8. invalid payload under unexpected integer, missing, Boolean, and noninteger
   return-code representations;
9. exact allowed `(error_code, diagnostic_code)` pairs and rejection of every
   crossed, unknown, missing, or extra-key variant;
10. historical exact v1 fixtures remain digest-verifiable while the new
    producer emits only v2;
11. canonical digest stability, one stdout line, empty stderr, and terminal
    process return behavior;
12. exactly one blob call and at most one SSH call, with no retry; and
13. private sentinel values placed in stdout, stderr-capable fakes, exception
    text, paths, hostnames, and unexpected return-code objects never occur in
    the serialized result.

The existing observer/transport combined suite must remain green after the
focused transport suite. Python compilation, path-scoped diff review, extra-key
negative tests, and root/Sol deep review are required acceptance evidence.

## Acceptance

SEC148 implementation is acceptable only when:

- changed paths are a subset of the two proposed implementation/test paths;
- every branch deterministically maps to the exact pair table or preserves one
  strict observer envelope unchanged;
- v2 is emitted once for new transport-generated blocked outcomes and strict
  historical v1 evidence remains verifiable without rewriting;
- all unknown fields, enums, pairs, schemas, types, JSON shapes, and digests
  fail closed;
- no retained record reveals raw data, values, sizes, fragments, input-derived
  hashes other than the required canonical envelope digest, paths,
  environment, exception, provider response, host identity, or actual
  unexpected return code;
- all fake-runner tests, the combined observer/transport suite, compilation,
  diff checks, and deep security review pass; and
- no implementation evidence is misreported as publication, live validation,
  capability PASS, deploy readiness, send readiness, or overall run success.

## Stop rules and next frontier

Stop without implementation or retry if any branch requires raw output,
dynamic diagnostic text, a numeric return-code field, payload length, content
fragment, path, host or provider identity, exception detail, or an unallowlisted
key. Stop on overlapping ownership, a required consumer outside the claim,
failed strict-schema tests, inability to preserve historical evidence, or any
request to infer capability from a diagnostic enum.

The next possible frontier after this strategy is a fresh, separately claimed
repo-only implementation of the two proposed paths followed by root/Sol deep
review. Publication would require a separate exact Git packet and authority.
Any later observation would require a newly published revision and another
separate one-use action packet and approval. The two prior live grants are
consumed and no further live attempt is requestable from this artifact.

## Freeze handoff

Changed path:
`docs/plans/security-incident-response-sec148-transport-branch-disambiguation-strategy.md`
only.

Checks before handoff: exact Markdown contract and path-scope review, followed
by `git diff --check` for this file. Not performed: implementation, tests,
staging, commit, push, publication, network, SSH, public-IP query, observation,
retry, provider call, deployment, delivery, send, package, container, or host
action.
