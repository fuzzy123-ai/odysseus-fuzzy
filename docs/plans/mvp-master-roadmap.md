# Odysseus MVP MasterRoadmap

Stand: 2026-06-23

Status: **aktive MVP-Konsolidierungsroadmap**

Diese Roadmap fasst die offenen Odysseus-Arbeiten zu einem MVP zusammen. Sie
ordnet die zuvor verstreuten Roadmaps nach Produktwert, Abhaengigkeiten und
Integrationsrisiko.

## Leitentscheidung

Das MVP besteht aus den Prioritaeten 1-10. Die Punkte 11 und 12 bleiben
Post-MVP-Ideen und werden erst relevant, wenn der Kern stabil laeuft.

MVP heisst hier:

```text
laufender Homeserver + belastbare Backend-Logik + sichere Datenpfade +
Runtime-Gates + ehrliche Evidence
```

UI-Arbeit ist fuer diese MVP-Phase kein Treiber. Bestehende UI wird nur dann
angefasst, wenn Backend-Logik sonst nicht pruefbar oder bedienbar waere. Die
eigentliche Gestaltung neuer Oberflaechen passiert spaeter gemeinsam.

## Quellenlage

- `specs/roadmaps/odysseus-multiagent-roadmap.v1.json` bleibt die operative
  strukturierte Quelle fuer PlanRuntime und Roadmap Lens.
- `docs/plans/unified-odysseus-roadmap.md` bleibt historischer und fachlicher
  Master-Kontext.
- Diese MVP-MasterRoadmap ist die menschliche Priorisierung fuer die naechste
  Konsolidierungsphase.

Wenn diese Roadmap mit Detailplaenen kollidiert, gilt:

1. Sicherheits- und Stop-Regeln aus den Detailplaenen bleiben gueltig.
2. Die Reihenfolge dieser MVP-Roadmap entscheidet, was als naechstes
   zusammengefuehrt wird.
3. JSON-PlanRuntime-State entscheidet, ob ein konkreter Node wirklich
   claimable ist.

## MVP Prioritaeten

| Prio | Track | Status | Aufwand 0-10 | MVP-Ziel | Done wenn |
| ---: | --- | --- | ---: | --- | --- |
| 1 | Runtime Closure Gates | 100% / go | 5 | Updates-/Backup-Logik, MCP Production Smoke und Telegram Text Runtime Smoke werden als getrennte Backend-/Runtime-Gates geschlossen. | Live-Smokes sind redacted dokumentiert oder explizit Partial/No-Go; kein Gate impliziert ein anderes. |
| 2 | Secure Data Mode Runtime Hooks | 100% / go | 7 | Sensible Quellen, Secure Chats und Local-only Policy greifen an den echten Runtime-Grenzen. | Provider, Retrieval, Telegram und private Quellen respektieren Policy Gates; unsichere Faelle blockieren oder gehen in Review. |
| 3 | Private Data / Nextcloud Memory Ingestion | 100% / go | 9 | Nextcloud/private Daten werden resumable, provenance-aware und ohne Raw-Content-Leaks in Memory vorbereitet. | Transfer-Readiness, Privacy-Partition, Scanner-Dry-Run, Ledger und kleine Live-Smokes sind gated; Full Corpus Transfer bleibt eine bewusste Folgeentscheidung nach Regeldefinition. |
| 4 | System Health Checker Host-Agent | 100% / go | 8 | Homeserver Health wird ueber einen getrennten Host-Agent und bereinigte APIs sichtbar, nicht ueber versteckte Core-Kommandos. | Debian Host-Agent liefert bereinigte Snapshots; Odysseus verarbeitet Health/Alerts ohne Root-, Socket- oder Secret-Leak. |
| 5 | Telegram Voice Pipeline | 90% / needs_live_go | 7 | Voice wird von Metadata-only zu fake-tested Download/STT/Reply-Pipeline erweitert. | Download und STT bleiben default-off und separat gated; Fake-Provider-Tests belegen Transcript-to-Agent und Reply-Pfad. |
| 6 | ORCA / Lens Naming & Backend Migration | 80% / needs_design | 7 | Obsidian-zentrierte Backend-, Route-, Tool-, Env- und Datenpfad-Begriffe werden zu ORCA/Lens kompatibel gemacht, ohne harte Breaking Changes. | Kompatibilitaetsadapter und Alias-Regeln sind getestet; interne Core-Module koennen schrittweise von Legacy-Namen entkoppelt werden. |
| 7 | PlanRuntime / Visual Planning Logic | 92% / needs_design | 6 | Die Roadmap-/PlanRuntime-Logik fuer Vorschlaege, Validierung, Review, Patch, Apply und Agent-Start-Gates wird stabilisiert. | Vorschlaege koennen ohne UI-Abhaengigkeit validiert, reviewed, gepatcht und sicher blockiert oder angenommen werden; kein impliziter Agent-Dispatch. |
| 8 | Release / Distribution Evidence | 82% / needs_live_go | 5 | Evidence, Known Limits und Release-Sprache werden ehrlich aus Backend-/Runtime-Gates aggregiert. | 1.0/externes Release kann als Go, Partial oder No-Go begruendet werden, ohne Runtime-Gates zu ueberzeichnen. |
| 9 | Image Tools Worker Final Smoke | 100% / go | 3 | Der isolierte Image Tools Worker wird praktisch nachgewiesen. | Finaler Remove-BG/Image-Smoke ist dokumentiert oder klar deferred; Core-venv bleibt entkoppelt. |
| 10 | GameDev Mount Write Smoke | 100% / go | 2 | Der optionale Schreibpfad fuer GameDev-Mounts wird eng und reversibel belegt. | Write-Smoke erfolgt nur mit explizitem Go, schreibt ein reversibles Testartefakt und leakt keine Host-Pfade. |

