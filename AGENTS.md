# Odysseus Agent Safety

These instructions apply to every repository path and every agent-operated
production diagnostic.

## Secret-safe runtime diagnostics

- Never emit a complete environment, secret store, credential file, process
  environment, or service environment into terminal or tool output.
- Forbidden diagnostic sources include `env`, `printenv`, `.env` contents,
  `podman inspect … .Config.Env`, `docker inspect … .Config.Env`,
  `systemctl show Environment`, unredacted `compose config`, and equivalent
  commands that serialize values.
- For Debian credential readiness, use only
  `ssh -F ops/homeserver/ssh_config odysseus-homeserver-probe`. This alias has a
  fixed `ops/homeserver/redacted_runtime_probe.py` remote command and rejects
  caller-supplied command overrides. Its JSON projection is the only agent-safe
  environment readback.
- Keep `odysseus-homeserver` for explicitly live-gated administration and
  deployments. Do not use that unrestricted alias for environment diagnostics.
- No credential value, prefix, suffix, length, or hash may be printed. Report
  only fixed-key boolean presence and bounded aggregate counts.
- Do not forward raw subprocess stdout, stderr, exception text, journals, or
  provider responses before a repository-owned redaction boundary validates
  and reserializes them.
- If a diagnostic cannot be answered through an allowlisted redacted schema,
  stop and request a narrower evidence contract. Do not fall back to raw output.
- Credential rotation, SSH-key changes, secret migration, and authentication
  configuration require a separate action-specific live GO, rollback plan, and
  post-change access readback.

## Safe start and ownership

- Treat versioned Planning JSON, its gate queue, durable claims, and
  clarification records as the mutable execution authorities. Do not create
  `STATE.md`, `OWNER_QUEUE.md`, or another maintenance state store.
- Before editing, inspect the applicable instructions, current structured
  state, `git status`, and the path-scoped diff. Old chat, prose, and prior
  receipts are context, not current authority.
- Work only inside one dependency-ready, path-scoped claim. Preserve all
  unrelated working-tree and staged changes. Overlapping writers must stop
  until a durable handoff releases the exact paths.
- If intent, ownership, acceptance, or an owner decision is missing, record or
  surface the blocker as `waiting_on_user`. Never invent the answer.

## Repository and external effects

- Do not run `git reset --hard`, `git clean`, checkout-based rewrites,
  history rewrites, force pushes, broad cleanup, or automatic conflict
  resolution.
- Stage only reviewed paths owned by the active claim. A commit, push, deploy,
  provider write, network mutation, host change, send, backup, restore, or
  other live action requires authority for that exact action; none is implied
  by implementation or test authority.
- Prefer read-only inspection and reversible repository edits. Stop when safe
  progress would require deleting, overwriting, or absorbing foreign work.

## Verification and handoff

- Run the smallest focused checks first, then every stronger lane required by
  the slice. A passing weak lane never implies that integration, UI, live, or
  temporal verification passed.
- Completion requires current machine evidence bound to the tested revision or
  dirty-diff digest. Policy text, checkboxes, commit messages, and agent prose
  cannot manufacture a PASS.
- Report checks that actually ran, their results, and a separate `Not
  verified` list. Do not omit unavailable or intentionally gated evidence.
- A durable handoff names the roadmap, slice, owner, claim, changed paths,
  tests and results, evidence reference, next action, blockers or owner
  questions, and remaining verification limits.
