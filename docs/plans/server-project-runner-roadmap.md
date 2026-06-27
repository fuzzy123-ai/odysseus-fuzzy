# Odysseus Server Project Runner Roadmap

Stand: 2026-06-27

Status: **Phase 1 repo-only universal project contract implemented; live execution remains operator-gated**

## Goal

Odysseus soll direkt auf dem Debian-Server beliebige Projekte planen,
bearbeiten, pruefen und bereitstellen koennen. Ein Projekt ist dabei nicht das
Odysseus-Repo selbst, sondern entsteht aus einem Projekttitel als eigener
Project Scope mit eigenem Repository, Workspace, Chat-Kontext und spaeterem
Deployment-/Tunnel-Ziel.

Der Runner ist deshalb kein blindes Shell-Terminal. Er ist eine kontrollierte
Projekt-Ausfuehrungsschicht mit Workspace-Grenzen, Git-Remote-Regeln,
Quality-Gates, Backup-Gate, Deploy-Gate, Smoke-Gate, Rollback/Hold und
audit-sicherem Report.

Die spaetere Project UI soll auf diesem Contract sitzen:

- Projekttitel eingeben oder bestehendes Projekt oeffnen
- Projektart waehlen
- mit der KI im Projektkontext chatten wie im normalen Vibecoding-Prozess
- Planung, Roadmap, Tasks und Aenderungen verfolgen
- bei Bedarf ein neues Repository aus dem Projekttitel erzeugen
- Arbeit serverseitig in einem isolierten Workspace durchfuehren
- Tests/Build/Smoke pruefen
- Bereitstellung freigeben
- optional Cloudflare Tunnel mit separatem Exposure-Gate aktivieren

## ABC Ownership

- Alice: Produkt- und Operator-Sprache, Go/Hold/No-Go-Grenzen, UX-unabhaengige
  Anforderungen.
- Bob: Backend-Modelle, Runner-Vertrag, Tests, spaeter aktive Executor-Schicht.
- Charlie: Scope-Kontrolle, Server-Gates, Commit/Push, Live-Handoff.

## Phase Plan

### P1 Universal Project Contract And Plan Model

Status: **done**

Allowed files:

- `docs/plans/server-project-runner-roadmap.md`
- `src/server_project_runner.py`
- `tests/test_server_project_runner.py`

Result:

- Projekt-Deployments werden als strukturierte Plaene beschrieben.
- Projekttitel werden zu `project_slug`, `repo_name`, `projects/<slug>`
  Workspace und `project:<slug>` Chat-Scope normalisiert.
- Der Runner defaultet nicht auf das Odysseus-Repo.
- Repo-Erzeugung ist als Gate/Plan-Schritt vorhanden, aber noch keine Live
  GitHub/Gitea/Filesystem-Aktion.
- Cloudflare Tunnel ist als separates Exposure-Gate modelliert.
- Push-Remote ist standardmaessig `fuzzy`.
- `origin` ist fuer Push/Deploy-Planung blockiert.
- UI-Arbeit bleibt ausserhalb dieses Backend-Runners.
- Tests, Backup-Evidence, Smoke-Ziel, Rollback-Plan und Operator-Go sind
  explizite Gates.
- Keine Live-Kommandos, kein SSH, kein Podman, keine Provider- oder
  Netzwerkaktion in dieser Phase.

### P2 Project Registry And Active Workspace Preparation

Status: **registry model done; active workspace creation remains gated**

Goal:

- Server-seitige Projekt-Registry definieren: Projekt-ID, Titel, Slug,
  Repo-Name, Workspace-Root, Chat-Scope, Projektart, Status und Deployment-Ziele.
- Projekt-Workspaces definieren: repo-relative Root, erlaubte Pfade, blockierte
  Pfade, Branch-Namen, Arbeitsmodus.
- Worktree/Branch-Isolation mit der vorhandenen `workspace_policy` koppeln.

Gate:

- Keine Projektarbeit ausserhalb des erlaubten Workspace-Roots.
- Keine Secrets, privaten Rohdaten, Chat-IDs oder Host-Pfade im Report.

Current result:

- `src/server_project_registry.py` persists universal project records as atomic
  JSON.
- Records bind `project_slug`, `repo_name`, `projects/<slug>` Workspace,
  `project:<slug>` Chat Scope and optional Cloudflare Tunnel request.
- Chat sessions can be attached idempotently.
- Audit summaries report counts and scopes, not raw session contents.
- Active repo creation, filesystem workspace creation, GitHub/Gitea calls and
  Cloudflare Tunnel setup remain future operator-gated slices.

### P2B Project Chat Context

Status: **backend context model done; route/UI wiring remains gated**

Goal:

- Jeden Projektchat an `project:<slug>` binden.
- Memory/RAPTOR-Abfragen und Arbeitsnotizen nur mit diesem Project Scope
  annotieren.
- Chat-Verlauf, Plan und Runner-State zusammenfuehren, ohne Rohdaten ins Repo
  zu schreiben.

Gate:

- Kein Projektchat darf stillschweigend in einen anderen Projekt-Scope schreiben.
- Cross-Project Memory braucht spaeter eine sichtbare UI-Freigabe.

Current result:

- `src/server_project_chat_context.py` binds sessions to registry projects.
- Message metadata can be stamped with `schema`, `project_slug`, `chat_scope`,
  `repo_name`, `workspace_root`, `project_type`, `runner_state` and
  `session_id`.
- Existing metadata for another project is rejected instead of overwritten.
- Context audit summaries omit raw session IDs.
- Actual chat route integration and UI entrypoints remain future slices.

