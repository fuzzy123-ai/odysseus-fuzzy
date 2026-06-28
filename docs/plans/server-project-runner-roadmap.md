# Odysseus Server Project Runner Roadmap

Stand: 2026-06-27

Status: **Non-UI backend/API slices plus local workspace/repo provisioning, planner adapter, task runner, commit runner and push runner done; visual Project UI and provider repo creation remain gated**

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

Status: **registry model and local workspace provisioning done; provider repo creation remains gated**

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
- `src/server_project_provisioner.py` can provision a local project workspace
  under a configured server projects root when `live_enabled=true` and
  `operator_decision=go`.
- Provisioning creates the project workspace, local repo directory and a small
  redacted `.odysseus/project.json` marker without persisting the host root.
- The provisioner emits a `WorkerWorkspaceAssignment` for later autonomous
  project agents.
- The API exposes this as `/api/projects/{project_slug}/provision`.
- GitHub/Gitea repository creation, remote attachment and Cloudflare Tunnel
  setup remain future operator/provider-gated slices.

### P2B Project Chat Context

Status: **backend context model and API binding done; visual UI wiring remains gated**

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
- Backend chat binding is exposed through `/api/projects/{project_slug}/chat-bind`.
- Visual UI entrypoints remain a future slice.

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

Status: **backend Git/review plan, local repo init, local commit and gated push runners done; provider repo operations remain gated**

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
- `src/server_project_repo_provisioner.py` can initialize the local project Git
  repository inside `projects/<slug>/repo` when workspace provisioning is done
  and `live_enabled=true` plus `operator_decision=go` are present.
- The repo provisioner writes a redacted `.odysseus-repo.json` marker and uses
  a tight `git init -b <branch>` allowlist with `shell=False`.
- The API exposes this as `/api/projects/{project_slug}/repo-provision`.
- Push remote is restricted to `fuzzy`; `origin` is blocked.
- New repository creation requires `operator_decision=go`.
- Unsafe branch names, absolute paths, secret-like commit messages and
  missing changed paths are rejected or held.
- `src/server_project_commit_runner.py` can locally review and commit changed
  project paths after green task checks when `live_enabled=true` and
  `operator_decision=go` are present.
- The commit runner executes only `git status --short --branch`,
  `git add -- <reviewed paths>` and `git commit -m <safe message>` with
  `shell=False`.
- The API exposes this as `/api/projects/{project_slug}/commit-run`.
- `src/server_project_push_runner.py` can push a confirmed local project commit
  to the allowlisted `fuzzy` remote and blocks `origin`, force-style requests,
  unsafe branches and unconfirmed commits.
- The push runner executes only `git status --short --branch` and
  `git push fuzzy <branch>` with `shell=False`.
- The API exposes this as `/api/projects/{project_slug}/push-run`.
- Remote provider repository creation and remote attachment remain future
  provider/operator-gated slices.

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

Status: **active executor and project task runner implemented; default execution remains blocked**

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
- `src/server_project_task_runner.py` implements a minimum autonomous project
  task loop: bounded file writes inside `projects/<slug>/repo`, allowed checks,
  stop-on-first-failure and redacted task reports.
- `src/server_project_task_planner.py` adapts structured AI planner output into
  executable task-runner inputs with acceptance criteria, safe file writes and
  default checks per profile.
- Task execution requires the local Git repository to exist and
  `live_enabled=true` plus `operator_decision=go`.
- The task command allowlist covers focused `pytest`, `npm test`,
  `npm run test`, `npm run build`, `node --check` and `git status`.
- The API exposes this as `/api/projects/{project_slug}/task-run`.
- Planner-to-task execution is exposed as
  `/api/projects/{project_slug}/planner-task-run`.
- Deploy execution is blocked unless the deploy handoff is
  `ready_for_operator_go`, live mode is enabled and `operator_decision=go`.
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

Status: **backend API surface done; UI remains gated**

Goal:

- Project UI fuer Planung, Chat, Tasks, Runner-State, Repo, Build/Test,
  Deployment und Cloudflare Tunnel.
- Odysseus AI kann Projektaufgaben starten, Status melden, Gates erklaeren und
  Human Decisions anfordern.

Gate:

- Keine automatische Live-Aktion aus Chat-Nachrichten.
- Jede riskante Aktion braucht eine explizite Freigabe.

Current backend result:

- `routes/server_project_routes.py` exposes backend API routes for listing,
  creating and reading projects plus binding an existing chat session to a
  project.
- `app.py` registers the `/api/projects` router.
- API persistence uses the safe project registry and does not create repos,
  deploy services, run tunnels or execute commands.
- Visual Project UI remains intentionally open.

## Current Human Decision Needed

Als naechstes sollte entschieden werden, welcher Provider fuer neue
Projekt-Repositories benutzt wird: GitHub im `fuzzy123-ai` Namespace, ein
serverlokales Gitea/Forgejo, oder zunaechst nur lokale Git-Repos ohne Remote.
Danach kann die visuelle Project UI gestaltet werden.

## Related Repo-Control Track

Der P4 Git/Review-Flow in dieser Roadmap gilt fuer Project-Runner-Repos. Die
breitere Frage "Odysseus kennt und verwaltet alle explizit freigegebenen Repos"
ist als eigener Backend-Track erfasst:

- `docs/plans/repo-control-roadmap.md`

Dieser Track soll eine generische Repo-Registry, read-only Git-Intelligence,
Remote-Policy, bestaetigte Commit-/Push-Flows, DSGVO-/Local-only-Gates und
spaetere Project-UI-Anbindung liefern. Der Project Runner soll diesen
generischen Repo-Control-Layer spaeter wiederverwenden, statt eine zweite
Git-Welt aufzubauen.
