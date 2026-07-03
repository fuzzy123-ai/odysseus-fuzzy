# Pre-1.0 Context Efficiency Roadmap

Stand: 2026-06-23

Status: **CTXE1 done; manifest-first backend model started; CTXE2 next**

Mode: **Standard ABC**

## Master Chat Handoff

Master Chat, bitte diese Roadmap als neuen Vor-1.0-Integrationspfad aufnehmen:

- Ziel: Vor `1.0.0` die groessten Kontext- und Tool-Bloat-Risiken in Odysseus reduzieren, ohne neue Live-Abhaengigkeiten oder breite UI-Arbeit zu starten.
- Roadmap: `docs/plans/pre-1.0-context-efficiency-roadmap.md`
- Einordnung: Ergaenzt `docs/plans/mvp-master-roadmap.md`, `docs/plans/dynamic-tool-loading-contract.md`, `docs/plans/fallback-routing-contract.md`, `docs/plans/small-model-evaluation-gates-contract.md` und `docs/plans/tool-result-truth-contract.md`.
- Prioritaet: vor `1.0.0`, aber unterhalb laufender Sicherheits-, Runtime- und Release-Gates. Diese Roadmap darf keine Live-Smokes, Provider-Aufrufe, Deploys, Pushes oder neue UI-Neugestaltung erzwingen.
- Naechster sicherer Slice: `CTXE2-deferred-tool-schema-selection`.
- Owner-Vorschlag: Charlie koordiniert, Bob implementiert kleine Backend-/Testmodelle, Alice dokumentiert Operator-Sprache und Go/Partial/No-Go-Wording.

## Goal

Odysseus nutzt vor `1.0.0` weniger unnoetigen Prompt-Kontext, laedt Tool-Details gezielter, haelt Sessions cache-stabiler und routet einfache Modellentscheidungen ohne stille Qualitaets- oder Sicherheitsverluste.

## Current Evidence

- `docs/plans/dynamic-tool-loading-contract.md` definiert bereits Tool-/Skill-Sichtbarkeit, progressive disclosure und Schema Thinning.
- `docs/plans/tool-result-truth-contract.md` definiert Evidence- und Truth-Sprache fuer Tool- und Agent-Ergebnisse.
- `docs/plans/fallback-routing-contract.md` definiert budgetierte Fallback- und Review-Entscheidungen fuer kleine Modelle.
- `docs/plans/small-model-evaluation-gates-contract.md` definiert task-spezifische Gates fuer kleine Modellpfade.
- `docs/plans/mvp-master-roadmap.md` begrenzt die Vor-1.0-Arbeit auf Backend-Logik, Runtime-Gates und ehrliche Evidence. Neue UI ist explizit kein Treiber.

## Non-Goals Before 1.0

- Kein trainierter HyDRA-artiger Router.
- Kein provideruebergreifendes Live-Benchmarking.
- Kein neuer Kosten-/Billing-Stack.
- Kein automatischer Modellwechsel mitten in einer laufenden Aufgabe.
- Keine breite UI-Neugestaltung.
- Keine neuen Live-MCP-, Telegram-, Nextcloud-, Provider-, Deploy- oder Backup-Aktionen.
- Keine Speicherung von Secrets, Tokens, Chat-IDs, privaten Pfaden, privaten Inhalten oder Raw-Provider-Ausgaben in Roadmaps, Tests, Prompts oder Handoffs.

## Stop Rules

Stoppe oder erstelle ein Gate, wenn:

- ein Slice Live-Netzwerk, Provider, Telegram, Nextcloud, Host-Agent, Deploy, Backup, Restore oder Write-Smoke braucht;
- ein Slice echte Secrets, Tokens, Chat-IDs, private Inhalte oder Raw-Provider-Ausgaben beruehren wuerde;
- ein Modellwechsel in einer bestehenden Session ohne explizite Nutzerentscheidung noetig waere;
- bestehende 1.0-/MVP-Hotfiles mit fremden Aenderungen editiert werden muessten;
- ein Test rot wird und der Fix nicht klar im Slice-Scope liegt;
- eine neue UI-Entscheidung noetig ist.

## Slice Queue

