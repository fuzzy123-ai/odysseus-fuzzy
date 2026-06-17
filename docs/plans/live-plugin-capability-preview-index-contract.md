# Live Plugin Capability Preview Index Contract

Stand: 2026-06-17

Status: **LIVE10A Docs-Contract fuer das Gate `live_plugin_capability_preview_index_plan`**

Quellen:

- `docs/plans/live-plugin-manifest-discovery-dry-run-contract.md`
- `docs/plans/live-plugin-loader-safe-mode-contract.md`
- `docs/plans/live-plugin-operator-review-packet-contract.md`
- `docs/plans/live-quality-gate-command-runner-contract.md`
- `docs/plans/live-release-evidence-closeout-contract.md`

Dieser Contract definiert die Operator- und Nutzersprache fuer einen spaeteren Plugin-Capability-Preview-Index im Offline-/Fixture-/Operator-Review-Mode. Der Preview-Index darf nur statische Manifest-, Capability- und Audit-Metadata lesen oder simulieren, um spaeter einen sicheren Ueberblick ueber potentielle Plugin-Faehigkeiten zu geben. Er importiert keinen Plugin-Code, ruft kein `setup()` auf, fuehrt keine dynamischen Imports oder Codeausfuehrung aus und startet keine Netzwerk-, Host-, Token- oder Runtime-Aktion. Das Gate bleibt read-only und erzeugt kein externes Release-Go.

## Purpose

`LIVE10A` ist die Vorbereitung fuer einen spaeteren Capability-Ueberblick nach Manifest Discovery und Safe Mode.

Der Contract soll beantworten:

- wie ein Capability-Preview-Index nur als statische Review-Schicht gedacht ist
- welche Manifest-, Capability- und Audit-Signale spaeter sichtbar gemacht werden duerfen
- welche Discovery- oder Aktivierungsaktionen hart verboten bleiben
- wie Operator-Review vor jedem spaeteren Import- oder Enablement-Gedanken bestehen bleibt
- wie Alice, Bob und Charlie vor jeder echten Code- oder Runtime-Aktivierung getrennt bleiben

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keinen Plugin-Import
- kein `setup()`
- keine dynamischen Imports
- keine Codeausfuehrung
- keine Netzwerk-, Host-, Token- oder Scheduler-Aktivierung
- kein externes `1.0`-Go

## Preview Index Scope And Boundaries

Die Section `preview_index_scope_and_boundaries` soll den erlaubten Umfang des spaeteren Preview-Index begrenzen.

Erlaubt spaeter im Dry-Run:

- statische Manifest-Dateien lesen
- deklarierte Capability-Signale verdichten
- Audit- oder Policy-Hinweise sichtbar machen
- Fixture- oder Preview-Daten fuer den Index verwenden
- blocked oder deferred Gruende strukturieren

Nicht erlaubt:

- Plugin-Code importieren
- `setup()` oder aequivalente Initialisierung aufrufen
- dynamische Imports oder `exec`
- Netzwerk-, Host-, Provider-, Export-/Import-/Rebuild-, Nextcloud-, Telegram- oder Scheduler-Aktionen

Wichtig:

- der Preview-Index bleibt rein statisch
- selbst plausible Capability-Signale duerfen keinen Codepfad aktivieren

## Allowed Capability Manifest Audit Signals

Die Section `allowed_capability_manifest_audit_signals` soll beschreiben, welche Signale spaeter im Preview-Index zulaessig sind.

Mindestens:

- Plugin-ID
- deklarierte Version
- deklarierte Capability-Liste
- deklarierte lokale oder externe Abhaengigkeiten
- deklarierte Safe-Mode-, Audit- oder Policy-Hinweise
- statische Metadata-Felder fuer Label, Scope oder Beschreibung
- lokale Audit-Signale ohne Runtime-Seiteneffekte

Wichtig:

- nur deklarierte, statische Signale
- keine aus Code berechneten Seiteneffekte

## Forbidden Actions

Die Section `forbidden_actions` muss die hart verbotenen Aktionen fuer den Preview-Index benennen.

Mindestens:

- `import`
- `setup`
- `exec`
- Netzwerkzugriff
- Hostzugriff
- Token-Laden oder Secret-Lesen
- Runtime-Enablement

Wichtig:

- jede dieser Aktionen fuehrt zu `blocked`
- `preview_index_ready` ist nie gleich `safe_to_enable`

## Offline Fixture Preview Flow

Die Section `offline_fixture_preview_flow` soll beschreiben, wie eine spaetere Capability-Vorschau ohne Runtime aussehen darf.

Mindestens:

- statische Fixture oder Manifest-Datei lesen
- erlaubte Signalschicht extrahieren
- Preview-Index-Zeilen oder Status ableiten
- Preview-Text ohne Import oder Initialisierung erzeugen

