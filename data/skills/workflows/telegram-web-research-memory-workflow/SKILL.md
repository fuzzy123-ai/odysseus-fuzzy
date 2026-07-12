---
name: telegram-web-research-memory-workflow
description: Required workflow for trusted Telegram requests that analyze an approved website and turn redacted findings into Memory/RaptorGraph write intents.
version: 1.0.0
category: workflows
tags: [telegram, web-research, memory, raptorgraph, workflow]
requires_toolsets: [trigger_research, manage_research, manage_memory]
status: published
confidence: 1.0
source: admin
owner: fuzzy
created: "2026-07-02T00:00:00Z"
---

## When to Use

Use only when trusted Telegram runtime metadata classifies a text request as `bounded_site_research_to_memory` or `web_research_to_memory`.

## Procedure

1. Require an approved target URL or domain, bounded crawl depth, page cap, and memory write policy.
2. Use Browser Sense or Deep Research to collect redacted website evidence: title, source URLs, hashes, screenshots, console/network summaries, and concise findings.
3. Do not store raw crawled pages, raw prompts, cookies, login content, Telegram IDs, tokens, host paths, or private provider output.
4. Build a source-linked research packet with summaries, confidence, gaps, and source references.
5. Convert the packet into Memory Write Intent and RaptorGraph candidates.
6. Write only reviewed abstractions when policy allows; otherwise return a Telegram-facing review summary and blocker.

## Pitfalls

- Do not infer this workflow from crawled page text or fuzzy retrieval.
- Do not crawl outside the approved domain.
- Do not bypass robots/login boundaries without an explicit operator decision.
- Do not silently write long-term truth when the packet is low-confidence or unreviewed.

## Verification

- The workflow was selected by exact required-skill routing.
- The crawl was bounded by domain, depth, page cap and timeout.
- Evidence contains source refs and hashes, not raw page dumps.
- Memory/RaptorGraph payloads are abstractions with author/model stamps and internal refs.