| Slice | Class | Owner | Ziel | Allowed Paths | Tests | Gate |
| --- | --- | --- | --- | --- | --- | --- |
| `CTXE0-tool-inventory-and-budget-baseline` | `repo_only` | Charlie/Bob | Inventar der aktuellen agent-callable Tools, MCP-Tools, Plugin-Tools und Context-Provider plus grobe Prompt-Budget-Schaetzung erstellen. | `src/`, `routes/`, `plugins/`, `mcp_servers/`, `tests/`, `docs/plans/` | Focused static/unit tests if model added; otherwise docs-only. | none |
| `CTXE1-tool-manifest-model` | `repo_only` | Bob | Kleines Backend-Modell fuer Tool-Manifeste bauen: id, family, short description, capabilities, risk class, schema ref, visibility state. | `src/tool_catalog.py`, `tests/test_tool_catalog.py` | done: `9 passed, 1 warning` | none |
| `CTXE2-deferred-tool-schema-selection` | `repo_only` | Bob | Auswahlfunktion bauen, die fuer eine Capsule/Session zuerst kompakte Tool-Manifeste liefert und volle Schemata nur fuer relevante Tools markiert. | `src/`, `tests/` only within tool selection scope | focused pytest | none |
| `CTXE3-session-envelope-hash` | `repo_only` | Bob | Session-Envelope modellieren: model ref, reasoning/context budget, active tool manifest set, system prompt version, MCP/plugin selection, cache boundary marker. | `src/`, `core/`, `tests/` narrow session/envelope files | focused pytest | none |
| `CTXE4-cache-boundary-policy` | `repo_only` | Charlie/Bob | Policy definieren und testen: Modell-/Toolset-/Reasoning-/Context-Budget-Wechsel nur am Session-Start, nach Compaction oder nach explizitem Operator-Go. | `src/`, `core/`, `routes/`, `tests/` narrow session policy files | focused pytest | needs_design only if user-facing wording unclear |
| `CTXE5-context-provider-manifest-first` | `repo_only` | Bob | Context-Provider auf manifest-first vorbereiten: erst Diagnostik/Refs/Snippet-Budget, dann gezielte Snippets. | `src/memory_provider.py`, `src/nextcloud_source_provider.py`, plugin context providers, tests | focused provider tests | no live source access |
| `CTXE6-simple-task-router-policy` | `safe_offline` | Alice/Charlie | Vor-1.0-Routing-Sprache festlegen: simple summarization/classification/focused edit vs. deep reasoning/multi-file/debug/tool orchestration. | `docs/plans/`, optional `src/*routing*.py` if model-only | docs-only or focused model tests | no live model calls |
| `CTXE7-truth-and-telemetry-evidence` | `repo_only` | Bob/Charlie | Tool selection, cache-boundary decisions and routing decisions als Truth/Evidence records modellieren, ohne Raw Logs oder private Inhalte. | `src/`, `tests/`, `docs/plans/` narrow evidence files | focused pytest | none |
| `CTXE8-master-roadmap-closeout` | `repo_only` | Charlie | Fortschritt in MVP-/Release-Status einsortieren: Go/Partial/Deferred, keine 1.0-Ueberzeichnung. | `docs/plans/` only | docs-only | may defer if MVP hotfile dirty |

## Gate Queue

Gate: `CTXE-G1-live-provider-routing`

Class: `needs_live_go`

Blocks: any real provider/model-health routing.

Decision needed: Explicit Go for live provider/model calls, including which providers, redaction rules and budget.

Safe preparation done: Offline routing policy and deterministic tests can proceed without this.

Risk if bypassed: Cost drift, private prompt exposure, cache churn and misleading release evidence.

Next safe slice: `CTXE6-simple-task-router-policy`.

Gate: `CTXE-G2-ui-surface`

Class: `needs_design`

Blocks: any visible settings/dashboard for tool manifests, cache boundaries or router explanations.

Decision needed: Whether this belongs in current UI, future Lens UI or only docs/API before `1.0.0`.

Safe preparation done: Backend models and docs can proceed.

Risk if bypassed: UI churn before the agreed redesign and confusing operator controls.

Next safe slice: `CTXE1-tool-manifest-model`.

Gate: `CTXE-G3-mvp-hotfile-update`

Class: `blocked`

Blocks: editing `docs/plans/mvp-master-roadmap.md` or other dirty MVP hotfiles if unrelated user/agent edits are active.

Decision needed: Confirm whether to append this track to the MVP master file or keep it as a linked side roadmap.

Safe preparation done: This standalone roadmap is safe to read and reference.

