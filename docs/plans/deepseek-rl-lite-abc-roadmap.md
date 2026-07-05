# DeepSeek RL-lite ABC Roadmap

Stand: 2026-07-05

Status: repo-seitige RL-lite Foundation abgeschlossen; Live-Provider, echte
Preference-Exports, Active-Production und Training bleiben Operator-Gates.

Update 2026-07-05:

- `RL-1` bis `RL-3` sind umgesetzt: Reward Contracts, redacted Episode Store
  und deterministischer Scorer.
- `RL-4` ist im Obsidian Memory-Router angebunden: Antwortpfade schreiben
  redacted Episoden, ohne Query, Snippets oder Antworttext zu persistieren.
- `RL-5` und `RL-6` sind umgesetzt: read-only Policy-Recommender und
  `model_rl_policy_mode=shadow`, ohne produktive Kandidatenordnung zu aendern.
- `RL-7` und `RL-8` sind umgesetzt: Offline Evaluation Runner und
  Activation-Gate fuer `active`.
- `RL-9` ist als redacted Preference-Export-Prep umgesetzt; echte Exporte
  bleiben Gate `RL-G3`.
- `RL-10` bleibt bewusst `needs_live_go`: kein Live-DeepSeek-Call, kein
  Training, keine produktive Aktivierung in diesem Abschluss.

## Goal

Odysseus lernt aus redacted Modell-Episoden, welche Modelle, Antwortmodi,
Prompt-Varianten und Retrieval-Budgets fuer DeepSeek-/kompatible Modellpfade
am besten funktionieren, ohne private Prompts, private Inhalte oder Provider-
Ausgaben zu persistieren.

Das Ziel ist zuerst `RL-lite`: Reward-Erfassung, Offline-Auswertung und
Policy-Routing. Echtes Fine-Tuning, DPO oder GRPO ist ein spaeteres Live-/GPU-
Gate und kein Bestandteil des ersten sicheren Implementierungspfads.

## Current Evidence

- DeepSeek-/Model-Routing existiert fuer Memory-Antworten:
  `plugins/obsidian/backend/model_router.py`.
- Der Obsidian Query Layer fuehrt Modellantworten und extractive Fallbacks
  zusammen: `plugins/obsidian/backend/query_layer.py`.
- Redacted Modellaktivitaet wird bereits ohne Raw-Prompts geloggt:
  `src/ai_activity_ledger.py`.
- Maintenance-Benchmarks liefern bereits deterministische Scores:
  `src/gemma_memory_benchmark.py` und `src/gemma_maintenance_comparison.py`.
- Kleine Modell-Evaluation-Gates existieren als Review-/Go-No-Go-Vertrag:
  `src/small_model_evaluation_gates.py`.
- Modellkooperation und Privacy-Gates sind dokumentiert:
  `docs/plans/model-cooperation-routing-contract.md`.
- Neu: `src/model_reward_contract.py`, `src/model_episode_store.py`,
  `src/model_reward_scorer.py`, `src/model_routing_policy.py`,
  `src/model_policy_evaluation.py`, `src/model_policy_activation_gate.py` und
  `src/model_preference_export.py` bilden die redacted RL-lite Foundation.
- Neu: `plugins/obsidian/backend/model_router.py` zeichnet Memory-Antworten als
  redacted Episoden auf und kann Shadow-Policy-Diagnostics liefern, ohne die
  produktive Kandidatenordnung zu veraendern.

## Mode

Standard ABC.

Begruendung: Der Nutzer ist praesent und will eine Implementierungsroadmap. Die
ersten Slices sind safe offline oder repo-only. Live-Provider, echtes Training,
GPU-Jobs, DPO/GRPO und produktive Policy-Aktivierung bleiben Gate-Items.

## Non-Goals

- Kein Training von DeepSeek V4 API-Gewichten.
- Keine Speicherung von Raw-Prompts, Raw-Outputs, privaten Dokumentinhalten,
  Chat-IDs, Tokens, API-Keys oder privaten Pfaden.
