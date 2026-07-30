# SEC154 Compose capability remediation strategy

Run: `ABC-SEC154-20260730-COMPOSE-CAPABILITY-REMEDIATION-STRATEGY`
Phase: `post-terminal-observation remediation strategy`
Mutation authority for this artifact: `repo_only`

## Decision and current evidence

SEC153 consumed its one-use observation grant. Its one strict, redacted
observer result was `status=needs_live_observation`,
`reason_code=semantic_proof_insufficient`,
`missing_proofs=[source_up_no_deps_guard_missing]`, and
`retry_permitted=false`. It is terminal evidence, not a transient transport
failure and not permission for another observation.

The installed Debian `podman-compose` 1.3.0 source shape exposes the real
semantic gap: `compose_up` does not prove that `--no-deps` controls dependency
expansion. The official-shaped offline AST fixture confirms that this is a
meaningful negative case, rather than a parser spelling or recognizer gap.

**Preferred remediation:** keep the observer fail-closed and prepare a
separately gated replacement or upgrade to an officially supported Compose
implementation. A candidate is eligible only if its exact installed source,
from a pinned and independently verifiable package or artifact provenance,
proves that `compose_up --no-deps` controls dependency expansion. No target
implementation, package name, release, version, repository, or artifact is
selected or implied by this strategy.

The required deployment invariant remains application-only transactional
control: the `up` path must use the selected app scope with `--no-deps` and
`--no-build`, and must neither pull/start dependencies nor recreate them.
Rollback must retain its explicitly proven scoped `--force-recreate` behavior.
Whole-stack `up`, dependency expansion, dependency pull/start/recreate, and a
claim that help text alone proves semantics are all unacceptable.

This document is a strategy and evidence contract only. It grants no Git,
network, SSH, public-IP query, package lookup/install/upgrade, artifact
download, container action, host change, probe, observation, retry, provider
call, deployment, delivery, send, credential, or authentication action.

## Non-negotiable safety boundaries

1. Do not weaken the observer, AST recognizer, missing-proof list, result
   schema, canonical digest, or `retry_permitted=false` merely to recognize
   Debian 1.3.0 as capable.
2. Do not edit `/usr`, patch an installed package in place, replace an
   executable by hand, add a shell alias, change PATH precedence, or install a
   shadow binary. These evade provenance and rollback controls.
3. Do not automatically install, upgrade, remove, pin, hold, configure, or
   invoke a package manager. A package/host change needs a separate exact
   live GO after candidate selection and publication.
4. Do not broaden the transactional unit to the whole Compose stack to work
   around the proof gap. A larger blast radius is not a substitute for the
   `--no-deps` invariant.
5. Direct Podman executor work is only a fallback. It requires a separate
   owner decision and a new roadmap slice; it is not an automatic alternate
   implementation, deployment path, or observation target.
6. No result in this strategy is capability PASS, deploy readiness, delivery
   readiness, or authority for `OPS-ALERT-DELIVERY-GO`.

## Sequential evidence gates

Every phase is sequential. A later phase is unavailable until the preceding
one has a published, reviewed PASS at the exact stated boundary. Failure,
ambiguity, unavailability, extra consumer, raw-output need, or scope growth is
a terminal stop for that candidate, not a reason to relax a gate.

### Gate A - repo-only candidate selection contract

Create a new, separately claimed repo-only selection slice before researching
or selecting a candidate. It must define a bounded candidate ledger without
network access and without asserting any candidate is suitable. Its smallest
anticipated paths are:

```text
docs/plans/security-incident-response-sec155-compose-candidate-selection-contract.md
```

The roadmap ledger and any roadmap test are root-owned, separate claims. Gate
A does not edit observer or transport source/tests: implementation and its
offline fixture tests begin only at Gate B after a candidate is selected.

That slice must first declare the exact provenance evidence required for a
candidate, not collect live evidence:

- the official implementation identity and supported distribution channel;
- the full entrypoint/provider chain. If the candidate entrypoint is
  `podman compose`, the contract must prove and bind the external Compose
  provider to which it delegates. The wrapper name, its version output, or its
  help text alone cannot establish candidate identity or semantic support;
