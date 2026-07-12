# Universal Inbox Nextcloud Flow Integration Review

Date: 2026-07-06

Status: UIX2/UIX3/UIX4/UIX5 integration review under Standard ABC

## Scope

This review covers the repo-only Universal Inbox and Nextcloud Flow artifacts
from UIX2 through UIX5. It verifies that the canonical flow state, Nextcloud
dry-run adapter, shared review reason vocabulary and browser-safe flow-state
route are integrated without enabling live Nextcloud writes, live file
extraction, Memory writes, RaptorGraph writes or private-content persistence.

Out of scope:

- Live WebDAV copy, move, delete or rewrite actions.
- Live extraction of real private documents.
- Durable Memory/RaptorGraph writes from Universal Inbox documents.
- Safe-area policy design decisions that require operator/product judgment.
- Any persistence of raw filenames, private paths, WebDAV URLs, chat IDs,
  secrets, raw content or provider output in docs/tests/evidence.

## Integration Map

| Area | Artifact | Integration evidence |
| --- | --- | --- |
| Canonical flow state | `src/universal_inbox_flow_state.py` | Flow-state tests verify the received -> classified -> extracted -> abstracted -> reviewed -> routed -> copied/exported -> memory-intent -> graph-provenance steps, redacted source hashes and `side_effects=("none",)`. |
| Nextcloud adapter | `src/nextcloud_universal_inbox_flow_adapter.py` | Adapter tests verify dry-run import reports, transfer readiness, live-readiness summaries and optional transfer results map into the canonical flow without scanning Nextcloud, calling WebDAV or writing Memory/RaptorGraph data. |
| Review reason vocabulary | `src/universal_inbox_review_reasons.py` | Review-reason tests verify legacy aliases normalize to canonical codes with category, severity and flow-stage classification. |
| Browser-safe route | `routes/universal_inbox_routes.py` | Status-route tests verify `/api/universal-inbox/items/{source_ref}/flow-state` reuses upload backend, auth, owner/admin and path-like source-ref checks, then returns a redacted metadata-only payload. |
| Existing UIX/Nextcloud surfaces | Universal Inbox pipeline, policy, routing, placement, import report, transfer readiness, live-readiness, transfer executor, local extraction review, WebDAV client and memory intent modules | Broad repo-only UIX/Nextcloud suite verifies the new contracts remain compatible with existing dry-run and readiness behavior. |

## Redaction And Safety Guarantees

The repo-only integration keeps these safety flags invariant:

- `source_ref_visible`, `source_path_visible`, `raw_content_visible`,
  `secret_values_visible` and `chat_id_visible` remain false in flow-state
  payloads.
- `live_write_allowed` defaults to false for both the canonical flow state and
  the Nextcloud adapter.
- route-level flow state is metadata-only and derives from the existing
  redacted upload status contract.
- path-like source references are rejected before upload lookup.
- cross-owner access returns not-found semantics without exposing existence.
- Nextcloud dry-run payloads do not leak WebDAV endpoints, private mirror
  paths, dry-run commands or sample review paths.
- runtime events for the flow-state contract report no side effects.

## Verification

Focused compile:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m py_compile src\universal_inbox_flow_state.py src\nextcloud_universal_inbox_flow_adapter.py src\universal_inbox_review_reasons.py routes\universal_inbox_routes.py tests\test_universal_inbox_flow_state.py tests\test_nextcloud_universal_inbox_flow_adapter.py tests\test_universal_inbox_review_reasons.py tests\test_universal_inbox_status_routes.py
```

Broad repo-only UIX/Nextcloud suite:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_universal_inbox_status_routes.py tests\test_universal_inbox_review_reasons.py tests\test_universal_inbox_flow_state.py tests\test_nextcloud_universal_inbox_flow_adapter.py tests\test_universal_inbox_pipeline.py tests\test_universal_inbox_policy.py tests\test_nextcloud_import_report.py tests\test_nextcloud_transfer_readiness.py tests\test_live_nextcloud_readiness_check.py tests\test_universal_inbox_nextcloud_transfer.py tests\test_nextcloud_local_extraction_review.py tests\test_nextcloud_webdav_client.py tests\test_universal_inbox_memory_write_intent.py tests\test_universal_inbox_routing.py tests\test_universal_inbox_placement.py -q
```

Result for this review: focused compile passed; the broad suite passed with
96 tests and only the known SQLAlchemy `declarative_base()` deprecation
warning.

## Deferred Gates

Gate: `UIX-SAFE-AREA-RULES`

State after this review: deferred

Required before automatic policy decisions: explicit safe-area rules, review
defaults, local-only subset boundaries and operator acceptance for repeated
decisions.

Gate: `UIX-NEXTCLOUD-LIVE-WRITE`

State after this review: deferred

Required before live action: explicit bounded operator Go, source provider,
source subset, target path, copy-only proof, no-delete rollback/no-op plan and
redacted evidence capture.

Gate: `UIX-MEMORY-WRITE-GO`

State after this review: deferred

Required before durable memory action: explicit bounded operator Go, exact
subset, abstraction-only retention policy, model route, write budget and
redacted evidence capture.

## Conclusion

UIX2 canonical state, UIX3 Nextcloud adapter, UIX4 review reason normalization
and UIX5 route contract are integrated as repo-only work. The operator can see
one redacted metadata-only flow surface for upload and dry-run Nextcloud states;
safe-area policy, live copy/write validation and durable memory writes remain
behind explicit gates.
