const test = require('node:test');
const assert = require('node:assert/strict');
const { webcrypto } = require('node:crypto');
const { ContextApiClient, ContextApiError } = require('../../static/frontpage-v3/context-api.js');

if (!globalThis.crypto) globalThis.crypto = webcrypto;

const encoder = new TextEncoder();
const digest = 'a'.repeat(64);
const pageDigest = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad';
const emptyDigest = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';
const token = `pc1.${'A'.repeat(43)}.${digest}`;
const forgeSelection = [{
  source: 'forge', selection_ref: 'forge.source.1', revision_ref: 'pv_11111111111111111111111111111111',
  forge_locator: { repo_id: 'demo', version_id: 'pv_11111111111111111111111111111111', path: 'README.md' },
}];
const mixedSelection = [
  forgeSelection[0],
  { source: 'knowledge', selection_ref: 'knowledge.source.1', revision_ref: 'knowledge-head.1' },
  { source: 'planning', selection_ref: 'planning.source.1', revision_ref: 'planning-head.1' },
];
const dataBySource = { forge: encoder.encode('abc'), knowledge: encoder.encode('abc'), planning: encoder.encode('abc') };

async function sha256(bytes) {
  const value = new Uint8Array(await webcrypto.subtle.digest('SHA-256', bytes));
  return Array.from(value, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

function headers(values = {}) {
  const normalized = new Map(Object.entries(values).map(([key, value]) => [key.toLowerCase(), String(value)]));
  return { get(name) { return normalized.get(String(name).toLowerCase()) || null; } };
}

function streamBody(chunks, controls = {}) {
  let index = 0;
  return { getReader() { return {
    async read() {
      if (controls.readError) throw new Error('provider secret');
      return index < chunks.length ? { done: false, value: chunks[index++] } : { done: true, value: undefined };
    },
    cancel() {
      controls.cancelled = true;
      controls.cancelCalls = (controls.cancelCalls || 0) + 1;
      index = chunks.length;
      if (controls.cancelSyncError) throw new Error('cancel secret');
      if (controls.cancelError) return Promise.reject(new Error('cancel secret'));
      return Promise.resolve();
    },
    releaseLock() {},
  }; } };
}

function jsonResponse(payload, status = 200, controls = {}) {
  const bytes = encoder.encode(JSON.stringify(payload));
  const split = Math.max(1, Math.floor(bytes.byteLength / 2));
  return {
    ok: status >= 200 && status < 300, status,
    headers: headers({ 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', 'content-length': bytes.byteLength }),
    body: streamBody([bytes.slice(0, split), bytes.slice(split)], controls),
    text() { throw new Error('text must not be used'); },
  };
}

function metadataResponse(currentSelection = mixedSelection, mutate = (value) => value) {
  const items = currentSelection.map((target) => ({
    source: target.source, selection_ref: target.selection_ref, revision_ref: target.revision_ref,
    boundary_sha256: digest, record_sha256: digest, payload_sha256: target.forge_locator?.path === 'empty.txt' ? emptyDigest : pageDigest,
    classification: 'public', provenance_ref: `${target.source}.provenance.1`,
  }));
  return jsonResponse(mutate({
    selection: currentSelection, items,
    targets: currentSelection.map((target, targetIndex) => ({ target_index: targetIndex, manifest_digest: target.source === 'knowledge' ? `sha256:${digest}` : digest })),
    requires_private_confirmation: false, manifest_digest: null,
  }));
}

function binaryResponse(bytes, target, options = {}) {
  const whole = options.whole || digest;
  const offset = options.offset === undefined ? 0 : options.offset;
  const selected = bytes.slice(offset, options.end === undefined ? bytes.byteLength : options.end);
  const pageDigest = options.page || digest;
  const native = target.source === 'knowledge' ? `sha256:${digest}` : digest;
  const controls = options.controls || {};
  return {
    ok: true, status: 200,
    headers: headers({
      'content-type': 'application/octet-stream', 'cache-control': 'no-store',
      'x-context-total': bytes.byteLength, 'x-context-offset': options.headerOffset === undefined ? offset : options.headerOffset,
      'x-context-end': (options.headerOffset === undefined ? offset : options.headerOffset) + selected.byteLength,
      'x-context-whole-sha256': whole, 'x-context-page-sha256': pageDigest,
      'x-context-manifest-sha256': options.manifest || native,
      'x-context-byteform': target.source === 'forge' ? 'git_blob' : target.source === 'planning' ? 'durable_revision_document' : 'knowledge_payload',
      ...options.headers,
    }),
    body: streamBody(options.chunks || [selected.slice(0, 1), selected.slice(1)], controls),
  };
}

function packItems(selectionValue) {
  return selectionValue.map((target) => ({
    source: target.source, selection_ref: target.selection_ref, revision_ref: target.revision_ref,
    boundary_sha256: digest, record_sha256: digest, payload_sha256: pageDigest,
    classification: 'public', provenance_ref: `${target.source}.provenance.1`,
  }));
}

async function packPayload(selectionValue, items, options = {}) {
  const targetIndex = options.targetIndex === undefined ? 0 : options.targetIndex;
  const target = selectionValue[targetIndex];
  const item = items.find((candidate) => candidate.source === target.source && candidate.selection_ref === target.selection_ref && candidate.revision_ref === target.revision_ref);
  const bytes = options.bytes || dataBySource[target.source];
  const pageSha = options.pageSha || `sha256:${await sha256(bytes)}`;
  const complete = options.complete === undefined ? targetIndex === selectionValue.length - 1 && (options.end === undefined ? bytes.byteLength : options.end) === (options.total === undefined ? bytes.byteLength : options.total) : options.complete;
  return {
    schema: 'odysseus.context_pack.v1', pack_id: options.packId || `sha256:${'b'.repeat(64)}`,
    items: items.map((candidate) => ({ ...candidate })),
    page: {
      target_index: targetIndex, offset: options.offset === undefined ? 0 : options.offset,
      end: options.end === undefined ? bytes.byteLength : options.end,
      total: options.total === undefined ? bytes.byteLength : options.total,
      whole_sha256: options.wholeSha || `sha256:${item.payload_sha256}`,
      page_sha256: pageSha,
      byteform: target.source === 'forge' ? 'git_blob' : target.source === 'planning' ? 'durable_revision_document' : 'knowledge_payload',
      content_b64: Buffer.from(bytes).toString('base64'),
    },
    next_cursor: complete ? '' : (options.nextCursor || 'Q'), complete,
    read_only: true, authority_granted: false, send_performed: false,
  };
}

test('mixed three-domain metadata streams, preserves native digests, and never auto-sends challenge', async () => {
  const calls = [];
  const client = new ContextApiClient({ fetchImpl: async (path, options) => {
    calls.push({ path, options, body: JSON.parse(options.body) });
    if (path === '/api/context/challenge') return jsonResponse({ challenge_token: token, command: `/context-disclose ${token}`, expires_at: '2026-10-04T12:00:00Z' });
    return options.body.includes('forge_locators') ? metadataResponse(forgeSelection) : metadataResponse();
  } });
  const metadata = await client.metadata({ chatSessionId: 'chat-owned', selection: mixedSelection });
  const bootstrapped = await client.bootstrapForge({ chatSessionId: 'chat-owned', forgeLocators: [forgeSelection[0].forge_locator] });
  const challenge = await client.challenge({ chatSessionId: 'chat-owned', metadata });
  assert.deepEqual(calls.map((call) => call.path), ['/api/context/metadata', '/api/context/metadata', '/api/context/challenge']);
  assert.deepEqual(metadata.selection.map((item) => item.source), ['forge', 'knowledge', 'planning']);
  assert.equal(metadata.targets[1].manifest_digest, `sha256:${digest}`);
  assert.deepEqual(bootstrapped.selection, forgeSelection);
  assert.equal(challenge.command, `/context-disclose ${token}`);
  assert.equal(calls.filter((call) => call.path === '/api/context/challenge').length, 1);
  assert.equal(calls.some((call) => call.path.includes('chat')), false);
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[0].options.redirect, 'error');
  assert.deepEqual(calls[0].options.headers, { Accept: 'application/json', 'Content-Type': 'application/json' });
  assert.equal(calls[0].body.chat_session_id, 'chat-owned');
  assert.deepEqual(calls[0].body, { chat_session_id: 'chat-owned', selection: mixedSelection });
  assert.equal(calls[1].path, '/api/context/metadata');
  assert.deepEqual(calls[1].body, { chat_session_id: 'chat-owned', forge_locators: [forgeSelection[0].forge_locator] });
  assert.deepEqual(calls[2].body, { chat_session_id: 'chat-owned', selection: mixedSelection });
  for (const call of calls) {
    assert.equal(call.options.method, 'POST');
    assert.equal(call.options.credentials, 'same-origin');
    assert.equal(call.options.redirect, 'error');
    assert.equal(call.options.cache, 'no-store');
    assert.deepEqual(call.options.headers, { Accept: 'application/json', 'Content-Type': 'application/json' });
    assert.ok(call.options.signal instanceof AbortSignal);
  }
});

test('all selected domains require whole hash equal to captured payload and exact requested offset', async () => {
  const client = new ContextApiClient({ fetchImpl: async (path, options) => {
    if (path === '/api/context/metadata') return metadataResponse();
    const body = JSON.parse(options.body);
    assert.equal(path, '/api/context/page');
    assert.deepEqual(body.selection, mixedSelection);
    assert.equal(body.chat_session_id, 'chat-owned');
    assert.equal(body.expected_manifest_digest, body.target_index === 1 ? `sha256:${digest}` : digest);
    const target = mixedSelection[body.target_index];
    const bytes = dataBySource[target.source];
    return binaryResponse(bytes, target, { whole: await sha256(bytes), page: await sha256(bytes) });
  } });
  const metadata = await client.metadata({ chatSessionId: 'chat-owned', selection: mixedSelection });
  for (let targetIndex = 0; targetIndex < mixedSelection.length; targetIndex += 1) {
    const page = await client.page({ chatSessionId: 'chat-owned', metadata, targetIndex, offset: 0, limit: 16 });
    assert.equal(page.offset, 0);
    assert.equal(page.wholeSha256, pageDigest);
  }
  const badOffsetClient = new ContextApiClient({ fetchImpl: async (path) => path === '/api/context/metadata' ? metadataResponse(forgeSelection) : binaryResponse(dataBySource.forge, forgeSelection[0], { whole: pageDigest, page: pageDigest, headerOffset: 1 }) });
  const badMetadata = await badOffsetClient.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection });
  await assert.rejects(badOffsetClient.page({ chatSessionId: 'chat-owned', metadata: badMetadata, targetIndex: 0, offset: 0, limit: 3 }), (error) => error.code === 'invalid_response');
});

test('empty pages, malformed headers, native form mismatches, and page hash failures are closed', async () => {
  const empty = new Uint8Array(0);
  const emptyHash = await sha256(empty);
  const emptySelection = [{ ...forgeSelection[0], selection_ref: 'forge.empty.1', revision_ref: 'pv_22222222222222222222222222222222', forge_locator: { ...forgeSelection[0].forge_locator, version_id: 'pv_22222222222222222222222222222222', path: 'empty.txt' } }];
  const emptyClient = new ContextApiClient({ fetchImpl: async (path) => path === '/api/context/metadata' ? metadataResponse(emptySelection) : binaryResponse(empty, emptySelection[0], { whole: emptyDigest, page: emptyHash }) });
  const emptyMetadata = await emptyClient.metadata({ chatSessionId: 'chat-owned', selection: emptySelection });
  const emptyPage = await emptyClient.page({ chatSessionId: 'chat-owned', metadata: emptyMetadata, targetIndex: 0, offset: 0, limit: 1 });
  assert.equal(emptyPage.bytes.byteLength, 0);

  const badClient = new ContextApiClient({ fetchImpl: async (path) => path === '/api/context/metadata' ? metadataResponse(forgeSelection) : binaryResponse(dataBySource.forge, forgeSelection[0], { whole: 'b'.repeat(64), page: 'c'.repeat(64), manifest: `sha256:${digest}` }) });
  const metadata = await badClient.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection });
  await assert.rejects(badClient.page({ chatSessionId: 'chat-owned', metadata, targetIndex: 0, offset: 0, limit: 3 }), (error) => error.code === 'native_digest_mismatch');
  await assert.rejects(badClient.page({ chatSessionId: 'chat-owned', metadata, targetIndex: 0, offset: 1, limit: 3 }), (error) => error.code === 'invalid_response');

  const malformedClient = new ContextApiClient({ fetchImpl: async (path) => path === '/api/context/metadata' ? metadataResponse(forgeSelection) : binaryResponse(dataBySource.forge, forgeSelection[0], { whole: pageDigest, page: pageDigest, manifest: digest }) });
  const malformedMetadata = await malformedClient.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection });
  const originalFetch = malformedClient._fetch;
  malformedClient._fetch = async (path, options) => path === '/api/context/metadata' ? originalFetch(path, options) : ({ ...binaryResponse(dataBySource.forge, forgeSelection[0], { whole: pageDigest, page: pageDigest, manifest: digest }), headers: headers({ 'content-type': 'text/plain', 'cache-control': 'no-store' }) });
  await assert.rejects(malformedClient.page({ chatSessionId: 'chat-owned', metadata: malformedMetadata, targetIndex: 0, offset: 0, limit: 3 }), (error) => error.code === 'invalid_response');
});

