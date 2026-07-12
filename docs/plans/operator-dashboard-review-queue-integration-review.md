# Operator Dashboard Review Queue Integration Review

Date: 2026-07-06

Status: ODR1/ODR2/ODR3/ODR4 integration review under Standard ABC

## Scope

This review covers the repo-only Operator Dashboard and Review Queue artifacts
from ODR1 through ODR4. It verifies that the backend contract, snapshot model,
review queue model and admin-gated route are integrated without enabling UI
placement, live action buttons, live provider calls, Telegram sends, Nextcloud
writes, Memory/RaptorGraph writes, code publication or security remediation.

Out of scope:

- Rendering the dashboard in any UI surface.
- Executing approve, retry, dismiss or live-action controls.
- Live Telegram delivery, Nextcloud copy/write, Memory/RaptorGraph write,
  security remediation, code publication or deployment.
- Persisting raw task prompts, private review text, paths, URLs, commands,
  tokens, chat IDs or private source references in docs/tests/evidence.

## Integration Map

| Area | Artifact | Integration evidence |
| --- | --- | --- |
| Contract | `docs/plans/operator-dashboard-review-queue-contract.md` | Defines route shape, section order, review item semantics, default sources, redaction invariants and gates. |
| Snapshot model | `src/operator_dashboard_snapshot.py` | Snapshot tests verify stable sections, redaction flags, read-only next actions, gated controls and private-value suppression. |
| Review queue model | `src/operator_review_queue.py` | Queue tests verify Nextcloud, Memory, RaptorGraph, file export, security, Telegram and coding approval families map to redacted read-only items. |
| Route | `routes/operator_dashboard_routes.py` | Route tests verify admin gating, injected provider redaction and default local source collection. |
| App wiring | `app.py` | The route is registered next to existing review-gate and readiness routes with the existing MCP manager passed for diagnostics. |

## Redaction And Safety Guarantees

The repo-only integration keeps these safety flags invariant:

- dashboard route, snapshot and queue report no live probes, live mutations or
  write actions.
- approve/execute controls are disabled or policy-gated.
- review queue items expose source reference hashes, not source references.
- task prompts, private task names, Telegram chat IDs, message IDs, private
  paths, URLs, commands, tokens, secrets and branch/source refs are suppressed
  from route payloads and evidence.
- default local providers read existing local state only and do not call
  providers, external APIs or deployment targets.

## Verification

Focused compile:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m py_compile src\operator_dashboard_snapshot.py src\operator_review_queue.py routes\operator_dashboard_routes.py tests\test_operator_dashboard_snapshot.py tests\test_operator_review_queue.py tests\test_operator_dashboard_routes.py app.py
```

Focused model and route suite:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_operator_dashboard_snapshot.py tests\test_operator_review_queue.py tests\test_operator_dashboard_routes.py -q
```

Result for this review: focused compile passed; the focused suite passed with
9 tests and only the known SQLAlchemy `declarative_base()` deprecation warning.

## Deferred Gates

Gate: `ODR-UI-PLACEMENT`

State after this review: deferred

Required before UI work: explicit placement decision and UI surface ownership.

Gate: `ODR-LIVE-ACTION-BUTTONS`

State after this review: deferred

Required before execution controls: explicit bounded live Go for each action
class and redacted evidence capture for the attempted action.

## Conclusion

Roadmap 2 has a backend-ready operator dashboard and review queue surface:
contract, models, admin-gated route, app wiring and focused redaction tests are
present. UI placement and live execution remain behind explicit gates.