## Post-MVP Ideen

| Prio | Track | Status | Warum spaeter |
| ---: | --- | --- | --- |
| 11 | GitHub Issue Intelligence | design-ready | Neues Feature mit Sync, Persistence, Embeddings, UI und MCP-Gates. Wertvoll, aber nicht noetig, um den Odysseus-Kern zusammenzubringen. |
| 12 | Qdrant/Kuzu/UMAP/GMM Research | deferred | Infrastruktur- und Research-Arbeit nur starten, wenn Diagnostics echte Performance- oder Qualitaetsluecken zeigen. |

## UI-Gestaltung Spaeter

Diese Flaechen werden bewusst nicht als MVP-Blocker behandelt:

- Roadmap Lens / Visual Agent Programming Browser Editor als fertige
  Operator-Oberflaeche.
- ORCA/Lens Navigation, Naming, Layout und Informationsarchitektur.
- System Health Dashboard.
- Nextcloud/Private-Data Review UI.
- Telegram Voice und Image Worker Nutzerflaechen.

Fuer das MVP reichen stabile Backend-Vertraege, API-Snapshots, Tests, redacted
Evidence und minimale vorhandene Bedienpfade. Die eigentlichen UIs gestalten wir
danach gemeinsam.

## Aktueller Fortschritt

Stand: 2026-06-23

| # | Roadmap | % | Warum nicht 100% |
| - | - | -: | - |
| 1 | Runtime Closure Gates | 100 | - |
| 2 | Secure Data Mode Runtime Hooks | 100 | - |
| 3 | Private Data / Nextcloud Memory Ingestion | 100 | - |
| 4 | System Health Checker Host-Agent | 100 | - |
| 5 | Telegram Voice Pipeline | 90 | Live-Server hat Voice-Download/STT-Gates aktiv und lokalen `faster-whisper` STT geladen; die erste Voice ist nur noch als redaktierter `pending_stt`-Eintrag vorhanden, daher fehlt eine frische Post-Deploy-Voice fuer den finalen Download/STT/Agent/Reply-Smoke. |
| 6 | ORCA / Lens Naming & Backend Migration | 80 | ORCA Naming, Boundary, Env-/Tool-/Provider-/Route-Aliases, ORCA-Core-Adapter und Legacy-Deprecation-Contract sind erledigt und getestet; Data-Path-Migration braucht noch konkretes Ziel/Rollback, UI-Lens-Wording bleibt Design-Gate. |
| 7 | PlanRuntime / Visual Planning Logic | 92 | Backend-Logik fuer PlanRuntime, Lens, Validation, Proposal Queue, Acceptance, Patch, Apply und bestaetigten post-apply Dispatch-Request ist erledigt und getestet; Browser-Editor/UI bleibt bis zur gemeinsamen UI-Neugestaltung offen. |
| 8 | Release / Distribution Evidence | 82 | MVP-MasterRoadmap-Aggregat und UI-live Gate blockieren 1.0-Claims korrekt und sind getestet; Deploy/Tag/Distribution brauchen ein konkretes Ziel-Go und die neue UI bleibt offen. |
| 9 | Image Tools Worker Final Smoke | 100 | - |
| 10 | GameDev Mount Write Smoke | 100 | - |

Gesamtfortschritt MVP-Roadmaps: 94%

Version-1.0-Gate: UI live? nein

Recommended next human decision:

- Roadmap 5: eine frische kurze Telegram-Voice-Nachricht aus dem erlaubten Chat senden,
  dann genau einen bounded Poll-Smoke fuer Download, lokalen STT, Agent-Turn und Reply laufen lassen.
- Roadmap 6: UI-Lens-Renaming bis zum gemeinsamen Redesign parken;
  Datenpfad-Migration und finaler Legacy-Removal bleiben Live-Go mit Rollback.
- Roadmap 7: Browser-Editor/UI bis zum Redesign parken; post-apply Dispatch ist
  als bestaetigter Request ohne Runtime-Ausfuehrung backendseitig vorbereitet.
- Roadmap 8: Deploy/Tag/Distribution weiter als separates Live-Go behandeln;
  Version-1.0-Claim bleibt automatisch blockiert bis 10/10 Roadmaps und neue UI live sind.
- Roadmap 10: optionalen GameDev-Write-Smoke entweder explizit freigeben oder
  bewusst deferred markieren; ohne Go bleibt nur der reversible Plan claimbar.

## Roadmap 1 Backend Evidence

Backend-Artefakt:

- `src/mvp_runtime_closure.py`
- `scripts/live_runtime_smoke.py`

Fokussierte Tests:

- `tests/test_mvp_runtime_closure.py`
- `tests/test_updates_backups_ui_static.py`
- `tests/test_mcp_server_plugin.py`
- `tests/test_telegram_text_boundary.py`

Aktuelle Gate-Zusammenfassung:

| Gate | Status | Klasse | Warum |
| --- | --- | --- | --- |
| Updates and backups backend contract | go | repo_only | Admin-gated update/backup status/action contract exists. |
| Updater server runtime evidence | go | needs_live_go | Debian homeserver user systemd has `odysseus-podman.service` active, `odysseus-auto-update.timer` active, and `/api/version` reports the running runtime and pending fuzzy commit without exposing secrets. |
| Updates and backups live smoke | go | needs_live_go | Debian homeserver has the backup timer active, Restic repo marker present, latest daily snapshot evidence, repository check without errors, and retention preview evidence; no destructive restore was run. |
| MCP offline route and policy coverage | go | repo_only | MCP route, policy, notification, and owner gates have offline coverage. |
| MCP plugin present in rebuilt runtime | go | needs_live_go | Local runtime loaded `mcp_server`; `/api/plugins/mcp/info` returned 200 with the plugin disabled by default. |
| MCP local route smoke | go | needs_live_go | MCP was temporarily enabled with all risky permissions false; JSON-RPC initialize, ping, tools/list, resources/list and readiness read returned 200, then config was restored to disabled. |
| Telegram text offline boundary | go | repo_only | Telegram text intake, allowlist, bridge, and identifier redaction have offline coverage. |
| Telegram text live roundtrip | go | needs_live_go | Debian container runtime has token, allowed-chat, agent-intake, polling and reply gates enabled; polling returned `poll_ok`, synthetic intake stored and bridged through the live webhook path, and the reply route sent a Telegram message with a message-id marker. |

