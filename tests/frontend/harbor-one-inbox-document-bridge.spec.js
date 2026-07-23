const { test, expect } = require('playwright/test');
const Bridge = require('../../static/frontpage-v3/inbox-document-bridge.js');

const capability = (suffix = '.pdf') => ({ schema: 'odysseus.universal_inbox.workbench_capability.v1', source_suffix: suffix,
  server_family: suffix === '.pdf' ? 'document' : 'text', mvp_tier: 'p0', browser_hint: 'not_provided', browser_hint_relation: 'not_provided',
  owner_authorized: true, has_working_copy: true, browser_download_allowed: true, original_immutable: true, working_copy_versioned: true,
  server_authoritative: true, browser_detection_advisory: true, raw_content_visible: false, absolute_path_visible: false, live_write_authorized: false,
  actions: ['inspect', 'route_dry_run', 'create_working_copy', 'edit_working_copy', 'download_original', 'export_working_copy'].map(action => ({
    action, state: 'allowed', reason_codes: ['local_browser_working_copy_export'], mutates_original: false, performs_live_write: false
  })) });
const doc = (patch = {}) => ({ id: 'a'.repeat(32), current_content: 'initial', version_count: 1,
  updated_at: '2026-07-23T10:00:00Z', title: 'Safe', language: 'markdown',
  working_copy: { schema: 'odysseus.universal_inbox.working_copy.v1', created: true, revision_created: false, working_copy_id: 'a'.repeat(32), version: 1 }, workbench_capability: capability(), ...patch });
const json = (body, status = 200) => ({ ok: status >= 200 && status < 300, status,
  headers: { get: name => name.toLowerCase() === 'content-type' ? 'application/json; charset=utf-8' : null }, json: async () => body });
const binary = ({ type, filename, body = 'data', length = body.length, encoding = '', nosniff = 'nosniff', disposition, status = 200, contentState = 'complete' }) => ({
  ok: status >= 200 && status < 300, status, headers: { get: name => ({ 'content-type': type, 'content-disposition': disposition || `attachment; filename="${filename}"`, 'content-length': String(length), 'content-encoding': encoding, 'x-content-type-options': nosniff, 'x-odysseus-content-state': contentState }[name.toLowerCase()] || null) },
  blob: async () => new Blob([body], { type })
});
const source = `upload:${'b'.repeat(32)}.pdf`;
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }

test('creates through the owner-scoped API, injects fresh doc, and projects only safe state', async () => {
  const calls = [], injected = [];
  const bridge = Bridge.create({ documentModule: { injectFreshDoc: d => injected.push(d) }, fetch: async (url, init) => {
    calls.push({ url, init }); return json(doc());
  }});
  await expect(bridge.createWorkingCopy(source)).resolves.toBe(true);
  expect(calls[0].url).toBe(`/api/universal-inbox/items/${encodeURIComponent(source)}/working-copy`);
  expect(calls[0].init.method).toBe('POST'); expect(calls[0].init.body).toBe('{"new_revision":false}');
  expect(calls[0].init.credentials).toBe('same-origin'); expect(calls[0].init.mode).toBe('same-origin');
  expect(calls[0].init.redirect).toBe('error'); expect(calls[0].init.cache).toBe('no-store');
  expect(injected).toHaveLength(1); expect(bridge.getState()).toEqual(expect.objectContaining({ viewMode: 'working_copy', saveState: 'ready', source_ref_redacted: true, original_immutable: true, live_write_authorized: false }));
  expect(JSON.stringify(bridge.getState())).not.toContain(source); expect(Object.values(bridge.getState()).some(v => typeof v === 'function')).toBeFalsy();
});

