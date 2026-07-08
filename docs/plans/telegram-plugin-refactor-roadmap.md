# Telegram Plugin Refactor Roadmap

Status: repo-only complete under Standard ABC; live gates deferred

ABC mode: Standard ABC

## Goal

Slim the Telegram plugin into smaller characterized services and route modules
while preserving existing safe-by-default behavior for text, voice, files,
Universal Inbox, reminders, DSGVO, screenshots, notifications and gated
outbound delivery.

## Current Evidence

- `plugins/telegram/plugin.py` is the central plugin entry and is very broad.
  It imports DSGVO helpers, secure channel policy, truth gates, image actions,
  screenshot delivery, voice pipeline, Universal Inbox readiness, attachment
  helpers, export helpers, live pipeline, outbound, polling and project intake.
- Supporting modules already exist: `admin.py`, `attachments.py`, `export.py`,
  `live_pipeline.py`, `outbound.py`, `parsing.py`, `polling.py`,
  `project_intake.py`, `stores.py`.
- Tests exist for Telegram plugin, text boundary, voice boundary, voice
  pipeline, image actions, screenshot delivery, task orchestrator and truth
  runtime.
- TGR1 inventory is captured in
  `docs/plans/telegram-plugin-route-command-inventory.md`.
- TGR2 route/tool/command characterization now lives in
  `tests/test_telegram_route_contract.py`.
- TGR3 shared gate/status objects now live in `plugins/telegram/status.py` and
  are exposed additively through `build_telegram_readiness`.
- TGR4 route extraction is complete:
  `plugins/telegram/routes_admin.py` now owns `/status`, `/history` and `/app`,
  `plugins/telegram/routes_polling.py` owns `/poll`, and
  `plugins/telegram/routes_outbound.py` owns `/reply` plus document reply
  routes. `plugins/telegram/routes_webhook.py` owns `/webhook`.
- TGR5 command/service split is complete for the repo-only scope:
  `plugins/telegram/webhook_service.py` owns webhook parse/store intake,
  invalid-update redaction, media/attachment/export/project/control/agent-turn
  branch execution and public/redacted summaries; `plugins/telegram/control_service.py`
  owns control-command orchestration.
- TGR6 formatting normalization is complete for the repo-only scope:
  `plugins/telegram/formatting.py` owns deterministic reply wording and
  agent-turn reply selection used by polling, webhook and compatibility wrappers.
- TGR5/TGR6/TGR8 integration review lives in
  `docs/plans/telegram-plugin-refactor-integration-review.md`.

## Mode

Standard ABC. Repo-only refactor with characterization tests first. Live
Telegram sends, downloads or polling smokes require explicit operator Go.

## Non-goals

- Do not change bot behavior in the first refactor slices.
- Do not send Telegram messages.
- Do not download real user audio/files.
- Do not print tokens, chat ids, file ids, transcript text or raw messages.
- Do not mix UI redesign with plugin decomposition.

## What Must Be Done

- Characterize current plugin route map and command behavior before moving
  code.
- Split plugin concerns into route/service modules:
  admin/status, privacy/DSGVO, control commands, inbound webhook/polling,
  outbound delivery, voice, attachments, Universal Inbox, export, screenshots,
  reminders/tasks, project intake and notifications.
- Keep `plugin.py` as manifest plus setup composition.
- Create shared Telegram gate/status objects so every feature reports readiness
  the same way.
- Normalize reply formatting and error/result payloads.
- Add module-level redaction tests.
- Preserve all existing environment gates and default-off live behavior.
- Add a migration checklist for every route and tool registration.

## Slice Queue

| Slice | Class | Owner | Allowed paths | Tests |
| --- | --- | --- | --- | --- |
| TGR1 route and command inventory | safe_offline | Alice | this roadmap, optional inventory doc | Done: `docs/plans/telegram-plugin-route-command-inventory.md` |
| TGR2 characterization tests | repo_only | Bob | `tests/test_telegram_plugin.py`, focused new tests | Done: `tests/test_telegram_route_contract.py` |
| TGR3 shared status/gate model | repo_only | Bob | `plugins/telegram/status.py`, tests | Done: `tests/test_telegram_status.py` |
| TGR4 route module split | repo_only | Bob | new `plugins/telegram/routes_*.py`, minimal `plugin.py` edits | Done: route registration extracted |
| TGR5 command/service split | repo_only | Bob | `plugins/telegram/*_service.py` modules | Done: webhook/control service split integrated |
| TGR6 formatting normalization | repo_only | Bob | formatting/outbound/parsing modules | Done: deterministic reply formatting centralized in `plugins/telegram/formatting.py` |
| TGR7 live gate packet refresh | needs_live_go | Charlie | docs/evidence only after Go | live smoke only if approved |
| TGR8 cleanup pass | repo_only | Charlie | remove dead compatibility only after tests | Done for known unreachable compatibility bodies; full Telegram focused suite passed |
| TGR9 integration review | repo_only | Charlie | docs/tests | Done: `docs/plans/telegram-plugin-refactor-integration-review.md` |

## Gate Queue

Gate: `TGR-LIVE-SEND-GO`
Class: needs_live_go
Blocks: real sendMessage/sendDocument/sendPhoto/sendAudio smoke
Decision needed: approve one bounded Telegram live send
Safe preparation done: fake/outbound tests, redacted status and shared gate packet
Risk if bypassed: chat id/token/message leakage or unwanted user message
Next safe slice: fake outbound tests

Gate: `TGR-VOICE-DOWNLOAD-GO`
Class: needs_live_go
Blocks: real Telegram voice download/STT smoke
Decision needed: approve fresh bounded voice test
Safe preparation done: fakeable STT and default-off gates
Risk if bypassed: raw audio/transcript leakage
Next safe slice: voice unit tests

Gate: `TGR-BEHAVIOR-CHANGE-GO`
Class: blocked
Blocks: changing command semantics during refactor
Decision needed: behavior changes must be separate product slices
Safe preparation done: route/tool/representative command characterization tests
Risk if bypassed: refactor hides regressions
Next safe slice: pure move/extract refactor

## Execution Progress

2026-07-05:
- TGR1 done: route, tool, command-family, env-gate and migration-checklist
  inventory written without recording Telegram secret values or raw payloads.
