# Interactive Game Artifact Closure Roadmap

Status: production deploy and bounded live validation passed; upstream publication hygiene pending
Mode: Standard ABC, repository plus bounded live deployment
Started: 2026-07-12
Live gate: granted by the user on 2026-07-12 with `Go deploy`

## Outcome

An Odysseus answer that creates a Pygame game must distinguish four different facts:

1. a native Python file was created;
2. the file passed syntax and bounded headless checks;
3. captured frames were actually inspected as images;
4. the user received an owner-scoped download link.

The assistant must never present a dummy-SDL run as an interactive preview. If the user asks to play inside Odysseus, the deliverable is a self-contained browser artifact. If the user explicitly asks for Pygame, the deliverable is a downloadable native artifact. If both are requested, both artifacts are required.

## Production evidence that opened this roadmap

- The generated `mario_game.py` existed and was syntactically valid.
- `pygame-ce` imported successfully, but the game only ran with `SDL_VIDEODRIVER=dummy`.
- The first screenshot script mutated `state` although the game reads `game_state`; its screenshots were therefore identical.
- Later PNG files were distinct and structurally valid, but no vision-capable tool inspected them. File dimensions, hashes and individual pixels were treated as visual QA.
- Shell tool output exposed file paths as text. It did not emit the image payload that the chat renderer expects.
- The prompt already warns that native GUI loops do not run interactively in the sandbox. This is advisory text, not a runtime gate.
- Existing claim evidence proves file existence and command completion, but not semantic visual inspection or download availability.

## Deliverable policy

| User intent | Required output | Runtime claim allowed |
|---|---|---|
| Explicit Pygame/native app | `.py` plus dependency/run instructions and owner-scoped download | `headless_tested`, never `interactive_preview_ready` from dummy SDL |
| Play/open/link here | self-contained HTML/canvas artifact | `interactive_preview_ready` only after browser smoke |
| Both native and in-app | both outputs | claims stay separate per artifact |
| Ambiguous game request | choose browser preview and state the choice | no native-GUI claim |

## Work slices

### Alice — artifact and vision architecture

- Map the existing owner-protected upload/download route.
- Identify the smallest safe registration API for generated files.
- Map assistant attachment persistence and rendering.
- Identify a real image-analysis call path for captured screenshots.

### Bob — deterministic policies

- Implement a pure deliverable decision contract.
- Classify native interactive launches, dummy/headless capture, dependency probes and risky install attempts.
- Detect pipeline-masked failures where the producer failed but a trailing command returned zero.
- Add focused unit tests.

### Charlie — Pygame headless contract

- Define a bounded, deterministic verification manifest.
- Require syntax/import checks, dummy SDL, a frame bound and screenshot evidence.
- Keep `headless_tested` distinct from interactive execution.
- Validate safe repository-relative artifact references.

### Root — integration

- Add owner-scoped generated-artifact publication using the existing upload store and route.
- Persist generated assistant attachments in message metadata.
- Render those attachments after reload as preview/download cards.
- Connect screenshot evidence to actual vision analysis rather than path/hash inspection.
- Enforce the runtime policy at tool execution boundaries.
- Extend truth claims to `file_created`, `syntax_verified`, `headless_tested`, `visual_inspected`, `download_ready`, and `interactive_preview_ready`.

## Safety boundaries

- Never serve arbitrary filesystem paths.
- Never put absolute server paths into assistant-visible metadata.
- Every download remains authenticated and owner-scoped.
- Only allow approved generated-file roots, regular files, safe names, bounded sizes and detected MIME types.
- Never install OS packages from a model-generated shell command.
- Never hide an upstream command failure behind a successful pipeline tail.
- Never claim visual quality until a vision result references the exact captured artifact.
- Preserve all unrelated dirty-worktree changes; no reset, checkout or broad formatting.

## Gates

### G1 — contract tests

- Deliverable intent matrix is deterministic.
- Runtime classifier rejects or rewrites native GUI launches.
- Dummy-SDL execution can prove only `headless_tested`.
- Unsafe paths and unsupported artifact types fail closed.

### G2 — backend integration

- Generated `.py`, `.html` and screenshot artifacts register in the existing owner store.
- A different owner cannot fetch the artifact.
- Assistant metadata contains stable attachment IDs and no raw filesystem path.
- Vision analysis is bound to the screenshot ID/hash it inspected.

### G3 — frontend persistence

