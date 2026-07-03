# Origin Publish Hygiene

Stand: 2026-06-19

Status: **credential-gated; active ABC publish target is fuzzy/dev**

## Goal

Keep Odysseus publishing unambiguous while the local GitHub credential mismatch is unresolved.

## Current Evidence

- Current branch: `dev`.
- `origin` points to the intended original repository: `https://github.com/pewdiepie-archdaemon/odysseus.git`.
- `fuzzy` points to the intended fork: `https://github.com/fuzzy123-ai/odysseus-fuzzy.git`.
- The central ABC masterplan now defines `fuzzy/dev` as the only active
  publish target for this fork.
- `git push origin dev` historically failed with GitHub `403` because the
  local credential manager authenticated as `fuzzy123-ai`.
- `git push fuzzy dev` succeeds and is the active publish path.
- Latest known pushed fork commit before this document: `935d11f6 Unify ABC execution roadmap`.

## Decision

Until the local credential manager authenticates as an account with write access to `pewdiepie-archdaemon/odysseus`, Charlie may:

- keep `origin` configured as the original repo;
- treat `origin` as read-only for ABC work;
- push completed scoped roadmap commits to `fuzzy/dev`;
- report any origin write as credential-gated, not as an implementation task.

Charlie must not:

- rename `origin` to the fork;
- push ABC work to `origin`;
- force-push;
- rewrite history to compensate for a credential problem;
- claim that the original repo was updated when only `fuzzy/dev` accepted the push.

## Go / Gated / No-Go

Go:
- `origin/dev` accepts normal non-force pushes from the intended account.
- The fork remains available as a secondary remote.

Gated:
- Current state. Remotes are correctly named, origin writes are outside the
  active ABC publish path, and fork publishing is working and explicit.
- No repo implementation work is blocked by this gate because `fuzzy/dev` is
  the active target.

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
push scoped commits to fuzzy/dev
record origin as credential-gated/read-only
never force-push
```
