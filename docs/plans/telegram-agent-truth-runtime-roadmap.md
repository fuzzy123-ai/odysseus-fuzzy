# Telegram Agent Truth Runtime Roadmap

Stand: 2026-07-05

Status: Anschluss-Roadmap nach Telegram-Chat-Review. Fokus: Erfolgs-Halluzinationen, falsche Delegate-Ausreden, zu viele Rueckfragen, unangemessene Jubel-Tonalitaet und fehlende harte Verifikation.

## Ziel

Odysseus soll sich in Telegram wie ein verifizierender Coding-Agent verhalten, nicht wie ein Textgenerator mit optimistischer Prosa.

Done bedeutet:

- Keine Erfolgsmeldung ohne maschinenlesbare Evidence.
- Kein "Delegat hat es getan" ohne echten Tool-/Run-Beleg.
- Keine mehrfachen Bestaetigungsfragen ohne neuen Blocker.
- Keine Jubel-/Emoji-Sprache bei `unknown`, `partial`, `blocked` oder `failed`.
- Telegram zeigt Run-Status statt freier Arbeitsbehauptungen.

## Aktueller Stand

Bereits live:

- `delegate` ist read-only und darf keine Implementierung vortaeuschen.
- Runtime-Snapshot wird pro Agent-Turn injiziert und nennt aktive Sandbox-/Telegram-/Evidence-Faehigkeiten.
- `ClaimEvidenceGate` relativiert unbelegte Erfolgsclaims nach dem Stream.
- Artefakt-Integrity ist aktiv fuer Sandbox-Log-Evidence und Telegram-Fotoartefakte.
- Telegram-Fotoversand blockt leere oder gefaelschte PNG/JPG/WebP-Dateien.

Neu implementiert in diesem Slice:

- Redigierter Failure-Corpus fuer die Telegram-Review-Muster.
- Telegram-Pre-Send-Truth-Gate vor `send_telegram_text`/Rich-Fallback.
- Outbound-History speichert den gegateten Sendetext plus redigierte Truth-Gate-Metadaten.
- Tone-Gate entfernt Jubel-/Emoji-Sprache bei nicht verifizierten Erfolgsclaims.
- Tool-Transaction-Ledger modelliert wirkungsvolle Toolausfuehrungen als `succeeded`, `failed`, `blocked` oder `verified`.
- ClaimEvidenceGate kann Erfolgsaussagen gegen Tool-Transaktionen statt nur gegen freie Tool-Output-Prosa pruefen.
- Capability-First-Gate modelliert Programm-/Screenshot-Aufgaben vor dem Start als `ready` oder `blocked`.
- Telegram-Run-State-Contract erzeugt die Sequenz `accepted`, `checking_capabilities`, `running`, `artifact_ready`, `sent`, `verified_done`, `blocked`, `failed`.
- Effectful-Tool-Matrix erweitert den Completion-Verifier um Sandbox-, Telegram-, Browser/Playwright-, Quality-Gate- und Git-Side-Effects.
- Regression-Metriken messen unsupported success, Delegate-Alibi, Repeated-Confirmation und Tone-Gate-Verletzungen.
- Telegram-Screenshot-Delivery-Packets verbinden Bildartefakt-Integrity, sicheren Filename/Caption, Dry-run-Preview und den Photo-Reply-Gate, bevor ein Screenshot-Transport versucht wird.

Noch offen fuer Live:

- Kein echter Telegram-End-to-End-Smoke ohne separates Operator-Go.
- Der Tool-Transaction-Ledger ist aktuell in-memory/metrics-basiert; persistente externe Audit-Speicherung bleibt ein separates Haertungsthema.
- Der echte Telegram-Screenshot-Versand bleibt operator-gated; repo-side sind Preview und Integrity-/Delivery-Packet aktiv.

## Nicht-Ziele

- Kein Live-Telegram-Schreibtest ohne separates Operator-Go.
- Keine Provider-, Nextcloud-, Host-, Backup-, Restore- oder Deploy-Aktion in dieser Roadmap.
- Keine neuen UI-Designs.
- Keine automatische Loeschung oder Rewrite fremder Runtime-Daten.

## Slices

### TTR-0: Telegram Failure Corpus

Class: `safe_offline`

Owner: Alice

Status: implemented repo-side.

Goal: Die beobachteten Fehler als redigierten Testkorpus festhalten.

Arbeit:

