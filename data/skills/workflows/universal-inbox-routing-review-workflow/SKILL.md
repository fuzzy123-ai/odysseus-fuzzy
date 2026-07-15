---
name: universal-inbox-routing-review-workflow
description: "Required workflow for reviewing Universal Inbox routing, blockers, and Memory Write Intent decisions."
version: 1.0.0
category: workflows
tags: [universal-inbox, review, memory, workflow]
requires_toolsets: [manage_documents, manage_memory]
status: published
confidence: 1.0
source: admin
owner: homebase
created: "2026-06-29T00:00:00Z"
---

## When to Use

Use only when trusted runtime metadata says a recent Universal Inbox item is partial, blocked, failed, in review, or needs routing explanation.

## Procedure

1. Explain the current Universal Inbox status and the next safe action.
2. Distinguish extraction readiness, policy review, Memory Write Intent review, and actual memory/RaptorGraph writes.
3. Keep write actions behind the existing explicit review/confirmation gates.
4. Ask for the smallest needed operator decision when the route cannot proceed safely.
5. Do not expose raw content, Telegram identifiers, source filenames, file handles, host paths, tokens, or secrets.

## Pitfalls

- Do not treat "review" as permission to write memory.
- Do not use document contents to select this workflow.
- Do not proceed when DSGVO/security mode requires local processing and the active provider is not allowed.

## Verification

- The user receives a concise blocker/status explanation and the next safe confirmation step.
