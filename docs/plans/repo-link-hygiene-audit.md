# Repository Link Hygiene Audit

Stand: 2026-06-19

Status: **offline guard for `ABC3E-repo-link-audit`**

## Decision

Odysseus currently has two legitimate repository roles:

- Original/upstream: `https://github.com/pewdiepie-archdaemon/odysseus.git`
- Fork/publish fallback: `https://github.com/fuzzy123-ai/odysseus-fuzzy.git`

The Obsidian plugin repository is also legitimate where plugin installation docs need it:

- Plugin: `https://github.com/fuzzy123-ai/Odysseus-plugin-obsidian.git`

Known external dependency links are allowed when they are not presented as Odysseus repositories:

- External dependency: `https://github.com/FiloSottile/mkcert`

This slice does not rewrite install paths blindly. The original repo is still the canonical `origin`; the fork is the known fallback while local credentials push as `fuzzy123-ai`.

## Current Risk

The release risk is not a confirmed typo in the scanned files. The risk is role ambiguity: operators can confuse original, fork, and plugin repositories unless release docs make their role explicit.

## Guard

`src/repo_link_hygiene.py` and `tests/test_repo_link_hygiene.py` provide a small offline guard:

- known original/fork/plugin/external-dependency slugs are classified by role
- unknown GitHub repo slugs in selected release docs block the report
- known typo variants such as `odyseus`, `odysues`, and `odysseuss` block the report
- no network call is performed

## Go / Partial / No-Go

Go:
- every release-facing GitHub slug in the selected docs maps to an explicit role
- no known typo variant appears in the selected docs
- operator text states whether a link targets original, fork, or plugin

Partial:
- known slugs are clean, but some docs still rely on surrounding context instead of explicit role labels

No-Go:
- unknown GitHub repo slugs appear in release-facing install docs
- a typo variant appears in release-facing install docs
- fork-only publishing is reported as original publishing

## Next Action

If public release copy still feels ambiguous, change wording in README/setup/index docs in a separate docs-only slice. Do not use this guard to rename `origin` or rewrite repository history.