- TGR2 done for the public setup contract: route paths/methods, registered tool
  names/permissions/required fields and representative slash-command aliases are
  characterized in `tests/test_telegram_route_contract.py`.
- TGR3 done additively: `plugins/telegram/status.py` provides uniform redacted
  `TelegramGateStatus` records, and readiness now embeds `readiness_gates`
  while preserving legacy fields.
- TGR4 started safely: `/status`, `/history` and `/app` moved into
  `plugins/telegram/routes_admin.py`, and `/poll` moved into
  `plugins/telegram/routes_polling.py`, both with explicit dependencies;
  `/reply` and document routes moved into `plugins/telegram/routes_outbound.py`;
  `/webhook` route registration moved into `plugins/telegram/routes_webhook.py`.
- Verification passed:
  `pytest tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_plugin.py::test_readiness_is_redacted_and_network_send_disabled tests/test_telegram_plugin.py::test_status_route_returns_redacted_readiness tests/test_telegram_plugin.py::test_plugin_routes_call_admin_gate tests/test_telegram_plugin.py::test_task_control_commands_are_detected tests/test_telegram_voice_boundary.py tests/test_telegram_formatting.py`
  with 26 passed and 1 known SQLAlchemy deprecation warning.
- Post-TGR4-start verification passed:
  `pytest tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py`
  with 144 passed and 1 known SQLAlchemy deprecation warning.
- Post-poll-route-split verification passed with the same command: 144 passed
  and 1 known SQLAlchemy deprecation warning.
- Post-outbound-route-split focused verification passed:
  `pytest tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_plugin_routes_call_admin_gate tests/test_telegram_plugin.py::test_reply_route_is_blocked_without_explicit_gate tests/test_telegram_plugin.py::test_reply_route_rejects_disallowed_chat_when_reply_gate_enabled tests/test_telegram_plugin.py::test_reply_route_blocks_sensitive_classification_by_channel_policy tests/test_telegram_plugin.py::test_reply_route_allows_public_classification_by_channel_policy tests/test_telegram_plugin.py::test_reply_route_truth_gates_unverified_success_before_send tests/test_telegram_plugin.py::test_reply_route_records_success_and_failure_history tests/test_telegram_plugin.py::test_document_reply_route_sends_screenshot_artifact_as_photo tests/test_telegram_plugin.py::test_document_reply_route_blocks_corrupt_screenshot_before_transport tests/test_telegram_plugin.py::test_document_reply_preview_reports_screenshot_packet_without_transport tests/test_telegram_plugin.py::test_document_reply_preview_returns_blocked_packet_for_corrupt_screenshot tests/test_telegram_plugin.py::test_document_reply_live_gate_ready_does_not_send tests/test_telegram_plugin.py::test_document_reply_live_gate_reports_missing_reply_gate tests/test_telegram_plugin.py::test_document_reply_live_gate_reports_blocked_artifact tests/test_telegram_plugin.py::test_document_reply_route_rejects_non_artifact_paths tests/test_telegram_plugin.py::test_document_reply_route_fetches_nextcloud_file_and_sends_document tests/test_telegram_plugin.py::test_document_reply_preview_for_nextcloud_does_not_fetch_or_send tests/test_telegram_formatting.py::test_reply_route_falls_back_to_classic_html_when_final_rich_fails`
  with 22 passed and 1 known SQLAlchemy deprecation warning.
- Post-outbound-route-split broader verification passed with the Telegram
  roadmap suite: 144 passed and 1 known SQLAlchemy deprecation warning.
- Post-webhook-route-wrapper verification passed:
  `pytest tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_plugin_routes_call_admin_gate tests/test_telegram_plugin.py::test_webhook_route_stores_inbound_and_returns_agent_bridge tests/test_telegram_plugin.py::test_webhook_blocks_disallowed_chat_and_persists_redacted_block_event tests/test_telegram_plugin.py::test_webhook_rejects_malformed_update_without_raw_payload_leak tests/test_telegram_plugin.py::test_webhook_invokes_agent_turn_handler_and_gated_reply tests/test_telegram_plugin.py::test_webhook_keeps_typing_indicator_until_agent_reply tests/test_telegram_plugin.py::test_webhook_image_action_uses_injected_worker_without_raw_image_payload tests/test_telegram_plugin.py::test_webhook_voice_pipeline_can_create_fake_stt_agent_turn_without_persisting_transcript`
  with 12 passed and 1 known SQLAlchemy deprecation warning.
- Final TGR4 route-contract verification passed with a guard that `plugin.py`
  delegates route registration and contains no local `@router.get` or
  `@router.post` handlers. Broader Telegram roadmap suite passed with 145 tests
  and 1 known SQLAlchemy deprecation warning.
- TGR5 started safely: `plugins/telegram/webhook_service.py` now provides
  `parse_and_store_webhook_update`, keeping invalid-update event recording and
  redacted inbound append outside the route handler while preserving the public
  `400 invalid telegram update` behavior.
- Post-webhook-intake-service focused verification passed:
  `pytest tests/test_telegram_webhook_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_webhook_route_stores_inbound_and_returns_agent_bridge tests/test_telegram_plugin.py::test_webhook_rejects_malformed_update_without_raw_payload_leak tests/test_telegram_plugin.py::test_webhook_blocks_disallowed_chat_and_persists_redacted_block_event tests/test_telegram_plugin.py::test_webhook_voice_pipeline_can_create_fake_stt_agent_turn_without_persisting_transcript`
  with 11 passed and 1 known SQLAlchemy deprecation warning.
- Post-webhook-intake-service broader verification passed with the Telegram
  roadmap suite plus the new service tests: 147 passed and 1 known SQLAlchemy
  deprecation warning.
- TGR5 continued safely: `plugins/telegram/webhook_service.py` now also owns
  the shared public webhook response envelope through
  `build_webhook_response_payload`, so control-command, export, project-intake
  and agent-turn branches keep one redacted response shape while `plugin.py`
  continues thinning into orchestration-only glue.
- Post-webhook-response-service focused verification passed:
  `pytest tests/test_telegram_webhook_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_webhook_route_stores_inbound_and_returns_agent_bridge tests/test_telegram_plugin.py::test_webhook_rejects_malformed_update_without_raw_payload_leak tests/test_telegram_plugin.py::test_webhook_blocks_disallowed_chat_and_persists_redacted_block_event tests/test_telegram_plugin.py::test_webhook_invokes_agent_turn_handler_and_gated_reply tests/test_telegram_plugin.py::test_webhook_keeps_typing_indicator_until_agent_reply tests/test_telegram_plugin.py::test_webhook_voice_pipeline_can_create_fake_stt_agent_turn_without_persisting_transcript`
  with 16 passed and 1 known SQLAlchemy deprecation warning.
