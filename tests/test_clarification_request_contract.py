import json
from pathlib import Path


def test_clarification_request_v2_schema_and_contract_exist():
    schema = json.loads(Path("specs/clarification_request.v2.schema.json").read_text(encoding="utf-8"))
    contract = Path("docs/plans/odysseus-clarification-request-v2-contract.md").read_text(encoding="utf-8")

    assert schema["$id"] == "odysseus.clarification_request.v2"
    assert schema["properties"]["scope"]["enum"] == ["conversation", "project", "coding_task"]
    assert "Plan-Unlock Invariant" in contract
    assert "Legacy Normalization" in contract
    assert "Memory Boundary" in contract


def test_clarification_request_v2_example_matches_schema_shape():
    schema = json.loads(Path("specs/clarification_request.v2.schema.json").read_text(encoding="utf-8"))
    example = {
        "schema": "odysseus.clarification_request.v2",
        "scope": "project",
        "intent_summary": "Build a local document-review workflow.",
        "questions": [
            {
                "key": "target_documents",
                "type": "short_text",
                "prompt": "Which document set should be reviewed first?",
                "required": True,
                "reason": "The document source changes scope and privacy boundaries.",
                "category": "scope"
            }
        ],
        "batch": {
            "label": "Scope",
            "index": 1,
            "total": 1,
            "max_visible_questions": 5
        },
        "defaults_visible": False
    }

    assert set(schema["required"]) <= set(example)
    assert example["questions"][0]["type"] in schema["properties"]["questions"]["items"]["properties"]["type"]["enum"]
    assert example["scope"] in schema["properties"]["scope"]["enum"]
