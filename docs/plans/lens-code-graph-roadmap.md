# Lens Code Graph Roadmap

Stand: 2026-07-17

Status: `planned_after_code_graph_api / ui_default_off`

Master-Track: `0.31.x`, `OWM-18`, `L24`

## 1. Goal

Odysseus gains a first-class `Lens > Code` workspace that uses the existing Lens
shell and visualization primitives while remaining operationally useful on real
repositories and bounded on very large graphs. CBM supplies derived code facts;
it does not supply or own the product UI.

The experience is complete when a user can:

- open a project and immediately see its code topology;
- search a symbol or concept and focus the relevant subgraph;
- switch between first-class Symbol Graph, Import Graph and Call Graph modes as
  well as overview, impact, communities and timeline without losing selection;
- inspect every node/edge with exact source/version/locator/confidence evidence;
- jump to the exact source through existing readers/editors;
- see the graph path an agent used without confusing that runtime trace with
  the code knowledge graph;
- explore a visually striking full-project `Code Planet` mode using aggregates
  and level of detail rather than an unbounded payload;
- use the essential workflow by keyboard, pointer and reduced-motion mode;
- fall back to an accessible list/table when WebGL is unavailable.

## 2. Product And Design Placement

The screen lives inside the existing Knowledge workspace and Odysseus Lens
shell. It is not a new top-level application, a second navigation system or a
CBM-branded iframe exposed directly to the user.

Existing Lens visualization solves the shell, canvas, navigation, selection and
inspector problem, but it does not yet solve code topology. This roadmap must
still deliver the CBM adapter, semantic node/edge mapping, bounded APIs and the
three code-specific work modes:

- `Symbol Graph`: files/modules/symbols plus contains, defines and reference
  relations;
- `Import Graph`: module/package imports, re-exports and dependencies;
- `Call Graph`: caller/callee relations with direct, indirect, unresolved,
  method and confidence evidence.

Existing AI Lens `Trace` remains a runtime explanation and is not a substitute
for these source-derived graphs.

Existing product language remains canonical:

- main workspace: `Knowledge`;
- product surface: `Lens`;
- view: `Code`;
- expansive overview mode: `Code Planet`;
- runtime explanation: `Trace`;
- source-derived topology: `Code Graph`.

The design follows `PRODUCT.md` and `DESIGN.md`: quiet operator-grade density,
dark graphite, cyan foreground, sparse red/blue state accents, compact Inter
and Fira Code typography, restrained 3-10 px radii and existing theme tokens.
The graph may be visually dramatic because the data itself is dramatic. Panels
and controls remain calm, familiar and task-focused.

## 3. Truth Boundary

Two graphs share a shell but never a truth claim:

| Surface | Meaning | Source |
| --- | --- | --- |
| `Code Graph` | symbols, files, calls, imports, inheritance, routes, communities | CBM projection plus USI refs |
| `Timeline` | first-observed, rename/copy/change/removal evidence | Code Lineage provider |
| `Trace` | what retrieval/tools/model used for one answer | AI Lens runtime events |
| `Semantic` | embedding projection/proximity, explicitly labelled | USI semantic provider |
| `Visual Effect` | layout/glow/motion with no additional fact claim | renderer only |

Runtime trace edges are never written into the knowledge graph. Code topology
edges are never presented as model reasoning. Semantic proximity is never
labelled as a call or dependency.

## 4. Reuse Decision

The existing Odysseus Lens shell and renderer are canonical. CBM is integrated
as a bounded data/query provider, not as a UI, iframe, vendored application or
microfrontend dependency.

- reuse the existing Lens shell, graph canvas, navigation, selection, inspector,
  tokens, icons, accessibility and responsive patterns;
- add code-specific node/edge semantics, filtering and evidence interactions;
- consume the bounded CBM Graph Projection API, not the raw engine database;
- do not expose engine paths, ports, hooks, installer or direct MCP controls;
- retain a full-project visual mode, driven by communities/aggregates and
  progressive detail for large repositories;
- use the upstream CBM UI only as optional behavioral/reference material after
  license/security review, with no production runtime dependency.

## 5. Layout And Interaction Contract

Desktop structure:

```text
Knowledge workspace header
  Code | Memory | Sources | Trace

Code toolbar
  project selector | search | view segmented control | filters | time

left rail (collapsible)   full-bleed graph canvas   inspector (collapsible)
  node/edge filters         stable viewport           evidence + source jump
  repository tree           selection/path focus       confidence + timeline

bottom status strip
  generation | freshness | visible/total | clipped | layout | WebGL/fallback
```

Mobile/tablet structure:

