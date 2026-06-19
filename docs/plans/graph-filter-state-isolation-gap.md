# Graph Filter State Isolation Gap

Stand: 2026-06-19

Status: **Partial; focused implementation and tests present, browser smoke pending**

## Decision

Graph/filter state isolation now has a focused implementation slice in `plugins/obsidian/frontend/main.js`.

The previous module-level singletons for graph filters, lens mode, filter panel state, outside-click handling, and Cytoscape instance state were moved behind a small graph-view state object. Filter persistence is scoped by graph context, and delayed Cytoscape callbacks are bound to the render token and instance that created them.

This supports an internal Partial until a browser smoke confirms the behavior in the rendered Obsidian panel.

## Risk Anchors

- `graphViewState` owns filter state, lens mode, filter panel state, outside-click handling, Cytoscape instance, and render token.
- `graphFilterScopeKey(...)` scopes filter storage by lens/focus context instead of using one global filter key.
- `createDefaultGraphFilterState()` returns fresh nested objects so reset/mutation does not leak across states.
- `renderCytoscapeGraph(...)` receives a render token and delayed animation only runs when the token and instance still match the active graph view.
- `tests/test_obsidian_graph_filter_state_isolation_js.py` exercises the pure helper behavior through Node.
- `tests/test_obsidian_sidebar_static.py` pins the absence of the old module-level graph/filter singletons.

## Required Slice

The smallest safe implementation slice is:

- isolate only Graph View state first, not the entire Obsidian frontend
- move graph filter state, lens mode, panel state, outside-click handler, and Cytoscape instance behind a small graph-view state object
- scope persistence keys by graph context instead of using one global filter key
- bind delayed Cytoscape callbacks to the instance or render id that created them

## Required Tests

Implemented:

- static test that module-wide `let graphFilterState =` and `let graphLensMode =` are no longer the active source of truth
- static test that persistence goes through a scope-key helper
- Node-driven behavior test for distinct graph-scope keys
- Node-driven behavior test that reset returns fresh nested state objects
- static render-token guard for delayed Cytoscape callbacks

Still pending:

- browser smoke for actual panel behavior
- full UI behavior test for outside-click lifecycle

## Go / Partial / No-Go

Go:
- graph/filter state is scoped and behavior-tested
- active filters are visible or clearable per graph context
- delayed renderer callbacks are bound to the intended instance

Partial:
- current state. Focused implementation and Node/static tests are green, but browser smoke is still pending.

No-Go:
- module-level singletons continue to drive cross-context graph/filter behavior
- one global localStorage key controls unrelated graph contexts
- old render callbacks can affect newer graph instances

## Next Action

Run a browser smoke against two graph contexts and verify filter persistence, reset, panel close behavior, and Cytoscape render refresh. Do not broaden this into a general Obsidian UI refactor.