test('JSON and binary streams are bounded and cancelled on overrun or read failure', async () => {
  const jsonControls = { cancelError: true };
  const oversized = new Uint8Array(256 * 1024 + 1);
  const jsonClient = new ContextApiClient({ fetchImpl: async () => ({ ok: true, status: 200, headers: headers({ 'content-type': 'application/json', 'cache-control': 'no-store' }), body: streamBody([oversized], jsonControls) }) });
  await assert.rejects(jsonClient.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection }), (error) => error.code === 'response_too_large');
  assert.equal(jsonControls.cancelled, true);
  assert.equal(jsonControls.cancelCalls, 1);

  const binaryControls = { cancelError: true };
  const binaryClient = new ContextApiClient({ fetchImpl: async (path) => path === '/api/context/metadata' ? metadataResponse(forgeSelection) : binaryResponse(dataBySource.forge, forgeSelection[0], { whole: digest, page: digest, controls: binaryControls, chunks: [new Uint8Array(2), new Uint8Array(2)] }) });
  const metadata = await binaryClient.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection });
  await assert.rejects(binaryClient.page({ chatSessionId: 'chat-owned', metadata, targetIndex: 0, offset: 0, limit: 1 }), (error) => error.code === 'response_too_large');
  assert.equal(binaryControls.cancelled, true);
  assert.equal(binaryControls.cancelCalls, 1);

  const readControls = { readError: true };
  const readClient = new ContextApiClient({ fetchImpl: async (path) => path === '/api/context/metadata' ? metadataResponse(forgeSelection) : binaryResponse(dataBySource.forge, forgeSelection[0], { whole: digest, page: digest, controls: readControls }) });
  const readMetadata = await readClient.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection });
  await assert.rejects(readClient.page({ chatSessionId: 'chat-owned', metadata: readMetadata, targetIndex: 0, offset: 0, limit: 3 }), (error) => error.code === 'request_failed');
  assert.equal(readControls.cancelled, true);
});

