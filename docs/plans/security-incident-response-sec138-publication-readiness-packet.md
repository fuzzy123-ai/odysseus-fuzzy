# SEC139 exact SEC138 publication-readiness packet

Run: `ABC-SEC139-20260729-PUBLICATION-READINESS`

Status: `exact_seven_path_publication_authorized_running`. The owner response
`Weiter` at `2026-07-29T19:09:07+02:00` authorizes one exact seven-path commit
and one push to `fuzzy/dev`, expiring at run end. It grants no SSH, live
observation, retry, deploy, delivery, provider, package, container or host
action.

## Bound predecessor

- Local branch: `dev`
- Required remote and branch: `fuzzy/dev`
- Candidate parent and current published revision:
  `d91b2fb695b32c57235c971e47d4f50e5d7bbb86`
- SEC137 one-use observation grant: consumed and not reusable
- SEC138 review: deep Sol review passed after three rounds

Publication must stop if `HEAD` or `refs/remotes/fuzzy/dev` differs from the
bound predecessor, if the real index is non-empty, if a candidate path contains
foreign or overlapping work, or if any required check fails.

## Exact candidate paths

Only these seven paths may enter a later path-scoped publication:

1. `docs/plans/security-incident-response-production-completion-roadmap.json`
2. `docs/plans/security-incident-response-runtime-shape-diagnostic-packet.md`
3. `docs/plans/security-incident-response-wrapped-usage-guard-repair-packet.md`
4. `docs/plans/security-incident-response-sec138-publication-readiness-packet.md`
5. `ops/homeserver/redacted_podman_compose_capability_observation.py`
6. `ops/homeserver/redacted_podman_compose_capability_transport.py`
7. `tests/test_homeserver_redacted_podman_compose_capability_observation.py`

The claimed transport test path is unchanged from the published parent and must
not be staged merely for symmetry. All unrelated working-tree paths remain
foreign and excluded.

## Current content bindings

The mutable roadmap and this self-describing packet are bound by the later
cached-diff and tree readback. Current non-self-referential file SHA-256 values:

- runtime-shape packet:
  `461a3748e9b92e0f12091b4efd9884ec9af32fd636e800decafaa66ab14d7569`
- SEC138 repair packet:
  `8e0d3529ae9875245fde4cda7d49b7820f014946a17f99dfbb27e1478cc04e88`
- observer:
  `af8e688ae86e1406a55f51d521f746d15b9300d399caeaa4698e53bd133bd46c`
- transport:
  `9dfc48f746fe95515776552fedd15d846ac72b53eeecc3e53d414cb166a76dd3`
- observation tests:
  `eefccd4ad029d4d6f032285be74465944327cd7c8d8188ecd198259beab4f5e0`

The transport pin must equal the observer SHA-256.

## Required publication checks

Before any later Git action:

1. revalidate JSON and both SEC138 claims released with zero active claims;
2. re-run Python compilation and the two focused observer/transport test files;
3. re-run `git diff --check` for the seven candidate paths;
4. confirm branch `dev`, remote `fuzzy`, remote branch `dev`, and equal local
   `HEAD`/`fuzzy/dev` predecessor;
5. confirm the real index is empty;
6. inspect the exact path-scoped diff and stage only the seven listed paths;
7. inspect the cached path inventory and cached diff before commit;
8. after commit, bind revision, parent, tree and observer hash;
9. push only to `fuzzy/dev`, then independently read back the remote revision,
   tree and published observer hash.

Any mismatch stops without cleanup, retry, deployment or observation.

## Remaining gates

Publication does not authorize the next observation. After a successful remote
readback, a new action-specific read-only observation grant would be required
for exactly one no-argument redacted transport invocation with bounded timeout,
zero follow-on queries and no retry.

That observation still would not authorize deployment or Telegram delivery.
`deploy-live-go` and `OPS-ALERT-DELIVERY-GO` remain separate action-specific
decisions with their own artifact, target, rollback/readback and one-send
contracts.
