# Open Gates Status - 2026-07-05

Purpose: close every gate that is safely closable, and make every remaining gate explicit enough that Odysseus must not claim completion without evidence.

## Current Verdict

- MVP backend roadmaps 1-10: 100%.
- Safe repo-only queue: exhausted.
- Current deployed app line: `0.99.x`; 1.0 remains blocked by UI.
- Version 1.0: not ready until `VERSION-1-UI-LIVE` is true.
- Audit queue: 35 live-gate entries, deduplicated to 24 unique live gates; 5 design-gate entries, deduplicated to 4 unique design gates.
- Completion lanes: 38 distinct gate IDs across live, design, service, operator-subset, post-MVP and release decisions.
- Live actions performed after explicit operator Go: homeserver deploy verification,
  Odysseus container recreate with the Nextcloud compose overlay, and one bounded
  Universal Inbox to Nextcloud copy-only smoke test. Later 0.99 activation added
  Telegram polling/reply/image gates, one Playwright GUI screenshot smoke, one
  Telegram photo delivery smoke, one sandbox host-runner smoke, one local
  Odysseus MCP workbench smoke, one read-only observability inventory, and one
  synthetic security incident tabletop. Later bounded read-only checks added one
  Memory scale dry-run/readiness smoke and one GitHub Issue Intelligence
  read-only/local smoke.

## Verified Readiness

