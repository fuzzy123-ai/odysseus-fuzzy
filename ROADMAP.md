# Roadmap / Help Wanted

Current Harbour One planning: [Product roadmap](docs/plans/harbour-one-product-roadmap.json)
and [Open Work](docs/plans/open-work-completion-master-roadmap.json). The
2026-09-05 product reconciliation distinguishes accepted foundations from
open integration. The upstream/legacy outline below is contextual; it does
not override those authorities or grant a new execution/release action.

Odysseus is on a voyage, but not home yet. It works great for me (lol), but this ship is moving fast and feedback/help would be appreciated! (I don't know what I'm doing, help).

If you see weird CSS, strange layout behavior, or a suspiciously murky corner of
the codebase, you are probably right to stay away.

## Post-1.0 — High Priority: Durable Agent Operating System

**Trigger:** begin only after the 1.0 UI and backend scope is complete and
accepted. **Outcome:** Odysseus becomes a durable, self-describing operating
environment for long-running multi-agent work with Codex and Goose—not a system
whose essential knowledge is trapped in chat history or transient memory.

- Establish a versioned **system atlas**: architecture, component ownership,
  data/control flows, integration boundaries, invariants, operational risks, and
  decision rationale. It must identify authoritative sources and distinguish
  current facts from historical context.
- Establish a tool-neutral **agent constitution**, with Codex- and
  Odysseus/Goose-specific adapters. Define non-negotiable rules for scope,
  authority, external effects, untrusted inputs, evidence, escalation, and
  conflict handling.
- Productize durable multi-agent execution: structured goals, dependency-ready
  claims, path/resource ownership, explicit handoffs, bounded work packets,
  integration gates, and reliable release/recovery of abandoned work.
- Build the **Odysseus-to-Goose execution boundary**: Odysseus owns the durable
  control plane and starts selected, short-lived Goose CLI workers only for
  dependency-ready claims. Each worker receives an isolated worktree, a pinned
  agent-pack/profile revision, scoped tools and paths, acceptance evidence, and
  no implied authority over central planning or external effects.
- Make model routing an Odysseus policy, never an ad-hoc worker choice. Select
  and record the provider, model, context/token/cost limits, timeout and
  permitted fallback from task class, risk, budget, context demand and current
  model health. Treat hidden auxiliary model calls as part of the same budget.
- Establish a redacted **agent-run feedback loop** that joins token/cost use,
  latency, context pressure, tool failures, retries, tests, review verdicts,
  rework and final acceptance to the exact claim, model and profile revisions.
  Use it for live budget/stall warnings and evidence-backed routing/profile
  recommendations; never silently rewrite production policy from telemetry.
- Package Codex/Goose adapters, prompts, recipes, MCP extensions and deployment
  configuration as a versioned agent pack outside the upstream runtimes. Define
  compatibility tests, canary promotion and automatic rollback for the pinned
  `Goose version + agent-pack version + model-profile` combination on Debian.
- Make recovery first-class. A fresh agent must be able to reconstruct the
  active goal, current state, decisions, risks, ownership, evidence, and next
  gate from versioned project state without relying on prior chat context.
- Turn repeatable workflows into maintained runbooks for feature work, bugs,
  refactors, reviews, releases, incidents, and migrations. Keep detailed
  mechanics outside the strategic roadmap, with links from the relevant runtime
  surfaces.
- Convert essential rules into enforceable controls where feasible: schemas,
  contract tests, CI gates, policy checks, provenance/evidence binding, and
  state-transition validation. Documentation alone must not be the only guard.
- Add health and audit views for long runs: stalled or conflicting claims,
  missing evidence, unmet gates, stale system knowledge, unsafe ownership
  overlap, and recovery readiness.
- Validate the operating model through realistic interrupted, parallel,
  cross-tool, and context-loss scenarios before declaring it ready for ongoing
  autonomous work.

**Acceptance evidence:** a newly started Codex or Goose agent can safely pick
up a deliberately interrupted multi-agent initiative from repository-backed
state; the system prevents or surfaces ownership/gate conflicts; and the
system atlas, rules, workflows, routing policy and enforceable checks are
maintained as part of normal product change. A Goose worker run additionally
has attributable cost/quality telemetry and can be safely started, upgraded or
rolled back through the declared agent-pack compatibility path.

## High Priority

