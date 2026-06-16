# Automated Agent Handoff E2E Smoke Runbook

Stand: 2026-06-16

Status: **AUTO7 deterministic smoke prepared**

## Ziel

Dieses Runbook beschreibt den ersten sicheren End-to-End-Smoke fuer die native Agent-Orchestration. Er beweist den Produktpfad:

```text
Approved Plan -> Agent Runs -> ThreadRefs -> Handoff -> Mailbox Dispatch -> Quality Gates -> Dashboard Snapshot
```

Der Smoke ist absichtlich deterministisch und offline. Er nutzt Fake-Thread-Refs und injizierte Evidence-Snapshots, damit keine echten Agenten geweckt, keine Git-Kommandos ausgefuehrt und keine Tests aus dem Runtime-Code heraus gestartet werden.

## Scope

Enthalten:

- Zwei Agenten: Alice als Runbook-/Contract-Agent, Bob als Backend-Smoke-Agent.
- PlanGraph mit sequenziellem `handoff_to` von Alice zu Bob.
- ThreadRegistry mit eindeutigen ThreadRefs.
- HandoffParser fuer Alice-Antwort.
- OrchestrationRuntimeLoop fuer einen Mailbox-Dispatch an Bob.
- RuntimeQualityGates fuer Bobs verified-done Nachweis.
- Dashboard-v2 Snapshot als sichtbarer Abschlusszustand.

Nicht enthalten:

- Echte Thread-Reads oder Thread-Sends.
- Echter Scheduler.
- Ausfuehrung von `git status`.
- Ausfuehrung von `pytest` aus der Runtime.
- Frontend-/API-Hotfiles.
- Autonomes Starten weiterer Agents.

## Akzeptanzkriterien

- `run_two_agent_smoke()` erzeugt genau einen Plan, zwei Runs, zwei ThreadRefs und einen queued Mailbox-Dispatch.
- Quality Gates sind `verified_done=True`.
- Dashboard-Status ist `completed`.
- Dashboard-Progress ist `100`.
- Mailbox bleibt sichtbar, damit ein echter Runtime-Hook spaeter nicht heimlich sendet.
- Der Test `tests/test_orchestration_e2e_smoke.py` ist gruen.

## Bekannte Grenzen

- Der Smoke beweist Modell-Integration, nicht echte Codex-Thread-I/O.
- Die Mailbox enthaelt eine queued Message; das echte Senden braucht weiterhin eine freigegebene Thread-Bridge.
- Git/Test-Evidence ist injiziert; echte Runner gehoeren in `AUTO5` Follow-up oder `AUTO7` real-mode Gate.
- Dashboard-v2 ist ein Backend-Snapshot; UI/API-Anbindung bleibt offen.

## Go/No-Go

Go fuer naechsten Slice, wenn:

- `tests/test_orchestration_e2e_smoke.py` gruen ist.
- Der Orchestration-Verbund gruen bleibt.
- Worktree nach Commit sauber ist.

No-Go, wenn:

- Der Smoke echte Threads anschreiben muesste.
- Git/Test-Ausfuehrung aus der Runtime heraus noetig waere.
- Dashboard/UI-Hotfiles ohne separaten Contract beruehrt werden muessten.
