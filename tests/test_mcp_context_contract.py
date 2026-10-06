import base64
import copy
import json

import pytest

from src.mcp_context_contract import (
    MCP_CONTEXT_PACK_SCHEMA,
    MCP_CONTEXT_SCOPE,
    McpContextContractError,
    McpContextCursorEnvelope,
    decode_context_cursor,
    encode_context_cursor,
    encode_context_jsonrpc_response,
    parse_mcp_context_request,
    validate_context_tool_arguments,
)


def _selection():
    return [{
        "source": "planning",
        "selection_ref": "selection.1",
        "revision_ref": "revision.1",
    }]


def _cursor():
    return McpContextCursorEnvelope(
        request_id="request.1",
        pack_id="pack.1",
        recipient="owner_" + "a" * 32,
        chat="chat.1",
        process_epoch="epoch_" + "b" * 32,
        selection="sha256:" + "1" * 64,
        snapshot="sha256:" + "2" * 64,
        kind="content",
        target_index=0,
        offset=0,
        page_bytes=16_384,
        expiry=1_800_000_000,
    )


def test_closed_tool_schemas_and_native_selection_detach():
    args = validate_context_tool_arguments(
        "harbour_context_prepare",
        {"chat_session_id": "chat.1", "selection": _selection(), "page_bytes": 128},
    )
    assert args["chat_session_id"] == "chat.1"
    assert type(args["selection"]).__name__ == "ContextSelection"
    with pytest.raises(McpContextContractError) as error:
        validate_context_tool_arguments(
            "harbour_context_cancel", {"request_id": "r.1", "unexpected": "__untrusted_input_marker__"}
        )
    assert error.value.code == "invalid_request"
    assert "__untrusted_input_marker__" not in repr(error.value)


def test_single_json_rpc_request_rejects_batches_notifications_duplicates_and_bool_ids():
    raw = json.dumps({
        "jsonrpc": "2.0",
        "id": "1",
        "method": "harbour_context_prepare",
        "params": {"chat_session_id": "chat.1", "selection": _selection()},
    }).encode()
    call = parse_mcp_context_request(raw)
    assert call.method == "harbour_context_prepare"
    assert "selection" not in repr(call)
    for bad in (
        json.dumps([json.loads(raw)]).encode(),
        b'{"jsonrpc":"2.0","method":"harbour_context_cancel","params":{"request_id":"r"}}',
        b'{"jsonrpc":"2.0","id":true,"method":"harbour_context_cancel","params":{"request_id":"r"}}',
        b'{"jsonrpc":"2.0","id":"r","method":"harbour_context_cancel","params":{"request_id":"r","request_id":"s"}}',
    ):
        with pytest.raises(McpContextContractError) as error:
            parse_mcp_context_request(bad)
        assert error.value.code == "invalid_request"


def test_cursor_is_closed_bounded_unpadded_and_not_authority():
    encoded = encode_context_cursor(_cursor())
    assert "=" not in encoded
    assert len(encoded) <= 2048
    decoded = decode_context_cursor(encoded)
    assert type(decoded) is McpContextCursorEnvelope
    assert decoded.chat_session_id == "chat.1"
    assert decoded.expires_at == 1_800_000_000
    assert MCP_CONTEXT_PACK_SCHEMA == decoded.schema
    assert MCP_CONTEXT_SCOPE == "mcp:harbour:context:read"
    for bad in (encoded + "A", "!", "eA", base64.urlsafe_b64encode(b"{}").decode().rstrip("=")):
        with pytest.raises(McpContextContractError) as error:
            decode_context_cursor(bad)
        assert error.value.code == "invalid_cursor"


