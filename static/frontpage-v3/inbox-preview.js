/* Harbor One V3 bounded, owner-scoped Universal Inbox preview adapter. */
(function exposeInboxPreview(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root && typeof root === 'object') root.HarborInboxPreview = api;
})(typeof globalThis === 'object' ? globalThis : null, function buildInboxPreview() {
  'use strict';

  const CONTENT_SCHEMA = 'odysseus.universal_inbox.source_content.v1';
  const CAPABILITY_SCHEMA = 'odysseus.universal_inbox.workbench_capability.v1';
  const SOURCE_REF = /^upload:[0-9a-f]{32}(?:\.[a-z0-9]+)?$/i;
  const TEXT_SUFFIXES = new Set(['.md', '.markdown', '.txt']);
  const PDF_SUFFIX = '.pdf';
  const DOCX_SUFFIX = '.docx';
  const DEFAULT_MAX_BYTES = 256 * 1024;
  const HARD_MAX_BYTES = DEFAULT_MAX_BYTES;

  function suffixFor(item) {
    const value = item && item.metadata && item.metadata.suffix;
    return String(value || '').trim().toLowerCase();
  }

  function sourceRefFor(item) {
    return String(item && item.source_ref || '').trim();
  }

  function typeFor(item) {
    const suffix = suffixFor(item);
    if (TEXT_SUFFIXES.has(suffix)) return 'text';
    if (suffix === PDF_SUFFIX) return 'pdf';
    if (suffix === DOCX_SUFFIX) return 'docx';
    return 'unsupported';
  }

  function isAbort(error) {
    return Boolean(error && error.name === 'AbortError');
  }

  function contentType(response) {
    return String(response.headers.get('content-type') || '').toLowerCase().split(';')[0].trim();
  }

  function header(response, name) {
    return String(response.headers.get(name) || '').trim();
  }

  function safeStatus(status) {
    const message = {
      loading: 'Loading bounded owner-scoped preview.',
      ready: 'Preview ready. The original remains immutable.',
      truncated: 'Preview is truncated at the visible byte limit. The original remains immutable.',
      byte_limit: 'Preview closed because the response exceeded the client byte limit.',
      unauthorized: 'Preview unavailable: owner authorization is required.',
      unavailable: 'Preview unavailable: the source service is not available.',
      oversized: 'Preview unavailable: source exceeds the permitted size.',
      unsupported: 'Preview unavailable: this format is closed for preview.',
      mismatch: 'Preview closed: server content validation did not match this selection.',
      password_required: 'Preview unavailable: a password or protected-document review is required.',
      blocked: 'Preview unavailable: this source is blocked by server policy.',
      review: 'Preview paused: review is required before content can be shown.',
      error: 'Preview unavailable due to a safe loading error.',
      extraction: 'Server-authoritative DOCX extraction preview ready. No DOCX round-trip is available.'
    };
    return message[status] || message.error;
  }

  class InboxPreviewController {
    constructor({ root, fetchImpl, urlApi, maxBytes = DEFAULT_MAX_BYTES } = {}) {
      if (!root || typeof root.replaceChildren !== 'function') throw new TypeError('preview_root_required');
      this.root = root;
      this.fetch = fetchImpl || (typeof fetch === 'function' ? fetch.bind(globalThis) : null);
      this.urlApi = urlApi || URL;
      const requestedMaxBytes = Number.isInteger(maxBytes) && maxBytes > 0 ? maxBytes : DEFAULT_MAX_BYTES;
      Object.defineProperty(this, 'maxBytes', {
        value: Math.min(requestedMaxBytes, HARD_MAX_BYTES), enumerable: true, writable: false
      });
      this.sequence = 0;
      this.controller = null;
      this.objectUrl = null;
      this.destroyed = false;
      this.root.classList.add('uix-inbox-preview');
      this._show('unsupported', 'Select a supported owner-scoped document to inspect it.');
    }

    async load(item, { extractedText = null, extractionState = 'ready' } = {}) {
      this._stopActive();
      if (this.destroyed) return;
      const sequence = ++this.sequence;
      const kind = typeFor(item);
      const sourceRef = sourceRefFor(item);
      const capabilityState = inspectStateFor(item, kind);
      if (item && item.metadata && item.metadata.blocked === true) {
        this._show('blocked');
        return;
      }
      if (item && item.metadata && item.metadata.review_required === true) {
        this._show('review');
        return;
      }
      if (capabilityState !== 'allowed') {
        this._show(capabilityState);
        return;
      }
      if (kind === 'unsupported' || !SOURCE_REF.test(sourceRef)) {
        this._show('unsupported');
        return;
      }
      if (kind === 'docx') {
        if (typeof extractedText !== 'string' || extractionState !== 'ready') {
          this._show(extractionState === 'review' ? 'review' : 'unsupported', 'DOCX is available only as a server-authoritative extracted text preview.');
          return;
        }
        if (new TextEncoder().encode(extractedText).byteLength > this.maxBytes) {
          this._show('byte_limit', 'DOCX extraction preview closed because it exceeds the client byte limit.');
          return;
        }
        this._renderText(extractedText, 'extraction', true);
        return;
      }
      if (!this.fetch) {
        this._show('error');
        return;
      }

      const controller = new AbortController();
      this.controller = controller;
      this._show('loading');
      try {
        const response = await this.fetch(this._contentUrl(sourceRef), {
          method: 'GET', credentials: 'same-origin', mode: 'same-origin', redirect: 'error', cache: 'no-store', signal: controller.signal,
          headers: { Range: `bytes=0-${this.maxBytes - 1}` }
        });
        if (!this._current(sequence, controller)) return;
        if (!response.ok) {
          this._show(this._statusForResponse(response));
          return;
        }
        const range = this._rangeWindow(response);
        if (!this._validHeaders(response, kind, range)) {
          this._show('mismatch');
          return;
        }
        const declaredLength = Number(header(response, 'content-length'));
        if (Number.isFinite(declaredLength) && declaredLength > this.maxBytes) {
          this._show('byte_limit');
          return;
        }
        const bytes = await response.arrayBuffer();
        if (!this._current(sequence, controller)) return;
        if (bytes.byteLength !== declaredLength) {
          this._show('mismatch', 'Preview closed because the response length did not match the server header.');
          return;
        }
        if (bytes.byteLength > this.maxBytes) {
          this._show('byte_limit');
          return;
        }
        const state = header(response, 'x-odysseus-content-state');
        const visiblyTruncated = state === 'truncated' || (range && range.incomplete);
        if (kind === 'text') {
          this._renderText(new TextDecoder('utf-8', { fatal: false }).decode(bytes), visiblyTruncated ? 'truncated' : 'ready', false);
        } else if (visiblyTruncated) {
          this._show('truncated', 'PDF preview closed because the bounded response is incomplete.');
        } else {
          this._renderPdf(bytes, 'ready');
        }
      } catch (error) {
        if (!this._current(sequence, controller) || isAbort(error)) return;
        this._revokeObjectUrl();
        this._show('error');
      } finally {
        if (this._current(sequence, controller)) this.controller = null;
      }
    }

    destroy() {
      this.destroyed = true;
      this.sequence += 1;
      this._stopActive();
      this.root.replaceChildren();
    }

    _contentUrl(sourceRef) {
      return `/api/universal-inbox/items/${encodeURIComponent(sourceRef)}/content`;
    }

    _current(sequence, controller) {
      return !this.destroyed && this.sequence === sequence && this.controller === controller;
    }

    _stopActive() {
      if (this.controller) this.controller.abort();
      this.controller = null;
      this._revokeObjectUrl();
    }

    _revokeObjectUrl() {
      if (this.objectUrl) this.urlApi.revokeObjectURL(this.objectUrl);
      this.objectUrl = null;
    }

    _rangeWindow(response) {
      if (response.status !== 206) return null;
      const match = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(header(response, 'content-range'));
      if (!match) return { valid: false };
      const start = Number(match[1]);
      const end = Number(match[2]);
      const total = Number(match[3]);
      if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || !Number.isSafeInteger(total)
        || start !== 0 || end < start || total <= end) return { valid: false };
      return { valid: true, incomplete: total > end + 1, length: end - start + 1 };
    }

    _validHeaders(response, kind, range) {
      const state = header(response, 'x-odysseus-content-state');
      const truncated = header(response, 'x-odysseus-content-truncated');
      const cacheControl = header(response, 'cache-control').toLowerCase();
      const declaredLength = Number(header(response, 'content-length'));
      const expectedType = kind === 'pdf' ? 'application/pdf' : 'text/plain';
      return (response.status === 200 || response.status === 206)
        && ((response.status === 200 && state === 'complete')
          || (response.status === 206 && ['partial', 'truncated'].includes(state)))
        && (response.status !== 206 || (range && range.valid))
        && Number.isSafeInteger(declaredLength) && declaredLength >= 0
        && (!range || declaredLength === range.length)
        && header(response, 'x-odysseus-content-schema') === CONTENT_SCHEMA
        && ['complete', 'partial', 'truncated'].includes(state)
        && ((state === 'truncated' && truncated === 'true') || (state !== 'truncated' && truncated === 'false'))
        && header(response, 'x-content-type-options').toLowerCase() === 'nosniff'
        && cacheControl.includes('no-store')
        && contentType(response) === expectedType;
    }

    _statusForResponse(response) {
      if (response.status === 401 || response.status === 403) return 'unauthorized';
      if (response.status === 413) return 'oversized';
      if (response.status === 503) return 'unavailable';
      if (response.status === 409 || response.status === 415) return 'mismatch';
      if (response.status === 422) return 'password_required';
      return 'error';
    }

    _show(status, detail) {
      this.root.dataset.previewState = status;
      const panel = document.createElement('section');
      panel.className = 'uix-inbox-preview-state';
      panel.setAttribute('aria-label', 'Document preview status');
      const title = document.createElement('h2');
      title.textContent = status === 'loading' ? 'Loading preview' : status === 'ready' ? 'Preview ready' : 'Preview closed';
      const copy = document.createElement('p');
      copy.textContent = detail || safeStatus(status);
      const limit = document.createElement('small');
      limit.textContent = `Client byte limit: ${this.maxBytes.toLocaleString()} bytes`;
      const live = document.createElement('p');
      live.className = 'uix-inbox-preview-live';
      live.setAttribute('aria-live', status === 'loading' ? 'polite' : 'assertive');
      live.textContent = detail || safeStatus(status);
      panel.append(title, copy, limit, live);
      this.root.replaceChildren(panel);
    }

    _renderText(text, status, extraction) {
      this.root.dataset.previewState = status;
      const panel = document.createElement('section');
      panel.className = 'uix-inbox-preview-text';
      const label = document.createElement('p');
      label.className = 'uix-inbox-preview-label';
      label.textContent = `${extraction ? 'Server-authoritative DOCX extraction · no DOCX round-trip' : 'Bounded text preview · original immutable'} · client byte limit: ${this.maxBytes.toLocaleString()} bytes`;
      const pre = document.createElement('pre');
      pre.setAttribute('data-preview-inert-text', 'true');
      pre.textContent = text;
      const live = document.createElement('p');
      live.className = 'uix-inbox-preview-live';
      live.setAttribute('aria-live', 'polite');
      live.textContent = safeStatus(extraction ? 'extraction' : status);
      panel.append(label, pre, live);
      this.root.replaceChildren(panel);
    }

    _renderPdf(bytes, status) {
      const blob = new Blob([bytes], { type: 'application/pdf' });
      this.objectUrl = this.urlApi.createObjectURL(blob);
      this.root.dataset.previewState = status;
      const panel = document.createElement('section');
      panel.className = 'uix-inbox-preview-pdf';
      const label = document.createElement('p');
      label.className = 'uix-inbox-preview-label';
      label.textContent = `Bounded PDF preview · isolated from the page · original immutable · client byte limit: ${this.maxBytes.toLocaleString()} bytes`;
      const frame = document.createElement('iframe');
      frame.title = 'Bounded PDF preview';
      frame.setAttribute('sandbox', '');
      frame.setAttribute('referrerpolicy', 'no-referrer');
      frame.src = this.objectUrl;
      const live = document.createElement('p');
      live.className = 'uix-inbox-preview-live';
      live.setAttribute('aria-live', 'polite');
      live.textContent = safeStatus(status);
      panel.append(label, frame, live);
      this.root.replaceChildren(panel);
    }
  }

  function inspectStateFor(item, kind) {
    const capability = item && item.capability;
    if (!capability || typeof capability !== 'object' || Array.isArray(capability)) return 'review';
    if (capability.schema !== CAPABILITY_SCHEMA || capability.server_authoritative !== true
      || capability.owner_authorized !== true || capability.live_write_authorized !== false
      || capability.original_immutable !== true || capability.raw_content_visible !== false
      || capability.absolute_path_visible !== false || capability.mvp_tier !== 'P0'
      || capability.source_suffix !== suffixFor(item) || !Array.isArray(capability.actions)) return 'review';
    const inspect = capability.actions.find(action => action && action.action === 'inspect');
    if (!inspect || inspect.state !== 'allowed' || inspect.mutates_original !== false
      || inspect.performs_live_write !== false) return 'review';
    return kind === 'unsupported' ? 'unsupported' : 'allowed';
  }

  return Object.freeze({ CONTENT_SCHEMA, CAPABILITY_SCHEMA, DEFAULT_MAX_BYTES, HARD_MAX_BYTES, InboxPreviewController, typeFor });
});
