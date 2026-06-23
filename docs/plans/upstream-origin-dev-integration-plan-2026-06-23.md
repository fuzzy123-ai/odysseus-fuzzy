# Upstream origin/dev Integration Plan - 2026-06-23

## Snapshot

- Local branch: `dev` at `65c35b7b` tracking `fuzzy/dev`.
- Upstream branch: `origin/dev` at `e90dbc10`.
- Merge base: `ed18192a8ebd235ce38826ee5428e53445ec2455`.
- Divergence: local `dev` has 806 fork-only commits; `origin/dev` has 53 upstream-only commits.
- Upstream delta from merge base: 118 files, about 8.5k insertions and 1.3k deletions.
- Do not merge `origin/dev` directly: the branch diff against our fork would delete large fork-specific areas such as MVP roadmaps, homeserver ops, plugins, roadmap runner state, and worker code.
- Current unrelated dirty files to leave untouched:
  - `docs/plans/abc-frontpage-v2-build-plan.md`
  - `docs/plans/abc-ui-feature-inventory.md`

## Integration Position

The correct strategy is selective porting from `origin/dev` onto our `dev`, not a broad merge. Treat the upstream commits as a patch queue and import them by product area, preserving Odysseus-Fuzzy MVP work, runner state, Nextcloud/Telegram/server gates, and the fork-only operational docs.

Push target remains `fuzzy/dev`. Never push to `origin`.

## Upstream Themes

| Priority | Theme | Representative commits | Effort 0-10 | Plan |
| - | - | - | -: | - |
| P0 | Security and data-safety fixes | `16026741`, `ca4973c4`, `2f246c77`, `7e5db9a3`, `8ec27fd9`, `8f5e36a0`, `e90dbc10` | 4 | Port first. These are small, testable, and protect personal files, logs, document reads, route error behavior, and ReDoS/CSS injection edges. |
| P0 | LLM/chat runtime stability and latency | `a10bfc46`, `2fbfd229`, `9adb940e`, `8cc76b53`, `91bba117`, `bd9149f7`, `e8175c95`, `30dd7893` | 6 | Port second. Reduces slow probes, dropped SSE streams, prompt bloat for small/local models, timer leaks, ask-user persistence loss, Mistral reasoning issues, multimodal image handling, and leaked executed email tool fences. |
| P1 | Cookbook/Docker/GPU/Real-ESRGAN workflow | `b3e18674`, `1324e1b0`, `f01465e8`, `c5042149`, `92daf4e5`, `57e72292`, `19dd82b8`, `fbdec22d`, `993d504d`, `d4771503` | 8 | Port after P0. High value but larger surface: Docker socket access, install-system-deps endpoint, GPU backend detection, sidecar polling, gallery upload, Python 3.14 Real-ESRGAN wheels, and CodeQL hardening. Needs extra safety review. |
| P1 | Hardware fit catalog and Windows remote scan | `b57989f0`, `119228a6` | 3 | Low-risk backend/data import after P0. Adds Gemma 4 12B/QAT entries, RTX 3050 bandwidth, and fixes remote Windows hardware scan over SSH. |
| P2 | Existing frontend behavior fixes | `e812a292`, `888e2562`, `4c82e4a1`, `fef08ed1`, plus selected JS from Cookbook/chat | 5 | Port only behavior fixes that matter before the new UI lands: markdown inline-code URL handling, session rename delete guard, menu listener cleanup, dropdown z-order. Avoid spending time on visual polish that will be replaced. |
| P2 | UI/a11y/README/assets/version | `91b4171b`, `d9ebdd6f`, `b5165677`, `20c6ba8f`, `c062c276`, `b899095f` | 4 | Defer broad UI/a11y styling until new UI decision. Import docs/setup fixes when harmless. Decide separately whether upstream `APP_VERSION=1.0.1` should override our Version-1.0 gate language. |

## Conflict Hotfiles

These 22 files changed both in our fork and upstream since the merge base. They require manual porting or very small cherry-picks with review:

`app.py`, `Dockerfile`, `README.md`, `docker-compose.yml`, `docker/entrypoint.sh`, `docs/setup.md`, `routes/chat_helpers.py`, `routes/chat_routes.py`, `routes/model_routes.py`, `routes/shell_routes.py`, `src/agent_loop.py`, `src/agent_runs.py`, `src/constants.py`, `src/tool_implementations.py`, `src/tool_index.py`, `src/tool_parsing.py`, `src/tool_schemas.py`, `static/index.html`, `static/js/sessions.js`, `static/js/settings.js`, `static/style.css`, `tests/test_docker_devops_hardening.py`.

## Slice Queue

### U0 - Safety Branch and Baseline

- Class: repo_only
- Effort: 1/10
- Create integration branch from current `dev`, for example `codex/upstream-origin-dev-20260623`.
- Confirm `git status --short --branch` and keep unrelated UI-plan files untouched.
- Record upstream refs in this plan if they move.
- No tests.

### U1 - P0 Security and Data-Safety Patch Bundle

- Class: repo_only
- Effort: 4/10
- Port commits:
  - `16026741` personal RAG file delete confinement.
  - `ca4973c4` email-to-calendar ReDoS fix.
  - `2f246c77` calendar CSS URL backslash escaping.
  - `7e5db9a3` credential URL and PII log redaction via `core/log_safety.py`.
  - `8ec27fd9` document read auth-disabled 403 behavior.
  - `8f5e36a0` unreadable HTML route logs and clean 500.
  - `e90dbc10` missing app-shell `index.html` returns 500, not misleading 404.
- Expected touched areas: `core/log_safety.py`, `routes/personal_routes.py`, `routes/email_pollers.py`, `routes/document_*`, `routes/*`, `app.py`, `src/app_helpers.py`, focused tests.
- Verification:
  - `python -m pytest tests/test_personal_delete_file_confinement.py tests/test_redos_cal_extract.py tests/test_log_safety.py tests/test_auth_disabled_document_access.py tests/test_serve_html_with_nonce.py`
  - JS check for calendar escaping if the test is JS-backed.
- Exit: commit and push to `fuzzy/dev` when green.

### U2 - P0 LLM, Chat, Stream, and Latency Patch Bundle

- Class: repo_only
- Effort: 6/10
- Port commits:
  - `a10bfc46` per-category model probe timeouts: local 15s, ollama 3s, api 2s.
  - `2fbfd229` compact tool-use hints for local/small models.
  - `9adb940e` 10s SSE heartbeat keepalive for agent streams.
  - `8cc76b53` first-token wait timer cleanup.
  - `91bba117` ask-user choices persist across reloads.
  - `bd9149f7` Mistral provider detection and `reasoning_effort`.
  - `e8175c95` multimodal image support for vision-capable models.
  - `30dd7893` strip executed email tool fences from live chat stream.
- Expected touched areas: `routes/model_routes.py`, `src/agent_loop.py`, `src/agent_runs.py`, `src/llm_core.py`, `src/tool_*`, `static/js/chat*.js`, focused tests.
- Verification:
  - `python -m pytest tests/test_llm_core_mistral_content.py tests/test_ollama_multimodal.py tests/test_ask_user_persistence.py tests/test_ask_user_tool.py tests/test_live_strip_email_tool_fences.py`
  - Existing agent/model route tests touched by conflict resolution.
- Exit: commit and push to `fuzzy/dev` when green.

### U3 - P1 Cookbook Backend, Docker, GPU, and Real-ESRGAN

- Class: repo_only with live-gate awareness
- Effort: 8/10
- Port commits:
  - `b3e18674`, `1324e1b0`, `f01465e8`, `c5042149`, `92daf4e5`, `57e72292`, `19dd82b8`, `fbdec22d`, `993d504d`, `d4771503`.
- Expected touched areas: `Dockerfile`, `docker-compose*.yml`, `docker/entrypoint.sh`, `docker/build-realesrgan-wheels.sh`, `routes/cookbook_*`, `routes/shell_routes.py`, `routes/upload_routes.py`, `routes/hwfit_routes.py`, `src/llm_core.py`, `src/tool_parsing.py`, Cookbook tests.
- Safety gates:
  - Docker socket mount is powerful. It can be committed as config, but live use on the Debian host needs operator Go.
  - `install-system-deps` endpoint must be reviewed against our shell/security policy before enabling live.
