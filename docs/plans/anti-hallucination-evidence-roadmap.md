# Anti-Hallucination Evidence Roadmap

Stand: 2026-07-05

Status: Roadmap nach Code-/Doc-Pruefung fuer Telegram, Sandbox, Delegate und Coding-Agent-Flows.

Update 2026-07-05:

- Die Telegram-Truth-Runtime-Slices TTR-0 bis TTR-7 sind repo-side umgesetzt.
- Runtime-Snapshot, Recent Changes, Tool-Transaction-Ledger, Telegram-Run-State, Screenshot-Preview und Screenshot-Live-Gate sind aktiv dokumentiert.
- Live Telegram, MCP-Service-Smokes, Provider-, Host- und Deploy-Aktionen bleiben Operator-Gates.

## Ziel

Odysseus soll keine erledigten Arbeiten, gesendeten Dateien, bestandenen Tests oder vorhandenen Artefakte behaupten, wenn dafuer keine maschinenlesbare Evidence vorliegt.

Die Leitregel:

- Freie Agentenprosa ist nie die Quelle der Wahrheit.
- Tool-Ergebnisse, Ledger, Artefaktpfade, Exit-Codes, Git-Zustaende und explizite Gates sind die Quelle der Wahrheit.
- Eine Antwort darf nur so stark formuliert sein wie die Evidence, die sie belegen kann.

## Gepruefte vorhandene Bausteine

### Bereits stark

- `src/tool_result_truth.py` trennt `success` von `verified_done`. Erfolg ohne Evidence bleibt nicht verifiziert.
- `docs/plans/tool-result-truth-contract.md` beschreibt bereits Statussprache, Evidence und die Trennung `claimed done` vs. `verified done`.
- `src/coding_agent_backend.py` hat Quality- und Done-Gates: geaenderte Pfade, Check-Ergebnisse, Review-Entscheidung, Reviewer und `content_reviewed` muessen passen.
- `src/agent_loop_prompts.py` enthaelt Grundregeln: keine Aktion behaupten ohne Tool-Ergebnis, nur fertig melden wenn konkrete Deliverables existieren oder erfolgreich waren.
- `src/agent_loop_orchestration.py` hat einen unabhaengigen Completion-Verifier fuer effectful Tools.
- `src/agent_result_observer.py` normalisiert Sandbox-Result-Evidence, inklusive Exit-Code, Verdict und redaktionssicheren Artefakt-Refs.
- `src/sandbox_artifact_policy.py` klassifiziert Sandbox-Artefakte und verhindert absolute/traversale Pfade.
- `src/agent_report_store.py` behandelt AgentReports als untrusted input und verhindert, dass Reports selbst `verified_done` oder Done-Promotion ausloesen.
- `plugins/telegram/plugin.py` prueft Telegram-Artefakt-Refs vor dem Versand auf erlaubte Roots, Workspace-Grenzen und Existenz.
- `src/agent_sandbox_worker.py` schreibt Live-Sandbox-Command-Artefakte und haengt Evidence an den Ledger.

### Durch die letzte Korrektur bereits adressiert

- `delegate` ist jetzt explizit read-only und darf nicht mehr als Implementierungsworker fuer `pong.py` oder andere Dateien beschrieben werden.
- Sandbox-Jobs tragen Default-Capabilities fuer Python, Node, Playwright, Browser-GUI und Screenshot-Artefakte.
- Telegram kann Sandbox-Screenshots als Foto aus erlaubten Artefaktpfaden senden.
- Capability-Self-Reports werden enger erkannt, damit Coding-/Implementierungsauftraege nicht als reine Diagnoseantwort abgefangen werden.
- `runtime_snapshot` wird in Agent-Turns injiziert und nennt Version, Sandbox, Telegram, Delegate, Evidence-Gates, Screenshot-Delivery und Selbstwissen-Quellen.
- `recent_changes` ist als Changelog-/Patch-Notes-Quelle fuer lokale Odysseus-Neuerungen vorhanden und soll Runtime-Neuerungen nicht aus Memory rekonstruieren.
- `tool_registry`/`tool_index` sind die aktuelle Tool-Inventar-Quelle; ein allgemeiner live Tool-/Endpoint-Status bleibt noch ein eigener Follow-up-Vertrag.
- `tool_transaction_ledger` und `ClaimEvidenceGate` binden Erfolgsclaims an Tool-Transaktionen, Exit-Codes, Artefakt-Refs und Dateisystem-Evidence.
- `telegram_truth_runtime` modelliert Capability-First-Checks, Run-State-Sequenzen und Regression-Metriken fuer unsupported success, Delegate-Alibi, Rueckfrage-Loops und Tone-Gate-Verletzungen.
- `telegram_screenshot_delivery` bietet Preview, Integrity-Packet und Live-Gate-Packet fuer Screenshot-Artefakte; der echte Versand bleibt operator-gated.