Live-Smoke Evidence, 2026-06-23:

- Local server: `GET /api/version` returned 200, version `0.99.6`, branch `dev`.
- Admin update routes: `GET /api/admin/system/update-status` and `POST /api/admin/system/update-check` returned 200 with redacted status; `backup-now` and `update-now` returned 200 but `status=blocked`, as expected on Windows without `systemctl`, restic repository, updater wrapper or `ODYSSEUS_UPDATER_LIVE_ENABLED`.
- Debian homeserver SSH evidence: host reachable as the configured homeserver target; user systemd shows `odysseus-podman.service`, `odysseus-auto-update.timer`, `odysseus-homeserver-backup.timer`, and `nextcloud-podman.service` active/loaded.
- Debian runtime evidence: `/api/version` on the homeserver returns version `0.99.6`, branch `dev`, fuzzy remote, running commit `cf28ce8f`, latest fuzzy commit `ca8889d6fe5f`, and `update_available=true`.
- Podman evidence: Odysseus containers are up, and a Nextcloud Podman stack exists with `nextcloud-app`, `nextcloud-cron`, `nextcloud-db`, and `nextcloud-redis` running.
- Backup evidence: backup target is mounted, Restic binary and repo markers are present, backup env marker is present, latest daily backup service exited successfully, Restic repository check found no errors, and retention preview completed.
- MCP runtime: `/api/plugins/mcp/info` and `/config` returned 200; disabled probe returned 403; temporary safe enable returned 200; JSON-RPC initialize/ping/tools/list/resources/list/readiness returned 200; config restored to disabled.
- Telegram runtime: container-local `/api/plugins/telegram/status` returned 200 with token, allowed-chat, agent-intake, polling and reply markers present and no token/chat values visible; `/poll` returned `poll_ok`; bounded synthetic `/webhook` intake stored and bridged a smoke message; `/reply` sent one Telegram test message and returned a message-id marker without exposing token, chat ID or private content.

## Roadmap 2 Backend Evidence

Backend-Artefakt:

- `src/mvp_secure_data_closure.py`
- `src/secure_provider_runtime.py`
- `src/memory_provider.py`
- `src/nextcloud_source_provider.py`
- `routes/session_routes.py`
- `plugins/telegram/plugin.py`

Fokussierte Tests:

- `tests/test_mvp_secure_data_closure.py`
- `tests/test_secure_provider_runtime.py`
- `tests/test_secure_provider_runtime_hook_static.py`
- `tests/test_memory_provider.py`
- `tests/test_nextcloud_source_provider.py`
- `tests/test_telegram_plugin.py`
- `tests/test_data_classification.py`
- `tests/test_secure_policy_gate.py`
- `tests/test_secure_model_routing.py`
- `tests/test_sensitive_retrieval_guard.py`
- `tests/test_secure_channel_policy.py`

Aktuelle Gate-Zusammenfassung:

| Gate | Status | Klasse | Warum |
| --- | --- | --- | --- |
| Data classification model | go | repo_only | Public/private/sensitive/secret classification and strict merge rules exist. |
| Immutable chat security state | go | repo_only | Normal/secure chat state is immutable and local-only scope is modeled. |
| Central secure policy gate | go | repo_only | Source, provider, embedding, tool, export, and ambiguous-state decisions are modeled. |
| Local-only model routing guard | go | repo_only | Secure chats require local primary, fallback, and embedding model routes. |
| Sensitive retrieval guard | go | repo_only | Memory/RAG/graph guard blocks sensitive normal-chat context without refs. |
| Telegram and channel policy | go | repo_only | Channel policy blocks sensitive Telegram/unsupported secure-flow paths. |
| Provider runtime hook | go | repo_only | Session provider/model selection calls secure policy before external model probes or model switches when secure mode is requested. |
| Retrieval runtime hook | go | repo_only | Native memory recall calls the sensitive retrieval guard before returning context hits when security state is supplied. |
| Telegram runtime hook | go | repo_only | Telegram reply route/tool calls channel policy before sending classified secure content. |
| Private source runtime hook | go | repo_only | Nextcloud/private-source readiness calls secure source policy before ingestion/review can proceed. |

## Roadmap 3 Backend Evidence

Backend-Artefakte:

- `src/mvp_private_data_ingestion_closure.py`
- `src/nextcloud_transfer_readiness.py`
- `src/nextcloud_resumable_scanner.py`
- `src/nextcloud_resumable_transfer.py`
- `src/nextcloud_privacy_partition.py`
- `src/nextcloud_chunked_extraction.py`
- `src/bigdata_ledger_contract.py`
- `src/nextcloud_intake_ledger.py`

Fokussierte Tests:

- `tests/test_mvp_private_data_ingestion_closure.py`
- `tests/test_nextcloud_transfer_readiness.py`
- `tests/test_nextcloud_resumable_scanner.py`
- `tests/test_nextcloud_resumable_transfer.py`
- `tests/test_nextcloud_privacy_partition.py`
- `tests/test_nextcloud_chunked_extraction.py`
- `tests/test_bigdata_ledger_contract.py`
- `tests/test_nextcloud_source_provider.py`
- `tests/test_live_nextcloud_readiness_check.py`
- `tests/test_nextcloud_intake_ledger.py`

