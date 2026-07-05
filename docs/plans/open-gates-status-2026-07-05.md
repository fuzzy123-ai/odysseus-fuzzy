# Open Gates Status - 2026-07-05

Purpose: close every gate that is safely closable, and make every remaining gate explicit enough that Odysseus must not claim completion without evidence.

## Current Verdict

- MVP backend roadmaps 1-10: 100%.
- Safe repo-only queue: exhausted.
- Version 1.0: not ready until `VERSION-1-UI-LIVE` is true.
- Audit queue: 35 live-gate entries, deduplicated to 24 unique live gates; 5 design-gate entries, deduplicated to 4 unique design gates.
- Completion lanes: 38 distinct gate IDs across live, design, service, operator-subset, post-MVP and release decisions.
- No live action was performed while preparing this status.

## Verified Readiness

- `scripts/mvp_roadmap_runner.py --report`: queue exhausted, all ten MVP roadmaps at 100%, UI live gate still false.
- `build_open_work_completion_status()`: `safe_open_slices=0`, `queue_exhausted=true`, status `live_and_design_gated`.
- `load_version_one_readiness()`: backend contracts ready, release actions disabled, `version_1_0_ready=false` because UI live is required.
- `build_live_affordance_readiness()`: Telegram delivery, sandbox execution, Nextcloud copy and converter execution are blocked by live/config/bounded-request gates; no tokens, chat IDs or host paths are exposed.
- `build_telegram_todo_digest_live_gate(owner="fuzzy", scheduled_time="09:00", weekdays="mo-fr")`: `ready_for_live_smoke`; one active canonical Telegram todo digest task exists for `0 9 * * 1,2,3,4,5`, next run `2026-07-06T09:00:00`.
- Homeserver redacted Nextcloud env presence check: `/opt/odysseus/.env` and container env both contain `UNIVERSAL_INBOX_NEXTCLOUD_LIVE_WRITE_ENABLED`, `UNIVERSAL_INBOX_NEXTCLOUD_OPERATOR_LIVE_GO`, `NEXTCLOUD_WEBDAV_BASE_URL`, `NEXTCLOUD_WEBDAV_USERNAME`, `NEXTCLOUD_WEBDAV_APP_PASSWORD`, and `NEXTCLOUD_WEBDAV_ROOT`; no values were printed.
- Odysseus AI tool surface: `manage_nextcloud_transfer` is available for readiness, smoke planning and copy-only Universal Inbox writes; it stays admin-only, disabled in Plan Mode and hidden from public MCP exposure by default.
- Focused verification: `python -m pytest tests/test_telegram_screenshot_delivery.py tests/test_runtime_tool_status.py tests/test_version_one_readiness.py tests/test_calendar_capability_service.py tests/test_task_summary_routes.py -q` passed with 23 tests.

## Gate Families

| Priority | Family | Status | Safe preparation done | Gate condition |
| -: | - | - | - | - |
| 10 | Version release | Blocked | Backend readiness packet exists and tests pass. | UI must be live; release/deploy/tag target and rollback decision need explicit Go. |
| 20 | Calendar reminders | Partially open | Canonical 09:00 Mo-Fr Telegram todo digest task exists for owner `fuzzy`; read-only gate is `ready_for_live_smoke`. | Observe one live Telegram delivery and record redacted evidence; CalDAV writeback remains separate. |
| 30 | Autonomous coding | Blocked | Dry-run/runtime contracts are repo-ready. | Needs bounded live control path: Telegram supervision, MCP availability or network allowlist. |
| 32 | AI GUI tools | Blocked | Screenshot artifact integrity and Telegram live-gate packets are implemented and tested. | Needs consolidated GUI tool surface, screenshot artifact, Telegram target config and explicit screenshot-delivery Go. |
| 35 | MCP workbench | Blocked | Local contracts are ready. | Needs service availability/setup decision and a bounded smoke. |
| 40 | Nextcloud import | Partially open | WebDAV env and live switches are present on the homeserver and visible in the container; Odysseus AI has `manage_nextcloud_transfer` for readiness/smoke/copy-only writes. | Needs a bounded target/source request for live smoke; private-content import subset and Memory/Raptor writes remain separate. |
| 50 | Observability ops | Blocked | Repo contracts and gate queue exist. | Needs Debian/Podman/log/Grafana/CrowdSec live inventory decisions. |
| 60 | Security ops | Blocked | Incident response models are prepared. | Needs synthetic tabletop Go; remediation/lockdown remain separate gates. |
| 70 | UI design | Blocked | Backend route contracts are ready. | UI owner must decide placement and wire the UI; backend ABC must not invent UI placement. |
| 80 | Memory scale | Deferred/post-backend | Tokenization, cache and diagnostics are complete. | Live reindex/migration needs rollback and quality evidence or explicit deferral. |
| 90 | GitHub issue intelligence | Deferred/post-MVP | Local duplicate/write-plan contracts are complete. | Needs token setup, bounded repo sync and separate write confirmation. |

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
