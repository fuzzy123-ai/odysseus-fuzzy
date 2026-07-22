# Odysseus Project Versioning And Forge Provider Roadmap

Stand: 2026-07-13

Status: **repository backend complete / Partial; PVF1-PVF8 and PVF13 done; UI and live provider writes gated**

Mode: **Standard ABC, backend/logik-first**

## Master Integration

Diese Roadmap ist der Detailvertrag fuer den neuen `0.22.x`-Track in
`docs/plans/unified-odysseus-roadmap.md` und Lane `L15` im
`docs/plans/central-abc-masterplan-2026-06-29.md`.

Sie wird bewusst nicht rueckwirkend in die abgeschlossene MVP-Runner-Queue
`docs/plans/mvp-roadmap-runner-state.json` aufgenommen. Das ausdrueckliche
Operator-Go fuer die Implementierung wurde am 2026-07-13 erteilt. Live
Nextcloud-/GitHub-Schreibaktionen brauchen weiterhin eigene enge Gates.

## Goal

Odysseus besitzt eine lokale, providerunabhaengige Forge, in der genau ein
sichtbares `commit_project`-Tool einen geprueften lokalen Git-Commit mit Titel
und Beschreibung erzeugt und danach anhand der gespeicherten Projekt-Policy
automatisch nach Local, Nextcloud, GitHub oder GitHub plus optionalem
Nextcloud-Dual-Backup synchronisiert.

## Frozen Product Decisions

1. Die lokale Forge ist immer vorhanden und bleibt die kanonische Arbeits- und
   Versionsquelle.
2. Fuer Nutzer und Agent existiert genau ein Commit-Workflow und genau ein
   sichtbares Tool: `commit_project`.
3. Provider werden nicht vom Modell pro Tool-Aufruf gewaehlt. Das Tool liest
   die persistierte Projekt-Policy.
4. Unterstuetzte Betriebsarten sind `local`, `nextcloud` und `github`.
5. Wenn GitHub aktiv ist, kann Nextcloud optional als Dual Backup mitlaufen.
6. Nextcloud-Projektdateien bleiben normal lesbar. Odysseus verschluesselt sie
   im MVP nicht clientseitig.
7. Der aktive `.git`-Ordner wird niemals per Nextcloud-Dateisync gespiegelt.
   Nextcloud erhaelt einen lesbaren Projektbaum, ein Manifest, Artefakte und
   optional ein unverschluesseltes Git-Bundle fuer die vollstaendige
   Wiederherstellung.
8. GitHub erhaelt native Git-Commits und Branches ueber den vorhandenen,
   policy-geprueften Git-Push-Pfad.
9. Nextcloud und GitHub synchronisieren nie direkt miteinander. Die lokale
   Forge ist der Hub.
10. Externe Provider-Ausfaelle rollen den lokalen Commit nicht zurueck. Der
    gemeinsame Vorgang wird `partial` beziehungsweise `sync_pending` und ueber
    eine persistente Outbox wiederholt.
11. Inbound-Aenderungen werden niemals mit Last-Write-Wins uebernommen.
    Provider-Divergenz landet in einem `incoming/<provider>/...`-Review-Pfad.
12. Automatische Sandbox-Checkpoints bleiben standardmaessig lokal. Benannte
    Versionen, Releases und Artefakte koennen nach Policy gespiegelt werden.

## User Workflow

```text
Aenderungen pruefen
      -> Commit-Titel und Beschreibung bestaetigen
      -> commit_project
      -> lokaler Git-Commit
      -> Provider-Policy auswerten
      -> konfigurierte Provider synchronisieren
      -> ein gemeinsamer Transaktionsstatus
```

Beispielstatus:

```text
Commit:     erfolgreich
GitHub:     synchronisiert
Nextcloud:  ausstehend - Wiederholung eingeplant
Gesamt:     partial
```

Die KI ruft keine separaten oeffentlichen Tools wie `git_commit`,
`github_push`, `nextcloud_upload`, `create_bundle` oder `retry_sync` auf. Diese
Funktionen sind interne Adapter und Worker hinter `commit_project`.

## Provider Model