test('abort during fetch and concurrent requests expose fixed private errors only', async () => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const client = new ContextApiClient({ fetchImpl: async () => pending });
  const controller = new AbortController();
  const request = client.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection, signal: controller.signal });
  controller.abort();
  await assert.rejects(request, (error) => error instanceof ContextApiError && error.code === 'request_aborted' && !String(error).includes('secret'));
  release(jsonResponse({}));

  const failing = new ContextApiClient({ fetchImpl: async () => { throw new Error('provider secret token'); } });
  await assert.rejects(failing.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection }), (error) => {
    assert.equal(error.code, 'request_failed');
    assert.equal(error.toJSON().code, 'request_failed');
    assert.equal(String(error).includes('provider secret'), false);
    return true;
  });

  const refs = [];
  const concurrent = new ContextApiClient({ fetchImpl: async (path, options) => {
    const body = JSON.parse(options.body);
    refs.push(body.selection[0].selection_ref);
    await new Promise((resolve) => setTimeout(resolve, body.selection[0].selection_ref.endsWith('.1') ? 10 : 0));
    return metadataResponse(body.selection);
  } });
  const first = forgeSelection.map((item) => ({ ...item, forge_locator: { ...item.forge_locator } }));
  const second = [{ ...first[0], selection_ref: 'forge.source.2', revision_ref: 'pv_33333333333333333333333333333333', forge_locator: { ...first[0].forge_locator, version_id: 'pv_33333333333333333333333333333333' } }];
  const firstRequest = concurrent.metadata({ chatSessionId: 'chat-owned', selection: first });
  first[0].selection_ref = 'forge.mutated';
  const secondRequest = concurrent.metadata({ chatSessionId: 'chat-owned', selection: second });
  const [firstResult, secondResult] = await Promise.all([firstRequest, secondRequest]);
  assert.deepEqual(refs.sort(), ['forge.source.1', 'forge.source.2']);
  assert.equal(firstResult.selection[0].selection_ref, 'forge.source.1');
  assert.equal(secondResult.selection[0].selection_ref, 'forge.source.2');
});

