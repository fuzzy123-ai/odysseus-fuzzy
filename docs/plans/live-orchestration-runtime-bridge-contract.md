# Live Orchestration Runtime Bridge Contract

Stand: 2026-06-17

Status: **LIVE3A Docs-Contract fuer das Gate `live_orchestration_runtime_bridge_dry_run`**

Quellen:

- `docs/plans/live-release-evidence-closeout-contract.md`
- `docs/plans/orchestration-runtime-readiness-contract.md`
- `docs/plans/orchestration-operator-activation-contract.md`
- `docs/plans/orchestration-activation-readiness-summary-contract.md`
- `docs/plans/live-quality-gate-command-runner-contract.md`

Dieser Contract definiert die Operator- und Nutzersprache fuer eine trockene Orchestration Runtime Bridge im read-only Modus. Die Bridge verbindet spaeter Registry-, Mailbox- oder Thread-Referenzen mit einem Dispatch-Plan und klaren Stop-/Review-Regeln, ohne jemals selbst zu senden, zu schedulen oder Git-/Test-Runner auszufuehren. Das Gate bleibt dry-run-only und erzeugt kein externes Release-Go.

## Purpose

`LIVE3A` ist die Vorbereitung fuer den ersten Live-Integration-Slice nach den reinen Evidence-Plan-Slices.

Der Contract soll beantworten:

- wie eine spaetere read-only Runtime Bridge ueberhaupt gedacht ist
- welche Eingaben sie lesen darf
- welche Dispatch-Plan-Zustaende sie im dry-run melden darf
- wie ein Operator den Plan prueft, bevor echte Sends oder Runner jemals freigeschaltet werden
- wie Alice, Bob und Charlie in diesem Track getrennt bleiben

## Leitregel

`LIVE3A` ist Vorbereitung und Contract, kein echter Thread-Send, kein Scheduler, kein automatischer Git-/Testlauf und kein externes Release-Go.

Das bedeutet:

- keine echte Ausfuehrung
- keine automatische Aktivierung
- keine stille Eskalation von dry-run zu live
- keine Vermischung mit Provider-, Export-, Import- oder Host-Gates

## Dry Run Scope

Die Section `dry_run_scope` soll den erlaubten Funktionsumfang der spaeteren Bridge begrenzen.

Erlaubt spaeter im dry-run:

- Registry lesen
- Mailbox- oder ThreadRefs lesen
- Handoff- oder Plan-Snapshots auswerten
- moegliche Dispatch-Schritte als Plan strukturieren
- Review- oder Stop-Gruende verdichten

Nicht erlaubt:

- Thread-Sends
- Scheduler-Starts
- Git-Kommandos
- Test-Kommandos
- Provider-, RAG-, Export-, Import-, Rebuild-, Nextcloud-, Telegram- oder Host-Aktionen

## Runtime Boundaries

Die Section `runtime_boundaries` muss die harten Grenzen fuer diese Bridge setzen.

Mindestens:

- keine echten Thread-Sends aus Runtime-Code
- kein Scheduler oder Heartbeat mit Live-Ticks
- keine Git-/Test-Runner
- keine Provider- oder Netzwerkaktivierung
- keine Export-/Import-/Rebuild-Aktivierung
- keine Host- oder Telegram-Aktivierung

Wichtig:

- diese Grenzen gelten auch dann, wenn die dry-run Bridge einen plausiblen Dispatch-Plan erzeugt
- `dry_run_ready` ist nie gleich `runtime_enabled`

## Allowed Inputs

Die Section `allowed_inputs` soll festlegen, welche Daten eine spaetere Bridge im dry-run lesen darf.

Zulaessige Inputs:

- Registry-Snapshots
- Plan-/Slice-Zuordnungen
- Mailbox- oder ThreadRefs
- Handoff-Daten
- Quality-Gate- oder Readiness-Snapshots
- bekannte Stop-Regeln und Review-Hinweise

Nicht zulaessige Inputs:

- Secrets
- Tokens
- private Pfade
- rohe Logs
- komplette Thread-Historien

Wichtig:

- die Bridge liest nur erlaubte, kompakte Steuerdaten
- sie braucht keine Live-Provider- oder Netzwerksicht

## Dispatch Plan States

Die Section `dispatch_plan_states` soll die Status- und Decision-Sprache festlegen.

Pflicht-Gate-ID:

- `live_orchestration_runtime_bridge_dry_run`

Pflicht-Statuswerte:

- `dry_run_ready`
- `needs_operator_review`
- `blocked`
- `deferred`

## Bedeutung der Dispatch Plan States

### `dry_run_ready`

Die Bridge kann aus vorhandenen read-only Inputs einen plausiblen Dispatch-Plan erzeugen.

Wichtig:

- kein Thread-Send
- kein Runner-Start
- kein globales Live-Go

### `needs_operator_review`

Ein Operator oder Charlie muss die dry-run Ausgabe bewusst lesen und freigeben, bevor spaetere Live-Gates ueberhaupt gedacht werden duerfen.

### `blocked`

Mindestens eine harte Grenze, ein fehlender Input oder eine Scope-Unklarheit verhindert selbst den sicheren dry-run Plan.

### `deferred`

Die Bewertung oder ein Folge-Gate ist bewusst vertagt und bleibt ausserhalb dieses Slices.

