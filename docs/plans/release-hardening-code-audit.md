# Release Hardening Code Audit

Stand: 2026-06-19

Status: **read-only audit for ABC3 technical follow-up slices**

## Goal

Map the five release-hardening gates from `docs/plans/release-hardening-gates.md` to concrete code, test, and documentation anchors without changing runtime behavior.

## Summary

The audit supports the current release decision:

- external `1.0.0` remains No-Go until manual Provider Proof and Test-Vault Export/Import/Rebuild evidence land;
- large-vault claims remain No-Go until larger synthetic evidence is recorded;
- graph/filter state, security disclosure, project-apply conflict blocking, and link hygiene are useful but still Partial for release-hardening purposes.

## Gate Findings

### Large-Vault Performance

Existing anchors:

- `plugins/obsidian/backend/performance_fixtures.py`
- `plugins/obsidian/tests/test_vault_performance_baseline.py`
- `tests/test_query_budgets.py`
- `tests/test_progressive_graph_api.py`
- `docs/obsidian/00-priorisierte-roadmap.md`

Current gap:

- The current Obsidian performance baseline covers a small/medium synthetic graph, not a named 10k-file or 1GB-scale release claim.
- Missing evidence includes p95 query/search/filter/graph latency and rebuild/index maximum duration.

Recommended slice:

- `ABC3A-performance-gate`

### Graph And Filter State Isolation

Existing anchors:

- `plugins/obsidian/frontend/main.js`
- `tests/test_obsidian_sidebar_static.py`
- `plugins/obsidian/README.md`

Current gap:

- Graph/filter state uses browser-local state that is not yet evidenced as scoped by vault, owner, project, or view.
- Project switching, reload, and multi-tab isolation need synthetic evidence.

Recommended slice:

- `ABC3B-graph-filter-state`

### At-Rest Security Disclosure

Existing anchors:

- `README.md`
- `plugins/obsidian/README.md`
- `plugins/obsidian/SECURITY.md`
- `plugins/obsidian/frontend/main.js`
- `tests/test_obsidian_sidebar_static.py`

Current gap:

- Password-flow warning exists, but release hardening still needs persistent UI/status disclosure for derived indexes, caches, logs, and metadata.

Recommended slice:

- `ABC3C-security-ui-docs`

### Project Apply And Merge Conflict Blocking

Existing anchors:

- `plugins/obsidian/backend/project_planning.py`
- `plugins/obsidian/plugin.py`
- `plugins/obsidian/backend/routes.py`
- `plugins/obsidian/tests/test_project_planning_backend.py`
- `docs/obsidian/00-priorisierte-roadmap.md`

Current gap:

- Conflict blocking exists and is safer than initially feared.
- The release gate still needs a strict-block matrix across tool, route, session, selected apply, and explicit overwrite flows.
- Explicit overwrite semantics must remain separate from strict default blocking.

Recommended slice:

- `ABC3D-strict-conflict-block-matrix`

### Repository And Link Hygiene

Existing anchors:

- `docs/plans/abc-prioritized-execution-roadmap.md`
- `docs/plans/origin-publish-hygiene.md`
- `README.md`
- `plugins/obsidian/README.md`
- `package.json`
- `docs/obsidian/00-priorisierte-roadmap.md`

Current gap:

- Operator docs intentionally mention both upstream/original and fork remotes, but release-facing paths still need an offline link audit and retention map.
- Historical typo risks should be marked as "legacy / do not use" or removed from operator paths.

Recommended slice:

- `ABC3E-repo-link-audit`

## Parallelization Guidance

Safe to run in parallel:

- `ABC3A-performance-gate`
- `ABC3C-security-ui-docs`
- `ABC3E-repo-link-audit`

Coordinate before editing:

- `ABC3B-graph-filter-state`, because it touches graph/UI state.
- `ABC3D-strict-conflict-block-matrix`, because it touches conflict semantics and must not run in parallel with merge/overwrite productization.

## Verification

Current read-only audit did not run runtime tests.

Follow-up test anchors:

- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_release_hardening_gates.py`
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest plugins\obsidian\tests\test_vault_performance_baseline.py`
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_obsidian_sidebar_static.py`
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest plugins\obsidian\tests\test_project_planning_backend.py`

## Handoff

Bob's audit result is captured here as a durable repo artifact. Charlie can now dispatch the next technical slices without depending on sidechat memory.