test('a late fetch response is cancelled after the request has already settled', async () => {
  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const controls = { cancelError: true };
  const client = new ContextApiClient({ fetchImpl: async () => pending });
  const controller = new AbortController();
  const request = client.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection, signal: controller.signal });
  controller.abort();
  await assert.rejects(request, (error) => error.code === 'request_aborted');
  release(binaryResponse(dataBySource.forge, forgeSelection[0], { whole: pageDigest, page: pageDigest, controls }));
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(controls.cancelled, true);
  assert.equal(controls.cancelCalls, 1);
});

test('abort during a response read or digest keeps the late result unreachable', async () => {
  let releaseRead;
  const pendingRead = new Promise((resolve) => { releaseRead = resolve; });
  const readClient = new ContextApiClient({ fetchImpl: async (path) => {
    if (path === '/api/context/metadata') return metadataResponse(forgeSelection);
    return {
      ok: true, status: 200,
      headers: headers({ 'content-type': 'application/octet-stream', 'cache-control': 'no-store', 'x-context-total': 3, 'x-context-offset': 0, 'x-context-end': 3, 'x-context-whole-sha256': pageDigest, 'x-context-page-sha256': pageDigest, 'x-context-manifest-sha256': digest, 'x-context-byteform': 'git_blob' }),
      body: { getReader() { return { read: () => pendingRead, cancel() {}, releaseLock() {} }; } },
    };
  } });
  const readMetadata = await readClient.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection });
  const readController = new AbortController();
  const readRequest = readClient.page({ chatSessionId: 'chat-owned', metadata: readMetadata, targetIndex: 0, offset: 0, limit: 3, signal: readController.signal });
  readController.abort();
  await assert.rejects(readRequest, (error) => error.code === 'request_aborted');
  releaseRead({ done: false, value: encoder.encode('abc') });

  const originalDigest = globalThis.crypto.subtle.digest;
  let releaseDigest;
  globalThis.crypto.subtle.digest = () => new Promise((resolve) => { releaseDigest = resolve; });
  const hashClient = new ContextApiClient({ fetchImpl: async (path) => path === '/api/context/metadata' ? metadataResponse(forgeSelection) : binaryResponse(dataBySource.forge, forgeSelection[0], { whole: pageDigest, page: pageDigest }) });
  const hashMetadata = await hashClient.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection });
  const hashController = new AbortController();
  const hashRequest = hashClient.page({ chatSessionId: 'chat-owned', metadata: hashMetadata, targetIndex: 0, offset: 0, limit: 3, signal: hashController.signal });
  await new Promise((resolve) => setTimeout(resolve, 0));
  hashController.abort();
  await assert.rejects(hashRequest, (error) => error.code === 'request_aborted');
  releaseDigest(new ArrayBuffer(32));
  globalThis.crypto.subtle.digest = originalDigest;
});