### P3 Quality Gate Integration

Status: **backend dry-run quality gate bundle done; execution remains gated**

Goal:

- Projekt-spezifische Test- und Build-Gates an den vorhandenen
  `live_quality_gate_command_runner` anbinden.
- Nur fokussierte, begrenzte, redigierte Gates erlauben.

Gate:

- Kein Deploy ohne gruenes Test-/Build-Gate.
- Keine unbounded Test-Suites als Default.

Current result:

- `src/server_project_quality_gate.py` wraps the existing
  `live_quality_gate_command_runner` for project-specific gates.
- Default project gates include focused test, build evidence and smoke test
  plans.
- Required gates must be `plan_ready` before the project deploy gate can be
  treated as ready.
- Network, host, destructive, unbounded and secret/path-bearing gate text is
  blocked.
- All gates remain dry-run/operator-review plans and do not execute commands.

### P4 Git And Review Flow

Status: **backend dry-run Git/review plan done; live Git operations remain gated**

Goal:

- Neues Repository aus dem Projekttitel planen oder ein bestehendes Repo
  anbinden.
- Branch anlegen, Aenderungen isolieren, Diff zusammenfassen, Commit-Plan
  erstellen und Push auf erlaubte Projekt-Remotes vorbereiten.

Gate:

- Nie auf `origin` pushen.
- Kein Force-Push, Reset, Checkout-Rewrite oder destruktive Cleanup-Aktion.
- Keine Repo-Erzeugung ohne eindeutigen Projekttitel und Operator-Go.

Current result:

- `src/server_project_git_review.py` models repo creation/attachment,
  worker branch review, change-set review, commit message review and push
  target review as data.
- Push remote is restricted to `fuzzy`; `origin` is blocked.
- New repository creation requires `operator_decision=go`.
- Unsafe branch names, absolute paths, secret-like commit messages and
  missing changed paths are rejected or held.
- No Git command is executed by this slice.

### P5 Deploy Handoff

Status: **backend operator-gated deploy handoff done; live deploy remains gated**

Goal:

- Projekt-Deployments an den Safe-Updater-Flow koppeln:
  pre-update snapshot, fast-forward/metadata, Podman compose, Healthcheck,
  Smoke, Rollback/Hold.

Gate:

- Live-Ausfuehrung nur mit separatem Operator-Go.
- Backup- und Restore-Smoke-Evidence muessen gruen sein.

Current result:

- `src/server_project_deploy_handoff.py` combines project quality gates, Git
  review, backup evidence and the existing updater live-boundary model.
- Handoff can become `ready_for_operator_go`, but still reports
  `live_execution_allowed=false`.
- Missing backup/restore evidence, blocked quality gates, held Git review,
  secret risk or requested live command execution hold or no-go the handoff.
- Planned deploy steps cover pre-update snapshot, metadata refresh, Podman
  handoff, healthcheck, smoke and rollback/hold as non-executing steps.

### P6 Active Executor

Status: **active executor implemented; default execution remains blocked**

Goal:

- Einen kleinen aktiven Executor bauen, der nur whitelisted Schritte mit
  `shell=False`, Timeout, redigierten Reports und Stop-on-first-failure
  ausfuehrt.

Gate:

- Default bleibt dry-run.
- Live erfordert `live_enabled=True`, `operator_decision=go`, gruenes Bundle
  und konkrete bounded Inputs.

Current result:

- `src/server_project_executor.py` implements whitelisted active execution
  steps with `subprocess.run(..., shell=False)`, timeout, captured output and
  secret-like output redaction.
- Execution is blocked unless the deploy handoff is `ready_for_operator_go`,
  live mode is enabled and `operator_decision=go`.
- Unsupported commands are blocked before a runner is called.
- Tests use fake runners for active paths; no live deploy, host, network or
  provider action is performed by verification.

### P7 Server Install And Service Wiring

Status: **backend service wiring templates done; live server install remains gated**

Goal:

- Auf dem Debian-Server systemd/Podman-Integration, Logs und Healthchecks
  operator-gegated einrichten.

Gate:

- Keine Secrets ins Repo.
- Kein Host-Pfad oder privater Output in Persistenzartefakten.

Current result:

- `src/server_project_service_wiring.py` builds reviewable systemd user-service,
  healthcheck and log unit templates for a project.
- Unit templates use placeholders such as `$ODYSSEUS_PROJECTS_ROOT` and
  `$ODYSSEUS_USER_BIN_DIR` instead of private host paths.
- Service install is `plan_ready` only with ready deploy handoff and
  `operator_decision=go`.
- Cloudflare exposure remains a separate hold gate with route/token/DNS and
  healthcheck review.
- No server install, service reload, Podman lifecycle action or tunnel setup is
  performed by this slice.

### P8 Project UI And AI Command Surface

Goal:

- Project UI fuer Planung, Chat, Tasks, Runner-State, Repo, Build/Test,
  Deployment und Cloudflare Tunnel.
- Odysseus AI kann Projektaufgaben starten, Status melden, Gates erklaeren und
  Human Decisions anfordern.

Gate:

- Keine automatische Live-Aktion aus Chat-Nachrichten.
- Jede riskante Aktion braucht eine explizite Freigabe.

## Current Human Decision Needed

Als naechstes sollte entschieden werden, ob P2 zuerst eine lokale
Project-Registry im Odysseus-Backend bekommt oder direkt ein serverseitiges
`projects/` Workspace-Schema fuer mehrere neue Repositories angelegt werden
soll.