def test_cursor_exact_types_and_budget_errors_are_fixed():
    with pytest.raises(McpContextContractError) as error:
        McpContextCursorEnvelope(
            request_id="r", pack_id="p", recipient="owner_" + "a" * 32, chat="c",
            process_epoch="epoch_" + "b" * 32, selection="sha256:" + "1" * 64,
            snapshot="sha256:" + "2" * 64, kind="content", target_index=True,
            offset=0, page_bytes=1, expiry=1,
        )
    assert error.value.code == "invalid_cursor"
    with pytest.raises(McpContextContractError) as error:
        parse_mcp_context_request(b"{" + b"x" * 262_144 + b"}")
    assert error.value.code == "budget_exceeded"


def test_response_and_tree_budgets_are_closed_at_boundaries():
    nested = value = {}
    for _ in range(16):
        child = {}; value["x"] = child; value = child
    encode_context_jsonrpc_response("r", nested)
    value["x"] = {}
    with pytest.raises(McpContextContractError) as error:
        encode_context_jsonrpc_response("r", nested)
    assert error.value.code == "budget_exceeded"
    # Derive the payload from the actual canonical envelope, including escaping.
    quote = chr(34)
    request_id = "r" + quote + "id"
    payload = {"text": quote}
    encoded = encode_context_jsonrpc_response(request_id, payload)
    payload["text"] += "a" * (65_536 - len(encoded))
    encoded = encode_context_jsonrpc_response(request_id, payload)
    assert len(encoded) == 65_536
    payload["text"] += "a"
    with pytest.raises(McpContextContractError) as error:
        encode_context_jsonrpc_response(request_id, payload)
    assert error.value.code == "budget_exceeded"


def test_direct_argument_budget_depth_and_nodes_boundaries():
    from src import mcp_context_contract as contract
    base = {"chat_session_id": "chat.1", "selection": _selection()}
    # The limit applies to the canonical serialized arguments, not a caller's estimate.
    base["chat_session_id"] = "c" * 256
    with pytest.raises(McpContextContractError) as error:
        validate_context_tool_arguments("harbour_context_prepare", {**base, "extra": "x"})
    assert error.value.code == "invalid_request"
    root = current = {}
    for _ in range(16): current["x"] = {}; current = current["x"]
    contract._check_json_tree(root)
    current["x"] = {}
    with pytest.raises(McpContextContractError) as error: contract._check_json_tree(root)
    assert error.value.code == "budget_exceeded"
    assert contract.MCP_CONTEXT_ARGUMENTS_MAX_BYTES == 32_768
    assert contract.MCP_CONTEXT_RESULT_MAX_BYTES == 65_536
    assert contract.MCP_CONTEXT_JSON_NODES_MAX == 4_096


def test_json_tree_exact_node_and_depth_boundaries_are_enforced():
    from src import mcp_context_contract as contract
    # A list root is one node, so 4095 leaves reaches the advertised 4096.
    contract._check_json_tree([None] * 4095)
    with pytest.raises(McpContextContractError) as error:
        contract._check_json_tree([None] * 4096)
    assert error.value.code == "budget_exceeded"
    # dict root + its key + list + 4093 leaves = 4096 nodes.
    result = {"v": [None] * 4093}
    encode_context_jsonrpc_response("r", result)
    result["v"].append(None)
    with pytest.raises(McpContextContractError) as error:
        encode_context_jsonrpc_response("r", result)
    assert error.value.code == "budget_exceeded"
    # The cancel envelope has thirteen fixed nodes; tree budget wins over its
    # closed-shape error when the untrusted extra list takes it over the limit.
    for leaves, code in ((4083, "invalid_request"), (4084, "budget_exceeded")):
        raw = json.dumps({"jsonrpc": "2.0", "id": "r", "method": "harbour_context_cancel",
                          "params": {"request_id": "r", "extra": [None] * leaves}}, separators=(",", ":")).encode()
        with pytest.raises(McpContextContractError) as error:
            parse_mcp_context_request(raw)
        assert error.value.code == code


