# Subagent Runtime v1 Roadmap

Stand: 2026-06-20

Status: **integrierter Runtime-Follow-up fuer `0.18.x` Orchestration**

## Goal

Odysseus bekommt eine echte langlebige Subagent-Runtime, die bestehende
Orchestration-Bausteine verbindet: Plan/Capsule -> SubagentRun ->
Thread/JobRef -> scoped execution -> Handoff -> Gates -> Status/UI.

## Current Evidence

- `src/delegate_tool.py` ist nur ein fokussierter LLM-Call. Der System-Prompt
  verbietet bewusst, geaenderte Dateien oder externen Zustand zu behaupten.
- `src/context_capsule.py` modelliert sichere, kleine Arbeitskapseln mit
  erlaubten Dateien, erwarteten Outputs, Tests, Handoff-Format und Stopps.
- `src/agent_run_store.py` modelliert Agent Runs mit Status, Evidence, Tests,
  Commits und Changed Files, aber noch ohne echten Worker-Spawn.
- `src/thread_registry.py` speichert eindeutige ThreadRefs, liest oder schreibt
  aber keine echten Threads.
- `src/handoff_mailbox.py` und `src/orchestration_runtime_loop.py` parsen und
  queue'n Handoffs/Dispatches trocken; sie senden nicht in echte Threads.
- `src/runtime_quality_gates.py` wertet injizierte Git-/Test-/Scope-Snapshots
  aus; es fuehrt keine Kommandos aus und bereinigt keinen Worktree.
- `docs/plans/automated-agent-handoff-orchestration-mvp.md` beschreibt die
  bestehende Orchestration-Foundation, aber markiert echte Thread-/Git-/Test-
  Hooks weiterhin als offen.

## Non-Goals

- Keine Live-Thread-Ausfuehrung ohne separate explizite Operator-Freigabe.
- `delegate` wird nicht als schreibender oder langlebiger Worker behandelt.
- Keine Secrets, Token, Thread-IDs, Chat-IDs, private Source-Inhalte oder rohe
  Provider-/Tool-Ausgaben in Docs, Tests, Logs, Evidence oder Handoffs.
- Keine destruktiven Git-Aktionen, kein Force-Push, kein Reset/Checkout-Rewrite.
- Keine beliebige Shell-/Host-Ausfuehrung und kein freier Command-Runner.
- Kein Restore, Delete, Prune, Rollback, Live-Nextcloud-Write, Telegram-Send,
  Provider-Call oder Production-Deploy als impliziter Teil dieses Tracks.
- Kein `verified done` ohne maschinenlesbare Evidence, Tests/Gate-Snapshots und
  Scope-Pruefung.

## Stop Rules

- Stop bei fremden staged files oder unklarem Worktree-Besitz.
- Stop bei ambiguous thread, fehlender ThreadRef oder mehrdeutiger JobRef.
- Stop, wenn ein Slice `delegate` als Schreib-Agent oder Durable Worker nutzt.
- Stop bei Scope-Verletzung, Hotfile-Overlap oder fehlendem Handoff-Pflichtfeld.
- Stop bei roten fokussierten Tests ohne engen Fix.
- Stop, wenn Live-Netzwerk, Provider, Telegram, Nextcloud, Host, Export/Import,
  Rebuild, Backup, Restore oder Deploy ohne separate Live-Freigabe noetig waere.

## Architecture Decision

`delegate` bleibt ein leichtes Analystenwerkzeug:

```text
prompt + provider context -> compact JSON summary
```

`subagent_runtime` wird die langlebige Worker-Schicht:

```text
SubagentRunSpec
  -> ContextCapsule
  -> AgentRun
  -> ThreadRef or JobRef
  -> ExecutionBackend
  -> HandoffMailbox
  -> RuntimeQualityGates
  -> Status snapshot / UI
```

Die erste Implementierung nutzt ein Fake-Backend. Ein echtes
Odysseus/Codex-Thread-Backend bleibt ein spaeteres, explizit freizugebendes
Runtime-Gate.

## ABC Slices

### ABC0 Reconciliation

Owner: Alice/Charlie.

Goal:
- Dokumentieren, warum `delegate` kein langlebiger Subagent ist und wie der
  neue Runtime-Layer in die bestehende Orchestration passt.

Files:
- `docs/plans/subagent-runtime-v1-roadmap.md`
- `docs/agents/master-implementation-agent.md`
- `docs/plans/unified-odysseus-roadmap.md`

Done when:
- Master-Roadmap verlinkt diesen Track.
- Master-Agent-Doku unterscheidet `delegate` und durable Subagents.
- No-goals und Stop-Regeln sind kanonisch dokumentiert.

### ABC1 Runtime Contract

Owner: Bob.

