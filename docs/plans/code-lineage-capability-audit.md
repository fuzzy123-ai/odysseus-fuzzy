# Code Lineage Capability Audit

Stand: 2026-07-18

Status: `go_clt_01_contract_only`

- Audit digest: `sha256:09e4d6ee4d7584e8f119ccf67639657cdbf67d1d9b953d77ff87f430f37126db`
- Python source files inspected: `684`
- Git commands executed: `0`
- Subprocesses executed: `0`
- Live actions: `0`
- Default wording: `first observable in available history`

This is a static AST/text audit. It does not inspect real repository history,
run Git, export identities, contact a network service or change Git state. Its
Go applies only to the repository-local CLT-01 contract slice.

## Canonical Capabilities

| Module | API | Capability | Present | Decision |
| --- | --- | --- | --- | --- |
| `src/project_forge_local.py` | `LocalProjectForge.store_commit` | retain existing commit | yes | reuse |
| `src/project_forge_local.py` | `LocalProjectForge.verify_version` | verify retained commit | yes | reuse |
| `src/project_version_store.py` | `ProjectVersionStore.iter_verified_versions` | ordered verified versions | yes | reuse |
| `src/project_version_store.py` | `ProjectVersionStore.load_version` | immutable version load | yes | reuse |
| `src/project_version_store.py` | `ProjectVersionStore.verify_version` | immutable version verification | yes | reuse |
| `src/repo_git_adapter.py` | `RepoGitAdapter.changed_paths` | working-tree changed paths | yes | reuse |
| `src/repo_git_adapter.py` | `RepoGitAdapter.current_branch` | current branch | yes | reuse |
| `src/repo_git_adapter.py` | `RepoGitAdapter.diff_stat` | working-tree diff stat | yes | reuse |
| `src/repo_git_adapter.py` | `RepoGitAdapter.log` | bounded recent commit log | yes | reuse |
| `src/repo_git_adapter.py` | `RepoGitAdapter.remotes` | redacted remotes | yes | reuse |
| `src/repo_git_adapter.py` | `RepoGitAdapter.snapshot` | bounded repository snapshot | yes | reuse |
| `src/repo_git_adapter.py` | `RepoGitAdapter.status` | working-tree status | yes | reuse |
| `src/repo_git_adapter.py` | `git_read_command_is_allowed` | read-command allowlist | yes | reuse |
| `src/repo_git_adapter.py` | `run_git_read_subprocess_command` | canonical read process boundary | yes | reuse |

No required canonical capability is missing. Project Version Store remains the
immutable version authority, Repo Registry remains the repository identity
authority and Repo Git Adapter remains the read authority.

## Direct Git Process Boundaries

| Module | Function | Role | Decision |
| --- | --- | --- | --- |
| `src/project_forge_local.py` | `LocalProjectForge._run_git` | canonical commit retention and verification | retain existing boundary |
| `src/recent_changes.py` | `_run_git` | uncatalogued direct Git process boundary | route to canonical adapter |
| `src/repo_commit_runner.py` | `run_git_commit_subprocess_command` | canonical commit mutation boundary | retain existing boundary |
| `src/repo_git_adapter.py` | `run_git_read_subprocess_command` | canonical read adapter | retain existing boundary |
| `src/repo_push_runner.py` | `run_git_push_subprocess_command` | canonical push mutation boundary | retain existing boundary |
| `src/server_project_commit_runner.py` | `run_git_commit_subprocess_command` | uncatalogued direct Git process boundary | route to canonical adapter |
| `src/server_project_push_runner.py` | `run_git_push_subprocess_command` | uncatalogued direct Git process boundary | route to canonical adapter |
| `src/server_project_repo_provisioner.py` | `run_git_subprocess_command` | uncatalogued direct Git process boundary | route to canonical adapter |
| `src/version_info.py` | `_git` | uncatalogued direct Git process boundary | route to canonical adapter |
| `src/version_info.py` | `_git_is_ancestor` | uncatalogued direct Git process boundary | route to canonical adapter |

The six uncatalogued modules are review findings, not authorization to edit
their owners' paths. CLT adds no parallel Git subprocess route. Existing
commit/push authorities remain out of scope and are never called by lineage.

## Required Adapter Extensions

All missing history facts extend the canonical read adapter only after its
owner handoff:

- `bounded_revision_graph`: commit IDs, parent IDs, `authored_at` and
  `committed_at` for an explicit revision range;
- `historical_path_changes`: typed add/modify/delete/rename/copy evidence
  between explicit revisions;
- `blob_and_object_metadata`: bounded blob IDs and object-presence facts,
  without source bodies;
- `history_boundary_state`: shallow, missing-object and rewritten-range
  evidence.

Immutable versions reuse Project Version Store. Repository identity and roots
reuse Repo Registry. Commit, push, fetch and branch mutation remain excluded.

## Frozen Truth Language

| Field | Meaning |
| --- | --- |
| `first_seen_at` | first observation by Odysseus |
| `history_first_observed_at` | earliest reachable supporting revision |
| `authored_at` | Git author timestamp; never topology order |
| `committed_at` | Git committer timestamp; never creation proof |
| `indexed_at` | time evidence was indexed |
| `valid_from` | start of one evidence validity window |
| `valid_to` | end of one evidence validity window |

Confidence stays method-specific: exact same-blob continuation is not merged
with Git rename candidates, structural candidates, bounded diff overlap, copy
candidates or semantic hints. A semantic candidate can aid discovery but can
never establish accepted lineage alone. Branch, merge, split, copy, deletion
and resurrection therefore remain representable without forcing one parent.

## Uncertainty, Privacy And Scope

- Shallow history means earliest reachable evidence, not creation.
- Rewritten history invalidates the affected indexed generation.
- Missing objects produce partial or unknown results.
- Imported, vendored and generated code retains explicit policy markers.
- Author name/email, absolute paths, source bodies and raw Git output are
  excluded by default.
- Commit subjects are not required for lineage identity.
- `current` queries use only the selected current USI snapshot.
- `historical` and `deleted` queries require an explicit bounded revision
  range and policy.
- `all_history` is an explicit bounded union, never the default.

## Acceptance Evidence

- Focused audit suite: `15 passed`.
- Classification regression subset: `4 passed`.
- Static report status: `go_clt_01_contract_only`.
- False-positive guard proves unrelated Tesseract-style subprocesses are not
  labelled as Git from module-level text.
- BOM guard accepts ordinary UTF-8 and UTF-8 with BOM without modifying source.
- The audit reports `0` executed Git commands, subprocesses and live actions.

CLT-00 is accepted when the final integrated suite remains green. CLT-01 may
then define pure record types; CLT-02 still requires an explicit Git-adapter
owner handoff and stays blocked from creating a second command path.