- Keine Live-Provider-Calls in Tests.
- Keine UI-Neugestaltung.
- Keine automatische Modellinstallation.
- Keine autonomen Memory-/Graph-Truth-Writes aus Modelloutput.

## Stop Rules

- Arbeit stoppen, wenn ein Slice private Inhalte oder Provider-Raw-Outputs
  persistieren muesste.
- Arbeit stoppen, wenn ein Live-Go fuer DeepSeek, Ollama, GPU, Telegram,
  Nextcloud, Host, Deploy oder Training benoetigt wird.
- Arbeit stoppen, wenn fremde staged files oder Hotfile-Konflikte im Scope
  auftauchen.
- Arbeit stoppen, wenn Tests rote Sicherheits-/Redaction-Signale zeigen und der
  Fix nicht eng im Slice-Scope bleibt.
- Keine destruktiven Git-Kommandos, kein Reset, kein Force-Push.

## Architecture Fit

Der neue Layer liegt in `src/` und bleibt provider-neutral:

```text
query route
  -> plugins/obsidian/backend/query_layer.py
  -> plugins/obsidian/backend/model_router.py
  -> src/llm_core.py
  -> src/ai_activity_ledger.py
  -> src/model_episode_store.py
  -> src/model_reward_scorer.py
  -> src/model_routing_policy.py
```

Der Router ruft die Policy vor der Kandidatenordnung ab. Nach einer Antwort
wird eine redacted Episode mit Outcome und Reward geschrieben. Training oder
Preference-Datensatz-Export liest spaeter nur diese redacted Episoden.

## Slice Queue

### RL-0: Contract And Threat Boundary

Owner: Alice

Class: `repo_only`

Mode: worker

Goal: Produkt- und Sicherheitsvertrag fuer RL-lite festlegen.

Allowed paths:

- `docs/plans/deepseek-rl-lite-abc-roadmap.md`
- `docs/plans/model-cooperation-routing-contract.md`
- optional `THREAT_MODEL.md`, nur falls ein neuer expliziter untrusted surface
  ergaenzt werden muss

Requirements:

- Definiere erlaubte Episode-Felder.
- Definiere verbotene Persistenzfelder.
- Definiere Go/Partial/No-Go fuer RL-lite, DPO und GRPO.
- Klaere, dass API-DeepSeek nicht direkt per RL trainiert wird.

Tests: Keine. Docs-only Slice.

Gate requirements: keine.

Status 2026-07-05: done in dieser Roadmap. Der Contract trennt RL-lite,
DPO/GRPO, echte Preference-Exports, Live-Provider-Proofs und Training als
separate Gates; Raw-Prompts, Raw-Outputs und private Inhalte bleiben verboten.

### RL-1: Reward Contract Models

Owner: Bob

Class: `safe_offline`

Mode: worker

Goal: Provider-neutrale Dataclasses und Validatoren fuer Modell-State, Action,
Outcome und Reward bauen.

Allowed paths:

- `src/model_reward_contract.py`
- `tests/test_model_reward_contract.py`

Requirements:

- `ModelEpisodeState`: surface, task_type, sensitivity flags, retrieval summary,
  context budget, owner-safe labels.
- `ModelEpisodeAction`: answer_mode, provider, model, endpoint hash or safe id,
  prompt_template_id, retrieval_depth, max_tokens.
- `ModelEpisodeOutcome`: status, duration_ms, citation_count, fallback_reason,
  warning tokens, confidence, verifier/gate refs.
- `ModelReward`: total score, component scores, status, reason codes.
- Reject secret markers and long/raw text.
- Audit summary must omit prompts and outputs.

Tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_model_reward_contract.py
```

Gate requirements: keine.

Status 2026-07-05: done.

Evidence: `src/model_reward_contract.py`,
`tests/test_model_reward_contract.py`.

### RL-2: Redacted Episode Store

Owner: Bob

Class: `safe_offline`

Mode: worker

Goal: JSONL-Store fuer redacted Modell-Episoden analog zum bestehenden
AI-Activity-Ledger bauen.

Allowed paths:

- `src/model_episode_store.py`
- `tests/test_model_episode_store.py`

Requirements:

- Date-partitionierter Store unter `DATA_DIR/model_episodes`.
- Append/read APIs mit owner, surface, task_type, model und status filters.
- Maximal kompakte Diagnose-Summary.
- Safety checks gegen secret markers, private paths, raw prompt/output keys.
- Keine Migration und keine DB-Abhaengigkeit im ersten Slice.

Tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_model_episode_store.py
```

Gate requirements: keine.

Status 2026-07-05: done.

Evidence: `src/model_episode_store.py`, `tests/test_model_episode_store.py`.

### RL-3: Deterministic Reward Scorer

Owner: Bob

Class: `safe_offline`

Mode: worker

Goal: Erste deterministische Reward-Funktion fuer Odysseus-Tasks bauen.

Allowed paths:

- `src/model_reward_scorer.py`
- `tests/test_model_reward_scorer.py`
- optional `src/gemma_memory_benchmark.py`, nur fuer Extraktion wiederverwendbarer
  Score-Helfer
- optional `tests/test_gemma_memory_benchmark.py`, nur bei noetiger Anpassung

Requirements:

- Komponenten: schema/json, citation/evidence, privacy/local-only,
  retrieval/confidence, fallback health, latency, cost proxy, user feedback.
- Scorebereich stabil, z. B. `-100..100` oder `0..100`; Entscheidung im Slice
  dokumentieren.
- Negative Rewards fuer fehlende Citations bei citation-required tasks.
- Negative Rewards fuer Cloud-Nutzung bei `local_only_required=true`.
- Kein LLM-Judge als Pflicht; LLM-Judge spaeter nur als Gate.

Tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_model_reward_scorer.py
```

Gate requirements: keine.

Status 2026-07-05: done.

Evidence: `src/model_reward_scorer.py`, `tests/test_model_reward_scorer.py`.

### RL-4: Obsidian Memory Episode Adapter

Owner: Bob

Class: `repo_only`

Mode: worker

Goal: Memory Query Outcomes nach `answer_query_async` oder `synthesize_answer`
als redacted Episoden erfassen.

Allowed paths:

- `plugins/obsidian/backend/model_router.py`
- `plugins/obsidian/backend/query_layer.py`
- `plugins/obsidian/tests/test_model_router_backend.py`
- `plugins/obsidian/tests/test_query_layer_backend.py`
- `src/model_reward_contract.py`
- `src/model_episode_store.py`
- `src/model_reward_scorer.py`

Requirements:

- Episode-Erfassung darf Antwortpfad nie brechen; Fehler werden sicher
  geschluckt oder als safe warning geloggt.
- Speichere nur summary fields: answer_mode, selected_model, provider,
  fallback_reason, warnings, citation_count, confidence, duration if available.
- Keine Citations-Texte, keine retrieved snippets, keine query strings.
- Owner-/surface-Felder bleiben safe labels.

Tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest plugins\obsidian\tests\test_model_router_backend.py plugins\obsidian\tests\test_query_layer_backend.py
```

Gate requirements: keine.

Status 2026-07-05: done.

Evidence: `plugins/obsidian/backend/model_router.py`,
`plugins/obsidian/tests/test_model_router_backend.py`,
`plugins/obsidian/tests/test_query_layer_backend.py`.

### RL-5: Offline Policy Recommender

Owner: Bob

Class: `safe_offline`

Mode: worker

Goal: Read-only Policy-Engine bauen, die aus Episoden eine Kandidatenordnung
empfiehlt, aber noch nicht produktiv steuert.

Allowed paths:

- `src/model_routing_policy.py`
- `tests/test_model_routing_policy.py`

Requirements:

- Start mit konservativer UCB- oder Thompson-Sampling-Variante, alternativ
  simple weighted moving average.