- `scripts/mvp_roadmap_runner.py --report`: queue exhausted, all ten MVP roadmaps at 100%, UI live gate still false.
- `build_open_work_completion_status()`: `safe_open_slices=0`, `queue_exhausted=true`, status `live_and_design_gated`.
- `load_version_one_readiness()`: backend contracts ready, release actions disabled, `version_1_0_ready=false` because UI live is required.
- `build_live_affordance_readiness()`: converter execution remains blocked by live/config/bounded-request gates; no tokens, chat IDs or host paths are exposed. Nextcloud copy, Telegram screenshot delivery, and sandbox execution now have bounded live-smoke evidence.
- `build_telegram_todo_digest_live_gate(owner="fuzzy", scheduled_time="09:00", weekdays="mo-fr")`: `ready_for_live_smoke`; one active canonical Telegram todo digest task exists for `0 9 * * 1,2,3,4,5`, next run `2026-07-06T09:00:00`.
- Homeserver redacted Nextcloud env presence check: `/opt/odysseus/.env` and container env both contain `UNIVERSAL_INBOX_NEXTCLOUD_LIVE_WRITE_ENABLED`, `UNIVERSAL_INBOX_NEXTCLOUD_OPERATOR_LIVE_GO`, `NEXTCLOUD_WEBDAV_BASE_URL`, `NEXTCLOUD_WEBDAV_USERNAME`, `NEXTCLOUD_WEBDAV_APP_PASSWORD`, and `NEXTCLOUD_WEBDAV_ROOT`; no values were printed.
- Odysseus AI tool surface: `manage_nextcloud_transfer` is available for readiness, smoke planning and copy-only Universal Inbox writes; it stays admin-only, disabled in Plan Mode and hidden from public MCP exposure by default.
- Homeserver live Nextcloud smoke: Odysseus was recreated with `docker-compose.nextcloud.yml`, joined `odysseus_default` and `nextcloud_default`, resolved `nextcloud-app` from inside the container, and completed a copy-only `manage_nextcloud_transfer` write to `Odysseus/Test/odysseus-universal-inbox-live-smoke-20260705-network-fixed.txt` with sidecar, target-size verification and no secret/host-path exposure.
- Homeserver Telegram live activation: `/opt/odysseus/.env` and the running container have Telegram token/allowed chat, agent chat, reply, polling and image actions enabled. `odysseus-telegram-poll.timer` is active and a manual poll smoke returned `status=poll_ok` without exposing token or chat values.
- Homeserver GUI/screenshot live smoke: Playwright CLI is available in the Odysseus container, a local browser screenshot was written to `data/reports/autonomous_coding_agent/gui-smoke/gui-smoke.png`, the Telegram screenshot live gate returned `ready_for_operator_go`, and the artifact was sent through `telegram_document_reply` as `delivery_mode=photo` with Telegram message id present.
- Homeserver sandbox live smoke: `ODYSSEUS_SANDBOX_RUNNER_BACKEND=host_ssh` is active; sandbox default capabilities include `python`, `node`, `playwright`, `browser_gui` and `screenshot_artifacts`; the host-runner live smoke succeeded for a terminal job and an RW report artifact job with `network_mode=none`, `secrets_attached=false`, and no token/host-path exposure.
- Homeserver MCP workbench live smoke: local `plugins/mcp_server` runtime gate was enabled without `expose_all` or remote exposure. JSON-RPC `initialize`, `tools/list`, and `resources/read` readiness succeeded over container loopback. `tools/list` exposed 12 bounded tools; high-risk tools such as `bash`, `python`, file writes, generic API, settings/token management, raw GitHub writes and `odysseus_call` were absent; `github_issue_find_duplicates` was present as the narrow read-only helper.
- Homeserver observability read-only inventory: systemd user services for Odysseus, Telegram polling, backups and Nextcloud were loaded/healthy enough for read-only status; Odysseus/Nextcloud Podman containers were listed without logs or secrets; local ports `7000`, `8080` and `8100` were listening while `3000`, `9090` and `3100` were not. Redacted metrics, alert routing and diagnostic bridge packets are ready; Prometheus/Loki/Grafana/CrowdSec endpoints remain not configured and no install or mutation was performed.
- Homeserver security tabletop: a synthetic `service_down_security_relevant` incident was classified at level 3/high, policy returned `gated_action`, notification payload stayed dry-run, and remediation planning prepared one service-restart action with `allowed_to_execute=false`, `writes_performed=false`, and operator gate required. No CrowdSec/firewall/restart/token/deploy action was executed.
- Homeserver Memory scale read-only smoke: `build_rag_reindex_dry_run_plan()` ran inside the Odysseus container against live Chroma and returned schema `odysseus.rag_reindex_generation_readonly_plan.v1`, status `ready`, one target, three collections, `read_only=true`, `writes_performed=0`, `rollback_supported=true`, `private_content_visible=false`, `secret_values_visible=false`, and next action `operator_go_required_before_collection_writes`. `/api/memory/stats` returned HTTP 200 with redacted counts only: 63 personal memory entries, vector index healthy, vector index count 63, one vector lane, roles present, and Chroma byte stats present. No reindex, migration or memory/RAG write was executed.
- Homeserver GitHub Issue Intelligence read-only smoke: server-side GitHub sync env is enabled for the allowlisted `fuzzy123-ai/odysseus-fuzzy` repository with a 50-item bound. The first sync attempt detected unusable server-side GitHub credentials and did not expose token values; because the public unauthenticated read gate is explicitly enabled, the final bounded sync fell back to `auth_mode=public_unauthenticated` and completed with `status=synced`, `fetched=0`, `created=0`, `updated=0`, `closed=0`, `provider_writes_performed=0`, and `exit_code=0`. HTTP readiness/duplicate routes stayed healthy; no issue creation, labeling, field update or token exposure occurred.
- Focused verification: `python -m pytest tests/test_telegram_screenshot_delivery.py tests/test_runtime_tool_status.py tests/test_version_one_readiness.py tests/test_calendar_capability_service.py tests/test_task_summary_routes.py -q` passed with 23 tests.
- Focused Memory/GitHub verification: `python -m pytest tests/test_rag_reindex_dry_run.py tests/test_memory_diagnostics.py tests/test_memory_store_stats.py tests/test_memory_perf_suite_metrics.py tests/test_memory_perf_suite_models.py tests/test_memory_perf_suite_reports.py tests/test_memory_perf_suite_raptor.py tests/test_memory_perf_suite_eventlog.py tests/test_github_issue_fields.py tests/test_github_issue_models.py tests/test_github_issue_sync.py tests/test_github_issue_index.py tests/test_github_issue_duplicates.py tests/test_github_issue_tools.py tests/test_github_issue_routes.py tests/test_github_issue_projection.py -q` passed with 78 tests.

