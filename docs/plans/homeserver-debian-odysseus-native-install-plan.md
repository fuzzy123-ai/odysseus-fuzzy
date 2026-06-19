# Homeserver Debian Odysseus Podman Install Plan

Ziel: Debian-Homeserver so vorbereiten, dass Codex von der aktuellen Maschine per SSH arbeiten kann und Odysseus anschliessend ohne Docker, aber mit einer Docker-kompatiblen Alternative betrieben wird.

Stand: 2026-06-18

## Leitentscheidungen

- Debian ist das Zielsystem.
- Docker wird nicht verwendet.
- Bevorzugte Betriebsart: Podman als Docker-kompatible Container-Runtime.
- Compose-Dateien aus dem Odysseus-Repo werden nach Moeglichkeit mit `podman compose` oder `podman-compose` genutzt.
- Native Python/systemd-Installation bleibt nur Fallback, falls Podman/Compose mit dem Projekt nicht sauber laeuft.
- Codex wird nicht auf dem Homeserver installiert, solange die Einrichtung per SSH von hier aus moeglich ist.
- Zugriff auf Odysseus nicht direkt aus dem Internet freigeben.
- SSH-Key statt Passwort fuer Adminzugriff.
- Firewall standardmaessig restriktiv.
- Naechtliches Wartungsfenster ist 03:00-06:00 Europe/Berlin.
- Wenn Podman gewaehlt wird, laufen Odysseus, ChromaDB, SearXNG und ntfy ueber Compose-kompatible Container mit loopback/private Bindings.
- Wenn native Installation gewaehlt wird, laeuft ChromaDB als eigener lokaler Dienst auf `127.0.0.1:8100`.
- Odysseus wird nicht oeffentlich gebunden; Zugriff erfolgt spaeter ueber Tailscale, SSH-Tunnel oder Reverse Proxy.

## Vorbedingungen

Vom Benutzer bereitzustellen:

- Server-IP oder Hostname.
- Linux-Benutzername.
- SSH-Port, falls nicht `22`.
- Bestaetigung, dass der Benutzer `sudo` darf.
- SSH-Public-Key auf dem Server in `~/.ssh/authorized_keys`.
- Entscheidung, ob Zugriff primaer ueber Tailscale laufen soll.

Erster Verbindungstest:

```bash
ssh user@server
sudo -v
```

## Phase 1: Systembasis

Ziel: Debian aktualisieren, Grundwerkzeuge installieren, Host eindeutig benennen.

Pakete:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y \
  sudo curl wget git ca-certificates gnupg lsb-release \
  tmux htop btop unzip rsync \
  nvme-cli smartmontools \
  ufw fail2ban unattended-upgrades \
  python3 python3-venv python3-pip build-essential \
  openssh-server
```

Basiseinstellungen:

```bash
sudo timedatectl set-timezone Europe/Berlin
sudo hostnamectl set-hostname homeserver
sudo systemctl enable --now ssh
sudo dpkg-reconfigure -plow unattended-upgrades
```

Pruefen:

```bash
cat /etc/os-release
uname -a
ip addr
df -h
lsblk
free -h
```

## Phase 2: SSH und Firewall

Ziel: Remote-Zugriff stabil halten und eingehende Ports bewusst begrenzen.

Firewall:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw enable
sudo ufw status verbose
```

SSH-Haertung nach erfolgreichem Key-Test:

```bash
sudo sshd -t
sudo systemctl reload ssh
```

Empfohlene SSH-Policy in `/etc/ssh/sshd_config.d/homeserver.conf`:

```text
PasswordAuthentication no
PermitRootLogin no
PubkeyAuthentication yes
```

Wichtig: Passwort-Login erst deaktivieren, wenn SSH-Key-Login in einer zweiten Session erfolgreich getestet wurde.

## Phase 3: Optionaler privater Zugriff per Tailscale

Ziel: Server privat erreichbar machen, ohne Odysseus oeffentlich freizugeben.