test('opaque identifiers accept protocol punctuation while forbidden forms stay rejected', async () => {
  const punctuated = [{ source: 'planning', selection_ref: 'a/+@~.1', revision_ref: 'r/+@~.1' }];
  const client = new ContextApiClient({ fetchImpl: async () => metadataResponse(punctuated) });
  const result = await client.metadata({ chatSessionId: 'chat-owned', selection: punctuated });
  assert.equal(result.selection[0].selection_ref, 'a/+@~.1');
  await assert.rejects(Promise.resolve().then(() => client.metadata({ chatSessionId: 'chat-owned', selection: [{ ...punctuated[0], selection_ref: 'bad value' }] })), (error) => error.code === 'invalid_request');
});

for (const source of ['forge', 'knowledge', 'planning']) {
  test(`metadata rejects the wrong native digest form for ${source}`, async () => {
    const selection = mixedSelection.filter((item) => item.source === source);
    const client = new ContextApiClient({ fetchImpl: async () => metadataResponse(selection, (value) => {
      value.targets[0].manifest_digest = source === 'knowledge' ? digest : `sha256:${digest}`;
      return value;
    }) });
    await assert.rejects(client.metadata({ chatSessionId: 'chat-owned', selection }), (error) => error.code === 'invalid_response');
  });
}

const pageFailures = [
  { name: 'whole hash differs from captured payload', code: 'native_digest_mismatch', options: { whole: digest } },
  { name: 'page hash differs from actual bytes', code: 'content_hash_mismatch', options: { page: digest } },
  { name: 'full page hash differs from the metadata-bound whole hash', code: 'whole_hash_mismatch', payload: digest, options: { whole: digest } },
  { name: 'end differs from actual body length', code: 'invalid_response', options: { headers: { 'x-context-end': 2 } } },
  { name: 'end exceeds total bytes', code: 'invalid_response', options: { headers: { 'x-context-total': 2 } } },
  { name: 'range header is not a decimal integer', code: 'invalid_response', options: { headers: { 'x-context-total': '3x' } } },
  { name: 'binary response lacks no-store', code: 'invalid_response', options: { headers: { 'cache-control': 'public' } } },
];
for (const scenario of pageFailures) {
  test(`page denies isolated ${scenario.name}`, async () => {
    const client = new ContextApiClient({ fetchImpl: async (path) => {
      if (path === '/api/context/metadata') return metadataResponse(forgeSelection, (value) => {
        value.items[0].payload_sha256 = scenario.payload || pageDigest;
        return value;
      });
      return binaryResponse(dataBySource.forge, forgeSelection[0], { whole: pageDigest, page: pageDigest, ...scenario.options });
    } });
    const metadata = await client.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection });
    await assert.rejects(client.page({ chatSessionId: 'chat-owned', metadata, targetIndex: 0, offset: 0, limit: 3 }), (error) => error.code === scenario.code);
  });
}

for (const responseHeaders of [
  { 'content-type': 'text/plain', 'cache-control': 'no-store' },
  { 'content-type': 'application/json', 'cache-control': 'public' },
]) {
  test(`metadata denies invalid JSON headers ${Object.keys(responseHeaders).map((key) => responseHeaders[key]).join('/')}`, async () => {
    const client = new ContextApiClient({ fetchImpl: async () => ({ ...metadataResponse(forgeSelection), headers: headers(responseHeaders) }) });
    await assert.rejects(client.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection }), (error) => error.code === 'invalid_response');
  });
}

