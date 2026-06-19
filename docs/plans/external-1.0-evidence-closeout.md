# External 1.0 Evidence Closeout

Stand: 2026-06-19

Status: **Evidence-Go for external 1.0 review; deploy/tag/distribution remain separate**

## Goal

Close the current P0 release-safety question with redacted live manual evidence, without implying deploy, tag, or distribution execution.

## Current Evidence

- `docs/plans/1.0-release-handoff.md` records accepted Go evidence.
- `docs/plans/1.0-evidence-release-checklist.md` records automated gates as green with `235 passed, 44 warnings`.
- Fresh install, upgrade path, and known limits are documented.
- `src/provider_fallback_answer_run.py` models the Provider/Fallback Answer Run as read-only evidence.
- `src/test_vault_export_import_rebuild.py` models the Test-Vault Export/Import/Rebuild gate as read-only evidence.
- `src/live_release_evidence_closeout.py` records Provider Proof and Test-Vault Export/Import/Rebuild as `go`.
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
- P1 isolated evidence on 2026-06-19 closes Test-Vault Export/Import/Rebuild:
  synthetic gitignored workspace data vault, 2 exported files, 2 imported files,
  rebuild proof configured, query layer ready, 1 citation, no production vault,
  source write, data loss, secrets, or raw provider output recorded.
- P1 isolated provider evidence on 2026-06-19 closes Provider/Fallback Answer Run:
  synthetic gitignored workspace data vault, ready query index, 2 citations,
  `answer_mode=cloud`, provider/model/endpoint signal recorded, empty fallback
  reason, and no raw answer, headers, secrets, tokens, or private content recorded.
- P0-P2 operator closure keeps deploy, tag, distribution, and unrelated live
  integrations as separate Go gates.

## Decision

External `1.0.0` evidence is **Go for final review**.

The reason is narrow and explicit:

- Provider/Fallback Answer Run is recorded as a successful real redacted manual evidence run.
- Test-Vault Export/Import/Rebuild is recorded as isolated redacted evidence.
- Offline validators remain green and are no longer the only release evidence.

This is still a release-language boundary: Evidence-Go does not execute deploy,
tagging, packaging, distribution, or unrelated live integrations.

## Allowed Next Slices

Allowed:

- Update operator runbooks.
- Improve read-only evidence summaries.
- Add UI/dashboard wording that clearly separates Evidence-Go from deploy/tag/distribution.
- Prepare final external review notes.
- Preserve the Provider and Test-Vault evidence as closed unless a regression or leak signal appears.

Not allowed without a separate live Go:

- additional real provider call;
- network-backed fallback answer run;
- export/import/rebuild execution;
- writes to real user vaults;
- raw provider output capture;
- token, credential, or private path logging.

Separate Go gates remain required for Telegram, Nextcloud, host mutation,
deploy-live, tag, distribution, and any future Export/Rebuild rerun. Evidence
from one gate must not be used to imply Go for another.

## Go / Partial / No-Go

Go:
- Provider/Fallback Answer Run is recorded with redacted manual evidence.
- Test-Vault Export/Import/Rebuild remains recorded with redacted isolated evidence.
- Operator decision explicitly permits final external review language.

Partial:
- Future state only if any required evidence regresses or is superseded.

No-Go:
- Any missing Provider Proof or Test-Vault evidence blocks public `1.0.0`.
- Any secret/private output leak blocks release.

Deferred:
- Postgres migration, live Nextcloud writes, live Telegram voice, fully automatic updater execution, accelerators, and research tracks.

## Handoff Card

Path: `ABC2-external-1-0-evidence-closeout`

Status: Go evidence / final external review

Goal:
- Preserve Evidence-Go while keeping deploy, tag, distribution, and unrelated live gates separate.

Changed files:
- `docs/plans/external-1.0-evidence-closeout.md`

Tests:
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_provider_fallback_answer_run.py tests\test_test_vault_export_import_rebuild.py tests\test_release_decision_bundle.py` -> `23 passed, 1 warning`
- `C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_live_release_evidence_closeout.py tests\test_live_integration_readiness_index.py tests\test_manual_release_evidence.py tests\test_manual_release_evidence_readiness_summary.py` -> `21 passed, 1 warning`

Risks:
- A future dashboard or release note could accidentally compress "Evidence-Go" into "deploy/tag/distribution done"; wording must keep the distinction.
- A future manual run must avoid raw provider output, tokens, private paths, or production-vault writes.

Next path:
- `ABC3-release-hardening-critique`, then `ABC4-updater-live-boundary-contract`.