Aktuelle Gate-Zusammenfassung:

| Gate | Status | Klasse | Warum |
| --- | --- | --- | --- |
| Planning sources memory inventory | go | repo_only | Planning source inventory is bounded, read-only, and redacted. |
| Planning sources memory ingest live | go | repo_only | Planning documents can be ingested as bounded memory capsules. |
| Big Data ledger contract | go | repo_only | Append-only metadata ledger contract exists without raw content. |
| Nextcloud transfer readiness | go | needs_live_go | Quelle/Ziel sind operativ benannt, sensible Top-Level-Roots bleiben runtime-only, der Ziel-User samt serverseitigem Secret-Marker ist angelegt, und das lokale Modell-Gate ist live belegt. |
| Privacy partition | go | repo_only | Runtime-sensitive roots werden nur als Parameter angenommen; Ledger/Reports speichern nur generische Klassen wie `local_sensitive` und `archive_candidate`, keine echten Namen, Pfade, Inhalte oder Counts. |
| Resumable transfer tooling | go | repo_only | Copy-only transfer planner records pending transfer ledger progress with redacted target labels, plans only archive candidates for the new Nextcloud mirror, and performs no live copy. |
| Resumable scanner dry-run | go | repo_only | Scanner-Dry-Run schreibt metadata-only Inventory-Records, markiert lokale sensible Bereiche fuer local-only Verarbeitung, kann Batch-Unterbrechungen fortsetzen und blockiert Ledger im Scan-Root. |
| Live small-batch transfer | go | needs_live_go | Bounded synthetischer Target-Write und Nextcloud-Scan liefen gegen den dedizierten Intake-User; keine private Quelle wurde kopiert. |
| Chunked extraction lanes | go | repo_only | Offline Chunking schreibt nur Chunk-Hashes/Offsets und modelliert retryable sowie needs_review Ledger-Zustaende ohne Raw-Content-Persistenz. |
| Memory abstraction ingest live | go | needs_live_go | Synthetischer Live-Memory-Store-Write und provenance-only RaptorGraph-Event-Contract sind belegt; echte Corpus-Memories warten auf die gemeinsam definierten Regeln. |
| Full 100GB+ transfer live | deferred | needs_operator_input | High-impact Live-Aktion bleibt bewusst Folgearbeit nach Regeldefinition, Batchbudget und Operator-Freigabe. |
| Full corpus analysis live | deferred | needs_operator_input | Echte Corpus-Analyse startet erst nach erfolgreichem Transfer plus operator-approved Analyse-Regeln. |
| Ingestion dashboard live | deferred | needs_design | Bewusst auf UI-Neugestaltung verschoben. |

Live-Smoke Evidence, 2026-06-23:

- Operator hat Quelle/Ziel und zwei sensible Top-Level-Root-Klassen benannt; diese echten Namen werden nicht in Repo-Artefakten persistiert.
- Debian Nextcloud ist live (`33.0.5`), der dedizierte Intake-User wurde angelegt, und der Secret-Marker liegt ausschliesslich serverseitig.
- Lokales Ollama auf dem Homeserver hat `gemma3:4b` verfuegbar; sensible Pipeline-Pfade bleiben `local_only`.
- Bounded synthetischer Nextcloud-Target-Smoke schrieb und scannte ein nicht-privates Testartefakt beim Intake-User.
- Synthetischer Live-Memory-Store-Smoke schrieb einen nicht-privaten R3-Evidence-Eintrag in den laufenden Odysseus-Datenpfad; der HTTP-Memory-API-Smoke blieb ohne gueltigen Login/API-Token korrekt bei `401`.
- `tests/test_nextcloud_privacy_partition.py`, `tests/test_nextcloud_resumable_scanner.py`, `tests/test_nextcloud_resumable_transfer.py`, `tests/test_live_nextcloud_readiness_check.py`, `tests/test_nextcloud_transfer_readiness.py` und `tests/test_mvp_runtime_closure.py` liefen gruen.
- Keine echte Quell-Nextcloud-Datei, kein privater Pfad, kein privater Dateiname, kein privater Inhalt und kein realer Corpus-Metadatensatz wurde in Repo-Artefakten persistiert.

## Roadmap 4 Backend Evidence

Backend-Artefakte:

- `src/mvp_system_health_closure.py`
- `src/live_system_health_host_agent_plan.py`
- `src/live_system_health_local_api_consumer.py`
- `src/system_health_ops_readiness.py`
- `src/system_health_agent_interface.py`
- `src/system_health_basic_collectors.py`
- `src/system_health_advanced_collectors.py`
- `src/system_health_rule_engine.py`
- `src/system_health_plugin_foundation_bundle.py`

Fokussierte Tests:

- `tests/test_mvp_system_health_closure.py`
- `tests/test_live_system_health_host_agent_plan.py`
- `tests/test_live_system_health_local_api_consumer.py`
- `tests/test_system_health_ops_readiness.py`
- `tests/test_system_health_plugin_foundation_bundle.py`
- `tests/test_system_health_agent_interface.py`
- `tests/test_system_health_basic_collectors.py`
- `tests/test_system_health_advanced_collectors.py`
- `tests/test_system_health_rule_engine.py`

Aktuelle Gate-Zusammenfassung:

| Gate | Status | Klasse | Warum |
| --- | --- | --- | --- |
| Plugin foundation bundle | go | repo_only | Plugin foundation, audit, readiness, review, score and release summaries exist. |
| Health-agent snapshot interface | go | repo_only | Sanitized snapshot interface is modeled without core host commands. |
| Basic collector normalization | go | repo_only | Basic CPU, memory, disk, load and uptime collectors degrade safely. |
| Advanced collector normalization | go | repo_only | Advanced sensor, SMART, update and reboot collectors expose unsupported/unknown states. |
| Rule engine and alert dedupe | go | repo_only | Rule engine, alert severity, dedupe and cooldown behavior are modeled. |
| Ops and security readiness | go | repo_only | Ops readiness keeps host access outside core and blocks auto-repair. |
| Host-agent MVP plan reviewed | go | repo_only | Debian host scope, bounded read-only snapshot method, permissions, rollback and no-secrets policy are reviewed for the MVP smoke. |
| Local API consumer plan | go | repo_only | Snapshot contract, offline fixture shape, timeout and sanitized payload policy are reviewed. |
| Host-agent runtime live smoke | go | needs_live_go | Debian host produced a sanitized snapshot: host reachable, Odysseus/Nextcloud services active, containers visible, load/memory/disk metrics readable. |
| Dashboard and alert UI live | deferred | needs_design | Dashboard and alert UX are deferred to the shared UI redesign and remain covered by the global Version-1.0 UI gate. |

Live-Smoke Evidence, 2026-06-23:

- Debian host scope wurde live ueber den bestehenden SSH-Zielmarker geprueft; keine Secrets, Tokens, privaten Rohdaten oder privaten Hostpfade wurden in Repo-Artefakten persistiert.
- Odysseus- und Nextcloud-User-Services waren aktiv; die relevanten Nextcloud- und Odysseus-Container waren sichtbar und laufend.
- Load-, Memory- und Root-Disk-Werte wurden als redacted Snapshot gelesen und lokal ueber `build_basic_health_snapshot` gegen das Health-Agent-Interface validiert (`overall_status=ok`, vier Collector-Werte).
- `tests/test_live_system_health_host_agent_plan.py`, `tests/test_live_system_health_local_api_consumer.py`, `tests/test_system_health_agent_interface.py`, `tests/test_system_health_basic_collectors.py`, `tests/test_system_health_advanced_collectors.py`, `tests/test_system_health_rule_engine.py` und `tests/test_mvp_system_health_closure.py` liefen gruen.

## Roadmap 5 Backend Evidence

Backend-Artefakte:

- `src/mvp_telegram_voice_closure.py`
- `src/telegram_voice_pipeline.py`
- `plugins/telegram/plugin.py`

Fokussierte Tests:

- `tests/test_mvp_telegram_voice_closure.py`
- `tests/test_telegram_voice_pipeline.py`
- `tests/test_telegram_plugin.py`
- `tests/test_telegram_voice_boundary.py`
- `tests/test_telegram_text_boundary.py`

Aktuelle Gate-Zusammenfassung:

| Gate | Status | Klasse | Warum |
| --- | --- | --- | --- |
| Voice operator contract | go | repo_only | Operator status language and separate download/STT/reply gates are documented. |
| Metadata-only voice intake | go | repo_only | Voice intake stores redacted metadata and does not become agent-ready before STT. |
| Redacted history and readiness | go | repo_only | History/readiness expose counts and redacted handles without raw chat or file ids. |
| Voice download gate plan | go | repo_only | Download planning is disabled by default, bounded and produces safe local refs. |
| Fakeable STT boundary | go | repo_only | Fakeable STT requires a local ref and redacts sensitive transcript fragments. |
| Voice transcript to agent turn | go | repo_only | Successful transcripts become internal Telegram voice agent prompts. |
| Gated Telegram text reply plan | go | repo_only | Reply planning remains disabled until the reply gate and reply text are present. |
| Plugin runtime integration | go | repo_only | Telegram plugin wires the default-off offline voice pipeline through fakeable download/STT/voice agent-turn hooks. |
| Manual live voice smoke | needs_live_go | needs_live_go | Live server is voice-ready with download/STT gates enabled and local `faster-whisper` loaded; the older voice is only available as redacted `pending_stt` metadata, so final proof needs one fresh post-deploy incoming voice message. |
| Voice UI live | deferred | needs_design | Voice UI/status controls are deferred until the shared UI redesign and remain covered by the global Version-1.0 UI gate. |

Live-Smoke Evidence, 2026-06-23:

- Telegram Bot API `getMe` returned 200 in Roadmap 1 smoke.
- Live Debian app readiness reports `agent_reply_ready`: bot token marker, allowed chat marker, polling, agent-chat and reply gate are present; raw chat ids and token values were not printed or persisted.
- `INSTALL_STT=true` rebuild installed `faster-whisper` without enabling the full optional dependency set.
- Live container reports `TELEGRAM_VOICE_DOWNLOAD_ENABLED=true`, `TELEGRAM_VOICE_STT_ENABLED=true`, local STT provider `local`, model `base`, and `stt_model_loaded=true`.
- Bounded live poll ran with redacted output and returned `poll_ok`, `processed=0`, `agent_turns=0`, `replies=0`; no fresh pending incoming voice message existed to transcribe.
- A previously sent voice exists only as redacted `pending_stt` metadata after the log-redaction deploy; raw Telegram file identifiers are intentionally not stored, so it cannot be re-downloaded for STT.
- `tests/test_mvp_telegram_voice_closure.py`, `tests/test_telegram_voice_pipeline.py`, `tests/test_telegram_plugin.py`, `tests/test_telegram_voice_boundary.py` and `tests/test_telegram_text_boundary.py` liefen gruen.
- Noch kein finaler Post-Deploy-Voice-Download, keine abschliessende STT/Agent/Reply-Evidence und kein voice-spezifischer Reply-Smoke wurden ausgefuehrt, weil keine frische Voice-Nachricht im Polling lag.

## Roadmap 6 Backend Evidence

Backend-Artefakte:

- `src/mvp_orca_lens_closure.py`
- `specs/roadmaps/orca-memory-graph-migration-roadmap.v1.json`
- `plugins/obsidian/backend/feature_flags.py`
- `plugins/obsidian/backend/tool_specs.py`
- `plugins/obsidian/backend/context_provider.py`
- `plugins/obsidian/backend/orca_core.py`
- `plugins/obsidian/backend/routes.py`
- `plugins/obsidian/plugin.py`

