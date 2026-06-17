# Live Plugin Operator Review Packet Contract

Stand: 2026-06-17

Status: **LIVE11A Docs-Contract fuer das Gate `live_plugin_operator_review_packet_plan`**

Quellen:

- `docs/plans/live-plugin-manifest-discovery-dry-run-contract.md`
- `docs/plans/live-plugin-capability-preview-index-contract.md`
- `docs/plans/live-plugin-loader-safe-mode-contract.md`
- `docs/plans/live-integration-readiness-index-contract.md`
- `docs/plans/live-release-evidence-closeout-contract.md`

Dieser Contract definiert die sichere Operator- und Nutzersprache fuer ein spaeteres Plugin-Operator-Review-Packet im Offline-, Fixture- und Manual-Review-Modus. Das Review-Packet darf nur statische Manifest-, Capability-, Audit-, Preview- und Safe-Mode-Signale zusammenfassen. Es importiert keinen Plugin-Code, ruft kein `setup()` auf, fuehrt keine dynamischen Imports oder Codepfade aus und aktiviert weder Runtime, Netzwerk, Host, Token noch automatische Freigaben. Das Gate bleibt read-only und erzeugt kein externes Release-Go.

## Purpose

`LIVE11A` bereitet ein spaeteres Review-Packet vor, das einem Operator eine kompakte, sichere Plugin-Entscheidungsgrundlage gibt.

Der Contract soll beantworten:

- wie ein Plugin-Review-Packet rein als statische Zusammenfassung gedacht ist
- welche Quellen und Signale in ein spaeteres Review-Packet einfliessen duerfen
- wie Review-Checklist, Blocker und Deferred-Gruende lesbar bleiben
- wie manuelle Operator-Freigabe vor jedem spaeteren Import- oder Enablement-Gedanken bestehen bleibt
- wie Alice, Bob und Charlie read-only bleiben und keine Runtime-Aktionen ausloesen

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keinen Plugin-Import
- kein `setup()`
- keine dynamischen Imports
- keine Codeausfuehrung
- keine Runtime-Aktivierung
- keine Netzwerk-, Host-, Token- oder Scheduler-Aktion
- keine automatische Freigabe
- kein externes `1.0`-Go

## Review Packet Scope And Boundaries

Die Section `review_packet_scope_and_boundaries` soll den Umfang eines spaeteren Review-Packets hart begrenzen.

Erlaubt spaeter im Review-Packet:

- statische Manifest-Signale referenzieren
- deklarierte Capability-Metadata zusammenfassen
- Local-Audit-Hinweise verdichten
- Preview-Index-Ergebnisse zitieren
- Safe-Mode-Gates und Blocker strukturiert anzeigen

Nicht erlaubt:

- Plugin-Code importieren
- `setup()` oder aequivalente Initialisierung aufrufen
- dynamische Imports oder `exec`
- Runtime-Enablement, Auto-Approval oder Side-Effects
- Netzwerk-, Host-, Token-, Provider-, Export-/Import-/Rebuild-, Nextcloud-, Telegram- oder Scheduler-Aktionen

Wichtig:

- das Review-Packet ist eine Leseschicht
- auch vollstaendige Signale bedeuten nie automatische Freigabe

## Allowed Sources And Signals

Die Section `allowed_sources_and_signals` soll festlegen, welche Quellen fuer das spaetere Review-Packet zulaessig sind.

Mindestens:

- Manifest-Dateien
- Capability-Metadata
- Local-Audit-Signale
- Preview-Index-Signale
- Safe-Mode-Gates
- strukturierte blocked- oder deferred-Gruende

Wichtig:

- nur statische, deklarierte oder read-only erzeugte Signale
- keine aus Codeausfuehrung gewonnenen Daten

## Forbidden Actions

Die Section `forbidden_actions` muss die hart verbotenen Aktionen fuer das Review-Packet nennen.

Mindestens:

- `import`
- `setup`
- `exec`
- Netzwerkzugriff
- Hostzugriff
- Token- oder Secret-Nutzung
- Runtime-Enablement
- Auto-Approval

Wichtig:

- jede dieser Aktionen fuehrt zu `blocked`
- `review_packet_ready` ist nie gleich `safe_to_run`

## Packet Sections And Review Checklist

Die Section `packet_sections_and_review_checklist` soll den minimalen Aufbau des spaeteren Review-Packets definieren.

Pflicht-Sections:

- `summary`
- `manifest_signals`
- `capability_signals`
- `local_audit_signals`
- `preview_index_status`
- `safe_mode_gate_status`
- `blocked_or_deferred_reasons`
- `operator_checklist`
- `next_manual_gate`

Pflicht-Checklist:

- Manifest vollstaendig und lesbar
- Capability-Scope statisch nachvollziehbar
- Local-Audit ohne verbotene Seiteneffekte
- Preview-Index bleibt read-only
- Safe-Mode-Gates nicht verletzt
- keine Secrets, Tokens, privaten Pfade oder rohen Logs sichtbar
- kein Import- oder Enablement-Druck aus dem Packet selbst

## Operator Approval Flow