- graph remains the primary unframed surface;
- toolbar wraps into two stable rows or a compact command strip;
- filter and inspector use accessible sheets/popovers, never nested cards;
- selection opens a bottom inspector without covering the selected node;
- text labels truncate/wrap predictably and expose the full value on focus;
- touch targets are at least 44 px where pointer precision cannot be assumed.

Controls:

- segmented control for Overview/Impact/Dependencies/Timeline/Trace;
- familiar icons for zoom, fit, reset, pause layout and source jump;
- checkbox/toggle filters for node/edge families;
- sliders/steppers only for numeric depth, confidence and time window;
- search supports symbols, paths and semantic concepts;
- keyboard command palette actions mirror visible controls;
- all unfamiliar icon buttons have tooltips and accessible names.

## 6. Visual And Motion Rules

- Full-bleed graph canvas, not a graph inside a decorative card.
- Panels are functional rails/inspectors, not floating card stacks.
- Nodes use a stable semantic palette by type; state colors do not change with
  arbitrary layout runs.
- Edge emphasis is selection/state driven; the default graph remains legible
  rather than rendering every edge at full intensity.
- Community hulls/labels appear only above useful zoom thresholds.
- Search focus, query paths and impact radius can use purposeful glow.
- Motion communicates layout settling, temporal change, query traversal or
  selection. No ambient decorative orbit animation.
- Most control transitions stay within 150-250 ms.
- Reduced motion freezes force animation after deterministic placement and
  uses crossfades/instant state changes.
- No gradient text, glassmorphism, decorative bokeh/orbs, oversized headings or
  purple-blue monoculture.
- The graph must not become blank in screenshots/headless rendering; deterministic
  fixture mode is required.

## 7. Mode And Gate Policy

Roadmap planning and backend contracts use `Standard ABC`. Implementation is a
UI-owned track and does not run as unattended backend work unless a selected
slice is explicitly backend-only.

- Only `LCG-00` is claimable after explicit goal start.
- Backend API, fixture, accessibility contract and deterministic render harness
  require no user gate.
- The existing Frontpage v3/Knowledge workspace is an active UI hot area and
  permits one writer at a time.
- No productive engine process, real private project graph or new UI route is
  enabled before the final gate.
- Exactly one gate, `LCG-UI-LIVE`, controls placement/cutover with a real
  project. It does not activate CBM or USI by itself.

## 8. Slice Queue

### LCG-00 - UX, Truth And Navigation Contract

- Class: `safe_offline`
- Owner: Alice
- Status: `ready_after_goal_start`
- Dependencies: explicit goal; Frontpage/Lens owner identified
- Allowed paths:
  - `docs/plans/lens-code-graph-roadmap.md`
  - `docs/plans/lens-code-graph-ux-contract.md`
  - `tests/test_lens_code_graph_contract.py`
- Work:
  - freeze placement, view names, truth labels and primary workflows;
  - map existing Knowledge, AI Lens and memory graph surfaces;
  - define desktop/mobile/keyboard/loading/empty/error/stale/clipped states;
  - prohibit duplicate shell, runtime graph confusion and raw engine UI.
- Tests: `python -m pytest -q tests/test_lens_code_graph_contract.py`
- Done when: every visible mode names its data source/truth level and no
  existing Lens responsibility is duplicated.

### LCG-01 - Lens Renderer Reuse And Code-Graph Gap Audit

- Class: `safe_offline`
- Owner: Charlie
- Dependencies: `LCG-00`; existing Lens surface available for inspection
- Allowed paths:
  - `docs/plans/lens-code-graph-renderer-gap-audit.md`
  - `tests/test_lens_code_graph_contract.py`
- Work:
  - inventory reusable AI Lens/Memory graph shell, canvas, layout, selection,
    inspector, accessibility and responsive primitives;
  - identify missing code-specific behavior for symbol, import and call graphs;
  - define the Odysseus-owned adapter from bounded CBM projections to Lens view
    contracts;
  - compare upstream CBM behavior only where useful, without vendoring it or
    introducing a runtime dependency;
  - freeze the rule that Lens cannot navigate or control the raw engine.
- Tests: `python -m pytest -q tests/test_lens_code_graph_contract.py`
- Done when: a concrete reuse/gap map shows how the three code graphs fit the
  existing Lens renderer with no duplicate shell or CBM UI dependency.

### LCG-02 - Code Graph View Contract And Fixture Service

- Class: `repo_only`
- Owner: Bob
- Dependencies: `LCG-00`, CBM-08 graph projection API
- Allowed paths:
  - `src/lens_code_graph_contract.py`
  - `src/lens_code_graph_service.py`
  - `tests/test_lens_code_graph_contract.py`
  - `tests/test_lens_code_graph_service.py`
