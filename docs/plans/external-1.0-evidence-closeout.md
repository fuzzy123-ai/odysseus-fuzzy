# External 1.0 Evidence Closeout

Stand: 2026-06-19

Status: **No-Go for external 1.0; internal RC remains ready**

## Goal

Close the current P0 release-safety question without pretending that offline validators are live manual evidence.

## Current Evidence

- `docs/plans/1.0-release-handoff.md` records an accepted Partial handoff.
- `docs/plans/1.0-evidence-release-checklist.md` records automated gates as green with `235 passed, 44 warnings`.
- Fresh install, upgrade path, and known limits are documented.
- `src/provider_fallback_answer_run.py` models the Provider/Fallback Answer Run as read-only evidence.
- `src/test_vault_export_import_rebuild.py` models the Test-Vault Export/Import/Rebuild gate as read-only evidence.
- `src/live_release_evidence_closeout.py` keeps both external release blockers at `needs_manual_evidence` by default.
- Focused local verification on 2026-06-19:
  - `tests/test_provider_fallback_answer_run.py`
  - `tests/test_test_vault_export_import_rebuild.py`
  - `tests/test_release_decision_bundle.py`
  - result: `23 passed, 1 warning`
- Additional closeout verification on 2026-06-19:
  - `tests/test_live_release_evidence_closeout.py`
  - `tests/test_live_integration_readiness_index.py`
  - `tests/test_manual_release_evidence.py`
  - `tests/test_manual_release_evidence_readiness_summary.py`
  - result: `21 passed, 1 warning`

## Decision

External `1.0.0` remains **No-Go**.

The reason is narrow and explicit:

- Provider/Fallback Answer Run has not been recorded as real redacted manual evidence.
- Test-Vault Export/Import/Rebuild has not been recorded as real redacted manual evidence.
- Offline validators are green, but validators are not live evidence.

This is not a feature regression. It is a release-language boundary.

## Allowed Next Slices

Allowed:

- Update operator runbooks.
- Improve read-only evidence summaries.
- Add UI/dashboard wording that clearly says external release is blocked.
- Prepare a manual Provider Proof runbook or local readiness packet.
- Prepare a manual Test-Vault Export/Import/Rebuild runbook or local readiness packet.

Not allowed without a separate live Go:

- real provider call;
- network-backed fallback answer run;
- export/import/rebuild execution;
- writes to real user vaults;
- raw provider output capture;
- token, credential, or private path logging.

## Go / Partial / No-Go

Go:
- Provider/Fallback Answer Run is recorded with redacted manual evidence.
- Test-Vault Export/Import/Rebuild is recorded with redacted manual evidence.
- Operator decision explicitly permits external release language.

Partial:
- Current internal RC state. Automated gates and offline validators are green, but external evidence is incomplete.

No-Go:
- Current external release state.
- Any missing manual evidence keeps public `1.0.0` blocked.
- Any secret/private output leak blocks release.

Deferred:
- Postgres migration, live Nextcloud writes, live Telegram voice, fully automatic updater execution, accelerators, and research tracks.

## Handoff Card

Path: `ABC2-external-1-0-evidence-closeout`

Status: Partial / external No-Go

Goal:
- Preserve internal RC readiness while blocking external release language until real manual evidence lands.

Changed files:
- `docs/plans/external-1.0-evidence-closeout.md`

Tests:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_provider_fallback_answer_run.py tests\test_test_vault_export_import_rebuild.py tests\test_release_decision_bundle.py` -> `23 passed, 1 warning`
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_live_release_evidence_closeout.py tests\test_live_integration_readiness_index.py tests\test_manual_release_evidence.py tests\test_manual_release_evidence_readiness_summary.py` -> `21 passed, 1 warning`

Risks:
- A future dashboard or release note could accidentally compress "internal RC ready" into "external release ready"; wording must keep the distinction.
- A future manual run must avoid raw provider output, tokens, private paths, or production-vault writes.

Next path:
- `ABC3-release-hardening-critique`, then `ABC4-updater-live-boundary-contract`.