- TGR5 continued safely: `build_webhook_attachment_event_payload` now owns the
  redacted Universal Inbox attachment event payload for webhook intake,
  including count coercion, warning-code tuple normalization and raw-content /
  raw-identifier / filename visibility flags.
- Post-webhook-attachment-event-service focused verification passed:
  `pytest tests/test_telegram_webhook_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_webhook_route_stores_inbound_and_returns_agent_bridge tests/test_telegram_plugin.py::test_webhook_rejects_malformed_update_without_raw_payload_leak tests/test_telegram_plugin.py::test_webhook_blocks_disallowed_chat_and_persists_redacted_block_event tests/test_telegram_plugin.py::test_webhook_invokes_agent_turn_handler_and_gated_reply tests/test_telegram_plugin.py::test_webhook_keeps_typing_indicator_until_agent_reply tests/test_telegram_plugin.py::test_webhook_image_action_uses_injected_worker_without_raw_image_payload tests/test_telegram_plugin.py::test_webhook_voice_pipeline_can_create_fake_stt_agent_turn_without_persisting_transcript`
  with 18 passed and 1 known SQLAlchemy deprecation warning.
- TGR5 continued safely: `build_webhook_export_plan_event_payload` and
  `build_webhook_export_delivery_event_payload` now own redacted Universal
  Inbox export-plan and export-delivery event payloads for webhook intake,
  including byte-count coercion and host-path visibility suppression.
- Post-webhook-export-event-service focused verification passed:
  `pytest tests/test_telegram_webhook_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_webhook_route_stores_inbound_and_returns_agent_bridge tests/test_telegram_plugin.py::test_webhook_rejects_malformed_update_without_raw_payload_leak tests/test_telegram_plugin.py::test_webhook_blocks_disallowed_chat_and_persists_redacted_block_event tests/test_telegram_plugin.py::test_webhook_invokes_agent_turn_handler_and_gated_reply tests/test_telegram_plugin.py::test_webhook_keeps_typing_indicator_until_agent_reply tests/test_telegram_plugin.py::test_webhook_image_action_uses_injected_worker_without_raw_image_payload tests/test_telegram_plugin.py::test_webhook_voice_pipeline_can_create_fake_stt_agent_turn_without_persisting_transcript tests/test_telegram_plugin.py::test_document_reply_route_fetches_nextcloud_file_and_sends_document tests/test_telegram_plugin.py::test_document_reply_preview_for_nextcloud_does_not_fetch_or_send`
  with 22 passed and 1 known SQLAlchemy deprecation warning.
- TGR5 continued safely: `build_webhook_export_plan_summary` and
  `build_webhook_project_intake_summary` now own the public webhook summaries
  for export-plan and project-intake branches, keeping raw content and host-path
  details out of route response construction.
- Post-webhook-summary-service focused verification passed with the same
  webhook/route/plugin/document command and 24 passed plus 1 known SQLAlchemy
  deprecation warning.
- TGR5 continued safely: `build_webhook_control_command_event_payload` and
  `build_webhook_control_command_summary` now own the redacted event payload and
  public response summary for handled webhook control commands.
- Post-webhook-control-command-service focused verification passed:
  `pytest tests/test_telegram_webhook_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_webhook_route_stores_inbound_and_returns_agent_bridge tests/test_telegram_plugin.py::test_webhook_rejects_malformed_update_without_raw_payload_leak tests/test_telegram_plugin.py::test_webhook_blocks_disallowed_chat_and_persists_redacted_block_event tests/test_telegram_plugin.py::test_webhook_invokes_agent_turn_handler_and_gated_reply tests/test_telegram_plugin.py::test_webhook_keeps_typing_indicator_until_agent_reply tests/test_telegram_plugin.py::test_task_control_commands_are_detected`
  with 23 passed and 1 known SQLAlchemy deprecation warning.
- Post-TGR5-service-extraction broader verification passed:
  `pytest tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py`
  with 157 passed and 1 known SQLAlchemy deprecation warning.
- TGR5 continued safely: `build_webhook_agent_turn_event_payload` now owns the
  redacted webhook `agent_turn` event payload, keeping prompt and reply text out
  of event construction while preserving the route response shape.
- Post-webhook-agent-turn-event-service focused verification passed:
  `pytest tests/test_telegram_webhook_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_webhook_route_stores_inbound_and_returns_agent_bridge tests/test_telegram_plugin.py::test_webhook_invokes_agent_turn_handler_and_gated_reply tests/test_telegram_plugin.py::test_webhook_keeps_typing_indicator_until_agent_reply tests/test_telegram_plugin.py::test_webhook_rejects_malformed_update_without_raw_payload_leak tests/test_telegram_plugin.py::test_webhook_blocks_disallowed_chat_and_persists_redacted_block_event`
  with 23 passed and 1 known SQLAlchemy deprecation warning.
- TGR5 continued safely: `run_webhook_project_intake_branch` now owns the
  text-only project-intake preview and gated reply branch behind injected
  helpers, preserving fakeability and keeping the route handler thinner.
- Post-webhook-project-intake-branch-service focused verification passed:
  `pytest tests/test_telegram_webhook_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_webhook_route_stores_inbound_and_returns_agent_bridge tests/test_telegram_plugin.py::test_webhook_invokes_agent_turn_handler_and_gated_reply tests/test_telegram_plugin.py::test_webhook_rejects_malformed_update_without_raw_payload_leak tests/test_telegram_plugin.py::test_webhook_blocks_disallowed_chat_and_persists_redacted_block_event`
  with 25 passed and 1 known SQLAlchemy deprecation warning.
- Post-project-intake-service broader verification passed:
  `pytest tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py`
  with 161 passed and 1 known SQLAlchemy deprecation warning.
- TGR5 continued safely: `run_webhook_attachment_branch` now owns the
  attachment webhook branch behind injected helpers: attachment pipeline,
  inbound status refresh, redacted attachment event, optional memory auto-write
  metadata and gated inbox reply. `plugin.py` now keeps only composition.