- Cold-start muss bestehende Router-Ordnung respektieren.
- Policy darf nur vorhandene Kandidaten umsortieren, keine neuen Provider
  erfinden.
- Privacy/safety hard gates schlagen Reward-Historie.
- Expliziter `explain_policy_decision` audit summary.

Tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_model_routing_policy.py
```

Gate requirements: keine.

Status 2026-07-05: done.

Evidence: `src/model_routing_policy.py`, `tests/test_model_routing_policy.py`.

### RL-6: Router Integration In Shadow Mode

Owner: Bob

Class: `repo_only`

Mode: worker

Goal: Policy-Recommendations im Model Router berechnen und als Diagnose
ausgeben, ohne produktive Kandidatenordnung zu veraendern.

Allowed paths:

- `plugins/obsidian/backend/model_router.py`
- `plugins/obsidian/tests/test_model_router_backend.py`
- `src/model_routing_policy.py`
- `tests/test_model_routing_policy.py`

Requirements:

- Neues Setting, z. B. `model_rl_policy_mode`, Default `off`.
- Modi: `off`, `shadow`, spaeter `active`.
- In `shadow` wird die Empfehlung geloggt/diagnostiziert, aber nicht genutzt.
- Tests beweisen, dass `off` und `shadow` bestehendes Verhalten nicht brechen.

Tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest plugins\obsidian\tests\test_model_router_backend.py tests\test_model_routing_policy.py
```

Gate requirements: keine.

Status 2026-07-05: done.

Evidence: `plugins/obsidian/backend/model_router.py`,
`plugins/obsidian/tests/test_model_router_backend.py`.

### RL-7: Redacted Evaluation Runner

Owner: Charlie

Class: `repo_only`

Mode: worker

Goal: CLI/Dry-run-Runner fuer DeepSeek/Gemma/local/extractive Vergleiche auf
synthetischen Faellen zusammenfuehren.

Allowed paths:

- `src/model_policy_evaluation.py`
- `scripts/model_policy_evaluation.py`
- `tests/test_model_policy_evaluation.py`
- optional `src/gemma_maintenance_comparison.py`
- optional `scripts/gemma_maintenance_comparison.py`

Requirements:

- Default offline deterministic.
- Live flags muessen explizit sein und sind nicht Teil der automatischen Tests.
- Reports enthalten nur aggregate metrics, hashes, reason codes und model labels.
- Kompatibel mit bestehenden Maintenance-Benchmark-Faellen.

Tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_model_policy_evaluation.py tests\test_gemma_maintenance_comparison.py
```

Gate requirements: Live-Vergleich bleibt `needs_live_go`.

Status 2026-07-05: done fuer offline deterministic evaluation.

Evidence: `src/model_policy_evaluation.py`,
`scripts/model_policy_evaluation.py`, `tests/test_model_policy_evaluation.py`.

### RL-8: Active Policy Gate

Owner: Charlie

Class: `repo_only`

Mode: worker

Goal: Aktivierungs-Gate bauen, das `model_rl_policy_mode=active` nur erlaubt,
wenn Safety-, Regression- und Offline-Evidence gruen sind.

Allowed paths:

- `src/model_policy_activation_gate.py`
- `tests/test_model_policy_activation_gate.py`
- optional `src/settings.py`
- optional `tests/test_settings_store_shape.py`

Requirements:

- Active mode braucht Mindestanzahl Episoden pro task/model bucket.
- Active mode braucht keine privacy violations, keine local-only violations,
  und ausreichende Offline-Passrate.
- Gate gibt `go`, `needs_review`, `fallback_required` oder `blocked` zurueck.
- Gate-Output ist kurz und redacted.

Tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_model_policy_activation_gate.py
```

Gate requirements: keine fuer Gate-Bau; Aktivierung in Produktion bleibt
Operator-Go.

Status 2026-07-05: done.

Evidence: `src/model_policy_activation_gate.py`,
`tests/test_model_policy_activation_gate.py`.

### RL-9: Preference Dataset Export Prep

Owner: Alice

Class: `repo_only`

Mode: worker

