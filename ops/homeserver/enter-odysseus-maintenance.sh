#!/usr/bin/env bash
set -euo pipefail

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
maintenance_dir="$HOME/.local/share/odysseus-maintenance"
service_dir="$HOME/.config/systemd/user"
backup_dir="/opt/backups/odysseus"

mkdir -p "$maintenance_dir" "$service_dir" "$backup_dir"

cat > "$maintenance_dir/index.html" <<'HTML'
<!doctype html>
<html lang="de">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="robots" content="noindex,nofollow">
    <title>Odysseus Wartung</title>
    <style>
      :root {
        color-scheme: dark;
        --bg: #10151f;
        --card: #182233;
        --text: #f4efe6;
        --muted: #b9c1cf;
        --accent: #f0b35a;
      }
      body {
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        background:
          radial-gradient(circle at 20% 20%, rgba(240, 179, 90, 0.18), transparent 28rem),
          linear-gradient(135deg, #0d1119, var(--bg));
        font-family: Georgia, "Times New Roman", serif;
        color: var(--text);
      }
      main {
        width: min(42rem, calc(100vw - 2rem));
        padding: 2.5rem;
        border: 1px solid rgba(240, 179, 90, 0.28);
        border-radius: 1.5rem;
        background: rgba(24, 34, 51, 0.86);
        box-shadow: 0 2rem 5rem rgba(0, 0, 0, 0.35);
      }
      h1 {
        margin: 0 0 1rem;
        font-size: clamp(2rem, 6vw, 4rem);
        line-height: 1;
      }
      p {
        margin: 0;
        color: var(--muted);
        font-size: 1.15rem;
        line-height: 1.6;
      }
      strong {
        color: var(--accent);
      }
    </style>
  </head>
  <body>
    <main>
      <h1>Odysseus ist kurz im Dock.</h1>
      <p><strong>Wartungsphase aktiv.</strong> Daten werden gerade sicher migriert. Bitte in ein paar Minuten erneut versuchen.</p>
    </main>
  </body>
</html>
HTML

cat > "$service_dir/odysseus-maintenance.service" <<EOF
[Unit]
Description=Odysseus maintenance page
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$maintenance_dir
ExecStart=/usr/bin/python3 -m http.server 7000 --bind 127.0.0.1
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

echo "Stopping Odysseus stack..."
systemctl --user stop odysseus-podman.service || true

echo "Creating Odysseus server-side backup..."
tar -C /opt/odysseus -czf "$backup_dir/data-before-maintenance-$timestamp.tgz" data .env docker-compose.yml

systemctl --user daemon-reload
systemctl --user enable --now odysseus-maintenance.service

echo "Maintenance mode active."
echo "Backup: $backup_dir/data-before-maintenance-$timestamp.tgz"
systemctl --user --no-pager status odysseus-maintenance.service
