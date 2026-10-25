# Telegram Windows PC Control: Debian-Aktivierungspaket

Dieses Paket beschreibt die spaetere Live-Aktivierung. Repository-Code allein
ist kein Beleg fuer eine aktive Installation.

## Default-off Umgebungsfelder

Nur die folgenden Namen werden benoetigt; Werte duerfen nie in Agent-Ausgaben,
Logs, Handoffs oder Git erscheinen:

- `TELEGRAM_PC_CONTROL_ENABLED=true`
- `TELEGRAM_PC_WAKE_ENABLED=true`
- `TELEGRAM_PC_MAC`
- `TELEGRAM_PC_BROADCAST`
- `TELEGRAM_PC_AGENT_SECRET`
- optional `TELEGRAM_PC_CONFIRM_TTL_SECONDS`
- optional `TELEGRAM_PC_COMMAND_TTL_SECONDS`
- optional `TELEGRAM_PC_AGENT_STALE_SECONDS`

MAC, Broadcastadresse und Secret bleiben ausschliesslich in der produktiven
Serverumgebung. Persistierter PC-Control-Zustand enthaelt diese Werte nicht.

## Aktivierungsreihenfolge

1. Aktuellen Hostzustand ausschliesslich mit
   `ssh -F ops/homeserver/ssh_config odysseus-homeserver-probe` lesen.
2. Backup-/Deploy-Gates aus `ops/homeserver/CONTEXT.md` ausfuehren.
3. Ein gemeinsames Agent-Secret interaktiv erzeugen und getrennt in die
   produktive Serverumgebung sowie per Windows-DPAPI installieren. Kein Wert
   darf im Terminal- oder Tool-Output erscheinen.
4. MAC und gerichtete LAN-Broadcastadresse serverseitig konfigurieren.
5. Gepruefte Revision deployen und Container neu erstellen.
6. Redigierte Bereitschaft erneut lesen.
7. Windows-Agent installieren und zuerst einen signierten Status-Poll pruefen.
8. Separat freigegebenen Wake-Smoke aus Ruhezustand ausfuehren.

## Stop-Regeln

- Redigierte Bereitschaft meldet fehlende Gates.
- Agent-Antwortsignatur oder Replay-Schutz schlaegt fehl.
- Der Container kann das gerichtete Wake-on-LAN-Paket nicht ins LAN senden.
- Eine Aktion wuerde einen beliebigen Shell-Befehl oder Rohfehler transportieren.
- Backup, Rueckweg oder Windows-Aufgaben-Readback ist nicht nachweisbar.

## Rueckweg

1. `TELEGRAM_PC_CONTROL_ENABLED=false` setzen und kontrolliert redeployen.
2. Windows-Aufgabe ueber das versionierte Installationsskript deaktivieren und
   Agent-Dateien in den `.disabled-*`-Ordner verschieben.
3. PC-Control-Secret auf beiden Seiten entfernen beziehungsweise rotieren. Das
   ist eine eigene Credential-Aktion mit separatem GO.
4. Bestehende Telegram-Funktionen, App-Health, Containerstatus und Version
   redigiert erneut pruefen.

## Erforderliche Live-GOs

- `GO windows-pc-agent-install` fuer die geplante Benutzeraufgabe und DPAPI-Datei.
- `GO windows-wake-settings` fuer administrative Adapter-/Energieaenderungen.
- `GO debian-pc-control-deploy` fuer Backup, Env-Aenderung, Deploy und Readback.
- `GO telegram-pc-control-live-smoke` fuer genau einen Wake-/Status-/Ruhezustand-Test.
