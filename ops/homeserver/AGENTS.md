# Codex-Anweisungen fuer den Debian-Homeserver

Dieser Ordner ist der Codex-Projekteinstieg fuer den produktiven Debian-
Homeserver. Er ist kein zweiter lokaler Odysseus-Checkout.

## Pflichtlektuere

Lies vor jeder Serverarbeit in dieser Reihenfolge:

1. `HANDOFF.md`
2. `CONTEXT.md`
3. `ssh_config`
4. die fuer die Aufgabe relevanten Skripte in diesem Ordner

Aktuelle Serverzustaende muessen per SSH verifiziert werden. Nie aus dem
Windows-Checkout, alten Handoffs oder Modellwissen auf den Live-Zustand
schliessen.

## Kanonischer Zugang

Aus diesem Ordner:

```powershell
ssh -F ssh_config odysseus-homeserver '<read-only-command>'
```

Aus dem Repository-Root:

```powershell
ssh -F ops/homeserver/ssh_config odysseus-homeserver '<read-only-command>'
```

`ssh_config` ist die einzige Quelle fuer Host, Benutzer und Identity-Datei.
Host, Benutzer oder Schluesselpfad nicht von Hand ersetzen. Der private
Schluessel darf niemals angezeigt, kopiert, in Chat-Ausgaben aufgenommen oder
Git hinzugefuegt werden. Falls die Sandbox den Schluessel oder das Netzwerk
blockiert, nur eine eng begrenzte Freigabe fuer den exakten SSH-Befehl
anfordern; keine Ersatzkopie des Schluessels anlegen.

## Arbeitsvertrag

- Standard ist zuerst eine rein lesende Bestandsaufnahme.
- Produktionspfad ist `/opt/odysseus`; primaerer Benutzer ist `homebase`.
- Der Host nutzt rootless Podman und `podman-compose`, nicht Docker.
- Fuer Produktionsprobleme zuerst den Debian-Host untersuchen. Lokale Windows-
  Prozesse und die lokale `.env` sind kein Produktionsbeleg.
- Vor Aenderungen den Ist-Zustand, den genauen Befehl, die Auswirkung, den
  Rueckweg und die Verifikation nennen.
- Installation, Update, Deploy, Neustart, Container-Recreate, Firewall-, SSH-,
  systemd-, Benutzer-, Rechte-, Datenbank-, Netzwerk- oder `.env`-Aenderungen
  brauchen eine ausdrueckliche Freigabe fuer diese konkrete Aktion.
- Vor Updates oder Deployments die vorhandenen Backup-/Restore-Gates in diesem
  Ordner verwenden. Eine allgemeine Arbeitsfreigabe ersetzt kein Deploy-Go.
- `sudo` nicht verwenden, sofern es fuer die konkrete Aktion nicht ausdruecklich
  freigegeben wurde. Bevorzuge User-Services mit `systemctl --user`.
- Keine Geheimnisse, Tokens, Passwoerter, Chat-IDs oder privaten Rohinhalte
  ausgeben. `/opt/odysseus/.env` niemals komplett anzeigen; nur benoetigte
  Variablennamen beziehungsweise Vorhandensein redigiert pruefen.
- Keine destruktiven oder irreversiblen Befehle ohne explizite Freigabe und
  verifizierten Rueckweg.
- Nach jeder freigegebenen Aenderung Dienststatus, Containerstatus, betroffenen
  Healthcheck und gegebenenfalls Git-Zustand erneut pruefen.

## Erstdiagnose

Beginne bei unbekanntem Zustand mit kleinen, lesenden Befehlen wie:

```bash
id -un
hostname
grep '^PRETTY_NAME=' /etc/os-release
git -C /opt/odysseus status --short --branch
systemctl --user --no-pager status odysseus-podman.service
podman ps --format '{{.Names}} {{.Status}} {{.Ports}}'
```

Fuehre nur die fuer die Aufgabe notwendigen Abfragen aus und redigiere Ausgaben,
bevor sie in ein Handoff oder einen Bericht gelangen.

## Fehlergrenze

Wenn SSH-Authentifizierung fehlschlaegt, nicht auf Passwortauthentifizierung
ausweichen und nicht am Windows-Checkout weiterdiagnostizieren. Folge dem
Abschnitt `Access Note` in `CONTEXT.md` und melde die genaue, redigierte
Fehlerklasse. Reparaturen an SSH oder Firewall erfordern eine eigene Freigabe.

