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