Wichtig:

- kein Code wird geladen
- kein Preview-Read fuehrt zu Enablement

## Operator Approval Flow

Die Section `operator_approval_flow` soll beschreiben, wie spaeter vor jedem weitergehenden Plugin-Schritt ein Mensch dazwischen bleiben muss.

Mindestens:

- Manifest-, Capability- und Audit-Signale lesen
- Policy- und Boundary-Hinweise pruefen
- blocked oder deferred Gruende bestaetigen
- nur dann ueber spaetere Import- oder Enablement-Gates nachdenken, wenn kein Dry-Run-Grenzbruch sichtbar ist

Wichtig:

- ohne Operator-Review bleibt alles Preview-only
- kein automatischer Enablement-Schritt aus Preview-Ergebnissen

## Redaction And Logging Rules

Die Section `redaction_and_logging_rules` soll die spaetere Logging- und Preview-Sprache begrenzen.

Zulaessig:

- kompakte Statuswerte
- statische Manifest-, Capability- oder Audit-Referenzen
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
- Capability-, Policy- und Boundary-Texte
- Dry-Run- und Stop-Regeln

### Bob

Bob verantwortet:

- isoliertes read-only Capability-Preview-Index-Modell
- `src/live_plugin_capability_preview_index.py`
- `tests/test_live_plugin_capability_preview_index.py`
- reine Bewertung von Manifest-, Capability- und Audit-Signalen

Wichtig:

- Bob darf keinen Plugin-Code importieren
- Bob darf kein `setup()`, keinen dynamischen Import und kein `exec` ausloesen
- Bob darf keine Netzwerk-, Host- oder Token-Aktion aktivieren

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- Testauswahl fuer read-only Modelle
- Worktree- und Push-Pruefung
- Stop-Entscheidung bei riskanter Scope-Verschiebung oder unklaren Preview-Grenzen

## Stop Rules

Die Section `stop_rules` muss das Abrutschen in echte Code- oder Runtime-Aktivierung verhindern.

Mindestens:

- wenn Import, `setup()`, dynamischer Import oder `exec` gefordert wird: stoppen
- wenn Netzwerk-, Host-, Token-, Provider-, Export-/Import-/Rebuild-, Nextcloud-, Telegram- oder Scheduler-Aktionen verlangt werden: stoppen
- wenn Secrets, Tokens, private Pfade oder rohe Logs auftauchen: stoppen
- wenn Manifest-, Capability- oder Audit-Grenzen unklar sind: `needs_operator_review` oder `blocked`, nicht `ready`

## Handoff To Bob

Die Section `handoff_to_bob` soll klar machen, was Bob auf dieser Basis bauen darf.

Erlaubt:

- read-only Capability-Preview-Index-Modell
- Bewertung von statischen Manifest-, Capability- und Audit-Signalen
- Statusableitung fuer `preview_index_ready`, `needs_operator_review`, `blocked`, `deferred`
- Tests mit mockten Manifest- und Fixture-Daten

Nicht erlaubt:

- Plugin-Importe
- `setup()`
- dynamische Imports oder `exec`
- Netzwerk- oder Host-Aktionen

Pflicht-Gate-ID:

- `live_plugin_capability_preview_index_plan`

Pflicht-Statuswerte:

- `preview_index_ready`
- `needs_operator_review`
- `blocked`
- `deferred`

## Example Safe Preview Index Status

Zulaessig:

- `allowed_signals = id, version, capabilities, audit hints`
- `offline_preview_flow = fixture -> static parse -> manual review`
- `forbidden_actions = import, setup, exec, network, host`
- `status = preview_index_ready`
- `handoff_to_bob = read-only capability preview model only`

Nicht zulaessig:

- `enable_now = true`
- `setup_call = true`
- `dynamic_exec = true`
- kompletter Plugin- oder Trace-Dump

## Akzeptanz Fuer Bob

Ein spaeterer Bob-Slice darf nur ein isoliertes read-only Capability-Preview-Index-Modell in `src/live_plugin_capability_preview_index.py` und `tests/test_live_plugin_capability_preview_index.py` bauen, das Manifest-/Capability-/Audit-Signale bewertet und niemals Plugin-Code ausfuehrt.

Wichtig:

- keine IO ausser read-only Artefakt-/Modelleingaben
- kein Netzwerk
- keine Host-Kommandos
- keine Importe, kein `setup()`, kein `exec`

## Abschluss

Dieser Slice liefert nur die sichere Plan- und Vertragssprache fuer einen spaeteren Plugin-Capability-Preview-Index im Dry-Run. Er ist keine Plugin-Aktivierung, keine Discovery-Runtime und keine Live-Freigabe.