Fokussierte Tests:

- `tests/test_mvp_orca_lens_closure.py`
- `tests/test_orca_core_contract.py`
- `tests/test_orca_compatibility_contract.py`
- `tests/test_obsidian_bridge_contract.py`
- `tests/test_plugin_obsidian_load.py`
- `tests/test_obsidian_memory_mission_contract.py`

Aktuelle Gate-Zusammenfassung:

| Gate | Status | Klasse | Warum |
| --- | --- | --- | --- |
| ORCA roadmap and delegation | go | repo_only | Canonical ORCA migration roadmap and ABC slices exist. |
| ORCA naming contract | go | repo_only | ORCA, Local Markdown Vault, Lens, Atlas and legacy Obsidian wording are defined. |
| Technical boundary audit | go | repo_only | ORCA core, source adapter, Lens and legacy compatibility boundaries are mapped. |
| Compatibility surface design | go | repo_only | Route, tool, env and docs compatibility design exists without deleting legacy surfaces. |
| Env, tool and provider aliases | go | repo_only | ODYSSEUS_ORCA flags, orca_* tools and orca.vault_context provider aliases are tested. |
| ORCA route aliases | go | repo_only | /api/plugins/orca aliases are mounted while legacy /api/plugins/obsidian routes remain intact. |
| ORCA core modules | go | repo_only | ORCA Core Adapter fasst Retrieval, Readiness, RAPTOR, Query und Lens-Contracts read-only hinter Legacy-Obsidian-Adaptern zusammen. |
| Frontend Lens naming | needs_design | needs_design | Frontend Lens wording is parked until the shared UI redesign. |
| Legacy Obsidian deprecation | go | repo_only | Legacy-Obsidian-Kompatibilitaet bleibt erhalten, aber ORCA-Migration-Map, Warnungen und Removal-Gates sind read-only modelliert. |
| Data path migration and final removal plan | blocked | needs_operator_input | Live-Go is granted, but final path migration/removal still needs a named target, rollback plan, backup evidence and compatibility cutover decision. |

Live/Test Evidence, 2026-06-23:

- `tests/test_mvp_orca_lens_closure.py`, `tests/test_orca_core_contract.py`, `tests/test_orca_compatibility_contract.py` und `tests/test_plugin_obsidian_load.py` liefen gruen.
- Kein Datenpfad wurde migriert, kein Legacy-Pfad entfernt und kein UI-Wording umgebaut.

## Roadmap 7 Backend Evidence

Backend-Artefakte:

- `src/mvp_planruntime_visual_closure.py`
- `src/plan_runtime.py`
- `src/roadmap_lens.py`
- `src/visual_agent_programming_lens.py`
- `src/planruntime_post_apply_dispatch.py`
- `src/subagent_plan_binding.py`
- `routes/roadmap_routes.py`

Fokussierte Tests:

- `tests/test_mvp_planruntime_visual_closure.py`
- `tests/test_plan_runtime.py`
- `tests/test_roadmap_lens.py`
- `tests/test_visual_agent_programming_lens.py`
- `tests/test_planruntime_post_apply_dispatch.py`
- `tests/test_subagent_plan_binding.py`

Aktuelle Gate-Zusammenfassung:

| Gate | Status | Klasse | Warum |
| --- | --- | --- | --- |
| PlanRuntime source of truth | go | repo_only | Structured roadmap JSON loads, validates and projects into PlanGraph. |
| Subagent PlanRuntime binding | go | repo_only | Subagent run specs are bound to PlanRuntime nodes and context capsules. |
| Roadmap Lens read-only graph | go | repo_only | Roadmap Lens exposes bounded read-only graph snapshots. |
| Visual programming read-only snapshot | go | repo_only | Visual programming snapshot exposes policy-gated controls without mutation. |
| Visual edit dry-run validator | go | repo_only | Create-node and connect-dependency proposals validate without writing or starting agents. |
| Proposal review queue | go | repo_only | Validated proposals are exposed as read-only review queue items. |
| Operator acceptance contract | go | repo_only | Operator-gated acceptance creates auditable accepted/rejected events. |
| Mutation patch contract | go | repo_only | Accepted proposals become mutation patches with audit and version metadata. |
| Mutation apply adapter | go | repo_only | Authorized patches apply to roadmap payloads with rollback metadata and no implicit agent start. |
| Admin route contracts | go | repo_only | Roadmap graph, visual snapshot, validate, review, acceptance, patch and apply routes are admin-protected. |
| Browser proposal editor UI | needs_design | needs_design | Browser proposal editor UI is deferred until the shared UI redesign. |
| Post-apply agent dispatch | go | repo_only | Post-apply dispatch erzeugt einen bestaetigten, auditierbaren Request mit SubagentRunSpec, bleibt aber ohne Thread-, Job- oder Runtime-Ausfuehrung. |

Test Evidence, 2026-06-23:

- `tests/test_mvp_planruntime_visual_closure.py`, `tests/test_plan_runtime.py`, `tests/test_roadmap_lens.py`, `tests/test_visual_agent_programming_lens.py`, `tests/test_planruntime_post_apply_dispatch.py` und `tests/test_subagent_plan_binding.py` liefen gruen.
- Kein UI-Editor wurde gebaut und kein Agent wurde implizit gestartet.

## Roadmap 8 Backend Evidence

Backend-Artefakte:

- `src/mvp_release_distribution_closure.py`
- `src/release_evidence_snapshot.py`
- `src/manual_release_evidence.py`
- `src/release_readiness_report.py`
- `src/release_readiness_pipeline.py`
- `src/mvp_master_roadmap_gate.py`
- `src/live_release_evidence_closeout.py`
- `docs/plans/external-1.0-evidence-closeout.md`

