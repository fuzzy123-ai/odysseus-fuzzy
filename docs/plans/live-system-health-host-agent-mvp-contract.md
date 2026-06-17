# Live System Health Host Agent MVP Contract

Stand: 2026-06-17

Status: **LIVE6A Docs-Contract fuer das Gate `live_system_health_host_agent_mvp_plan`**

Quellen:

- `docs/plans/live-plugin-loader-safe-mode-contract.md`
- `docs/plans/system-health-security-ops-runbook.md`
- `docs/plans/system-health-agent-interface-contract.md`
- `docs/plans/system-health-plugin-audit-index-contract.md`
- `docs/plans/live-system-health-local-api-consumer-contract.md`

Dieser Contract definiert die Operator- und Nutzersprache fuer einen spaeteren System Health Host Agent MVP als Live-Integrations-Vorbereitung. Der Plan beschreibt nur, wie ein Debian-/System-Health-Host-Agent spaeter sicher installiert, gelesen, freigegeben und wieder deaktiviert werden koennte. Er fuehrt keine Host-Kommandos aus, installiert keinen systemd-Service, beruehrt keine Podman-/Docker-/Socket-Konfiguration und startet keine Telegram-, Netzwerk- oder Token-Aktionen. Das Gate bleibt read-only und erzeugt kein externes Release-Go.

## Purpose

`LIVE6A` ist die Vorbereitung fuer den ersten echten Host-Agent-bezogenen Planungsslice nach Safe Mode und Dry-Run-Orchestration.

Der Contract soll beantworten:

- wie der Host Agent MVP nur als Plan- und Freigabe-Schicht beschrieben wird
- welche Operator-Inputs vor einem spaeteren manuellen Installationsversuch vorliegen muessen
- welche Snapshot-API der Host Agent spaeter liefern soll
- wie Installation, Rechte, Rollback und Disable nur als Plan beschrieben werden
- wie Alice, Bob und Charlie vor jeder spaeteren Host-Aktivierung getrennt bleiben

## Leitregel

`LIVE6A` ist Vorbereitung und Contract, kein Host-Agent-Deployment, kein systemd-Start, kein Container-/Socket-Zugriff und kein externes Release-Go.

Das bedeutet:

- keine Host-Kommandos
- keine Paketinstallation
- keine systemd- oder Service-Aktivierung
- keine Socket- oder Container-Manipulation
- keine Netzwerkausfuehrung

## Mvp Scope

Die Section `mvp_scope` soll den erlaubten Plan-Umfang des spaeteren Host Agent MVP begrenzen.

Erlaubt spaeter im Plan-Modus:

- Host-Agent-MVP als separates Artefakt beschreiben
- benoetigte Inputs, Rechte und Disable-Pfade strukturieren
- Snapshot-API als read-only Contract beschreiben
- Operator-Review und Freigabegates verdichten

Nicht erlaubt:

- Host-Agent installieren
- Services anlegen oder starten
- Host-Kommandos ausfuehren
- Container-, Socket-, Telegram-, Netzwerk- oder Token-Aktionen ausloesen

## Host Boundary

Die Section `host_boundary` muss die Trennung zwischen Odysseus-Core und Host-Agent hart festsetzen.

Mindestens:

- Host-Agent sammelt spaeter auf dem Host
- Odysseus-Core liest spaeter nur bereinigte Snapshots
- keine Host-Kommandos aus Odysseus-Core
- keine implizite Docker- oder Podman-Socket-Pflicht
- keine stille Vermischung von Plugin-Loader und Host-Agent-Deployment

Wichtig:

- Host-Zugriff bleibt ausserhalb des Core
- Safe Mode und Host Agent bleiben getrennte Gates

## Required Operator Inputs

Die Section `required_operator_inputs` soll beschreiben, was vor einem spaeteren manuellen Installations- oder Freigabeversuch bekannt sein muss.

Mindestens:

- Zielsystem und grobe Host-Rolle
- geplanter nicht-produktiver oder kontrollierter Rollout-Rahmen
- bekannte Paket- oder Rechtevoraussetzungen
- geplanter Snapshot-Ablage- oder Uebergabepfad
- Rollback- und Disable-Strategie
- Redaktionsregeln fuer spaetere manuelle Evidence