Die Section `operator_approval_flow` soll beschreiben, wie ein Mensch das Review-Packet spaeter lesen darf.

Mindestens:

- Packet-Sections in fester Reihenfolge lesen
- blocked- und deferred-Gruende gegen Scope pruefen
- Capability- und Audit-Signale mit Safe-Mode-Gates abgleichen
- nur manuelle Folgeentscheidung treffen, nie automatische Freigabe

Wichtig:

- ohne Operator-Review bleibt alles read-only
- selbst ein gutes Packet fuehrt nicht direkt zu Import oder Runtime-Enablement

## Redaction And Logging Rules

Die Section `redaction_and_logging_rules` soll die spaetere Paket-Sprache kompakt und sicher halten.

Zulaessig:

- kurze Statuswerte
- kompakte Manifest-, Capability-, Audit- und Gate-Referenzen
- kurze Blocker-, Deferred- und Review-Hinweise

Nicht zulaessig:

- Secrets
- Tokens
- rohe Chat- oder Plugin-IDs mit sensitiver Bedeutung
- private Pfade
- rohe Logs
- komplette Plugin-Dumps

Wichtig:

- das Review-Packet bleibt redigiert
- Logging erklaert Gruende, aber kopiert keine sensitiven Rohdaten

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die sichere Aufteilung fuer dieses Gate festhalten.

### Alice

Alice verantwortet:

- Nutzer- und Operator-Sprache
- Packet-Struktur und Review-Checklist
- Redaction-, Logging- und Stop-Regeln

### Bob

Bob verantwortet:

- ein isoliertes read-only Review-Packet-Modell
- die Zusammenfassung erlaubter Manifest-, Capability-, Audit-, Preview- und Safe-Mode-Signale
- Statusableitung fuer das Packet

Wichtig:

- Bob darf keinen Plugin-Code importieren
- Bob darf kein `setup()`, keinen dynamischen Import und kein `exec` ausloesen
- Bob darf keine Runtime-, Netzwerk-, Host- oder Token-Aktion aktivieren

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- Testauswahl fuer read-only Modelle
- Worktree- und Push-Pruefung
- Stop-Entscheidung bei Scope-Bruch, fremden staged Files oder versteckten Enablement-Signalen

## Stop Rules

Die Section `stop_rules` muss das Abrutschen in echte Plugin-Aktivierung verhindern.

Mindestens:

- wenn Import, `setup()`, dynamischer Import oder `exec` verlangt wird: stoppen
- wenn Netzwerk-, Host-, Token-, Provider-, Export-/Import-/Rebuild-, Nextcloud-, Telegram- oder Scheduler-Aktionen auftauchen: stoppen
- wenn Runtime-Enablement oder Auto-Approval aus dem Packet abgeleitet werden soll: stoppen
- wenn Secrets, Tokens, private Pfade oder rohe Logs sichtbar werden: stoppen
- wenn Manifest-, Capability-, Audit-, Preview- oder Safe-Mode-Signale unklar bleiben: `needs_operator_review` oder `blocked`, nicht `ready`

## Handoff To Bob

Die Section `handoff_to_bob` soll klar begrenzen, was Bob spaeter bauen darf.

Erlaubt:

- isoliertes read-only Review-Packet-Modell
- Zusammenfassung von Manifest-, Capability-, Audit-, Preview- und Safe-Mode-Signalen
- Statusableitung fuer `review_packet_ready`, `needs_operator_review`, `blocked`, `deferred`
- Tests mit mockten oder Fixture-basierten Signalen

Nicht erlaubt:

- Plugin-Importe
- `setup()`
- dynamische Imports oder `exec`
- Netzwerk-, Host-, Token- oder Runtime-Aktionen
- automatische Freigabe

Pflicht-Gate-ID:

- `live_plugin_operator_review_packet_plan`

Pflicht-Statuswerte:

- `review_packet_ready`
- `needs_operator_review`
- `blocked`
- `deferred`

## Example Safe Review Packet

Zulaessig:

- `summary = static review only`
- `allowed_sources = manifest, capability metadata, local audit, preview index, safe mode gates`
- `operator_checklist = manual review before any follow-up gate`
- `status = review_packet_ready`

Nicht zulaessig:

- `import_now = true`
- `setup_call = true`
- `enable_runtime = true`
- `auto_approved = true`

## Akzeptanz Fuer Bob

Ein spaeterer Bob-Slice darf nur ein isoliertes read-only Review-Packet-Modell bauen, das statische Manifest-, Capability-, Audit-, Preview- und Safe-Mode-Signale zu einer manuellen Operator-Zusammenfassung verdichtet.

Wichtig:

- keine IO ausser read-only Artefakt- oder Modelleingaben
- kein Netzwerk
- keine Host-Kommandos
- keine Plugin-Imports
- kein `setup()`
- keine Runtime-Aktivierung

## Abschluss

Dieser Slice liefert nur die sichere Plan- und Vertragssprache fuer ein spaeteres Plugin-Operator-Review-Packet im Offline- und Manual-Review-Modus. Er ist keine Plugin-Aktivierung, keine Discovery-Runtime und keine Live-Freigabe.
