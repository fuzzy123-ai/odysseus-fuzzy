# SEC150 exact seven-path publication-readiness packet

Run: `ABC-SEC150-20260729-PUBLICATION-READINESS`
Phase: `exact_path_scoped_publication_readiness`
Mutation authority for this artifact: `repo_only`

This is a publication-readiness request packet, not Git or external-action
authority. It prepares one later, exact path-scoped publication of the accepted
SEC149 transport candidate. It does not itself authorize or begin staging,
commit, push, network access, public-IP query, SSH, probe, observation, retry,
provider access, deployment, delivery, send, packaging, container work, or
host work.

## Immutable predecessor and destination

Every later publication preflight must independently confirm all of these
values before staging. Any mismatch is terminal for the proposed publication.

- Local branch: `dev`
- Required parent and pre-publication `HEAD`:
  `2a3b3bd93143cc03f4c267cdcedfc54b93fd5b56`
- Required pre-publication remote revision: `fuzzy/dev` at
  `2a3b3bd93143cc03f4c267cdcedfc54b93fd5b56`
- Only permitted later destination: `fuzzy/dev`
- Accepted candidate transport SHA-256:
  `fdbbb0a5103eca34d0a1b96e55f34d45f34ef7e83493fa1f7cafe3c772de44a3`

No other parent, branch, remote, ref, transport content, or destination is
interchangeable with this binding.

## Exact candidate paths

The candidate publication set is exactly these seven paths, in this order:

1. `docs/plans/security-incident-response-production-completion-roadmap.json`
2. `docs/plans/security-incident-response-sec146-compose-observation-packet.md`
3. `docs/plans/security-incident-response-sec148-transport-branch-disambiguation-strategy.md`
4. `docs/plans/security-incident-response-sec150-publication-readiness-packet.md`
5. `ops/homeserver/redacted_podman_compose_capability_transport.py`
6. `tests/test_homeserver_redacted_podman_compose_capability_transport.py`
7. `tests/test_security_incident_response_production_completion_roadmap.py`

All other tracked, staged, unstaged, ignored, or untracked paths are foreign to
SEC150 and must remain excluded. A listed path must also be rejected if it
contains a foreign, overlapping, generated, or unexplained hunk.

## Required later preflight

A separately authorized publication executor must perform and retain a bounded
result for every item below before the first Git mutation:

1. Confirm branch `dev`, remote `fuzzy`, destination branch `dev`, and that
   local `HEAD` and `fuzzy/dev` both equal the immutable predecessor.
2. Confirm the real Git index is empty. An alternate index, assumed-clean
   index, or path-limited cached view is not acceptable.
3. Confirm the exact path-scoped dirty set equals the seven candidate paths
   above. Inspect every candidate diff and confirm it contains no foreign or
   overlapping hunk. Unrelated dirty paths outside the set may remain present
   but must not enter the publication.
4. Recompute the candidate transport file SHA-256 and require the accepted
   value above.
5. Parse
   `docs/plans/security-incident-response-production-completion-roadmap.json`
   as JSON after the SEC150 handoff, and require zero claims whose state is not
   terminal or released.
6. Compile the observer and transport without invoking their live paths:

   ```text
   C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m py_compile ops\homeserver\redacted_podman_compose_capability_observation.py ops\homeserver\redacted_podman_compose_capability_transport.py
   ```

7. Run the exact focused observer/transport lane and require all 36 tests to
   pass:

   ```text
   C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_homeserver_redacted_podman_compose_capability_observation.py tests\test_homeserver_redacted_podman_compose_capability_transport.py
   ```

8. Run the roadmap suite while excluding only the known broadly stale
   evidence-manifest assertion. No other deselection, xfail, ignored failure,
   or weaker lane is permitted:

   ```text
   C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_security_incident_response_production_completion_roadmap.py --deselect=tests/test_security_incident_response_production_completion_roadmap.py::test_sirp_handoff_evidence_manifest_matches_current_claimed_files
   ```

9. Run `git diff --check` against exactly the seven candidate paths and require
   no whitespace error.

Any failed, unavailable, interrupted, cancelled, contradictory, or unknown
preflight result stops the publication without staging, cleanup, retry,
fallback, or scope expansion.

## Candidate digest and tree binding

Only after all preflight checks pass and a separate publication authority is
present may the executor stage the seven exact paths. It must then:

1. require the cached path inventory to equal the seven-path list exactly;
2. inspect the complete cached diff for foreign or overlapping hunks;
3. compute and retain `candidate_diff_sha256` over the exact raw bytes produced
   by `git diff --cached --binary --no-ext-diff --` with the seven paths in the
   order listed above;
4. compute and retain `candidate_tree` with `git write-tree`;
5. re-run the cached diff check; and
6. require the staged transport blob to retain SHA-256
   `fdbbb0a5103eca34d0a1b96e55f34d45f34ef7e83493fa1f7cafe3c772de44a3`.

The resulting commit must have the immutable predecessor as its sole parent,
contain exactly the retained `candidate_tree`, and correspond to the retained
candidate diff. A mismatch stops before push. The digest and tree are evidence
bindings, not publication authority.

## Later one-commit and one-push authority

Only after root presents this exact packet context as accepted may the owner
approve the bounded Git action with a plain `weiter`. Such an approval would
authorize:

- exactly one commit containing only the accepted seven-path candidate;
- exactly one push of that commit to `fuzzy/dev`; and
- only the independent readbacks specified below.

The authority would be single-use, bound to this predecessor, candidate,
destination, and run, and expire at `RUN_END`. It would not authorize a second
commit, retry, amend, rebase, merge, force push, cleanup, alternate remote,
alternate branch, or any live action. This packet alone grants no Git action,
commit, or push.

## Independent post-push readback

After a separately authorized one-commit/one-push action, the executor must
independently read back and require all of the following:

- `refs/heads/dev` from remote `fuzzy` equals the new local commit revision;
- the new commit has sole parent
  `2a3b3bd93143cc03f4c267cdcedfc54b93fd5b56`;
- the remote revision resolves to the retained `candidate_tree`;
- its changed-path inventory is exactly the seven candidate paths; and
- the published transport blob SHA-256 equals
  `fdbbb0a5103eca34d0a1b96e55f34d45f34ef7e83493fa1f7cafe3c772de44a3`.

A commit command, successful push process, local tracking reference, or agent
prose is not a substitute for independent remote revision, tree, path, and
published transport-hash readback. A readback mismatch or unavailable task
status means publication success is not verified and permits no retry.

## Separate gates remain closed

This packet and any later Git publication grant no network access, public-IP
query, SSH, probe, observation, retry, provider, deployment, delivery, send,
package, container, host, credential, or authentication action. A corrected
transport publication does not prove Compose capability and does not satisfy
`deploy-live-go` or `OPS-ALERT-DELIVERY-GO`.

The enclosing Codex task and goal status must be checked independently before
any completion claim. A failed, blocked, interrupted, cancelled,
contradictory, unavailable, or unknown task/goal state is not overall run
success even when Git commit, push, and remote readback individually pass.

## Freeze handoff

Changed path:
`docs/plans/security-incident-response-sec150-publication-readiness-packet.md`
only.

Checks before handoff: exact Markdown contract and path-scope review, followed
by `git diff --check` for this file. Not performed by this packet: staging,
commit, push, publication, network access, public-IP query, SSH, probe,
observation, retry, provider call, deployment, delivery, send, package action,
container action, or host change.