- Work:
  - bounded overview/community/neighborhood/path/query/timeline payloads;
  - explicit `symbol`, `import` and `call` graph modes in the view contract;
  - stable file/module/symbol node and contains/defines/reference/import/call
    edge semantics with filters, method and confidence;
  - separate deterministic fixture families for Symbol Graph, Import Graph and
    Call Graph, including unresolved and low-confidence edges;
  - fixture mode with deterministic layout seeds;
  - source/version/locator/confidence and clipping metadata;
  - policy-filter before payload construction.
- Tests:
  - `python -m pytest -q tests/test_lens_code_graph_contract.py tests/test_lens_code_graph_service.py`
- Done when: production UI can be built entirely against safe deterministic
  fixtures and cannot request unbounded graph data.

### LCG-03 - Read-Only Backend Routes And Progressive Graph API

- Class: `repo_only`
- Owner: Bob
- Dependencies: `LCG-02`; UIR read-only route/service composition contract;
  route owner handoff
- Allowed paths:
  - `routes/lens_code_graph_routes.py`
  - `tests/test_lens_code_graph_routes.py`
  - `src/app_initializer.py` only in a serialized integration sub-slice
- Work:
  - project list/status, overview, neighborhood, path, query and timeline reads;
  - owner/admin/project access checks;
  - ETag/generation/cursor, max budgets and cancellation;
  - fixture route disabled in production by default;
  - consume the injected USI/CBM query service, never a raw engine singleton;
  - no write, reindex, project registration or engine-control endpoint.
- Tests: `python -m pytest -q tests/test_lens_code_graph_routes.py`
- Done when: route tests prove bounded/policy-safe payloads and engine absence
  returns an actionable degraded state.

### LCG-04 - Native Knowledge Workspace Shell

- Class: `repo_only`
- Owner: Alice plus one UI worker after an explicit UI hotfile claim
- Dependencies: `LCG-00`, `LCG-01`, `LCG-02`
- Allowed paths:
  - `static/frontpage-v3/index.html`
  - `static/frontpage-v3/app.js`
  - `static/frontpage-v3/v3-fixed.css`
  - `tests/test_frontpage_v3_lens_code_graph.py`
- Work:
  - Code/Memory/Sources/Trace navigation using existing component vocabulary;
  - full-bleed graph canvas with stable toolbar/rails/status strip;
  - responsive structural breakpoints, no fluid font sizing;
  - all control states and keyboard focus;
  - skeleton, empty, stale, clipped, no-WebGL and permission states.
- Tests: static DOM/route/component tests named in the implementation claim
- Done when: the screen feels native to Odysseus and loads directly into a
  usable graph task rather than a marketing/empty landing page.

### LCG-05 - Code Planet Overview And Level Of Detail

- Class: `repo_only` for component code after `LCG-04` UI ownership
- Owner: UI worker
- Dependencies: `LCG-03`, `LCG-04`
- Allowed paths:
  - `static/frontpage-v3/code-graph/`
  - `tests/ui/lens_code_graph_overview.spec.js`
- Work:
  - community aggregates at far zoom, file/module nodes at medium zoom and
    symbols at near zoom;
  - deterministic seeded layout, stable viewport and saved camera state;
  - visible/total counts and clipped indicator;
  - progressive fetch around viewport/selection;
  - pause/resume/reset/fit controls without layout shift.
- Tests: component tests plus canvas nonblank fixture check
- Done when: small graphs can look complete and large graphs look impressive
  without loading or drawing every edge at once.

### LCG-06 - Search, Filters And Evidence Inspector

- Class: `repo_only`
- Owner: UI worker
- Dependencies: `LCG-04`, `LCG-05`
- Allowed paths:
  - `static/frontpage-v3/code-graph/`
  - `tests/ui/lens_code_graph_inspector.spec.js`
- Work:
  - symbol/path/concept search with keyboard result navigation;
  - type/edge/confidence/repository/community filters;
  - exact evidence inspector with source/version/locator/method/confidence;
  - source jump uses existing reader/editor action;
  - selection persists across compatible view changes.
- Tests: component interaction and accessibility tests
- Done when: a user can locate and verify a code item without interpreting the
  entire visual graph.

### LCG-07 - Symbol, Import And Call Work Modes

- Class: `repo_only`
- Owner: UI worker
- Dependencies: `LCG-06`, CBM-05 query provider
- Allowed paths:
  - `static/frontpage-v3/code-graph/`
  - `tests/ui/lens_code_graph_modes.spec.js`
