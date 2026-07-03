"""Repo, skill, chat-search, and recent-change tool implementations."""

import json
import logging
import os
from typing import Any, Dict, Optional

from src.tool_domains.common import _parse_tool_args
from src.tool_domains.repo_output import (
    repo_changes_output as _repo_changes_output,
    repo_commit_output as _repo_commit_output,
    repo_forge_output as _repo_forge_output,
    repo_push_output as _repo_push_output,
    repo_status_output as _repo_status_output,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Search chats
# ---------------------------------------------------------------------------

async def do_search_chats(query: str, limit: int = 20, owner: str | None = None) -> Dict:
    """Search past session transcripts for the calling user's sessions only.

    Without an owner filter this used to leak EVERY user's chat history
    into the agent's `search_chats` results (v2 review HIGH-11). The
    caller in `tool_execution.execute_tool_block` now plumbs the owner
    through; legacy callers without owner pass through as before but
    will only see legacy/null-owner rows.
    """
    try:
        from src.session_search import search_session_messages

        results = search_session_messages(query, limit=limit, owner=owner)
        if not results:
            return {"results": f"No chats found matching \"{query}\"."}

        # Group by session to avoid duplicate links
        seen_sessions = {}
        for result in results:
            if result.session_id not in seen_sessions:
                seen_sessions[result.session_id] = result

        lines = [f"Found {len(seen_sessions)} session(s) matching \"{query}\":\n"]
        for sid, result in seen_sessions.items():
            lines.append(f"- **{result.session_name}** (#{sid})")
            lines.append(f"  Link: [Open chat](#{sid})")
            lines.append(f"  Match ({result.role}): {result.content_snippet}")
            if result.context_before:
                before = result.context_before[-1]
                lines.append(f"  Before ({before['role']}): {before['content'][:180]}")
            if result.context_after:
                after = result.context_after[0]
                lines.append(f"  After ({after['role']}): {after['content'][:180]}")
            lines.append("")

        return {"results": "\n".join(lines)}
    except Exception as e:
        logger.error(f"search_chats failed: {e}")
        return {"error": str(e), "exit_code": 1}


# ---------------------------------------------------------------------------
# Skills management tool
# ---------------------------------------------------------------------------

async def do_manage_skills(content: str, owner: Optional[str] = None) -> Dict:
    """Handle manage_skills tool calls.

    SKILL.md-backed CRUD with progressive disclosure (Hermes-style). Actions:

      list / index               — Level 0: name + description summary.
      view {name}                — Level 1: full SKILL.md.
      view_ref {name, path}      — Level 2: a sub-file under the skill dir.
      add  {name, description, when_to_use, procedure[], pitfalls[],
            verification[], tags[], category, status}
                                 — Create a new skill (draft by default).
      patch {name, old_string, new_string}
                                 — Token-efficient surgical edit on the
                                   raw SKILL.md text. Fails on ambiguous
                                   `old_string` (multiple matches).
      edit  {name, content}      — Replace the entire SKILL.md.
      publish {name}             — Flip status: draft -> published.
      delete {name}              — Remove the skill directory.
      search {query}             — Relevance match on published skills.
    """
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = (args.get("action") or "").lower()

    def _confirmed() -> bool:
        return bool(args.get("confirmed") or args.get("confirm"))

    def _confirmation_required(target: str) -> Dict:
        return {
            "response": f"Skill {target} requires explicit confirmation.",
            "status": "confirmation_required",
            "requires_confirmation": True,
            "exit_code": 0,
        }

    from services.memory.skills import SkillsManager
    from services.memory.skill_format import Skill, slugify
    from src.constants import DATA_DIR
    sm = SkillsManager(DATA_DIR)

    # Accept legacy `skill_id` as an alias for `name`.
    name = (args.get("name") or args.get("skill_id") or "").strip()

    if action in ("list", "index", ""):
        all_skills = sm.load(owner=owner)
        if not all_skills:
            return {"results": "No skills yet. Create one with action='add'."}
        published = [s for s in all_skills if s.get("status") == "published"]
        drafts = [s for s in all_skills if s.get("status") == "draft"]
        lines = []
        if published:
            lines.append("## Published")
            for s in sorted(published, key=lambda x: x["name"]):
                lines.append(f"- **{s['name']}** ({s.get('category','general')}): {s.get('description','')}")
        if drafts:
            lines.append("\n## Drafts")
            for s in sorted(drafts, key=lambda x: x["name"]):
                lines.append(f"- **{s['name']}** [draft]: {s.get('description','')}")
        return {"results": "\n".join(lines) if lines else "No skills yet."}

    if action == "view":
        if not name:
            return {"error": "name is required for view", "exit_code": 1}
        md = sm.read_skill_md(name, owner=owner)
        if md is None:
            return {"error": f"Skill {name!r} not found", "exit_code": 1}
        return {"results": md}

    if action == "view_ref":
        if not name:
            return {"error": "name is required for view_ref", "exit_code": 1}
        ref = (args.get("path") or "").strip()
        if not ref:
            return {"error": "path is required for view_ref", "exit_code": 1}
        text = sm.read_skill_reference(name, ref, owner=owner)
        if text is None:
            return {"error": f"Reference {ref!r} not found under {name!r}", "exit_code": 1}
        return {"results": text}
    if action == "add":
        if not name:
            return {
                "error": "name is required for add. Provide the exact slug the user should see, then report the returned name.",
                "exit_code": 1,
            }
        proc = args.get("procedure")
        if proc is None:
            proc = args.get("steps") or []
        if not proc and not args.get("body_extra") and not args.get("solution"):
            return {"error": "procedure (or solution body) is required", "exit_code": 1}
        # Same auto-publish gate as the extractor path — when the user
        # has auto_approve_skills on and the caller didn't pin an explicit
        # status, publish immediately. Audit later demotes/removes on fail.
        _status_arg = args.get("status")
        if not _status_arg:
            try:
                from routes.prefs_routes import _load_for_user as _load_prefs
                _prefs = _load_prefs(owner) or {}
                _status_arg = "published" if _prefs.get("auto_approve_skills", True) else "draft"
            except Exception:
                _status_arg = "draft"
        entry = sm.add_skill(
            name=args.get("name"),
            description=(args.get("description") or args.get("title") or "").strip(),
            category=args.get("category") or "general",
            tags=args.get("tags") or [],
            platforms=args.get("platforms") or [],
            requires_toolsets=args.get("requires_toolsets") or [],
            fallback_for_toolsets=args.get("fallback_for_toolsets") or [],
            when_to_use=(args.get("when_to_use") if args.get("when_to_use") is not None
                         else args.get("problem", "")),
            procedure=proc,
            pitfalls=args.get("pitfalls") or [],
            verification=args.get("verification") or [],
            status=_status_arg,
            version=args.get("version") or "1.0.0",
            confidence=args.get("confidence", 0.8),
            source=args.get("source", "learned"),
            teacher_model=args.get("teacher_model"),
            owner=owner,
            title=args.get("title", ""),
            problem=args.get("problem", ""),
            solution=args.get("solution", ""),
            steps=args.get("steps") or [],
        )
        if entry.get("_deduped"):
            return {"results": (
                f"A near-identical skill already exists: `{entry['name']}` — not creating "
                f"a duplicate. View or edit it with action='view', name='{entry['name']}'."
            )}
        try:
            from src.event_bus import fire_event
            fire_event("skill_added", owner)
        except Exception:
            logger.debug("skill_added event dispatch failed", exc_info=True)
        verify_hint = ""
        if entry.get("status") == "draft":
            verify_hint = (
                "\n\nThis skill is a DRAFT. Run through the procedure once to verify, "
                f"then publish with action='publish', name='{entry['name']}'."
            )
        return {"results": f"Created skill `{entry['name']}` — {entry.get('description','')}{verify_hint}"}

    if action == "edit":
        if not name:
            return {"error": "name is required for edit", "exit_code": 1}
        new_content = args.get("content")
        if not isinstance(new_content, str) or not new_content.strip():
            return {"error": "content (full SKILL.md) is required for edit", "exit_code": 1}
        try:
            sk_new = Skill.from_markdown(new_content)
        except Exception as e:
            return {"error": f"Could not parse content as SKILL.md: {e}", "exit_code": 1}
        sk_new.name = slugify(sk_new.name or name)
        existing = sm.load(owner=owner)
        match = next((s for s in existing if s.get("name") == name), None)
        if not match:
            return {"error": f"Skill {name!r} not found", "exit_code": 1}
        if not sk_new.owner:
            sk_new.owner = match.get("owner") or owner
        ok = sm.update_skill(name, _skill_dump(sk_new), owner=owner)
        return {"results": f"Edited skill `{sk_new.name}`."} if ok else {"error": "Update failed", "exit_code": 1}

    if action == "patch":
        if not name:
            return {"error": "name is required for patch", "exit_code": 1}
        old = args.get("old_string")
        new_str = args.get("new_string", "")
        if not isinstance(old, str) or not old:
            return {"error": "old_string is required and must be non-empty", "exit_code": 1}
        md = sm.read_skill_md(name, owner=owner)
        if md is None:
            return {"error": f"Skill {name!r} not found", "exit_code": 1}
        count = md.count(old)
        if count == 0:
            return {"error": "old_string not found in SKILL.md", "exit_code": 1}
        if count > 1:
            return {"error": f"old_string is ambiguous (appears {count} times). Make it more specific.", "exit_code": 1}
        new_md = md.replace(old, new_str, 1)
        try:
            sk_new = Skill.from_markdown(new_md)
        except Exception as e:
            return {"error": f"Patched content is not valid SKILL.md: {e}", "exit_code": 1}
        sk_new.name = slugify(sk_new.name or name)
        ok = sm.update_skill(name, _skill_dump(sk_new), owner=owner)
        return {"results": f"Patched skill `{sk_new.name}`."} if ok else {"error": "Patch update failed", "exit_code": 1}

    if action == "publish":
        if not name:
            return {"error": "name is required for publish", "exit_code": 1}
        all_skills = sm.load(owner=owner)
        match = next((s for s in all_skills if s.get("name") == name), None)
        if not match:
            return {"error": f"Skill {name!r} not found", "exit_code": 1}
        updates = {"status": "published"}
        if args.get("confidence") is not None:
            updates["confidence"] = max(0.0, min(1.0, float(args["confidence"])))
        sm.update_skill(name, updates, owner=owner)
        return {"results": f"✅ Published `{name}`. It now appears in the skills index for future turns."}

    if action == "delete":
        if not name:
            return {"error": "name is required for delete", "exit_code": 1}
        if not _confirmed():
            return _confirmation_required("delete")
        ok = sm.delete_skill(name, owner=owner)
        return {"results": f"Deleted skill `{name}`."} if ok else {"error": f"Skill {name!r} not found", "exit_code": 1}

    if action == "search":
        query = (args.get("query") or "").strip()
        if not query:
            return {"error": "query is required for search", "exit_code": 1}
        results = sm.get_relevant_skills(query, sm.load(owner=owner), max_items=5)
        if not results:
            return {"results": "No matching skills found."}
        lines = []
        for sk in results:
            proc = sk.get("procedure") or sk.get("steps") or []
            steps_str = " → ".join(proc[:5])
            lines.append(f"**{sk['name']}**: {sk.get('description','')}\n  When: {sk.get('when_to_use','')}\n  Steps: {steps_str}")
        return {"results": "\n\n".join(lines)}

    return {
        "error": (
            f"Unknown action: {action!r}. "
            "Use one of: list, view, view_ref, add, edit, patch, publish, delete, search."
        ),
        "exit_code": 1,
    }


async def do_recent_changes(content: str, owner: Optional[str] = None) -> Dict:
    """Create/read persistent local patch-note snapshots."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action") or "collect").strip().lower()
    try:
        from src.recent_changes import (
            collect_recent_changes,
            list_change_history,
            read_change_snapshot,
            render_patch_notes,
        )

        if action in {"collect", "summary", "summarize", ""}:
            snapshot = collect_recent_changes(
                hours=int(args.get("hours") or 12),
                persist=bool(args.get("persist", True)),
                force=bool(args.get("force", False)),
                trigger=str(args.get("trigger") or "tool"),
                retention_limit=int(args.get("retention_limit") or 0) or None,
            )
            if snapshot.get("persisted"):
                note = "stored"
            else:
                note = f"not stored (duplicate of {snapshot.get('duplicate_of')})"
            return {
                "output": render_patch_notes(snapshot) + f"\n\nHistory: {note}.",
                "snapshot": snapshot,
                "exit_code": 0,
            }
        if action == "list":
            items = list_change_history(limit=int(args.get("limit") or 20))
            if not items:
                return {"output": "No recent-change snapshots stored yet.", "items": [], "exit_code": 0}
            lines = ["Recent-change snapshot history:"]
            for item in items:
                summary = " ".join(item.get("summary") or [])[:180]
                lines.append(f"- `{item.get('id')}` ({item.get('generated_at')}): {summary}")
            return {"output": "\n".join(lines), "items": items, "exit_code": 0}
        if action == "read":
            snapshot_id = str(args.get("snapshot_id") or args.get("id") or "latest").strip()
            snapshot = read_change_snapshot(snapshot_id)
            if snapshot is None:
                return {"error": f"Recent-change snapshot '{snapshot_id}' not found.", "exit_code": 1}
            return {"output": render_patch_notes(snapshot), "snapshot": snapshot, "exit_code": 0}
        return {"error": "Use action collect, list, or read.", "exit_code": 1}
    except Exception as exc:
        logger.error("recent_changes failed: %s", exc)
        return {"error": str(exc), "exit_code": 1}


async def do_manage_repos(content: str, owner: Optional[str] = None) -> Dict:
    """Read registered repo metadata and read-only Git facts."""
    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action") or "list").strip().lower()
    try:
        from src.constants import BASE_DIR
        from src.repo_git_adapter import RepoGitAdapter
        from src.repo_registry import REPO_REGISTRY_FILE, RepoRegistry

        registry_path = os.environ.get("ODYSSEUS_REPO_REGISTRY_FILE") or REPO_REGISTRY_FILE
        registry = RepoRegistry.load_or_empty(registry_path)

        if action in {"register", "forget", "update_policy"}:
            return _repo_registry_mutation(
                action=action,
                args=args,
                owner=owner,
                registry=registry,
                registry_path=registry_path,
            )

        if action == "list":
            summary = registry.audit_summary()
            rows = summary.get("repos") or []
            if not rows:
                return {"output": "No repos registered yet.", "repos": [], "exit_code": 0}
            lines = ["Registered repos:"]
            for item in rows:
                lines.append(
                    "- `{repo_id}` {title} ({repo_kind}, {privacy}/{scope}; remotes={remotes}, push={push})".format(
                        repo_id=item.get("repo_id"),
                        title=item.get("title"),
                        repo_kind=item.get("repo_kind"),
                        privacy=item.get("privacy_class"),
                        scope=item.get("provider_scope"),
                        remotes=item.get("remote_count"),
                        push=item.get("push_remote_count"),
                    )
                )
            return {"output": "\n".join(lines), "repos": rows, "exit_code": 0}

        repo_id = str(args.get("repo_id") or args.get("id") or "").strip()
        if not repo_id:
            return {"error": "repo_id is required for this action", "exit_code": 1}

        if action == "get":
            record = registry.get(repo_id)
            return {
                "output": (
                    f"Repo `{record.repo_id}`: {record.title} "
                    f"({record.repo_kind}, {record.privacy_class}/{record.provider_scope})."
                ),
                "repo": record.to_dict(),
                "exit_code": 0,
            }

        workspace_base = _repo_workspace_base(default=BASE_DIR)
        adapter = RepoGitAdapter(
            registry=registry,
            workspace_base=workspace_base,
        )
        if action == "status":
            status = adapter.status(repo_id)
            return {"output": _repo_status_output(status.to_dict()), "status": status.to_dict(), "exit_code": 0}
        if action == "log":
            commits = [commit.to_dict() for commit in adapter.log(repo_id, limit=int(args.get("limit") or 10))]
            lines = [f"Recent commits for `{repo_id}`:"]
            lines.extend(f"- `{item['commit'][:8]}` {item['subject']} ({item['authored_at']})" for item in commits)
            return {"output": "\n".join(lines), "commits": commits, "exit_code": 0}
        if action == "diff_stat":
            diff_stat = adapter.diff_stat(repo_id)
            return {"output": diff_stat or f"No diff stat for `{repo_id}`.", "diff_stat": diff_stat, "exit_code": 0}
        if action == "changed_paths":
            paths = [item.to_dict() for item in adapter.changed_paths(repo_id)]
            lines = [f"Changed paths for `{repo_id}`:"]
            lines.extend(f"- {item['status']} {item['path']}" for item in paths)
            if not paths:
                lines.append("- none")
            return {"output": "\n".join(lines), "changed_paths": paths, "exit_code": 0}
        if action == "remotes":
            remotes = [remote.to_dict() for remote in adapter.remotes(repo_id)]
            lines = [f"Remotes for `{repo_id}`:"]
            lines.extend(f"- {item['name']} {item['url_redacted']} ({item['direction']})" for item in remotes)
            if not remotes:
                lines.append("- none")
            return {"output": "\n".join(lines), "remotes": remotes, "exit_code": 0}
        if action in {"commit_plan", "commit"}:
            from src.repo_commit_runner import plan_repo_local_commit, run_repo_local_commit

            common = {
                "registry": registry,
                "repo_id": repo_id,
                "workspace_base": workspace_base,
                "objective": args.get("objective") or args.get("summary") or f"Update {repo_id}",
                "changed_paths": _repo_changed_path_args(args),
                "checks_passed": args.get("checks_passed") is True,
                "content_reviewed": args.get("content_reviewed") is True,
                "confirmed": args.get("confirmed") is True,
                "commit_message": args.get("commit_message"),
            }
            report = (
                plan_repo_local_commit(**common)
                if action == "commit_plan"
                else run_repo_local_commit(**common)
            )
            payload = report.to_dict()
            return {
                "output": _repo_commit_output(payload),
                "commit_report": payload,
                "exit_code": 0 if report.status in {"plan_ready", "committed"} else 1,
            }
        if action in {"push_plan", "push"}:
            from src.repo_push_runner import plan_repo_push, run_repo_push

            common = {
                "registry": registry,
                "repo_id": repo_id,
                "workspace_base": workspace_base,
                "remote_name": args.get("remote_name") or args.get("remote") or "fuzzy",
                "branch_name": args.get("branch_name") or args.get("branch"),
                "commit_sha": args.get("commit_sha") or args.get("commit_ref"),
                "confirmed": args.get("confirmed") is True,
                "operator_go": args.get("operator_go") is True,
                "live_enabled": _repo_optional_bool(args, "live_enabled"),
            }
            report = plan_repo_push(**common) if action == "push_plan" else run_repo_push(**common)
            payload = report.to_dict()
            return {
                "output": _repo_push_output(payload),
                "push_report": payload,
                "exit_code": 0 if report.status in {"plan_ready", "pushed"} else 1,
            }
        if action in {"forge_plan", "forge_metadata"}:
            from src.repo_forge_provider import plan_repo_forge_metadata, run_repo_forge_metadata

            common = {
                "registry": registry,
                "repo_id": repo_id,
                "provider": args.get("provider") or args.get("remote_provider") or "github",
                "namespace": args.get("namespace") or args.get("remote_namespace"),
                "repo_name": args.get("repo_name"),
                "api_base_url": args.get("api_base_url") or "",
                "integration_id": args.get("integration_id") or "",
                "auth_ready": args.get("auth_ready") is True,
                "confirmed": args.get("confirmed") is True,
                "operator_go": args.get("operator_go") is True,
                "live_enabled": args.get("live_enabled") is True,
                "create_repo_requested": args.get("create_repo_requested") is True,
            }
            report = (
                plan_repo_forge_metadata(**common)
                if action == "forge_plan"
                else run_repo_forge_metadata(**common)
            )
            payload = report.to_dict()
            return {
                "output": _repo_forge_output(payload),
                "forge_report": payload,
                "exit_code": 0 if report.status in {"plan_ready", "fetched"} else 1,
            }
        if action in {"changes", "change_history"}:
            from src.repo_recent_memory import collect_repo_change_capsule, list_repo_change_history

            history_dir = os.environ.get("ODYSSEUS_REPO_CHANGES_HISTORY_DIR")
            if action == "change_history":
                rows = list_repo_change_history(
                    repo_id=repo_id,
                    history_dir=history_dir,
                    limit=int(args.get("limit") or 20),
                )
                lines = [f"Repo change history for `{repo_id}`:"]
                if rows:
                    lines.extend(
                        "- `{id}` {generated_at}: {counts}".format(
                            id=item.get("id"),
                            generated_at=item.get("generated_at"),
                            counts=item.get("counts") or {},
                        )
                        for item in rows
                    )
                else:
                    lines.append("- none")
                return {"output": "\n".join(lines), "history": rows, "exit_code": 0}
            report = collect_repo_change_capsule(
                registry=registry,
                repo_id=repo_id,
                workspace_base=workspace_base,
                hours=int(args.get("hours") or 12),
                history_dir=history_dir,
                persist=args.get("persist", True) is not False,
                force=args.get("force") is True,
            )
            payload = report.to_dict()
            return {
                "output": _repo_changes_output(payload),
                "repo_changes": payload,
                "exit_code": 0,
            }

        return {
            "error": (
                "Use action list, get, status, log, diff_stat, changed_paths, "
                "remotes, commit_plan, commit, push_plan, push, forge_plan, forge_metadata, changes, change_history, "
                "register, forget, or update_policy."
            ),
            "exit_code": 1,
        }
    except Exception as exc:
        logger.error("manage_repos failed: %s", exc)
        return {"error": str(exc), "exit_code": 1}


def _repo_changed_path_args(args: Dict[str, Any]) -> list[str]:
    values = args.get("changed_paths", args.get("paths", []))
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        raise ValueError("changed_paths must be a list of repo-relative paths")
    return [str(item) for item in values]


def _repo_optional_bool(args: Dict[str, Any], key: str) -> Optional[bool]:
    if key not in args:
        return None
    return args.get(key) is True


def _repo_registry_mutation(
    *,
    action: str,
    args: Dict[str, Any],
    owner: Optional[str],
    registry,
    registry_path: str,
) -> Dict:
    if args.get("confirmed") is not True:
        return {"error": f"{action} requires confirmed=true after explicit user confirmation.", "exit_code": 1}

    from src.repo_registry import RepoRecord

    now = _repo_now_iso()
    if action == "register":
        path_ref = str(args.get("path_ref") or args.get("project_root") or "").strip()
        project_root = str(args.get("project_root") or path_ref).strip()
        workspace_root = str(args.get("workspace_root") or _repo_default_workspace_root(project_root)).strip()
        outside_allowed = not _repo_path_is_in_allowed_roots(project_root or path_ref)
        if outside_allowed and args.get("operator_go") is not True:
            return {
                "error": (
                    "register outside allowed roots requires operator_go=true. "
                    f"Allowed roots: {', '.join(_repo_allowed_roots())}."
                ),
                "exit_code": 1,
            }
        record = RepoRecord.create(
            repo_id=args.get("repo_id") or args.get("id"),
            title=args.get("title") or args.get("repo_id") or args.get("id") or path_ref,
            repo_kind=args.get("repo_kind", "project"),
            owner=args.get("owner") or owner or "default",
            path_ref=path_ref,
            workspace_root=workspace_root,
            project_root=project_root,
            system_root=args.get("system_root", ""),
            default_branch=args.get("default_branch", "main"),
            current_branch=args.get("current_branch", ""),
            remotes=_repo_remote_records(args.get("remotes") or []),
            privacy_class=args.get("privacy_class", "private"),
            provider_scope=args.get("provider_scope"),
            allowed_actions=args.get("allowed_actions"),
            linked_project_slug=args.get("linked_project_slug", ""),
            created_at=args.get("created_at") or now,
            updated_at=now,
        )
        registry.add(record)
        registry.save_json(registry_path)
        return {
            "output": f"Registered repo `{record.repo_id}`. No files were touched.",
            "repo": record.to_dict(),
            "mutation": {
                "action": "register",
                "repo_id": record.repo_id,
                "outside_allowed_roots": outside_allowed,
                "files_touched": False,
            },
            "exit_code": 0,
        }

    repo_id = str(args.get("repo_id") or args.get("id") or "").strip()
    if not repo_id:
        return {"error": "repo_id is required for this action", "exit_code": 1}

    if action == "forget":
        removed = registry.forget(repo_id)
        registry.save_json(registry_path)
        if not removed:
            return {"error": f"unknown repo: {repo_id}", "exit_code": 1}
        return {
            "output": f"Forgot repo `{repo_id}` from the registry. No repo files were deleted.",
            "mutation": {"action": "forget", "repo_id": repo_id, "files_deleted": False},
            "exit_code": 0,
        }

    if action == "update_policy":
        record = registry.get(repo_id)
        remotes = _repo_remote_records(args["remotes"]) if "remotes" in args else None
        updated = record.with_policy(
            privacy_class=args.get("privacy_class"),
            provider_scope=args.get("provider_scope"),
            allowed_actions=args.get("allowed_actions"),
            remotes=remotes,
            updated_at=now,
        )
        registry.put(updated)
        registry.save_json(registry_path)
        return {
            "output": f"Updated repo policy for `{updated.repo_id}`.",
            "repo": updated.to_dict(),
            "mutation": {
                "action": "update_policy",
                "repo_id": updated.repo_id,
                "files_touched": False,
            },
            "exit_code": 0,
        }

    return {"error": f"Unsupported repo mutation action: {action}", "exit_code": 1}


def _repo_remote_records(values: Any) -> tuple:
    from src.repo_registry import RepoRemote

    if not isinstance(values, list):
        raise ValueError("remotes must be a list")
    records = []
    for item in values:
        if not isinstance(item, dict):
            raise ValueError("remote entries must be objects")
        records.append(
            RepoRemote.create(
                name=item.get("name"),
                url=item.get("url"),
                url_redacted=item.get("url_redacted"),
                purpose=item.get("purpose", "other"),
                push_policy=item.get("push_policy", "read_only"),
            )
        )
    return tuple(records)


def _repo_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _repo_default_workspace_root(project_root: str) -> str:
    normalized = str(project_root or "").strip().replace("\\", "/").strip("/")
    parts = [part for part in normalized.split("/") if part]
    if len(parts) >= 2 and parts[0] == "projects":
        return "/".join(parts[:2])
    return parts[0] if parts else "repos"


def _repo_allowed_roots() -> tuple[str, ...]:
    raw = os.environ.get("ODYSSEUS_REPO_ALLOWED_ROOTS") or "repos,projects"
    roots = []
    for item in raw.split(","):
        root = item.strip().replace("\\", "/").strip("/")
        if root and root not in roots:
            roots.append(root)
    return tuple(roots or ["repos", "projects"])


def _repo_path_is_in_allowed_roots(path_ref: str) -> bool:
    normalized = str(path_ref or "").strip().replace("\\", "/").strip("/")
    if not normalized:
        return False
    return any(normalized == root or normalized.startswith(f"{root}/") for root in _repo_allowed_roots())


def _repo_workspace_base(*, default: str) -> str:
    try:
        from src.tool_execution import get_active_workspace

        active = get_active_workspace()
    except Exception:
        active = None
    return str(active or os.environ.get("ODYSSEUS_REPO_WORKSPACE_BASE") or default)


def _skill_dump(sk) -> Dict:
    """Translate a parsed Skill back into the kwargs `update_skill` expects."""
    return {
        "name": sk.name,
        "description": sk.description,
        "version": sk.version,
        "category": sk.category,
        "tags": sk.tags,
        "platforms": sk.platforms,
        "requires_toolsets": sk.requires_toolsets,
        "fallback_for_toolsets": sk.fallback_for_toolsets,
        "status": sk.status,
        "confidence": sk.confidence,
        "source": sk.source,
        "teacher_model": sk.teacher_model,
        "owner": sk.owner,
        "when_to_use": sk.when_to_use,
        "procedure": sk.procedure,
        "pitfalls": sk.pitfalls,
        "verification": sk.verification,
        "body_extra": sk.body_extra,
    }