| Provider | Rolle | Mindestfaehigkeiten |
| --- | --- | --- |
| `local` | kanonische Forge | Repo, Commit, Branch, Checkpoint, Version, Artefakt-Refs, Restore |
| `nextcloud` | lesbare Remote Forge oder Dual Backup | WebDAV Sync, lesbarer Projektbaum, Manifest, Artefakte, unverschluesseltes Recovery-Bundle |
| `github` | native Git Forge | Git Push/Fetch, Branch-Refs, spaeter optional Pull Requests und Releases |

Provider implementieren Capability-Interfaces. Ein Provider darf fehlende
Faehigkeiten deklarieren; Nextcloud muss keine Pull Requests vortaeuschen und
GitHub muss nicht automatisch das private Artefaktarchiv uebernehmen.

Empfohlene Policy:

```yaml
schema: odysseus.project_forge_policy.v1
forge_mode: github
sync_on_commit: true
backup_providers:
  - nextcloud
nextcloud:
  mirror_scope: named_versions_and_releases
  include_readable_tree: true
  include_artifacts: true
  include_git_bundle: true
  client_side_encryption: false
github:
  push_branch: true
```

## Commit Contract

Minimaler Input fuer das eine Tool:

```json
{
  "repo_id": "my-game",
  "title": "Persist sandbox artifacts",
  "description": "Store generated deliverables outside the disposable sandbox and link them to the project version.",
  "version_label": "v0.4",
  "change_notes": [
    "Add persistent output mount",
    "Record artifact manifest"
  ],
  "reviewed_paths": ["src", "tests"],
  "checks_passed": true,
  "content_reviewed": true,
  "confirmed": true
}
```

Der Titel wird die erste Zeile der Git-Commit-Message. Beschreibung und
Change Notes werden als normaler mehrzeiliger Commit-Body gespeichert. Reine
Odysseus-Metadaten wie Run-ID, Evidence und Provider-Status bleiben in einem
owner-scoped Version Record und werden nicht unkontrolliert in externe
Commit-Trailer geschrieben.

Minimaler Output:

```json
{
  "schema": "odysseus.project_commit_transaction.v1",
  "transaction_id": "pct_...",
  "repo_id": "my-game",
  "commit_sha": "...",
  "local_status": "committed",
  "provider_statuses": {
    "github": "synced",
    "nextcloud": "sync_pending"
  },
  "overall_status": "partial",
  "retry_scheduled": true
}
```

## Nextcloud Layout

Nextcloud bleibt fuer Menschen nutzbar und lesbar:

```text
Odysseus/Projects/<project>/
  Current/
    <normaler lesbarer Projektbaum>
  Versions/
    <version-id>/
      README.md
      manifest.json
      repository.bundle
  Artifacts/
    <version-id>/
  .odysseus/
    remote-state.json
```

Uploads werden zuerst unter einem run-spezifischen Staging-Pfad geschrieben
und erst nach Hash-/Groessenpruefung in den sichtbaren Zielstand promoted.
Overwrite, Delete und direkte Konfliktaufloesung bleiben getrennte Gates.

## Current Evidence

- `src/repo_registry.py` persistiert freigegebene Repositories, Remotes,
  Privacy Class, Provider Scope und erlaubte Aktionen.
- `src/repo_git_adapter.py` liefert begrenzte Git-Snapshots.
- `src/repo_commit_runner.py` plant und fuehrt bestaetigte lokale Commits fuer
  gepruefte Pfade aus.
- `src/repo_push_runner.py` plant und fuehrt policy-gepruefte Git-Pushes aus.
- `src/repo_forge_provider.py` modelliert GitHub/Gitea/Forgejo-Metadaten und
  Provider-Gates, ist aber noch kein universeller Forge-Adapter.
- `src/coding_agent_backend.py` erzeugt taskbezogene persistente Worktrees.
- `src/nextcloud_webdav_client.py` bietet den bestehenden, gegateten
  Nextcloud-WebDAV-Pfad.
- `src/nextcloud_software_archives.py` und
  `src/nextcloud_software_archive_executor.py` belegen ZIP-/Sidecar-/Manifest-
  Archive ohne Original-Delete.
