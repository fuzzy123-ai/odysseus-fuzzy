# Plugin System Hardening Operator Runbook

Status: PLG6 docs-only runbook

Scope: local operator decisions for plugin schema, capability, lifecycle,
readiness and release gates. This runbook does not approve remote registry
fetches, plugin installs, plugin enablement, network access or live actions.

## Decision States

- `discovered`: plugin exists locally or in a local registry index.
- `audited`: manifest, capability boundary and local audit were checked.
- `loadable`: local policy allows runtime loading under current compatibility
  rules.
- `loaded`: plugin is active in the runtime without new live permissions.
- `degraded`: plugin is usable with warnings or reduced capability.
- `disabled`: operator or policy keeps plugin unavailable.
- `quarantined`: plugin is blocked by audit, release gate or safety failure.
- `uninstallable`: plugin can be removed by a local operator flow after
  dependency checks.

## Permission Decisions

- `read_only`: allow read-only app or plugin-owned state.
- `owner_scoped_write`: allow writes only to plugin-owned or delegated
  owner-scoped records.
- `admin`: allow plugin administration and local operator controls.
- `host_adjacent`: allow declared host-health or filesystem-adjacent snapshots
  without broad host control.
- `networked`: allow declared network clients without live mutation.
- `live_action`: allow external write/send/install/deploy/provider actions
  only after a separate bounded live Go.

Legacy `user` and `admin` manifests remain compatible during the PLG roadmap.
Explicit new tiers must cover declared capabilities.

## Local Review Flow

1. Validate manifest policy.
2. Validate capability boundary.
3. Run local plugin audit without importing plugin code.
4. Build plugin lifecycle readiness.
5. Evaluate plugin release gate.
6. If any plugin is `quarantined`, do not load or update it.
7. If any plugin is `degraded`, decide whether reduced capability is acceptable.
8. If all gates are ready, plugin readiness is Go for local non-live use.

## Go Language

- Go: manifest policy, capability boundary, local audit, lifecycle readiness
  and release gate pass without live action.
- Partial: plugin is degraded but operator accepts reduced capability.
- Deferred: remote registry, install, update or live action needs separate Go.
- Blocked: quarantined plugin, unsafe capability, invalid manifest or failed
  release gate.
- No-Go: untrusted remote code, raw secrets, token exposure, private content
  exposure or unapproved live mutation would occur.

## Stop Rules

- Stop before remote registry fetch or plugin install unless
  `PLG-REMOTE-REGISTRY-GO` is explicitly granted.
- Stop before breaking existing manifest compatibility unless
  `PLG-BREAKING-SCHEMA-GO` is decided.
- Stop if plugin import, setup, network calls, host commands, Telegram sends,
  Nextcloud writes or provider calls would be needed for evidence.
- Stop if evidence would include raw private content, tokens, chat IDs,
  private host paths or provider output.
