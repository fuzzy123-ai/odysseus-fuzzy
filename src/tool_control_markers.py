"""Pure UI-control marker handlers for agent tools."""

import json
from typing import Any


def handle_ask_user_marker(content: str) -> tuple[str, dict[str, Any]]:
    """Return the ask-user marker payload for the agent loop/frontend bridge."""
    question, options, multi = "", [], False
    raw = (content or "").strip()
    try:
        parsed = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        parsed = {}
    if isinstance(parsed, dict):
        question = str(parsed.get("question", "")).strip()
        multi = bool(parsed.get("multi") or parsed.get("multiSelect"))
        for opt in parsed.get("options") or []:
            if isinstance(opt, dict):
                label = str(opt.get("label", "")).strip()
                descr = str(opt.get("description", "")).strip()
            elif isinstance(opt, str):
                label, descr = opt.strip(), ""
            else:
                continue
            if label:
                options.append({"label": label, "description": descr})
    else:
        question = raw
    if not question or len(options) < 2:
        return "ask_user: invalid", {
            "error": (
                "ask_user needs a non-empty `question` and at least 2 `options` "
                "(each an object with a `label`, optional `description`)."
            ),
            "exit_code": 1,
        }
    options = options[:6]
    desc = f"ask_user: {question[:80]}"
    labels = ", ".join(o["label"] for o in options)
    return desc, {
        "ask_user": {"question": question, "options": options, "multi": multi},
        "output": f"Asked the user: {question}\nOptions: {labels}\nAwaiting their selection.",
        "exit_code": 0,
    }


def handle_update_plan_marker(content: str) -> tuple[str, dict[str, Any]]:
    """Return the plan-update marker payload for the agent loop/frontend bridge."""
    raw = (content or "").strip()
    plan = ""
    try:
        parsed = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        parsed = {}
    if isinstance(parsed, dict) and parsed.get("plan"):
        plan = str(parsed.get("plan", "")).strip()
    else:
        plan = raw
    if not plan:
        return "update_plan: invalid", {
            "error": "update_plan needs a non-empty `plan` (the full updated checklist as markdown).",
            "exit_code": 1,
        }
    plan = plan[:8192]
    done = plan.count("- [x]") + plan.count("- [X]")
    total = done + plan.count("- [ ]")
    desc = f"update_plan: {done}/{total} done" if total else "update_plan"
    output = f"Plan updated ({done}/{total} steps complete)." if total else "Plan updated."
    return desc, {
        "plan_update": {"plan": plan},
        "output": output,
        "exit_code": 0,
    }