- `src/generated_artifact_publication.py` publiziert owner-scoped generierte
  Artefakte in den bestehenden Upload Store.
- `src/sandbox_job_ledger.py` und `src/sandbox_artifact_policy.py` liefern
  redaktierte Run-/Artefakt-Evidence, speichern aber noch keine universelle
  Projektversion.

## Non-Goals

- Kein eigenes Git-Dateiformat und keine Neuimplementierung von Git.
- Kein Sync eines aktiven `.git`-Verzeichnisses ueber Nextcloud Desktop.
- Keine automatische Nextcloud-zu-GitHub-Synchronisation.
- Kein Force Push, History Rewrite, `reset --hard`, destruktives Cleanup oder
  automatisches Merge/Rebase.
- Keine providerabhaengige Tool-Auswahl durch das Modell.
- Keine stillen Commits fremder, ungepruefter oder ausserhalb des Scope
  liegender Dateien.
- Keine Secrets, Tokens, Credential-URLs, privaten Host-Pfade oder Raw-Provider-
  Antworten in Commit-Beschreibung, Manifest, Tests oder Ledger.
- Keine clientseitige Odysseus-Verschluesselung fuer Nextcloud im MVP.
- Keine breite visuelle Forge-/GitHub-Kopie ohne separaten UI-Design-Slice.

## Stop Rules

- Stop bei fremden staged files, Hotfile-Konflikt oder ungeklaertem Worktree.
- Stop bei Secrets oder privaten Pfaden in Commit-Text, Diff, Provider-Payload
  oder Testoutput.
- Stop bei Provider-/Branch-/Projekt-Ambiguitaet.
- Stop vor Live Nextcloud/GitHub Writes ohne das jeweilige Live-Go.
- Stop vor Delete, Overwrite, Remote-Branch-Loeschung, Force Push oder
  automatischer Konfliktaufloesung.
- Stop, wenn ein Slice Tool-/Route-Hotfiles beruehrt, waehrend dort fremde
  Aenderungen aktiv sind.
- Rote fokussierte Tests duerfen nur mit engem, in-scope Fix weitergefuehrt
  werden; sonst Blocker/Handoff.

## Slice Queue

### PVF0 Contract And Master Routing

Status: `done` mit Erstellung dieser Roadmap

Class: `repo_only`

Owner: Charlie

Recommended model: GPT-5.6 Sol, weil Produktgrenzen, Provider-Semantik,
Security und Master-Abhaengigkeiten zusammengefuehrt werden.

Allowed paths:

- `docs/plans/project-versioning-forge-provider-roadmap.md`
- `docs/plans/unified-odysseus-roadmap.md`
- `docs/plans/central-abc-masterplan-2026-06-29.md`
- `docs/plans/multi-agent-execution-guidance.json`

Verification:

```powershell
git diff --check -- docs/plans/project-versioning-forge-provider-roadmap.md docs/plans/unified-odysseus-roadmap.md docs/plans/central-abc-masterplan-2026-06-29.md docs/plans/multi-agent-execution-guidance.json
```

### PVF1 Provider-Neutral Contracts And Policy

Status: `done` am 2026-07-13

Class: `safe_offline`

Owner: Bob

Recommended model: GPT-5.6 Terra, weil es ein begrenzter Modell-, Validator-
und Test-Slice ohne Live IO ist.

Dependencies: PVF0

Allowed paths:

- `src/project_forge_contract.py`
- `src/project_forge_policy.py`
- `tests/test_project_forge_contract.py`
- `tests/test_project_forge_policy.py`

Done when:

- Provider, Capabilities, Projekt-Policy, Commit Request/Result und
  Provider-Status sind strikt validiert.
- `local`, `nextcloud`, `github` und optionaler Dual Backup sind darstellbar.
- Provider-Auswahl ist nur aus persistierter Policy ableitbar.
- Secret-/Pfadmarker und unbekannte Capabilities werden blockiert.

Tests:

```powershell
venv\Scripts\python.exe -m pytest tests\test_project_forge_contract.py tests\test_project_forge_policy.py -q
```

### PVF2 Local Forge Store And Version Manifest

Status: `done` am 2026-07-13

Class: `repo_only`

Owner: Bob