- exact package or artifact identity, version, architecture when applicable,
  immutable digest/checksum or signed identity where the channel provides it;
- approved repository/channel and trusted signing-key or signature-verification
  mechanism, without storing keys or any raw provider response;
- the exact installed executable and source location *as a bounded identity
  predicate*, never emitted as a path or source fragment;
- an offline, synthetic source fixture representing the candidate's documented
  `compose_up` control flow; and
- the deterministic AST predicates proving that `--no-deps` selects the
  service-only branch while the opposite branch performs dependency expansion.

The selection contract may not name a candidate version until independently
reviewed official provenance exists under a later, separate read-only evidence
authority. It must explicitly record that Debian 1.3.0 remains an adverse
fixture: its `compose_up` shape yields
`source_up_no_deps_guard_missing`, which must continue to produce
`needs_live_observation`, never `ok`.

### Gate B - offline implementation and fixture acceptance

Only after Gate A selects a candidate with a durable owner decision may a new
repo-only implementation slice update the observer contract. The smallest
anticipated paths are exactly:

```text
ops/homeserver/redacted_podman_compose_capability_observation.py
tests/test_homeserver_redacted_podman_compose_capability_observation.py
ops/homeserver/redacted_podman_compose_capability_transport.py
tests/test_homeserver_redacted_podman_compose_capability_transport.py
```

Any roadmap or packet update is root-owned and must be a distinct claim. No
installed source, package metadata, remote host, or raw command output may be
used by these tests; all fixtures are local and synthetic.

Acceptance requires all of the following:

1. The candidate fixture proves, structurally and locally, a `compose_up`
   branch whose `--no-deps` condition controls whether dependency expansion is
   performed. A flag parser, a usage line, or a string occurrence alone fails.
2. The candidate fixture also proves service-scoped build and up selection,
   `--no-build`, and scoped rollback `--force-recreate` consumption required
   by the transactional contract.
3. The Debian 1.3.0 official-shaped negative fixture remains accepted only as
   `needs_live_observation` with the canonical
   `source_up_no_deps_guard_missing` proof gap.
4. Malformed, partial, misleading, renamed-without-semantic-branch, or
   conflicting fixtures fail closed and cannot become `ok`.
5. The observer schema is versioned or pinned deliberately if and only if the
   selected candidate changes a previously exact contract. Historical SEC153
   evidence remains valid under its original schema and digest; it is never
   rewritten. The transport validator/pin changes only after the observer
   contract is selected, implemented, and tested.
6. Observer and transport each emit one redacted canonical result only. No
   raw source, path, host identity, package manager output, artifact content,
   exception, environment, IP address, provider value, or dynamic diagnostic
   appears in a retained result.

The focused observer and transport suites, their combined suite, Python
compilation, exact path-scope diff check, extra-key and digest-negative tests,
and a root/Sol deep review must pass before publication can be proposed.

### Gate C - reviewed publication

Publication is a separate exact Git packet. It must bind the selected observer
and transport sources, tests, strategy/selection records, parent revision,
tree, path list, candidate schema/version, and hashes. It must require a clean
index, no foreign staged work, exact-path staging, review of the cached diff,
one commit, one push to `fuzzy/dev`, and independent remote readback. It grants
no package, host, deploy, public-IP, notification, or observation action.

### Gate D - separately authorized package/host change

After Gate C, root may prepare, but not execute, a new action-specific live GO
for one bounded package or host change. The packet must name the exact
published revision, selected candidate identity, target host class, package or
artifact identity, installation source, bounded command/action count, timeout,
maintenance/rollback preconditions, and redacted post-change readback schema.

The packet must include a tested rollback plan that restores the prior,
identified executable/package state without manually editing `/usr` or
retaining package-manager output. It must confirm access continuity before and
after the change through an allowlisted redacted readback. Failed package
verification, unsupported provenance, inability to snapshot/restore the prior
state, loss of access, mismatch between installed identity and selected
candidate, or any unexpected service effect stops the action and enters the
rollback/readback branch. It does not attempt a deployment or notification.

