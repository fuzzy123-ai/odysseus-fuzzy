# SEC146 exact one-use published Compose capability observation packet

Run: `ABC-SEC146-20260729-PUBLISHED-COMPOSE-OBSERVATION-PACKET`
Phase: `action_specific_packet_preparation`
Mutation authority for this artifact: `repo_only`

This is a request packet, not a live grant. It prepares exactly one later,
read-only, redacted observation of the published Compose capability transport.
It does not itself create, grant, consume, extend, or reuse live authority.

## Immutable published binding

The proposed observation is valid only against all of these exact published
values. A failed, unavailable, contradictory, or unknown binding check is
terminal and must stop before invocation.

- Remote/ref: `fuzzy/dev`
- Revision: `2a3b3bd93143cc03f4c267cdcedfc54b93fd5b56`
- Tree: `50a22b47e40ebdd8bd55789b890ebb2aa8faecf8`
- Observer SHA-256:
  `af8e688ae86e1406a55f51d521f746d15b9300d399caeaa4698e53bd133bd46c`
- Transport SHA-256:
  `630e460799f4a940f582cb6b4396a13902d32c080d7d7a22176256f2c92bbe79`

No different remote, revision, tree, observer, transport, command, target, or
run is interchangeable with this binding.

## Later exact approval request

Only after root presents this exact packet context as accepted may the owner
approve the single action with a plain `weiter`. That later approval would be
usable only for this complete command and contract:

```text
C:\Users\nkatz\odysseus\venv\Scripts\python.exe ops\homeserver\redacted_podman_compose_capability_transport.py
```

The command has no arguments. The limits are exactly:

- maximum invocations: `1`;
- maximum retained results: `1`;
- outer timeout: at most `30` seconds;
- follow-on queries or commands: `0`;
- retries: `0`; and
- expiry: `RUN_END`.

The executor must not add a shell wrapper, command argument, help query, source
read, environment read, host diagnostic, provider query, or second
observation. A timeout, failure, or incomplete result does not replenish the
single invocation.

## Mandatory preflight

Before the sole invocation, the authorized executor must independently:

1. confirm the complete immutable remote revision, tree, observer hash, and
   transport hash binding above;
2. confirm the action-specific grant is fresh, unconsumed, bound to this packet
   and expires at `RUN_END`;
3. confirm the command is exact, has no arguments, and the one-invocation,
   one-result, 30-second, no-follow-on, no-retry limits are enforced; and
4. confirm that only the repository-owned strict redaction boundary will
   validate and retain the result.

Any failed or unavailable preflight is terminal. Do not invoke, retry, broaden
diagnostics, substitute another transport, or inspect raw runtime data.

## Sole retained evidence

The sole retained result may be only one repository-owned, strictly validated,
redacted JSON record and its validated canonical `evidence_sha256` digest.
Reject any extra key, invalid type, unexpected status, malformed JSON, schema
mismatch, or digest mismatch.

Do not retain, print, forward, summarize from, hash, or otherwise expose raw
stdout, stderr, exception text, help, source, environment, journal, provider
response, credential material, or private path. The post-invocation readback
may confirm only that one bounded attempt occurred and record the validated
terminal status and canonical digest. It must not cause another query or
attempt.

## Terminal interpretation

Every accepted result is terminal for this grant and has
`retry_permitted: false`.

- `ok`: accept only the complete strict `ok` schema and matching canonical
  digest. This is capability evidence only; it grants no deployment or send.
- `needs_live_observation`: accept only its strict schema and matching
  canonical digest. Record the missing proof classification and route it to a
  repository-only strategy decision. Do not infer PASS or retry.
- `blocked`: accept only its strict schema and matching canonical digest.
  Record the bounded error classification and stop without retry or expanded
  diagnostics.
- Any other status, timeout, malformed result, schema failure, digest failure,
  binding mismatch, or readback failure: terminal stop with no retry and no
  follow-on action.

## Explicit authority boundary

This packet grants no live action, Git action, network access, SSH action,
provider access, host action, container action, package action, credential
action, deployment, delivery, or send. It does not satisfy `deploy-live-go` or
`OPS-ALERT-DELIVERY-GO`. A strict `ok` result would still require separate,
action-specific downstream decisions.

A plain `weiter` has meaning for this observation only after root presents
this exact accepted packet context. It cannot approve any other revision,
command, retry, probe, observation, deployment, delivery, or run.

## Task and goal completion rule

Packet authorship is handoff-ready only after this file passes exact scope
review and `git diff --check`. The later observation may be reported as
successful only when its strict retained result validates as `ok` and the
enclosing Codex task and goal status are independently checked.

A `needs_live_observation`, `blocked`, failed, interrupted, cancelled,
contradictory, unavailable, or unknown task/goal state is not overall run
success. Packet authorship, prior Git publication, or an invoked process cannot
override that rule.

## Freeze handoff

Changed path:
`docs/plans/security-incident-response-sec146-compose-observation-packet.md`
only.

Checks before handoff: exact Markdown contract and path-scope review, followed
by `git diff --check` for this file. Not performed by this packet: live
observation, Git action, network access, SSH, provider call, deployment,
delivery, send, package action, container action, host change, or retry.