Recommended model: GPT-5.6 Terra, weil persistente lokale Modelle und atomare
Dateioperationen mit fokussierten Tests gebaut werden.

Dependencies: PVF1

Allowed paths:

- `src/constants.py`
- `src/project_forge_local.py`
- `src/project_version_store.py`
- `tests/test_project_forge_local.py`
- `tests/test_project_version_store.py`

Done when:

- Lokale Version Records und Manifeste liegen owner-/project-scoped unter
  `DATA_DIR`.
- Atomare Writes, Idempotenz, Hashes und Restore-Readiness sind getestet.
- Lokale Forge funktioniert ohne konfigurierten externen Provider.

Tests:

```powershell
venv\Scripts\python.exe -m pytest tests\test_project_forge_local.py tests\test_project_version_store.py -q
```

### PVF3 Unified Commit Service With Description

Status: `done` am 2026-07-13

Class: `repo_only`

Owner: Bob

Recommended model: GPT-5.6 Sol, weil bestehende Commit-Gates, mehrzeilige
Beschreibung, Transaktionsgrenzen und fremde Worktree-Aenderungen gemeinsam
abgesichert werden muessen.

Dependencies: PVF1, PVF2

Allowed paths:

- `src/repo_commit_runner.py`
- `src/project_commit_service.py`
- `tests/test_repo_commit_runner.py`
- `tests/test_project_commit_service.py`

Done when:

- Titel, Beschreibung, Version Label und Change Notes werden sicher
  normalisiert.
- Genau die reviewed paths werden committed.
- Der lokale Commit entsteht vor jedem Provider-Dispatch.
- Ein providerloser Commit ist ein vollstaendiger Erfolg.
- Externe Fehler koennen den lokalen Commit nicht zurueckrollen.

Tests:

```powershell
venv\Scripts\python.exe -m pytest tests\test_repo_commit_runner.py tests\test_project_commit_service.py -q
```

### PVF4 Persistent Sync Outbox And Reconciliation

Status: `done` am 2026-07-13

Class: `repo_only`

Owner: Bob

Recommended model: GPT-5.6 Terra, weil Saga-/Retry-Zustaende deterministisch
und ohne Live Provider testbar sind.

Dependencies: PVF2, PVF3

Allowed paths:

- `src/project_forge_outbox.py`
- `src/project_forge_sync.py`
- `tests/test_project_forge_outbox.py`
- `tests/test_project_forge_sync.py`

Done when:

- `pending`, `syncing`, `synced`, `partial`, `failed`, `conflict` und
  `retry_scheduled` sind persistente, idempotente Zustaende.
- Neustart/Reconciliation verliert keinen erfolgreichen lokalen Commit.
- Kein Retry erzeugt doppelte Provider-Versionen.
- Inbound-Divergenz wird als Review-Branch/Packet statt Last-Write-Wins
  modelliert.

Tests:

```powershell
venv\Scripts\python.exe -m pytest tests\test_project_forge_outbox.py tests\test_project_forge_sync.py -q
```

### PVF5 Nextcloud Forge Adapter, Offline First

Status: `done` am 2026-07-13; offline getestet, kein Live-Write

Class: `repo_only`

Owner: Bob

Recommended model: GPT-5.6 Sol, weil WebDAV-Pfade, lesbarer Mirror, Staging,
Manifest-Integritaet und No-Delete-Grenzen security-relevant sind.

Dependencies: PVF1, PVF4

Allowed paths:

- `src/project_forge_nextcloud.py`
- `src/nextcloud_webdav_client.py`
- `tests/test_project_forge_nextcloud.py`
- `tests/test_nextcloud_webdav_client.py`

Done when:

- Fake Client deckt Plan, Staging, Upload, Verifikation und Promotion ab.
- Projektbaum und Beschreibungen bleiben im Provider lesbar.
- Kein clientseitiger Encryption-Schritt wird still eingefuehrt.
- `.git`, Secrets, Cache-/Dependency-Ordner und blockierte Pfade werden nicht
  gespiegelt.
- Delete, unbestaetigtes Overwrite und Konfliktaufloesung bleiben blockiert.

Tests:

