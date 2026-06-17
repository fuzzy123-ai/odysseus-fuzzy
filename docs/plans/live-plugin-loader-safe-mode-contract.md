# Live Plugin Loader Safe Mode Contract

Stand: 2026-06-17

Status: **LIVE5A Docs-Contract fuer das Gate `live_plugin_loader_safe_mode`**

Quellen:

- `docs/plans/live-quality-gate-command-runner-contract.md`
- `docs/plans/system-health-plugin-audit-index-contract.md`
- `docs/plans/orchestration-runtime-readiness-contract.md`
- `docs/plans/live-release-evidence-closeout-contract.md`

Dieser Contract definiert die Operator- und Nutzersprache fuer einen spaeteren Plugin Loader Safe Mode im read-only Plan-Modus. Der Safe Loader darf nur Manifest-, Capability- und Local-Audit-Signale auswerten, um zu entscheiden, ob ein Plugin spaeter ueberhaupt fuer einen menschlich geprueften Folge-Slice in Frage kommt. Er importiert keinen Plugin-Code, ruft kein `setup()` auf und aktiviert weder Host-, Netzwerk-, Telegram- noch Runtime- oder Plugin-Seiteneffekte. Das Gate bleibt read-only und erzeugt kein externes Release-Go.

## Purpose

`LIVE5A` ist die Vorbereitung fuer einen sicheren Plugin-Loader-Plan nach dem Dry-Run Command Runner.

Der Contract soll beantworten:

- wie ein spaeterer Safe Loader nur als Plan- und Audit-Schicht gedacht ist
- welche Manifest-Signale Pflicht sind
- welche Capability-Grenzen vor jeder spaeteren Aktivierung lesbar sein muessen
- wie lokales Plugin-Audit vor Import oder `setup()` als Pflichtgate bleibt
- wie Operator-Review vor jedem spaeteren Enablement bestehen bleibt

## Leitregel

`LIVE5A` ist Vorbereitung und Contract, kein Plugin-Import, kein `setup()`, kein Host-Agent-Enablement und kein externes Release-Go.

Das bedeutet:

- kein Plugin-Code wird importiert
- kein `setup()` wird ausgefuehrt
- keine Runtime-Aktivierung entsteht aus dem Safe-Mode-Plan
- keine Vermischung mit Host-, Netzwerk-, Telegram-, Provider- oder Scheduler-Aktionen

## Safe Mode Scope

Die Section `safe_mode_scope` soll den erlaubten Funktionsumfang des spaeteren Safe Loaders begrenzen.

Erlaubt spaeter im Plan-Modus:

- Manifest lesen
- deklarierte Capabilities klassifizieren
- lokale Audit-Signale verdichten
- Import-Blocker oder Review-Gruende strukturieren
- Safe-Mode-Status ableiten

Nicht erlaubt:

- Plugin-Code importieren
- `setup()` oder aequivalente Initialisierung aufrufen
- Host-, Netzwerk-, Telegram-, Provider-, Export-/Import-/Rebuild-, Nextcloud- oder Scheduler-Aktionen ausloesen

## Manifest Requirements

Die Section `manifest_requirements` soll festlegen, welche Manifest-Signale vor jedem spaeteren Enablement mindestens vorhanden sein muessen.

Mindestens:

- stabile Plugin-ID
- klare Version oder Scope-Angabe
- deklarierte Capabilities
- deklarierte lokale oder externe Abhaengigkeiten
- lesbare Safe-Mode- oder Audit-Hinweise

Wichtig:

- fehlende oder widerspruechliche Manifest-Daten fuehren zu `blocked` oder `needs_operator_review`
- Manifest-Lesen ist kein Code-Import

## Capability Boundary Requirements

Die Section `capability_boundary_requirements` soll die Grenzen deklarierter Plugin-Capabilities vorab absichern.

Mindestens:

