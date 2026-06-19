# Feature Completion Board

Stand: 2026-06-19

Status: **operator board for P0-P2 closure**

| Track | Status | Evidence | Missing | Next action |
| --- | --- | --- | --- | --- |
| P0 Status / Repo Hygiene | Go for closure | Focused test groups are green: GameDev/Mount `33 passed, 1 skipped`; MCP `18 passed`; Updater/Backup Gate `23 passed`; Telegram `48 passed`; Nextcloud/private-source `30 passed`; Obsidian `178 passed`; System Health `138 passed`. Charlie also fixed homeserver ops executable bits and the MCP activation-state script. | Charlie integration and commit; continued check for foreign staged files or hotfile conflicts. | Review and integrate only P0-P2 docs plus the narrow ops-script fixes. |
| P0 Homeserver Backup Gate | Go | Live homeserver gate succeeded at commit `4eec20b`; `pre_update_snapshot`, `repository_check`, and `restore_smoke` passed; restore smoke targeted a temporary restore location; no deploy was run. | Deploy-live permission remains separate; no Restic or host detail output belongs in docs. | Keep Backup Gate Go as evidence-backed and keep deploy as its own Go gate. |
| P1 External 1.0 Evidence | No-Go | Offline validators and evidence models exist; release wording already separates internal readiness from external release. | Redacted real Provider/Fallback Answer Run evidence; redacted real Test-Vault Export/Import/Rebuild evidence; operator release decision. | Run both evidence gates only after separate live Go and record redacted evidence only. |
| P2 MCP Runtime Smoke | Blocked at deploy-live gate | Offline MCP route/policy tests are green; production activation docs exist; homeserver commit is `4eec20b`; plugin state can be enabled. | The running Odysseus container does not include `/app/plugins/mcp_server`; a targeted container rebuild/recreate is required before route smoke can pass. | Get explicit deploy-live approval for an Odysseus container rebuild/recreate, then run local MCP smoke; do not expose remotely. |
| P2 Telegram Text Runtime Smoke | Partial | Telegram focused tests are green and redaction boundaries are represented. | Live text roundtrip smoke with redacted evidence; no raw chat IDs or tokens. | Run live text smoke only after separate Go and record minimal redacted evidence. |
| P2 GameDev Mount Runtime Smoke | Go for runtime validation and read smoke | GameDev/Mount focused tests are green; `/mnt/canyon-racer` runtime config validates with no host path visible; read-only virtual mount smoke listed entries without exposing host paths. | Optional write smoke remains separate because it mutates the project mount. | Treat read access as closed; request explicit operator approval before any write smoke. |
| Provider Gate | Separate Go gate | Provider/Fallback Answer Run is defined as a required external evidence gate. | Real redacted provider/fallback answer evidence. | Keep blocked until explicit live Go and redacted evidence. |
| Nextcloud Gate | Separate Go gate | Nextcloud/private-source focused tests are green. | Any live Nextcloud write or mutation evidence. | Keep live writes separate from P0-P2 closure. |
| Export/Rebuild Gate | Separate Go gate | Test-Vault Export/Import/Rebuild is modeled as required evidence. | Redacted real export/import/rebuild evidence. | Run only after explicit live Go; do not write private vault contents into docs. |
| Host / Deploy-Live Gate | Separate Go gate | Backup Gate Go exists and did not include deploy. | Any host mutation or deployment permission/evidence. | Treat deploy-live as a future operator-approved gate. |
| P3 Planning | Planning only | P3 is named as a separate track. | Execution scope, owners, tests, and live gates are not authorized here. | Plan separately; do not execute in this worker slice. |

## Board Language

- **Go** means the named gate has evidence-backed, redacted, operator-approved
  closure.
- **Partial** means repo artifacts or focused tests are ready but runtime/live
  evidence is still missing.
- **No-Go** means required evidence is missing or release language would overstate
  readiness.
- **Separate Go gate** means the track may not be collapsed into another gate or
  inferred from adjacent evidence.