Installation:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4
```

Danach kann die Firewall spaeter optional auf Tailscale-Zugriff eingeschraenkt werden. Erst nach stabilem Test anfassen.

## Phase 4: Podman als Container-Runtime

Ziel: Docker vermeiden und Podman als Container-Runtime fuer Odysseus vorbereiten.

Pakete:

```bash
sudo apt install -y podman podman-compose
```

Pruefen:

```bash
podman --version
podman-compose --version
podman info
```

Offene technische Entscheidung:

- Rootless Podman als `homebase`, wenn Compose-Netzwerk und Volume-Rechte sauber laufen.
- Rootful Podman ueber `sudo`, wenn der Stack rootless Probleme mit Ports, Volumes oder Berechtigungen macht.
- Langfristig systemd-Integration ueber Podman Quadlet oder generierte systemd-Units.

## Phase 5A: Odysseus per Podman Compose installieren

Ziel: Odysseus mit Podman statt Docker betreiben.

- Repository unter `/opt/odysseus` klonen.
- `.env` fuer private Bindings setzen.
- Compose-Kompatibilitaet pruefen.
- `podman-compose up -d --build` oder `podman compose up -d --build`.
- Health Checks fuer `odysseus`, `chromadb`, `searxng`, `ntfy`.
- systemd-Autostart fuer den Podman-Stack einrichten.

## Phase 5B: Odysseus nativ installieren

Ziel: Fallback, falls Podman fuer diesen Stack nicht tragfaehig ist: Odysseus in `/opt/odysseus` installieren und mit Python-venv betreiben.

Repository:

```bash
cd /opt
sudo git clone https://github.com/pewdiepie-archdaemon/odysseus.git
sudo chown -R "$USER:$USER" /opt/odysseus
cd /opt/odysseus
```

Python-Umgebung:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -U pip
pip install -r requirements.txt
cp .env.example .env
python setup.py
```

Empfohlene `.env`-Werte fuer den Start:

```env
AUTH_ENABLED=true
APP_BIND=127.0.0.1
APP_PORT=7000
CHROMADB_HOST=localhost
CHROMADB_PORT=8100
LOCALHOST_BYPASS=false
```

## Phase 6B: ChromaDB Dienst bei nativer Installation

Ziel: ChromaDB persistent und lokal starten.

Manueller Smoke-Test:

```bash
cd /opt/odysseus
source venv/bin/activate
mkdir -p data/chroma logs
venv/bin/chroma run --path data/chroma --host 127.0.0.1 --port 8100
```

Systemd-Service `/etc/systemd/system/odysseus-chroma.service`:

```ini
[Unit]
Description=Odysseus ChromaDB
After=network.target

[Service]
Type=simple
User=REPLACE_USER
WorkingDirectory=/opt/odysseus
ExecStart=/opt/odysseus/venv/bin/chroma run --path /opt/odysseus/data/chroma --host 127.0.0.1 --port 8100
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

## Phase 7B: Odysseus Dienst bei nativer Installation

Ziel: Odysseus automatisch starten und nur lokal binden.

Manueller Smoke-Test:

```bash
cd /opt/odysseus
source venv/bin/activate
python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

Systemd-Service `/etc/systemd/system/odysseus.service`:

```ini
[Unit]
Description=Odysseus UI
After=network.target odysseus-chroma.service
Requires=odysseus-chroma.service

[Service]
Type=simple
User=REPLACE_USER
WorkingDirectory=/opt/odysseus
EnvironmentFile=/opt/odysseus/.env
ExecStart=/opt/odysseus/venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Aktivierung:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now odysseus-chroma
sudo systemctl enable --now odysseus
sudo systemctl status odysseus-chroma
sudo systemctl status odysseus
```

## Phase 8: Zugriff auf Odysseus

Option A: SSH-Tunnel fuer ersten Test:

```bash
ssh -L 7000:127.0.0.1:7000 user@server
```

Dann lokal oeffnen:

```text
http://127.0.0.1:7000
```

Option B: Tailscale:

- Odysseus weiter auf `127.0.0.1` lassen und einen Reverse Proxy auf Tailscale-IP setzen.
- Alternativ spaeter gezielt `APP_BIND` auf Tailscale-IP setzen, nicht auf eine oeffentliche Adresse.

Option C: Reverse Proxy mit HTTPS:

- Caddy oder nginx vor Odysseus.
- Proxy-Ziel bleibt `http://127.0.0.1:7000`.
- `AUTH_ENABLED=true`, `LOCALHOST_BYPASS=false`, `SECURE_COOKIES=true`.

## Phase 9: Health Checks und Betrieb

Pruefen:

```bash
systemctl is-active odysseus-chroma
systemctl is-active odysseus
journalctl -u odysseus -n 100 --no-pager
journalctl -u odysseus-chroma -n 100 --no-pager
curl -I http://127.0.0.1:7000
```

