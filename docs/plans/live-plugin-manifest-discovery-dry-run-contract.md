# Live Plugin Manifest Discovery Dry Run Contract

Stand: 2026-06-17

Status: **LIVE9A Docs-Contract fuer das Gate `live_plugin_manifest_discovery_dry_run_plan`**

Quellen:

- `docs/plans/live-plugin-loader-safe-mode-contract.md`
- `docs/plans/live-telegram-status-dry-run-contract.md`
- `docs/plans/live-quality-gate-command-runner-contract.md`
- `docs/plans/live-release-evidence-closeout-contract.md`

Dieser Contract definiert die Operator- und Nutzersprache fuer eine spaetere Plugin-Manifest-Discovery im Dry-Run. Die Discovery darf nur statische Manifest-, Metadata- und Policy-Signale im Offline-/Fixture-/Operator-Review-Mode lesen oder simulieren. Sie importiert keinen Plugin-Code, ruft kein `setup()` auf, fuehrt keine dynamischen Imports oder Exec-Pfade aus und startet keine Netzwerk-, Host-, Token- oder Runtime-Aktion. Das Gate bleibt read-only und erzeugt kein externes Release-Go.

## Purpose

`LIVE9A` ist die Vorbereitung fuer einen spaeteren Manifest-Discovery-Plan nach Safe Mode, Local API Consumer und Telegram Dry Run.

Der Contract soll beantworten:

- wie Plugin-Discovery nur als statische Metadata-/Policy-Schicht gedacht ist
- welche Manifest- und Metadata-Signale spaeter im Dry-Run lesbar sein duerfen
- welche Discovery-Aktionen hart verboten bleiben
- wie Operator-Review vor jedem spaeteren Import- oder Enablement-Gedanken bestehen bleibt
- wie Alice, Bob und Charlie vor jeder echten Code- oder Runtime-Aktivierung getrennt bleiben

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keinen Plugin-Import
- kein `setup()`
- keine dynamischen Imports
- kein `exec`
- keine Netzwerk-, Host-, Token- oder Scheduler-Aktivierung
- kein externes `1.0`-Go

## Dry Run Scope And Manifest Discovery Boundaries

Die Section `dry_run_scope_and_manifest_discovery_boundaries` soll den erlaubten Umfang der spaeteren Discovery begrenzen.

Erlaubt spaeter im Dry-Run:

- statische Manifest-Dateien lesen
- deklarierte Metadata-Felder verdichten
- Policy-Hinweise oder Capability-Deklarationen klassifizieren
- Fixture- oder Preview-Daten fuer Discovery verwenden
- blocked oder deferred Gruende strukturieren

Nicht erlaubt:

- Plugin-Code importieren
- `setup()` oder aequivalente Initialisierung aufrufen
- dynamische Imports oder Codeauswertung
- Netzwerk-, Host-, Provider-, Export-/Import-/Rebuild-, Nextcloud-, Telegram- oder Scheduler-Aktionen

Wichtig:

- Discovery bleibt rein statisch
- selbst plausible Manifest-Signale duerfen keinen Codepfad aktivieren

## Allowed Manifest And Metadata Signals

Die Section `allowed_manifest_and_metadata_signals` soll beschreiben, welche Signale spaeter im Dry-Run zulaessig sind.

Mindestens:

- Plugin-ID
- deklarierte Version
- deklarierte Capability-Liste
- deklarierte lokale oder externe Abhaengigkeiten
- deklarierte Safe-Mode-, Audit- oder Policy-Hinweise
- statische Metadata-Felder fuer Scope, Label oder Beschreibung

Wichtig:

- nur deklarierte, statische Signale
- keine aus Code berechneten Seiteneffekte

## Forbidden Discovery Actions

Die Section `forbidden_discovery_actions` muss die hart verbotenen Discovery-Aktionen benennen.

Mindestens:

- `import`
- `setup`
- `exec`
- Netzwerkzugriff
- Hostzugriff
- Token-Laden oder Secret-Lesen

Wichtig:

- jede dieser Aktionen fuehrt zu `blocked`
- `discovery_plan_ready` ist nie gleich `safe_to_import`

## Offline Fixture Preview Flow

Die Section `offline_fixture_preview_flow` soll beschreiben, wie eine spaetere Discovery-Vorschau ohne Runtime aussehen darf.

Mindestens:

- statische Fixture oder Manifest-Datei lesen
- erlaubte Signalschicht extrahieren
- Dry-Run-Status ableiten
- Preview-Text ohne Import oder Initialisierung erzeugen

Wichtig:

- kein Code wird geladen
- kein Manifest-Read fuehrt zu Enablement

## Operator Approval Flow

Die Section `operator_approval_flow` soll beschreiben, wie spaeter vor jedem weitergehenden Plugin-Schritt ein Mensch dazwischen bleiben muss.

Mindestens:

- Manifest- und Metadata-Signale lesen
- Policy- und Boundary-Hinweise pruefen
- blocked oder deferred Gruende bestaetigen
- nur dann ueber spaetere Import- oder Enablement-Gates nachdenken, wenn kein Dry-Run-Grenzbruch sichtbar ist

Wichtig:

- ohne Operator-Review bleibt alles Discovery-only
- kein automatischer Enablement-Schritt aus Preview-Ergebnissen

## Redaction And Logging Rules

Die Section `redaction_and_logging_rules` soll die spaetere Logging- und Preview-Sprache begrenzen.

Zulaessig:

- kompakte Statuswerte
- statische Manifest- oder Metadata-Referenzen
- kurze Policy-, Boundary- oder Blocker-Hinweise
- Fixture-Hinweise

Nicht zulaessig:

- Secrets
- Tokens
- private Pfade
- rohe Logs
- komplette Plugin-Dumps oder dynamische Trace-Ausgaben

Wichtig:

- Logging bleibt kompakt und redigiert
- kein Rohdump als Standard-Preview

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die Aufteilung fuer dieses Gate klar ziehen.

### Alice

Alice verantwortet:

- Nutzer- und Operator-Sprache
- Metadata-, Policy- und Boundary-Texte
- Dry-Run- und Stop-Regeln

### Bob

Bob verantwortet:

- isoliertes read-only Manifest-Discovery-Planmodell
- `src/live_plugin_manifest_discovery_dry_run.py`
- `tests/test_live_plugin_manifest_discovery_dry_run.py`
- reine Bewertung von Manifest-, Metadata- und Policy-Signalen

Wichtig:

- Bob darf keinen Plugin-Code importieren
- Bob darf kein `setup()`, keinen dynamischen Import und kein `exec` ausloesen
- Bob darf keine Netzwerk-, Host- oder Token-Aktion aktivieren

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- Testauswahl fuer read-only Modelle
- Worktree- und Push-Pruefung
- Stop-Entscheidung bei riskanter Scope-Verschiebung oder unklaren Discovery-Grenzen

## Stop Rules

Die Section `stop_rules` muss das Abrutschen in echte Code- oder Runtime-Aktivierung verhindern.

Mindestens:

- wenn Import, `setup()`, dynamischer Import oder `exec` gefordert wird: stoppen
- wenn Netzwerk-, Host-, Token-, Provider-, Export-/Import-/Rebuild-, Nextcloud-, Telegram- oder Scheduler-Aktionen verlangt werden: stoppen
- wenn Secrets, Tokens, private Pfade oder rohe Logs auftauchen: stoppen
- wenn Manifest-, Metadata- oder Policy-Grenzen unklar sind: `needs_operator_review` oder `blocked`, nicht `ready`

## Handoff To Bob

Die Section `handoff_to_bob` soll klar machen, was Bob auf dieser Basis bauen darf.

Erlaubt:

- read-only Manifest-Discovery-Planmodell
- Bewertung von statischen Manifest- und Metadata-Signalen
- Statusableitung fuer `discovery_plan_ready`, `needs_operator_review`, `blocked`, `deferred`
- Tests mit mockten Manifest- und Fixture-Daten

Nicht erlaubt:

- Plugin-Importe
- `setup()`
- dynamische Imports oder `exec`
- Netzwerk- oder Host-Aktionen

Pflicht-Gate-ID:

- `live_plugin_manifest_discovery_dry_run_plan`

Pflicht-Statuswerte:

- `discovery_plan_ready`
- `needs_operator_review`
- `blocked`
- `deferred`

## Example Safe Discovery Status

Zulaessig:

- `allowed_manifest_signals = id, version, capabilities, policy hints`
- `offline_preview_flow = fixture -> static parse -> manual review`
- `forbidden_discovery_actions = import, setup, exec, network`
- `status = discovery_plan_ready`
- `handoff_to_bob = read-only manifest discovery model only`

Nicht zulaessig:

- `import_now = true`
- `setup_call = true`
- `dynamic_exec = true`
- kompletter Plugin- oder Trace-Dump

## Akzeptanz Fuer Bob

Ein spaeterer Bob-Slice darf nur ein isoliertes read-only Manifest-Discovery-Planmodell in `src/live_plugin_manifest_discovery_dry_run.py` und `tests/test_live_plugin_manifest_discovery_dry_run.py` bauen, das Manifest-/Metadata-/Policy-Signale bewertet und niemals Plugin-Code ausfuehrt.

Wichtig:

- keine IO ausser read-only Artefakt-/Modelleingaben
- kein Netzwerk
- keine Host-Kommandos
- keine Importe, kein `setup()`, kein `exec`

## Abschluss

Dieser Slice liefert nur die sichere Plan- und Vertragssprache fuer eine spaetere Plugin-Manifest-Discovery im Dry-Run. Er ist keine Plugin-Aktivierung, keine Discovery-Runtime und keine Live-Freigabe.