## Tool- und Selbstwissen-Coverage aus dem Telegram-Review

Diese Tabelle uebernimmt die aktuelle Tool-Bestandsanalyse in die Roadmap. Wichtig: Runtime-Faehigkeiten werden nicht als langlebiger Memory-Fakt behandelt, sondern aus aktuellen Systemquellen gelesen.

| Punkt | Status | Quelle/Umsetzung | Naechster sicherer Schritt |
| --- | --- | --- | --- |
| Runtime-Snapshot mit Version, Features, Limits und Timestamp | repo-side aktiv | `src/runtime_snapshot.py`, Agent-Kontext-Injection, `/api/version` live pruefbar | Snapshot bei neuen Systemfaehigkeiten aktualisieren und testen |
| Live Tool Registry mit Schema, Side-Effect-Klasse und Gate-Status | teilweise aktiv | `src/tool_registry.py`, `src/tool_index.py`, Tool-Schemas, Effectful-Tool-Matrix | redigierten Runtime-Tool-Status ohne Secrets/Raw-Endpoints als separates Contract-Slice bauen |
| Capability Probe fuer Python, Playwright/GUI, Screenshot-Artefakte, Telegram und MCP | teilweise aktiv | Sandbox defaults, Telegram screenshot live-gate, capability-first program/screenshot check | MCP-Service-Status und allgemeine GUI-/Playwright-Probe bleiben live/service-gated |
| Evidence Ledger fuer Tool-Ausfuehrungen | repo-side aktiv | `src/tool_transaction_ledger.py`, `ClaimEvidenceGate`, Verifier machine evidence | Persistente externe Audit-Speicherung separat haerten |
| Recent Changes / Changelog Injection | repo-side aktiv | `src/recent_changes.py`, Recent-Changes-Routing, Runtime-Snapshot verweist darauf | Nicht in Memory speichern; bei Neuerungsfragen `recent_changes` nutzen |
| Run-State-Modell | repo-side aktiv | `src/telegram_truth_runtime.py` mit `accepted`, `checking_capabilities`, `running`, `artifact_ready`, `sent`, `verified_done`, `blocked`, `failed` | Live-Telegram-Smoke nur nach Operator-Go |
| Ask-User Policy gegen Rueckfrage-Spam | repo-side teilweise aktiv | Regression-Metrik `repeated_confirmation_count`, Runtime-Snapshot-Policy `one_clarification_then_act_or_block` | allgemeine Agent-Policy breiter in Tool-/Prompt-Tests absichern |
| Tool-Failure-Transparenz | repo-side teilweise aktiv | Tool-Transaction-Status, exit_code, blocker, screenshot live-gate statuses | fuer alle grossen Tool-Domaenen konsistente `blocked/failed/next_step` Packets vereinheitlichen |

Nicht als Memory speichern:

- aktuelle Tool-Liste
- aktuelle Systemversion
- aktuelle Env-/Live-Gates
- MCP-/Endpoint-Verfuegbarkeit