test('rejects unsafe/foreign-like inputs and immutable-mode edits without exposing content', async () => {
  const events = []; let calls = 0;
  const bridge = Bridge.create({ documentModule: { loadDocument: async () => {} }, onChange: s => { events.push(s); throw new Error('ignored'); }, fetch: async () => { calls++; return json(doc()); } });
  await expect(bridge.createWorkingCopy('upload:foreign/path')).resolves.toBe(false);
  await expect(bridge.createWorkingCopy(`upload:${'c'.repeat(32)}.a_b`)).resolves.toBe(false);
  expect(calls).toBe(0); expect(bridge.getState().saveState).toBe('error'); expect(JSON.stringify(events)).not.toContain('foreign/path');
  expect(bridge.setWorkingCopyContent('secret')).toBeFalsy(); expect(bridge.setViewMode('extraction')).toBeTruthy(); expect(bridge.setWorkingCopyContent('secret')).toBeFalsy();
});

test('dirty save preflights then saves, detects every baseline mismatch, and preserves changed content', async () => {
  let puts = 0; const bridge = Bridge.create({ debounceMs: 999999, documentModule: { loadDocument: async () => {} }, fetch: async (url, init) => {
    if (url.includes('working-copy')) return json(doc());
    if (init.method === 'GET') return json(doc());
    puts++; return json(doc({ current_content: 'edit', version_count: 2, updated_at: '2026-07-23T10:01:00Z' }));
  }});
  await bridge.createWorkingCopy(source); bridge.setWorkingCopyContent('edit');
  await expect(bridge.save('x')).resolves.toBe(true); expect(puts).toBe(1); expect(bridge.getState().saveState).toBe('saved');
  for (const mismatch of [
    { version_count: 2 }, { updated_at: '2026-07-23T10:02:00Z' }, { current_content: 'remote content' }
  ]) {
    let mismatchPuts = 0;
    const candidate = Bridge.create({ debounceMs: 999999, documentModule: { loadDocument: async () => {} }, fetch: async (url, init) => {
      if (url.includes('working-copy')) return json(doc());
      if (init.method === 'GET') return json(doc(mismatch));
      mismatchPuts++; return json(doc());
    }});
    await candidate.createWorkingCopy(source); candidate.setWorkingCopyContent('next');
    await expect(candidate.save()).resolves.toBe(false);
    expect(mismatchPuts).toBe(0); expect(candidate.getState()).toEqual(expect.objectContaining({ saveState: 'conflict', dirty: true, conflict: 'remote_changed' }));
    candidate.destroy();
  }
});

test('change during save stays dirty and failed saves keep local working-copy content', async () => {
  const put = deferred(); let stage = 0;
  const savedFirst = doc({ current_content: 'first', version_count: 2, updated_at: '2026-07-23T10:01:00Z' });
  const bridge = Bridge.create({ debounceMs: 999999, documentModule: { loadDocument: async () => {} }, fetch: async (url, init) => {
    if (url.includes('working-copy')) return json(doc()); if (init.method === 'GET') return json(stage ? savedFirst : doc());
    if (stage++ === 0) return put.promise; return json({}, 503);
  }});
  await bridge.createWorkingCopy(source); bridge.setWorkingCopyContent('first'); const saving = bridge.save(); bridge.setWorkingCopyContent('second'); put.resolve(json(savedFirst));
  await saving; expect(bridge.getState()).toEqual(expect.objectContaining({ saveState: 'dirty', dirty: true }));
  await bridge.save(); expect(bridge.getState()).toEqual(expect.objectContaining({ saveState: 'error', dirty: true }));
});

test('versions are bounded and only the document diff adapter is used', async () => {
  const calls = []; const adapter = { loadDocument: async () => {}, enterDiffMode: (...args) => calls.push(args), exitDiffMode: discard => calls.push(['exit', discard]) };
  const bridge = Bridge.create({ documentModule: adapter, fetch: async (url) => {
    if (url.includes('working-copy')) return json(doc()); if (url.endsWith('/versions')) return json([{ id: 'c'.repeat(32), version_number: 1, content: 'old', summary: '', source: 'upload', created_at: '2026-07-23T10:00:00Z' }]);
    if (url.includes('/version/1')) return json({ id: 'c'.repeat(32), document_id: 'a'.repeat(32), version_number: 1, content: 'old', summary: '', source: 'upload', created_at: '2026-07-23T10:00:00Z' }); return json(doc());
  }});
  await bridge.createWorkingCopy(source); await expect(bridge.listVersions()).resolves.toHaveLength(1); expect(bridge.setViewMode('difference')).toBeFalsy(); await expect(bridge.showDifference(1)).resolves.toBe(true);
  expect(calls[0]).toEqual(['initial', 'old']); expect(bridge.getState().viewMode).toBe('difference'); expect(bridge.closeDifference(false)).toBeTruthy();
  expect(calls[1]).toEqual(['exit', true]);
});

