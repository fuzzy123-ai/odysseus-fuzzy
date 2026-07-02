# Model Cooperation Routing Contract

Status: active contract for backend planning. This is not a runtime subagent
launch plan.

## Goal

Route work by task class so local models handle bounded maintenance and stronger
API/planner models handle planning only when privacy gates allow it.

## Roles

| Role | Model class | Intended work | Hard limits |
| --- | --- | --- | --- |
| Local maintenance | small/local model such as Gemma4 E4B | Inbox triage, labels, sensitivity precheck, summaries, dedupe candidates, RaptorGraph candidate preparation | No autonomous truth writes, no raw-content persistence, bounded packets only, concurrency 1 |
| Strong planner | strong API or local planner model | Architecture, roadmap shaping, complex implementation plans, decomposition | Must not receive sensitive/raw content when DSGVO/local-only gates are active |
| Verifier/reviewer | security/review-capable model or deterministic verifier | Security review, regression review, final sanity checks, schema and policy validation | Reviewer output is evidence, not permission to bypass gates |

## Routing Rules

- Trusted runtime metadata decides mandatory maintenance routing: channel,
  message kind, recent attachment state, Universal Inbox status, sensitivity
  classification, and DSGVO/security mode.
- Untrusted document text must not select privileged workflow skills or required
  subagents.
- Local maintenance output is treated as a candidate or review packet until the
  backend validates schema, provenance, confidence, and write gates.
- API escalation requires `local_only_required=false`, `api_escalation_allowed=true`,
  and an explicit non-sensitive workload reason.

## Non-Goals

- No runtime agent spawning in this contract.
- No UI placement decision.
- No live provider calls.
- No direct memory or graph truth writes from model output.