Memory darf hoechstens merken: "Runtime-Faehigkeiten sind aus Runtime-Snapshot, Recent Changes und Tool Registry zu lesen; Memory ist dafuer nicht autoritativ." Runtime-Token: `memory_is_not_authoritative_for_runtime_capabilities`.

## Noch noetige Arbeit nach aktuellem Stand

### Luecke 1: Kein zentraler Claim-to-Evidence-Check fuer finale Antworten

Status: repo-side umgesetzt fuer kritische Datei-, Test-, Sandbox-, Telegram- und Screenshot-Claims; weiter ausbauen fuer weitere Domaenen.

Aktuell existieren viele einzelne Gates. Die zentrale Schicht klassifiziert bereits Aussagen wie diese:

- "Ich habe `pong.py` erstellt."
- "Die Tests sind durchgelaufen."
- "Der Screenshot wurde per Telegram geschickt."
- "Der Sandbox-Run war erfolgreich."
- "Die GUI ist verfuegbar."

Weiter noetig: Claim-Typen fuer weitere Domaenen wie Memory/RAG-Writes, Provider-Sync, Calendar/CalDAV und GitHub-Issues schrittweise anbinden.

### Luecke 2: Evidence-Artefakte werden nicht ueberall inhaltlich verifiziert

Status: repo-side umgesetzt fuer Sandbox-Log-Evidence und Telegram-Foto-/Screenshot-Artefakte; weitere Artefaktarten bleiben Follow-up.

Einige Komponenten validieren sichere Pfade und Status, aber nicht durchgaengig:

- Existiert der referenzierte Artefaktpfad wirklich?
- Stimmen Groesse und Hash mit dem geschriebenen Inhalt ueberein?
- Passt `exit_code=0` zur behaupteten Zusammenfassung?
- Ist ein Screenshot wirklich ein Bild und kein leeres oder falsches Artefakt?

Der Artefakt-Integrity-Layer prueft sichere Refs, Existenz, Dateityp, Groesse und Content-Hash fuer die bereits angebundenen Pfade.

### Luecke 3: Completion-Verifier deckt neue effectful Tools nicht voll ab

Status: repo-side umgesetzt mit Effectful-Tool-Matrix und maschinenlesbarer Evidence im Actions Snapshot.

Der Verifier soll fuer die aktuellen Problemfaelle erfassen:

- Sandbox-Worker-Submits und Sandbox-Ledger-Events
- `telegram_document_reply`
- Browser-/Playwright-/Screenshot-Artefakte
- Coding-Agent-Handoff-, Quality- und Done-Gates
- Repo-/Git-Status-Aktionen

Weiter noetig: neue Tool-Domaenen muessen in die Matrix aufgenommen werden, sobald sie wirkungsvolle Side Effects bekommen.

### Luecke 4: Telegram-Status braucht maschinenlesbare Run- und Artefaktbindung

Status: repo-side umgesetzt als Run-State-Contract; echter End-to-End-Telegram-Smoke bleibt Operator-Go.

Telegram soll nicht nur Text wie "ich arbeite dran" erhalten, sondern Status aus einem Job-/Run-Ledger:

- `accepted`
- `running`
- `blocked`
- `failed`
- `artifact_ready`
- `sent`
- `verified_done`

Jede Nachricht ueber erstellte Programme oder Screenshots sollte `run_id`, `job_id` oder `artifact_ref` im internen Audit tragen.

### Luecke 5: Rueckfragen/Confirmations brauchen eine Policy

Status: repo-side teilweise umgesetzt ueber Regression-Metrik und Runtime-Snapshot-Policy.

Das beobachtete Verhalten "fragt immer wieder, faengt nicht an" deutet auf unscharfe Confirmation- und Tool-Policy hin. Noetig ist eine Regel:

- Eine Rueckfrage ist erlaubt, wenn der Auftrag ohne Antwort riskant oder unklar ist.
- Danach muss entweder begonnen, sauber blockiert oder ein konkreter Genehmigungsrequest fuer eine echte Mutation gestellt werden.
- Wiederholte Rueckfragen ohne neue Unsicherheit gelten als Policy-Fehler und muessen getestet werden.