- SQUASH BUGS
- Fresh install smoke tests on Linux, macOS, and Windows. Docker, native Python,
  and WSL all need coverage.

- Integration audit: do integrations even work? Confirm what works, what needs setup docs, and what should be removed or hidden. 
- Self-host troubleshooting cookbook. Document the weird 30-second fixes that otherwise become 30-minute searches: Dovecot cleartext auth for local stacks, ntfy Android Instant Delivery for non-ntfy.sh servers, clipboard limits on plain-HTTP Tailscale URLs, Radicale collection URLs, and similar traps.
- Cookbook reliability on other computers. This is probably the area most likely to need work across different machines, GPUs, drivers, shells, and Python environments.
- Cookbook SGLang support across platforms. Make sure SGLang setup/serve works
  predictably on Linux, Windows/WSL, macOS where possible, Docker, and common
  NVIDIA/AMD hardware paths.
- Deep Research model presets by hardware. Recommend approved model/parameter
  profiles for small, medium, and large local setups so people with different
  hardware can use Deep Research without guessing. Surface this either in Deep
  Research settings or as a Cookbook scan/dropdown suggestion.
- Cookbook model scan/download ranking. Prioritize newer architectures and
  better hardware-fit models instead of scoring everything almost the same.
  Ranking should account for architecture age, quant format, VRAM/RAM fit,
  backend support, vision/mmproj requirements, and likely serve reliability.
- Cookbook error feedback and logging. Failed downloads, dependency installs,
  preflights, and serve jobs should show the actual command/output/error in the
  UI, with copyable logs and clear next steps instead of just "crashed".
- Agent prompt/context bloat. Agent mode is too heavy for smaller local models:
  tool schemas, skills, memory, documents, and instructions can eat the context
  before the user request really starts. We need slimmer prompts, better tool
  selection, smaller default tool sets, and clearer guidance for models with
  4k/8k/16k context windows.
- Skill/tool prompt-injection audit. User-editable skills, notes, documents,
  fetched pages, and memories should be treated as untrusted data. Keep testing
  whether models follow malicious instructions from those surfaces.
- Better degraded-state reporting for ChromaDB, SearXNG, email, ntfy, and provider probes.
- Email performance audit. Fetching, searching, opening, deleting, and sending
  email can feel slow, especially over IMAP/SMTP providers with high latency.
  Need someone who knows mail performance to profile the current flow, identify
  whether the bottleneck is IMAP folder select/fetch, cache invalidation,
  attachment/body loading, SMTP handshakes, or frontend refresh behavior, then
  propose safer caching/prefetch/batching without breaking multi-account state.
- Provider setup/probing audit for Anthropic, Gemini, Groq, xAI, OpenRouter, OpenAI, and DeepSeek.

## Refactor Targets
- CSS cleanup. `static/style.css` basically Calypso's island atm.
- Tour core helper. The onboarding tours have too much copy-pasted scaffolding; promote a shared `tour-core.js` helper before adding more tours.
- Modal/window positioning cleanup. Some window controls have improved, but the
  underlying popup/dropdown/fixed-position behavior is still too fragile.
- Mobile media override discoverability. A lot of "CSS did not move" bugs are mobile `@media` overrides of the same selector; comments or linting around desktop/mobile paired rules would help.
- Dead code pass for old routes, stale feature flags, and unused UI states.

## Frontend

- Expand the Editor for quicker, more robust everyday use. Better file/document
  handling, smoother window behavior, clearer save/export flows, stronger image
  editing affordances, and fewer brittle edge cases.
- Better AI integration for Notes and Todos. Notes should be easier for the
  agent to read, update, summarize, and turn into actions. Todos should be
  assignable to an agent from the UI, possibly through a button, task action,
  or dedicated skill/tool flow.
- Mobile gallery/editor polish. Easier to launch/download inpaint model or any missing pieces.
- Accessibility pass: keyboard navigation, focus states, contrast, reduced motion.
- Improve empty states and error messages on fresh installs.
- Tighten first-run setup, hints, and tours so they do not repeat or fight each other.
- Vendor CDN assets eventually for a more fully self-hosted/offline mode.

## Backend

- More tests around endpoint probing and provider setup.
- Better task scheduler defaults and visibility.
- Backup/restore guide and helper flow for `data/`.
- Security hardening around admin-only tools and clear docs for their risk.

## Not The Focus Right Now

I prob shouldnt add more themes.