- Work:
  - first-class Symbol Graph for file/module/symbol containment, definitions and
    references;
  - first-class Import Graph for module/package imports, re-exports and
    dependencies;
  - first-class Call Graph for caller/callee exploration, including direct,
    indirect, unresolved and low-confidence relations;
  - inheritance, routes/dataflow, community and impact radius modes;
  - mode-specific legend and edge emphasis;
  - breadcrumb/back/forward for graph exploration;
  - unresolved/low-confidence edges visibly distinct;
  - no hidden arbitrary Cypher requirement for normal users.
- Tests: deterministic fixture mode tests for every mode
- Done when: Symbol Graph, Import Graph and Call Graph each work from deterministic
  CBM fixtures, answer one concrete engineering question and provide bounded
  controls, exact source jump and edge evidence.

### LCG-08 - Timeline And Project Growth Playback

- Class: `repo_only`
- Owner: UI worker
- Dependencies: CLT-10 graph/timeline bridge, `LCG-06`
- Allowed paths:
  - `static/frontpage-v3/code-graph/`
  - `tests/ui/lens_code_graph_timeline.spec.js`
- Work:
  - time range, current snapshot and first-observed sort controls;
  - play/pause/step with visible revision/time/evidence status;
  - add/move/change/remove state without decorative ambiguity;
  - shallow/import/generated warnings;
  - reduced-motion mode steps instantly between snapshots.
- Tests: timeline state, long-label and reduced-motion tests
- Done when: project growth is visually compelling and every transition remains
  grounded in available Git/USI evidence.

### LCG-09 - Agent Query Focus And AI Lens Trace Bridge

- Class: `repo_only`
- Owner: Bob for bridge, UI worker for projection; serialized integration
- Dependencies: USI-08 context bridge, `LCG-06`, AI Lens contract
- Allowed paths:
  - `src/lens_code_graph_trace_bridge.py`
  - `tests/test_lens_code_graph_trace_bridge.py`
  - `static/frontpage-v3/code-graph/`
  - `tests/ui/lens_code_graph_trace.spec.js`
- Work:
  - open the exact code subgraph returned by `query_knowledge`;
  - overlay selected/excluded/clipped evidence from one answer trace;
  - truth labels remain `Code Graph` versus `Trace`;
  - no hidden reasoning, prompt body or raw private content;
  - trace capture failure cannot affect code query or answer.
- Tests: `python -m pytest -q tests/test_lens_code_graph_trace_bridge.py`
- Done when: users can see what code evidence the agent used without claiming
  to display model neurons or private chain-of-thought.

### LCG-10 - Rendering Performance And Failure Degradation

- Class: `repo_only`
- Owner: UI worker plus Charlie verification
- Dependencies: `LCG-05` through `LCG-09`
- Allowed paths:
  - `static/frontpage-v3/code-graph/`
  - `tests/ui/lens_code_graph_performance.spec.js`
- Work:
  - off-main-thread layout where beneficial, frame and memory budgets;
  - cap pixel ratio and postprocessing based on capability;
  - suspend rendering in hidden tabs and cancel stale fetches;
  - WebGL context-loss recovery and accessible list/table fallback;
  - no ambient animation when idle/reduced-motion.
- Tests: synthetic 1k/10k/100k visible/aggregate fixture profiles
- Done when: interaction remains responsive and failure never leaves a blank
  unexplained canvas.

### LCG-11 - Accessibility, Responsive And Internationalization Hardening

- Class: `repo_only`
- Owner: Alice plus UI worker
- Dependencies: `LCG-04` through `LCG-10`
- Allowed paths:
  - `static/frontpage-v3/code-graph/`
  - `tests/ui/lens_code_graph_accessibility.spec.js`
- Work:
  - keyboard graph navigation and list alternative;
  - focus visibility, semantic controls, tooltips and announcements;
  - WCAG AA text/control contrast;
  - mobile/tablet inspector/filter behavior;
  - long German/English labels and 200% zoom without overlap;
  - stable control dimensions and no text overflow.
- Tests: automated accessibility, keyboard and responsive component tests
- Done when: essential search/inspect/source-jump works without pointer or
  animated canvas.

### LCG-12 - Playwright And Canvas Visual QA

- Class: `repo_only`
- Owner: Charlie
- Dependencies: `LCG-04` through `LCG-11`
- Allowed paths:
  - `tests/ui/lens_code_graph.spec.*`
  - screenshot baselines/artifacts in the repository-approved test location
  - no unrelated production UI edits