- Post-webhook-attachment-branch-service focused verification passed:
  `pytest tests/test_telegram_webhook_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_webhook_route_stores_inbound_and_returns_agent_bridge tests/test_telegram_plugin.py::test_webhook_image_action_uses_injected_worker_without_raw_image_payload tests/test_telegram_plugin.py::test_webhook_invokes_agent_turn_handler_and_gated_reply tests/test_telegram_plugin.py::test_webhook_rejects_malformed_update_without_raw_payload_leak tests/test_telegram_plugin.py::test_webhook_blocks_disallowed_chat_and_persists_redacted_block_event`
  with 28 passed and 1 known SQLAlchemy deprecation warning.
- Redaction test hardening: `test_reply_route_records_success_and_failure_history`
  now checks for a raw chat-id JSON string instead of matching incidental
  timestamp digits, preserving the no-raw-chat-id assertion without flaking on
  generated numeric fields.
- Post-attachment-service broader verification passed:
  `pytest tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py`
  with 163 passed and 1 known SQLAlchemy deprecation warning.
- TGR5 continued safely: `run_webhook_attachment_export_branch` now owns the
  text-only recent attachment export branch behind injected helpers: export
  plan execution, redacted plan event append, gated text fallback, gated
  document delivery and redacted delivery event append. `plugin.py` now keeps
  only branch composition and public response assembly for this path.
- Post-webhook-export-branch-service focused verification passed:
  `pytest tests/test_telegram_webhook_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_webhook_route_stores_inbound_and_returns_agent_bridge tests/test_telegram_plugin.py::test_webhook_invokes_agent_turn_handler_and_gated_reply tests/test_telegram_plugin.py::test_webhook_rejects_malformed_update_without_raw_payload_leak tests/test_telegram_plugin.py::test_webhook_blocks_disallowed_chat_and_persists_redacted_block_event tests/test_telegram_plugin.py::test_document_reply_route_fetches_nextcloud_file_and_sends_document tests/test_telegram_plugin.py::test_document_reply_preview_for_nextcloud_does_not_fetch_or_send`
  with 34 passed and 1 known SQLAlchemy deprecation warning.
- Post-export-branch-service broader verification passed:
  `pytest tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py`
  with 168 passed and 1 known SQLAlchemy deprecation warning.
- TGR5 continued safely: `run_webhook_control_command_branch` now owns webhook
  control-command detection, injected command handling and redacted event append.
  `plugin.py` now delegates this branch before public response assembly.
- Post-webhook-control-branch-service focused verification passed:
  `pytest tests/test_telegram_webhook_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_task_control_commands_are_detected tests/test_telegram_plugin.py::test_webhook_route_stores_inbound_and_returns_agent_bridge tests/test_telegram_plugin.py::test_webhook_blocks_disallowed_chat_and_persists_redacted_block_event tests/test_telegram_plugin.py::test_webhook_invokes_agent_turn_handler_and_gated_reply`
  with 34 passed and 1 known SQLAlchemy deprecation warning.
- Post-control-branch-service broader verification passed:
  `pytest tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py`
  with 170 passed and 1 known SQLAlchemy deprecation warning.
- TGR5 continued safely: `run_webhook_agent_turn_branch` now owns webhook
  agent-turn branch execution behind injected helpers: session binding,
  redacted bridge rebuild, deterministic/async agent turn selection, typing
  pulse cleanup, redacted event append and gated reply. `plugin.py` now keeps
  only branch ordering and public response assembly for this path.
- Post-webhook-agent-turn-branch-service focused verification passed:
  `pytest tests/test_telegram_webhook_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_webhook_route_stores_inbound_and_returns_agent_bridge tests/test_telegram_plugin.py::test_webhook_invokes_agent_turn_handler_and_gated_reply tests/test_telegram_plugin.py::test_webhook_keeps_typing_indicator_until_agent_reply tests/test_telegram_plugin.py::test_webhook_rejects_malformed_update_without_raw_payload_leak tests/test_telegram_plugin.py::test_webhook_blocks_disallowed_chat_and_persists_redacted_block_event`
  with 37 passed and 1 known SQLAlchemy deprecation warning.
- Post-agent-turn-branch-service broader verification passed:
  `pytest tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py`
  with 172 passed and 1 known SQLAlchemy deprecation warning.
- TGR5 control-command module split started safely:
  `plugins/telegram/control_service.py` now owns Agent-Task control command
  execution and the redacted public task-record view. `plugin.py` keeps
  compatibility wrappers for existing ops smoke imports while delegating the
  behavior to the service module.
- Post-agent-task-control-service focused verification passed:
  `pytest tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_task_control_commands_are_detected tests/test_telegram_plugin.py::test_task_control_status_and_pause_use_redacted_ledger tests/test_telegram_plugin.py::test_task_control_events_are_filterable_for_coding_runner tests/test_autonomous_coding_remote_control_smoke.py`
  with 14 passed and 1 known SQLAlchemy deprecation warning.
- Post-agent-task-control-service broader verification passed:
  `pytest tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py tests/test_autonomous_coding_remote_control_smoke.py`
  with 178 passed and 1 known SQLAlchemy deprecation warning.
- TGR5 control-command module split continued safely:
  `handle_dsgvo_control_command` in `plugins/telegram/control_service.py` now
  owns DSGVO control command orchestration behind injected state, reply, pin and
  bridge helpers. `plugin.py` still owns the concrete settings/pin functions so
  existing monkeypatch and ops-smoke contracts keep working.
- Post-dsgvo-control-service focused verification passed:
  `pytest tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_telegram_control_command_detects_dsgvo_aliases tests/test_telegram_plugin.py::test_polling_cycle_dsgvo_command_updates_settings_without_agent_turn tests/test_telegram_plugin.py::test_dsgvo_command_from_blocked_chat_does_not_change_settings tests/test_telegram_plugin.py::test_polling_cycle_dsgvo_disable_unpins_privacy_message tests/test_telegram_plugin.py::test_polling_cycle_dsgvo_text_uses_secure_session_slot`
  with 16 passed and 1 known SQLAlchemy deprecation warning.
- Post-dsgvo-control-service broader verification passed:
  `pytest tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py tests/test_autonomous_coding_remote_control_smoke.py`
  with 181 passed and 1 known SQLAlchemy deprecation warning.
