# Security Finding Closure - 2026-07-02

Scope: functional backend roadmap security closure. This file records durable
status only; it intentionally omits scanner raw output, private runtime paths,
tokens, chat IDs, and private content.

## Findings

| ID | Severity | Area | Status | Evidence |
| --- | --- | --- | --- | --- |
| SEC1 | high | API-token chat/session scope gates | fixed | `require_api_token_scope()` and `scoped_effective_user()` centralize scope checks. Chat and session route owner resolution now requires `chat` scope for bearer-token callers while browser sessions stay unchanged. |
| SEC2 | medium | Built-in vault MCP write boundary | fixed | Public/non-admin MCP gate now allows only explicit read-only vault MCP tools. Mutating write/delete/batch/undo/rebuild tools are blocked before dispatch. |
| SEC3 | medium | Token-supplied direct `base_url` SSRF | fixed | Direct API-token `base_url` remains public-URL validated and is disabled by default unless `ODYSSEUS_API_TOKEN_DIRECT_BASE_URL_ENABLED=true` is set server-side. Admin-created/stored endpoints remain the local/LAN provider path. |

## Regression Evidence

Focused bundle:

```powershell
C:\Users\nkatz\odysseus\venv\Scripts\python.exe -m pytest tests\test_api_chat_security.py tests\test_api_token_scope_gates.py tests\test_public_blocked_tool_nonstring.py tests\test_vault_mcp_chat_bridge.py tests\test_vault_mcp_security.py
```

Result: `38 passed`.

## Remaining Gates

- Direct token-supplied `base_url` can be re-enabled only by explicit
  server-side opt-in. Full connect-time IP pinning remains deferred until the
  feature is genuinely needed.
- Non-admin vault writes remain blocked. A future `vault:write` permission can
  be designed later as a separate product/security decision.