```powershell
venv\Scripts\python.exe -m pytest tests\test_project_forge_nextcloud.py tests\test_nextcloud_webdav_client.py -q
```

### PVF6 GitHub Forge Adapter, Offline First

Status: `done` am 2026-07-13; offline getestet, Live-Push bleibt gesperrt

Class: `repo_only`

Owner: Bob

Recommended model: GPT-5.6 Sol, weil Remote-/Branch-Policy, Provider Auth,
Commit-SHA-Bindung und Push-Idempotenz sicherheitsrelevant sind.

Dependencies: PVF1, PVF4

Allowed paths:

- `src/project_forge_github.py`
- `src/repo_forge_provider.py`
- `src/repo_push_runner.py`
- `tests/test_project_forge_github.py`
- `tests/test_repo_forge_provider.py`
- `tests/test_repo_push_runner.py`

Done when:

- Fake GitHub Provider und command runner belegen native Push-Planung ohne
  Netzwerk.
- Der Provider erhaelt exakt den lokal bestaetigten Commit und Branch.
- Force Push, falscher Remote, abweichende SHA und unklare Auth blockieren.
- Pull Requests/Releases bleiben optionale Capabilities, nicht Voraussetzung
  fuer Commit/Push.

Tests:

```powershell
venv\Scripts\python.exe -m pytest tests\test_project_forge_github.py tests\test_repo_forge_provider.py tests\test_repo_push_runner.py -q
```

### PVF7 One Public `commit_project` Tool

Status: `done` am 2026-07-13; genau eine oeffentliche Commit-Aktion,
Provider-Auswahl nur aus der gespeicherten Policy

Class: `repo_only`

Owner: Bob

Recommended model: GPT-5.6 Sol, weil eine effectful Tool-Surface, Policy,
Confirmation, Evidence und vorhandene Tool-Hotfiles integriert werden.

Dependencies: PVF3, PVF4, PVF5, PVF6

Allowed paths:

- `src/agent_tools/project_commit_tools.py`
- `src/tool_schema_definitions.py`
- `src/tool_schemas.py`
- `src/tool_index.py`
- `src/tool_policy.py`
- `src/tool_security.py`
- `src/tool_execution.py`
- `src/tool_domains/repo_skills.py`
- `src/effectful_tool_matrix.py`
- `src/agent_tools/__init__.py`
- `tests/test_commit_project_tool.py`
- `tests/test_tool_policy.py`
- `tests/test_manage_repos_read_tool.py`
- `tests/test_effectful_tool_matrix.py`

Done when:

- Nur `commit_project` ist als oeffentliche Commit-/Provider-Aktion sichtbar.
- Tool-Argumente enthalten keine freie Provider-Wahl.
- Tool liest die gespeicherte Policy und orchestriert lokale Commit- plus
  Provider-Synchronisation.
- Confirmation, checks, reviewed paths, description und Evidence sind
  verpflichtend beziehungsweise eindeutig optional modelliert.
- Tool liefert eine gemeinsame Transaktions-ID und Provider-Teilstatus.

Tests:

```powershell
venv\Scripts\python.exe -m pytest tests\test_commit_project_tool.py tests\test_tool_policy.py -q
```

### PVF8 Admin API And Project Integration

Status: `done` am 2026-07-13; owner-scoped Router, App-Verkabelung,
ServerProject-Registry-Bindung und Legacy-Commit-/Push-Ablösung sind offline
getestet

Class: `repo_only`

Owner: Bob/Charlie, seriell

Recommended model: GPT-5.6 Terra fuer Route/DTO, danach GPT-5.6 Sol fuer die
Hotfile-Integration und Scope-Abnahme.

Dependencies: PVF7

Allowed paths:

- `routes/project_versioning_routes.py`
- `routes/server_project_routes.py`
- `app.py`
- `src/project_commit_service.py`
- `src/repo_commit_runner.py`
- `tests/test_project_versioning_routes.py`
- `tests/test_server_project_routes.py`

Done when:

- Admin kann Project Forge Policy, Versionen und Sync-Status owner-scoped
  lesen/aendern.
- Commit Route nutzt denselben Service wie das Tool, keinen zweiten Workflow.
- `app.py` wird erst angefasst, wenn kein fremder Hotfile-Slice aktiv ist.

