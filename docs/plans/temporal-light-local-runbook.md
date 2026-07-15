# Temporal Light: lokaler Entwicklungsbetrieb

Status: ausschließlich lokale Entwicklung. Dieser CLI-Server ist weder für
Produktion noch für externe Erreichbarkeit vorgesehen. Er überspringt bewusst
Produktions-Sicherheitsprüfungen und erteilt keinerlei Git-, Provider-,
Deployment- oder Agent-Effect-Autorität.

## Gepinnte Komponenten

- Python SDK: `temporalio==1.30.0`
- Temporal CLI: `v1.8.0`
- CLI Windows amd64 ZIP SHA-256:
  `8cf686dc5ae1280357509ad8d0f1e1b3647d1f3873d904a3e9fe20eb820cfbf4`
- gRPC: ausschließlich `127.0.0.1:7233`
- Namespace: `default`
- Task Queue: `odysseus-temporal-light`
- UI: deaktiviert (`--headless`)
- Persistenz: lokale SQLite-Datei außerhalb des Repositories unter
  `%LOCALAPPDATA%\Odysseus\TemporalLight\runtime\temporal.db` auf Windows
  beziehungsweise dem Benutzer-State-Verzeichnis auf Unix.

Ein Upgrade einer der beiden Versionen ist ein eigener Kompatibilitäts-Slice
mit Replay-Tests. Ein variabler Bind-Host oder Port ist absichtlich nicht
unterstützt.

## Vorprüfung

Windows:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\run_temporal_light.ps1 -Action check
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\run_temporal_light.ps1 -Action describe
```

Unix:

```bash
scripts/run_temporal_light.sh check
scripts/run_temporal_light.sh describe
```

`describe` gibt nur redigierte Benutzerpfade aus. `check` verifiziert die
exakten SDK-/CLI-Pins, startet aber keinen Prozess.

## Start, Readiness und Stopp

Der Server läuft im Vordergrund und muss im selben Operator-Fenster beendet
werden. Ein unbeaufsichtigter Autostart, Windows-Dienst oder Daemon ist nicht
Teil von Temporal Light.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\run_temporal_light.ps1 -Action serve
```

In einem zweiten Fenster:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\run_temporal_light.ps1 -Action health
```

Erwartet wird ein erfolgreicher Cluster-Health-Readback an
`127.0.0.1:7233`. Beenden mit `Ctrl+C`; anschließend muss der Operator prüfen,
dass kein von diesem Lauf gestarteter `temporal`-Prozess mehr existiert.

## Persistenz-Restarttest

1. Server starten und Health-Readback abwarten.
2. Genau einen Test-Workflow in `odysseus-temporal-light` starten und seinen
   wartenden Zustand über den SDK-Client lesen.
3. Worker und Server sauber stoppen, ohne die SQLite-Datei zu löschen.
4. Denselben Server mit identischer Datenbank erneut starten.
5. Einen neuen Worker verbinden, denselben Workflow lesen, kontrolliert
   fortsetzen und sein Ergebnis abwarten.
6. Test-Worker und Server stoppen; PIDs und Health-Evidence im ABC-Runstate
   protokollieren, niemals rohe History oder private Payloads.

PASS gilt nur, wenn Workflow-ID und Zustand nach dem Serverneustart erhalten
bleiben, der Abschluss genau einmal erfolgt und ausschließlich Loopback-Listener
beobachtet wurden. Bei Portkonflikt, abweichendem Bind, Versionsdrift,
beschädigter Datenbank oder einem übrig gebliebenen Prozess gilt der Test als
FAIL und die nächste TLR-Stufe bleibt blockiert.
