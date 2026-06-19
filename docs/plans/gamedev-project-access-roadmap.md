# Mount-Backed GameDev Project Access Roadmap

Stand: 2026-06-19

Status: **active; product readiness Partial until profile, command gate, and
smoke evidence are complete**

## Goal

Expose `E:\Canyoning` as the virtual project mount `/mnt/canyon-racer` for
Godot/GameDev work without granting broad host access or pretending shell access
is sandboxed.

## Current Evidence

- Owner-scoped mounts, sensitive path blocking, write policy, and write audit
  exist in `core/mount_manager.py` and `core/path_resolver.py`.
- File tools can be bound to a per-turn workspace in `src/tool_execution.py`.
- `get_workspace` explicitly states that file tools are confined to the
  workspace, while shell starts there but is not sandboxed.
- Default write extensions in code include Godot files such as `.gd`, `.tscn`,
  `.tres`, `.godot`, and `.gdshader`.
- The local runtime mount data may still need its stored allowed extensions
  updated by an operator because `data/mounts.json` is runtime state, not a
  tracked roadmap artifact.

## Non-Goals

- No mount of all `E:\`, a user home, a drive root, `/`, or system folders.
- No free Bash, PowerShell, Python, or command prompt as a project command gate.
- No destructive file operations, project builds, exports, or engine commands
  without a separate command intent and operator Go.
- No secrets, `.env`, raw provider output, credentials, or private chat
  identifiers in docs, tests, prompts, or audit artifacts.

## Stop Rules

- Stop if the host project folder is unclear or broader than the named project
  parent.
- Stop if a stored mount enables shell-like tools as the access boundary.
- Stop if a write would target sensitive paths, symlinks, reparse points,
  dependency folders, VCS internals, or disallowed extensions.
- Stop if test fixtures need real `E:\Canyoning` access.

## Slices

### GDEV0 Inventory And Roadmap

Owner: Charlie.

Done when:
- This roadmap exists.
- The unified feature roadmap links the GameDev track.
- Current dirty files are named and avoided.

### GDEV1 GameDev Mount Profile

Owner: Bob.

Goal:
- Add a reusable offline profile for Godot project mounts.

Done when:
- The profile includes Godot text/resource extensions.
- The profile rejects broad host roots and shell-like mount tools.
- Tests run without touching a real mounted project.

### GDEV2 Project Mode Binding

Owner: Bob, Charlie integration.

Goal:
- Treat `/mnt/canyon-racer` as a named project context rather than a raw host
  path.

Done when:
- UI/API wording references the virtual mount name.
- Workspace binding never implies shell sandboxing.
- File tools remain owner-scoped.

### GDEV3 Command Gate

Owner: Bob.

Goal:
- Model allowed project command intents separately from shell.

Done when:
- Command intents are named, bounded, and auditable.
- Free-form shell is not considered a valid GameDev command gate.
- Destructive/build/export intents require a higher gate than read-only scans.

### GDEV4 Audit And Undo

Owner: Bob, Alice docs.

Goal:
- Make write audit and undo expectations explicit.

Done when:
- Mount write policy requires backups for writable project mounts.
- Restore/undo instructions are documented.
- Large binary writes remain out of scope unless explicitly allowed.

### GDEV5 Operator Runbook

Owner: Alice.

Goal:
- Document how to enable, validate, use, and disable a GameDev mount safely.

Done when:
- The runbook explains allowed extensions, broad-root rejection, shell caveat,
  and smoke-test steps.

### GDEV6 Final Gate

Owner: Charlie.

Done when:
- Focused tests pass.
- One dry-run validation against the stored mount configuration is documented
  without leaking private host contents.
- Any real write smoke is explicitly operator-approved.

## Go / Partial / No-Go

- **Go**: profile tests pass, stored mount configuration validates, shell is not
  treated as sandboxed, and a smoke test proves read/write within the virtual
  mount only.
- **Partial**: profile and docs exist, but local runtime mount configuration or
  smoke evidence is pending.
- **No-Go**: broad host root, shell-as-sandbox, secret leakage, or destructive
  project commands are required.
