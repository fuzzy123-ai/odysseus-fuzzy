# Context source and pack API

This is the developer reference for the accepted backend context-source router at `/api/context`. It describes the concrete implementation at backend commit `8eb28b7fe81457b8ba889c91cee4f98b6aa78cef` (`HEAD` when this reference was prepared). The backend router is wired by `install_context_sources` when the concrete Forge, Planning, and Knowledge prerequisites are present; construction sets `context_source_status.runtime_verified` to `false` and does not claim runtime or release validation.

## Transport and authorization

All operations are `POST` requests with a JSON body, and use the real session cookie named by `routes.auth_routes.SESSION_COOKIE`. The request must resolve through `strict_session_principal`, the application must use the bound `AuthManager`, and the principal must be an admin. A missing or invalid cookie is unauthenticated; a non-admin is unauthorized.

The transport is deliberately narrow. The request must have no query parameters, must not be an API-token request, and must not contain any of these headers: `authorization`, `forwarded`, `x-forwarded-for`, `x-forwarded-host`, `x-real-ip`, `cf-connecting-ip`, `cf-ray`, `cf-visitor`, `x-odysseus-internal-token`, `x-odysseus-owner`, `x-internal-token`, or `x-tool-token`. `Origin` is required and must equal the request's own `scheme://netloc` (with a trailing slash ignored). If `Sec-Fetch-Site` is present it must be `same-origin`. These checks are in addition to real cookie, admin, chat-ownership, authority, and security-revision checks; an object in the JSON body cannot supply or replace an authority.

The body is parsed as a bounded JSON body (`MCP_BODY_MAX_BYTES`) and every request object is closed: unknown keys are rejected. Responses use `Cache-Control: no-store`.

## Shared request values

`chat_session_id` is a string. `selection` is a list of 1 to 300 objects. Each selection object is exactly:

```json
{
  "source": "forge | planning | knowledge",
  "selection_ref": "string",
  "revision_ref": "string",
  "forge_locator": {
    "repo_id": "string",
    "version_id": "string",
    "path": "string"
  }
}
```

At the wire parser, `forge_locator` is optional, but the concrete `SourceSelection` type permits it only when `source` is `forge`; a non-Forge selection carrying a locator is invalid. In practical concrete reads, a Forge selection must carry a `ForgeSelectionLocator` with `repo_id`, `version_id`, and `path`, because the Forge manifest/page reader requires it. Planning and Knowledge selections omit the locator. The accepted source tuple for mapping is `(source, selection_ref, revision_ref)`; clients must retain the request selection order. Integer fields are JSON integers (booleans are not accepted), and bounded offsets use the implementation's safe integer ceiling of `2^53 - 1`.

## Endpoints

### `POST /api/context/challenge`

Request schema (exact closed object):

```json
{
  "chat_session_id": "string",
  "selection": ["Selection object", "..."
  ]
}
```

The response is an exact object with three fields:

```json
{
  "challenge_token": "server-issued token",
  "command": "/context-disclose <challenge_token>",
  "expires_at": "ISO-8601 timestamp"
}
```

This only issues the server challenge. The `command` is shown to the human for acknowledgment through the chat command flow. The chat acknowledgment yields a persisted `disclosure_message_id`; the client passes that positive integer to the subsequent private read. There is no client approval boolean and no additional acknowledgment endpoint described by this backend contract.

### `POST /api/context/metadata`

The request must contain exactly `chat_session_id` and one of the following mutually exclusive fields:

```json
{
  "chat_session_id": "string",
  "selection": ["Selection object", "..."]
}
```

or:

```json
{
  "chat_session_id": "string",
  "forge_locators": [
    {"repo_id": "string", "version_id": "string", "path": "string"}
  ]
}
```

`forge_locators` contains 1 to 300 exact locator objects. The response is:

```json
{
  "selection": ["Selection object", "..."],
  "items": ["canonical ManifestItem", "..."],
  "targets": [
    {"target_index": 0, "manifest_digest": "string"}
  ],
  "requires_private_confirmation": true,
  "manifest_digest": "sha256:... or null"
}
```

`targets` follows request selection order and supplies the per-target manifest digest used by `/page`. `manifest_digest` is populated only when a selected item is private. Metadata does not disclose content bytes.

### `POST /api/context/page`

Request schema (exact closed object):

```json
{
  "chat_session_id": "string",
  "selection": ["Selection object", "..."],
  "target_index": 0,
  "expected_manifest_digest": "string",
  "offset": 0,
  "limit": 1048576,
  "disclosure_message_id": 123
}
```

`disclosure_message_id` is optional in the wire shape, but a positive integer is required whenever the selected manifest contains private content; Planning reads also require it. `target_index` is within the selection list. `offset` is non-negative. `limit` is 1 through 1,000,000 for Forge and 1 through 1,048,576 for Planning or Knowledge. The server reads one bounded page from the one concrete target selected by `target_index`.

The response body is the raw page bytes (`application/octet-stream`). The response headers are:

| Header | Meaning |
| --- | --- |
| `X-Context-Total` | Total bytes in the target payload. |
| `X-Context-Offset` | Returned page start; equals requested `offset`. |
| `X-Context-End` | Exclusive returned page end. |
| `X-Context-Whole-SHA256` | 64 lowercase hex SHA-256 of the complete raw payload. |
| `X-Context-Page-SHA256` | 64 lowercase hex SHA-256 of these raw page bytes. |
| `X-Context-Manifest-SHA256` | Selected target manifest digest, without the `sha256:` prefix. |
| `X-Context-Byteform` | `git_blob`, `durable_revision_document`, or `knowledge_payload`. |

### `POST /api/context/pack`

Request schema (exact closed object):

```json
{
  "chat_session_id": "string",
  "selection": ["Selection object", "..."],
  "page_bytes": 1000000,
  "cursor": "opaque cursor or null",
  "disclosure_message_id": 123
}
```

`page_bytes` is 1 through 1,000,000. `cursor` is required in the object and is either `null` for the first page or an opaque string returned by the preceding response. `disclosure_message_id` is optional in the shape and required for a private selection after the human acknowledgment flow.

The pack response has exactly nine top-level fields:

```json
{
  "schema": "odysseus.context_pack.v1",
  "pack_id": "sha256:...",
  "items": ["canonical ManifestItem", "..."],
  "page": {
    "target_index": 0,
    "offset": 0,
    "end": 10,
    "total": 10,
    "whole_sha256": "sha256:...",
    "page_sha256": "sha256:...",
    "byteform": "git_blob | durable_revision_document | knowledge_payload",
    "content_b64": "padded standard base64"
  },
  "next_cursor": "opaque cursor or empty string",
  "complete": false,
  "read_only": true,
  "authority_granted": false,
  "send_performed": false
}
```

The nested `page` object has exactly eight fields. `content_b64` is standard padded Base64 of the raw page bytes (`base64.b64encode` semantics), not URL-safe Base64. The `whole_sha256` and `page_sha256` values are `sha256:` followed by 64 lowercase hex characters; hashes are over raw bytes, before Base64 encoding. The page invariant is `end - offset == decoded content_b64 byte length`, and the page hash is the hash of those decoded bytes. An empty selected document produces a valid zero-length page with `offset == end == total == 0` and the SHA-256 of empty bytes; it is an EOF page and is not eagerly skipped. EOF is `end == total`. At EOF the assembler advances once to the next selection target; `complete` becomes true only after the final target reaches EOF, and `next_cursor` is the empty string. Otherwise `next_cursor` is required and resumes at the returned `end` offset (or offset zero for the next target).

## Ordering, identity, and cursors

