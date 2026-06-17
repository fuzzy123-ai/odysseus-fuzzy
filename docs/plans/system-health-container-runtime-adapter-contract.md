# System Health Container Runtime Adapter Contract

Stand: 2026-06-17

Status: **SHC6A Docs-Contract fuer den System Health Checker Container Runtime Adapter**

Quellen:

- `docs/plans/system-health-agent-interface-contract.md`
- `docs/plans/system-health-rule-engine-alert-contract.md`
- `docs/plans/system-health-checker-plugin.md`

Dieser Contract definiert die Runtime-Erkennung und Snapshot-Semantik fuer Container-Health im System Health Checker. Podman bleibt die bevorzugte Laufzeit, Docker ein kompatibler Fallback. Odysseus Core und Container fuehren weiterhin keine Host-Kommandos aus und mounten keinen Docker- oder Podman-Socket. Dieser Slice beschreibt nur Modell- und Entscheidungssprache fuer spaetere Host-Agent-Adapter.

## Ziel

Odysseus braucht eine klare, konservative Runtime-Adapter-Semantik fuer Container-Health.

Diese Semantik soll:

- Podman-first und Docker-compatible abbilden
- Unknown-, Unsupported- und Fehlerfaelle robust modellieren
- Container-Health als bereinigten Snapshot sichtbar machen
- den Core strikt von Host-CLI und Sockets trennen

## Leitregel

Container-Runtime-Erkennung und Container-Health gehoeren spaeter in den Host-Agenten, nicht in den Odysseus-Core.

Das bedeutet:

- keine direkten Host-Kommandos im Odysseus-Core
- kein Docker- oder Podman-Socket im Odysseus-Container
- keine direkte Runtime-CLI im Core
- Odysseus konsumiert nur bereinigte Adapter-Entscheidungen und Snapshot-Daten

## Runtime-Typen

Die spaetere Runtime-Erkennung soll mindestens diese Typen kennen:

- `podman`
- `docker`
- `both`
- `none`
- `unknown`

## Bedeutung der Runtime-Typen

### `podman`

Podman ist verfuegbar und gilt als bevorzugte Container-Runtime.

### `docker`

Docker ist verfuegbar und dient als kompatibler Fallback.

### `both`

Podman und Docker sind beide erkennbar.

Wichtig:

- Podman-first bleibt trotzdem die Default-Praferenz

### `none`

Keine unterstuetzte Container-Runtime wurde erkannt.

### `unknown`

Es ist aktuell nicht verlaesslich feststellbar, welche Runtime verfuegbar ist.

Wichtig:

- `unknown` ist kein stilles `none`

## Adapter-Typen

Die spaetere Modell- oder Policy-Schicht soll mindestens diese Adapter-Klassen oder logisch aequivalenten Typen kennen:

- `PodmanAdapter`
- `DockerAdapter`
- `NoContainerRuntimeAdapter`

Optional spaeter denkbar:

- ein Resolver oder Decision-Layer, der zwischen ihnen waehlt

## Podman-first Regel

Podman bleibt die bevorzugte Runtime.

Das bedeutet:

- bei `both` gewinnt Podman als primaerer Adapter
- rootless Podman muss ausdruecklich mitgedacht werden
- keine Socket-Pflicht fuer Podman

## Keine Socket-Pflicht

Dieser Contract setzt eine harte Grenze:

- keine Docker-Socket-Pflicht
- keine Podman-Socket-Pflicht
- kein direkter Socket-Mount in den Odysseus-Container

Wenn spaeter Socket-basierte Integrationen denkbar sind, bleiben sie ausserhalb dieses Slices und duerfen nicht als Default angenommen werden.

## CLI-/Socket-Grenze

CLI- und Socket-Details bleiben spaeter ausschliesslich Host-Agent-Verantwortung.

Das bedeutet:

- Odysseus-Core kennt keine echten Runtime-Kommandos
- Odysseus-Core parst keine `podman ps`- oder `docker ps`-Ausgaben
- Adapter-Entscheidungen werden als bereinigte Daten uebergeben

## Output-Semantik

Der Runtime-Adapter soll spaeter in `RuntimeStatus` und `CollectorStatus` fuer `containers` uebersetzt werden.

Mindestens relevant:

- `runtime_type`
- `containers.status`
- `container_count`
- `unhealthy_count`
- `setup_hint`

## `RuntimeStatus`

`RuntimeStatus` soll fuer den Containerbereich mindestens ausdruecken koennen:

- erkannte Runtime
- Runtime verfuegbar oder nicht
- Snapshot plausibel oder unknown

Empfohlene konservative Zustaende:

- `ok`
- `degraded`
- `offline`
- `unknown`

## `CollectorStatus` fuer `containers`

Der `containers`-Collector soll spaeter mindestens Folgendes sichtbar machen:

- `status`
- `observed_value`
- `setup_hint` optional

Beispiel fuer `observed_value`:

- `{ "runtime_type": "podman", "container_count": 6, "unhealthy_count": 1 }`

## `container_count`

`container_count` zeigt die Anzahl sichtbarer Container in der gewaehlten Runtime-Sicht.

Wichtig:

- kompakter Snapshotwert
- keine kompletten Container-Listen in diesem Interface noetig

## `unhealthy_count`

`unhealthy_count` zeigt, wie viele Container spaeter als unhealthy, down oder vergleichbar auffaellig gelten.

Wichtig:

- nur Zaehlwert oder kompakte Verdichtung
- keine direkten Reparaturhandlungen

## `setup_hint`

`setup_hint` erklaert, was bei fehlender oder unvollstaendiger Runtime-Lage spaeter geprueft werden sollte.

Beispiele:

- `verify rootless podman is available for host agent`
- `docker runtime not detected on host`
- `container runtime visibility unavailable in host agent`

## Failure Semantics

Fehler und Nichtverfuegbarkeit muessen als Datenzustand modelliert werden, nicht als Crash.

Mindestens ausdruecklich modellieren:

- `unsupported`
- `unavailable`
- `permission_denied`
- `command_failed`

## Bedeutung der Failure Semantics

### `unsupported`

Diese Runtime-Art oder Runtime-Erkennung ist in der aktuellen Umgebung bewusst nicht verfuegbar.

### `unavailable`

Die Runtime waere prinzipiell relevant, ist aber aktuell nicht verfuegbar.

Beispiele:

- Podman nicht installiert
- Docker-Dienst nicht vorhanden

### `permission_denied`

Der spaetere Host-Agent kann die Runtime nicht ausreichend lesen.

Wichtig:

- Datenzustand
- kein Crash

### `command_failed`

Ein spaeterer Host-Agent-Check ist fehlgeschlagen, obwohl die Runtime prinzipiell da sein koennte.

Wichtig:

- nur als bereinigter Status
- keine rohen stderr-/Trace-Dumps im Core

## Unknown- und Unsupported-Semantik

Container-Health muss konservativ bleiben:

- `unknown` ist kein `ok`
- `unsupported` ist keine Fehlbehauptung
- `none` ist nicht automatisch `critical`, solange keine Policy das so ausdruecklich festlegt

## Rootless Podman

Rootless Podman ist ein expliziter Designfall.

Das bedeutet:

- kein Root-Zwang im Contract
- keine Annahme eines privilegierten Podman-Sockets
- Host-Agent darf spaeter rootless Podman als normalen positiven Runtime-Fall modellieren

## Sicherheitsregeln

Dieser Contract setzt harte Grenzen:

- keine Host-Kommandos aus dem Odysseus-Core
- kein Docker-Socket-Mount in den Odysseus-Container
- kein Podman-Socket-Mount in den Odysseus-Container
- keine Container-Reparatur oder Restart-Logik
- keine Secrets oder Tokens im Runtime-Snapshot

## Akzeptanz fuer Bob

Ein spaeterer Bob-Slice darf nur reine Adapter-Modelle oder Decision-Modelle bauen mit:

- gemockten Inputs
- reinen Dataclasses
- reinen Buildern oder Resolvern

Wichtig:

- keine subprocess-Aufrufe
- keine IO
- keine Host-Kommandos
- keine Socket-Zugriffe
- keine Runtime-Ausfuehrung

## Beispiel fuer spaetere sichere Ausgaben

Zulaessig:

- `runtime_type = podman`
- `containers.status = ok`
- `container_count = 6`
- `unhealthy_count = 0`
- `setup_hint = "none"`

Oder:

- `runtime_type = unknown`
- `containers.status = unknown`
- `setup_hint = "container runtime visibility unavailable in host agent"`

Nicht zulaessig:

- `mount /var/run/docker.sock`
- `run podman ps from Odysseus core`
- `restart unhealthy container now`

## Nicht-Ziele

Dieser Contract beschreibt bewusst nicht:

- keine Adapter-Implementierung
- keine Host-Agent-CLI-Ausfuehrung
- keine Socket-Integration
- keine Container-Restart- oder Repair-Logik
- keine UI-Implementierung

Er legt nur fest, wie Container-Runtime-Erkennung und Container-Health spaeter konservativ, Podman-first und Core-entkoppelt in Snapshot-Daten uebersetzt werden sollen.