## Operator Review Flow

Die Section `operator_review_flow` soll die spaetere menschliche Pruefreihenfolge beschreiben.

Mindestens:

- dry-run Inputs lesen
- Dispatch-Plan gegen Scope und Stop-Regeln pruefen
- pruefen, ob nur read-only Daten genutzt wurden
- blocked oder deferred Gruende bestaetigen
- nur dann ueber naechste Live-Slices nachdenken, wenn kein Dry-Run-Grenzbruch sichtbar ist

Wichtig:

- der Operator entscheidet spaeter manuell
- ohne diese Pruefung bleibt die Bridge im dry-run Kontext

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die Aufteilung fuer dieses Gate klar ziehen.

### Alice

Alice verantwortet:

- Nutzer- und Operator-Sprache
- Runbook- und Gate-Texte
- Dry-run- und Stop-Regeln

### Bob

Bob verantwortet:

- isoliertes read-only Bridge-Plan-Modell
- `src/live_orchestration_runtime_bridge.py`
- `tests/test_live_orchestration_runtime_bridge.py`
- Bewertung von Dispatch-Faehigkeit nur im dry-run

Wichtig:

- Bob darf niemals senden
- Bob darf keinen Scheduler starten
- Bob darf keine Git-/Test-Runner oder Netzwerkausfuehrung aktivieren

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- Testauswahl fuer read-only Modelle
- Worktree- und Push-Pruefung
- Stop-Entscheidung bei unklaren Inputs oder riskanter Scope-Verschiebung

## Stop Rules

Die Section `stop_rules` muss das Abrutschen in echte Runtime-Aktionen verhindern.

Mindestens:

- wenn ein Modell echten Thread-Send plant oder impliziert: stoppen
- wenn Scheduler oder Heartbeat live starten sollen: stoppen
- wenn Git- oder Test-Runner direkt ausgeloest werden sollen: stoppen
- wenn Provider-, Netzwerk-, Telegram-, Export-/Import-/Rebuild-, Nextcloud- oder Host-Aktionen verlangt werden: stoppen
- wenn Secrets, Tokens, private Pfade oder rohe Logs auftauchen: stoppen
- wenn Inputs nicht klar read-only und dry-run sind: `needs_operator_review` oder `blocked`, nicht `ready`

## Handoff To Bob

Die Section `handoff_to_bob` soll klar machen, was Bob auf dieser Basis bauen darf.

Erlaubt:

- read-only Bridge-Plan-Modell
- dry-run Statusableitung
- Tests mit mockten Registry-, Mailbox- und Handoff-Daten

Nicht erlaubt:

- echte Thread-Sends
- Scheduler-Ticks
- Git-/Test-Kommandos
- Provider- oder Netzwerkzugriff

## Handoff To Next Live Slice

Die Section `handoff_to_next_live_slice` soll beschreiben, wie ein spaeterer Folge-Slice anknuepfen darf.

Mindestens:

- dry-run Bridge-Ergebnis bleibt read-only
- echter Send oder Runner braucht spaeter separates Operator-Gate
- offene externe `1.0`-Gates bleiben unberuehrt
- naechste Live-Slices duerfen nur auf explizit freigegebenen dry-run Artefakten aufbauen

Wichtig:

- auch eine gute dry-run Bridge hebt `provider_fallback_answer_run` und `test_vault_export_import_rebuild` nicht auf
- externes `1.0` bleibt `No-Go`, bis diese manuellen Gates belegt sind

## No-Secrets und No-Raw-Logs

Dieser Bridge-Contract und alle Folge-Artefakte duerfen nicht enthalten:

- Secrets
- Tokens
- private Pfade
- rohe Logs
- komplette Thread-Historien

Zulaessig sind:

- kompakte Statuswerte
- kurze Review- und Stop-Hinweise
- read-only Snapshot-Referenzen

## Beispiel fuer spaeteren sicheren Dry-Run-Bridge-Status

Zulaessig:

- `allowed_inputs = registry snapshot, mailbox refs, handoff data`
- `dispatch_plan_states = dry_run_ready`
- `operator_review_flow = review before any live gate`
- `handoff_to_bob = read-only bridge plan model only`
- `handoff_to_next_live_slice = still needs operator gate`

Nicht zulaessig:

- `send_now = true`
- `scheduler_start = true`
- `run_git_tests = true`
- kompletter Log- oder Threaddump

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur ein isoliertes read-only Bridge-Plan-Modell in `src/live_orchestration_runtime_bridge.py` und `tests/test_live_orchestration_runtime_bridge.py` bauen, das Dispatch-Faehigkeit nur als dry-run bewertet und niemals sendet.

Wichtig:

- keine IO ausser read-only Artefakt-/Modelleingaben
- kein Netzwerk
- keine Host-Kommandos
- keine echten Thread-Sends oder Scheduler

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keinen echten Thread-Send
- keinen Scheduler
- keinen Git-/Test-Runner
- keine Provider-, Netzwerk-, Telegram-, Export-/Import-/Rebuild-, Nextcloud- oder Host-Aktivierung
- kein externes `1.0`-Go

Er legt nur fest, wie der erste Live-Integration-Slice als trockene Orchestration Runtime Bridge sprachlich und prozessual sicher vorbereitet wird.
