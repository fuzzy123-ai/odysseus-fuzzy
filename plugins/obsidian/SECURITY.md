# Security Policy

## Supported Versions

For the current RC cycle:

- `0.10.0-rc.1` in the active `dev` line is the supported release-candidate target.
- Security fixes should be applied to the latest maintained `dev` commit before any public tag or release branch is cut.
- Older exploratory commits and ad-hoc local plugin snapshots should be treated as unsupported once a newer RC checkpoint exists.

## RC Security Notes

The current release-candidate scope includes a few explicit limits:

- Vault password protection controls plugin access, but it is not full at-rest encryption for plaintext Markdown already stored on disk.
- RAPTOR status and readiness surfaces are read-only in the RC; rebuild/write flows stay disabled until they are separately hardened and tested.
- Risky write paths such as imports, project-plan apply flows, memory-review apply flows, and destructive file operations should only be used with the existing confirmation gates intact.
- Authenticated plugin data routes must stay protected even when the standalone app shell and static assets are allowed to load before login.

## RC Operator Checklist

Before calling the current RC line ready for wider internal use, confirm:

1. `plugin.py` and `plugin.json` advertise the same `0.10.0-rc.1` version string.
2. The install path, upgrade path, and release-archive layout in the READMEs still match the actual distribution flow.
3. Password-protection wording in the UI and docs still states that plugin access control is not full at-rest encryption for existing plaintext vault files.
4. Authenticated plugin data routes remain gated even if the standalone app shell and static assets are allowed to load before login.

## Reporting a Vulnerability

Please report vulnerabilities responsibly and privately.

Recommended options:

1. Use GitHub private vulnerability reporting (if enabled in repository settings).
2. Open a private communication channel with the repository owner.

Please include:

- A clear description of the vulnerability
- Reproduction steps
- Potential impact
- Suggested remediation (if available)

If the issue involves vault-content leakage, path traversal, import/export handling, password flows, or authenticated route bypass, include the affected route or tool name if known.

Do **not** open public issues for undisclosed security vulnerabilities.
