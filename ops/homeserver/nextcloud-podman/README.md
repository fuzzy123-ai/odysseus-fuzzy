# Nextcloud Podman Stack

Rootless Podman stack for the Debian homeserver.

The stack binds Nextcloud to `127.0.0.1:8080` by default. Keep it private and
publish it later through Tailscale, an SSH tunnel, or a reverse proxy.

Services:

- `nextcloud-app`: official Nextcloud Apache image.
- `nextcloud-cron`: official Nextcloud cron worker image using the same app/data volumes.
- `nextcloud-db`: MariaDB 11.
- `nextcloud-redis`: Redis cache.

Data lives in named Podman volumes:

- `nextcloud-app`
- `nextcloud-data`
- `nextcloud-db`
- `nextcloud-redis`

Operational commands on the server:

```bash
cd /opt/nextcloud
podman-compose up -d
podman-compose ps
podman-compose logs -f nextcloud-app
```

Local access test from the server:

```bash
curl -I http://127.0.0.1:8080
```

Remote access test through SSH tunnel:

```bash
ssh -L 8080:127.0.0.1:8080 homebase@192.168.178.122
```

Then open `http://127.0.0.1:8080`.
