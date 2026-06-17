# Live Quality Gate Command Runner Contract

Stand: 2026-06-17

Status: **LIVE4A Docs-Contract fuer das Gate `live_quality_gate_command_runner_dry_run`**

Quellen:

- `docs/plans/live-orchestration-runtime-bridge-contract.md`
- `docs/plans/quality-gates-contract.md`
- `docs/plans/orchestration-runtime-readiness-contract.md`
- `docs/plans/live-release-evidence-closeout-contract.md`
- `docs/plans/live-plugin-loader-safe-mode-contract.md`

Dieser Contract definiert die Operator- und Nutzersprache fuer einen spaeteren Quality-Gate Command Runner im Plan-/Dry-Run-Modus. Der Runner darf Kommandos nur klassifizieren, planen und gegen Stop-Regeln halten. Er fuehrt keine Tests aus, startet keine Git-Kommandos, aktiviert keinen Scheduler und beruehrt keine Provider-, Host-, Telegram- oder Netzwerkruntime. Das Gate bleibt read-only und erzeugt kein externes Release-Go.

## Purpose

`LIVE4A` ist die Vorbereitung fuer den naechsten sicheren Live-Integration-Slice nach der Dry-Run Runtime Bridge.

Der Contract soll beantworten:

- wie ein spaeterer Command Runner nur als Plan-Schicht gedacht ist
- welche Kommandoklassen ueberhaupt als spaeter potenziell erlaubbar modelliert werden duerfen
- welche Kommandoklassen hart blockiert bleiben
- wie Operator-Freigabe vor jeder spaeteren Ausfuehrung zwingend bleibt
- wie Evidence und Logs begrenzt und redigiert bleiben

## Leitregel

`LIVE4A` ist Vorbereitung und Contract, kein echter Command-Runner, kein automatischer Testlauf, kein Git-Runner und kein externes Release-Go.

Das bedeutet:

- keine echte Kommandoausfuehrung
- keine Test- oder Git-Prozesse
- keine automatische Eskalation von Plan zu Lauf
- keine Vermischung mit Provider-, Export-, Host- oder Netzwerkgates

## Dry Run Scope

Die Section `dry_run_scope` soll den erlaubten Funktionsumfang des spaeteren Runners begrenzen.

Erlaubt spaeter im dry-run:

- Kommandos klassifizieren
- erlaubte und blockierte Klassen markieren
- moegliche Quality-Gate-Kommandos als Plan beschreiben
- Zeitlimit-, Review- und Evidence-Anforderungen verdichten
- blocked oder deferred Gruende erklaeren

Nicht erlaubt:

- Tests starten
- Git-Kommandos ausfuehren
- Scheduler oder Heartbeat starten
- Provider-, Netzwerk-, Telegram-, Export-/Import-/Rebuild-, Nextcloud- oder Host-Aktionen planen oder ausfuehren

## Allowed Command Classes

Die Section `allowed_command_classes` soll nur die Klassen beschreiben, die spaeter prinzipiell als operator-kontrollierte Quality-Gate-Kommandos modelliert werden duerfen.

Zulaessige Klassen im Plan-Modus:

- fokussierte Testgruppe
- read-only Statuspruefung
- statische Validierung
- deterministischer Renderer- oder Contract-Check
- read-only Artifact-/Snapshot-Pruefung

Wichtig:

- auch diese Klassen bleiben in `LIVE4A` rein geplant
- keine dieser Klassen wird hier ausgefuehrt

## Blocked Command Classes

Die Section `blocked_command_classes` muss die hart gesperrten Klassen benennen.

Mindestens:

- destruktive Kommandos
- Host-Kommandos
- Git-Mutationen
- ungebundene Test-Suites
- Provider- oder Netzwerk-Kommandos
- Telegram-, Export-/Import-/Rebuild-, Nextcloud- oder Scheduler-Aktionen

Wichtig:

- blockierte Klassen bleiben blockiert, auch wenn ein Plan sie technisch erkennen kann
- `plan_ready` ist nie gleich `safe_to_run_without_operator`

## Operator Approval Flow

Die Section `operator_approval_flow` soll beschreiben, wie spaeter vor jeder echten Kommandoausfuehrung ein Mensch dazwischen bleiben muss.

Mindestens:

- Command-Plan lesen
- Klasse und Scope pruefen
- Zeitlimit und Evidence-Regeln pruefen
- blocked oder deferred Gruende bestaetigen
- nur dann ueber einen spaeteren echten Runner nachdenken, wenn ein separates Operator-Gate existiert

Wichtig:

- ohne Operator-Freigabe bleibt alles Plan-only
- keine automatische Ausfuehrung aus dem dry-run Ergebnis

## Timeout And Log Policy

Die Section `timeout_and_log_policy` soll klare Leitplanken fuer spaetere, noch nicht aktivierte Kommandoausfuehrung definieren.

Mindestens:

- jeder spaetere Kommando-Plan braucht explizites Timeout
- Logs duerfen nur kompakt und redigiert beschrieben werden
- kein Rohlog als Pflichtartefakt
- keine stillen Langlaeufer

Wichtig:

- dieser Contract fuehrt keine Timeouts durch
- er beschreibt nur spaetere Pflichtregeln

## Evidence Capture Rules

Die Section `evidence_capture_rules` soll die spaetere Evidence fuer geplante Kommandos begrenzen.

Zulaessig spaeter:

- Kommandoklasse
- geplanter Scope
- Timeout-Hinweis
- Operator-Approval-Status
- kompakte Ergebnis- oder Blocker-Referenz

Nicht zulaessig:

- rohe Logs
- Secrets
- Tokens
- private Pfade
- komplette Test- oder Git-Outputs ohne Redaktion