for (const kind of ['json', 'binary']) {
  test(`${kind} invalid chunk cancels even when cancellation throws synchronously`, async () => {
    const controls = { cancelSyncError: true };
    const client = new ContextApiClient({ fetchImpl: async (path) => {
      if (kind === 'json') return { ...metadataResponse(forgeSelection), body: streamBody(['invalid chunk'], controls) };
      if (path === '/api/context/metadata') return metadataResponse(forgeSelection);
      return binaryResponse(dataBySource.forge, forgeSelection[0], { whole: pageDigest, page: pageDigest, chunks: ['invalid chunk'], controls });
    } });
    let request;
    if (kind === 'json') request = client.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection });
    else {
      const metadata = await client.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection });
      request = client.page({ chatSessionId: 'chat-owned', metadata, targetIndex: 0, offset: 0, limit: 3 });
    }
    await assert.rejects(request, (error) => error.code === 'invalid_response' && !String(error).includes('secret'));
    assert.equal(controls.cancelCalls, 1);
  });
}

test('packPage sends one exact bounded request and preserves ordered canonical items', async () => {
  const calls = [];
  const orderedItems = packItems(mixedSelection).reverse();
  const client = new ContextApiClient({ fetchImpl: async (path, options) => {
    calls.push({ path, options, body: JSON.parse(options.body) });
    if (path === '/api/context/metadata') return metadataResponse(mixedSelection, (value) => {
      value.items.reverse();
      return value;
    });
    assert.equal(path, '/api/context/pack');
    return jsonResponse(await packPayload(mixedSelection, orderedItems, { packId: `sha256:${'c'.repeat(64)}` }));
  } });
  const metadata = await client.metadata({ chatSessionId: 'chat-owned', selection: mixedSelection });
  const pack = await client.packPage({ chatSessionId: 'chat-owned', metadata, pageBytes: 16 });
  assert.deepEqual(calls.map((call) => call.path), ['/api/context/metadata', '/api/context/pack']);
  assert.deepEqual(calls[1].body, { chat_session_id: 'chat-owned', selection: mixedSelection, page_bytes: 16, cursor: null });
  assert.equal(pack.schema, 'odysseus.context_pack.v1');
  assert.equal(pack.pack_id, `sha256:${'c'.repeat(64)}`);
  assert.deepEqual(pack.items, orderedItems);
  assert.deepEqual(Object.keys(pack.page).sort(), ['byteform', 'content_b64', 'end', 'offset', 'page_sha256', 'target_index', 'total', 'whole_sha256']);
  assert.deepEqual(Array.from(pack.page.bytes), Array.from(dataBySource.forge));
  assert.equal(pack.complete, false);
  assert.equal(pack.next_cursor, 'Q');
  assert.equal(calls.filter((call) => call.path === '/api/context/challenge').length, 0);
});

test('packPage continuation binds canonical pack identity while target_index resolves by selection triple', async () => {
  const packId = `sha256:${'d'.repeat(64)}`;
  const calls = [];
  let packCalls = 0;
  const client = new ContextApiClient({ fetchImpl: async (path, options) => {
    calls.push({ path, body: JSON.parse(options.body) });
    if (path === '/api/context/metadata') return metadataResponse(mixedSelection);
    packCalls += 1;
    const body = calls.at(-1).body;
    if (packCalls === 1) {
      assert.equal(body.cursor, null);
      assert.equal(Object.prototype.hasOwnProperty.call(body, 'expected_pack_id'), false);
      return jsonResponse(await packPayload(mixedSelection, packItems(mixedSelection), {
        packId, bytes: encoder.encode('a'), end: 1, total: 3, complete: false, nextCursor: 'Q',
      }));
    }
    assert.equal(body.cursor, 'Q');
    assert.equal(Object.prototype.hasOwnProperty.call(body, 'expected_pack_id'), false);
    return jsonResponse(await packPayload(mixedSelection, packItems(mixedSelection), {
      packId, targetIndex: 1, bytes: dataBySource.knowledge, complete: false, nextCursor: 'R',
    }));
  } });
  const metadata = await client.metadata({ chatSessionId: 'chat-owned', selection: mixedSelection });
  const first = await client.packPage({ chatSessionId: 'chat-owned', metadata, pageBytes: 1 });
  const second = await client.packPage({ chatSessionId: 'chat-owned', metadata, pageBytes: 3, cursor: first.next_cursor, expectedPackId: first.pack_id });
  assert.equal(first.page.target_index, 0);
  assert.equal(second.page.target_index, 1);
  assert.deepEqual(Array.from(second.page.bytes), Array.from(dataBySource.knowledge));
  assert.throws(() => client.packPage({ chatSessionId: 'chat-owned', metadata, pageBytes: 3, cursor: 'Q' }), (error) => error.code === 'invalid_request');
  assert.throws(() => client.packPage({ chatSessionId: 'chat-owned', metadata, pageBytes: 3, expectedPackId: packId }), (error) => error.code === 'invalid_request');
});