Goal:
- `SubagentRunSpec` und `SubagentRunState` als schmale Runtime-Vertraege
  einfuehren und an `ContextCapsule`/`AgentRun` anbinden.

Expected files:
- `src/subagent_runtime.py`
- `tests/test_subagent_runtime_contract.py`

Done when:
- Spec validiert agent/slice/objective/allowed paths/tests/handoff/evidence.
- State trennt `planned`, `spawned`, `running`, `handoff`, `blocked`, `done`,
  `failed`, `cancelled`.
- Done ohne Evidence oder Gate-Signal wird blockiert.

### ABC2 Spawn API

Owner: Bob.

Goal:
- `create_subagent_run(spec)` persistiert Run, Capsule und ThreadRef/JobRef
  ueber injizierbare Stores, ohne echte Threads zu erzeugen.

Expected files:
- `src/subagent_runtime.py`
- `tests/test_subagent_runtime.py`

Done when:
- Run-IDs sind deterministisch/auditierbar genug fuer Tests.
- Capsule- und AgentRun-Summary enthalten keine Secrets oder absolute Hostpfade.
- Ambiguous thread/job assignment blockiert.

### ABC3 Execution Bridge

Owner: Bob/Charlie.

Goal:
- `SubagentExecutionBackend` einfuehren, zuerst als Fake backend.

Done when:
- Fake backend kann Runs starten, Handoff simulieren und Fehler/Blocker liefern.
- Backend-Interface ist schmal: spawn/read/cancel/retry/status.
- Echtes Thread-Backend ist nur als Interface/No-Go dokumentiert.

### ABC4 Handoff + Gates

Owner: Bob/Charlie.

Goal:
- HandoffMailbox, RuntimeQualityGates und Orchestration Registry in den
  SubagentRun-Lifecycle integrieren.

Done when:
- `done` ohne Evidence blockiert.
- Scope-Verletzung blockiert.
- Ambiguous thread/job blockiert.
- Test-/Git-Snapshots bleiben injiziert und offline fakebar.

### ABC5 Tool Discovery

Owner: Bob/Charlie.

Goal:
- Neues Tool-Surface `spawn_subagent` oder `manage_subagents` fuer die
  Runtime planen/verdrahten.

Keywords:
- subagent, unteragent, alice, bob, charlie, delegate, worker, parallel

Expected tests:
- `tests/test_subagent_tool_selection.py`

Done when:
- Tool Discovery schickt langlebige Worker-Anfragen nicht mehr zu `delegate`.
- `delegate` bleibt fuer lightweight Analyse erreichbar.
- Orchestrator-Allowlist bleibt eng und enthaelt keine freie Shell.

### ABC6 UI / Status

Owner: Alice/Bob.

Goal:
- Eine Statussicht fuer laufende Subagents bereitstellen.

Expected UI data:
- Agent, slice, state, backend, handoff status, tests, blockers, next action,
  updated timestamp.

Allowed actions:
- pause, resume, cancel, retry.

Forbidden actions:
- restore, delete, prune, rollback, arbitrary shell, production deploy.

Done when:
- UI/API zeigt Fake-Backend-Runs ohne Live-Thread-Aktionen.
- Status unterscheidet `claimed done`, `gate blocked` und `verified done`.

### ABC7 E2E Smoke

Owner: Charlie.

Goal:
- Fake-End-to-End-Pfad belegen:

```text
Plan -> Spawn Alice/Bob -> Fake execution -> Handoff parsed -> Gate evaluated -> done/blocked
```

Focused tests:

```text
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_subagent_runtime_contract.py tests\test_subagent_runtime.py tests\test_subagent_tool_selection.py tests\test_orchestration_runtime_loop.py tests\test_handoff_mailbox.py
```

Done when:
- Der Fake-Pfad ist gruen.
- Keine Live-Thread-Ausfuehrung, kein Netzwerk, keine Host-Mutation.
- No-goals sind in Tests/Docs sichtbar.

## Execution Order

1. `ABC0-reconciliation`
2. `ABC1-runtime-contract`
3. `ABC2-spawn-api`
4. `ABC3-execution-bridge-fake`
5. `ABC4-handoff-gates`
6. `ABC5-tool-discovery`
7. `ABC6-ui-status`
8. `ABC7-e2e-fake-smoke`

## Go / Partial / No-Go

- **Go**: Fake backend, contracts, tool discovery, UI/status and focused tests
  pass without live actions.
- **Partial**: Runtime model exists but either UI/status, tool discovery or E2E
  fake smoke is missing.
- **No-Go**: `delegate` is used as durable worker, live thread execution is
  started without explicit Go, secrets leak into evidence, or `done` bypasses
  gates.
- **Deferred**: real Codex/Odysseus thread backend, real test-command runner,
  real git gates, production auto-dispatch, and arbitrary N-agent scaling.
