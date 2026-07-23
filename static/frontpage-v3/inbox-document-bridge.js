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
  var MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024;
  var MAX_VERSIONS = 200;
  // Mirrors src/upload_handler.py UPLOAD_ID_RE (the server owns this authority).
  var SOURCE_REF = /^upload:[0-9a-fA-F]{32}(?:\.[A-Za-z0-9]+)?$/;
  var DOC_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
  var REASON = /^[a-z0-9_]{1,64}$/;
  var MODES = { original: true, extraction: true, working_copy: true, difference: true };
  var SAVE_STATES = { idle: true, creating: true, ready: true, dirty: true, saving: true, saved: true, conflict: true, error: true };
  var EXPORT_STATES = { idle: true, downloading: true, downloaded: true, blocked: true, error: true };
  var CAPABILITY_SCHEMA = 'odysseus.universal_inbox.workbench_capability.v1';
  var ACTIONS = ['inspect', 'route_dry_run', 'create_working_copy', 'edit_working_copy', 'download_original', 'export_working_copy'];
  var ACTION_STATES = { allowed: true, review: true, blocked: true, not_supported: true, live_gate_required: true };
  var SAFE_SUFFIX = /^\.[a-z0-9]{1,12}$/;
  var LANGUAGE_EXPORT_TYPES = {
    bash: ['.sh', 'text/x-shellscript'], c: ['.c', 'text/x-c'], cpp: ['.cpp', 'text/x-c++'],
    css: ['.css', 'text/css'], csv: ['.csv', 'text/csv'], email: ['.eml', 'message/rfc822'],
    go: ['.go', 'text/plain'], html: ['.html', 'text/html'], ini: ['.ini', 'text/plain'],
    java: ['.java', 'text/plain'], javascript: ['.js', 'text/javascript'], json: ['.json', 'application/json'],
    markdown: ['.md', 'text/markdown'], php: ['.php', 'text/plain'], plain: ['.txt', 'text/plain'],
    python: ['.py', 'text/x-python'], ruby: ['.rb', 'text/plain'], rust: ['.rs', 'text/plain'],
    sql: ['.sql', 'text/plain'], svg: ['.svg', 'image/svg+xml'], text: ['.txt', 'text/plain'],
    toml: ['.toml', 'text/plain'], typescript: ['.ts', 'text/plain'], xml: ['.xml', 'application/xml'],
    yaml: ['.yml', 'text/yaml']
  };
  var ORIGINAL_TYPES = {
    '.md': ['text/markdown', 'text/plain'], '.markdown': ['text/markdown', 'text/plain'],
    '.txt': ['text/plain'], '.pdf': ['application/pdf'],
    '.docx': ['application/vnd.openxmlformats-officedocument.wordprocessingml.document']
  };

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
  function header(response, name) {
    var value = response && response.headers && typeof response.headers.get === 'function' && response.headers.get(name);
    return typeof value === 'string' ? value : '';
  }
  function exportType(language) {
    var key = typeof language === 'string' ? language.trim().toLowerCase() : 'text';
    return LANGUAGE_EXPORT_TYPES[key] || ['.txt', 'text/plain'];
  }
  function exportSlug(title) {
    var value = typeof title === 'string' ? title.trim() : '';
    value = value.replace(/\.pdf$/i, '').replace(/\s+/g, '_').replace(/[^A-Za-z0-9._-]/g, '').replace(/_+/g, '_').replace(/^_+|_+$/g, '');
    return (value || 'form').slice(0, 96);
  }
  function workingFilename(title, version, extension) {
    var number = Number.isInteger(version) && version > 0 ? version : 1;
    var base = exportSlug(title || 'document');
    if (base.toLowerCase().endsWith(extension)) base = base.slice(0, -extension.length).replace(/[._-]+$/, '') || 'document';
    return base + '-v' + number + extension;
  }
  function pdfFilename(title) { return exportSlug(title || 'form') + '_annotated.pdf'; }
  function headerTypeAllowed(value, allowed) {
    var match = /^([^;\s]+\/[^;\s]+)(?:;\s*charset=utf-8)?$/i.exec((value || '').trim());
    return !!match && allowed.indexOf(match[1].toLowerCase()) !== -1;
  }
  function contentLength(value) {
    if (!/^(?:0|[1-9][0-9]*)$/.test(value || '')) return null;
    var length = Number(value);
    return Number.isSafeInteger(length) && length <= MAX_DOWNLOAD_BYTES ? length : null;
  }
  function dispositionFilename(value, expectedFilename, expectedSuffix) {
    if (typeof value !== 'string' || value.length > 1024 || /[\r\n]/.test(value) || !/^attachment(?:\s*;|$)/i.test(value)) return null;
    var extended = /(?:^|;)\s*filename\*=UTF-8''([^;\s]+)/i.exec(value);
    var basic = /(?:^|;)\s*filename="?([^;"\s]+)"?/i.exec(value);
    if (!extended && !basic) return null;
    var filename;
    try { filename = decodeURIComponent((extended || basic)[1]); } catch (_) { return null; }
    if (!filename || filename.length > 160 || /[\u0000-\u001f\u007f\r\n\\/:*?"<>|]/.test(filename) || filename === '.' || filename === '..') return null;
    if (expectedFilename && !/^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$/.test(filename)) return null;
    if (expectedFilename && filename !== expectedFilename) return null;
    if (expectedSuffix && !filename.toLowerCase().endsWith(expectedSuffix)) return null;
    return filename;
  }
  function capabilityFrom(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value) || value.schema !== CAPABILITY_SCHEMA ||
        value.owner_authorized !== true || value.has_working_copy !== true || value.browser_download_allowed !== true ||
        value.original_immutable !== true || value.working_copy_versioned !== true || value.server_authoritative !== true ||
        value.browser_detection_advisory !== true || value.raw_content_visible !== false || value.absolute_path_visible !== false ||
        value.live_write_authorized !== false || typeof value.source_suffix !== 'string' ||
        !(SAFE_SUFFIX.test(value.source_suffix) || value.source_suffix === 'other') || !Array.isArray(value.actions) || value.actions.length !== ACTIONS.length) return null;
    var decisions = {};
    for (var i = 0; i < value.actions.length; i += 1) {
      var item = value.actions[i];
      if (!item || typeof item !== 'object' || Array.isArray(item) || item.action !== ACTIONS[i] || !ACTION_STATES[item.state] ||
          item.mutates_original !== false || item.performs_live_write !== false || !Array.isArray(item.reason_codes) ||
          !item.reason_codes.length || item.reason_codes.some(function (reason) { return !cleanReason(reason, ''); })) return null;
      decisions[item.action] = item.state;
    }
    return { sourceSuffix: value.source_suffix, decisions: decisions };
  }

  function create(options) {
    options = options || {};
    var fetchImpl = options.fetch || root.fetch;
    var moduleOverride = options.documentModule;
    var debounceMs = Number.isInteger(options.debounceMs) && options.debounceMs >= 0 ? options.debounceMs : 800;
    var state = {
      viewMode: 'original', saveState: 'idle', workingCopyId: null, version_count: 0,
      dirty: false, conflict: null, original_immutable: true, source_ref_redacted: true,
      live_write_authorized: false, exportState: 'idle', exportTarget: null, exportError: null
    };
    var sourceRef = null, baseline = null, content = '', capability = null, documentMeta = null, timer = null, sequence = 0, destroyed = false;
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
    function exportFail(code, target, stateName) {
      transition({ exportState: stateName || 'error', exportTarget: target || null, exportError: cleanReason(code, 'download_failed') });
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
      sourceRef = null; baseline = null; content = ''; capability = null; documentMeta = null;
      state.workingCopyId = null; state.version_count = 0; state.dirty = false; state.conflict = null;
      state.exportState = 'idle'; state.exportTarget = null; state.exportError = null;
    }
    function getState() {
      return {
        viewMode: state.viewMode, saveState: state.saveState, workingCopyId: state.workingCopyId,
        version_count: state.version_count, dirty: state.dirty, conflict: state.conflict,
        original_immutable: true, source_ref_redacted: true, live_write_authorized: false,
        exportState: state.exportState, exportTarget: state.exportTarget, exportError: state.exportError
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
        var workingCopy = data && data.working_copy, parsedCapability = capabilityFrom(data && data.workbench_capability);
        if (!doc || !workingCopy || typeof workingCopy !== 'object' || Array.isArray(workingCopy) ||
            workingCopy.schema !== 'odysseus.universal_inbox.working_copy.v1' || !validId(workingCopy.working_copy_id) ||
            workingCopy.working_copy_id !== doc.id || workingCopy.version !== doc.version_count ||
            typeof workingCopy.created !== 'boolean' || typeof workingCopy.revision_created !== 'boolean' || !parsedCapability) throw { code: 'invalid_document' };
        if (stale(token)) return false;
        content = doc.current_content; setBaseline(doc); capability = parsedCapability;
        documentMeta = { title: doc.title, language: doc.language };
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
      content = next; transition({ dirty: true, saveState: 'dirty', conflict: null, exportState: 'idle', exportTarget: null, exportError: null });
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
    function defaultDownload(blob, filename) {
      var urlApi = root.URL || root.webkitURL;
      var doc = root.document;
      if (!urlApi || typeof urlApi.createObjectURL !== 'function' || typeof urlApi.revokeObjectURL !== 'function' || !doc || typeof doc.createElement !== 'function') throw { code: 'download_unavailable' };
      var url = urlApi.createObjectURL(blob);
      var clicked = false;
      try {
        var link = doc.createElement('a');
        link.href = url; link.download = filename; link.rel = 'noopener';
        if (doc.body && typeof doc.body.appendChild === 'function') doc.body.appendChild(link);
        if (typeof link.click !== 'function') throw { code: 'download_unavailable' };
        link.click();
        clicked = true;
        if (link.parentNode && typeof link.parentNode.removeChild === 'function') link.parentNode.removeChild(link);
      } finally {
        // Let the browser consume the click before revoking, but make that
        // lifecycle bounded and deterministic. Failed clicks revoke at once.
        if (!clicked) urlApi.revokeObjectURL(url);
        else if (typeof root.setTimeout === 'function') root.setTimeout(function () { urlApi.revokeObjectURL(url); }, 1000);
        else urlApi.revokeObjectURL(url);
      }
    }
    function downloadRequirements(target) {
      if (!capability || !state.workingCopyId || capability.decisions[target === 'original' ? 'download_original' : 'export_working_copy'] !== 'allowed') return null;
      if (target === 'working_copy' && (state.dirty || state.saveState === 'dirty' || state.saveState === 'saving' || state.saveState === 'conflict' || state.saveState === 'error')) return null;
      if (target === 'original') {
        var originalTypes = ORIGINAL_TYPES[capability.sourceSuffix];
        if (!originalTypes) return null;
        return {
          path: '/api/universal-inbox/items/' + encodeURIComponent(sourceRef) + '/content?download=true',
          types: originalTypes,
          expectedFilename: null,
          expectedSuffix: capability.sourceSuffix
        };
      }
      var sourceIsPdf = capability.sourceSuffix === '.pdf';
      var type = exportType(documentMeta && documentMeta.language);
      return {
        path: '/api/document/' + encodeURIComponent(state.workingCopyId) + (sourceIsPdf ? '/export-pdf' : '/export'),
        types: [sourceIsPdf ? 'application/pdf' : type[1]],
        expectedFilename: sourceIsPdf ? pdfFilename(documentMeta && documentMeta.title) : workingFilename(documentMeta && documentMeta.title, state.version_count, type[0]),
        expectedSuffix: sourceIsPdf ? '.pdf' : type[0]
      };
    }
    async function download(target) {
      if (destroyed || !EXPORT_STATES[state.exportState] || state.exportState === 'downloading') return false;
      var requirements = downloadRequirements(target);
      if (!requirements) return exportFail(target === 'working_copy' && (state.dirty || state.saveState !== 'ready' && state.saveState !== 'saved') ? 'working_copy_unsaved' : 'download_not_allowed', target, 'blocked');
      var token = sequence;
      var startingVersion = state.version_count;
      transition({ exportState: 'downloading', exportTarget: target, exportError: null });
      var controller = newRequest();
      try {
        if (typeof fetchImpl !== 'function') throw { code: 'fetch_unavailable' };
        var response = await fetchImpl(requirements.path, {
          method: 'GET', credentials: 'same-origin', mode: 'same-origin', redirect: 'error', cache: 'no-store',
          headers: { Accept: requirements.types.join(', ') }, signal: controller && controller.signal
        });
        if (stale(token)) return false;
        if (!response || !response.ok) throw { code: 'download_http_error' };
        if (target === 'original' && (response.status !== 200 || header(response, 'x-odysseus-content-state').trim().toLowerCase() !== 'complete')) throw { code: 'original_incomplete' };
        if (header(response, 'x-content-type-options').trim().toLowerCase() !== 'nosniff') throw { code: 'download_nosniff_required' };
        if (!headerTypeAllowed(header(response, 'content-type'), requirements.types)) throw { code: 'download_content_type_invalid' };
        var length = contentLength(header(response, 'content-length'));
        if (length === null) throw { code: 'download_length_invalid' };
        var encoding = header(response, 'content-encoding').trim().toLowerCase();
        if (encoding && encoding !== 'identity' && encoding !== 'gzip' && encoding !== 'br') throw { code: 'download_encoding_invalid' };
        var filename = dispositionFilename(header(response, 'content-disposition'), requirements.expectedFilename, requirements.expectedSuffix);
        if (!filename || typeof response.blob !== 'function') throw { code: 'download_disposition_invalid' };
        var blob = await response.blob();
        if (stale(token)) return false;
        if (!blob || !Number.isInteger(blob.size) || blob.size < 0 || blob.size > MAX_DOWNLOAD_BYTES || ((!encoding || encoding === 'identity') && blob.size !== length)) throw { code: 'download_size_invalid' };
        if (target === 'working_copy' && (state.version_count !== startingVersion || !downloadRequirements('working_copy'))) return exportFail('working_copy_changed', target, 'blocked');
        var sink = typeof options.downloadSink === 'function' ? options.downloadSink : defaultDownload;
        await sink(blob, filename, target);
        if (stale(token)) return false;
        transition({ exportState: 'downloaded', exportTarget: target, exportError: null });
        return true;
      } catch (error) {
        if (abortError(error) || (error && error.code === 'stale')) return false;
        return exportFail(error && error.code, target, 'error');
      } finally {
        if (controller) controllers = controllers.filter(function (item) { return item !== controller; });
      }
    }
    function downloadOriginal() { return download('original'); }
    function exportWorkingCopy() { return download('working_copy'); }
    function destroy() { if (destroyed) return; destroyed = true; sequence += 1; clearRequests(); clearTimeout(timer); timer = null; content = ''; baseline = null; sourceRef = null; capability = null; documentMeta = null; }
    return { createWorkingCopy: createWorkingCopy, openWorkingCopy: createWorkingCopy, setViewMode: setViewMode,
      setWorkingCopyContent: setWorkingCopyContent, markDirty: markDirty, save: save, listVersions: listVersions,
      getVersion: getVersion, showDifference: showDifference, closeDifference: closeDifference, downloadOriginal: downloadOriginal,
      exportWorkingCopy: exportWorkingCopy, getState: getState, destroy: destroy };
  }
  return { create: create, MAX_CONTENT_BYTES: MAX_CONTENT_BYTES };
}));
