# Debian-Server: Codex-Handoff

Stand der Verifikation: **2026-07-17 (Europe/Berlin)**

Dieses Handoff macht `ops/homeserver` zum eindeutigen Codex-Projekteinstieg
fuer den Debian-Homeserver. `AGENTS.md` wird von Codex automatisch als
dauerhafte Projektanweisung geladen. Dieses Dokument liefert die verifizierte
Zugangskette und den Betriebsrahmen; `CONTEXT.md` enthaelt die ausfuehrlichen
Odysseus-Runbooks.

## Verifizierte Zielidentitaet

- SSH-Alias: `odysseus-homeserver`
- Host: `192.168.178.122`
- SSH-Benutzer: `homebase`
- Server-Hostname: `debian`
- Betriebssystem: `Debian GNU/Linux 13 (trixie)`
- Verifizierter Kernel: `Linux 6.12.90+deb13.1-amd64`
- Odysseus-Root: `/opt/odysseus`
- Runtime: rootless Podman `5.4.2`
- Compose: `podman-compose 1.3.0`
- User-Service: `odysseus-podman.service` war bei der Verifikation `active`

Die SSH-Anmeldung mit der unten beschriebenen Konfiguration wurde am
2026-07-17 erfolgreich und rein lesend getestet. Live-Daten koennen sich
aendern und sind zu Beginn jeder neuen Aufgabe erneut zu pruefen.

## Wo Codex die Zugangsdaten findet

Die kanonische Zugangskette ist absichtlich zweistufig:

1. `ops/homeserver/ssh_config` definiert Alias, Host, Benutzer und Optionen.
2. Dessen `IdentityFile` verweist auf
   `C:/Users/nkatz/odysseus/data/ssh/id_ed25519_odysseus_homeserver_20260620_host`.

Der verifizierte ED25519-Fingerprint dieses privaten Schluessels ist:

```text
SHA256:gIo9i+URGIJy+E3nG1F8YsMh0rcW4ZNiskZ9xeDx2xA
```

Er stimmt mit dem kanonischen oeffentlichen Schluessel
`authorized_keys/odysseus-homeserver-20260620.pub` ueberein. `data/` ist durch
die Repository-`.gitignore` ausgeschlossen. Der private Schluessel bleibt
trotzdem geheim: nicht lesen, ausgeben, duplizieren, hochladen oder committen.
Es ist kein Passwort-Handoff erforderlich und es soll nicht nach einem Passwort
geraten werden. `BatchMode yes` und `IdentitiesOnly yes` in `ssh_config` sorgen
fuer einen eindeutigen, nicht-interaktiven Zugang.

## Verbindung und Identitaetspruefung

Wenn Codex mit diesem Ordner als Arbeitsverzeichnis gestartet wurde:

```powershell
ssh -F ssh_config odysseus-homeserver 'id -un; hostname; grep PRETTY_NAME /etc/os-release'
```

Vom Repository-Root `C:\Users\nkatz\odysseus`:

```powershell
ssh -F ops/homeserver/ssh_config odysseus-homeserver 'id -un; hostname; grep PRETTY_NAME /etc/os-release'
```

Erwartete Identitaet: Benutzer `homebase`, Hostname `debian`, Debian 13. Weicht
einer dieser Werte ab, sofort stoppen und die Abweichung melden; keine
Aenderungen ausfuehren.

## Verifizierter Runtime-Snapshot

Bei der letzten rein lesenden Pruefung waren folgende Container aktiv:

- `odysseus_odysseus_1`
- `odysseus_chromadb_1`
- `odysseus_searxng_1`
- `odysseus_ollama_1`
- `odysseus_ntfy_1`
- `nextcloud-app`, `nextcloud-cron`, `nextcloud-db`, `nextcloud-redis`

Der Server-Checkout befand sich dabei auf Branch `dev` bei Kurz-Commit
`61442e80`. Das ist nur ein datierter Snapshot, keine Sollvorgabe. Vor jeder
Git-, Update- oder Deploy-Aufgabe Branch, Commit und Worktree erneut lesen.

## Sichere Arbeitsweise

1. Aufgabe und betroffenen Dienst bestimmen.
2. Nur die dafuer notwendigen Live-Fakten per SSH lesen.
3. Geheimnisse und private Inhalte bereits an der Quelle ausfiltern.
4. Bei einer Aenderung einen konkreten Befehlsplan mit Auswirkung, Backup,
   Rueckweg und Verifikation vorlegen und die erforderliche Freigabe abwarten.
5. Die kleinste freigegebene Aenderung ausfuehren.
6. Dienststatus, Containerstatus, Healthcheck und Git-Zustand verifizieren.
7. Ergebnis, Abweichungen und verbleibende Risiken redigiert dokumentieren.

Wichtige Betriebsregeln:

- Podman verwenden; keine Docker-Annahmen.
- User-Units mit `systemctl --user` behandeln.
- Die produktive `.env` liegt unter `/opt/odysseus/.env`. Nie komplett ausgeben.
- Vor Update oder Deploy die vorhandenen Backup- und Restore-Skripte sowie die
  Gates in `CONTEXT.md` beachten.
- Ports und Dienste nicht ohne separate Freigabe exponieren, installieren,
  neu starten oder rekonfigurieren.
- Bei Produktionsfehlern nicht vom lokalen Windows-Zustand auf den Server
  schliessen.

## Wenn der Zugang nicht funktioniert

- Meldet SSH `Permission denied (publickey,password)`, keine anderen Identitaeten
  oder Passwoerter ausprobieren. Existenz des in `ssh_config` genannten Keys und
  seinen Fingerprint pruefen; dann stoppen und den redigierten Fehler melden.
- Scheitert der Zugriff bereits vor der Authentifizierung, vom Windows-Rechner
  zuerst TCP/22 mit dem in `CONTEXT.md` dokumentierten `Test-NetConnection`-
  Befehl pruefen.
- `repair-ssh-access.sh` ist ein Konsolen-Reparaturweg, kein automatisch
  auszufuehrender Diagnosebefehl. SSH-/Firewall-Reparaturen brauchen ein
  ausdrueckliches Go.
- Verweigert nur die Codex-Sandbox den Zugriff auf Key oder Netzwerk, eine eng
  begrenzte Freigabe fuer `ssh -F ...` anfordern. Den Schluessel nicht an einen
  anderen Ort kopieren.

## Quellen in diesem Projektordner

- `AGENTS.md`: automatisch geladene Arbeitsregeln fuer Codex
- `CONTEXT.md`: Live-Host-Kontext, Incident-Befehle, Updates, Telegram und
  Wartungsgrenzen
- `ssh_config`: kanonische SSH-Aufloesung
- `authorized_keys/`: kanonischer oeffentlicher Schluessel
- `*.sh` und `run-sandbox-job.py`: bestehende, aufgabenspezifische Runbooks und
  Hilfsprogramme; vor Verwendung immer lesen