Tests:

```powershell
venv\Scripts\python.exe -m pytest tests\test_project_versioning_routes.py tests\test_server_project_routes.py -q
```

### PVF9 UX Contract And Design Gate

Status: `deferred`; wartet auf `PVF-UI-DESIGN`

Class: `needs_design`

Owner: Alice

Recommended model: GPT-5.6 Sol, weil Provider-Modus, Commit-Beschreibung,
Partial Sync, Konflikt-Review und Restore eine gemeinsame UX-Entscheidung
brauchen.

Dependencies: PVF1 contract freeze; Backend darf bis PVF8 ohne neue visuelle
Forge-Oberflaeche fortfahren.

Allowed paths after design Go:

- `docs/plans/project-versioning-forge-provider-roadmap.md`
- ein spaeter explizit freigegebener UI-Scope

Decision needed:

- Platzierung der Project-Version-/Provider-Sicht.
- Wording fuer `Commit`, `Commit & Sync`, `Partial`, `Incoming`, `Restore`.
- Ob die Beschreibung inline, in einem Review-Dialog oder beidem bearbeitet
  wird.

### PVF10 Nextcloud Live Commit/Push Smoke

Status: `deferred`; wartet auf `PVF-NEXTCLOUD-LIVE-GO`

Class: `needs_live_go`

Owner: Charlie

Recommended model: GPT-5.6 Sol, weil echte private Provider-Daten,
Credentials, WebDAV Writes und Restore-Evidence betroffen sind.

Dependencies: PVF5, PVF7, gruene Backup-/Credential-/Target-Gates

Bounded evidence:

- synthetisches Testprojekt
- ein Commit mit Titel und Beschreibung
- lesbarer `Current`-Baum
- Manifest/Bundle/Artefakt-Hash verifiziert
- kein Delete, kein ungeprueftes Overwrite, keine privaten Inhalte
- Pull/Restore in ein leeres Testrepo belegt

### PVF11 GitHub Live Commit/Push Smoke

Status: `deferred`; wartet auf `PVF-GITHUB-LIVE-GO`

Class: `needs_live_go`

Owner: Charlie

Recommended model: GPT-5.6 Sol, weil echter Remote-Write, Credentials,
Branch-Policy und oeffentliche/private Sichtbarkeit betroffen sind.

Dependencies: PVF6, PVF7, exakter Test-Remote und Branch, Operator-Go

Bounded evidence:

- synthetisches oder explizit freigegebenes Testrepo
- ein non-force Push des bestaetigten Commits
- Commit-Titel und Beschreibung auf Provider lesbar
- lokale und Remote SHA stimmen ueberein
- keine Issue-/PR-/Release-Mutation ohne separates Go

### PVF12 GitHub Plus Optional Nextcloud Dual Backup Smoke

Status: `deferred`; wartet auf beide Provider-Live-Gates und PVF10/PVF11

Class: `needs_live_go`

Owner: Charlie

Recommended model: GPT-5.6 Sol, weil zwei externe Writes, Partial Failure,
Retry und Restore zusammen verifiziert werden.

Dependencies: PVF10, PVF11

Bounded evidence:

- ein `commit_project`-Aufruf
- GitHub `synced`
- Nextcloud `synced` oder bewusst provoziertes `sync_pending` mit erfolgreichem
  Retry
- kein zweiter Tool-Aufruf fuer Provider-Dispatch notwendig
- Nextcloud-Kopie bleibt lesbar und kann unabhaengig von GitHub restored werden

### PVF13 Final Integration And Go/Partial/No-Go

Status: `done` am 2026-07-13 mit Ergebnis `Partial`: der sichere lokale und
offline getestete Backend-Kern ist abgeschlossen; sichtbare UI und echte
Provider-Writes bleiben bewusst deferred

Class: `repo_only` plus vorhandene Live Evidence

Owner: Charlie

Recommended model: GPT-5.6 Sol, weil Scope, Tests, Provider-Evidence,
Security-Gates und Master-Status abschliessend zusammengefuehrt werden.

Dependencies: PVF1-PVF8; PVF9-PVF12 duerfen bewusst `deferred` sein, muessen
aber ehrlich ausgewiesen werden.