Goal: Exportvertrag fuer spaetere DPO/GRPO-Vorbereitung dokumentieren und
trocken validieren.

Allowed paths:

- `docs/plans/deepseek-rl-lite-abc-roadmap.md`
- `docs/plans/model-cooperation-routing-contract.md`
- optional `src/model_preference_export.py`
- optional `tests/test_model_preference_export.py`

Requirements:

- Nur redacted preference pairs oder synthetic cases.
- Kein Export von Raw-Prompts/Outputs ohne separate explizite Datenschutz- und
  Operator-Freigabe.
- DPO nur fuer preference pairs.
- GRPO nur fuer verifizierbare Aufgaben mit automatischem Reward.
- Dr. GRPO/Length-bias-Risiko als technisches Gate festhalten.

Tests:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_model_preference_export.py
```

Falls nur Docs: Keine Tests.

Status 2026-07-05: done.

Evidence: `src/model_preference_export.py`,
`tests/test_model_preference_export.py`.

### RL-10: Live Provider And Training Gates

Owner: Charlie

Class: `needs_live_go`

Mode: explorer

Goal: Bounded Live-Gates fuer DeepSeek-V4-Providervergleich, lokale Modelle und
spaeteres Training vorbereiten.

Allowed paths:

- `docs/plans/deepseek-rl-lite-abc-roadmap.md`
- `docs/plans/provider-proof-operator-runbook.md`
- optional `docs/plans/live-provider-proof-run-contract.md`

Requirements:

- Konkrete Go-Fragen fuer Live-DeepSeek, Live-Ollama, GPU-Training,
  Preference-Export und Kostenbudget.
- Kein Live-Call ohne explizites Go.
- Kein Training ohne Datensatz-, Hardware-, Kosten- und Privacy-Gate.

Tests: Keine. Gate-only/docs slice.

Gate requirements: siehe Gate Queue.

Status 2026-07-05: deferred / needs_live_go. Safe preparation is complete;
no live provider call, productive active-policy switch, real preference export
or training run was performed.

## Gate Queue

Gate: `RL-G1-live-deepseek-provider-proof`

Class: `needs_live_go`

Blocks: Live-Vergleich von DeepSeek V4 Flash/Pro gegen lokale Modelle.

Decision needed: Darf ein bounded Live-Test gegen den konfigurierten DeepSeek-
Endpunkt laufen, mit synthetischen oder redacted Testfaellen und Kostenlimit?

Safe preparation done: Offline Runner und redacted Report koennen vorher gebaut
werden.

Risk if bypassed: Kosten, Providerdatenabfluss, Secret-/Endpoint-Fehler.

Next safe slice: `RL-1`.

Gate: `RL-G2-active-policy-production`

Class: `needs_live_go`

Blocks: `model_rl_policy_mode=active` in produktiver Nutzung.

Decision needed: Darf die RL-lite-Policy die Kandidatenordnung tatsaechlich
veraendern, statt nur Shadow Diagnostics zu schreiben?

Safe preparation done: Shadow Mode, Offline Evidence und Activation Gate.

Risk if bypassed: Schlechtere Modellwahl, Datenschutzverletzung durch falsche
Cloud-Eskalation, unerwartete Kosten.

Next safe slice: `RL-6`.

Gate: `RL-G3-preference-export`

Class: `needs_live_go`

Blocks: Export von echten Preference-Pairs fuer DPO/GRPO.

Decision needed: Duerfen echte Nutzerfeedbackdaten exportiert werden, oder nur
synthetische/redacted Episoden?

Safe preparation done: Exportvertrag und Validatoren.

Risk if bypassed: Persistenz privater Inhalte oder nicht erlaubter Outputs.

Next safe slice: `RL-9`.

Gate: `RL-G4-training-run`

Class: `needs_live_go`

Blocks: DPO/GRPO/LoRA/GPU-Training.

Decision needed: Welches Zielmodell, welche Hardware, welches Budget, welcher
Datensatz und welche Privacy-Grenzen gelten?

Safe preparation done: Reward-Scorer, Evaluation Runner und redacted exports.

Risk if bypassed: Kosten, instabile Modelle, private Daten im Trainingssatz,
Reward hacking oder GRPO length bias.

Next safe slice: none.

## Paths

### Path A: Contract And Operator Safety

Owner: Alice

Slices: `RL-0`, `RL-9`, `RL-10`

Completion criteria:

- Produktvertrag beschreibt RL-lite vs DPO/GRPO klar.
- Gate Queue ist fuer Live/Training konkret.
- Keine falsche Zusage, dass API-DeepSeek trainiert wird.

### Path B: Backend Reward Infrastructure

Owner: Bob

Slices: `RL-1`, `RL-2`, `RL-3`, `RL-4`

Completion criteria:

- Reward contracts, store and scorer exist.
- Obsidian memory answers write redacted episodes.
- Focused tests prove no raw prompts/outputs are stored.

### Path C: Policy, Evaluation, Activation

Owner: Charlie

Slices: `RL-5`, `RL-6`, `RL-7`, `RL-8`

Completion criteria:

- Policy recommender exists.
- Shadow mode does not change production routing.
- Offline evaluation runner produces redacted reports.
- Active policy gate blocks unsafe activation.

## Verification

Focused checks by slice:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_model_reward_contract.py
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_model_episode_store.py
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_model_reward_scorer.py
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest plugins\obsidian\tests\test_model_router_backend.py plugins\obsidian\tests\test_query_layer_backend.py
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_model_routing_policy.py
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_model_policy_evaluation.py
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_model_policy_activation_gate.py
```