The readback may expose only fixed boolean predicates and bounded aggregate
counts, for example: expected candidate identity matches, required executable
resolves, source audit schema validates, `--no-deps` semantic predicate is
true, prior-state rollback readiness is true, and access readback is true. It
must not expose versions beyond the approved fixed identity, paths, raw source,
hostnames, package-manager transcripts, environment, credentials, IP values,
or private errors.

The package/host-change action is limited to the approved installation or
replacement and its bounded redacted readback. It must not invoke Compose or
Podman Compose, start a container, restart/recreate a container, restart or
otherwise act on a service, or perform a deployment. Any package hook or side
effect outside that bounded contract requires an immediate stop and rollback;
it is not an implicit permission to expand the action.

### Gate E - new one-use capability observation

Only a successful, redacted Gate-D readback on the published revision permits
preparation of a *new* one-use observation packet. It must bind the exact
published observer/transport hashes and installed candidate identity, use the
same one-command/one-result/no-retry/expiry limits as the earlier packet, and
require fresh remote/readback preflight. SEC153 is consumed and cannot be
reused. A strict observer `ok` remains capability evidence only; it does not
authorize deployment, public-IP discovery, alert delivery, or send.

## Required offline test matrix

The Gate-B test claim must cover at least:

| Case | Required result |
| --- | --- |
| Candidate source with exact `--no-deps` control over dependency expansion | semantic predicate true; eligible for strict `ok` only if every other proof passes |
| Debian 1.3.0 official-shaped source | `needs_live_observation` including `source_up_no_deps_guard_missing` |
| Help/parser flags without source branch | fail closed; no capability PASS |
| `--no-deps` branch that does not control expansion | fail closed; no capability PASS |
| Service-only branch but missing `--no-build` or rollback consumption | `needs_live_observation` with canonical missing proof(s) |
| Malformed/multi-record/extra-key source-audit payload | terminal blocked, no raw retention |
| Candidate schema/pin update attempt before candidate selection | rejected by tests/contract |
| Historical SEC153 observer and transport envelopes | continue to validate under their original exact schema and digest |
| Secrets, paths, IP-like sentinels, hostnames, raw source and exception text in fakes | absent from all serialized output |

## C2 and deployment ordering

`OPS-ALERT-C2-source-ip-context-and-self-egress-suppression` remains a
deployment predecessor. Its source-IP notification context, own-public-IP
freshness/equality safeguards, fail-open-to-notification behavior, and
critical-event non-suppression must be accepted before `OPS-ALERT-D` may
become deployment-ready. Compose remediation neither implements nor weakens
C2, and an eventual Compose capability PASS does not bypass it.

## Stop rules and owner decisions

Stop and return to the owner if candidate selection requires an unverified
implementation identity, a new third-party public-IP provider, a raw package
or source transcript, an unbounded source parser, a host/package action,
automatic fallback, a whole-stack expansion, an in-place system edit, or a
change outside the declared path set. Stop if candidate provenance cannot be
pinned, signature/digest verification cannot be expressed redactedly, the
semantic AST predicate is ambiguous, the negative Debian fixture turns green,
or rollback/access-readback cannot be made explicit.

If the preferred path cannot meet these gates, the only alternative is a
separate owner decision to open a Direct-Podman executor roadmap. That decision
must define an equivalent service/dependency isolation proof, transactional
rollback, redaction boundary, host-change packet, and independent observation;
it does not reuse this strategy as authority.

## Freeze handoff

Changed path:
`docs/plans/security-incident-response-sec154-compose-capability-remediation-strategy.md`
only.

Checks before handoff: exact contract review, SEC153 non-weakening review,
path-scope review, and `git diff --check` for this file. Not performed:
implementation, test edits, staging, commit, push, network, SSH, public-IP
query, package/artifact action, container action, host change, probe,
observation, retry, deployment, delivery, or send.