## Priorisierte Roadmap

### P0: Truth Gate fuer kritische Abschlussclaims

Ziel: Kein finaler Erfolgsclaim ohne Evidence.

Arbeit:

- `ClaimEvidenceGate` mit Claim-Typen einfuehren: `file_created`, `file_changed`, `command_passed`, `sandbox_succeeded`, `artifact_exists`, `telegram_sent`, `git_committed`. `verified_done` bleibt ein abgeleiteter Gate-Zustand, kein normaler Claim-Typ.
- Deterministische Evidence-Resolver bauen: Dateisystem, Sandbox-Ledger, Telegram-Dispatch-Result, Coding-Done-Gate, Git-Status.
- Mindestfelder fuer Resolver festlegen: `file_created` braucht Pfad plus Existenz/Stat; `command_passed` braucht Command-Text, Exit-Code und Run-/Event-ID; `telegram_sent` braucht Outbound-Dispatch mit `ok=true`; `artifact_exists` braucht sichere Repo-Ref plus Existenz.
- Finale Antworten vor Versand pruefen: unbelegte Claims werden als `partial`, `unknown` oder `blocked` markiert und in Nutzertext als nicht verifiziert kenntlich gemacht.
- Regressionstests fuer falsche Claims: `pong.py` nicht erstellt, fehlender Screenshot, fehlender Telegram-Versand, Tests nicht gelaufen.

Akzeptanz:

- Ein Agent kann nicht mehr "erstellt/gesendet/getestet" schreiben, wenn der passende Evidence-Resolver leer bleibt.
- Tests decken Telegram, Sandbox und Coding-Flow ab.

### P1: Artefakt-Integrity fuer Sandbox und Telegram

Status 2026-07-05: repo-seitig umgesetzt fuer Sandbox-Log-Evidence und Telegram-Fotoartefakte. Ein echter Live-Telegram-Smoke bleibt ein separater Operator-Go.

Ziel: Artefakt-Refs sind nicht nur sichere Strings, sondern belegbare Dateien.

Arbeit:

- `ResultArtifact` optional um `size_bytes`, echten Datei-`content_hash`, `exists`, `mime_hint` erweitern. **Done fuer Observer-Bundles.**
- Sandbox-Worker schreibt echte Content-Hashes fuer Log-Artefakte. **Done.**
- Telegram-Document-Reply akzeptiert nur Foto-Artefakte mit bestandenem Integrity-Check im Outbound-Fotopfad. **Done fuer PNG/JPG/WebP.**
- Screenshot-Sanity pruefen: Bildformat, Mindestgroesse, nicht leer. **Done fuer Telegram-Fotoartefakte.**

Akzeptanz:

- Fehlende oder leere Screenshot-Dateien koennen nicht als gesendet oder fertig gelten.
- Evidence-Bundles unterscheiden "Ref geplant" von "Artefakt existiert".

### P1: Effectful Tool Matrix erweitern

Ziel: Der Completion-Verifier sieht alle relevanten Side Effects.

Arbeit:

- `_VERIFIER_EFFECTFUL_TOOLS` um Sandbox, Telegram, Browser, Coding- und Git-Aktionen erweitern.
- Actions Snapshot strukturierter machen: Tool, Exit-Code, Run-ID, Artefakt-Refs, Status, Gate-Ergebnis.
- Modell-Verifier erst nach deterministischen Checks laufen lassen.
- Wenn deterministische Checks widersprechen, gewinnt der Check gegen Modellprosa.

Akzeptanz:

- Ein Sandbox-Fail kann nicht durch eine optimistische Modellantwort zu "done" werden.
- Telegram-Versand wird nur als erledigt gemeldet, wenn das Dispatch-Ergebnis `ok=true` liefert.

### P1: Telegram Run-State und Screenshot Delivery UX

Ziel: Telegram-Nutzer sehen echten Fortschritt statt freier Arbeitsprosa.

Arbeit:

- Telegram Coding-Auftraege mit `run_id` und `job_id` in Ledger aufnehmen.
- Statusantworten aus Ledger rendern.
- Wenn ein Programm in der Sandbox gebaut wird, Screenshot-Artefakte automatisch fuer `telegram_document_reply` vormerken.
- Bei Blockern genau eine klare Rueckfrage oder Genehmigungsanforderung senden.

Akzeptanz:

- "Baue Pong und schick Screenshot" fuehrt zu `accepted -> running -> artifact_ready -> sent` oder zu einem konkreten `blocked`.
- Keine wiederholten identischen Bestaetigungsfragen.

### P2: Source-of-Truth Catalog

Ziel: Jede Domaene hat eine kanonische Wahrheitsquelle.

Arbeit:

- Katalog dokumentieren: Dateien = FS/Git, Tests = CommandResult, Sandbox = SandboxLedger, Telegram = outbound result/history, Browser = screenshot/DOM evidence, Memory/RAG = source refs.
- Toolbeschreibungen und Prompts auf diesen Katalog ausrichten.
- UI und Telegram koennen "verified by" kurz anzeigen.

Akzeptanz:

- Entwickler und Agenten koennen fuer jeden Claim-Typ eine eindeutige Quelle nennen.

### P2: RAG- und Wissensantworten evidenzbinden

Ziel: Nicht nur Aktionen, sondern auch Faktenantworten werden weniger halluziniert.

Arbeit:

- Antworten aus internen Docs/Chats brauchen `source_refs` oder eine sichtbare Unsicherheitsformulierung.
- Externe aktuelle Fakten brauchen Web-/Provider-Evidence mit Datum.
- Derived Summaries bleiben als Derived Data markiert und duerfen keine Truth-Writes ausloesen.

Akzeptanz:

- Bei fehlender Quelle sagt Odysseus "nicht belegt" statt eine konkrete Tatsache zu erfinden.

### P3: Hallucination Regression Suite und Metriken

Ziel: Halluzinationen als messbare Regression behandeln.

Arbeit:

- Testkorpus mit adversarial Prompts: fehlende Dateien, fehlende Freigaben, widerspruechliche Tool-Ausgaben, abgeschnittene Logs, leere Screenshots.
- Metriken: blocked false-positive rate, unsupported-success rate, repeated-confirmation count, artifact-missing claim count.
- Nightly/CI-Smoke fuer Telegram/Sandbox/Coding-Claims.

Akzeptanz:

- Eine neue Aenderung, die unbelegte Erfolgsaussagen wieder erlaubt, faellt im Test auf.

## Reihenfolge

1. P0 `ClaimEvidenceGate` implementieren und an finale Antworten anbinden.
2. P1 Artefakt-Integrity fuer Sandbox/Telegram einfuehren.
3. P1 Verifier-Toolmatrix und deterministische Checks erweitern.
4. P1 Telegram Run-State fuer Coding- und Screenshot-Flows verbinden.
5. P2 Source-of-Truth Catalog in Toolbeschreibungen, Prompts und UI sichtbar machen.
6. P2/P3 Faktenantworten und Regression Suite nachziehen.

## Minimaler MVP

Der kleinste wirksame Schritt ist:

- Claim-Typen erkennen.
- Evidence fuer `file_created`, `command_passed`, `sandbox_succeeded`, `artifact_exists`, `telegram_sent` pruefen.
- `artifact_exists` prueft im MVP nur sichere Ref und Existenz; echte Groessen-/MIME-/Datei-Hash-Integrity bleibt P1.
- Finalantwort blockieren oder abschwaechen, wenn Evidence fehlt.
- Tests fuer die beobachteten Fehlerfaelle `pong.py`, Sandbox-Screenshot und Telegram-Versand schreiben.

Das ist wichtiger als neue Prompts, weil Prompts nur Verhalten vorschlagen. Der Claim-Evidence-Check erzwingt die Wahrheit an der Stelle, an der Halluzinationen fuer den Nutzer sichtbar werden.
