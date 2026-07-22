"""Shared clarification contract constants."""

from __future__ import annotations


CLARIFICATION_REQUEST_SCHEMA = "odysseus.clarification_request.v2"
CLARIFICATION_RUN_SCHEMA = "odysseus.clarification_run.v1"
CLARIFICATION_EVENT_SCHEMA = "odysseus.clarification_event.v1"
CLARIFICATION_POLICY_REVIEW_SCHEMA = "odysseus.clarification_policy.review.v1"

QUESTION_TYPES = frozenset(
    {
        "single_select",
        "multi_select",
        "boolean",
        "short_text",
        "long_text",
        "number",
        "date",
        "resource_ref",
    }
)

MATERIAL_DIMENSIONS = (
    {
        "key": "outcome",
        "label": "Outcome",
        "required": True,
        "reason": "The desired result changes the plan and acceptance criteria.",
    },
    {
        "key": "target_users",
        "label": "Target users",
        "required": True,
        "reason": "The user group changes UX, permissions and evidence.",
    },
    {
        "key": "scope",
        "label": "Scope",
        "required": True,
        "reason": "In-scope and out-of-scope behavior changes implementation boundaries.",
    },
    {
        "key": "data_privacy",
        "label": "Data and privacy",
        "required": True,
        "reason": "Data boundaries change tool, model, storage and deployment choices.",
    },
    {
        "key": "acceptance_criteria",
        "label": "Acceptance criteria",
        "required": True,
        "reason": "Completion cannot be verified without evidence criteria.",
    },
    {
        "key": "runtime_constraints",
        "label": "Runtime constraints",
        "required": False,
        "reason": "Runtime, platform and integration constraints can materially change the design.",
    },
    {
        "key": "design_direction",
        "label": "Design direction",
        "required": False,
        "reason": "User-visible UI work needs an explicit design direction or visible default.",
    },
    {
        "key": "live_permissions",
        "label": "Live permissions",
        "required": False,
        "reason": "Network, write, deploy and publish actions require explicit permission gates.",
    },
)

MATERIAL_DIMENSION_KEYS = frozenset(item["key"] for item in MATERIAL_DIMENSIONS)
REQUIRED_MATERIAL_DIMENSION_KEYS = frozenset(item["key"] for item in MATERIAL_DIMENSIONS if item["required"])
