---
name: telegram-document-export-workflow
description: Required workflow for Telegram requests that convert or export the latest Universal Inbox attachment.
version: 1.0.0
category: workflows
tags: [telegram, universal-inbox, export, workflow]
requires_toolsets: [manage_documents]
status: published
confidence: 1.0
source: admin
owner: fuzzy
created: "2026-06-29T00:00:00Z"
---

## When to Use

Use only when trusted runtime metadata says the current Telegram text turn has a recent Universal Inbox attachment and the intent is export or conversion.

## Procedure

1. Confirm that the requested target format is supported by the Universal Export pipeline.
2. Use the existing export planner/executor path; do not invent ad-hoc conversion logic.
3. Keep Universal Inbox write and delivery gates intact.
4. Return a clear Telegram-facing result when export is blocked, unsupported, or requires review.
5. Do not persist raw file contents, source filenames, Telegram identifiers, file handles, host paths, or provider output.

## Pitfalls

- Do not unlock export from document contents.
- Do not bypass DSGVO/security mode.
- Do not silently fall back to fuzzy skills if this required workflow is unavailable.

## Verification

- The export plan names the target format and built-in tool or blocker.
- The Telegram reply reports delivery, unsupported format, or the review blocker.