- lokale-only Capabilities klar markiert
- externe oder riskante Capabilities explizit sichtbar
- keine implizite Netzwerk-, Host- oder Telegram-Capability
- keine Capability darf still `setup()`-Seiteneffekte verstecken

Wichtig:

- unbekannte oder zu breite Capabilities bleiben blockiert
- Capabilities werden nur bewertet, nicht aktiviert

## Local Audit Requirements

Die Section `local_audit_requirements` soll beschreiben, welche lokalen Audit-Signale vor einem spaeteren Import-Gedanken vorliegen muessen.

Mindestens:

- lokaler Plugin-Audit-Status
- bekannte Blocker oder Warnungen
- Safe-Mode-geeignete lokale Dateisignale
- keine unerklaerten externen oder hostnahen Seiteneffekte

Wichtig:

- lokales Audit bleibt read-only
- es ersetzt keine spaetere Operator-Freigabe

## Import Blocking Rules

Die Section `import_blocking_rules` muss die harten Grenzen vor jedem spaeteren Plugin-Import oder `setup()` festsetzen.

Mindestens:

- fehlendes oder kaputtes Manifest blockiert
- unklare oder zu breite Capabilities blockieren
- fehlende Local-Audit-Signale blockieren
- Hinweise auf Host-, Netzwerk-, Telegram-, Token- oder Scheduler-Seiteneffekte blockieren
- unbekannte Initialisierungswege blockieren

Wichtig:

- `safe_mode_plan_ready` ist nie gleich `import_allowed`
- Blocker sind Sicherheitsgrenzen, keine TODO-Liste fuer stilles Nachziehen

## Operator Review Flow

Die Section `operator_review_flow` soll beschreiben, wie spaeter ein Mensch die Safe-Mode-Ausgabe liest.

Mindestens:

- Manifest-Signale pruefen
- Capability-Grenzen pruefen
- Local-Audit-Signale gegen Blocker lesen
- blocked oder deferred Gruende bestaetigen
- nur dann ueber spaetere Import-Gates nachdenken, wenn kein Safe-Mode-Grenzbruch sichtbar ist

Wichtig:

- ohne Operator-Review bleibt alles Plan-only
- kein automatischer Enablement-Schritt aus dem Safe-Mode-Ergebnis

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die Aufteilung fuer dieses Gate klar ziehen.

### Alice

Alice verantwortet:

- Nutzer- und Operator-Sprache
- Safe-Mode- und Stop-Regeln
- Audit- und Boundary-Texte

### Bob

Bob verantwortet:

- isoliertes read-only Safe-Loader-Planmodell
- `src/live_plugin_loader_safe_mode.py`
- `tests/test_live_plugin_loader_safe_mode.py`
- reine Bewertung von Manifest-, Capability- und Audit-Signalen

Wichtig:

- Bob darf keinen Plugin-Code importieren
- Bob darf kein `setup()` ausloesen
- Bob darf keine Host-, Netzwerk-, Telegram- oder Scheduler-Runtime aktivieren

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- Testauswahl fuer read-only Modelle
- Worktree- und Push-Pruefung
- Stop-Entscheidung bei riskanter Scope-Verschiebung oder zu breiten Capability-Signalen

## Stop Rules

Die Section `stop_rules` muss das Abrutschen in echte Plugin-Aktivierung verhindern.

Mindestens:

- wenn ein Modell Plugin-Code importieren will: stoppen
- wenn `setup()` oder aequivalente Initialisierung gefordert wird: stoppen
- wenn Host-, Netzwerk-, Telegram-, Provider-, Export-/Import-/Rebuild-, Nextcloud- oder Scheduler-Aktionen verlangt werden: stoppen
- wenn Tokens, Secrets, private Pfade oder rohe Logs auftauchen: stoppen
- wenn Manifest, Capability oder Audit-Signale unklar sind: `needs_operator_review` oder `blocked`, nicht `ready`

## Handoff To Bob

Die Section `handoff_to_bob` soll klar machen, was Bob auf dieser Basis bauen darf.

