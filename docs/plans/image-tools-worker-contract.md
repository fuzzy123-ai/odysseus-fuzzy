# Image Tools Worker Contract

Stand: 2026-06-16

Status: **geplanter Stabilisierungstrack fuer Background Removal und spaetere Image-AI-Tools**

## Ausgangslage

Odysseus hat bereits Background Removal in mehreren Pfaden:

- Frontend: `static/js/editor/ai-rembg.js` ruft `/api/image/remove-bg` auf und legt Ergebnisse als neue Layer an.
- Shared Runner: `static/js/editor/ai-tool-runner.js` kennt `rembg` als optionale Dependency.
- Agent Tool: `edit_image` unterstuetzt `rembg`.
- Backend: `routes/gallery_routes.py` implementiert `/api/image/remove-bg` und versucht `from rembg import remove`, danach Fallback ueber `transformers`/`briaai/RMBG-1.4`.

Das aktuelle Risiko ist die Dependency-Grenze: Die Odysseus-Haupt-venv laeuft auf Python 3.14, waehrend `rembg` aktuell nicht sauber in diesen Core passt. Background Removal darf den Odysseus-Core deshalb nicht destabilisieren.

## Zielentscheidung

`rembg` wird nicht in die Odysseus-Haupt-venv gepresst.

Stattdessen bekommt Odysseus einen isolierten `image_tools_worker`:

- eigener Python-3.12-venv-Worker oder Docker-Container
- kleiner interner HTTP- oder Subprocess-Adapter
- stabile Core-API: `remove_background(image, hint_mask=None) -> png`
- klare Fehlermeldung, wenn Worker fehlt, statt Serverfehler

Odysseus Core bleibt frei von harten `rembg`, `transformers` oder GPU-Abhaengigkeiten.

## Capabilities

Initial:

- `remove_background`

Spaeter optional:

- `sharpen`
- `upscale`
- `inpaint`

Neue Capabilities duerfen erst nach eigenem Contract und Tests in die UI.

## Runtime-Modi

| Mode | Bedeutung | Default |
| --- | --- | --- |
| `disabled` | Image Tools sind deaktiviert; UI/API melden Setup-Hinweis | ja |
| `local-venv` | Worker laeuft lokal in eigener Python-3.12-venv | nein |
| `docker` | Worker laeuft isoliert im Container | nein |
| `remote` | spaeterer Remote-Worker, nur mit Security-Gate | nein |

## Config Keys

- `IMAGE_TOOLS_WORKER_MODE`
- `IMAGE_TOOLS_WORKER_URL`
- `IMAGE_TOOLS_WORKER_TIMEOUT_SEC`
- `IMAGE_TOOLS_WORKER_MAX_MB`
- optional spaeter: `IMAGE_TOOLS_WORKER_ALLOW_LEGACY_FALLBACK`

Default bleibt sicher: Worker aus, kein harter Core-Import.

## Fehlersemantik

| Fehler | Bedeutung |
| --- | --- |
| `not_configured` | Worker ist deaktiviert oder URL fehlt |
| `dependency_missing` | Worker laeuft, aber Capability/Dependency fehlt |
| `worker_unreachable` | Worker ist nicht erreichbar |
| `timeout` | Worker antwortet nicht innerhalb des Budgets |
| `invalid_image` | Eingabe ist kein gueltiges Bild oder zu gross |
| `invalid_response` | Worker-Antwort ist nicht interpretierbar |

UI/API sollen diese Fehler in nutzbare Setup- oder Retry-Hinweise uebersetzen.

## Security

- Default-URL ist nur `localhost`/Loopback.
- Payload-Groesse wird ueber `IMAGE_TOOLS_WORKER_MAX_MB` begrenzt.
- Privilege-Gate bleibt `can_generate_images`.
- Worker darf keine Dateipfade aus dem Nutzerinput blind lesen oder schreiben.
- Core sendet Bytes, nicht beliebige lokale Pfade.
- Legacy-Fallbacks im Core sind nur explizit erlaubt, nicht Default.

## Slices

| Slice | Ziel | Alice | Bob | Charlie | Parallel? |
| --- | --- | --- | --- | --- | --- |
| `ITW1-worker-contract-config` | Contract, Modi, Config, Fehlersemantik und Security festlegen | Contract/Setup-Sprache | technische Reihenfolge reviewen | Roadmap einordnen, Scope sperren | ja, Doku |
| `ITW2-backend-adapter-service` | Core-seitiger Worker-Client ohne harte Image-Dependencies | Fehlermeldungs-/UI-Text pruefen | `src/image_tools_worker.py`, Tests fuer disabled/unreachable/timeout/success | Tests/Gates, keine `rembg`-Imports im Core | ja nach Contract |
| `ITW3-route-integration` | `/api/image/remove-bg` nutzt Worker-Client | Antworttexte/Setup-Link pruefen | vorsichtige Route-Integration | Hotfile `routes/gallery_routes.py` koordinieren | nein oder eng sequenziell |
| `ITW4-worker-mvp` | isolierter Python-3.12-Worker fuer `rembg[cpu]` | README/Installpfad pruefen | `workers/image_tools_worker/*` | Security-/Ops-Review | ja, isoliert |
| `ITW5-cookbook-ui-alignment` | Cookbook/UI erklaert Worker statt Core-Dependency | Copy, Windows-/Docker-/venv-Erklaerung | kleine UI/Status-Anpassungen | keine widerspruechlichen Installhinweise | bedingt |
| `ITW6-telegram-readiness` | Telegram-Bildaktionen nutzen denselben Client | Nutzertexte fuer Telegram-Antworten | Bridge nutzt `ImageToolsWorkerClient` | erst beim Telegram-Plan aktivieren | nein |

## Akzeptanzkriterien

- Odysseus startet ohne `rembg`, `transformers` oder Image Worker sauber.
- Background-Removal-UI zeigt einen klaren Status statt kryptischem Fehler.
- Wenn Worker konfiguriert ist, funktioniert `/api/image/remove-bg` weiter fuer den Editor.
- Python-3.14-Core bleibt frei von harter `rembg`-Dependency.
- Tests decken disabled/missing/unreachable/timeout/success ab.
- Security-Gate `can_generate_images` bleibt erhalten.
- Windows-Story ist nicht mehr widerspruechlich: Core install unsupported, Worker via Python 3.12 venv oder Docker supported.

## Nicht-Ziele

- keine `rembg`-Installation in die Haupt-venv erzwingen
- keine GPU-Pflicht
- keine Telegram-Image-Actions direkt in diesem Track implementieren
- keine neuen Gallery-Editor-Features ausser Status-/Setup-Klarheit
- keine Remote-Worker-Freigabe ohne separaten Security-Gate

## Startbedingung

Dieser Track startet erst, wenn:

- die aktive Lens-/Hotfile-Arbeit sauber committed ist,
- `routes/gallery_routes.py` nicht parallel von einem anderen Slice bearbeitet wird,
- und Background Removal oder Telegram-Image-Actions vor dem naechsten Release priorisiert werden.
