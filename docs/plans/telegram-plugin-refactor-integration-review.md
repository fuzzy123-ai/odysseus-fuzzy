# Telegram Plugin Refactor Integration Review

Date: 2026-07-06

Status: TGR5/TGR6/TGR8 integration review under Standard ABC

## Scope

This review covers the repo-only Telegram Plugin Refactor artifacts from TGR1
through TGR8. It verifies that route registration, webhook branch execution,
control-command orchestration, shared status/gates, shared reply formatting and
known cleanup work are integrated without enabling live Telegram sends, live
downloads, live STT, provider calls or network mutation.

Out of scope:

- Real Telegram `sendMessage`, `sendDocument`, `sendPhoto` or `sendAudio`.
- Real Telegram `getUpdates` polling against the Telegram API.
- Real Telegram file download or voice download.
- Live STT/provider execution.
- Any persistence of raw chat IDs, tokens, file IDs, transcripts, private
  content or private paths in docs/tests/evidence.

## Integration Map

| Area | Artifact | Integration evidence |
| --- | --- | --- |
| Route inventory | `docs/plans/telegram-plugin-route-command-inventory.md` | TGR1 inventory maps route/tool/command families and gates. |
| Route split | `plugins/telegram/routes_admin.py`, `routes_polling.py`, `routes_outbound.py`, `routes_webhook.py` | Route-contract tests verify route registration remains delegated. |
| Shared gate/status model | `plugins/telegram/status.py` | Status/readiness tests verify redacted gate reporting and default-off live behavior. |
| Webhook branch service | `plugins/telegram/webhook_service.py` | Webhook-service tests cover parse/store, summaries, branch execution, redacted event payloads and agent-turn reply selection. |
| Control-command service | `plugins/telegram/control_service.py` | Control-service tests cover Agent-Task, DSGVO, Calendar, Universal Inbox, Project-Intake and new-chat orchestration behind injected helpers. |
| Shared reply formatting | `plugins/telegram/formatting.py` | Formatting tests cover calendar, DSGVO, Universal Inbox, Project-Intake, attachment, export, Nextcloud blocker, agent failure, agent-turn reply selection, Agent-Task and new-chat wording. |
| Polling integration | `plugins/telegram/polling.py` | Polling tests verify fakeable polling, deterministic capability response, typing lifecycle, attachments, Project-Intake and voice fake/STT boundaries. |
| Compatibility wrappers | `plugins/telegram/attachments.py`, `export.py`, `project_intake.py`, `plugin.py`, `polling.py` | Broader Telegram suite verifies compatibility wrappers preserve behavior while delegating moved wording. |

## Redaction And Safety Guarantees

The repo-only integration keeps these safety flags invariant:

- `token_value_visible` remains false in public readiness/reply paths.
- raw chat IDs are not persisted in Telegram history fixtures or redacted event
  payloads.
- raw attachment content, filenames and identifiers are suppressed in Universal
  Inbox events unless represented through safe metadata.
- raw project-intake text is not persisted in public summaries or redacted
  events.
- raw agent prompts/replies are not stored in agent-turn event payloads.
- live send/download/STT behavior remains guarded by environment gates and fake
  transport injection in tests.

## Verification

Focused compile:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m py_compile plugins\telegram\formatting.py plugins\telegram\control_service.py plugins\telegram\polling.py plugins\telegram\webhook_service.py plugins\telegram\plugin.py tests\test_telegram_formatting.py tests\test_telegram_control_service.py tests\test_telegram_webhook_service.py tests\test_telegram_plugin.py
```

Broad repo-only Telegram suite:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_telegram_formatting.py tests\test_telegram_attachment_ocr.py tests\test_telegram_control_service.py tests\test_telegram_route_contract.py tests\test_telegram_status.py tests\test_telegram_webhook_service.py tests\test_telegram_plugin.py tests\test_telegram_text_boundary.py tests\test_telegram_voice_pipeline.py tests\test_telegram_voice_boundary.py tests\test_telegram_image_actions.py tests\test_telegram_screenshot_delivery.py tests\test_telegram_task_orchestrator.py tests\test_telegram_truth_runtime.py tests\test_autonomous_coding_remote_control_smoke.py -q
```

Expected result for this review: 223 tests passed with only the known
SQLAlchemy `declarative_base()` deprecation warning.

## Deferred Gates

Gate: `TGR-LIVE-SEND-GO`

State after this review: deferred

Required before live action: explicit bounded operator Go, target chat
confirmation, message/document/photo/audio class, expected transport, rollback
or no-op plan and redacted evidence capture.

Gate: `TGR-VOICE-DOWNLOAD-GO`

State after this review: deferred

Required before live action: explicit bounded operator Go, one known voice
message/file reference, privacy decision for transcript handling, STT provider
choice and redacted evidence capture.

Gate: `TGR-BEHAVIOR-CHANGE-GO`

State after this review: deferred

Required before semantic changes: separate product slice, characterization
coverage for old/new behavior and explicit operator acceptance that the change
is not merely a refactor.

## Conclusion

TGR5 command/service split, TGR6 formatting normalization and TGR8 cleanup are
integrated as repo-only work. The Telegram plugin still has live validation
gates, but the tested local surface is now split into route modules, service
helpers and shared formatting without behavior drift or live mutation.