The client selection order is the traversal order. `target_index`, `targets`, target manifest digests, and cursor `index`/`offset` all use that order. The canonical `items` array in metadata and pack responses is sorted by the compact JSON representation of each `ManifestItem`; it is not the selection order. The triple `(source, selection_ref, revision_ref)` maps a selected target to exactly one canonical item. For `/page`, the target manifest digest is checked against the client-provided `expected_manifest_digest` before reading. `/pack` has no expected-manifest request field: the assembler recaptures a native snapshot, computes the pack identity, and supplies the native per-target digest internally to the concrete page reader. It also recaptures before and after each page and rejects a changed snapshot.

`pack_id` is a SHA-256 identity over the schema, owner/chat/auth/security binding, selected triples, canonical snapshot items, target manifest digests, and disclosure id. A continuation cursor carries that pack identity plus owner, chat, auth binding, security revision, selection digest, disclosure id, target index, and offset. It is a signed, canonical JSON payload encoded as unpadded URL-safe Base64, limited to 16,384 characters. The cursor is opaque: clients must copy it exactly and must not parse or synthesize it. Its HMAC key is generated when the assembler is constructed, so a process restart invalidates outstanding cursors. Binding, security, selection, disclosure, pack, or offset mismatches return `invalid_cursor` internally and are projected at the HTTP boundary as `context_unavailable`.

## Fixed errors and side effects

The HTTP boundary exposes only these error codes: `invalid_request`, `body_too_large`, `not_authenticated`, `not_authorized`, `invalid_transport`, `invalid_origin`, `context_unavailable`, and `context_changed`. Malformed/unknown-key bodies are 400; oversized request bodies are 413; authentication failures are 401; transport, origin, and admin failures are 403; stale or unavailable context is normally 409. The boundary emits only `{"error":{"code":"..."}}` with no provider or exception text.

The API is read-only. It performs no send, provider write, authority grant, or external side effect. The pack flags are explicit: `read_only: true`, `authority_granted: false`, and `send_performed: false`. The backend binds existing providers and a forked cookie authority in the application; callers cannot submit provider, authority, or session objects.

## Verification limits

This reference is grounded in the accepted local backend component at `HEAD 8eb28b7fe81457b8ba889c91cee4f98b6aa78cef`, with source pins:

| Source | SHA-256 pin |
| --- | --- |
| `src/context_pack_assembler.py` | `76c4bd8c7349ab71e03a5b551712336036a3c1f4c853261eafda2413ef7fdd39` |
| `routes/context_source_routes.py` | `d44eeae184b6d25f9dcbfb236ba32b0d08a7e9c351cc4a142d954c91e1ae7356` |
| `src/context_manifest_producer.py` | `56300b8bac4bf0cf7b93c6c02705f173ba1a692e605e7b5e9cb6e8a77e7be828` |
| `src/context_source_app.py` | `c206df5d8c926e69a5a55dd00d74df01b5d0dd8f45af3e2d80fb49158ce40b41` |

The bounded offline actual-app three-source proof is accepted at commit `c82fb9a0ebd7cd2f1144b78ae0f44615f8e42447`: `tests/test_app_context_pack.py` recorded `1 PASS`, `0` failures, `0` errors, and `0` skips in r12, with receipt `.abc-test-tmp/h1r07-app-client-next-20261005/app-r12/receipt.json` (SHA-256 `4e5c1b8712bc0f532bfc53bc2bdb96ec5aa8a8d05f7104a3de47c2916e4cca2c`). The private test recorded `1 PASS` and was explicitly reused after an independent full 3811-file map comparison confirmed unchanged private inputs and the sole App-proof module delta; no fresh private rerun was performed. The final declared representation matches all 3811 files (the HEAD65 archive plus 15 declared raw Git overlays) and was independently verified. Whole-parent/product acceptance, browser execution/UI activation, live behavior, temporal acceptance, and production/default-archive parity remain unverified; default archive raw parity is FAIL and no runtime was executed at final HEAD. The browser `packPage` implementation is locally accepted by 31 Node checks, but it remains outside this HTTP reference and actual browser execution/UI activation remain pending.