Wichtig:

- fehlende Inputs fuehren spaeter zu `needs_operator_input` statt zu improvisiertem Host-Zugriff

## Snapshot Api Contract

Die Section `snapshot_api_contract` soll beschreiben, welche read-only Snapshot-Semantik der Host Agent spaeter liefern muss.

Mindestens:

- Snapshot-Version
- erzeugt-am-Zeitpunkt
- bereinigte Collector-Zustaende
- Unknown-/Unsupported-Semantik
- klare Trennung zwischen Host-Agent-Rohwelt und Odysseus-Snapshot

Wichtig:

- diese Section beschreibt nur den spaeteren Datenausgang
- sie startet keine API und keinen Agenten

## Install Plan Without Execution

Die Section `install_plan_without_execution` soll die spaetere Installationsidee nur beschreiben, nicht ausfuehren.

Mindestens:

- Paket- und Service-Schritte bleiben nur als Operator-Plan
- keine Befehle werden hier ausgefuehrt
- keine systemd-Unit wird erstellt
- keine Container- oder Socket-Konfiguration wird veraendert

Wichtig:

- Plan ja, Execution nein
- der Vertrag bleibt ausdruecklich read-only

## Permission And Secret Rules

Die Section `permission_and_secret_rules` muss die spaeteren Rechte- und Geheimnisgrenzen hart setzen.

Mindestens:

- minimale Rechte statt breiter Root-Freigabe
- keine Tokens im Repo
- keine Secrets in Logs
- keine private Pfade in Review-Artefakten
- keine Podman-/Docker-Sockets als Pflicht

Wichtig:

- fehlende oder zu breite Rechte bleiben `blocked` oder `needs_operator_input`
- fehlende Geheimnishygiene blockiert jeden spaeteren Host-Agent-Versuch

## Rollback And Disable Plan

Die Section `rollback_and_disable_plan` soll den spaeteren manuellen Rueckweg beschreiben.

Mindestens:

- klarer Disable-Pfad fuer den Host Agent
- Rueckkehr zu read-only Snapshot-Abwesenheit statt kaputtem Halbzustand
- keine destruktiven oder undokumentierten Aufraeumaktionen

Wichtig:

- der Rueckweg muss spaeter manuell und kontrolliert bleiben
- dieser Contract fuehrt keinen Rollback aus

## Alice Bob Charlie Roles

Die Section `alice_bob_charlie_roles` soll die Aufteilung fuer dieses Gate klar ziehen.

### Alice

Alice verantwortet:

- Nutzer- und Operator-Sprache
- Freigabe-, Rechte- und Rollback-Texte
- Boundary- und Stop-Regeln

### Bob

Bob verantwortet:

- isoliertes read-only Host-Agent-Planmodell
- `src/live_system_health_host_agent_plan.py`
- `tests/test_live_system_health_host_agent_plan.py`
- reine Bewertung von Operator-Inputs, Snapshot-Signalen und Plan-Grenzen

Wichtig:

- Bob darf keine Host-Kommandos starten
- Bob darf keine systemd-, Podman-, Docker- oder Socket-Aktivierung ausloesen
- Bob darf keine Netzwerk- oder Telegram-Aktionen aktivieren

### Charlie

Charlie verantwortet:

- Scope-Kontrolle
- Testauswahl fuer read-only Modelle
- Worktree- und Push-Pruefung
- Stop-Entscheidung bei riskanter Scope-Verschiebung oder zu breiten Host-Rechten

## Stop Rules

Die Section `stop_rules` muss das Abrutschen in echte Host- oder Service-Aktionen verhindern.

Mindestens:

- wenn Host-Kommandos gefordert werden: stoppen
- wenn systemd-, Service-, Podman-, Docker- oder Socket-Aktionen gefordert werden: stoppen
- wenn Tokens, Secrets, private Pfade oder rohe Logs auftauchen: stoppen
- wenn ein Modell echte Netzwerk-, Telegram-, Provider-, Export-/Import-/Rebuild- oder Scheduler-Aktionen verlangt: stoppen
- wenn Operator-Inputs, Snapshot-Grenzen oder Rollback-Pfade unklar sind: `needs_operator_input` oder `blocked`, nicht `ready`

