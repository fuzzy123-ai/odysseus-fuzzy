# Telegram Project Intake Completion Roadmap

Status: complete for backend/API/Telegram scope; UI design remains gated

Goal: Telegram project plans can be sent while mobile, reviewed in Telegram, confirmed with `/project ok`, and then safely integrated into a project-local state that the Project Manager UI and later AI merge workers can consume.

Mode: Standard ABC, backend/logik-first.

Non-goals:
- No visual Project UI design in this track.
- No live provider, deploy, Cloudflare, GitHub, Nextcloud or host mutation.
- No raw Telegram IDs, private raw text, secrets, tokens or host paths in repo artifacts.

Slice queue:

1. `TPI-1-roadmap`
   - Class: repo_only
   - Owner: Alice/Charlie
   - Status: done
   - Output: this execution roadmap.

2. `TPI-2-project-state-merge`
   - Class: repo_only
   - Owner: Bob
   - Status: done
   - Allowed paths: `src/project_intake.py`, `src/server_project_intake_state.py`, `tests/test_server_project_intake_state.py`
   - Goal: read project intake ledger events, dedupe tasks/decisions/risks/roadmap updates, and persist redacted `project_state.json`.

3. `TPI-3-api-contract`
   - Class: repo_only
   - Owner: Bob
   - Status: done
   - Allowed paths: `routes/server_project_routes.py`, `tests/test_server_project_routes.py`
   - Goal: expose intake state/status and explicit merge endpoints for v2 Project Manager.

4. `TPI-4-telegram-confirm-flow`
   - Class: repo_only
   - Owner: Charlie
   - Status: done
   - Allowed paths: `plugins/telegram/plugin.py`, `tests/test_telegram_plugin.py`
   - Goal: `/project ok` applies the reviewed proposal to the ledger and then merges it into project state with a useful Telegram reply.

5. `TPI-5-final-verification`
   - Class: repo_only plus already-approved deploy
   - Owner: Charlie
   - Status: done
   - Goal: focused tests pass, scoped commit pushed to `fuzzy/dev`, Debian version updated.

Done definition:
- Telegram plan preview remains review-gated.
- `/project ok` creates or reuses the ledger event and merges it into project state.
- Duplicate plans do not create duplicate project tasks.
- API can show Project Manager-ready intake state and merge results.
- Telegram tells the user what was integrated and what remains held.
- No raw chat IDs, raw source text, secrets or host paths are persisted in repo artifacts or returned API payloads.

Gate queue:
- UI placement and visual design remain `needs_design` for the UI agent.
- AI semantic merge beyond deterministic dedupe remains a later enhancement; this track creates the state contract it will write to.

Completion evidence:
- `src/server_project_intake_state.py` merges applied ledger events into redacted `project_state.json`.
- `/api/projects/{project_slug}/intake/state` exposes Project Manager-ready state snapshots.
- `/api/projects/{project_slug}/intake/merge` performs explicit ledger-to-state merge.
- Telegram `/project ok` now applies the review, merges the event into project state, and replies with integrated item counts.
- Focused verification: `87 passed` for project intake, intake state, server project routes and Telegram plugin tests.
