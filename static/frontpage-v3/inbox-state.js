/* Sequence-safe, DOM-free state machine for the Universal Inbox read model. */
(function exposeInboxState(root, factory) {
  let InboxApi = root && root.HarborInboxApi;
  let InboxFixtures = root && root.HarborInboxFixtures;
  if (typeof module === 'object' && module.exports) {
    InboxApi = require('./inbox-api.js');
    InboxFixtures = require('./inbox-fixtures.js');
  }
  const api = factory(InboxApi, InboxFixtures);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && typeof root === 'object') root.HarborInboxState = api;
})(typeof globalThis === 'object' ? globalThis : null, function buildInboxState(
  InboxApi,
  InboxFixtures
) {
  'use strict';

  if (!InboxApi || !InboxFixtures) {
    throw new Error('Inbox API and fixture modules are required');
  }

  const STATE_SCHEMA = 'odysseus.universal_inbox.read_model_state.v1';
  const MODES = Object.freeze([
    'fixture',
    'loading',
    'live',
    'stale',
    'empty',
    'unauthorized',
    'unavailable',
    'error'
  ]);

  class InboxReadModel {
    constructor({ apiClient = null, onChange = null } = {}) {
      this.apiClient = apiClient;
      this.listeners = new Set();
      if (typeof onChange === 'function') this.listeners.add(onChange);
      this.sequence = 0;
      this.activeController = null;
      this.state = freezeState({
        schema: STATE_SCHEMA,
        mode: 'loading',
        origin: originFor('loading'),
        list: null,
        snapshot: null,
        error: null,
        request: {
          sequence: 0,
          pending: false,
          has_loaded: false
        }
      });
    }

    subscribe(listener) {
      if (typeof listener !== 'function') {
        throw new TypeError('listener must be a function');
      }
      this.listeners.add(listener);
      return () => this.listeners.delete(listener);
    }

    getState() {
      return deepClone(this.state);
    }

    async load({
      source = 'live',
      scenario = 'fixture',
      limit = 25,
      cursor = '',
      owner = ''
    } = {}) {
      const sequence = ++this.sequence;
      this._abortActive();

      if (source === 'fixture') {
        const fixture = InboxFixtures.buildFixtureScenario(scenario);
        this._apply({
          schema: STATE_SCHEMA,
          mode: fixture.mode,
          origin: fixture.origin,
          list: fixture.list,
          snapshot: fixture.snapshot,
          error: fixture.error,
          request: {
            sequence,
            pending: false,
            has_loaded: true
          }
        });
        return { applied: true, sequence, mode: fixture.mode };
      }
      if (source !== 'live') {
        throw new RangeError('source must be live or fixture');
      }
      if (!this.apiClient || typeof this.apiClient.readInbox !== 'function') {
        throw new TypeError('apiClient.readInbox is required for live loads');
      }

      const controller = new AbortController();
      this.activeController = controller;
      this._apply({
        schema: STATE_SCHEMA,
        mode: 'loading',
        origin: originFor('loading'),
        list: this.state.list,
        snapshot: this.state.snapshot,
        error: null,
        request: {
          sequence,
          pending: true,
          has_loaded: this.state.request.has_loaded
        }
      });

      try {
        const result = await this.apiClient.readInbox({
          limit,
          cursor,
          owner,
          signal: controller.signal
        });
        if (sequence !== this.sequence) {
          return { applied: false, sequence, reason: 'stale_response' };
        }
        const mode = result.list.items.length ? 'live' : 'empty';
        this.activeController = null;
        this._apply({
          schema: STATE_SCHEMA,
          mode,
          origin: originFor(mode),
          list: result.list,
          snapshot: result.snapshot,
          error: null,
          request: {
            sequence,
            pending: false,
            has_loaded: true
          }
        });
        return { applied: true, sequence, mode };
      } catch (error) {
        if (sequence !== this.sequence) {
          return { applied: false, sequence, reason: 'stale_response' };
        }
        this.activeController = null;
        const mode = modeForError(error);
        this._apply({
          schema: STATE_SCHEMA,
          mode,
          origin: originFor(mode),
          list: null,
          snapshot: null,
          error: {
            code: String(error && error.code || 'error'),
            status: Number(error && error.status || 0)
          },
          request: {
            sequence,
            pending: false,
            has_loaded: true
          }
        });
        return { applied: true, sequence, mode };
      }
    }

    markStale(reason = 'refresh_required') {
      const sequence = ++this.sequence;
      this._abortActive();
      this._apply({
        schema: STATE_SCHEMA,
        mode: 'stale',
        origin: {
          state: 'stale',
          source: 'universal_inbox_api',
          reason: String(reason || 'refresh_required'),
          counts_as_live_evidence: false
        },
        list: this.state.list,
        snapshot: this.state.snapshot,
        error: null,
        request: {
          sequence,
          pending: false,
          has_loaded: this.state.request.has_loaded
        }
      });
      return this.getState();
    }

    dispose() {
      this.sequence += 1;
      this._abortActive();
      this.listeners.clear();
    }

    _abortActive() {
      if (this.activeController) {
        this.activeController.abort();
        this.activeController = null;
      }
    }

    _apply(nextState) {
      if (!MODES.includes(nextState.mode)) {
        throw new RangeError('invalid Inbox read-model mode');
      }
      this.state = freezeState(nextState);
      const snapshot = this.getState();
      this.listeners.forEach(listener => {
        try {
          listener(snapshot);
        } catch {
          // View callbacks cannot corrupt or roll back the authoritative model.
        }
      });
    }
  }

  function originFor(mode) {
    if (mode === 'live' || mode === 'empty') {
      return {
        state: 'live',
        source: 'universal_inbox_api',
        counts_as_live_evidence: true
      };
    }
    return {
      state: mode,
      source: 'universal_inbox_api',
      counts_as_live_evidence: false
    };
  }

  function modeForError(error) {
    const code = String(error && error.code || '');
    if (code === 'unauthorized') return 'unauthorized';
    if (code === 'unavailable') return 'unavailable';
    return 'error';
  }

  function freezeState(value) {
    const clone = deepClone(value);
    return deepFreeze(clone);
  }

  function deepFreeze(value) {
    if (!value || typeof value !== 'object' || Object.isFrozen(value)) {
      return value;
    }
    Object.values(value).forEach(deepFreeze);
    return Object.freeze(value);
  }

  function deepClone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  return Object.freeze({
    MODES,
    STATE_SCHEMA,
    InboxReadModel
  });
});