## Gate Families

| Priority | Family | Status | Safe preparation done | Gate condition |
| -: | - | - | - | - |
| 10 | Version release | Blocked | Backend readiness packet exists and tests pass. | UI must be live; release/deploy/tag target and rollback decision need explicit Go. |
| 20 | Calendar reminders | Partially open | Canonical 09:00 Mo-Fr Telegram todo digest task exists for owner `fuzzy`; read-only gate is `ready_for_live_smoke`. | Observe one live Telegram delivery and record redacted evidence; CalDAV writeback remains separate. |
| 30 | Autonomous coding | Partially live | Dry-run/runtime contracts are repo-ready; Telegram polling/reply gates are active; sandbox host-runner smoke succeeded. | UI placement and broader autonomous task supervision remain separate gates. |
| 32 | AI GUI tools | Live smoke completed | Screenshot artifact integrity, Playwright CLI smoke, Telegram live-gate packet and Telegram photo delivery smoke are verified. | Consolidated UI tool surface and product UI placement remain separate gates. |
| 35 | MCP workbench | Live smoke completed | Local MCP runtime gate is enabled for loopback; initialize/tools/list/readiness succeeded; high-risk tools remain absent. | Remote/LAN/tunnel exposure and any additional MCP service installation remain separate explicit gates. |
| 40 | Nextcloud import | Live smoke completed | WebDAV env and live switches are present on the homeserver and visible in the container; Odysseus AI has `manage_nextcloud_transfer` for readiness/smoke/copy-only writes; one bounded copy-only live write was verified in Nextcloud. | Private-content import subset, recurring ingestion, and Memory/Raptor writes remain separate gates. |
| 50 | Observability ops | Read-only inventory completed | Debian/systemd/Podman/port inventory and redacted metrics/diagnostic packets were verified without logs/secrets or writes. | Prometheus/Loki/Grafana/CrowdSec installation/configuration and log shipping remain separate live gates. |
| 60 | Security ops | Tabletop completed | Synthetic incident classification, policy, notification dry-run and prepare-only remediation plan were verified. | Real incident notification dispatch, CrowdSec/firewall/restart/token/deploy remediation and lockdown remain separate gated actions. |
| 70 | UI design | Blocked for 1.0 | Backend route contracts are ready; 0.99 live backends are active. | UI owner must decide placement and wire the UI; 1.0 must not be claimed until `VERSION-1-UI-LIVE` is true. |
| 80 | Memory scale | Read-only live smoke completed | Tokenization, cache, diagnostics, live memory stats and live Chroma reindex dry-run are complete with `writes_performed=0`. | Actual live reindex/migration remains a separate operator decision with rollback target and quality evidence. |
| 90 | GitHub issue intelligence | Bounded live-read sync completed | Local duplicate/write-plan contracts, HTTP readiness, duplicate preview and write gate behavior are live-verified; bounded provider sync adapter is deployed behind server env, repo allowlist and confirmation; live sync completed with 0 provider writes. | Issue creation/field writes need a concrete issue payload, duplicate decision if needed, and separate live write Go. |

## Next Executable Gate

Completed first Calendar/Reminder action:

```text
Created canonical task for owner=fuzzy schedule=Mo-Fr 09:00 output=telegram; no CalDAV writeback; no delivery claim until observed.
```

Next bounded smoke:

```text
GO calendar_reminders observe one 09:00 Telegram todo digest delivery and record redacted evidence.
```

Do not treat a broad phrase such as "handle all gates" as permission for Telegram sends, provider writes, host changes, deploys, Nextcloud writes, reindexing, or remediation.