Done when:

- Ein Tool/Service ist die einzige Commit-Autoritaet.
- Local-only ist voll funktionsfaehig.
- Nextcloud-/GitHub-Adapter sind offline getestet.
- Live-Evidence ist Go oder als Partial/Deferred dokumentiert.
- Master-Roadmap, Guidance und Handoff spiegeln denselben Status.

## Gate Queue

Gate: `PVF-IMPLEMENTATION-GO`

Status: `satisfied` am 2026-07-13 durch den ausdruecklichen Nutzerauftrag,
mit ABC und gesetztem Goal die Umsetzung zu beginnen

Class: `needs_design`

Blocks: PVF1-PVF8 Dispatch

Decision needed: ausdruecklich mit der repo-only Implementierung dieser
Roadmap starten.

Safe preparation done: Produktentscheidungen, Architektur, Pfade, Tests und
Stop-Regeln sind dokumentiert.

Risk if bypassed: ein neuer aktiver Track kollidiert mit dem stark belegten
Worktree und vorhandenen Tool-/Route-Hotfiles.

Next safe slice: PVF1 nach Go.

---

Gate: `PVF-UI-DESIGN`

Class: `needs_design`

Blocks: PVF9 sichtbare Forge-/Commit-Oberflaeche

Decision needed: Platzierung und Interaction Design gemeinsam festlegen.

Safe preparation done: Backend-DTOs und benoetigte UX-Zustaende sind in
dieser Roadmap beschrieben.

Risk if bypassed: parallele oder inkonsistente Commit-Workflows entstehen.

Next safe slice: PVF10/11 nur nach Backend und Live-Go, UI ist dafuer nicht
zwingend.

---

Gate: `PVF-NEXTCLOUD-LIVE-GO`

Class: `needs_live_go`

Blocks: PVF10 und Nextcloud-Teil von PVF12

Decision needed: exakter Nextcloud-User, Testprojekt, Zielroot und erlaubter
Write/Overwrite-Scope.

Safe preparation done: Fake Client, Dry-run, Staging, Hash- und No-Delete-
Vertrag.

Risk if bypassed: private Daten, falsche Ordner oder bestehende Dateien werden
ungeprueft beruehrt.

Next safe slice: PVF11, falls separat freigegeben.

---

Gate: `PVF-GITHUB-LIVE-GO`

Class: `needs_live_go`

Blocks: PVF11 und GitHub-Teil von PVF12

Decision needed: exaktes Testrepo, Sichtbarkeit, Remote, Branch und Credential-
Handoff.

Safe preparation done: Fake Provider, Policy und non-force Push-Plan.

Risk if bypassed: falscher Remote/Branch oder unbeabsichtigte externe
Veroeffentlichung.

Next safe slice: none, wenn kein Live-Go vorliegt.

## Verification

Fokussierte Backend-Suite nach PVF8:

```powershell
venv\Scripts\python.exe -m pytest tests\test_project_forge_contract.py tests\test_project_forge_policy.py tests\test_project_forge_local.py tests\test_project_version_store.py tests\test_project_commit_service.py tests\test_project_forge_outbox.py tests\test_project_forge_sync.py tests\test_project_forge_nextcloud.py tests\test_project_forge_github.py tests\test_commit_project_tool.py tests\test_project_versioning_routes.py -q
```

Bestehende Regressionen:

```powershell
venv\Scripts\python.exe -m pytest tests\test_repo_registry.py tests\test_repo_git_adapter.py tests\test_repo_commit_runner.py tests\test_repo_push_runner.py tests\test_repo_forge_provider.py tests\test_nextcloud_webdav_client.py tests\test_server_project_routes.py tests\test_tool_policy.py -q
```

Final:

```powershell
git diff --check
git status --short --branch
```

Abschlussevidence vom 2026-07-13:

- `289 passed, 1 warning` fuer die zusammengefuehrte PVF-, Repo-, Provider-,
  Tool-, ServerProject- und App-Router-Suite.
- Die Warnung ist die bestehende SQLAlchemy-2.x-Deprecation fuer
  `declarative_base()` und kein PVF-Fehler.