test('packPage requires an existing private disclosure reference and never auto-challenges', async () => {
  const privateSelection = [forgeSelection[0]];
  let packCalls = 0;
  const client = new ContextApiClient({ fetchImpl: async (path) => {
    if (path === '/api/context/metadata') return metadataResponse(privateSelection, (value) => {
      value.items[0].classification = 'private';
      value.requires_private_confirmation = true;
      value.manifest_digest = digest;
      return value;
    });
    packCalls += 1;
    const items = packItems(privateSelection);
    items[0].classification = 'private';
    return jsonResponse(await packPayload(privateSelection, items, { complete: true }));
  } });
  const metadata = await client.metadata({ chatSessionId: 'chat-owned', selection: privateSelection });
  assert.throws(() => client.packPage({ chatSessionId: 'chat-owned', metadata, pageBytes: 3 }), (error) => error.code === 'invalid_request');
  const pack = await client.packPage({ chatSessionId: 'chat-owned', metadata, pageBytes: 3, disclosureMessageId: 42 });
  assert.equal(pack.complete, true);
  assert.equal(packCalls, 1);
});

test('packPage rejects item-order, base64, hash, range, EOF, flags, and cursor violations without exposing content', async () => {
  const metadataClient = new ContextApiClient({ fetchImpl: async (path) => path === '/api/context/metadata' ? metadataResponse(mixedSelection, (value) => {
    value.items.reverse();
    return value;
  }) : jsonResponse({}) });
  const metadata = await metadataClient.metadata({ chatSessionId: 'chat-owned', selection: mixedSelection });
  const faults = [
    { name: 'ordered items', change: (payload) => { payload.items.reverse(); } },
    { name: 'noncanonical base64 padding bits', change: (payload) => { payload.page.content_b64 = 'Zh=='; } },
    { name: 'whole payload hash', change: (payload) => { payload.page.whole_sha256 = `sha256:${'e'.repeat(64)}`; } },
    { name: 'zero progress', change: (payload) => { payload.page.end = 0; payload.page.total = 3; payload.page.content_b64 = ''; payload.complete = false; payload.next_cursor = 'Q'; } },
    { name: 'truth flags', change: (payload) => { payload.authority_granted = true; } },
    { name: 'noncanonical continuation', change: (payload) => { payload.complete = false; payload.next_cursor = 'Q='; } },
  ];
  for (const fault of faults) {
    const client = new ContextApiClient({ fetchImpl: async (path) => {
      if (path === '/api/context/metadata') return metadataResponse(mixedSelection, (value) => {
        value.items.reverse();
        return value;
      });
      const payload = await packPayload(mixedSelection, packItems(mixedSelection).reverse(), { complete: false, nextCursor: 'Q' });
      fault.change(payload);
      return jsonResponse(payload);
    } });
    const current = await client.metadata({ chatSessionId: 'chat-owned', selection: mixedSelection });
    await assert.rejects(client.packPage({ chatSessionId: 'chat-owned', metadata: current, pageBytes: 3 }), (error) => {
      assert.ok(['invalid_response', 'native_digest_mismatch', 'content_hash_mismatch', 'whole_hash_mismatch'].includes(error.code), fault.name);
      return true;
    });
  }
});

test('packPage keeps the pack envelope cap separate from the existing metadata cap', async () => {
  const client = new ContextApiClient({ fetchImpl: async (path) => {
    if (path === '/api/context/metadata') return metadataResponse(forgeSelection);
    const response = jsonResponse(await packPayload(forgeSelection, packItems(forgeSelection), { complete: true }));
    return { ...response, headers: headers({ 'content-type': 'application/json', 'cache-control': 'no-store', 'content-length': 2 * 1024 * 1024 }) };
  } });
  const metadata = await client.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection });
  await assert.rejects(client.packPage({ chatSessionId: 'chat-owned', metadata, pageBytes: 1_000_000 }), (error) => error.code === 'response_too_large');
});

