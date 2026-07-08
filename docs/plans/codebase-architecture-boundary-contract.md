# Codebase Architecture Boundary Contract

Date: 2026-07-06

Status: ARC3 safe-offline boundary contract under Standard ABC

## Purpose

Reduce module sprawl by moving proven boundaries, not by inventing a new
architecture during cleanup. This contract defines the minimum safety bar before
any package move.

## Boundary Rules

1. A candidate package must have a current import map.
2. Public routes, plugin manifests and user-facing API schemas must remain
   stable.
3. Compatibility aliases must be added before consumers are migrated.
4. Behavior changes and file moves must be separate slices.
5. A move must name its focused tests before editing files.
6. Compatibility alias removal requires `ARC-COMPAT-REMOVAL-GO`.
7. Large domain-family moves require `ARC-BROAD-MOVE-GO`.

## Public API Expectations

Package-level `__init__.py` or facade files may be added only when consumers
are known. They should export stable constructors, validators, schema constants
or route setup functions, not raw private helpers.

## Compatibility Expectations

Old import paths should continue to work during the migration window. Alias
modules should be tiny and should not duplicate logic. Deleting an alias is a
release/design decision because plugins and local integrations may depend on
old paths.

## Characterization Expectations

Each package move needs:

- pre-move import map evidence;
- compile checks for moved and alias files;
- focused domain tests;
- plugin load or route-contract tests when plugin/route-facing imports move;
- post-move import map comparison.

## No-Go Conditions

- broad moves without inventory;
- route path or schema rename inside an architecture cleanup slice;
- compatibility alias deletion without gate approval;
- test failures hidden by narrowing coverage;
- live/deploy/provider/file mutation during cleanup.