test('malformed/oversized responses and stale destroy are fail-closed', async () => {
  const pending = deferred();
  const bridge = Bridge.create({ documentModule: { loadDocument: async () => {} }, fetch: async () => pending.promise });
  const opening = bridge.createWorkingCopy(source); bridge.destroy(); pending.resolve(json(doc())); await expect(opening).resolves.toBe(false);
  const broken = Bridge.create({ documentModule: { loadDocument: async () => {} }, fetch: async () => json(doc({ current_content: 'x'.repeat(Bridge.MAX_CONTENT_BYTES + 1) })) });
  await expect(broken.createWorkingCopy(source)).resolves.toBe(false); expect(broken.getState().saveState).toBe('error');
  const missingContract = Bridge.create({ documentModule: { loadDocument: async () => {} }, fetch: async () => json({ id: 'a'.repeat(32), current_content: 'initial', version_count: 1, updated_at: '2026-07-23T10:00:00Z' }) });
  await expect(missingContract.createWorkingCopy(source)).resolves.toBe(false); expect(missingContract.getState().saveState).toBe('error');
});

test('downloads are explicit, owner-scoped browser requests, and select original, PDF, or generic working-copy endpoints', async () => {
  const calls = [], delivered = [];
  const bridge = Bridge.create({ documentModule: { loadDocument: async () => {} }, downloadSink: (...args) => delivered.push(args), fetch: async (url, init) => {
    calls.push({ url, init });
    if (url.includes('working-copy')) return json(doc());
    if (url.includes('/content?download=true')) return binary({ type: 'application/pdf', filename: 'source.pdf', body: 'original' });
    return binary({ type: 'application/pdf', filename: 'Safe_annotated.pdf', body: 'filled' });
  }});
  await bridge.createWorkingCopy(source);
  expect(calls).toHaveLength(1); // creation never auto-fetches or downloads
  expect(bridge).not.toHaveProperty('exportToNextcloud');
  expect(bridge.getState().live_write_authorized).toBeFalsy();
  await expect(bridge.downloadOriginal()).resolves.toBe(true);
  await expect(bridge.exportWorkingCopy()).resolves.toBe(true);
  expect(calls.slice(1).map(call => call.url)).toEqual([
    `/api/universal-inbox/items/${encodeURIComponent(source)}/content?download=true`,
    `/api/document/${'a'.repeat(32)}/export-pdf`
  ]);
  expect(calls.slice(1).every(call => call.init.credentials === 'same-origin' && call.init.mode === 'same-origin' && call.init.redirect === 'error' && call.init.cache === 'no-store')).toBeTruthy();
  expect(delivered.map(item => item.slice(1))).toEqual([['source.pdf', 'original'], ['Safe_annotated.pdf', 'working_copy']]);
  expect(JSON.stringify(bridge.getState())).not.toContain(source);

  const markdownSource = `upload:${'c'.repeat(32)}.md`, genericCalls = [];
  const generic = Bridge.create({ documentModule: { loadDocument: async () => {} }, downloadSink: () => {}, fetch: async (url) => {
    genericCalls.push(url);
    if (url.includes('working-copy')) return json(doc({ title: 'notes.md', workbench_capability: capability('.md') }));
    return binary({ type: 'text/markdown; charset=utf-8', filename: 'notes-v1.md', body: 'copy' });
  }});
  await generic.createWorkingCopy(markdownSource); await expect(generic.exportWorkingCopy()).resolves.toBe(true);
  expect(genericCalls[1]).toBe(`/api/document/${'a'.repeat(32)}/export`);

  const unicodeNames = [];
  const unicodeOriginal = Bridge.create({ documentModule: { loadDocument: async () => {} }, downloadSink: (_blob, filename) => unicodeNames.push(filename), fetch: async url => {
    if (url.includes('working-copy')) return json(doc());
    return binary({ type: 'application/pdf', filename: 'source.pdf', body: 'original', disposition: "attachment; filename=source.pdf; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf" });
  }});
  await unicodeOriginal.createWorkingCopy(source); await expect(unicodeOriginal.downloadOriginal()).resolves.toBe(true);
  expect(unicodeNames).toEqual(['résumé.pdf']);

  const longTitle = 'a'.repeat(120), longPdf = Bridge.create({ documentModule: { loadDocument: async () => {} }, downloadSink: () => {}, fetch: async url => {
    if (url.includes('working-copy')) return json(doc({ title: longTitle }));
    return binary({ type: 'application/pdf', filename: `${'a'.repeat(96)}_annotated.pdf`, body: 'filled' });
  }});
  await longPdf.createWorkingCopy(source); await expect(longPdf.exportWorkingCopy()).resolves.toBe(true);
});