Fokussierte Tests:

- `tests/test_mvp_release_distribution_closure.py`
- `tests/test_release_readiness_report.py`
- `tests/test_release_evidence_snapshot.py`
- `tests/test_live_release_evidence_closeout.py`
- `tests/test_manual_release_evidence.py`
- `tests/test_plugin_release_gate.py`
- `tests/test_release_readiness_pipeline.py`
- `tests/test_mvp_master_roadmap_gate.py`
- `tests/test_release_decision_bundle.py`

Aktuelle Gate-Zusammenfassung:

| Gate | Status | Klasse | Warum |
| --- | --- | --- | --- |
| Automated release gates | go | repo_only | Automated release evidence snapshot and readiness reports are modeled. |
| Manual provider proof evidence | go | needs_live_go | Provider proof is recorded with isolated redacted evidence. |
| Manual test-vault export/import/rebuild evidence | go | needs_live_go | Test-vault export/import/rebuild proof is recorded with isolated redacted evidence. |
| Known limits review | go | repo_only | Known limits are reviewed without implying deploy, tag or distribution execution. |
| Plugin release gate | go | repo_only | Plugin release gate is modeled separately from release decision language. |
| Release readiness pipeline | go | repo_only | Readiness pipeline aggregates snapshots, manual evidence and plugin gates. |
| Evidence-Go closeout language | go | repo_only | Closeout language separates Evidence-Go from deploy, tag and distribution. |
| Live phase boundary gates | go | repo_only | Provider, export/import/rebuild, host, Telegram and network actions remain separate operator gates. |
| MVP roadmap aggregate for 1.0 | go | repo_only | Release-Pipeline liest das MVP-MasterRoadmap-Aggregat und blockiert Version 1.0, solange nicht alle zehn Roadmaps 100% sind und die neue UI live ist. |
| Deploy, tag and distribution execution | blocked | needs_operator_input | Live-Go is broad, but external deploy/tag/distribution still needs a concrete target, version decision and rollback/announcement plan; Version 1.0 gate is not ready. |
| New UI live release gate | needs_design | needs_design | Version 1.0 still requires the new UI to be live. |

Test Evidence, 2026-06-23:

- Release/Evidence focused suite lief gruen after updating the expected MasterRoadmap aggregate to 79%.
- Kein Deploy, Tag, Push oder Distribution wurde ausgefuehrt.

## Roadmap 9 Backend Evidence

Backend-Artefakte:

- `src/mvp_image_tools_worker_closure.py`
- `src/image_tools_worker.py`
- `src/telegram_image_actions.py`
- `workers/image_tools_worker/app.py`
- `workers/image_tools_worker/README.md`
- `routes/gallery_routes.py`

Fokussierte Tests:

- `tests/test_mvp_image_tools_worker_closure.py`
- `tests/test_image_tools_worker_mvp_static.py`
- `tests/test_image_tools_worker.py`
- `tests/test_telegram_image_actions.py`
- `tests/test_gallery_remove_bg_worker.py`

Aktuelle Gate-Zusammenfassung:

| Gate | Status | Klasse | Warum |
| --- | --- | --- | --- |
| Image worker contract | go | repo_only | Worker modes, config, errors, payload limits and security boundaries are documented. |
| Core worker client | go | repo_only | Core client handles disabled, timeout, unreachable, payload and PNG response semantics. |
| Remove-BG route integration | go | repo_only | Gallery remove-bg route calls the worker client after privilege checks and maps errors. |
| Isolated worker MVP | go | repo_only | workers/image_tools_worker exposes /remove-background with structured JSON errors. |
| Fake worker smoke | go | safe_offline | Worker response builder can be smoke-tested with a fake PNG without importing rembg. |
| Core dependency isolation | go | repo_only | Core starts without hard rembg, PIL or worker dependency imports on the client path. |
| UI and cookbook contract | go | repo_only | Setup/error wording is frozen for the later UI and cookbook redesign. |
| Telegram image action readiness | go | repo_only | Telegram-Image-Actions laufen default-off ueber redacted Metadata, injizierbare Bytes und den ImageToolsWorkerClient ohne neue Core-Dependencies. |
| Manual Remove-BG smoke | go | needs_live_go | Isolated worker venv uses Python 3.11.9 with `rembg` 2.0.76 and `onnxruntime` 1.27.0; local `/remove-background` smoke returned 200 with PNG output. |
| Image tools UI live | deferred | needs_design | Image-worker UI/status controls are deferred to the shared UI redesign and remain part of the separate Version-1.0 UI-live gate, not the Roadmap 9 backend blocker. |

Live-Smoke Evidence, 2026-06-23:

- Local worker started on `127.0.0.1:8123`.
- `GET /health` returned 200 with `remove_background` capability.
- Isolated worker venv used Python 3.11.9 with `rembg` 2.0.76 and `onnxruntime` 1.27.0; the core venv remains dependency-isolated.
- `POST /remove-background` with a bounded synthetic PNG returned 200, `mime_type=image/png`, 2339 output bytes, PNG signature true, and no hint-mask side effects.
- The worker process was stopped after the smoke.

## Roadmap 10 Backend Evidence

Backend-Artefakte:

- `src/mvp_gamedev_mount_closure.py`
- `src/gamedev_project_profile.py`
- `core/mount_manager.py`
- `core/path_resolver.py`
- `src/agent_tools/filesystem_tools.py`
- `docs/gamedev-project-access-runbook.md`
- `docs/plans/gamedev-project-access-roadmap.md`

Fokussierte Tests:

- `tests/test_mvp_gamedev_mount_closure.py`
- `tests/test_gamedev_project_profile.py`
- `tests/test_mount_points.py`

