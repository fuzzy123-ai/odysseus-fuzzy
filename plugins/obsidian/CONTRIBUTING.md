# Contributing to Odysseus Plugin for Obsidian

Thanks for your interest in contributing!

## Development workflow

- `main` is reserved for stable releases.
- `dev` is the active integration branch.
- Create feature branches from `dev`.
- Open pull requests targeting `dev`.

## Local setup

1. Clone the repository.
2. Copy `.env.example` to `.env` or `.env.local`.
3. Configure local values and never commit secrets.

## Pull request guidelines

- Keep PRs focused and small.
- Explain what changed and why.
- Reference related issues when applicable.
- Mention the smallest relevant tests or smoke checks you ran, or say what you could not run.
- Ensure no secrets or machine-specific paths are committed.

## Code style

- Prefer clear, maintainable code.
- Keep configuration flexible and environment-driven.
- Avoid hardcoded local paths or API endpoints.

## RC release hygiene

- Keep `plugin.py` and `plugin.json` on the same plugin version string before cutting an RC or archive.
- Keep install, upgrade, and release-archive notes aligned across the root README, the Obsidian README, and `plugins/obsidian/SECURITY.md`.
- Preserve the wording that vault password protection is not full at-rest encryption for existing plaintext Markdown files.
- Do not ship local-only artifacts such as `__pycache__/`, `.obsidian/`, or ad-hoc smoke-test output in release branches or archives.