test('rejects unsafe binary headers and size mismatches, but accepts bounded compressed transfer responses', async () => {
  const reasons = [];
  const bridge = Bridge.create({ documentModule: { loadDocument: async () => {} }, downloadSink: () => {}, onChange: state => reasons.push(state.exportError), fetch: async (url) => {
    if (url.includes('working-copy')) return json(doc());
    return binary({ type: 'application/pdf', filename: 'Safe_annotated.pdf', body: 'long decoded body', length: 3, encoding: 'gzip' });
  }});
  await bridge.createWorkingCopy(source);
  await expect(bridge.exportWorkingCopy()).resolves.toBe(true); // gzip transfer length need not equal decoded Blob size

  const invalid = Bridge.create({ documentModule: { loadDocument: async () => {} }, downloadSink: () => { throw new Error('must not deliver'); }, fetch: async (url) => {
    if (url.includes('working-copy')) return json(doc());
    return binary({ type: 'text/html', filename: 'Safe_annotated.pdf', body: 'bad', nosniff: 'missing' });
  }});
  await invalid.createWorkingCopy(source); await expect(invalid.exportWorkingCopy()).resolves.toBe(false);
  expect(invalid.getState()).toEqual(expect.objectContaining({ exportState: 'error', exportTarget: 'working_copy', exportError: 'download_nosniff_required' }));

  const wrongMime = Bridge.create({ documentModule: { loadDocument: async () => {} }, downloadSink: () => {}, fetch: async (url) => {
    if (url.includes('working-copy')) return json(doc());
    return binary({ type: 'text/html', filename: 'Safe_annotated.pdf', body: 'bad' });
  }});
  await wrongMime.createWorkingCopy(source); await expect(wrongMime.exportWorkingCopy()).resolves.toBe(false);
  expect(wrongMime.getState().exportError).toBe('download_content_type_invalid');

  const mismatch = Bridge.create({ documentModule: { loadDocument: async () => {} }, downloadSink: () => {}, fetch: async (url) => {
    if (url.includes('working-copy')) return json(doc());
    return binary({ type: 'application/pdf', filename: 'Safe_annotated.pdf', body: 'four', length: 3 });
  }});
  await mismatch.createWorkingCopy(source); await expect(mismatch.exportWorkingCopy()).resolves.toBe(false);
  expect(mismatch.getState().exportError).toBe('download_size_invalid');

  const partialOriginal = Bridge.create({ documentModule: { loadDocument: async () => {} }, downloadSink: () => { throw new Error('must not deliver'); }, fetch: async url => {
    if (url.includes('working-copy')) return json(doc());
    return binary({ type: 'application/pdf', filename: 'source.pdf', body: 'part', status: 206, contentState: 'partial' });
  }});
  await partialOriginal.createWorkingCopy(source); await expect(partialOriginal.downloadOriginal()).resolves.toBe(false);
  expect(partialOriginal.getState()).toEqual(expect.objectContaining({ exportState: 'error', exportTarget: 'original', exportError: 'original_incomplete' }));
});