Storage und SSD:

```bash
lsblk
df -h
sudo smartctl -a /dev/nvme0n1
sudo smartctl -a /dev/nvme1n1
```

Backups spaeter planen:

- `/opt/odysseus/data/`
- `/opt/odysseus/.env`
- `/opt/odysseus/logs/` optional
- ChromaDB liegt bei nativer Installation unter `/opt/odysseus/data/chroma/`.

## Phase 10: Naechtliche Wartungsphase

Ziel: Wartung nicht zufaellig im Tagesbetrieb ausfuehren, sondern in einem festen Fenster buendeln.

Standardfenster:

```text
03:00-06:00 Europe/Berlin
```

Empfohlene Reihenfolge:

1. 03:00-03:30: Odysseus-Backup, Nextcloud-/DB-Backup, Backup-Verify.
2. 03:30-04:15: Debian Security Updates, Podman Image Pulls, kontrollierte Container-Restarts.
3. 04:15-05:30: Inbox-Import, Indexpflege, RAG-/Graph-Maintenance, kleinere Rebuild-Jobs.
4. 05:30-06:00: Healthchecks, Log-Summary, Speicher-/SMART-Pruefung, Alerts.

Systemd-Timer sind fuer den Homeserver besser als verstreute Cronjobs, weil sie Logs, Status und Fehler sauber ueber `journalctl` sichtbar machen.

Zielarchitektur:

```text
odysseus-maintenance.timer
  -> odysseus-maintenance.service
       -> /usr/local/sbin/odysseus-nightly-maintenance
```

Timer-Policy:

```ini
[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true
RandomizedDelaySec=10m
```

Script-Policy fuer `/usr/local/sbin/odysseus-nightly-maintenance`:

- bricht ab, wenn ein Lockfile bereits existiert.
- startet keine Major-Upgrades.
- startet keine schweren Jobs nach 05:30.
- fuehrt Updates nur aus, wenn Backup und Verify erfolgreich waren.
- schreibt ein kurzes Summary nach `/var/log/odysseus-maintenance.log`.
- prueft danach Nextcloud, Odysseus, Podman-Container und Speicherlage.

Beispiel-Kommandos fuer spaetere Umsetzung:

```bash
systemctl list-timers '*odysseus*'
journalctl -u odysseus-maintenance.service -n 100 --no-pager
```

## Offene Entscheidungen

- Soll der Server per Tailscale angebunden werden?
- Soll Podman rootless oder rootful betrieben werden?
- Soll der Podman-Stack per `podman-compose`, `podman compose` oder Quadlet/systemd verwaltet werden?
- Soll native Python/systemd-Installation nur als Fallback dokumentiert bleiben?
- Soll Odysseus langfristig nur per SSH-Tunnel, Tailscale oder Reverse Proxy erreichbar sein?
- Wurde Debian mit RAID1/Spiegelung auf den zwei 500-GB-NVMe installiert?
- Soll ein separater Benutzer `odysseus` statt des normalen Admin-Benutzers verwendet werden?
- Welche lokalen oder externen Modellanbieter sollen spaeter angebunden werden: Ollama, OpenAI API, remote GPU-Host?
- Welche Jobs duerfen in der naechtlichen Wartungsphase automatisch laufen und welche brauchen manuelles Go?

## Abschlusskriterien

- SSH-Key-Login funktioniert.
- `sudo` funktioniert fuer den gewaehlten Benutzer.
- Firewall ist aktiv und blockiert ungewollte eingehende Ports.
- Systemupdates und Sicherheitsupdates sind eingerichtet.
- Naechtliches Wartungsfenster 03:00-06:00 Europe/Berlin ist dokumentiert und ueber Timer/Cron umsetzbar.
- Podman wurde als Docker-Alternative bestaetigt.
- Odysseus-Repository liegt unter `/opt/odysseus`.
- Bei Podman-Betrieb: Compose-kompatibler Stack laeuft gesund.
- Bei nativer Installation: Python-venv und Requirements sind installiert.
- Bei nativer Installation: `python setup.py` wurde erfolgreich ausgefuehrt.
- Bei nativer Installation: ChromaDB laeuft als systemd-Service.
- Bei nativer Installation: Odysseus laeuft als systemd-Service.
- Zugriff auf `http://127.0.0.1:7000` funktioniert via SSH-Tunnel oder privatem Netz.
- Admin-Login wurde erfolgreich getestet.