Aktuelle Gate-Zusammenfassung:

| Gate | Status | Klasse | Warum |
| --- | --- | --- | --- |
| GameDev mount profile | go | repo_only | Safe Godot mount profile includes write extensions, backup policy and broad-root rejection. |
| Runtime config and read smoke | go | needs_live_go | Runtime config validation and read-only virtual mount smoke are recorded without host-path leakage. |
| Path and owner scope | go | repo_only | Virtual mount access is owner-scoped and blocks traversal, broad roots and sensitive paths. |
| Write policy guards | go | repo_only | write_file/edit_file require explicit tools, allowed extensions, size limits and symlink checks. |
| Named command gate | go | repo_only | GameDev commands are named argv plans and free-form shell is rejected. |
| Operator runbook | go | repo_only | Runbook documents enablement, dry-run validation, smoke steps and cleanup constraints. |
| Reversible write-smoke plan | go | safe_offline | Write-smoke target and cleanup are planned with virtual paths, size limits and operator gate. |
| Manual write smoke | go | needs_live_go | Live write-smoke wrote 46 bytes through `/mnt/canyon-racer`, read them back, audited the virtual mount write and removed the temporary file without exposing the host path. |

Live-Smoke Evidence, 2026-06-23:

- Mount `/mnt/canyon-racer`, owner `fuzzy`, write policy and backup enabled, `.txt` allowed.
- Wrote `/mnt/canyon-racer/.odysseus-write-smoke-20260623.txt`, read back exact content, then removed the file.
- Smoke result: `write_ok=true`, `readback_ok=true`, `cleanup_ok=true`, `host_path_visible=false`.

## Arbeitsreihenfolge

### Phase 1: Runtime und Gates stabilisieren

1. Updates-/Backup-Logik und Runtime-Version sauber belegen.
2. MCP Production Activation Smoke schliessen.
3. Telegram Text Runtime Smoke schliessen.
4. Release-/Distribution-Evidence erst danach aktualisieren.

Exit:

- Jeder Runtime-Track hat Go, Partial oder No-Go mit redacted Evidence.
- Deploy, Provider, Telegram, Nextcloud und Host-Aktionen bleiben separate Gates.

### Phase 2: Daten- und Policy-Backbone bauen

1. Secure Data Mode Runtime Hooks aktivieren.
2. Nextcloud Transfer Readiness erfassen.
3. Resumable Scanner Dry-Run und Ledger pruefen.
4. Kleine Live-Batches erst nach Operator-Go.

Exit:

- Private Datenpfade sind messbar, resumable und review-gated.
- Keine Raw-Content-Leaks in Logs, Evidence, Tests oder Handoffs.

### Phase 3: Betriebslogik und Kanaele schliessen

1. System Health Checker Host-Agent.
2. Telegram Voice Pipeline.
3. Image Tools Worker Final Smoke.
4. GameDev Mount Write Smoke.

Exit:

- Homeserver-Betrieb ist backendseitig sichtbar.
- Externe Kanaele und Worker sind realistisch nutzbar oder sauber deferred.

### Phase 4: Naming und PlanRuntime-Logik entkoppeln

1. ORCA/Lens Backend-Naming und Compatibility-Aliases stabilisieren.
2. PlanRuntime / Visual Planning Logic ohne UI-Abhaengigkeit schliessen.
3. UI-Anforderungen als gemeinsame Folgearbeit sammeln, nicht nebenbei bauen.

Exit:

- Backend-Begriffe und Datenpfade laufen nicht weiter auseinander.
- Roadmap-Vorschlaege, Gates und Apply-Logik sind ohne neue UI testbar.

## Globale Stop-Regeln

- Keine Live-Netzwerk-, Provider-, Telegram-, Nextcloud-, Host-, Backup-,
  Restore-, Deploy- oder Write-Smoke-Aktion ohne explizites Go.
- Keine Tokens, Chat-IDs, Secrets, privaten Pfade, Raw-Provider-Ausgaben oder
  private Inhalte in Repo, Tests, Logs, Evidence oder Handoffs.
- Keine destruktiven Git-Kommandos, keine Force-Pushes, kein Reset/Checkout-
  Rewrite.
- Keine neue UI bauen, wenn ein Backend-/Runtime-Gate noch offen ist. UI-Edits
  nur fuer minimale Bedienbarkeit oder Verifikation.
- Keine neuen Grossfeatures starten, solange ein hoeher priorisierter MVP-Track
  blockiert ist und kein klares Deferred/No-Go existiert.
- Kein Post-MVP Research starten, bevor die MVP-Punkte 1-10 mindestens Go oder
  bewusst Partial/Deferred sind.

## Fortschrittsformat

```text
MVP-Gesamtfortschritt: XX %
Aktiver Track: <Prio + Name>
Status: go | partial | blocked | deferred | no_go
Evidence:
- ...
Naechster Schritt:
- ...
```

## Master Definition Of Done

Das MVP ist erreicht, wenn:

- Prioritaeten 1-10 jeweils Go, bewusstes Partial oder bewusstes Deferred haben.
- PlanRuntime, Gate-Runner, Datenpfade und Runtime-Smokes backendseitig
  verlaesslich sind.
- ORCA/Lens/Memory-Backend-Sprache nicht mehr auseinanderlaeuft.
- Runtime-Gates nicht mehr als offene Bauchgefuehl-Claims herumliegen.
- Private Datenpfade policy-gated, resumable und redacted sind.
- Homeserver Health, Telegram, Image Worker und GameDev Mounts als echte
  Backend-/Betriebspfade eingeordnet sind.
- UI-Neugestaltung als eigener gemeinsamer Folgeblock vorbereitet ist.
- Post-MVP-Ideen 11 und 12 nicht mehr mit MVP-Arbeit konkurrieren.