- Redigierte Fixtures fuer die kritischen Muster erstellen:
  - behauptetes `pygame`/Installieren ohne Tool-Evidence
  - behaupteter Ordner/Datei/Screenshot ohne Dateisystem-Evidence
  - "Delegate hat falsche Rueckmeldung gegeben" ohne echten Delegate-Event
  - wiederholte Rueckfragen trotz ausreichender Aufgabe
  - Jubel-/Emoji-Ton bei unverified Status
- Keine echten Chat-IDs, Tokens, privaten Inhalte oder Raw-Telegram-Transcripts speichern.

Akzeptanz:

- Tests koennen auf redigierte Fixtures zugreifen.
- Fixtures enthalten nur synthetische oder redigierte Beispiele.

### TTR-1: Pre-Send Claim Gate fuer Telegram

Class: `repo_only`

Owner: Bob

Status: implemented repo-side for Telegram text replies.

Goal: Telegram-Antworten werden vor dem Versand gegen Claims geprueft.

Arbeit:

- Telegram-Reply-Pfad so puffern, dass finale Antwort vor `_reply_with_gate()` durch `ClaimEvidenceGate` laeuft.
- Unsupported Claims muessen vor Versand umformuliert werden:
  - `success` ohne Evidence -> `unknown` oder `partial`
  - "gesendet/erstellt/getestet" ohne Evidence -> "nicht verifiziert"
  - Delegate-Alibi ohne Delegate-Event -> blockiert/unknown
- Bestehenden Post-Stream-Gate im Agent-Loop behalten, aber Telegram bekommt den strengeren Pre-Send-Pfad.

Akzeptanz:

- Telegram kann keine Erfolgsmeldung fuer `pong.py`, Screenshot oder Testlauf senden, wenn die Evidence fehlt.
- Tests beweisen, dass der gesendete Text korrigiert ist, nicht nur nachtraeglich kommentiert.

### TTR-2: Tool Transaction Ledger

Class: `repo_only`

Owner: Bob

Status: implemented repo-side as redacted in-memory transaction ledger exposed through final metrics.

Goal: Jede wirkungsvolle Aktion hat einen maschinenlesbaren Status.

Arbeit:

- Kleines Ledger-Modell einfuehren:
  - `transaction_id`
  - `surface`
  - `tool`
  - `claim_type`
  - `status`: `planned`, `started`, `succeeded`, `failed`, `blocked`, `verified`
  - `evidence_refs`
  - `exit_code`
  - `artifact_refs`
  - `raw_content_visible=false`
- Agent-/Telegram-Pfade koennen daraus lesen, ob ein Claim belegbar ist.
- Keine sensiblen Raw-Outputs speichern.

Akzeptanz:

- Tests zeigen: Erfolgstext braucht `verified` oder passende `succeeded + evidence`.
- Failed/blocked Transaktionen koennen nicht als `verified_done` erscheinen.

### TTR-3: Capability-First Execution Gate

Class: `repo_only`

Owner: Charlie

Status: implemented repo-side.

Goal: GUI/Screenshot/Coding-Aufgaben starten mit einem realen Capability-Check.

Arbeit:

- Fuer Aufgaben wie "baue Pong und schick Screenshot" Pflicht-Check definieren:
  - Python verfuegbar
  - benoetigte Library oder Installationspfad verfuegbar
  - Sandbox kann laufen
  - GUI/Headless/Playwright oder passender Renderer verfuegbar
  - Screenshot-Artefaktpfad beschreibbar
  - Telegram-Foto-Reply verfuegbar
- Fehlende Voraussetzung fuehrt zu `blocked`, nicht zu Erfindung oder Rueckfrage-Spam.

Akzeptanz:

- Wenn `pygame`/Display fehlt, meldet Odysseus konkret `blocked` und nennt die fehlende Evidence.
- Wenn Sandbox/Playwright verfuegbar ist, beginnt Odysseus mit dem passenden Toolpfad.

### TTR-4: Tone Gate

Class: `safe_offline`

Owner: Alice

Status: implemented repo-side for unverified Telegram success claims.

Goal: Tonalitaet folgt Truth-Status.

Arbeit:

- Antwort-Policy:
  - `verified`: knapp bestaetigen
  - `partial`: Fortschritt plus fehlende Evidence
  - `unknown`: klar "nicht verifiziert"
  - `blocked`: Blocker plus naechster konkreter Schritt
  - `failed`: Fehler plus knapper Fixvorschlag
- Emojis/Jubelworte bei nicht-verifizierten Status blockieren.
- Tests fuer "fertig!", "alles erledigt", "screenshot gesendet" ohne Evidence.

Akzeptanz:

- Unsupported/blocked Antworten enthalten keine Jubel-/Emoji-Sprache.
- Verified Antworten bleiben kurz und sachlich.