def test_prepare_arguments_exact_canonical_32768_byte_boundary():
    from src import mcp_context_contract as contract
    rows = [{"source": "planning", "selection_ref": f"s{i:04d}", "revision_ref": "r"} for i in range(128)]
    args = {"chat_session_id": "chat.1", "selection": rows}
    compact = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
    need = 32_768 - len(compact(args))
    for row in rows:
        amount = min(need, 255)
        row["revision_ref"] += "a" * amount
        need -= amount
    assert need == 0 and len(compact(args)) == 32_768
    assert type(validate_context_tool_arguments("harbour_context_prepare", args)["selection"]).__name__ == "ContextSelection"
    too_large = copy.deepcopy(args)
    for row in reversed(too_large["selection"]):
        if len(row["revision_ref"]) < 256:
            row["revision_ref"] += "a"
            break
    assert len(compact(too_large)) == 32_769
    with pytest.raises(McpContextContractError) as error:
        validate_context_tool_arguments("harbour_context_prepare", too_large)
    assert error.value.code == "budget_exceeded"
    value = current = {}
    for _ in range(16):
        child = {}; current["x"] = child; current = child
    contract._check_json_tree(value)
    current["x"] = {}
    with pytest.raises(McpContextContractError) as error:
        contract._check_json_tree(value)
    assert error.value.code == "budget_exceeded"


def test_cursor_deep_json_and_postconstruction_mutation_are_closed():
    cursor = _cursor()
    object.__setattr__(cursor, "kind", "bad kind")
    with pytest.raises(McpContextContractError) as error:
        encode_context_cursor(cursor)
    assert error.value.code == "invalid_cursor"
    deep = "[" * 750 + "0" + "]" * 750
    encoded = base64.urlsafe_b64encode(deep.encode()).decode().rstrip("=")
    assert len(encoded) <= 2048
    with pytest.raises(McpContextContractError) as error:
        decode_context_cursor(encoded)
    assert error.value.code == "invalid_cursor"


def test_response_serializes_a_detached_builtin_snapshot(monkeypatch):
    from src import mcp_context_contract as contract
    original = contract._json_bytes
    payload = {"items": ["safe"]}
    calls = []
    def capture(value, **kwargs):
        serialized = original(value, **kwargs)
        calls.append(value)
        if len(calls) == 1:
            payload["items"].append("caller mutation")
        return serialized
    monkeypatch.setattr(contract, "_json_bytes", capture)
    encoded = contract.encode_context_jsonrpc_response("r", payload)
    decoded = json.loads(encoded)
    assert decoded["result"] == {"items": ["safe"]}


def test_cursor_duplicate_nonfinite_and_malformed_fields_are_invalid_cursor():
    import math
    encoded = encode_context_cursor(_cursor())
    payload = json.loads(base64.urlsafe_b64decode(encoded + "=="))
    payload["expiry"] = math.inf
    malformed = base64.urlsafe_b64encode(json.dumps(payload, allow_nan=True).encode()).decode().rstrip("=")
    for value in (malformed, base64.urlsafe_b64encode(b'{"schema":"x","schema":"y"}').decode().rstrip("=")):
        with pytest.raises(McpContextContractError) as error: decode_context_cursor(value)
        assert error.value.code == "invalid_cursor"


@pytest.mark.parametrize("field,value", [
    ("request_id", "bad space"), ("pack_id", ""), ("recipient", "bad space"),
    ("chat", ""), ("process_epoch", "bad space"),
])
def test_cursor_identifier_fields_always_map_to_invalid_cursor(field, value):
    values = dict(_cursor().__dict__) if hasattr(_cursor(), "__dict__") else {
        name: getattr(_cursor(), name) for name in ("request_id", "pack_id", "recipient", "chat", "process_epoch", "selection", "snapshot", "kind", "target_index", "offset", "page_bytes", "expiry", "schema")
    }
    values[field] = value
    with pytest.raises(McpContextContractError) as error: McpContextCursorEnvelope(**values)
    assert error.value.code == "invalid_cursor"
    encoded = base64.urlsafe_b64encode(json.dumps(values, separators=(",", ":")).encode()).decode().rstrip("=")
    with pytest.raises(McpContextContractError) as error: decode_context_cursor(encoded)
    assert error.value.code == "invalid_cursor"