test('packPage keeps page and opaque cursor bounds closed', async () => {
  const metadataClient = new ContextApiClient({ fetchImpl: async (path) => metadataResponse(forgeSelection) });
  const metadata = await metadataClient.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection });
  assert.throws(
    () => metadataClient.packPage({ chatSessionId: 'chat-owned', metadata, pageBytes: 1_000_001 }),
    (error) => error.code === 'invalid_request',
  );

  const maxCursor = 'Q'.repeat(16_384);
  const acceptedClient = new ContextApiClient({ fetchImpl: async (path) => jsonResponse(await packPayload(forgeSelection, packItems(forgeSelection), { bytes: encoder.encode('a'), end: 1, total: 3, complete: false, nextCursor: maxCursor })) });
  const accepted = await acceptedClient.packPage({ chatSessionId: 'chat-owned', metadata, pageBytes: 1_000_000 });
  assert.equal(accepted.next_cursor, maxCursor);

  const oversizedClient = new ContextApiClient({ fetchImpl: async (path) => jsonResponse(await packPayload(forgeSelection, packItems(forgeSelection), { bytes: encoder.encode('a'), end: 1, total: 3, complete: false, nextCursor: `${maxCursor}Q` })) });
  await assert.rejects(oversizedClient.packPage({ chatSessionId: 'chat-owned', metadata, pageBytes: 1_000_000 }), (error) => error.code === 'invalid_response');
});

test('packPage cancels an over-budget streamed envelope and exposes fixed abort errors', async () => {
  const controls = { cancelError: true };
  const oversized = new Uint8Array(2 * 1024 * 1024);
  const client = new ContextApiClient({ fetchImpl: async (path) => {
    if (path === '/api/context/metadata') return metadataResponse(forgeSelection);
    return {
      ok: true, status: 200,
      headers: headers({ 'content-type': 'application/json', 'cache-control': 'no-store' }),
      body: streamBody([oversized], controls),
    };
  } });
  const metadata = await client.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection });
  await assert.rejects(client.packPage({ chatSessionId: 'chat-owned', metadata, pageBytes: 1_000_000 }), (error) => error.code === 'response_too_large');
  assert.equal(controls.cancelled, true);
  assert.equal(controls.cancelCalls, 1);

  let release;
  const pending = new Promise((resolve) => { release = resolve; });
  const abortClient = new ContextApiClient({ fetchImpl: async (path) => path === '/api/context/metadata' ? metadataResponse(forgeSelection) : pending });
  const abortMetadata = await abortClient.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection });
  const controller = new AbortController();
  const request = abortClient.packPage({ chatSessionId: 'chat-owned', metadata: abortMetadata, pageBytes: 3, signal: controller.signal });
  controller.abort();
  await assert.rejects(request, (error) => error instanceof ContextApiError && error.code === 'request_aborted' && !String(error).includes('secret'));
  release(jsonResponse({}));
});

test('packPage isolates concurrent immutable metadata snapshots', async () => {
  const refs = [];
  const client = new ContextApiClient({ fetchImpl: async (path, options) => {
    if (path === '/api/context/metadata') return metadataResponse(forgeSelection);
    const body = JSON.parse(options.body);
    refs.push(body.selection[0].selection_ref);
    await new Promise((resolve) => setTimeout(resolve, body.page_bytes === 1 ? 10 : 0));
    return jsonResponse(await packPayload(forgeSelection, packItems(forgeSelection), {
      bytes: dataBySource.forge.slice(0, body.page_bytes), end: body.page_bytes, total: 3, complete: false, nextCursor: 'Q',
    }));
  } });
  const metadata = await client.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection });
  const first = client.packPage({ chatSessionId: 'chat-owned', metadata, pageBytes: 1 });
  const second = client.packPage({ chatSessionId: 'chat-owned', metadata, pageBytes: 2 });
  const [firstResult, secondResult] = await Promise.all([first, second]);
  assert.deepEqual(refs, ['forge.source.1', 'forge.source.1']);
  assert.equal(firstResult.page.target_index, 0);
  assert.equal(secondResult.page.target_index, 0);
});


test('packPage rejects caller abort while SHA validation is pending', async () => {
  const payload = await packPayload(forgeSelection, packItems(forgeSelection), { complete: true });
  const actualDigest = await webcrypto.subtle.digest('SHA-256', dataBySource.forge);
  const client = new ContextApiClient({ fetchImpl: async (path) => path === '/api/context/metadata' ? metadataResponse(forgeSelection) : jsonResponse(payload) });
  const metadata = await client.metadata({ chatSessionId: 'chat-owned', selection: forgeSelection });
  const originalDigest = globalThis.crypto.subtle.digest;
  let releaseDigest;
  let reachedDigest;
  const reached = new Promise((resolve) => { reachedDigest = resolve; });
  globalThis.crypto.subtle.digest = () => new Promise((resolve) => { releaseDigest = resolve; reachedDigest(); });
  try {
    const controller = new AbortController();
    const request = client.packPage({ chatSessionId: 'chat-owned', metadata, pageBytes: 3, signal: controller.signal });
    await reached;
    controller.abort();
    releaseDigest(actualDigest);
    await assert.rejects(request, (error) => error instanceof ContextApiError && error.code === 'request_aborted');
  } finally {
    globalThis.crypto.subtle.digest = originalDigest;
  }
});