Final focused suite after all repo-only slices:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_ai_activity_ledger.py tests\test_gemma_memory_benchmark.py tests\test_gemma_maintenance_comparison.py tests\test_small_model_evaluation_gates.py plugins\obsidian\tests\test_model_router_backend.py plugins\obsidian\tests\test_query_layer_backend.py
```

## Go Language

`Go`: Focused tests pass, redaction checks pass, no raw prompts/outputs are
persisted, active behavior is either off or explicitly gated.

`Partial`: Contracts and offline pieces are built, but router integration,
shadow mode or evaluation runner is still missing.

`No-Go`: Any slice stores raw private content, exposes secrets, changes routing
without a gate, or fails local-only/privacy tests.

`Deferred`: Live provider proof, real preference export, active production
policy or training waits for explicit operator Go.

`Blocked`: Required evidence cannot be produced safely, or worktree conflicts
make scope ownership unclear.

## Delegation Prompts

### Alice Prompt

```xml
<codex_delegation>
  <source_thread_id>current</source_thread_id>
  <input>Alice-Slice: RL-0

Arbeite im Odysseus-Fork an einem kleinen, sicheren Slice.

Execution mode: worker
Slice class: repo_only
Reason: Contract/docs work only; no live provider, no training, no private data.

Ziel:
- Finalisiere den RL-lite Produkt- und Sicherheitsvertrag fuer DeepSeek/
  kompatible Modellpfade.

Erlaubte Dateien:
- docs/plans/deepseek-rl-lite-abc-roadmap.md
- docs/plans/model-cooperation-routing-contract.md
- THREAT_MODEL.md nur falls ein expliziter untrusted surface ergaenzt werden
  muss.

Nicht anfassen:
- Backend-Code, Tests, UI, Settings.
- Keine fremden Aenderungen revertieren; andere Agenten koennen parallel
  arbeiten.

Anforderungen:
- Klaere RL-lite vs DPO vs GRPO.
- Definiere verbotene Persistenzfelder.
- Definiere Operator-Go-Sprache fuer Live/Training.
- Keine Secrets, privaten Inhalte, Chat-IDs oder Provider-Raw-Outputs in Docs.

Tests:
- Keine. Docs-only Slice.

Stop-Regeln:
- Hotfile-Konflikt oder fremde staged files.
- Secrets/Token/Chat-IDs/private Inhalte sollen persistiert oder geloggt
  werden.