### TTR-5: Telegram Run-State UX

Class: `repo_only`

Owner: Charlie

Status: implemented repo-side as deterministic run-state contract.

Goal: Telegram zeigt nachvollziehbare Run-Zustaende statt freier Arbeitsprosa.

Arbeit:

- Statusfolge fuer Coding-/Sandbox-Auftraege:
  - `accepted`
  - `checking_capabilities`
  - `running`
  - `artifact_ready`
  - `sent`
  - `verified_done`
  - `blocked`
  - `failed`
- Jede Telegram-Statusmeldung referenziert intern `run_id`, `job_id` oder `artifact_ref`.
- Nutzertext bleibt kurz; Audit enthaelt die Evidence.

Akzeptanz:

- "Baue Pong und schick Screenshot" fuehrt zu einer Run-State-Sequenz oder einem konkreten `blocked`.
- Keine identische Rueckfrage erscheint mehr als einmal ohne neuen Blocker.

### TTR-6: Effectful Tool Matrix

Class: `repo_only`

Owner: Bob

Status: implemented repo-side for verifier snapshots.

Goal: Der Completion-Verifier sieht moderne Side Effects.

Arbeit:

- Effectful Tools erweitern:
  - Sandbox worker submit/status
  - Telegram reply/document/photo
  - Browser/Playwright screenshot
  - Coding-Agent Done-/Quality-Gates
  - Git commit/push
- Actions Snapshot mit `run_id`, `artifact_refs`, `integrity_status`, `transaction_status` erweitern.
- Deterministische Checks schlagen Modell-Verifier.

Akzeptanz:

- Ein Tool-Fail kann nicht durch Modellprosa zu "done" werden.
- Snapshot enthaelt genuegend Evidence fuer eine externe Review.

### TTR-7: Regression Suite aus Telegram-Review

Class: `safe_offline`

Owner: Charlie

Status: implemented repo-side using the redacted Telegram review corpus.

Goal: Die beobachteten Fehler werden dauerhaft messbar.

Arbeit:

- Tests fuer:
  - fake `pygame installed`
  - fake `screenshot.png created`
  - fake Telegram send
  - Delegate-Alibi
  - repeated confirmation loop
  - excessive celebratory tone on non-verified outcome
- Metriken:
  - unsupported_success_count
  - repeated_confirmation_count
  - fake_delegate_blame_count
  - tone_gate_violation_count

Akzeptanz:

- Neue Aenderungen, die das schlechte Verhalten wieder erlauben, schlagen im Test fehl.

## Reihenfolge

1. TTR-0 Failure Corpus. Done repo-side.
2. TTR-1 Pre-Send Claim Gate fuer Telegram. Done repo-side for text replies.
3. TTR-4 Tone Gate. Done repo-side for unverified Telegram success claims.
4. TTR-2 Tool Transaction Ledger. Done repo-side for agent tool events and ClaimEvidenceGate.
5. TTR-3 Capability-First Execution Gate. Done repo-side for program/screenshot prerequisites.
6. TTR-5 Telegram Run-State UX. Done repo-side as deterministic run-state contract.
7. TTR-6 Effectful Tool Matrix. Done repo-side for verifier snapshots.
8. TTR-7 Regression Suite als CI-/Nightly-Basis. Done repo-side for redacted review failures.

## Gate Queue

Gate: `live-telegram-smoke`

Class: `needs_live_go`

Blocks: echter End-to-End-Test "Baue Pong und schick Screenshot per Telegram".

Decision needed: Operator erlaubt einen bounded Live-Telegram-Smoke mit redigiertem Audit.

Safe preparation done: Repo-only Gates, Fixtures und Tests koennen vorher gebaut werden.

Risk if bypassed: echte Telegram-Nachrichten oder Chat-/Token-Leaks ohne Freigabe.

Next safe slice: optional live smoke only after explicit Operator-Go.

## Done Definition

Diese Roadmap ist repo-side done, wenn:

- Telegram-Antworten pre-send gegated sind.
- Tool-Transaktionen Evidence-gebunden sind.
- GUI/Screenshot-Aufgaben erst nach Capability-Check starten.
- Tonalitaet an Truth-Status gebunden ist.
- Der schlechte Telegram-Chat als Regressionstest abgedeckt ist.

Repo-side Status: done.

Optionaler Live-Abschluss nach separatem Go:

- Ein bounded Live-Smoke zeigt die Sequenz `accepted -> checking_capabilities -> running -> artifact_ready -> sent -> verified_done` oder einen korrekten Blocker.
