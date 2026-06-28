# Odysseus Repo Control Roadmap

Stand: 2026-06-28

Status: **new backend feature track; roadmap created after reviewing adjacent project, automation, sandbox and GitHub roadmaps**

## Goal

Odysseus soll explizit freigegebene Git-Repositories kennen, lesen,
bewerten und nach klarer Freigabe bearbeiten koennen. Das gilt fuer das
Odysseus-Repo, neue Project-Runner-Repos und spaeter weitere Nutzer-Repos.

Das Ziel ist nicht "Agent darf beliebige Git-Kommandos ueber Shell ausfuehren".
Das Ziel ist eine kontrollierte Repo-Schicht mit Registry, Remote-Policy,
Workspace-Grenzen, DSGVO-/Provider-Gates, Commit-/Push-Bestaetigung und
auditierbarer History.

## Current Evidence

- `docs/plans/server-project-runner-roadmap.md` hat bereits einen
  projektinternen Git/Review-Flow: lokale Repo-Provisionierung, Commit Runner
  und Push Runner fuer Project-Runner-Repos.
- `docs/plans/workspace-sandbox-v2-contract.md` definiert `workspace root`,
  `project root`, writable/blocked roots, Hot Files und Commit-Hygiene.
- `docs/plans/upstream-origin-dev-integration-plan-2026-06-23.md` belegt die
  wichtige Remote-Policy: `origin` ist nicht unser Push-Ziel, `fuzzy/dev` ist
  der eigene Fork-Zielpfad.
- `src/recent_changes.py` und das Tool `recent_changes` erzeugen lokale
  Patch-Notes aus Commits, dirty files, untracked files und mtimes.
- `docs/plans/github-issue-intelligence-roadmap.md` behandelt GitHub Issues,
  aber nicht allgemeine Repo-Operationen.
- `docs/plans/1.1-private-source-ops-roadmap.md` und
  `docs/plans/updater-active-runner-operator-contract.md` enthalten Git als
  Update-/Deploy-Gate, nicht als universelle Repo-Verwaltung.

## Gap

Es gibt noch keine generische Odysseus-Schicht fuer:

- Repo-Registry ueber mehrere freigegebene Repos hinweg.
- Read-only Git-Intelligence wie Status, Log, Diff, Branches, Remotes und
  Patch-Notes pro Repo.
- Per-Repo Remote-Policy: welche Remotes sind read-only, welche duerfen pushen,
  welcher Branch ist Default, welche Aktionen sind verboten.
- Bestaetigte write flows fuer Branch, Stage, Commit und Push ausserhalb des
  Project-Runner-Spezialfalls.
- Repo-zu-Projekt-, Repo-zu-Memory- und Repo-zu-Recent-Changes-Verknuepfung.
- DSGVO-/Local-only-Regeln fuer private Repos, wenn lokale KI statt API-KI
  genutzt werden muss.

## Non-Goals

- Keine freie Shell als Git-API.
- Kein `reset --hard`, force push, history rewrite, checkout-rewrite,
  destructive cleanup oder automatische Merge/Rebase-Ausfuehrung im MVP.
- Keine Secrets, Tokens, private Remote-URLs mit Credentials oder private
  Rohinhalte in Repo-Artefakten, Tests, Reports oder Memories.
- Keine automatische Provider-Repo-Erstellung ohne separaten Operator-Go.
- Keine UI-Entscheidung in diesem Backend-Track; die Project/UI-Agenten
  gestalten spaeter die Oberflaeche.

## Mode

Standard ABC, backend/logik-first. Slices sind `safe_offline` oder `repo_only`,
bis ein explizites Live-/Provider-Go vorliegt.

## Slice Queue

### RC0 Roadmap And Integration Point

Class: `repo_only`

Owner: Charlie

Allowed paths:

- `docs/plans/repo-control-roadmap.md`
- `docs/plans/server-project-runner-roadmap.md`

Done when:

- Diese Roadmap existiert.
- Die Project-Runner-Roadmap verweist auf den generischen Repo-Control-Track.
- Bestehende Roadmaps sind als Evidence eingeordnet.

Verification:

- Docs-only.
- `git diff --check -- docs/plans/repo-control-roadmap.md docs/plans/server-project-runner-roadmap.md`

### RC1 Repo Registry Model

Class: `safe_offline`

Owner: Bob

Goal:

- Ein kanonisches Modell fuer freigegebene Repos bauen.

Proposed paths:

- `src/repo_registry.py`
- `tests/test_repo_registry.py`

Minimum fields:

- `repo_id`, `title`, `repo_kind`, `owner`, `path_ref`
- `workspace_root`, `project_root`, `system_root`
- `default_branch`, `current_branch`
- `remotes` with `name`, `url_redacted`, `purpose`, `push_policy`
- `privacy_class`: `public`, `private`, `sensitive`
- `provider_scope`: `local_only`, `default`, `external_allowed`
- `allowed_actions`
- `linked_project_slug`, optional

Done when:

- Repo records validate without touching real Git.
- Paths are normalized through existing workspace/sandbox policy.
- Remote URLs are redacted.
- Private/sensitive repos default to local-only provider policy.

### RC2 Git Read Adapter

Class: `repo_only`

Owner: Bob

Goal:

- Read-only Git facts fuer registrierte Repos liefern.

Proposed paths:

- `src/repo_git_adapter.py`
- `tests/test_repo_git_adapter.py`

Allowed commands, all with `shell=False` and timeouts:

- `git status --short --branch`
- `git branch --show-current`
- `git log --max-count <n> --date=iso --pretty=...`
- `git diff --name-status`
- `git diff --stat`
- `git remote -v`

Done when:

- Adapter rejects unregistered or out-of-scope paths.
- Outputs are bounded and redacted.
- Dirty status, branch, remotes, recent commits and changed paths are available
  without exposing secrets.

### RC3 `manage_repos` Read Tool

Class: `repo_only`

Owner: Charlie/Bob

Goal:

- Odysseus bekommt ein natives Agent-Tool fuer sichere Repo-Intelligence.

Proposed paths:

- `src/tool_implementations.py`
- `src/tool_execution.py`
- `src/tool_schemas.py`
- `src/tool_index.py`
- `src/agent_loop.py`
- `tests/test_manage_repos_read_tool.py`

Actions:

- `list`
- `get`
- `status`
- `log`
- `diff_stat`
- `changed_paths`
- `remotes`

Done when:

- Read-only Aktionen brauchen keine Bestätigung.
- Das Tool arbeitet nur auf registrierten Repos.
- `app_api` oder Shell muss dafuer nicht genutzt werden.

### RC4 Repo Register / Forget Flow

Class: `repo_only`

Owner: Bob

Goal:

- Repos explizit freigeben oder aus der Registry entfernen.

Actions:

- `register`
- `forget`
- `update_policy`

Gate:

- Mutationen brauchen `confirmed=true`.
- Registrierung eines Pfads ausserhalb erlaubter Roots braucht Operator-Go.
- Forget loescht keine Repo-Dateien, nur den Registry-Eintrag.

Done when:

- Repo-Freigabe ist auditierbar.
- Keine Secrets oder privaten Host-Pfade landen in Reports.
- Existing Project-Runner-Repos koennen importiert oder referenziert werden.

### RC5 Remote Policy And Branch Gate

Class: `safe_offline`

Owner: Bob

Goal:

- Pro Repo entscheiden, welche Remotes und Branches erlaubt sind.

Rules:

- `origin` defaultet auf read-only, solange keine explizite Policy anderes sagt.
- `fuzzy` kann push-allowlisted sein.
- Force push, delete branch, tag publish und protected branch writes sind
  separate blocked/live gates.
- Branch-Namen muessen normalisiert und safe sein.

Proposed paths:

- `src/repo_remote_policy.py`
- `tests/test_repo_remote_policy.py`

Done when:

- Push-Entscheidungen sind deterministisch und testbar.
- Fehlertexte schlagen die erlaubte Alternative vor.

### RC6 Commit Runner

Class: `repo_only`

Owner: Charlie/Bob

Goal:

- Bestaetigt einzelne Pfade stagen und committen, ohne fremde Aenderungen zu
  beruehren.

Allowed commands:

- `git status --short --branch`
- `git add -- <reviewed paths>`
- `git commit -m <safe message>`

Gate:

- `confirmed=true`
- exakte changed paths
- kein fremdes staging
- kein Secret-/Private-Content-Risiko
- optional gruenes Test-/Quality-Gate fuer das Repo

Done when:

- Commit Runner ist fuer generische registrierte Repos verfuegbar.
- Odysseus kann erklaeren, warum ein Commit blockiert wurde.

### RC7 Push Runner

Class: `repo_only` until real remote push; live push itself is `needs_live_go`

Owner: Charlie

Goal:

- Push-Plan und spaeter bestaetigten Push auf erlaubte Remotes ausfuehren.

Gate:

- `confirmed=true`
- Remote policy erlaubt Push.
- Kein `origin`-Push ohne explizite Repo-Policy.
- Kein force push.
- Branch und Commit-SHA sind bekannt.

Done when:

