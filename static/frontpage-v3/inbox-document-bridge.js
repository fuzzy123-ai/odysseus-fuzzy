/*
 * Universal Inbox -> Document bridge.  This deliberately contains no editor,
 * storage, or diff implementation: the owner-scoped Document APIs and the
 * already-loaded documentModule remain the only persistence/UI paths.
 *
 * Preflight reads reduce accidental overwrites, but GET followed by PUT is
 * not an atomic compare-and-swap.  A concurrent write after the GET remains
 * a server-side TOCTOU window and is intentionally not represented as CAS.
 */
(function (root, factory) {
  var api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.HarborInboxDocumentBridge = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';

  var MAX_CONTENT_BYTES = 1024 * 1024;
  var MAX_VERSIONS = 200;
  // Mirrors src/upload_handler.py UPLOAD_ID_RE (the server owns this authority).
  var SOURCE_REF = /^upload:[0-9a-fA-F]{32}(?:\.[A-Za-z0-9]+)?$/;
  var DOC_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
  var REASON = /^[a-z0-9_]{1,64}$/;
  var MODES = { original: true, extraction: true, working_copy: true, difference: true };
  var SAVE_STATES = { idle: true, creating: true, ready: true, dirty: true, saving: true, saved: true, conflict: true, error: true };

  function bytes(value) {
    if (typeof value !== 'string') return Infinity;
    if (typeof TextEncoder !== 'undefined') return new TextEncoder().encode(value).length;
    return unescape(encodeURIComponent(value)).length;
  }
  function validContent(value) { return typeof value === 'string' && bytes(value) <= MAX_CONTENT_BYTES; }
  function validDate(value) { return typeof value === 'string' && value.length <= 64 && Number.isFinite(Date.parse(value)); }
  function validId(value) { return typeof value === 'string' && DOC_ID.test(value); }
  function cleanReason(value, fallback) { return typeof value === 'string' && REASON.test(value) ? value : fallback; }
  function abortError(error) { return error && error.name === 'AbortError'; }

  function create(options) {
    options = options || {};
    var fetchImpl = options.fetch || root.fetch;
    var moduleOverride = options.documentModule;
    var debounceMs = Number.isInteger(options.debounceMs) && options.debounceMs >= 0 ? options.debounceMs : 800;
    var state = {
      viewMode: 'original', saveState: 'idle', workingCopyId: null, version_count: 0,
      dirty: false, conflict: null, original_immutable: true, source_ref_redacted: true,
      live_write_authorized: false
    };
    var sourceRef = null, baseline = null, content = '', timer = null, sequence = 0, destroyed = false;
    var controllers = [];

    function emit() {
      if (typeof options.onChange !== 'function') return;
      try { options.onChange(getState()); } catch (_) { /* caller isolation */ }
    }
    function transition(patch) {
      Object.assign(state, patch);
      emit();
    }
    function getModule(required) {
      var dm = moduleOverride || root.documentModule;
      if (!dm || !required.every(function (name) { return typeof dm[name] === 'function'; })) return null;
      return dm;
    }
    function newRequest() {
      var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
      if (controller) controllers.push(controller);
      return controller;
    }
    function clearRequests() {
      controllers.forEach(function (controller) { try { controller.abort(); } catch (_) {} });
      controllers = [];
    }
    function stale(token) { return destroyed || token !== sequence; }
    function fail(code, keepDirty) {
      transition({ saveState: 'error', dirty: !!keepDirty, conflict: null });
      state.errorReason = cleanReason(code, 'request_failed'); // never projected
      return false;
    }
    async function request(path, init, token) {
      if (typeof fetchImpl !== 'function') throw { code: 'fetch_unavailable' };
      var controller = newRequest();
      var headers = { Accept: 'application/json' };
      if (init && init.body) headers['Content-Type'] = 'application/json';
      try {
        var response = await fetchImpl(path, Object.assign({
          credentials: 'same-origin', mode: 'same-origin', redirect: 'error', cache: 'no-store',
          headers: headers, signal: controller && controller.signal
        }, init || {}));
        if (stale(token)) throw { code: 'stale' };
        if (!response || !response.ok) throw { code: 'http_' + (response && Number.isInteger(response.status) ? response.status : 0) };
        var type = response.headers && response.headers.get && response.headers.get('content-type');
        if (typeof type !== 'string' || type.toLowerCase().indexOf('application/json') !== 0) throw { code: 'invalid_content_type' };
        var body;
        try { body = await response.json(); } catch (_) { throw { code: 'invalid_json' }; }
        if (stale(token)) throw { code: 'stale' };
        return body;
      } finally {
        if (controller) controllers = controllers.filter(function (item) { return item !== controller; });
      }
    }
    function documentFrom(value) {
      if (!value || typeof value !== 'object' || Array.isArray(value) || !validId(value.id) ||
          !validContent(value.current_content) || !Number.isInteger(value.version_count) || value.version_count < 1 ||
          !validDate(value.updated_at)) return null;
      return { id: value.id, current_content: value.current_content, version_count: value.version_count, updated_at: value.updated_at,
        title: typeof value.title === 'string' ? value.title.slice(0, 512) : '', language: typeof value.language === 'string' ? value.language.slice(0, 64) : '',
        session_id: typeof value.session_id === 'string' ? value.session_id.slice(0, 128) : null };
    }
    function versionFrom(value, wanted, expectedDocId, requireDocumentId) {
      if (!value || typeof value !== 'object' || Array.isArray(value) ||
          (requireDocumentId && (!validId(value.document_id) || value.document_id !== expectedDocId)) ||
          (!requireDocumentId && value.document_id !== undefined && (!validId(value.document_id) || value.document_id !== expectedDocId)) ||
          !Number.isInteger(value.version_number) || value.version_number < 1 ||
          (wanted && value.version_number !== wanted) || !validContent(value.content)) return null;
      return { id: validId(value.id) ? value.id : null, document_id: expectedDocId, version_number: value.version_number,
        content: value.content, summary: typeof value.summary === 'string' ? value.summary.slice(0, 512) : '',
        source: typeof value.source === 'string' ? value.source.slice(0, 64) : '', created_at: validDate(value.created_at) ? value.created_at : null };
    }
    function setBaseline(doc) { baseline = { version_count: doc.version_count, updated_at: doc.updated_at, current_content: doc.current_content }; }
    function sameBaseline(doc) { return baseline && doc.version_count === baseline.version_count && doc.updated_at === baseline.updated_at && doc.current_content === baseline.current_content; }
    function resetForSwitch() {
      sequence += 1; clearRequests(); clearTimeout(timer); timer = null;
      sourceRef = null; baseline = null; content = '';
      state.workingCopyId = null; state.version_count = 0; state.dirty = false; state.conflict = null;
    }
    function getState() {
      return {
        viewMode: state.viewMode, saveState: state.saveState, workingCopyId: state.workingCopyId,
        version_count: state.version_count, dirty: state.dirty, conflict: state.conflict,
        original_immutable: true, source_ref_redacted: true, live_write_authorized: false
      };
    }
    async function createWorkingCopy(ref, createOptions) {
      if (destroyed || !SOURCE_REF.test(ref || '')) return fail('invalid_source_ref', false);
      var dm = moduleOverride || root.documentModule;
      if (!dm || (typeof dm.injectFreshDoc !== 'function' && typeof dm.loadDocument !== 'function')) return fail('document_module_unavailable', false);
      resetForSwitch(); sourceRef = ref; var token = sequence;
      transition({ viewMode: 'working_copy', saveState: 'creating', dirty: false, conflict: null });
      try {
        var data = await request('/api/universal-inbox/items/' + encodeURIComponent(ref) + '/working-copy', {
          method: 'POST', body: JSON.stringify({ new_revision: !!(createOptions && createOptions.new_revision) })
        }, token);
        var doc = documentFrom(data);
        var workingCopy = data && data.working_copy;
        if (!doc || !workingCopy || typeof workingCopy !== 'object' || Array.isArray(workingCopy) ||
            workingCopy.schema !== 'odysseus.universal_inbox.working_copy.v1' || !validId(workingCopy.working_copy_id) ||
            workingCopy.working_copy_id !== doc.id || workingCopy.version !== doc.version_count ||
            typeof workingCopy.created !== 'boolean' || typeof workingCopy.revision_created !== 'boolean') throw { code: 'invalid_document' };
        if (stale(token)) return false;
        content = doc.current_content; setBaseline(doc);
        transition({ workingCopyId: doc.id, version_count: doc.version_count, saveState: 'ready', dirty: false, conflict: null, viewMode: 'working_copy' });
        if (typeof dm.injectFreshDoc === 'function') dm.injectFreshDoc(Object.assign({}, doc, { content: doc.current_content }));
        else await dm.loadDocument(doc.id);
        return true;
      } catch (error) {
        if (abortError(error) || (error && error.code === 'stale')) return false;
        return fail(error && error.code, false);
      }
    }
    function setViewMode(mode) {
      if (!MODES[mode]) return false;
      if (mode === 'difference') return false;
      if ((mode === 'original' || mode === 'extraction' || mode === 'difference') && state.dirty && mode !== 'difference') return false;
      transition({ viewMode: mode }); return true;
    }
    function setWorkingCopyContent(next) {
      if (destroyed || state.viewMode !== 'working_copy' || !state.workingCopyId || !validContent(next)) return false;
      content = next; transition({ dirty: true, saveState: 'dirty', conflict: null });
      clearTimeout(timer); timer = setTimeout(function () { save().catch(function () {}); }, debounceMs);
      if (timer && typeof timer.unref === 'function') timer.unref();
      return true;
    }
    function markDirty(next) { return typeof next === 'string' ? setWorkingCopyContent(next) : (state.viewMode === 'working_copy' && setWorkingCopyContent(content)); }
    async function save(summary) {
      if (destroyed || state.viewMode !== 'working_copy' || !state.workingCopyId || !state.dirty || !validContent(content)) return false;
      var token = sequence, docId = state.workingCopyId, savingContent = content;
      transition({ saveState: 'saving', conflict: null });
      try {
        var remote = documentFrom(await request('/api/document/' + encodeURIComponent(docId), { method: 'GET' }, token));
        if (!remote || remote.id !== docId) throw { code: 'invalid_document' };
        if (!sameBaseline(remote)) {
          transition({ saveState: 'conflict', dirty: true, conflict: 'remote_changed' }); return false;
        }
        var payload = { content: savingContent, summary: typeof summary === 'string' ? summary.slice(0, 512) : 'Universal Inbox working copy edit' };
        var saved = documentFrom(await request('/api/document/' + encodeURIComponent(docId), { method: 'PUT', body: JSON.stringify(payload) }, token));
        if (!saved || saved.id !== docId || saved.current_content !== savingContent) throw { code: 'invalid_document' };
        setBaseline(saved); state.version_count = saved.version_count;
        if (content !== savingContent) { transition({ saveState: 'dirty', dirty: true, conflict: null }); schedule(); return false; }
        transition({ saveState: 'saved', dirty: false, conflict: null }); return true;
      } catch (error) {
        if (abortError(error) || (error && error.code === 'stale')) return false;
        return fail(error && error.code, true);
      }
    }
    function schedule() { clearTimeout(timer); timer = setTimeout(function () { save().catch(function () {}); }, debounceMs); if (timer && typeof timer.unref === 'function') timer.unref(); }
    async function listVersions() {
      if (!state.workingCopyId) return null;
      var token = sequence, docId = state.workingCopyId;
      try {
        var list = await request('/api/document/' + encodeURIComponent(docId) + '/versions', { method: 'GET' }, token);
        if (!Array.isArray(list) || list.length > MAX_VERSIONS) throw { code: 'invalid_versions' };
        var versions = list.map(function (item) { return versionFrom(item, null, docId, false); });
        if (versions.some(function (item) { return !item; })) throw { code: 'invalid_versions' };
        return versions;
      } catch (error) { if (!abortError(error) && !(error && error.code === 'stale')) fail(error && error.code, state.dirty); return null; }
    }
    async function getVersion(number) {
      if (!state.workingCopyId || !Number.isInteger(number) || number < 1 || number > 1000000) return null;
      var token = sequence, docId = state.workingCopyId;
      try {
        var value = versionFrom(await request('/api/document/' + encodeURIComponent(docId) + '/version/' + number, { method: 'GET' }, token), number, docId, true);
        if (!value) throw { code: 'invalid_version' }; return value;
      } catch (error) { if (!abortError(error) && !(error && error.code === 'stale')) fail(error && error.code, state.dirty); return null; }
    }
    async function showDifference(number) {
      var dm = getModule(['enterDiffMode', 'exitDiffMode']);
      if (!dm || state.viewMode !== 'working_copy') return false;
      var version = await getVersion(number);
      if (!version || destroyed || state.viewMode !== 'working_copy') return false;
      // documentModule restores its first argument on discard.  This bridge is
      // history/read-only, so the current working copy must be that argument.
      dm.enterDiffMode(content, version.content); transition({ viewMode: 'difference' }); return true;
    }
    function closeDifference() {
      var dm = getModule(['exitDiffMode']);
      if (!dm || state.viewMode !== 'difference') return false;
      // Never apply a resolved historical diff: the bridge has no safe way to
      // re-read a partially resolved editor and would otherwise diverge.
      dm.exitDiffMode(true); transition({ viewMode: 'working_copy' }); return true;
    }
    function destroy() { if (destroyed) return; destroyed = true; sequence += 1; clearRequests(); clearTimeout(timer); timer = null; content = ''; baseline = null; sourceRef = null; }
    return { createWorkingCopy: createWorkingCopy, openWorkingCopy: createWorkingCopy, setViewMode: setViewMode,
      setWorkingCopyContent: setWorkingCopyContent, markDirty: markDirty, save: save, listVersions: listVersions,
      getVersion: getVersion, showDifference: showDifference, closeDifference: closeDifference, getState: getState, destroy: destroy };
  }
  return { create: create, MAX_CONTENT_BYTES: MAX_CONTENT_BYTES };
}));