## Handoff To Bob

Die Section `handoff_to_bob` soll klar machen, was Bob auf dieser Basis bauen darf.

Erlaubt:

- read-only Host-Agent-Planmodell
- Bewertung von Operator-Inputs
- Bewertung der Snapshot-API- und Boundary-Signale
- Tests mit mockten Plan- und Snapshot-Daten

Nicht erlaubt:

- Host-Kommandos
- Service- oder systemd-Aktionen
- Podman-/Docker-/Socket-Zugriffe
- Netzwerk- oder Telegram-Aktionen

## Handoff To Next Live Slice

Die Section `handoff_to_next_live_slice` soll beschreiben, wie spaetere Folge-Slices anknuepfen duerfen.

Mindestens:

- Host-Agent-Plan bleibt read-only
- echte Installation oder Aktivierung braucht spaeter separates Operator-Gate
- offene externen `1.0`-Gates bleiben unberuehrt
- naechste Live-Slices duerfen nur auf explizit freigegebenen Plan-Artefakten aufbauen

Wichtig:

- auch ein gutes Host-Agent-Planmodell hebt `provider_fallback_answer_run` und `test_vault_export_import_rebuild` nicht auf
- externes `1.0` bleibt `No-Go`, bis diese manuellen Gates belegt sind

## Status And Decision Sprache

Pflicht-Gate-ID:

- `live_system_health_host_agent_mvp_plan`

Pflicht-Statuswerte:

- `host_agent_plan_ready`
- `needs_operator_input`
- `blocked`
- `deferred`

### `host_agent_plan_ready`

Der Host-Agent-MVP kann als read-only Plan aus vorhandenen Inputs und Grenzen plausibel beschrieben werden.

Wichtig:

- kein Host-Agent wird installiert
- kein globales Live-Go

### `needs_operator_input`

Ein Operator oder Charlie muss spaeter bewusst lesen, ob aus dem Plan ueberhaupt ein spaeteres Installations-Gate werden darf.

### `blocked`

Mindestens eine harte Grenze, ein fehlender Input oder eine verbotene Rechte-/Hostannahme verhindert selbst den sicheren Plan.

### `deferred`

Die Bewertung oder ein Folge-Gate ist bewusst vertagt und bleibt ausserhalb dieses Slices.

## No-Secrets und No-Raw-Logs

Dieser Host-Agent-Contract und alle Folge-Artefakte duerfen nicht enthalten:

- Secrets
- Tokens
- private Pfade
- rohe Logs
- komplette Host- oder Service-Dumps

Zulaessig sind:

- kompakte Statuswerte
- kurze Review- und Stop-Hinweise
- read-only Snapshot- oder Plan-Referenzen

## Beispiel fuer spaeteren sicheren Host-Agent-Plan-Status

Zulaessig:

- `required_operator_inputs = target system, rollback plan, snapshot path`
- `snapshot_api_contract = sanitized snapshot only`
- `permission_and_secret_rules = minimal rights, no tokens, no sockets`
- `status = host_agent_plan_ready`
- `rollback_and_disable_plan = manual disable path documented`

Nicht zulaessig:

- `install_now = true`
- `systemd_start = true`
- `mount_docker_socket = true`
- kompletter Host- oder Logdump

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur ein isoliertes read-only Host-Agent-Planmodell in `src/live_system_health_host_agent_plan.py` und `tests/test_live_system_health_host_agent_plan.py` bauen, das Operator-Inputs bewertet und niemals Host-Kommandos startet.

Wichtig:

- keine IO ausser read-only Artefakt-/Modelleingaben
- kein Netzwerk
- keine Host-Kommandos
- keine systemd-, Podman-, Docker- oder Socket-Aktionen

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keinen Host-Agent-Deployment-Schritt
- keine Host-Kommandos
- keine systemd-, Podman-, Docker- oder Socket-Aktivierung
- keine Telegram-, Netzwerk-, Provider-, Export-/Import-/Rebuild- oder Scheduler-Aktivierung
- kein externes `1.0`-Go

Er legt nur fest, wie der naechste Live-Integration-Slice fuer einen System Health Host Agent MVP sprachlich und prozessual sicher vorbereitet wird.