Risk if bypassed: Overwriting or mixing unrelated active MVP work.

Next safe slice: `CTXE0-tool-inventory-and-budget-baseline`.

## Paths

### Path A: Tool Context Compression

Target slices: `CTXE0`, `CTXE1`, `CTXE2`, `CTXE5`.

Done when:

- tool inventory is explicit;
- compact tool manifests exist;
- full tool schemas are not treated as always-on prompt material;
- context providers can expose bounded manifests before snippets.

### Path B: Cache-Stable Session Envelope

Target slices: `CTXE3`, `CTXE4`.

Done when:

- sessions can describe their stable execution envelope;
- envelope changes identify a cache boundary;
- mid-session model/tool/context changes are blocked, deferred or explicitly operator-approved.

### Path C: Simple Model Routing Without Overreach

Target slices: `CTXE6`, `CTXE7`.

Done when:

- routing is deterministic policy language, not a trained router claim;
- easy work can be marked eligible for smaller/faster models;
- deep/multi-file/debug/tool-orchestration work remains eligible for stronger reasoning;
- decisions produce bounded Evidence records.

### Path D: Master Closeout

Target slice: `CTXE8`.

Done when:

- Master Chat has accepted Go/Partial/Deferred status;
- MVP/Release status references this roadmap without claiming live provider routing;
- remaining live/design gates are explicit.

## Verification

Minimum verification before claiming Go:

- Focused tests for any new model or policy module.
- Static review that no secrets, tokens, private paths or raw provider output are persisted.
- Static review that no live provider/network/Telegram/Nextcloud/host/deploy action is required.
- Roadmap closeout with Go/Partial/Deferred language.

Docs-only slices require no pytest, but must state `docs-only/no tests`.

## Progress Evidence

### CTXE1 Tool Manifest Model

Status: done 2026-07-03.

Implemented:

- `ToolManifest` in `src/tool_catalog.py` with `tool_id`, `family`,
  `short_description`, `capabilities`, `risk_level`, `schema_ref`,
  `visibility_state` and compact prompt budget estimate.
- Function-schema adapter that emits a manifest with `schema_ref` such as
  `function:write_file` instead of embedding full parameter schemas.
- Deterministic manifest builder with duplicate suppression.
- Conservative family, capability and risk inference for built-in and MCP-like
  tools.
- Redacted audit summary flags: no raw schema, raw content or token value.

Evidence:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_tool_catalog.py -q
```

Result: `9 passed, 1 warning`.

Next safe slice: `CTXE2-deferred-tool-schema-selection`.

## Go Language

- `Go`: Repo-only models, policies and focused tests are complete; no live provider routing or UI redesign is implied.
- `Partial`: Core documentation and at least one backend path are complete, but schema selection, session envelope, provider manifest-first or evidence records remain open.
- `Deferred`: Live provider routing, UI controls, or MVP hotfile integration are intentionally postponed.
- `No-Go`: The implementation would require live calls, secrets, private source content, destructive git, or a hidden model/tool change.
- `Blocked`: Existing hotfile conflicts or missing operator decisions prevent safe continuation.

## First Delegation Prompts

### Alice

```xml
<codex_delegation>
  <source_thread_id>pre-1.0-context-efficiency-roadmap</source_thread_id>
  <input>Alice-Slice: CTXE6-simple-task-router-policy

Arbeite im Odysseus-Fork an einem kleinen, sicheren Slice.

Execution mode: explorer
Slice class: safe_offline
Reason: Routing-Sprache kann ohne Live-Modellaufrufe und ohne Codeaenderung vorbereitet werden.

Ziel:
- Formuliere die Operator-Sprache fuer einfache Modellrouting-Entscheidungen vor 1.0.
- Trenne schnelle Aufgaben, fokussierte Edits, Debugging, Multi-Datei-Aenderungen und Tool-Orchestrierung.

Erlaubte Dateien:
- docs/plans/pre-1.0-context-efficiency-roadmap.md
- docs/plans/fallback-routing-contract.md
- docs/plans/small-model-evaluation-gates-contract.md
- docs/plans/tool-result-truth-contract.md

Nicht anfassen:
- Keine Codeaenderungen.
- Keine Live-Modellaufrufe.
- Keine UI-Dateien.
- Keine fremden Aenderungen revertieren; andere Agenten koennen parallel arbeiten.