- TGR5 control-command module split continued safely:
  `handle_calendar_control_command` in `plugins/telegram/control_service.py`
  now owns Kalender control orchestration, including command-tail parsing,
  reminder/todo digest argument parsing, Telegram reply formatting and injected
  Calendar Capability service calls. `plugin.py` keeps only the thin wrapper
  that injects the concrete calendar helpers.
- Post-calendar-control-service focused verification passed:
  `pytest tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_telegram_control_command_detects_calendar_commands tests/test_telegram_plugin.py::test_polling_cycle_calendar_status_replies_without_agent_turn tests/test_telegram_plugin.py::test_polling_cycle_calendar_reminder_create_and_update tests/test_telegram_plugin.py::test_polling_cycle_calendar_todo_digest_creates_single_task`
  with 18 passed and 1 known SQLAlchemy deprecation warning.
- Post-calendar-control-service broader verification passed:
  `pytest tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py tests/test_autonomous_coding_remote_control_smoke.py`
  with 184 passed and 1 known SQLAlchemy deprecation warning.
- TGR5 control-command module split continued safely:
  `handle_universal_inbox_control_command` in
  `plugins/telegram/control_service.py` now owns Universal Inbox status,
  Nextcloud review confirm/status and Memory/Raptor review confirm/status
  orchestration behind injected readiness, transfer, format, write and bridge
  helpers. `plugin.py` delegates the branch while retaining the concrete
  repo/local helper implementations and live gates.
- Post-universal-inbox-control-service focused verification passed:
  `py_compile plugins/telegram/plugin.py plugins/telegram/control_service.py tests/test_telegram_control_service.py tests/test_telegram_plugin.py`;
  `pytest tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_telegram_control_command_detects_universal_inbox_status tests/test_telegram_plugin.py::test_polling_cycle_universal_inbox_command_replies_without_agent_turn tests/test_telegram_plugin.py::test_review_ok_confirms_latest_partial_universal_inbox_attachment tests/test_telegram_plugin.py::test_review_ok_blocks_nextcloud_live_copy_without_chat_credentials tests/test_telegram_plugin.py::test_review_memory_ok_confirms_latest_memory_write_intent tests/test_telegram_plugin.py::test_review_memory_ok_reports_blocked_when_memory_writer_missing`
  with 24 passed and 1 known SQLAlchemy deprecation warning.
- Post-universal-inbox-control-service broader verification passed:
  `pytest tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py tests/test_autonomous_coding_remote_control_smoke.py`
  with 188 passed and 1 known SQLAlchemy deprecation warning.
- Todo-digest notification defect fixed and hardened in the shared notification
  contract: scheduled `todo_digest` Telegram deliveries now use
  `render_mode=plain`, legacy tasks identified by name/body are treated as
  digests, and multiline list structure is preserved without the generic
  `[Odysseus]` prefix or metadata footer. Verification passed with
  `pytest tests/test_todo_digest.py tests/test_user_notification_contract.py tests/test_task_scheduler_delivery.py`
  with 18 passed and 1 known SQLAlchemy deprecation warning.
- TGR5 control-command module split continued safely:
  `handle_project_intake_control_command` in
  `plugins/telegram/control_service.py` now owns Project-Intake review status,
  confirm and hold orchestration behind injected bridge, apply and formatting
  helpers. `plugin.py` delegates this branch while keeping concrete
  Project-Intake apply/format helpers and registry paths local.
- Post-project-intake-control-service focused verification passed:
  `py_compile plugins/telegram/plugin.py plugins/telegram/control_service.py tests/test_telegram_control_service.py tests/test_telegram_plugin.py`;
  `pytest tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_polling_cycle_project_intake_preview_for_mobile_plan tests/test_telegram_plugin.py::test_project_commands_report_and_confirm_latest_intake_review`
  with 23 passed and 1 known SQLAlchemy deprecation warning.
- Post-project-intake-control-service broader verification passed:
  `pytest tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py tests/test_autonomous_coding_remote_control_smoke.py`
  with 191 passed and 1 known SQLAlchemy deprecation warning.
- TGR5 control-command module split completed for the remaining new-chat
  branch: `handle_new_chat_control_command` in
  `plugins/telegram/control_service.py` now owns `/new` session rebinding,
  reply text and pending/bound status selection behind injected session,
  creator, reply and bridge helpers. `plugin.py` delegates the branch.
- Post-new-chat-control-service focused verification passed:
  `py_compile plugins/telegram/plugin.py plugins/telegram/control_service.py tests/test_telegram_control_service.py tests/test_telegram_plugin.py`;
  `pytest tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_polling_cycle_new_command_rebinds_session_without_agent_turn tests/test_telegram_plugin.py::test_polling_cycle_project_intake_preview_for_mobile_plan tests/test_telegram_plugin.py::test_project_commands_report_and_confirm_latest_intake_review`
  with 26 passed and 1 known SQLAlchemy deprecation warning.
- Post-new-chat-control-service broader verification passed:
  `pytest tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py tests/test_autonomous_coding_remote_control_smoke.py`
  with 193 passed and 1 known SQLAlchemy deprecation warning.
- TGR6 formatting normalization started safely:
  `plugins/telegram/formatting.py` now owns deterministic Telegram reply
  formatters for calendar readiness, agenda/reminders and calendar write
  results. `plugins/telegram/control_service.py` imports those helpers instead
  of carrying local formatting functions, keeping control orchestration and
  reply wording separate.
- Post-calendar-formatting-normalization focused verification passed:
  `py_compile plugins/telegram/formatting.py plugins/telegram/control_service.py tests/test_telegram_formatting.py tests/test_telegram_control_service.py`;
  `pytest tests/test_telegram_formatting.py tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_polling_cycle_calendar_status_replies_without_agent_turn tests/test_telegram_plugin.py::test_polling_cycle_calendar_reminder_create_and_update tests/test_telegram_plugin.py::test_polling_cycle_calendar_todo_digest_creates_single_task`
  with 40 passed and 1 known SQLAlchemy deprecation warning.
- Post-calendar-formatting-normalization broader verification passed:
  `pytest tests/test_telegram_formatting.py tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py tests/test_autonomous_coding_remote_control_smoke.py`
  with 207 passed and 1 known SQLAlchemy deprecation warning.