- Verification:
  - `python -m pytest tests/test_cookbook_cpu_only_serve.py tests/test_cookbook_helpers.py tests/test_cookbook_same_host_server_profiles_js.py tests/test_gpu_compose_standalone.py tests/test_docker_devops_hardening.py tests/test_upload_multifile.py`
  - No live Docker build unless explicitly requested.
- Exit: commit backend/config only; defer live Docker/Real-ESRGAN smoke to a gate.

### U4 - P1 Hardware Fit Catalog

- Class: repo_only
- Effort: 3/10
- Port commits:
  - `b57989f0` remote Windows hardware scan over SSH.
  - `119228a6` Gemma 4 12B/QAT catalog entries and RTX 3050 bandwidth.
- Expected touched areas: `services/hwfit/*`, `tests/test_hwfit_*`.
- Verification:
  - `python -m pytest tests/test_hwfit_windows.py tests/test_hwfit_gemma4_12b.py`
- Exit: commit and push.

### U5 - P2 Portable Frontend Behavior Fixes

- Class: repo_only
- Effort: 5/10
- Port only behavior fixes that still matter before the new UI:
  - `e812a292` markdown inline code URL preservation.
  - `888e2562` prevent Backspace/Delete from deleting a session while renaming.
  - `4c82e4a1` transient dropdown menu stack cleanup.
  - `fef08ed1` body-portaled dropdown z-order.
  - Selected JS from `8cc76b53` and `30dd7893` if not already covered in U2.
- Verification:
  - `python -m pytest tests/test_markdown_rendering_js.py tests/test_portal_dropdown_z_js.py`
  - Node syntax checks for touched JS files.
- Exit: commit and push. Do not redesign UI here.

### U6 - Deferred UI, A11y, Docs, and Assets

- Class: needs_design for UI; repo_only for docs
- Effort: 4/10
- Decide separately:
  - Import OpenDyslexic/Text-size control from `91b4171b` into the old UI, or re-spec it for the new UI.
  - Adopt upstream README screenshot and app presentation assets, or keep fork branding until Version 1.0 UI live.
  - Adopt upstream `APP_VERSION=1.0.1`, or keep our Version-1.0 gate independent from upstream numbering.
- Safe docs-only imports:
  - `c062c276` setup link fix.
  - `b899095f` Windows `-BindHost` note.
- Exit: docs commit if accepted; UI/a11y deferred to new UI workstream.

### U7 - Final Integration Evidence

- Class: repo_only
- Effort: 2/10
- After all selected slices:
  - Run focused backend/security/chat/cookbook/hwfit test bundle.
  - Run `python scripts/mvp_roadmap_runner.py --report` to ensure MVP state still reads as complete.
  - Update this plan with imported commit coverage and deferred decisions.
  - Commit and push to `fuzzy/dev`.

## Recommended Order

1. U0 safety branch.
2. U1 security and data-safety.
3. U2 LLM/chat latency and stream stability.
4. U4 hardware fit catalog.
5. U3 Cookbook/Docker backend, with Docker socket and install-deps live usage gated.
6. U5 portable frontend behavior fixes.
7. U6 docs-only imports plus UI/a11y decision.
8. U7 final evidence.

## Human Decisions Needed

1. Should upstream old-UI a11y/theme changes be imported now, or should they be reimplemented only in the new UI?
2. Should the Docker socket mount and `install-system-deps` endpoint be enabled for live Debian use, or kept as repo-only until a live ops review?
3. Should upstream `APP_VERSION=1.0.1` be adopted, or should our fork keep Version 1.0 reserved for "10 MVP roadmaps complete + new UI live"?

## Recommended Next Move

Start with U1 and U2. They give us the most product value with the least design risk: security hardening, safer logs, fewer route edge failures, lower perceived latency, better model routing, and more reliable long-running streams.