- Assistant file cards render for live messages and history reloads.
- Images preview safely; other files expose a download action.
- Missing or forbidden artifacts fail without exposing storage details.

### G4 — end-to-end repository smoke

- Create a tiny Pygame fixture.
- Run syntax/import/dummy-SDL bounded-frame checks.
- Capture at least one frame and inspect it through the vision path.
- Publish the `.py` and screenshot as owner-scoped attachments.
- Verify download, owner isolation, reload persistence and truthful claims.

### G5 — live validation (separate Go)

- Deploy only an explicitly reviewed commit to the homeserver.
- Repeat the runtime smoke with disposable owner storage and no retained artifact.
- Verify the deployed Linux image, owner/download routes, persistence contract, UI hooks, logs, and cleanup.
- Run a persistent disposable-chat canary only when per-artifact cleanup exists or retention is explicitly accepted.
- Roll back if auth scope, persistence, rendering or claim evidence regresses.

## Stop rules

- Stop before live deployment without a bounded live Go.
- Stop publication if relevant hotfiles cannot be separated from unrelated user changes.
- Stop on any owner-isolation regression.
- Stop visual claims if the vision provider is unavailable; retain the honest state `capture_created`, not `visual_inspected`.

## Completion record

Repository implementation is complete only when G1–G4 pass. A production result is complete only after G5 receives its own Go and passes.

### 2026-07-12 repository closeout

- G1 passed: deterministic deliverable/runtime contracts and the Pygame headless contract are integrated at the agent/tool boundary.
- G2 passed: generated `.py`, self-contained `.html`, and verified PNG files use a fail-closed owner-scoped registration path in the existing upload store. The agent loop performs owner-bound readback before metadata persistence.
- G3 passed: generated assistant attachments render live and after reload, have an explicit ID-derived Download action, and assistant images cannot trigger user-message regeneration.
- G4 passed: a deterministic end-to-end test covers create → bounded headless capture → owner-scoped publish → vision-path inspection → metadata persistence → claim evidence → cross-owner denial. A separate real local Pygame/SDL-dummy smoke also passed.
- Focused suite: 142 passed, 4 skipped. The skips are platform-conditional symlink cases; the real Pygame smoke passed.
- Adjacent regression suite: 226 passed, 1 skipped.
- End-to-end/claim closeout: 13 passed.
- Python compilation, both edited JavaScript syntax checks, and `git diff --check` passed. Git reported only existing LF/CRLF notices.
- Impeccable review: the new Download control uses the existing product tokens, a documented 5px radius, visible focus, and a 44px mobile target. Detector findings elsewhere in the large legacy chat/style files predate this slice and were left unchanged.
- G5 deploy/smoke passed under the bounded live Go. A persistent chat-history canary remains intentionally deferred because production has no owner-scoped single-artifact cleanup endpoint; no old chat was backfilled.

### 2026-07-12 live closeout

- Production now runs feature-only commit `25e7d11b6a27` and image `9987d90fa3a1`, based directly on the prior production commit so unrelated, root-owned `data/skills` updates were not forced into this release.
- The current production overlay was preserved exactly at 18 Git porcelain entries. The pre-update Restic snapshot is `9b1db66e`; repository check and restore smoke both passed. A separate rollback packet contains the old HEAD, tracked binary patch, untracked archive, and old image ID.
- Release tests passed on both the production-base commit and the current upstream-base cherry-pick: `148 passed, 4 skipped`; the real Pygame dummy-SDL test passed explicitly. The current Linux candidate added `11 passed`, and the post-deploy owner/download/reload suite added `77 passed`.
- The running container passed an ephemeral central-dispatch smoke with `pygame-ce 2.5.6`, a valid 6,255-byte PNG, `headless_verified=true`, `interactive_ready=false`, `download_ready=true`, and cross-owner denial. Its temporary workspace/upload store was removed automatically.
- Live `/api/health`, Chroma heartbeat, served attachment UI hooks, and `/api/version` passed. Post-deploy logs contained zero `Traceback`, zero `Unknown tool type`, and zero owner-readback failures.
- The GitHub push was not performed because the external remote ownership was not independently approved. Production therefore reports an available/diverged upstream state, and the pre-existing dirty worktree still prevents the automatic updater from running. This is an operations follow-up, not a runtime failure of this release.
- No persistent disposable chat/artifact was created, and the old Mario chat was deliberately not mutated or backfilled.