- TGR6 formatting normalization continued safely:
  `format_dsgvo_reply_text` now lives in `plugins/telegram/formatting.py`.
  `plugin.py` keeps a thin `_dsgvo_reply_text` compatibility wrapper that
  supplies the current DSGVO mode while the deterministic wording lives with
  the other Telegram reply formatters.
- Post-dsgvo-formatting-normalization focused verification passed:
  `py_compile plugins/telegram/formatting.py plugins/telegram/plugin.py tests/test_telegram_formatting.py tests/test_telegram_plugin.py`;
  `pytest tests/test_telegram_formatting.py tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_plugin.py::test_telegram_control_command_detects_dsgvo_aliases tests/test_telegram_plugin.py::test_polling_cycle_dsgvo_command_updates_settings_without_agent_turn tests/test_telegram_plugin.py::test_dsgvo_command_from_blocked_chat_does_not_change_settings tests/test_telegram_plugin.py::test_polling_cycle_dsgvo_disable_unpins_privacy_message tests/test_telegram_plugin.py::test_polling_cycle_dsgvo_text_uses_secure_session_slot`
  with 43 passed and 1 known SQLAlchemy deprecation warning.
- Post-dsgvo-formatting-normalization broader verification passed:
  `pytest tests/test_telegram_formatting.py tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py tests/test_autonomous_coding_remote_control_smoke.py`
  with 208 passed and 1 known SQLAlchemy deprecation warning.
- TGR6 formatting normalization continued safely:
  `format_universal_inbox_review_status` and
  `format_universal_inbox_memory_review_status` now live in
  `plugins/telegram/formatting.py`. `plugins/telegram/attachments.py` keeps the
  previous underscored helper names as compatibility wrappers while delegating
  deterministic review wording to the shared formatting module.
- Post-universal-inbox-review-formatting-normalization focused verification
  passed:
  `py_compile plugins/telegram/formatting.py plugins/telegram/attachments.py tests/test_telegram_formatting.py tests/test_telegram_attachment_ocr.py tests/test_telegram_plugin.py`;
  `pytest tests/test_telegram_formatting.py tests/test_telegram_attachment_ocr.py tests/test_telegram_control_service.py tests/test_telegram_plugin.py::test_polling_cycle_universal_inbox_command_replies_without_agent_turn tests/test_telegram_plugin.py::test_review_ok_confirms_latest_partial_universal_inbox_attachment tests/test_telegram_plugin.py::test_review_memory_ok_confirms_latest_memory_write_intent`
  with 43 passed and 1 known SQLAlchemy deprecation warning.
- Post-universal-inbox-review-formatting-normalization broader verification
  passed:
  `pytest tests/test_telegram_formatting.py tests/test_telegram_attachment_ocr.py tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py tests/test_autonomous_coding_remote_control_smoke.py`
  with 215 passed and 1 known SQLAlchemy deprecation warning.
- TGR6 formatting normalization continued safely:
  `format_telegram_project_intake_reply` and
  `format_project_intake_review_status` now live in
  `plugins/telegram/formatting.py`. `plugins/telegram/project_intake.py` keeps
  compatibility wrappers for the previous public/underscored helper names,
  while deterministic Project-Intake reply and review-status wording lives with
  the shared Telegram formatters.
- Post-project-intake-formatting-normalization focused verification passed:
  `py_compile plugins/telegram/formatting.py plugins/telegram/project_intake.py tests/test_telegram_formatting.py tests/test_telegram_plugin.py`;
  `pytest tests/test_telegram_formatting.py tests/test_telegram_control_service.py tests/test_telegram_plugin.py::test_polling_cycle_project_intake_preview_for_mobile_plan tests/test_telegram_plugin.py::test_project_commands_report_and_confirm_latest_intake_review`
  with 37 passed and 1 known SQLAlchemy deprecation warning.
- Post-project-intake-formatting-normalization broader verification passed:
  `pytest tests/test_telegram_formatting.py tests/test_telegram_attachment_ocr.py tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py tests/test_autonomous_coding_remote_control_smoke.py`
  with 216 passed and 1 known SQLAlchemy deprecation warning.
- TGR6 formatting normalization continued safely:
  `format_telegram_attachment_export_reply` now lives in
  `plugins/telegram/formatting.py`. `plugins/telegram/export.py` keeps the
  previous public helper name as a compatibility wrapper while deterministic
  recent-attachment export reply wording lives with the shared Telegram
  formatters.
- Post-attachment-export-formatting-normalization focused verification passed:
  `py_compile plugins/telegram/formatting.py plugins/telegram/export.py tests/test_telegram_formatting.py tests/test_telegram_plugin.py tests/test_telegram_webhook_service.py`;
  `pytest tests/test_telegram_formatting.py tests/test_telegram_webhook_service.py::test_run_webhook_attachment_export_branch_replies_to_blocked_plan tests/test_telegram_webhook_service.py::test_run_webhook_attachment_export_branch_sends_exported_document tests/test_telegram_webhook_service.py::test_run_webhook_attachment_export_branch_reports_document_delivery_failure tests/test_telegram_plugin.py::test_polling_cycle_followup_export_request_sends_recent_attachment_pdf`
  with 22 passed and 1 known SQLAlchemy deprecation warning.
- Post-attachment-export-formatting-normalization broader verification passed:
  `pytest tests/test_telegram_formatting.py tests/test_telegram_attachment_ocr.py tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py tests/test_autonomous_coding_remote_control_smoke.py`
  with 217 passed and 1 known SQLAlchemy deprecation warning.
- TGR6 formatting normalization continued safely:
  `format_telegram_attachment_inbox_reply` and
  `telegram_attachment_ocr_note` now live in `plugins/telegram/formatting.py`.
  `plugins/telegram/attachments.py` delegates the active reply/OCR wording to
  the shared formatting module while keeping compatibility helper names.
  The pre-existing mojibake-heavy unreachable compatibility body after the
  early wrapper return in `plugins/telegram/attachments.py` was removed in the
  TGR8 cleanup pass; the active wrapper still delegates to the shared formatter.
