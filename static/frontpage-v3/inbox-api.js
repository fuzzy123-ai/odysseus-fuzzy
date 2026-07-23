/* DOM-free client and validation boundary for the Universal Inbox read API. */
(function exposeInboxApi(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && typeof root === 'object') root.HarborInboxApi = api;
})(typeof globalThis === 'object' ? globalThis : null, function buildInboxApi() {
  'use strict';

  const ITEMS_SCHEMA = 'odysseus.universal_inbox.items.v1';
  const SNAPSHOT_SCHEMA = 'odysseus.universal_inbox.snapshot.v1';
  const SOURCE_REF_RE = /^upload:[0-9a-f]{32}(?:\.[a-z0-9]+)?$/i;
  const FORBIDDEN_KEYS = new Set([
    'absolute_path',
    'body',
    'bytes',
    'chat_id',
    'content',
    'file_hash',
    'file_path',
    'hash',
    'owner',
    'owner_id',
    'path',
    'raw_bytes',
    'raw_content',
    'raw_text',
    'source_path',
    'storage_path',
    'text_content'
  ]);
  const SNAPSHOT_IDENTITY_KEYS = new Set([
    'display_name',
    'item',
    'items',
    'source_ref'
  ]);

  class InboxApiError extends Error {
    constructor(code, { status = 0, cause = null } = {}) {
      super(code);
      this.name = 'InboxApiError';
      this.code = code;
      this.status = status;
      if (cause) this.cause = cause;
    }
  }

  class InboxApiClient {
    constructor({
      fetchImpl = typeof fetch === 'function' ? fetch.bind(globalThis) : null,
      baseUrl = '/api/universal-inbox'
    } = {}) {
      if (typeof fetchImpl !== 'function') {
        throw new TypeError('fetchImpl is required');
      }
      this.fetchImpl = fetchImpl;
      this.baseUrl = String(baseUrl || '/api/universal-inbox').replace(/\/+$/, '');
    }

    async listItems({
      limit = 25,
      cursor = '',
      owner = '',
      signal = null
    } = {}) {
      const query = new URLSearchParams();
      query.set('limit', String(limit));
      if (cursor) query.set('cursor', String(cursor));
      if (owner) query.set('owner', String(owner));
      const payload = await this._request(
        `${this.baseUrl}/items?${query.toString()}`,
        { signal }
      );
      return assertItemsPayload(payload);
    }

    async getSnapshot({ owner = '', signal = null } = {}) {
      const query = new URLSearchParams();
      if (owner) query.set('owner', String(owner));
      const suffix = query.toString() ? `?${query.toString()}` : '';
      const payload = await this._request(
        `${this.baseUrl}/snapshot${suffix}`,
        { signal }
      );
      return assertSnapshotPayload(payload);
    }

    async readInbox(options = {}) {
      const [list, snapshot] = await Promise.all([
        this.listItems(options),
        this.getSnapshot(options)
      ]);
      return { list, snapshot };
    }

    async _request(url, { signal = null } = {}) {
      let response;
      try {
        response = await this.fetchImpl(url, {
          method: 'GET',
          credentials: 'same-origin',
          cache: 'no-store',
          headers: { Accept: 'application/json' },
          signal
        });
      } catch (error) {
        if (error && error.name === 'AbortError') {
          throw new InboxApiError('aborted', { cause: error });
        }
        throw new InboxApiError('unavailable', { cause: error });
      }

      if (!response || typeof response.status !== 'number') {
        throw new InboxApiError('invalid_response');
      }
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) {
          throw new InboxApiError('unauthorized', { status: response.status });
        }
        if (response.status === 503) {
          throw new InboxApiError('unavailable', { status: response.status });
        }
        throw new InboxApiError('request_failed', { status: response.status });
      }

      try {
        return await response.json();
      } catch (error) {
        throw new InboxApiError('invalid_response', {
          status: response.status,
          cause: error
        });
      }
    }
  }

  function assertItemsPayload(payload) {
    assertPlainObject(payload, 'invalid_items_payload');
    if (
      payload.schema !== ITEMS_SCHEMA ||
      payload.absolute_paths_visible !== false ||
      payload.raw_content_visible !== false ||
      !Array.isArray(payload.items)
    ) {
      throw new InboxApiError('invalid_items_payload');
    }
    assertNoForbiddenKeys(payload);
    payload.items.forEach(item => {
      assertPlainObject(item, 'invalid_item');
      if (
        !SOURCE_REF_RE.test(String(item.source_ref || '')) ||
        typeof item.display_name !== 'string' ||
        !item.display_name ||
        item.absolute_path_visible !== false ||
        item.raw_content_visible !== false ||
        item.owner_identifier_visible !== false
      ) {
        throw new InboxApiError('invalid_item');
      }
      assertPlainObject(item.metadata, 'invalid_item_metadata');
      assertPlainObject(item.capability, 'invalid_item_capability');
    });
    assertPlainObject(payload.page, 'invalid_items_page');
    if (
      !Number.isInteger(payload.page.returned_count) ||
      typeof payload.page.has_more !== 'boolean' ||
      payload.page.returned_count !== payload.items.length
    ) {
      throw new InboxApiError('invalid_items_page');
    }
    return deepClone(payload);
  }

  function assertSnapshotPayload(payload) {
    assertPlainObject(payload, 'invalid_snapshot_payload');
    if (
      payload.schema !== SNAPSHOT_SCHEMA ||
      payload.item_names_visible !== false ||
      payload.source_refs_visible !== false ||
      payload.absolute_paths_visible !== false ||
      payload.raw_content_visible !== false ||
      !Number.isInteger(payload.total_count) ||
      payload.total_count < 0
    ) {
      throw new InboxApiError('invalid_snapshot_payload');
    }
      assertNoForbiddenKeys(payload);
      assertNoKeys(payload, SNAPSHOT_IDENTITY_KEYS, 'unsafe_snapshot_key');
      assertPlainObject(payload.counts, 'invalid_snapshot_counts');
    assertPlainObject(payload.readiness, 'invalid_snapshot_readiness');
    return deepClone(payload);
  }

  function assertNoForbiddenKeys(value) {
    assertNoKeys(value, FORBIDDEN_KEYS, 'unsafe_payload_key');
  }

  function assertNoKeys(value, forbiddenKeys, code) {
    const queue = [value];
    while (queue.length) {
      const current = queue.shift();
      if (!current || typeof current !== 'object') continue;
      for (const [key, nested] of Object.entries(current)) {
        if (forbiddenKeys.has(key)) {
          throw new InboxApiError(code);
        }
        if (nested && typeof nested === 'object') queue.push(nested);
      }
    }
  }

  function assertPlainObject(value, code) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new InboxApiError(code);
    }
  }

  function deepClone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  return Object.freeze({
    ITEMS_SCHEMA,
    SNAPSHOT_SCHEMA,
    InboxApiClient,
    InboxApiError,
    assertItemsPayload,
    assertSnapshotPayload
  });
});