Erlaubt:

- read-only Safe-Loader-Planmodell
- Klassifikation von Manifest- und Capability-Signalen
- dry-run Statusableitung
- Tests mit mockten Manifest- und Audit-Daten

Nicht erlaubt:

- Plugin-Code importieren
- `setup()` starten
- Host-, Netzwerk- oder Scheduler-Aktionen

## Handoff To Next Live Slice

Die Section `handoff_to_next_live_slice` soll beschreiben, wie spaetere Folge-Slices anknuepfen duerfen.

Mindestens:

- Safe-Mode-Ergebnis bleibt read-only
- echter Plugin-Import braucht spaeter separates Operator-Gate
- offene externen `1.0`-Gates bleiben unberuehrt
- naechste Live-Slices duerfen nur auf explizit freigegebenen Safe-Mode-Artefakten aufbauen

Wichtig:

- auch ein gutes Safe-Mode-Modell hebt `provider_fallback_answer_run` und `test_vault_export_import_rebuild` nicht auf
- externes `1.0` bleibt `No-Go`, bis diese manuellen Gates belegt sind

## Status And Decision Sprache

Pflicht-Gate-ID:

- `live_plugin_loader_safe_mode`

Pflicht-Statuswerte:

- `safe_mode_plan_ready`
- `needs_operator_review`
- `blocked`
- `deferred`

### `safe_mode_plan_ready`

Der Safe Loader kann aus Manifest-, Capability- und Audit-Signalen einen plausiblen read-only Plan erzeugen.

Wichtig:

- kein Plugin wird importiert
- kein globales Live-Go

### `needs_operator_review`

Ein Operator oder Charlie muss spaeter bewusst lesen, ob aus einem Safe-Mode-Plan ueberhaupt ein spaeteres Import-Gate werden darf.

### `blocked`

Mindestens eine harte Grenze, ein fehlendes Signal oder eine verbotene Capability verhindert selbst den sicheren Plan.

### `deferred`

Die Bewertung oder ein Folge-Gate ist bewusst vertagt und bleibt ausserhalb dieses Slices.

## No-Secrets und No-Raw-Logs

Dieser Safe-Mode-Contract und alle Folge-Artefakte duerfen nicht enthalten:

- Secrets
- Tokens
- private Pfade
- rohe Logs
- komplette Plugin- oder Audit-Dumps

Zulaessig sind:

- kompakte Statuswerte
- kurze Review- und Stop-Hinweise
- read-only Manifest- oder Audit-Referenzen

## Beispiel fuer spaeteren sicheren Safe-Mode-Status

Zulaessig:

- `manifest_requirements = plugin id, version, capabilities present`
- `capability_boundary_requirements = no hidden network or host actions`
- `local_audit_requirements = local audit references present`
- `status = safe_mode_plan_ready`
- `operator_review_flow = manual review before any import gate`

Nicht zulaessig:

- `import_now = true`
- `setup_call = true`
- `enable_plugin_runtime = true`
- kompletter Log- oder Plugin-Dump

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur ein isoliertes read-only Safe-Loader-Planmodell in `src/live_plugin_loader_safe_mode.py` und `tests/test_live_plugin_loader_safe_mode.py` bauen, das Manifest-/Capability-/Audit-Signale bewertet und niemals Plugin-Code importiert.

Wichtig:

- keine IO ausser read-only Artefakt-/Modelleingaben
- kein Netzwerk
- keine Host-Kommandos
- kein Plugin-Import und kein `setup()`

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keinen Plugin-Import
- kein `setup()`
- keine Host-, Netzwerk-, Telegram-, Provider-, Export-/Import-/Rebuild-, Nextcloud- oder Scheduler-Aktivierung
- kein externes `1.0`-Go

Er legt nur fest, wie der naechste Live-Integration-Slice als trockener Plugin Loader Safe Mode sprachlich und prozessual sicher vorbereitet wird.