- Work:
  - desktop, wide desktop, tablet and mobile screenshots;
  - canvas pixel nonblank/distribution checks;
  - selection, filter, search, impact, timeline, resize and context-loss flows;
  - no overlap, clipped controls, blank first render or layout shift;
  - reduced motion and list fallback screenshots.
- Tests: exact project Playwright command recorded in the implementation slice
- Done when: deterministic fixtures pass visual, interaction and canvas checks
  across required viewports.

### LCG-13 - Synthetic Staging And UI Activation Packet

- Class: `repo_only`
- Owner: Charlie
- Dependencies: `LCG-00` through `LCG-12`; USI/CBM/CLT readiness and UIR
  runtime route binding declared
- Allowed paths:
  - `docs/plans/lens-code-graph-activation-packet.md`
  - `docs/plans/open-work-completion-master-roadmap.json`
- Work:
  - selected UI shell/route, feature flags, asset/version pin and CSP;
  - fixture and real-project smoke sequence;
  - performance/accessibility/browser support and known limits;
  - rollback to prior Knowledge workspace;
  - materialize one UI-live decision only after green packet.
- Tests: focused backend/UI/Playwright suite plus master JSON validation
- Done when: activation changes no engine/source/tool truth and can revert the
  visual surface independently.

### LCG-UI-LIVE - Single User Gate

- Class: `needs_design`
- Status: `dormant`
- Blocks: productive `Lens > Code` placement and real project graph display
- Decision needed: selected shell/route, default view, project, enabled modes,
  performance profile, observation window and visual rollback
- Go phrase:
  `GO LCG-UI-LIVE: enable Lens > Code <version> in <shell/route> for <project scope>, default to <view>, observe <window>, and roll back to <prior Knowledge surface> on No-Go.`

## 9. Parallelism And Hotfiles

- `LCG-00` and `LCG-01` are disjoint contract/audit work after ownership check.
- `LCG-02`/`LCG-03` backend may run before production UI.
- One UI writer owns the selected Frontpage/Lens surface from `LCG-04` through
  integration. No simultaneous legacy/v2/v3 broad rewrite.
- CBM owns engine process/query/projection, not production UI files.
- AI Lens owns runtime event schemas; this roadmap adds only a bridge.
- GRO owns metrics/exporters; renderer telemetry uses its bounded registry only
  after handoff.
- No slice may edit both the renderer and master/queue JSON without Charlie
  integration.

## 10. Acceptance Metrics

Product gates:

- symbol/path search to selected node in at most two interactions after query;
- source jump preserves exact project/path/revision/line evidence;
- every structural edge exposes type, method and confidence;
- no truth-label confusion in usability review fixtures;
- clipped/partial/stale state always visible;
- keyboard-only search, inspect and source jump pass;
- no text/control overlap at 360x800, 768x1024, 1440x900 and 1920x1080;
- nonblank first graph frame and stable viewport after data load;
- interaction target of 60 fps on normal views and no long task above 100 ms
  from layout/render work on the accepted reference machine;
- graph payload and visible element counts remain within backend/UI budgets;
- context loss/no-WebGL returns an explained accessible fallback.

## 11. Go Language

- `Go`: native Lens placement, bounded Symbol/Import/Call graphs, evidence
  inspector, accessibility, performance and visual QA are green.
- `Partial`: core search/inspect/impact works, while timeline, semantic or
  extreme-scale modes remain explicitly disabled.
- `No-Go`: raw engine UI leaks through, truth levels blur, graph is unbounded,
  first render is blank, controls overlap or exact evidence cannot be opened.
- `Deferred`: optional postprocessing, multi-repo overlay, mobile force editing
  or advanced timeline animation is not needed for initial value.
- `Blocked`: stable CBM/USI graph API, chosen UI shell or required design
  ownership is unavailable.

## 12. Definition Of Done

- The spectacular full-project graph exists as a real Odysseus product mode.
- The default workflows remain efficient for repeated engineering work.
- Existing Lens visual technology renders bounded CBM-derived data without a
  duplicate app shell, CBM UI dependency, project registry, tool family or
  unsafe engine control.
- Symbol Graph, Import Graph and Call Graph are independently usable, filtered
  and source-linked; AI Lens Trace is not presented as code topology.
- Code Graph, Timeline, Semantic and Trace truth levels remain distinct.
- Every visible fact is source-linked, bounded and policy-safe.
- Large graphs use progressive aggregates/LOD instead of unbounded payloads.
- Desktop/mobile, keyboard, reduced motion, WebGL failure and long text are
  verified.
- UI activation is independent from USI/CBM/CLT activation and has one explicit
  gate plus rollback.
