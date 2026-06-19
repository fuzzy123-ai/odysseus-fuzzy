# Origin Publish Hygiene

Stand: 2026-06-19

Status: **Partial; origin is correct, local credentials still block upstream push**

## Goal

Keep Odysseus publishing unambiguous while the local GitHub credential mismatch is unresolved.

## Current Evidence

- Current branch: `dev`.
- `origin` points to the intended original repository: `https://github.com/pewdiepie-archdaemon/odysseus.git`.
- `fuzzy` points to the intended fork: `https://github.com/fuzzy123-ai/odysseus-fuzzy.git`.
- Branch config tracks `origin/dev`.
- `git push origin dev` currently fails with GitHub `403` because the local credential manager authenticates as `fuzzy123-ai`.
- `git push fuzzy dev` succeeds and is the current publish fallback.
- Latest known pushed fork commit before this document: `935d11f6 Unify ABC execution roadmap`.

## Decision

Until the local credential manager authenticates as an account with write access to `pewdiepie-archdaemon/odysseus`, Charlie may:

- keep `origin` configured as the original repo;
- attempt `git push origin dev` once during publish;
- treat a repeated `403` as a credential blocker, not a repository-target ambiguity;
- push the same commit to `fuzzy/dev`;
- report `dev...origin/dev [ahead N]` as expected while origin is blocked.

Charlie must not:

- rename `origin` to the fork;
- force-push;
- rewrite history to compensate for a credential problem;
- claim that the original repo was updated when only `fuzzy/dev` accepted the push.

## Go / Partial / No-Go

Go:
- `origin/dev` accepts normal non-force pushes from the intended account.
- The fork remains available as a secondary remote.

Partial:
- Current state. Remotes are correctly named, but local credentials block origin writes.
- Fork publishing is working and explicit.

No-Go:
- Remote target is ambiguous.
- A force push, history rewrite, token logging, or credential dump would be needed.
- A commit is reported as pushed to origin when only the fork accepted it.

Deferred:
- Any repository transfer, remote rename, credential-store surgery, or token rotation outside normal GitHub account login flow.

## Operator Fix Path

Safe next steps for a human operator:

1. Open the GitHub credential manager or GitHub CLI account state locally.
2. Ensure the active account has write access to `pewdiepie-archdaemon/odysseus`.
3. Retry a normal `git push origin dev`.
4. Do not paste tokens, passwords, recovery codes, or credential helper output into docs, prompts, logs, or handoffs.

## Charlie Publish Rule

For ABC roadmap work:

```text
try origin/dev once
if origin returns 403 with fuzzy123-ai, push fuzzy/dev
record origin as blocked by local credentials
never force-push
```
