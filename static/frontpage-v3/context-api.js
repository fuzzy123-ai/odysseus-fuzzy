(function (root, factory) {
  const api = factory(root || globalThis);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.HarborContextApi = api;
})(typeof window !== 'undefined' ? window : globalThis, function (host) {
  'use strict';

  const MAX_BODY_BYTES = 256 * 1024;
  const MAX_SELECTIONS = 300;
  const MAX_JSON_INT = Number.MAX_SAFE_INTEGER;
  const MAX_FORGE_PAGE = 1_000_000;
  const MAX_OTHER_PAGE = 1_048_576;
  const MAX_PACK_PAGE = 1_000_000;
  const MAX_PACK_CURSOR_BYTES = 16_384;
  const PACK_CURSOR = /^[A-Za-z0-9_-]+$/;
  const PACK_SCHEMA = 'odysseus.context_pack.v1';
  const PACK_PAGE_B64_BYTES = 4 * Math.ceil(MAX_PACK_PAGE / 3);
  const PACK_ITEM_TEMPLATE = {
    boundary_sha256: 'a'.repeat(64), classification: 'private', payload_sha256: 'a'.repeat(64),
    provenance_ref: 'x'.repeat(256), record_sha256: 'a'.repeat(64), revision_ref: 'x'.repeat(256),
    selection_ref: 'x'.repeat(256), source: 'knowledge',
  };
  // All legal opaque values are printable ASCII without JSON escape characters. This
  // template therefore proves the maximum compact UTF-8 envelope size, including
  // 300 legal canonical items, a full cursor, and a maximum base64 page.
  const PACK_ENVELOPE_MARGIN_BYTES = 4096;
  const MAX_PACK_JSON_BYTES = JSON.stringify({
    schema: PACK_SCHEMA, pack_id: `sha256:${'a'.repeat(64)}`,
    items: Array.from({ length: MAX_SELECTIONS }, () => PACK_ITEM_TEMPLATE),
    page: {
      target_index: MAX_JSON_INT, offset: MAX_JSON_INT, end: MAX_JSON_INT, total: MAX_JSON_INT,
      whole_sha256: `sha256:${'a'.repeat(64)}`, page_sha256: `sha256:${'a'.repeat(64)}`,
      byteform: 'knowledge_payload', content_b64: 'A'.repeat(PACK_PAGE_B64_BYTES),
    },
    next_cursor: 'A'.repeat(MAX_PACK_CURSOR_BYTES), complete: false,
    read_only: true, authority_granted: false, send_performed: false,
  }).length + PACK_ENVELOPE_MARGIN_BYTES;
  const REQUEST_TIMEOUT_MS = 8_000;
  const OPAQUE = /^[A-Za-z0-9][A-Za-z0-9_.:\/+@~\-]{0,255}$/;
  const DIGEST = /^(?:sha256:)?[0-9a-f]{64}$/;
  const RAW_DIGEST = /^[0-9a-f]{64}$/;
  const TOKEN = /^pc1\.[A-Za-z0-9_-]{43}\.[0-9a-f]{64}$/;
  const PATH = /^[A-Za-z0-9._/@+ -]{1,240}$/;
  const REPO_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;
  const VERSION_ID = /^pv_[0-9a-f]{32}$/;
  const ERROR_CODES = new Set([
    'invalid_request', 'invalid_response', 'request_failed', 'request_timeout',
    'request_aborted', 'response_too_large', 'stream_unavailable',
    'content_hash_mismatch', 'whole_hash_mismatch', 'native_digest_mismatch',
    'fetch_unavailable', 'crypto_unavailable',
  ]);

  class ContextApiError extends Error {
    constructor(code, status) {
      const safe = typeof code === 'string' && ERROR_CODES.has(code) ? code : 'request_failed';
      super(safe);
      this.name = 'ContextApiError';
      this.code = safe;
      this.status = Number.isSafeInteger(status) && status >= 0 ? status : 0;
    }

    toJSON() { return { code: this.code, status: this.status }; }
  }

  function fail(code, status) { throw new ContextApiError(code, status); }

  function isPlainObject(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const proto = Object.getPrototypeOf(value);
    return proto === Object.prototype || proto === null;
  }

  function cloneFreeze(value, seen) {
    if (value === null || typeof value !== 'object') return value;
    const prior = seen.get(value);
    if (prior) fail('invalid_request');
    seen.set(value, true);
    let copy;
    if (Array.isArray(value)) copy = value.map((item) => cloneFreeze(item, seen));
    else if (isPlainObject(value)) {
      copy = {};
      for (const key of Object.keys(value)) copy[key] = cloneFreeze(value[key], seen);
    } else fail('invalid_request');
    seen.delete(value);
    return Object.freeze(copy);
  }

  function asciiText(value, maximum) {
    if (typeof value !== 'string' || value.length < 1 || value.length > maximum) fail('invalid_request');
    for (let index = 0; index < value.length; index += 1) {
      const code = value.charCodeAt(index);
      if (code < 0x21 || code > 0x7e) fail('invalid_request');
    }
    return value;
  }

  function opaque(value) {
    if (typeof value !== 'string' || !OPAQUE.test(value)) fail('invalid_request');
    return value;
  }

  function rawDigest(value, allowPrefix) {
    if (typeof value !== 'string' || !DIGEST.test(value)) fail('invalid_response');
    if (!allowPrefix && !RAW_DIGEST.test(value)) fail('invalid_response');
    return value;
  }

  function canonicalPackDigest(value, errorCode) {
    if (typeof value !== 'string' || !/^sha256:[0-9a-f]{64}$/.test(value)) fail(errorCode);
    return value;
  }

  function normalizedDigest(value) {
    rawDigest(value, true);
    return value.startsWith('sha256:') ? value.slice(7) : value;
  }

  function forgeLocator(value) {
    if (!isPlainObject(value) || Object.keys(value).sort().join(',') !== 'path,repo_id,version_id') fail('invalid_request');
    if (typeof value.repo_id !== 'string' || !REPO_ID.test(value.repo_id)) fail('invalid_request');
    if (typeof value.version_id !== 'string' || !VERSION_ID.test(value.version_id)) fail('invalid_request');
    if (typeof value.path !== 'string' || !PATH.test(value.path) || value.path.startsWith('/') || value.path.startsWith('~') || value.path.includes('\\')) fail('invalid_request');
    const parts = value.path.split('/');
    const blocked = new Set(['.git', '.env', '.ssh', 'id_rsa', 'id_dsa', 'id_ed25519']);
    if (parts.some((part) => !part || part === '.' || part === '..' || blocked.has(part.toLowerCase()))) fail('invalid_request');
    return Object.freeze({ repo_id: value.repo_id, version_id: value.version_id, path: value.path });
  }

  function selection(value) {
    if (!Array.isArray(value) || value.length < 1 || value.length > MAX_SELECTIONS) fail('invalid_request');
    const items = value.map((entry) => {
      if (!isPlainObject(entry)) fail('invalid_request');
      const keys = Object.keys(entry).sort();
      const allowed = entry.source === 'forge' ? 'forge_locator,revision_ref,selection_ref,source' : 'revision_ref,selection_ref,source';
      if (keys.join(',') !== allowed) fail('invalid_request');
      if (!['forge', 'planning', 'knowledge'].includes(entry.source)) fail('invalid_request');
      const result = {
        source: entry.source,
        selection_ref: opaque(entry.selection_ref),
        revision_ref: opaque(entry.revision_ref),
      };
      if (entry.source === 'forge') result.forge_locator = forgeLocator(entry.forge_locator);
      return Object.freeze(result);
    });
    const keys = items.map((item) => `${item.source}\u0000${item.selection_ref}\u0000${item.revision_ref}`);
    const sorted = keys.slice().sort();
    if (keys.some((key, index) => key !== sorted[index]) || new Set(items.map((item) => item.selection_ref)).size !== items.length) fail('invalid_request');
    return Object.freeze(items);
  }

  function bodyBytes(value) {
    let encoded;
    try { encoded = JSON.stringify(value); } catch (_) { fail('invalid_request'); }
    if (typeof encoded !== 'string') fail('invalid_request');
    const bytes = new TextEncoder().encode(encoded);
    if (bytes.byteLength > MAX_BODY_BYTES) fail('response_too_large');
    return { encoded, bytes };
  }

  function exactObject(value, keys) {
    if (!isPlainObject(value) || Object.keys(value).sort().join(',') !== keys.slice().sort().join(',')) fail('invalid_response');
    return value;
  }

  function header(response, name) {
    const value = response && response.headers && typeof response.headers.get === 'function'
      ? response.headers.get(name) : null;
    return typeof value === 'string' ? value : '';
  }

  function safeInteger(value, minimum, maximum) {
    if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail('invalid_response');
    return value;
  }

  function requestInteger(value, minimum, maximum) {
    if (!Number.isSafeInteger(value) || value < minimum || value > maximum) fail('invalid_request');
    return value;
  }

  function validateMetadata(payload, submittedSelection, bootstrapLocators) {
    exactObject(payload, ['items', 'manifest_digest', 'requires_private_confirmation', 'selection', 'targets']);
    const returnedSelection = selection(payload.selection);
    if (submittedSelection && JSON.stringify(returnedSelection) !== JSON.stringify(submittedSelection)) fail('invalid_response');
    if (bootstrapLocators) {
      if (returnedSelection.some((item) => item.source !== 'forge')) fail('invalid_response');
      const expected = bootstrapLocators.map((item) => `${item.repo_id}\u0000${item.version_id}\u0000${item.path}`).sort();
      const actual = returnedSelection.map((item) => `${item.forge_locator.repo_id}\u0000${item.forge_locator.version_id}\u0000${item.forge_locator.path}`).sort();
      if (expected.length !== actual.length || expected.some((value, index) => value !== actual[index])) fail('invalid_response');
    }
    if (!Array.isArray(payload.items) || payload.items.length !== returnedSelection.length) fail('invalid_response');
    const triples = new Set(returnedSelection.map((item) => `${item.source}\u0000${item.selection_ref}\u0000${item.revision_ref}`));
    const itemTriples = new Set();
    for (const item of payload.items) {
      exactObject(item, ['boundary_sha256', 'classification', 'payload_sha256', 'provenance_ref', 'record_sha256', 'revision_ref', 'selection_ref', 'source']);
      if (!['forge', 'planning', 'knowledge'].includes(item.source) || !['public', 'private'].includes(item.classification)) fail('invalid_response');
      opaque(item.selection_ref); opaque(item.revision_ref); opaque(item.provenance_ref);
      rawDigest(item.boundary_sha256, false); rawDigest(item.record_sha256, false); rawDigest(item.payload_sha256, false);
      const triple = `${item.source}\u0000${item.selection_ref}\u0000${item.revision_ref}`;
      if (!triples.has(triple) || itemTriples.has(triple)) fail('invalid_response');
      itemTriples.add(triple);
    }
    if (itemTriples.size !== triples.size || typeof payload.requires_private_confirmation !== 'boolean') fail('invalid_response');
    const privateExpected = payload.items.some((item) => item.classification === 'private');
    if (privateExpected !== payload.requires_private_confirmation) fail('invalid_response');
    if (payload.manifest_digest !== null && (typeof payload.manifest_digest !== 'string' || !RAW_DIGEST.test(payload.manifest_digest))) fail('invalid_response');
    if (payload.requires_private_confirmation !== (payload.manifest_digest !== null)) fail('invalid_response');
    if (!Array.isArray(payload.targets) || payload.targets.length !== returnedSelection.length) fail('invalid_response');
    const targetIndexes = new Set();
    for (const target of payload.targets) {
      exactObject(target, ['manifest_digest', 'target_index']);
      const index = safeInteger(target.target_index, 0, returnedSelection.length - 1);
      const source = returnedSelection[index].source;
      const targetDigest = source === 'knowledge' ? /^sha256:[0-9a-f]{64}$/ : RAW_DIGEST;
      if (targetIndexes.has(index) || typeof target.manifest_digest !== 'string' || !targetDigest.test(target.manifest_digest)) fail('invalid_response');
      targetIndexes.add(index);
    }
    if (targetIndexes.size !== returnedSelection.length) fail('invalid_response');
    return Object.freeze({
      selection: returnedSelection,
      items: Object.freeze(payload.items.map((item) => Object.freeze({ ...item }))),
      targets: Object.freeze(payload.targets.map((target) => Object.freeze({ ...target }))),
      requires_private_confirmation: payload.requires_private_confirmation,
      manifest_digest: payload.manifest_digest,
    });
  }

  function validateChallenge(payload) {
    exactObject(payload, ['challenge_token', 'command', 'expires_at']);
    if (typeof payload.challenge_token !== 'string' || !TOKEN.test(payload.challenge_token)) fail('invalid_response');
    if (payload.command !== `/context-disclose ${payload.challenge_token}` || typeof payload.expires_at !== 'string' || !Number.isFinite(Date.parse(payload.expires_at))) fail('invalid_response');
    const result = { challenge_token: payload.challenge_token, command: payload.command, expires_at: payload.expires_at };
    Object.defineProperties(result, {
      challengeToken: { value: payload.challenge_token },
      expiresAt: { value: payload.expires_at },
    });
    return Object.freeze(result);
  }

  function contentTypeIsOctets(response) {
    const value = header(response, 'content-type').toLowerCase();
    return value.split(';', 1)[0].trim() === 'application/octet-stream';
  }

  function contentTypeIsJson(response) {
    const value = header(response, 'content-type').toLowerCase();
    const mediaType = value.split(';', 1)[0].trim();
    return mediaType === 'application/json';
  }

  function hasNoStore(response) {
    return header(response, 'cache-control').toLowerCase().split(',').map((item) => item.trim()).indexOf('no-store') >= 0;
  }

  async function cancelReader(reader) {
    try {
      if (reader && typeof reader.cancel === 'function') await reader.cancel();
    } catch (_) { /* fixed boundary */ }
  }

  async function cancelResponseBody(response) {
    try {
      const body = response && response.body;
      if (!body) return;
      if (typeof body.cancel === 'function') {
        await body.cancel();
        return;
      }
      if (typeof body.getReader === 'function') {
        const reader = body.getReader();
        await cancelReader(reader);
        try { reader.releaseLock?.(); } catch (_) { /* fixed boundary */ }
      }
    } catch (_) { /* fixed boundary */ }
  }

  async function digestHex(bytes) {
    const cryptoObject = host.crypto || (typeof globalThis !== 'undefined' ? globalThis.crypto : null);
    if (!cryptoObject || !cryptoObject.subtle || typeof cryptoObject.subtle.digest !== 'function') fail('crypto_unavailable');
    const digest = new Uint8Array(await cryptoObject.subtle.digest('SHA-256', bytes));
    let result = '';
    for (const byte of digest) result += byte.toString(16).padStart(2, '0');
    return result;
  }

  async function readBinary(response, limit, state) {
    if (!contentTypeIsOctets(response) || !hasNoStore(response)) fail('invalid_response');
    const reader = response.body && typeof response.body.getReader === 'function' ? response.body.getReader() : null;
    if (!reader) fail('stream_unavailable');
    state.reader = reader;
    const chunks = [];
    let total = 0;
    try {
      while (true) {
        if (state.aborted()) fail(state.abortCode());
        const part = await reader.read();
        if (!part || part.done) break;
        if (!(part.value instanceof Uint8Array)) fail('invalid_response');
        total += part.value.byteLength;
        if (total > limit) fail('response_too_large');
        chunks.push(new Uint8Array(part.value));
      }
    } catch (error) {
      await cancelReader(reader);
      throw error;
    } finally {
      state.reader = null;
      try { reader.releaseLock?.(); } catch (_) { /* fixed boundary */ }
    }
    const bytes = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    return bytes;
  }

  async function readJsonBounded(response, state, maximum = MAX_BODY_BYTES) {
    if (!contentTypeIsJson(response) || !hasNoStore(response)) fail('invalid_response');
    const reader = response.body && typeof response.body.getReader === 'function' ? response.body.getReader() : null;
    if (!reader) fail('stream_unavailable');
    state.reader = reader;
    const chunks = [];
    let total = 0;
    try {
      while (true) {
        if (state.aborted()) fail(state.abortCode());
        const part = await reader.read();
        if (!part || part.done) break;
        if (!(part.value instanceof Uint8Array)) fail('invalid_response');
        total += part.value.byteLength;
        if (total > maximum) fail('response_too_large');
        chunks.push(new Uint8Array(part.value));
      }
    } catch (error) {
      await cancelReader(reader);
      throw error;
    } finally {
      state.reader = null;
      try { reader.releaseLock?.(); } catch (_) { /* fixed boundary */ }
    }
    const bytes = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    let text;
    try {
      text = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    } catch (_) { fail('invalid_response'); }
    return parseJsonText(text, maximum);
  }

  function parseJsonText(text, maximum = MAX_BODY_BYTES) {
    if (typeof text !== 'string' || new TextEncoder().encode(text).byteLength > maximum) fail('response_too_large');
    try { return JSON.parse(text); } catch (_) { fail('invalid_response'); }
  }

  function packCursor(value, errorCode) {
    if (typeof value !== 'string' || value.length < 1 || value.length > MAX_PACK_CURSOR_BYTES || !PACK_CURSOR.test(value)) fail(errorCode);
    return value;
  }

  function decodeCanonicalBase64(value) {
    if (
      typeof value !== 'string' || value.length % 4 !== 0
      || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)
    ) fail('invalid_response');
    if (value.endsWith('==')) {
      const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
      if ((alphabet.indexOf(value[value.length - 3]) & 0x0f) !== 0) fail('invalid_response');
    } else if (value.endsWith('=')) {
      const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
      if ((alphabet.indexOf(value[value.length - 2]) & 0x03) !== 0) fail('invalid_response');
    }
    const decoder = host.atob || (typeof globalThis !== 'undefined' ? globalThis.atob : null);
    if (typeof decoder !== 'function') fail('invalid_response');
    let binary;
    try { binary = decoder(value); } catch (_) { fail('invalid_response'); }
    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
    return bytes;
  }

  function exactPackItem(left, right) {
    exactObject(left, ['boundary_sha256', 'classification', 'payload_sha256', 'provenance_ref', 'record_sha256', 'revision_ref', 'selection_ref', 'source']);
    exactObject(right, ['boundary_sha256', 'classification', 'payload_sha256', 'provenance_ref', 'record_sha256', 'revision_ref', 'selection_ref', 'source']);
    return Object.keys(right).every((key) => left[key] === right[key]);
  }

  async function validatePackResponse(payload, checked, pageBytes, initial, expectedPackId) {
    exactObject(payload, ['authority_granted', 'complete', 'items', 'next_cursor', 'pack_id', 'page', 'read_only', 'schema', 'send_performed']);
    if (payload.schema !== PACK_SCHEMA || payload.read_only !== true || payload.authority_granted !== false || payload.send_performed !== false) fail('invalid_response');
    canonicalPackDigest(payload.pack_id, 'invalid_response');
    if (expectedPackId !== null && payload.pack_id !== expectedPackId) fail('invalid_response');
    if (!Array.isArray(payload.items) || payload.items.length !== checked.items.length) fail('invalid_response');
    for (let index = 0; index < payload.items.length; index += 1) {
      if (!exactPackItem(payload.items[index], checked.items[index])) fail('invalid_response');
      if (!['forge', 'planning', 'knowledge'].includes(payload.items[index].source) || !['public', 'private'].includes(payload.items[index].classification)) fail('invalid_response');
      opaque(payload.items[index].selection_ref); opaque(payload.items[index].revision_ref); opaque(payload.items[index].provenance_ref);
      rawDigest(payload.items[index].boundary_sha256, false); rawDigest(payload.items[index].record_sha256, false); rawDigest(payload.items[index].payload_sha256, false);
    }
    exactObject(payload.page, ['byteform', 'content_b64', 'end', 'offset', 'page_sha256', 'target_index', 'total', 'whole_sha256']);
    const page = payload.page;
    const targetIndex = safeInteger(page.target_index, 0, checked.selection.length - 1);
    const target = checked.selection[targetIndex];
    const expectedItem = checked.items.find((item) => item.source === target.source && item.selection_ref === target.selection_ref && item.revision_ref === target.revision_ref);
    if (!expectedItem) fail('invalid_response');
    const offset = safeInteger(page.offset, 0, MAX_JSON_INT);
    const end = safeInteger(page.end, offset, MAX_JSON_INT);
    const total = safeInteger(page.total, end, MAX_JSON_INT);
    const bytes = decodeCanonicalBase64(page.content_b64);
    if (bytes.byteLength > pageBytes || end - offset !== bytes.byteLength || (bytes.byteLength === 0 && end !== total)) fail('invalid_response');
    canonicalPackDigest(page.whole_sha256, 'invalid_response');
    canonicalPackDigest(page.page_sha256, 'invalid_response');
    if (page.whole_sha256 !== `sha256:${expectedItem.payload_sha256}`) fail('native_digest_mismatch');
    const expectedByteform = target.source === 'forge' ? 'git_blob' : target.source === 'planning' ? 'durable_revision_document' : 'knowledge_payload';
    if (page.byteform !== expectedByteform) fail('invalid_response');
    const actualPage = `sha256:${await digestHex(bytes)}`;
    if (actualPage !== page.page_sha256) fail('content_hash_mismatch');
    if (offset === 0 && end === total && page.page_sha256 !== page.whole_sha256) fail('whole_hash_mismatch');
    const isComplete = targetIndex === checked.selection.length - 1 && end === total;
    if (typeof payload.complete !== 'boolean' || payload.complete !== isComplete) fail('invalid_response');
    if (isComplete) {
      if (payload.next_cursor !== '') fail('invalid_response');
    } else {
      packCursor(payload.next_cursor, 'invalid_response');
      if (end === offset && end !== total) fail('invalid_response');
    }
    if (initial && (targetIndex !== 0 || offset !== 0)) fail('invalid_response');
    const resultPage = {
      target_index: targetIndex, offset, end, total, whole_sha256: page.whole_sha256,
      page_sha256: page.page_sha256, byteform: page.byteform, content_b64: page.content_b64,
    };
    Object.defineProperties(resultPage, {
      bytes: { value: bytes },
      payload_bytes: { value: bytes },
    });
    return Object.freeze({
      schema: PACK_SCHEMA, pack_id: payload.pack_id,
      items: Object.freeze(payload.items.map((item) => Object.freeze({ ...item }))),
      page: Object.freeze(resultPage), next_cursor: payload.next_cursor, complete: payload.complete,
      read_only: true, authority_granted: false, send_performed: false,
    });
  }

  class ContextApiClient {
    constructor(options) {
      const settings = options || {};
      this._fetch = settings.fetchImpl || (typeof host.fetch === 'function' ? host.fetch.bind(host) : null);
      if (typeof this._fetch !== 'function') fail('fetch_unavailable');
    }

    metadata({ chatSessionId, selection: submittedSelection, signal } = {}) {
      const chat = asciiText(chatSessionId, 128);
      const selectionValue = selection(submittedSelection);
      return this._json('/api/context/metadata', { chat_session_id: chat, selection: selectionValue }, signal)
        .then((payload) => validateMetadata(payload, selectionValue));
    }

    bootstrapForge({ chatSessionId, forgeLocators, signal } = {}) {
      const chat = asciiText(chatSessionId, 128);
      if (!Array.isArray(forgeLocators) || forgeLocators.length < 1 || forgeLocators.length > MAX_SELECTIONS) fail('invalid_request');
      const locators = forgeLocators.map(forgeLocator).map((item) => cloneFreeze(item, new Map()));
      return this._json('/api/context/metadata', { chat_session_id: chat, forge_locators: locators }, signal)
        .then((payload) => validateMetadata(payload, null, locators));
    }

    challenge({ chatSessionId, metadata, signal } = {}) {
      const chat = asciiText(chatSessionId, 128);
      const checked = validateMetadata(metadata, null);
      return this._json('/api/context/challenge', { chat_session_id: chat, selection: checked.selection }, signal)
        .then(validateChallenge);
    }

    page({ chatSessionId, metadata, targetIndex, offset, limit, disclosureMessageId, signal } = {}) {
      const chat = asciiText(chatSessionId, 128);
      const checked = validateMetadata(metadata, null);
      const index = safeInteger(targetIndex, 0, checked.selection.length - 1);
      const target = checked.selection[index];
      const maximum = target.source === 'forge' ? MAX_FORGE_PAGE : MAX_OTHER_PAGE;
      const pageOffset = safeInteger(offset, 0, MAX_JSON_INT);
      const pageLimit = safeInteger(limit, 1, maximum);
      if (disclosureMessageId !== undefined && disclosureMessageId !== null) safeInteger(disclosureMessageId, 1, MAX_JSON_INT);
      const expected = checked.targets.find((item) => item.target_index === index);
      if (!expected) fail('invalid_request');
      const body = {
        chat_session_id: chat,
        selection: checked.selection,
        target_index: index,
        expected_manifest_digest: expected.manifest_digest,
        offset: pageOffset,
        limit: pageLimit,
      };
      if (disclosureMessageId !== undefined && disclosureMessageId !== null) body.disclosure_message_id = disclosureMessageId;
      const item = checked.items.find((candidate) => candidate.source === target.source && candidate.selection_ref === target.selection_ref && candidate.revision_ref === target.revision_ref);
      if (!item) fail('invalid_response');
      return this._binary('/api/context/page', body, pageLimit, signal, target, expected.manifest_digest, item.payload_sha256, pageOffset);
    }

    packPage({ chatSessionId, metadata, pageBytes, cursor = null, expectedPackId = null, disclosureMessageId, signal } = {}) {
      const chat = asciiText(chatSessionId, 128);
      const checked = validateMetadata(metadata, null);
      const pageLimit = requestInteger(pageBytes, 1, MAX_PACK_PAGE);
      const initial = cursor === null;
      if (initial) {
        if (expectedPackId !== null) fail('invalid_request');
      } else {
        packCursor(cursor, 'invalid_request');
        if (expectedPackId === null) fail('invalid_request');
      }
      if (expectedPackId !== null) canonicalPackDigest(expectedPackId, 'invalid_request');
      const disclosurePresent = disclosureMessageId !== undefined && disclosureMessageId !== null;
      if (disclosurePresent) requestInteger(disclosureMessageId, 1, MAX_JSON_INT);
      if (checked.items.some((item) => item.classification === 'private') && !disclosurePresent) fail('invalid_request');
      const body = { chat_session_id: chat, selection: checked.selection, page_bytes: pageLimit, cursor };
      if (disclosurePresent) body.disclosure_message_id = disclosureMessageId;
      return this._json('/api/context/pack', body, signal, MAX_PACK_JSON_BYTES,
        (payload) => validatePackResponse(payload, checked, pageLimit, initial, expectedPackId));
    }

    _json(path, body, signal, maximum = MAX_BODY_BYTES, validate = (payload) => payload) {
      return this._request(path, body, signal, async (response, state) => {
        if (!response.ok) fail('request_failed', response.status);
        const lengthHeader = header(response, 'content-length');
        if (lengthHeader && !/^[0-9]+$/.test(lengthHeader)) fail('invalid_response');
        const length = Number(lengthHeader);
        if (Number.isFinite(length) && length > maximum) fail('response_too_large');
        return validate(await readJsonBounded(response, state, maximum));
      });
    }

    _binary(path, body, limit, signal, target, nativeDigest, expectedPayloadDigest, requestedOffset) {
      return this._request(path, body, signal, async (response, state) => {
        if (!response.ok) fail('request_failed', response.status);
        const bytes = await readBinary(response, limit, state);
        const total = safeHeaderInteger(response, 'x-context-total', 0, MAX_JSON_INT);
        const pageOffset = safeHeaderInteger(response, 'x-context-offset', 0, MAX_JSON_INT);
        const end = safeHeaderInteger(response, 'x-context-end', pageOffset, MAX_JSON_INT);
        if (pageOffset !== requestedOffset || end - pageOffset !== bytes.byteLength || bytes.byteLength > limit || pageOffset < 0 || end > total) fail('invalid_response');
        const whole = header(response, 'x-context-whole-sha256');
        const page = header(response, 'x-context-page-sha256');
        const manifest = header(response, 'x-context-manifest-sha256');
        if (!RAW_DIGEST.test(whole) || !RAW_DIGEST.test(page) || normalizedDigest(whole) !== normalizedDigest(expectedPayloadDigest) || normalizedDigest(manifest) !== normalizedDigest(nativeDigest)) fail('native_digest_mismatch');
        const byteform = header(response, 'x-context-byteform');
        const expectedByteform = target.source === 'forge' ? 'git_blob' : target.source === 'planning' ? 'durable_revision_document' : 'knowledge_payload';
        if (byteform !== expectedByteform) fail('invalid_response');
        const actualPage = await digestHex(bytes);
        if (actualPage !== page) fail('content_hash_mismatch');
        if (pageOffset === 0 && end === total && page !== whole) fail('whole_hash_mismatch');
        const result = { bytes, total, offset: pageOffset, end, wholeSha256: whole, pageSha256: page, manifestDigest: manifest, byteform };
        Object.defineProperties(result, {
          payload_bytes: { value: bytes },
          total_bytes: { value: total },
          whole_sha256: { value: whole },
          page_sha256: { value: page },
          manifest_digest: { value: manifest },
        });
        return Object.freeze(result);
      });
    }

    _request(path, body, callerSignal, processResponse) {
      const encoded = bodyBytes(body).encoded;
      const controller = typeof AbortController === 'function' ? new AbortController() : null;
      const state = { reader: null, aborted: () => false, abortCode: () => 'request_aborted' };
      return new Promise((resolve, reject) => {
        let settled = false;
        let timer = null;
        const finish = (error, value) => {
          if (settled) return;
          settled = true;
          if (timer) clearTimeout(timer);
          if (callerSignal && typeof callerSignal.removeEventListener === 'function') callerSignal.removeEventListener('abort', onAbort);
          if (error) reject(error); else resolve(value);
        };
        const onAbort = () => {
          state.aborted = () => true;
          state.abortCode = () => 'request_aborted';
          try { controller?.abort(); } catch (_) { /* fixed boundary */ }
          void cancelReader(state.reader);
          finish(new ContextApiError('request_aborted'));
        };
        if (callerSignal && callerSignal.aborted) { onAbort(); return; }
        if (callerSignal && typeof callerSignal.addEventListener === 'function') callerSignal.addEventListener('abort', onAbort, { once: true });
        timer = setTimeout(() => {
          state.aborted = () => true;
          state.abortCode = () => 'request_timeout';
          try { controller?.abort(); } catch (_) { /* fixed boundary */ }
          void cancelReader(state.reader);
          finish(new ContextApiError('request_timeout'));
        }, REQUEST_TIMEOUT_MS);
        const options = {
          method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
          headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
          body: encoded, signal: controller ? controller.signal : callerSignal,
        };
        Promise.resolve().then(() => this._fetch(path, options)).then((response) => {
          if (settled) {
            void cancelResponseBody(response);
            return;
          }
          if (!response || typeof response !== 'object') {
            finish(new ContextApiError('request_failed'));
            return;
          }
          Promise.resolve().then(() => processResponse(response, state)).then(
            (value) => finish(null, value),
            (error) => {
              if (settled) return;
              if (error instanceof ContextApiError) finish(error);
              else if (state.aborted()) finish(new ContextApiError(state.abortCode()));
              else finish(new ContextApiError('request_failed'));
            },
          );
        }).catch((error) => {
          if (settled) return;
          if (state.aborted()) finish(new ContextApiError(state.abortCode()));
          else if (error instanceof ContextApiError) finish(error);
          else finish(new ContextApiError('request_failed'));
        });
      });
    }
  }

  function safeHeaderInteger(response, name, minimum, maximum) {
    const value = header(response, name);
    if (!/^[0-9]+$/.test(value)) fail('invalid_response');
    return safeInteger(Number(value), minimum, maximum);
  }

  return { ContextApiClient, ContextApiError };
});
