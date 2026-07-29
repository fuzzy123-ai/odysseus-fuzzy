# SEC145 exact seven-path publication-readiness packet

Run: `ABC-SEC145-20260729-PUBLICATION-READINESS`
Phase: `exact_path_scoped_publication_readiness`
Mutation authority for this artifact: `repo_only`

This is a publication-readiness request packet, not Git or external-action
authority. It prepares one later, path-scoped publication of an already
accepted repository candidate. It does not itself authorize or begin staging,
commit, push, network access, live observation, deployment, delivery, provider
access, packaging, container work, or host work.

## Immutable predecessor and destination

Every later publication preflight must independently confirm all of these
values before staging. Any mismatch is terminal for the proposed publication.

- Local branch: `dev`
- Required parent and pre-publication `HEAD`:
  `9ea87e67464015cedbeeaada9117899edcab3ae2`
- Required pre-publication remote revision: `fuzzy/dev` at
  `9ea87e67464015cedbeeaada9117899edcab3ae2`
- Only permitted later destination: `fuzzy/dev`
- Accepted candidate transport SHA-256:
  `630e460799f4a940f582cb6b4396a13902d32c080d7d7a22176256f2c92bbe79`

No other parent, branch, remote, ref, transport content, or destination is
interchangeable with this binding.

## Exact candidate paths

The candidate publication set is exactly these seven paths, in this order:

1. `docs/plans/security-incident-response-production-completion-roadmap.json`
2. `docs/plans/security-incident-response-sec138-publication-readiness-packet.md`
3. `docs/plans/security-incident-response-sec143-compose-observation-packet.md`
4. `docs/plans/security-incident-response-sec145-publication-readiness-packet.md`
5. `ops/homeserver/redacted_podman_compose_capability_transport.py`
6. `tests/test_homeserver_redacted_podman_compose_capability_transport.py`
7. `tests/test_security_incident_response_production_completion_roadmap.py`

All other tracked, staged, unstaged, ignored, or untracked paths are foreign to
SEC145 and must remain excluded. A matching filename is insufficient if a
candidate path contains a foreign or overlapping hunk.

## Required later preflight

A separately authorized publication executor must perform and retain a bounded
result for every item below before the first Git mutation:

1. Confirm branch `dev`, remote `fuzzy`, destination branch `dev`, and that
   local `HEAD` and `fuzzy/dev` both equal the immutable predecessor.
2. Confirm the real Git index is empty. An alternate index, assumed-clean
   index, or path-limited cached view is not acceptable.
3. Confirm the exact path-scoped dirty set equals the seven candidate paths
   above. Inspect each candidate diff and confirm there are no foreign,
   overlapping, generated, or unexplained hunks. Unrelated dirty paths outside
   the set may remain present but must not enter the publication.
4. Recompute the candidate transport file SHA-256 and require the accepted
   value above.
5. Parse
   `docs/plans/security-incident-response-production-completion-roadmap.json`
   as JSON after the SEC145 handoff, and require zero claims whose state is not
   terminal or released.
6. Compile the observer and transport without executing either live path:

   ```text
   C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m py_compile ops\homeserver\redacted_podman_compose_capability_observation.py ops\homeserver\redacted_podman_compose_capability_transport.py
   ```

7. Run the exact focused observer/transport suite and require all 32 tests to
   pass:

   ```text
   C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_homeserver_redacted_podman_compose_capability_observation.py tests\test_homeserver_redacted_podman_compose_capability_transport.py
   ```

8. Run the roadmap suite while excluding only the known broadly stale
   evidence-manifest assertion. No other deselection, xfail, or ignored failure
   is permitted:

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
5. re-run the cached diff check and require the accepted transport blob to
   retain SHA-256
   `630e460799f4a940f582cb6b4396a13902d32c080d7d7a22176256f2c92bbe79`.

The resulting commit must have the immutable predecessor as its sole parent,
must contain exactly the retained `candidate_tree`, and must correspond to the
retained candidate diff. A mismatch stops before push. The digest and tree are
evidence bindings, not publication authority.

## Later one-commit and one-push authority

Only after root presents this exact packet context may the owner approve the
bounded Git action with a plain `weiter`. Such an approval would authorize:

- exactly one commit containing only the accepted seven-path candidate;
- exactly one push of that commit to `fuzzy/dev`; and
- only the independent readbacks specified below.

The authority would be single-use, bound to this predecessor, candidate,
destination, and run, and expire at `RUN_END`. It would not authorize a second
commit, retry, amend, rebase, merge, force push, cleanup, alternate remote,
alternate branch, or any live action. This packet alone grants none of those
actions and grants no commit or push.

## Independent post-push readback

After a separately authorized one-commit/one-push action, the executor must
independently read back and require all of the following:

- `refs/heads/dev` from remote `fuzzy` equals the new local commit revision;
- the new commit has sole parent
  `9ea87e67464015cedbeeaada9117899edcab3ae2`;
- the remote revision resolves to the retained `candidate_tree`;
- its changed-path inventory is exactly the seven candidate paths;
- the published transport blob SHA-256 equals
  `630e460799f4a940f582cb6b4396a13902d32c080d7d7a22176256f2c92bbe79`.

A commit command, successful push process, local tracking reference, or agent
prose is not a substitute for the independent remote revision, tree, path, and
published transport-hash readback. A readback mismatch or unavailable task
status means publication success is not verified and permits no retry.

## Separate gates remain closed

This packet and any later Git publication grant no SSH, probe, observation,
provider, deployment, delivery, send, package, container, host, credential, or
authentication action. In particular, a corrected publication does not grant
the next one-use observation and does not satisfy `deploy-live-go` or
`OPS-ALERT-DELIVERY-GO`.

The enclosing Codex task and goal status must be checked before any completion
claim. A failed, blocked, interrupted, cancelled, contradictory, unavailable,
or unknown task/goal state is not overall run success even when Git commit,
push, and remote readback individually pass.

## Freeze handoff

Changed path:
`docs/plans/security-incident-response-sec145-publication-readiness-packet.md`
only.

Checks before handoff: exact Markdown contract and path-scope review, followed
by `git diff --check` for this file. Not performed by this packet: staging,
commit, push, network access, live observation, deployment, delivery, provider
call, package action, container action, host change, or retry.