- Scope wird verlassen.
- Live-Go, Design-Go oder Operator-Go waere noetig.
- Destruktive Git-Kommandos waeren noetig.

Wenn fertig:
- Status melden: done | blocked | deferred | failed.
- Geaenderte Dateien nennen.
- Tests und Ergebnis nennen.
- Offene Risiken und Handoff nennen.
  </input>
</codex_delegation>
```

### Bob Prompt

```xml
<codex_delegation>
  <source_thread_id>current</source_thread_id>
  <input>Bob-Slice: RL-1

Arbeite im Odysseus-Fork an einem kleinen, sicheren Slice.

Execution mode: worker
Slice class: safe_offline
Reason: Pure Python contracts and tests; no provider, no live data, no network.

Ziel:
- Baue provider-neutrale Reward-Contract-Modelle fuer State, Action, Outcome
  und Reward.

Erlaubte Dateien:
- src/model_reward_contract.py
- tests/test_model_reward_contract.py

Nicht anfassen:
- plugins/obsidian/backend/model_router.py
- src/llm_core.py
- src/ai_activity_ledger.py
- UI-Dateien
- Keine fremden Aenderungen revertieren; andere Agenten koennen parallel
  arbeiten.

Anforderungen:
- Safe labels, bounded text, reject secret markers.
- Audit summary ohne Prompts, Outputs, private Pfade oder lange Datasets.
- Dataclasses sollen spaeter vom Episode Store und Scorer nutzbar sein.

Tests:
- C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_model_reward_contract.py

Stop-Regeln:
- Hotfile-Konflikt oder fremde staged files.
- Secrets/Token/Chat-IDs/private Inhalte sollen persistiert oder geloggt
  werden.
- Scope wird verlassen.
- Rote Tests ohne klaren fokussierten Fix.
- Live-Go, Design-Go oder Operator-Go waere noetig.
- Destruktive Git-Kommandos waeren noetig.

Wenn fertig:
- Status melden: done | blocked | deferred | failed.
- Geaenderte Dateien nennen.
- Tests und Ergebnis nennen.
- Offene Risiken und Handoff nennen.
  </input>
</codex_delegation>
```

### Charlie Prompt

```xml
<codex_delegation>
  <source_thread_id>current</source_thread_id>
  <input>Charlie-Slice: RL-5

Arbeite im Odysseus-Fork an einem kleinen, sicheren Slice.

Execution mode: worker
Slice class: safe_offline
Reason: Offline policy recommendation from redacted episodes only.

Ziel:
- Baue eine read-only Policy-Engine, die Kandidaten fuer Modellrouting
  empfiehlt, ohne produktives Routing zu veraendern.

Erlaubte Dateien:
- src/model_routing_policy.py
- tests/test_model_routing_policy.py

Nicht anfassen:
- plugins/obsidian/backend/model_router.py bis RL-6.
- Live provider configs, secrets, UI.
- Keine fremden Aenderungen revertieren; andere Agenten koennen parallel
  arbeiten.

Anforderungen:
- Cold-start respektiert bestehende Router-Ordnung.
- Privacy/safety hard gates schlagen Reward-Historie.
- Policy darf nur vorhandene Kandidaten umsortieren.
- Audit summary erklaert die Entscheidung ohne raw episode dumps.

Tests:
- C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_model_routing_policy.py

Stop-Regeln:
- Hotfile-Konflikt oder fremde staged files.
- Secrets/Token/Chat-IDs/private Inhalte sollen persistiert oder geloggt
  werden.
- Scope wird verlassen.
- Rote Tests ohne klaren fokussierten Fix.
- Live-Go, Design-Go oder Operator-Go waere noetig.
- Destruktive Git-Kommandos waeren noetig.

Wenn fertig:
- Status melden: done | blocked | deferred | failed.
- Geaenderte Dateien nennen.
- Tests und Ergebnis nennen.
- Offene Risiken und Handoff nennen.
  </input>
</codex_delegation>
```