- Post-attachment-inbox-formatting-normalization focused verification passed:
  `py_compile plugins/telegram/formatting.py plugins/telegram/attachments.py tests/test_telegram_formatting.py tests/test_telegram_attachment_ocr.py tests/test_telegram_plugin.py`;
  `pytest tests/test_telegram_formatting.py tests/test_telegram_attachment_ocr.py tests/test_telegram_plugin.py::test_document_attachment_is_redacted_and_processed_by_universal_inbox tests/test_telegram_plugin.py::test_polling_cycle_document_attachment_processes_without_prompt_or_agent_turn tests/test_telegram_plugin.py::test_polling_cycle_next_text_turn_receives_recent_attachment_context_ephemerally`
  with 29 passed and 1 known SQLAlchemy deprecation warning.
- Post-attachment-inbox-formatting-normalization broader verification passed:
  `pytest tests/test_telegram_formatting.py tests/test_telegram_attachment_ocr.py tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py tests/test_autonomous_coding_remote_control_smoke.py`
  with 219 passed and 1 known SQLAlchemy deprecation warning.
- TGR6 formatting normalization continued safely:
  `format_nextcloud_transfer_blocked_reply` now lives in
  `plugins/telegram/formatting.py`. `plugins/telegram/plugin.py` keeps the
  previous `_format_nextcloud_transfer_blocked_reply` helper as a compatibility
  wrapper while the deterministic secret-boundary wording lives with the shared
  Telegram formatters.
- Post-nextcloud-transfer-blocked-formatting-normalization focused
  verification passed:
  `py_compile plugins/telegram/formatting.py plugins/telegram/plugin.py tests/test_telegram_formatting.py tests/test_telegram_plugin.py`;
  `pytest tests/test_telegram_formatting.py tests/test_telegram_control_service.py tests/test_telegram_plugin.py::test_review_ok_confirms_latest_partial_universal_inbox_attachment tests/test_telegram_plugin.py::test_review_ok_blocks_nextcloud_live_copy_without_chat_credentials tests/test_telegram_plugin.py::test_review_ok_executes_nextcloud_copy_only_with_explicit_live_gates`
  with 42 passed and 1 known SQLAlchemy deprecation warning.
- Post-nextcloud-transfer-blocked-formatting-normalization broader
  verification passed:
  `pytest tests/test_telegram_formatting.py tests/test_telegram_attachment_ocr.py tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py tests/test_autonomous_coding_remote_control_smoke.py`
  with 220 passed and 1 known SQLAlchemy deprecation warning.
- TGR6 formatting normalization continued safely:
  `format_agent_failure_reply` now lives in `plugins/telegram/formatting.py`.
  `plugins/telegram/polling.py` delegates `_agent_failure_reply` to the shared
  formatter, preserving the injected webhook/polling failure fallback contract.
  The mojibake-heavy unreachable compatibility body after the early wrapper
  return in `plugins/telegram/polling.py` was removed in the TGR8 cleanup pass;
  the active wrapper still delegates to the shared formatter.
- Post-agent-failure-formatting-normalization focused verification passed:
  `py_compile plugins/telegram/formatting.py plugins/telegram/polling.py tests/test_telegram_formatting.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py`;
  `pytest tests/test_telegram_formatting.py tests/test_telegram_webhook_service.py::test_run_webhook_agent_turn_branch_uses_deterministic_turn_without_typing_or_handler tests/test_telegram_plugin.py::test_webhook_invokes_agent_turn_handler_and_gated_reply`
  with 24 passed and 1 known SQLAlchemy deprecation warning.
- Post-agent-failure-formatting-normalization polling verification passed:
  `pytest tests/test_telegram_plugin.py::test_polling_cycle_keeps_typing_indicator_until_agent_reply tests/test_telegram_plugin.py::test_poll_route_uses_app_state_hooks_without_event_loop_collision`
  with 2 passed and 1 known SQLAlchemy deprecation warning.
- Post-agent-failure-formatting-normalization broader verification passed:
  `pytest tests/test_telegram_formatting.py tests/test_telegram_attachment_ocr.py tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py tests/test_autonomous_coding_remote_control_smoke.py`
  with 221 passed and 1 known SQLAlchemy deprecation warning.
- TGR6 formatting normalization continued safely:
  Agent-Task control reply wording for help, missing-task, unknown command,
  status and pause/resume/cancel acknowledgements now lives in
  `plugins/telegram/formatting.py`. `plugins/telegram/control_service.py`
  delegates those deterministic replies while continuing to own ledger
  read/write orchestration and redacted public task records.
- Post-agent-task-formatting-normalization focused verification passed:
  `py_compile plugins/telegram/formatting.py plugins/telegram/control_service.py tests/test_telegram_formatting.py tests/test_telegram_control_service.py`;
  `pytest tests/test_telegram_formatting.py tests/test_telegram_control_service.py -q`
  with 41 passed and 1 known SQLAlchemy deprecation warning.
- TGR6 formatting normalization continued safely:
  Project-Intake review apply and hold reply wording now lives in
  `plugins/telegram/formatting.py`. `plugins/telegram/control_service.py`
  still owns the apply call, redacted event append and status selection, but
  delegates deterministic operator wording to shared formatting helpers.
- Post-project-intake-apply-formatting-normalization focused verification
  passed:
  `py_compile plugins/telegram/formatting.py plugins/telegram/control_service.py tests/test_telegram_formatting.py tests/test_telegram_control_service.py`;
  `pytest tests/test_telegram_formatting.py tests/test_telegram_control_service.py -q`
  with 41 passed and 1 known SQLAlchemy deprecation warning.
- TGR6 formatting normalization continued safely:
  Universal-Inbox review missing, Nextcloud transfer confirm/dry-run and
  Universal-Inbox memory review/write reply wording now lives in
  `plugins/telegram/formatting.py`. `plugins/telegram/control_service.py`
  still owns store lookups, review confirmation, transfer dry-run execution,
  memory write execution and redacted event append; the existing injected
  blocked-transfer formatter remains in use for blocked transfer states.
- Post-universal-inbox-control-formatting-normalization focused verification
  passed:
  `py_compile plugins/telegram/formatting.py plugins/telegram/control_service.py tests/test_telegram_formatting.py tests/test_telegram_control_service.py`;
  `pytest tests/test_telegram_formatting.py tests/test_telegram_control_service.py -q`
  with 41 passed and 1 known SQLAlchemy deprecation warning.
- TGR6 formatting normalization continued safely:
  Calendar unknown-command and command-error reply wording now lives in
  `plugins/telegram/formatting.py`; `plugins/telegram/control_service.py`
  still owns parsing, capability calls and redacted error payloads.
