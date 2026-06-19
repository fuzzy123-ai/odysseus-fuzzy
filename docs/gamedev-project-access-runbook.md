# GameDev Project Access Runbook

Status: operator runbook for mount-backed Godot/GameDev project access.

## Purpose

Use a narrow virtual mount such as `/mnt/canyon-racer` to let Odysseus inspect
and edit an explicitly approved project folder without exposing a whole drive or
pretending shell access is sandboxed.

## Safe Shape

- Host path: a named project folder or project parent, for example
  `E:\Canyoning`.
- Virtual path: `/mnt/canyon-racer`.
- Owner: a named Odysseus user such as `fuzzy`, not a writable global mount.
- Allowed file tools: `read_file`, `write_file`, `edit_file`, `ls`, `grep`,
  `glob`.
- Write policy: enabled, backup enabled, bounded payload size, and Godot text
  extensions such as `.gd`, `.tscn`, `.tres`, `.godot`, `.gdshader`, `.import`,
  `.uid`, `.md`, `.json`, `.yaml`, and `.txt`.

## Must Not Do

- Do not mount all of `E:\`, a home directory, `/`, or system folders.
- Do not allow shell-like tools such as Bash, PowerShell, CMD, Python, or SSH as
  mount tools.
- Do not treat shell cwd as a sandbox. Shell may start in a workspace, but it is
  not the security boundary for GameDev project access.
- Do not store secrets, `.env` contents, provider output, chat identifiers, or
  private credentials in docs, tests, logs, or mount audit notes.
- Do not run builds, exports, destructive cleanup, or engine commands unless a
  named command intent and operator Go exist.

## Enablement Checklist

1. Confirm the host path is the intended project folder or narrow parent.
2. Confirm the virtual mount path is `/mnt/canyon-racer`.
3. Confirm the mount owner is explicit and the mount is not writable for `*`.
4. Confirm write backup is enabled.
5. Confirm Godot extensions are included in the stored write policy.
6. Confirm no shell-like tool appears in `allowed_tools`.
7. Run the offline profile tests before treating the profile as implementation
   ready.

## Dry-Run Validation

Use offline validation first. This does not touch the real project:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_gamedev_project_profile.py tests\test_mount_points.py
```

Expected result:
- GameDev profile accepts the narrow project path.
- Broad host roots are rejected.
- Shell-like mount tools are rejected.
- Godot extensions are required.
- Command plans are represented as named argv plans, not free shell strings.

## Smoke Plan

Only after the profile validates and the operator approves a real smoke:

1. Read a harmless project file through `/mnt/canyon-racer`.
2. Write a small temporary text file under an approved non-secret project test
   path.
3. Confirm the response shows the virtual path, not the host path.
4. Confirm mount audit records the write without secret values.
5. Remove or revert the temporary file through an explicit safe cleanup plan.

If cleanup would require broad deletion or uncertain paths, stop and leave the
temporary file for manual review.

## Command Intent Policy

Project commands must use named intents such as `inspect_project`,
`godot_lint`, `godot_test`, or `godot_export`.

- `inspect_project`: read-only and safe by default.
- `godot_lint`: allowed only as configured argv data.
- `godot_test`: allowed only as configured argv data.
- `godot_export`: requires operator Go.

Free-form shell strings such as `powershell -Command ...` are not valid command
intents.

## Status Language

- **Go**: profile tests pass, runtime mount config validates, shell is not the
  safety boundary, and a real smoke has redacted evidence.
- **Partial**: profile/tests/runbook exist, but runtime mount config or real
  smoke evidence is pending.
- **No-Go**: broad host roots, shell-as-sandbox, secrets, or destructive
  project commands are required.
