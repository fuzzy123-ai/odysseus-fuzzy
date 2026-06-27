# Odysseus Server Project Runner Roadmap

Stand: 2026-06-27

Status: **Phase 1 repo-only contract implemented; live execution remains operator-gated**

## Goal

Odysseus soll direkt auf dem Debian-Server Projekte bearbeiten, pruefen und
bereitstellen koennen, ohne einen zweiten unkontrollierten Deploy-Pfad neben
dem bestehenden Safe Updater zu bauen.

Der Runner ist deshalb kein blindes Shell-Terminal. Er ist eine kontrollierte
Projekt-Ausfuehrungsschicht mit Workspace-Grenzen, Git-Remote-Regeln,
Quality-Gates, Backup-Gate, Deploy-Gate, Smoke-Gate, Rollback/Hold und
audit-sicherem Report.

## ABC Ownership

- Alice: Produkt- und Operator-Sprache, Go/Hold/No-Go-Grenzen, UX-unabhaengige
  Anforderungen.
- Bob: Backend-Modelle, Runner-Vertrag, Tests, spaeter aktive Executor-Schicht.
- Charlie: Scope-Kontrolle, Server-Gates, Commit/Push, Live-Handoff.

## Phase Plan

### P1 Contract And Plan Model

Status: **done**

Allowed files:

- `docs/plans/server-project-runner-roadmap.md`
- `src/server_project_runner.py`
- `tests/test_server_project_runner.py`

Result:

- Projekt-Deployments werden als strukturierte Plaene beschrieben.
- Push-Remote ist standardmaessig `fuzzy`.
- `origin` ist fuer Push/Deploy-Planung blockiert.
- UI-Arbeit bleibt ausserhalb dieses Backend-Runners.
- Tests, Backup-Evidence, Smoke-Ziel, Rollback-Plan und Operator-Go sind
  explizite Gates.
- Keine Live-Kommandos, kein SSH, kein Podman, keine Provider- oder
  Netzwerkaktion in dieser Phase.

### P2 Active Workspace Preparation

Goal:

- Server-seitige Projekt-Workspaces definieren: repo-relative Root, erlaubte
  Pfade, blockierte Pfade, Branch-Namen, Arbeitsmodus.
- Worktree/Branch-Isolation mit der vorhandenen `workspace_policy` koppeln.

Gate:

- Keine Projektarbeit ausserhalb des erlaubten Workspace-Roots.
- Keine Secrets, privaten Rohdaten, Chat-IDs oder Host-Pfade im Report.

### P3 Quality Gate Integration

Goal:

- Projekt-spezifische Test- und Build-Gates an den vorhandenen
  `live_quality_gate_command_runner` anbinden.
- Nur fokussierte, begrenzte, redigierte Gates erlauben.

Gate:

- Kein Deploy ohne gruenes Test-/Build-Gate.
- Keine unbounded Test-Suites als Default.

### P4 Git And Review Flow

Goal:

- Branch anlegen, Aenderungen isolieren, Diff zusammenfassen, Commit-Plan
  erstellen und Push auf `fuzzy/dev` oder projektbezogene `fuzzy/*` Branches
  vorbereiten.

Gate:

- Nie auf `origin` pushen.
- Kein Force-Push, Reset, Checkout-Rewrite oder destruktive Cleanup-Aktion.

### P5 Deploy Handoff

Goal:

- Projekt-Deployments an den Safe-Updater-Flow koppeln:
  pre-update snapshot, fast-forward/metadata, Podman compose, Healthcheck,
  Smoke, Rollback/Hold.

Gate:

- Live-Ausfuehrung nur mit separatem Operator-Go.
- Backup- und Restore-Smoke-Evidence muessen gruen sein.

### P6 Active Executor

Goal:

- Einen kleinen aktiven Executor bauen, der nur whitelisted Schritte mit
  `shell=False`, Timeout, redigierten Reports und Stop-on-first-failure
  ausfuehrt.

Gate:

- Default bleibt dry-run.
- Live erfordert `live_enabled=True`, `operator_decision=go`, gruenes Bundle
  und konkrete bounded Inputs.

### P7 Server Install And Service Wiring

Goal:

- Auf dem Debian-Server systemd/Podman-Integration, Logs und Healthchecks
  operator-gegated einrichten.

Gate:

- Keine Secrets ins Repo.
- Kein Host-Pfad oder privater Output in Persistenzartefakten.

### P8 Telegram/AI Command Surface

Goal:

- Odysseus AI kann Projektaufgaben starten, Status melden, Gates erklaeren und
  Human Decisions anfordern.

Gate:

- Keine automatische Live-Aktion aus Chat-Nachrichten.
- Jede riskante Aktion braucht eine explizite Freigabe.

## Current Human Decision Needed

Als naechstes sollte entschieden werden, ob P2 zuerst nur fuer das Odysseus-Repo
selbst gilt oder ob direkt ein allgemeines `projects/` Workspace-Schema fuer
mehrere Projekte auf dem Server angelegt werden soll.