- Ein langer pytest-Temp-Pfad traf unter Git for Windows dessen `MAX_PATH`-
  Grenze; derselbe Gesamtlauf ist mit kurzem isoliertem Temp-Root gruen.
- Keine Nextcloud-/GitHub-Live-Schreibaktion, kein Commit und kein Push wurden
  fuer diesen Roadmap-Abschluss ausgefuehrt.

## PVF13 Handoff

- Ergebnis: `Partial`, weil Local-only und der gesamte Offline-Kern gruen sind,
  aber Provider-Live-Evidence und sichtbare UI absichtlich fehlen.
- Einzige oeffentliche Autoritaet: `commit_project`; `manage_repos` plant nur,
  alte ServerProject-`commit-run`/`push-run`-Routen antworten `410`.
- Persistenz: Local Forge, Manifeste, Policy und Outbox liegen owner-/project-
  scoped unter `DATA_DIR`; Serverprojekte ausserhalb `BASE_DIR` werden ueber
  eine exakte Registry-Bindung aufgeloest.
- Provider: Nextcloud bleibt lesbar und unverschluesselt; GitHub bleibt nativer
  non-force Git-Push; beide Adapter sind offline mit Fake-Targets getestet.
- Naechste Arbeit darf nur ueber `PVF-UI-DESIGN`,
  `PVF-NEXTCLOUD-LIVE-GO` oder `PVF-GITHUB-LIVE-GO` gestartet werden.

## Go Language

`Go`:

- Local-only Commit/Version/Restore ist gruen getestet.
- Genau ein oeffentliches `commit_project`-Tool existiert.
- Provider folgen gespeicherter Policy und haben keine freie Modellwahl.
- Alle aktivierten Provider haben passende Offline- und erforderliche Live-
  Evidence.
- Nextcloud-Mirror ist lesbar und ohne `.git`-Sync wiederherstellbar.

`Partial`:

- Local-only ist gruen, aber ein optionaler Provider oder Live-Smoke ist noch
  pending/deferred.
- Ein lokaler Commit ist erfolgreich, waehrend Provider-Sync ueber die Outbox
  nachgeholt wird.

`No-Go`:

- Lokaler Commit kann verloren gehen oder wird bei Providerfehler
  zurueckgerollt.
- Mehrere oeffentliche Tools erzeugen konkurrierende Commit-/Push-Workflows.
- Provider oder Branch koennen vom Modell frei gewaehlt werden.
- Nextcloud verlangt einen aktiven `.git`-Sync, verschluesselte Pflichtdateien
  oder stilles Last-Write-Wins.
- Secrets, private Pfade oder Raw-Provider-Inhalte koennen persistiert werden.

`Deferred`:

- Visuelle Forge UI, GitHub Pull Requests/Releases, Nextcloud direkte
  Mehrnutzer-Edits und automatische Schedule-Syncs koennen bewusst spaeter
  folgen, wenn der Ein-Tool-Commit-Kern abgeschlossen ist.

`Blocked`:

- Ungeklaerte Hotfile-Ownership, rote Safety-Tests, fehlender exakter Provider-
  Scope oder notwendige Live-Credentials ohne freigegebenes Handoff.

## Definition Of Done

- Lokale Forge arbeitet ohne Nextcloud oder GitHub.
- `commit_project` akzeptiert Titel, Beschreibung, optionale Version und
  Change Notes und committed ausschliesslich reviewed paths.
- Die gespeicherte Project Forge Policy bestimmt automatisch Local,
  Nextcloud, GitHub und optionalen Nextcloud-Dual-Backup.
- Nextcloud enthaelt normale lesbare Projektdateien, Beschreibungen, Artefakte
  und eine unabhaengig wiederherstellbare History-Repraesentation.
- GitHub erhaelt den identischen lokalen Commit ueber den nativen Git-Pfad.
- Provider-Ausfaelle sind transparent, persistent retrybar und erzeugen keine
  doppelten Versionen.
- Pull/Inbound-Divergenz braucht Review; kein Last-Write-Wins.
- Tests, Evidence, Master-Anbindung und verbleibende Live-/Design-Gates sind
  konsistent dokumentiert.
