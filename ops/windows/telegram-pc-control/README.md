# Windows-PC-Agent fuer Telegram-Steuerung

Der Agent baut ausschliesslich ausgehende, HMAC-signierte HTTP-Anfragen zum
Odysseus-Server auf. Auf Windows wird kein Port geoeffnet. Das gemeinsame
Agent-Secret liegt DPAPI-geschuetzt im Profil des angemeldeten Benutzers und
wird weder ausgegeben noch in das Repository geschrieben.

## Sichere Standardvariante

- Windows bleibt angemeldet, aber gesperrt.
- `/pc ruhezustand` erhaelt die Sitzung und die Codex-Anmeldung.
- `/pc an` sendet Wake-on-LAN und legt `start_codex` fuer den Agenten bereit.
- `/pc aus` funktioniert ebenfalls, nach einem echten Neustart ist jedoch eine
  Windows-Anmeldung erforderlich. Automatische Anmeldung ist absichtlich nicht
  Teil dieses Pakets.

Die installierte OpenAI-App wird ueber den stabilen AppX-Endpunkt
`OpenAI.Codex_2p2nqsd0c76g0!App` gestartet. Die aktuelle Windows-App und Codex
laufen dabei in der interaktiven Benutzersitzung.

## Installation (erst nach dem Windows-Live-GO)

In einer normalen PowerShell des Zielbenutzers:

```powershell
$secret = Read-Host 'Windows-PC-Agent-Secret' -AsSecureString
ops\windows\telegram-pc-control\install-pc-agent.ps1 `
  -ServerBaseUrl 'https://odysseus.katzarow.de' `
  -AgentSecret $secret
```

Das Secret muss exakt dem serverseitigen `TELEGRAM_PC_AGENT_SECRET`
entsprechen. Es darf nicht in Shell-History, Chat, Git oder Diagnoseausgaben
erscheinen.

Der Agent akzeptiert produktiv nur HTTPS. Unverschluesseltes HTTP ist allein
fuer einen lokalen Loopback-Test zugelassen. Der bestehende App-Port 7000 ist
vom Windows-LAN aus nicht erreichbar; der produktive Pfad verwendet daher den
bereits vorhandenen Cloudflare-Tunnel und zusaetzlich die beidseitige
HMAC-Signatur.

Offline-/Einmallauf ohne dauerhafte Schleife:

```powershell
& "$env:LOCALAPPDATA\Odysseus\PcAgent\pc-agent.ps1" -Once
```

Der Status steht in einer inhaltsfreien Datei unter dem Installationsordner.
Sie enthaelt nur feste Statuscodes und Zeitstempel.

## Rueckweg

```powershell
ops\windows\telegram-pc-control\install-pc-agent.ps1 -Uninstall
```

Der Rueckweg entfernt die geplante Aufgabe und verschiebt die Agent-Dateien in
einen zeitgestempelten `.disabled-*`-Ordner. Die DPAPI-Datei bleibt dadurch bis
zur manuellen, kontrollierten Loeschung wiederherstellbar.

## Noch manuell zu pruefen

- UEFI/BIOS: Wake-on-LAN beziehungsweise Power on by PCI-E aktivieren.
- Windows-Netzwerkadapter: `Wake on Magic Packet` aktivieren und das Aufwecken
  des Computers durch das Geraet erlauben.
- Kabelgebundenes Ethernet verwenden.
- Erst mit Ruhezustand testen; Wake aus S5/Aus ist hardwareabhaengig.
