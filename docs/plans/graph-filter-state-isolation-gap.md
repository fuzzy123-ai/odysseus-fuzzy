# Graph Filter State Isolation Gap

Stand: 2026-06-19

Status: **No-Go for isolated graph/filter state claims**

## Decision

Graph/filter state isolation is not yet evidenced. The current frontend keeps graph filters, lens mode, filter panel state, outside-click handling, and Cytoscape instance state as module-level singletons in `plugins/obsidian/frontend/main.js`.

This does not prove data loss, but it is enough to block release claims such as "independent project graph views" or "safe multi-project graph filtering."

## Risk Anchors

- `graphFilterState` and `graphLensMode` are top-level state and are persisted through one global `localStorage` key.
- Filter event handlers mutate the same singleton state and rerender the graph.
- `graphFilterPanelOpen` and `graphFilterOutsideClickHandler` are top-level and reused across renders.
- `graphCytoscapeInstance` and `graphCytoscapeLoadPromise` are global renderer state.
- Delayed animation work references the current global Cytoscape instance rather than a render-local instance.
- Existing tests are mostly static string checks; they do not prove scoped state, scoped persistence, or render-race safety.

## Required Slice

The smallest safe implementation slice is:

- isolate only Graph View state first, not the entire Obsidian frontend
- move graph filter state, lens mode, panel state, outside-click handler, and Cytoscape instance behind a small graph-view state object
- scope persistence keys by graph context instead of using one global filter key
- bind delayed Cytoscape callbacks to the instance or render id that created them

## Required Tests

At minimum:

- static test that module-wide `let graphFilterState =` and `let graphLensMode =` are no longer the active source of truth
- static test that persistence goes through a scope-key helper
- Node-driven behavior test for independent read/write across two graph scopes
- Node-driven behavior test that reset returns fresh nested state objects
- Node-driven behavior test that delayed callbacks cannot mutate a newer Cytoscape instance
- handler lifecycle test that outside-click handling belongs to the current render

## Go / Partial / No-Go

Go:
- graph/filter state is scoped and behavior-tested
- active filters are visible or clearable per graph context
- delayed renderer callbacks are bound to the intended instance

Partial:
- visible filter state exists, but persistence or renderer race isolation remains static-only

No-Go:
- module-level singletons continue to drive cross-context graph/filter behavior
- one global localStorage key controls unrelated graph contexts
- old render callbacks can affect newer graph instances

## Next Action

Treat `ABC3B` as the next technical hardening slice before public wording about independent graph/project views. This should be a focused frontend/test change, not a broad UI refactor.