test('working-copy export neither saves nor downloads when dirty or conflicted, and refuses a changed in-flight copy', async () => {
  let calls = 0;
  const bridge = Bridge.create({ debounceMs: 999999, documentModule: { loadDocument: async () => {} }, downloadSink: () => { throw new Error('must not deliver'); }, fetch: async (url) => {
    calls++; return json(doc());
  }});
  await bridge.createWorkingCopy(source); bridge.setWorkingCopyContent('edited');
  await expect(bridge.exportWorkingCopy()).resolves.toBe(false);
  expect(calls).toBe(1); expect(bridge.getState()).toEqual(expect.objectContaining({ exportState: 'blocked', exportError: 'working_copy_unsaved', dirty: true }));

  const pending = deferred(), delivered = [];
  const changed = Bridge.create({ documentModule: { loadDocument: async () => {} }, downloadSink: () => delivered.push('delivered'), fetch: async (url) => {
    if (url.includes('working-copy')) return json(doc());
    return pending.promise;
  }});
  await changed.createWorkingCopy(source); const exporting = changed.exportWorkingCopy();
  changed.setWorkingCopyContent('new local text'); pending.resolve(binary({ type: 'application/pdf', filename: 'Safe_annotated.pdf', body: 'filled' }));
  await expect(exporting).resolves.toBe(false); expect(delivered).toEqual([]);
  expect(changed.getState()).toEqual(expect.objectContaining({ exportState: 'blocked', exportError: 'working_copy_changed', dirty: true }));

  let conflictCalls = 0;
  const conflicted = Bridge.create({ debounceMs: 999999, documentModule: { loadDocument: async () => {} }, downloadSink: () => { throw new Error('must not deliver'); }, fetch: async (url, init) => {
    conflictCalls++;
    if (url.includes('working-copy')) return json(doc());
    if (init.method === 'GET') return json(doc({ version_count: 2 }));
    throw new Error('binary fetch must not happen');
  }});
  await conflicted.createWorkingCopy(source); conflicted.setWorkingCopyContent('conflicting edit'); await conflicted.save();
  await expect(conflicted.exportWorkingCopy()).resolves.toBe(false);
  expect(conflictCalls).toBe(2); expect(conflicted.getState()).toEqual(expect.objectContaining({ saveState: 'conflict', exportState: 'blocked', exportError: 'working_copy_unsaved' }));
});

test('default download sink clicks and removes an anchor, then revokes its object URL on the bounded timer', async () => {
  const previous = { URL: global.URL, document: global.document, setTimeout: global.setTimeout };
  const lifecycle = { clicked: 0, appended: 0, removed: 0, revoked: [], timer: null };
  const anchor = { click: () => { lifecycle.clicked++; }, parentNode: { removeChild: () => { lifecycle.removed++; } } };
  global.URL = { createObjectURL: () => 'blob:test', revokeObjectURL: url => lifecycle.revoked.push(url) };
  global.document = { createElement: () => anchor, body: { appendChild: () => { lifecycle.appended++; } } };
  global.setTimeout = (callback, delay) => { lifecycle.timer = { callback, delay }; return 1; };
  try {
    const bridge = Bridge.create({ documentModule: { loadDocument: async () => {} }, fetch: async url => {
      if (url.includes('working-copy')) return json(doc({ workbench_capability: capability('.md') }));
      return binary({ type: 'text/markdown; charset=utf-8', filename: 'Safe-v1.md', body: 'copy' });
    }});
    await bridge.createWorkingCopy(`upload:${'f'.repeat(32)}.md`);
    await expect(bridge.exportWorkingCopy()).resolves.toBe(true);
    expect(lifecycle).toEqual(expect.objectContaining({ clicked: 1, appended: 1, removed: 1 }));
    expect(lifecycle.revoked).toEqual([]); expect(lifecycle.timer.delay).toBe(1000);
    lifecycle.timer.callback(); expect(lifecycle.revoked).toEqual(['blob:test']);
  } finally {
    global.URL = previous.URL; global.document = previous.document; global.setTimeout = previous.setTimeout;
  }
});
