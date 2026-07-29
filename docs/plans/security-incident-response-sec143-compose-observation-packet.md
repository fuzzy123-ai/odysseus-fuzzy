# SEC143 exact one-use published Compose capability observation packet

Run: `ABC-SEC143-20260729-PUBLISHED-COMPOSE-OBSERVATION-PACKET`
Phase: `action_specific_packet_preparation`
Mutation authority for this artifact: `repo_only`

This is a request packet, not a live grant. It prepares exactly one later,
read-only observation of the already published transport. It does not itself
create, grant, consume, extend, or reuse live authority.

## Immutable published binding

The later observation is valid only against all of these exact published
values. Any mismatch is terminal and must stop before invocation.

- Remote/ref: `fuzzy/dev`
- Revision: `9ea87e67464015cedbeeaada9117899edcab3ae2`
- Tree: `992bb061b311aac8cc537420ee1928bfb15ff7f6`
- Observer SHA-256:
  `af8e688ae86e1406a55f51d521f746d15b9300d399caeaa4698e53bd133bd46c`
- Transport SHA-256:
  `9dfc48f746fe95515776552fedd15d846ac72b53eeecc3e53d414cb166a76dd3`

## Later approval request

Only if root presents this exact packet context, the owner may approve this
single action later with a plain `weiter`. That approval is usable only for the
following complete contract, expires at `RUN_END`, and is neither implied by
this packet nor transferable to another revision, command, target, or run.

```text
C:\Users\nkatz\odysseus\venv\Scripts\python.exe ops\homeserver\redacted_podman_compose_capability_transport.py
```

The command has no arguments. Its limits are exactly one invocation and one
result, an outer timeout of at most 30 seconds, zero follow-on queries, and
zero retries. The executor must not add shell wrappers, command arguments,
help queries, source reads, environment reads, host administration, or any
second observation.

## Mandatory preflight and readback

Before the one invocation, an authorized executor must independently confirm
the immutable published binding above and that the grant is still fresh,
single-use, and unconsumed. A failed or unavailable preflight is terminal; do
not invoke the transport.

The sole retained result may be only one repository-owned, strict, redacted
JSON record that validates its exact allowed schema and its canonical
`evidence_sha256` digest. Do not retain or forward raw stdout, stderr, help,
source, environment, exception, provider response, or private path. Preserve
only the validated canonical record and its digest as evidence; reject any
extra key, invalid type, schema mismatch, malformed JSON, or digest mismatch.

The post-invocation readback is limited to confirming that exactly one bounded
attempt occurred and recording the validated canonical digest and terminal
classification. It must not cause another query or attempt.

## Terminal interpretation

Every result is terminal for this grant and has `retry_permitted: false`.

- `ok`: capability PASS only when the complete strict `ok` schema and its
  canonical digest validate. This is evidence for the next repository-only
  deployment-strategy decision; it is not deployment authority.
- `needs_live_observation`: accept only its strict schema and canonical digest.
  Route it to a repository-only deployment-strategy decision that accounts for
  the reported missing proofs. Do not retry or infer a PASS.
- `blocked`: accept only its strict schema and canonical digest, record the
  bounded error classification, and stop. Do not retry, broaden diagnostics, or
  substitute raw output.
- Any other result, timeout, malformed result, preflight mismatch, or readback
  failure: terminal stop with no retry and no follow-on diagnostic.

## Explicit exclusions

This packet grants no deploy, send, package, host, provider, container,
credential, SSH-administration, Git, or network action. In particular,
`deploy-live-go` and `OPS-ALERT-DELIVERY-GO` remain separate, ungranted,
action-specific decisions. A capability PASS does not satisfy either gate.

## Task-status completion rule

The SEC143 packet-authoring task may be handed to root for review only after
this file passes scope validation and `git diff --check`. The later observation
may be reported as successful only when its strict readback is `ok`; a
`needs_live_observation`, `blocked`, failed, interrupted, cancelled, or unknown
task/goal status is not success. Git publication or packet authorship never
changes that rule. Root must check the current task/goal status before making
any completion claim.

## Freeze handoff

Changed path: `docs/plans/security-incident-response-sec143-compose-observation-packet.md` only.
Checks required before handoff: Markdown scope review and `git diff --check` for
this path.
Not performed by this packet: live observation, deployment, delivery, provider
call, host change, staging, commit, push, or any network action.
