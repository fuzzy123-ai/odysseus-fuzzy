# Project Apply Conflict Gate Evidence

Stand: 2026-06-19

Status: **read-only evidence summary for `ABC3D-strict-conflict-block-matrix`**

## Decision

The current Project Apply flow is strict by default. Conflicting writes are blocked unless the operator supplies an explicit overwrite path and conflict confirmation.

This is a Go for the strict-default claim, with a documented exception path for deliberate overwrite.

## Code Anchors

- Conflict collection happens while validating planned files that already exist in the vault: `plugins/obsidian/backend/project_planning.py`.
- `prepare_project_plan_for_apply(...)` only accepts `overwrite_paths` when they are in the prepared plan and correspond to real conflicts.
- Apply logic blocks any conflict that is not explicitly present in `overwrite_paths`.
- The final per-file write guard aborts if a file already exists and is not in the overwrite set.
- Tool handling requires `confirm_conflicts=true` when `overwrite_paths` are supplied.
- Route/session apply paths reuse the same helper policy.

## Existing Test Coverage

Existing tests cover:

- strict default block on conflict
- tool-level explicit overwrite confirmation
- session-route explicit overwrite confirmation
- route normalization of `overwrite_paths`
- rejection of unknown or non-conflicting overwrite paths
- selected apply where unselected conflicts do not block selected non-conflicting writes

## Minimal Matrix To Keep Green

1. `confirm=true`, no conflicts, no `overwrite_paths`: success and only new files written.
2. Conflict exists, no `overwrite_paths`: hard block, no writes.
3. Conflict exists, `overwrite_paths` present, `confirm_conflicts=false`: hard block before write.
4. Conflict exists, exact `overwrite_paths`, `confirm_conflicts=true`: only that file may be overwritten; other conflicts still block.
5. `overwrite_paths` references unknown or non-conflicting path: validation error.
6. `selected_paths` excludes the conflict file: selected non-conflicting apply may proceed.

## Go / Partial / No-Go

Go:
- strict default remains fail-closed
- overwrite exception requires exact path plus explicit conflict confirmation
- route, tool, and session paths preserve the same policy

Partial:
- strict behavior is present, but one route lacks focused regression evidence

No-Go:
- any conflict can write without explicit overwrite and confirmation
- unknown or non-conflicting overwrite paths are accepted
- partial writes occur after a blocked conflict

## Next Action

Keep this as a regression gate. Do not broaden overwrite behavior while `ABC3B` graph/filter state remains unresolved.
