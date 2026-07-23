const { test, expect } = require('playwright/test');
const Bridge = require('../../static/frontpage-v3/inbox-document-bridge.js');

const doc = (patch = {}) => ({ id: 'a'.repeat(32), current_content: 'initial', version_count: 1,
  updated_at: '2026-07-23T10:00:00Z', title: 'Safe', language: 'markdown',
  working_copy: { schema: 'odysseus.universal_inbox.working_copy.v1', created: true, revision_created: false, working_copy_id: 'a'.repeat(32), version: 1 }, ...patch });
const json = (body, status = 200) => ({ ok: status >= 200 && status < 300, status,
  headers: { get: () => 'application/json; charset=utf-8' }, json: async () => body });
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