Wichtig:

- `LIVE4A` selbst erfasst keine neue Evidence aus laufenden Kommandos
- es friert nur das spaetere Evidence-Schema ein

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die Aufteilung fuer dieses Gate klar ziehen.

### Alice

Alice verantwortet:

- Nutzer- und Operator-Sprache
- Plan-/Dry-Run- und Stop-Regeln
- Log- und Evidence-Grenzen

### Bob

Bob verantwortet:

- isoliertes read-only Command-Plan-Modell
- `src/live_quality_gate_command_runner.py`
- `tests/test_live_quality_gate_command_runner.py`
- reine Klassifizierung und Planung, niemals Ausfuehrung

Wichtig:

- Bob darf keine Kommandos ausfuehren
- Bob darf keine Scheduler-, Git-, Test- oder Netzwerkruntime aktivieren

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- Testauswahl fuer read-only Modelle
- Worktree- und Push-Pruefung
- Stop-Entscheidung bei riskanter Scope-Verschiebung oder zu breiten Kommandoplanen

## Stop Rules

Die Section `stop_rules` muss das Abrutschen in echte Kommandoausfuehrung verhindern.

Mindestens:

- wenn ein Modell echte Tests oder Git-Kommandos direkt ausfuehren will: stoppen
- wenn destruktive oder Host-Kommandos auftauchen: stoppen
- wenn Provider-, Netzwerk-, Telegram-, Export-/Import-/Rebuild-, Nextcloud- oder Scheduler-Aktionen verlangt werden: stoppen
- wenn Tokens, Secrets, private Pfade oder rohe Logs auftauchen: stoppen
- wenn ein Plan keine klare Klasse oder kein klares Timeout hat: `needs_operator_approval` oder `blocked`, nicht `ready`

## Handoff To Bob

Die Section `handoff_to_bob` soll klar machen, was Bob auf dieser Basis bauen darf.

Erlaubt:

- read-only Command-Plan-Modell
- Klassifikation erlaubter und blockierter Klassen
- dry-run Statusableitung
- Tests mit mockten Command-Definitionen

Nicht erlaubt:

- echte Test- oder Git-Kommandos
- Host- oder Netzwerkaktionen
- Scheduler- oder Runner-Starts

## Handoff To Next Live Slice

Die Section `handoff_to_next_live_slice` soll beschreiben, wie spaetere Folge-Slices anknuepfen duerfen.

Mindestens:

- Command-Plan bleibt dry-run-only
- echte Ausfuehrung braucht separates Operator-Gate
- offene externen `1.0`-Gates bleiben unberuehrt
- naechste Live-Slices duerfen nur auf explizit freigegebenen Plan-Artefakten aufbauen

Wichtig:

- auch ein gutes dry-run Command-Modell hebt `provider_fallback_answer_run` und `test_vault_export_import_rebuild` nicht auf
- externes `1.0` bleibt `No-Go`, bis diese manuellen Gates belegt sind

## Status And Decision Sprache

Pflicht-Gate-ID:

- `live_quality_gate_command_runner_dry_run`

Pflicht-Statuswerte:

- `plan_ready`
- `needs_operator_approval`
- `blocked`
- `deferred`

### `plan_ready`

Der Runner kann aus erlaubten read-only Inputs einen plausiblen Command-Plan beschreiben.

Wichtig:

- kein Command wird ausgefuehrt
- kein globales Live-Go

### `needs_operator_approval`

Ein Operator oder Charlie muss spaeter bewusst freigeben, ob aus einem Plan ueberhaupt ein zulassiger Folge-Gate-Versuch werden darf.

### `blocked`

Mindestens eine harte Grenze, ein fehlender Input oder eine verbotene Kommandoklasse verhindert selbst den sicheren dry-run Plan.

### `deferred`

Die Bewertung oder ein Folge-Gate ist bewusst vertagt und bleibt ausserhalb dieses Slices.

## No-Secrets und No-Raw-Logs

Dieser Command-Runner-Contract und alle Folge-Artefakte duerfen nicht enthalten:

- Secrets
- Tokens
- private Pfade
- rohe Logs
- komplette Test- oder Git-Outputs

Zulaessig sind:

- kompakte Statuswerte
- kurze Review- und Stop-Hinweise
- read-only Snapshot- oder Plan-Referenzen

## Beispiel fuer spaeteren sicheren Command-Plan-Status

Zulaessig:

- `allowed_command_classes = focused tests, static validation`
- `blocked_command_classes = git mutations, host commands, network actions`
- `status = plan_ready`
- `operator_approval_flow = manual approval before any run`
- `handoff_to_bob = read-only planner only`

Nicht zulaessig:

- `run_now = true`
- `git_execute = true`
- `test_start = true`
- kompletter Log- oder Kommandooutputdump

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur ein isoliertes read-only Command-Plan-Modell in `src/live_quality_gate_command_runner.py` und `tests/test_live_quality_gate_command_runner.py` bauen, das Kommandos nur klassifiziert oder plant und niemals ausfuehrt.

Wichtig:

- keine IO ausser read-only Artefakt-/Modelleingaben
- kein Netzwerk
- keine Host-Kommandos
- keine echten Tests, Git-Runs oder Scheduler

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keinen echten Command-Runner
- keinen automatischen Testlauf
- keinen Git-Runner
- keine Provider-, Netzwerk-, Telegram-, Export-/Import-/Rebuild-, Nextcloud- oder Host-Aktivierung
- kein externes `1.0`-Go

Er legt nur fest, wie der naechste Live-Integration-Slice als trockener Quality-Gate Command Runner sprachlich und prozessual sicher vorbereitet wird.
