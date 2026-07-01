# Gemma Memory Efficiency Benchmark

Goal: prove whether `gemma4:e4b` is good enough for Odysseus local memory
workflows: Universal Inbox triage, DSGVO routing, Memory Write Intent,
RaptorGraph provenance, and follow-up recall.

## Scope

- Synthetic and redacted cases only.
- No private documents, chat IDs, tokens, host paths, raw prompts, or raw model
  outputs are persisted.
- Default mode is deterministic and offline.
- Live mode calls the configured local model and still writes only redacted
  metrics if `--output` is used.

## Runner

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe scripts\gemma_memory_benchmark.py
```

Live local Ollama example on the Debian Podman stack:

```bash
podman exec -i odysseus_odysseus_1 python scripts/gemma_memory_benchmark.py \
  --live \
  --base-url http://ollama:11434/api \
  --model gemma4:e4b \
  --provider local_ollama
```

## Cases

| Case | Checks |
| --- | --- |
| `project_decision_podman` | durable project decision, ready memory intent |
| `dsgvo_sensitive_invoice` | sensitive classification, local-only, review gate |
| `telegram_followup_after_file` | recent attachment recall without raw content |
| `smalltalk_skip_memory` | transient content is not written to long-term memory |
| `nextcloud_import_triage` | import signal can become abstract project memory |

## Score

| Category | Weight |
| --- | ---: |
| JSON/schema contract | 15 |
| Sensitivity and local/API routing | 25 |
| Memory Write Intent status | 30 |
| Recall terms and safe pipeline | 20 |
| Runtime target | 10 |

Gemma is considered memory-ready when the total score is at least `80`, all
local-only gates pass, and no case stores raw content.

## ABC Status

Mode: Standard ABC.

Slices:

- `GMB-1` repo_only: benchmark contracts and synthetic cases.
- `GMB-2` repo_only: CLI runner and redacted report.
- `GMB-3` safe_offline: tests for contract, privacy, and scoring.
- `GMB-4` needs_live_go: run `gemma4:e4b` against live local Ollama and report
  only aggregate/redacted results.

Non-goals:

- No UI integration.
- No live memory writes.
- No real Nextcloud/Telegram content.
- No API model comparison unless explicitly requested later.