- Post-calendar-error-formatting-normalization focused verification passed:
  `py_compile plugins/telegram/formatting.py plugins/telegram/control_service.py tests/test_telegram_formatting.py tests/test_telegram_control_service.py`;
  `pytest tests/test_telegram_formatting.py tests/test_telegram_control_service.py -q`
  with 41 passed and 1 known SQLAlchemy deprecation warning.
- TGR6 formatting normalization continued safely:
  Project-Intake missing-review wording now reuses the shared review-status
  formatter, and new-chat success/pending wording now lives in
  `plugins/telegram/formatting.py`. `plugins/telegram/control_service.py`
  still owns session rebinding, project review lookup and apply/hold state.
- Post-new-chat-formatting-normalization focused verification passed:
  `py_compile plugins/telegram/formatting.py plugins/telegram/control_service.py tests/test_telegram_formatting.py tests/test_telegram_control_service.py`;
  `pytest tests/test_telegram_formatting.py tests/test_telegram_control_service.py -q`
  with 41 passed and 1 known SQLAlchemy deprecation warning.
- TGR6 formatting normalization continued safely:
  Agent-turn reply selection now lives in `plugins/telegram/formatting.py`.
  `plugins/telegram/polling.py` and `plugins/telegram/webhook_service.py`
  both use the shared helper to prefer explicit `reply_text` and fall back to
  the injected or default failure reply without changing agent execution,
  typing, store or transport behavior.
- Post-agent-turn-reply-formatting-normalization focused verification passed:
  `py_compile plugins/telegram/formatting.py plugins/telegram/polling.py plugins/telegram/webhook_service.py tests/test_telegram_formatting.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py`;
  `pytest tests/test_telegram_formatting.py tests/test_telegram_webhook_service.py::test_run_webhook_agent_turn_branch_binds_runs_types_and_replies tests/test_telegram_webhook_service.py::test_run_webhook_agent_turn_branch_uses_deterministic_turn_without_typing_or_handler tests/test_telegram_plugin.py::test_polling_cycle_keeps_typing_indicator_until_agent_reply tests/test_telegram_plugin.py::test_poll_route_uses_app_state_hooks_without_event_loop_collision -q`
  with 28 passed and 1 known SQLAlchemy deprecation warning.
- TGR8 cleanup pass completed for the known unreachable compatibility bodies:
  `plugins/telegram/attachments.py` now keeps only the active
  attachment-inbox/OCR wrappers, and `plugins/telegram/polling.py` now keeps
  only the active agent-failure wrapper. No Telegram behavior changed; shared
  formatter functions remain the single source for the moved wording.
- Post-TGR8-cleanup focused verification passed:
  `py_compile plugins/telegram/attachments.py plugins/telegram/polling.py plugins/telegram/formatting.py tests/test_telegram_formatting.py tests/test_telegram_attachment_ocr.py tests/test_telegram_plugin.py`;
  `pytest tests/test_telegram_formatting.py tests/test_telegram_attachment_ocr.py tests/test_telegram_plugin.py::test_document_attachment_is_redacted_and_processed_by_universal_inbox tests/test_telegram_plugin.py::test_polling_cycle_document_attachment_processes_without_prompt_or_agent_turn tests/test_telegram_plugin.py::test_polling_cycle_next_text_turn_receives_recent_attachment_context_ephemerally tests/test_telegram_plugin.py::test_polling_cycle_keeps_typing_indicator_until_agent_reply tests/test_telegram_plugin.py::test_poll_route_uses_app_state_hooks_without_event_loop_collision`
  with 33 passed and 1 known SQLAlchemy deprecation warning.
- Post-TGR8-cleanup broader verification passed:
  `pytest tests/test_telegram_formatting.py tests/test_telegram_attachment_ocr.py tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py tests/test_autonomous_coding_remote_control_smoke.py`
  with 221 passed and 1 known SQLAlchemy deprecation warning.
- TGR9 integration review completed safely:
  `docs/plans/telegram-plugin-refactor-integration-review.md` maps TGR1-TGR8
  artifacts across route modules, status gates, webhook service, control
  service, shared formatting, polling integration and compatibility wrappers,
  records redaction/no-live guarantees and keeps live send, voice download/STT
  and behavior-change gates deferred.
- Post-TGR9 integration verification passed:
  `py_compile plugins/telegram/formatting.py plugins/telegram/control_service.py plugins/telegram/polling.py plugins/telegram/webhook_service.py plugins/telegram/plugin.py tests/test_telegram_formatting.py tests/test_telegram_control_service.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py`;
  `pytest tests/test_telegram_formatting.py tests/test_telegram_attachment_ocr.py tests/test_telegram_control_service.py tests/test_telegram_route_contract.py tests/test_telegram_status.py tests/test_telegram_webhook_service.py tests/test_telegram_plugin.py tests/test_telegram_text_boundary.py tests/test_telegram_voice_pipeline.py tests/test_telegram_voice_boundary.py tests/test_telegram_image_actions.py tests/test_telegram_screenshot_delivery.py tests/test_telegram_task_orchestrator.py tests/test_telegram_truth_runtime.py tests/test_autonomous_coding_remote_control_smoke.py -q`
  with 223 passed and 1 known SQLAlchemy deprecation warning.

## Paths

Alice path:
- inventory commands, routes, env gates and operator wording
- write migration checklist

Bob path:
- write characterization tests
- split modules behind the same route/tool surface
- keep redaction and default-off behavior

Charlie path:
- sequence small refactors
- run focused Telegram suite after each group
- stop on behavior drift

## Verification

- `pytest tests/test_telegram_plugin.py`
- `pytest tests/test_telegram_text_boundary.py`
- `pytest tests/test_telegram_voice_pipeline.py`
- `pytest tests/test_telegram_voice_boundary.py`
- `pytest tests/test_telegram_image_actions.py`
- `pytest tests/test_telegram_screenshot_delivery.py`
- `pytest tests/test_telegram_task_orchestrator.py`
- `pytest tests/test_telegram_truth_runtime.py`
- `git diff --check`

## Go Language

- Go: `plugin.py` is a thin setup layer, behavior is characterized, existing
  tests pass and all live features remain gated.
- Partial: some concerns are split but large command sections remain.
- Deferred: live send/voice/file smokes wait for operator Go.
- No-Go: refactor changes behavior without tests or leaks Telegram secrets.
