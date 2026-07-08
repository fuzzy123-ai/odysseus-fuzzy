# Codebase Architecture Cleanup Integration Review

Date: 2026-07-06

Status: ARC1-ARC6 integration review under Standard ABC

## Scope

This review covers the repo-only Codebase Architecture Cleanup artifacts from
ARC1 through ARC5 and closes ARC6 as a gate/handoff review. It verifies that
dependency inventory, static import-map tooling, boundary rules, one small
package move and compatibility aliases are integrated without broad module
re-layout, route/schema changes, alias removal, live actions, deploy actions or
behavior changes.

Out of scope:

- Moving large domain families.
- Removing old import paths or public aliases.
- Renaming route paths, plugin manifests or user-facing schemas.
- Combining behavior changes with file moves.
- Running live providers, host commands, deployment, backup, restore or write
  smokes.

## Integration Map

| Area | Artifact | Integration evidence |
| --- | --- | --- |
| Dependency inventory | `docs/plans/codebase-architecture-dependency-inventory.md` | Candidate domains and cleanup posture are documented before any move. |
| Import map tooling | `scripts/architecture_import_map.py` | AST-based static scanner parses repo files without importing modules and reports no side effects, no executed imports and no file moves. |
| Import map tests | `tests/test_architecture_import_map.py` | Tests cover local edge detection, parse-error tolerance and domain classification. |
| Boundary contract | `docs/plans/codebase-architecture-boundary-contract.md` | Move rules require import-map evidence, compatibility aliases and separate behavior/move slices. |
| First package move | `src/operator_dashboard/` | Operator dashboard backend models moved behind a package facade without route or schema changes. |
| Compatibility aliases | `src/operator_dashboard_snapshot.py`, `src/operator_review_queue.py` | Legacy import paths re-export moved implementations and have focused alias tests. |
| Consumer update | `routes/operator_dashboard_routes.py` | Route imports through the new package facade while preserving `GET /api/operator-dashboard/snapshot`. |

## Safety Guarantees

The repo-only integration keeps these invariants:

- no broad domain-family moves were performed;
- no compatibility alias was removed;
- no public route path or schema was renamed;
- no plugin manifest was changed for architecture cleanup;
- no module import was executed by the architecture import-map scanner;
- no file mutation is performed by the scanner;
- the moved operator dashboard models keep legacy import compatibility;
- the moved route/model behavior remains covered by focused route and model
  tests.

## Verification

Focused compile:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m py_compile src\operator_dashboard\__init__.py src\operator_dashboard\snapshot.py src\operator_dashboard\review_queue.py src\operator_dashboard_snapshot.py src\operator_review_queue.py routes\operator_dashboard_routes.py tests\test_operator_dashboard_snapshot.py tests\test_operator_review_queue.py tests\test_operator_dashboard_routes.py scripts\architecture_import_map.py tests\test_architecture_import_map.py
```

Focused model/route/architecture suite:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_operator_dashboard_snapshot.py tests\test_operator_review_queue.py tests\test_operator_dashboard_routes.py tests\test_architecture_import_map.py -q
```

Post-move import-map smoke:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -c "from pathlib import Path; from scripts.architecture_import_map import build_import_map; p=build_import_map(Path('.')); assert p['schema']=='odysseus.architecture_import_map.v1'; assert p['files_moved'] is False; assert p['imports_executed'] is False; print(p['scanned_file_count'], p['module_count'], p['parse_error_count'], p['local_cross_domain_edge_count'])"
```

Result for this review: focused compile passed; the focused suite passed with
14 tests and only the known SQLAlchemy `declarative_base()` deprecation
warning. The post-move import-map smoke returned 848 Python files scanned, 845
modules parsed, 3 parse errors recorded and 1333 local cross-domain edges.

## Deferred Gates

Gate: `ARC-BROAD-MOVE-GO`

State after this review: blocked/deferred

Required before broad move: explicit operator approval for exactly one domain
family, pre-move import-map snapshot, focused characterization tests, alias
plan, rollback plan and post-move import-map comparison.

Gate: `ARC-COMPAT-REMOVAL-GO`

State after this review: deferred

Required before alias removal: compatibility duration decision, release notes,
consumer search proving no in-repo use remains, plugin/integration risk
acceptance and focused import tests.

## Conclusion

Roadmap 10 is repo-only complete for the current safe architecture-cleanup
track. The repo now has an inventory, a static import-map generator, a boundary
contract, one small package move and compatibility aliases. Further cleanup is
intentionally parked behind explicit broad-move and alias-removal gates.