- Push-Plan ist offline testbar.
- Live-Push wird nur mit separatem Go ausgefuehrt.

### RC8 GitHub / Forge Provider Bridge

Class: `needs_live_go`

Owner: Bob/Alice

Goal:

- GitHub, Gitea oder Forgejo als Provider fuer Repo-Metadaten anbinden:
  Issues, PRs, default branch, permissions und optional repo creation.

Gate:

- Auth nur ueber secure handoff oder vorhandene serverseitige Credentials.
- Keine Tokens im Chat, Repo, Logs oder Tests.
- Provider-Auswahl ist Human Decision.

Done when:

- Read-only provider metadata works behind a clear auth gate.
- Repo creation remains separately confirmed.

### RC9 Recent Changes And Memory Integration

Class: `repo_only`

Owner: Bob/Charlie

Goal:

- Neuerungen pro Repo in Recent Changes, Memory/RAPTOR und Project Context
  einhaengen.

Rules:

- Public Repo: commit summaries and paths may be summarized.
- Private Repo: default local-only summarization; no raw diffs to external API.
- Sensitive Repo: only redacted metadata unless secure/local model is selected.

Done when:

- Odysseus kann beantworten: "Was ist in Repo X neu?"
- Snapshots sind repo-scoped und deduped.
- Memory-Eintraege speichern Entscheidungen und Architektur-Aenderungen, nicht
  rohe private Inhalte.

### RC10 API Surface For Future UI

Class: `repo_only`

Owner: Bob

Goal:

- Backend Routes fuer Project UI und Settings UI bereitstellen.

Proposed routes:

- `GET /api/repos`
- `GET /api/repos/{repo_id}`
- `POST /api/repos/register`
- `PATCH /api/repos/{repo_id}/policy`
- `GET /api/repos/{repo_id}/status`
- `GET /api/repos/{repo_id}/changes`
- `POST /api/repos/{repo_id}/commit-plan`
- `POST /api/repos/{repo_id}/push-plan`

Done when:

- UI kann Repos anzeigen, Policies erklaeren und Human Decisions anfordern.
- Keine UI-Implementierung ist Teil dieses Slices.

## Gate Queue

### Gate RC-G1 Provider Choice

Class: `needs_live_go`

Decision needed:

- GitHub `fuzzy123-ai`, lokales Gitea/Forgejo oder zunaechst nur lokale Repos?

Blocks:

- Provider Repo Creation
- Remote attach automation

### Gate RC-G2 Repo Roots

Class: `needs_live_go`

Decision needed:

- Welche lokalen Root-Pfade duerfen als Repo-Registry-Roots verwendet werden?

Blocks:

- Live registration beyond Odysseus workspace
- Multi-repo project access

### Gate RC-G3 Remote Push Policy

Class: `needs_live_go`

Decision needed:

- Welche Remotes duerfen pushen? Standardvorschlag: `fuzzy` erlaubt,
  `origin` read-only.

Blocks:

- Live push runner

### Gate RC-G4 Private Repo Provider Policy

Class: `needs_live_go`

Decision needed:

- Sollen private/sensitive Repos immer local-only KI erzwingen, oder darf der
  Nutzer pro Repo externe API-KI erlauben?

Blocks:

- Diff summarization via external providers
- Repo memory enrichment

## Verification

Initial focused tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_repo_registry.py
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_repo_git_adapter.py
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_repo_remote_policy.py
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_manage_repos_read_tool.py
```

Final backend bundle:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_repo_*.py tests\test_manage_repos_read_tool.py
```

## Go / Partial / No-Go

- `Go`: Repos sind explizit registriert, read-only Git-Intelligence funktioniert
  fuer registrierte Repos, Remote Policy ist testbar, und mutierende Aktionen
  brauchen Bestaetigung.
- `Partial`: Read-only Registry/Status/Log/Diff funktioniert, aber Commit,
  Push, Provider oder Memory-Integration sind noch gated.
- `No-Go`: freie Shell-Git-Ausfuehrung, Secret-Leak, unregistrierte Repo-Pfade,
  `origin`-Push ohne Policy, Force Push oder destruktive Git-Aktionen werden
  benoetigt.
- `Deferred`: UI-Design, echte Provider-Repo-Erstellung, live Pushes und
  private Repo API-KI-Freigaben.

## Recommended Next Slice

`RC1 Repo Registry Model` zuerst. Danach `RC2 Git Read Adapter`, dann
`RC3 manage_repos Read Tool`.

Damit bekommt Odysseus zuerst sichere Wahrnehmung ueber Repos, bevor er
irgendwelche Schreibrechte bekommt.