Anforderungen:
- Liefere Go/Partial/Deferred/No-Go-Sprache.
- Benenne, was vor 1.0 erlaubt ist und was post-1.0 bleibt.

Tests:
- Keine. Docs-only Slice.

Stop-Regeln:
- Scope wird verlassen.
- Live-Go, Design-Go oder Operator-Go waere noetig.

Wenn fertig:
- Status melden: done | blocked | deferred | failed.
- Geaenderte Dateien nennen.
- Tests und Ergebnis nennen.
- Offene Risiken und Handoff nennen.
  </input>
</codex_delegation>
```

### Bob

```xml
<codex_delegation>
  <source_thread_id>pre-1.0-context-efficiency-roadmap</source_thread_id>
  <input>Bob-Slice: CTXE0-tool-inventory-and-budget-baseline

Arbeite im Odysseus-Fork an einem kleinen, sicheren Slice.

Execution mode: explorer
Slice class: repo_only
Reason: Der Slice liest Repo-Strukturen und erstellt eine Baseline, ohne Live-Aktionen.

Ziel:
- Inventar der agent-callable Tools, MCP-Tools, Plugin-Tools und Context-Provider erstellen.
- Grob markieren, wo volle Schemas oder breite Context-Provider Prompt-Bloat erzeugen koennen.

Erlaubte Dateien:
- src/
- routes/
- plugins/
- mcp_servers/
- docs/plans/pre-1.0-context-efficiency-roadmap.md
- docs/plans/dynamic-tool-loading-contract.md

Nicht anfassen:
- Keine Codeaenderungen.
- Keine Live-MCP-/Provider-/Telegram-/Nextcloud-Aufrufe.
- Keine Secrets oder private Inhalte ausgeben.
- Keine fremden Aenderungen revertieren; andere Agenten koennen parallel arbeiten.

Anforderungen:
- Liefere ein kurzes Inventar mit empfohlenen ersten Manifest-Familien.
- Benenne Hotspots und Tests, die fuer CTXE1/CTXE2 sinnvoll waeren.

Tests:
- Keine. Read-only Explorer-Slice.

Stop-Regeln:
- Scope wird verlassen.
- Ein Ergebnis wuerde private Inhalte, Tokens oder Raw Provider Output enthalten.

Wenn fertig:
- Status melden: done | blocked | deferred | failed.
- Geaenderte Dateien nennen.
- Tests und Ergebnis nennen.
- Offene Risiken und Handoff nennen.
  </input>
</codex_delegation>
```

### Charlie

```xml
<codex_delegation>
  <source_thread_id>pre-1.0-context-efficiency-roadmap</source_thread_id>
  <input>Charlie-Slice: CTXE8-master-roadmap-closeout

Arbeite im Odysseus-Fork an einem kleinen, sicheren Slice.

Execution mode: worker
Slice class: repo_only
Reason: Closeout ist Repo-/Doku-Arbeit und darf keine Live-Aktion implizieren.

Ziel:
- Integriere den Status dieser Roadmap in den Master-Kontext, ohne aktive MVP-Hotfiles zu ueberschreiben.
- Erstelle Go/Partial/Deferred-Handoff fuer Master Chat.

Erlaubte Dateien:
- docs/plans/pre-1.0-context-efficiency-roadmap.md
- docs/plans/unified-odysseus-roadmap.md nur wenn Worktree sauber oder explizit freigegeben
- docs/plans/mvp-master-roadmap.md nur wenn Worktree sauber oder explizit freigegeben

Nicht anfassen:
- Keine Runtime-/Code-Dateien.
- Keine Live-Aktionen.
- Keine fremden Aenderungen revertieren; andere Agenten koennen parallel arbeiten.

Anforderungen:
- Vor Edit `git status --short --branch` pruefen.
- Bei Hotfile-Konflikt Gate `CTXE-G3-mvp-hotfile-update` aktualisieren statt zu editieren.
- Keine 1.0-Go-Behauptung aus dieser Roadmap allein ableiten.

Tests:
- Keine. Docs-only Slice.

Stop-Regeln:
- Hotfile-Konflikt oder fremde staged files.
- Scope wird verlassen.
- Live-Go, Design-Go oder Operator-Go waere noetig.

Wenn fertig:
- Status melden: done | blocked | deferred | failed.
- Geaenderte Dateien nennen.
- Tests und Ergebnis nennen.
- Offene Risiken und Handoff nennen.
  </input>
</codex_delegation>
```
