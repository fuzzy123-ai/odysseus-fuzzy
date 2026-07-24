const { test, expect } = require('playwright/test');
const http = require('node:http');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');

const repoRoot = path.resolve(__dirname, '..', '..');
const screenshots = path.join(os.tmpdir(), 'odysseus-uix20-playwright');
const safeRef = `upload:${'a'.repeat(32)}.txt`;
let server;
let baseUrl;
let requestedPaths = [];

function headers({ state = 'complete', contentType = 'text/plain; charset=utf-8', bytes, valid = true } = {}) {
  const value = Buffer.isBuffer(bytes) ? bytes : Buffer.from(bytes || 'safe');
  return valid ? {
    'cache-control': 'private, no-store',
    'content-type': contentType,
    'content-length': String(value.length),
    'x-content-type-options': 'nosniff',
    'x-odysseus-content-schema': 'odysseus.universal_inbox.source_content.v1',
    'x-odysseus-content-state': state,
    'x-odysseus-content-truncated': String(state === 'truncated')
  } : { 'content-type': contentType, 'content-length': String(value.length) };
}

test.beforeAll(async () => {
  await fs.mkdir(screenshots, { recursive: true });
  server = http.createServer(async (request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    if (url.pathname === '/harness') {
      response.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' });
      response.end(`<!doctype html><html><head><style>:root{--bg:#282c34;--ink:#9cdef2;--muted:#b9d7df;--cyan:#00aaff;--light-bg:#f5f5f5;--font:Inter,system-ui,sans-serif;--mono:"Fira Code",ui-monospace,monospace}html,body{min-height:100%;margin:0;background:var(--bg)}#preview{min-height:640px;height:calc(100vh - 2px)}</style><link rel="stylesheet" href="/static/frontpage-v3/inbox-preview.css"></head><body><main id="preview" aria-label="Document preview"></main><script src="/static/frontpage-v3/inbox-preview.js"></script></body></html>`);
      return;
    }
    if (url.pathname.startsWith('/api/universal-inbox/items/')) {
      requestedPaths.push(url.pathname);
      const scenario = url.searchParams.get('case') || 'text';
      if (scenario === 'slow') {
        request.on('close', () => { /* Abort is asserted from the browser signal, not server timing. */ });
        await new Promise(resolve => setTimeout(resolve, 300));
      }
      const body = scenario === 'pdf' ? Buffer.from('%PDF-1.4\nfixture')
        : scenario === 'large' ? Buffer.alloc(130, 65)
          : scenario === 'truncated' ? Buffer.from('short')
            : Buffer.from('<script>window.pwned=1</script><form><button>bad</button></form><a href="/x">link</a>');
      if (scenario === 'unauthorized') { response.writeHead(403, headers({ bytes: 'closed' })); response.end('closed'); return; }
      if (scenario === 'unavailable') { response.writeHead(503, headers({ bytes: 'closed' })); response.end('closed'); return; }
      if (scenario === 'error') { response.writeHead(500, headers({ bytes: 'closed' })); response.end('closed'); return; }
      if (scenario === 'bad') { response.writeHead(200, headers({ bytes: body, valid: false })); response.end(body); return; }
      if (scenario === 'range') {
        response.writeHead(206, { ...headers({ bytes: Buffer.alloc(64, 65), state: 'partial' }), 'content-range': 'bytes 0-63/4096' });
        response.end(Buffer.alloc(64, 65)); return;
      }
      if (scenario === 'bad-range') {
        response.writeHead(206, { ...headers({ bytes: body, state: 'partial' }), 'content-range': 'bytes invalid' });
        response.end(body); return;
      }
      const isPdf = scenario === 'pdf';
      const responseHeaders = headers({
        bytes: body, state: scenario === 'truncated' ? 'truncated' : 'complete',
        contentType: isPdf ? 'application/pdf' : 'text/plain; charset=utf-8'
      });
      if (scenario === 'truncated') responseHeaders['content-range'] = 'bytes 0-4/256';
      response.writeHead(scenario === 'truncated' ? 206 : 200, responseHeaders);
      response.end(body);
      return;
    }
    try {
      const target = path.resolve(repoRoot, decodeURIComponent(url.pathname).replace(/^\/+/, ''));
      if (!target.startsWith(repoRoot + path.sep)) throw new Error('outside_repo');
      const body = await fs.readFile(target);
      response.writeHead(200, { 'content-type': target.endsWith('.css') ? 'text/css' : 'text/javascript', 'cache-control': 'no-store' });
      response.end(body);
    } catch { response.writeHead(404).end('not found'); }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

test.afterAll(async () => { if (server) await new Promise(resolve => server.close(resolve)); });

async function setup(page, maxBytes = 64) {
  await page.goto(`${baseUrl}/harness`);
  await page.evaluate(limit => {
    window.previewRoot = document.querySelector('#preview');
    window.previewController = new window.HarborInboxPreview.InboxPreviewController({ root: window.previewRoot, maxBytes: limit });
    window.__uix20Item = suffix => ({ source_ref: `upload:${'a'.repeat(32)}${suffix}`, metadata: { suffix }, capability: {
      schema: 'odysseus.universal_inbox.workbench_capability.v1', source_suffix: suffix,
      server_authoritative: true, owner_authorized: true, live_write_authorized: false,
      original_immutable: true, raw_content_visible: false, absolute_path_visible: false, mvp_tier: 'P0',
      actions: [{ action: 'inspect', state: 'allowed', mutates_original: false, performs_live_write: false }]
    } });
  }, maxBytes);
}

function capability(suffix, state = 'allowed') {
  return {
    schema: 'odysseus.universal_inbox.workbench_capability.v1', source_suffix: suffix,
    server_authoritative: true, owner_authorized: true, live_write_authorized: false,
    original_immutable: true, raw_content_visible: false, absolute_path_visible: false, mvp_tier: 'P0',
    actions: [{ action: 'inspect', state, mutates_original: false, performs_live_write: false }]
  };
}

function item(suffix, ref = safeRef, metadata = {}, cap = capability(suffix)) {
  return { source_ref: ref, metadata: { suffix, ...metadata }, capability: cap };
}

test('text is inert, byte-bounded, header-validated, and stale-safe', async ({ page }) => {
  await setup(page, 256);
  await page.evaluate(async value => {
    const original = window.previewController.fetch;
    window.previewController.fetch = (url, options) => {
      window.__uix20FetchOptions = { mode: options.mode, redirect: options.redirect, credentials: options.credentials, cache: options.cache };
      return original(url, options);
    };
    await window.previewController.load(value);
    window.previewController.fetch = original;
  }, item('.txt'));
  expect(await page.evaluate(() => window.__uix20FetchOptions)).toEqual({ mode: 'same-origin', redirect: 'error', credentials: 'same-origin', cache: 'no-store' });
  await expect(page.locator('[data-preview-inert-text]')).toContainText('<script>window.pwned=1</script>');
  await expect(page.locator('[data-preview-inert-text] script, [data-preview-inert-text] form, [data-preview-inert-text] a, [data-preview-inert-text] button')).toHaveCount(0);
  await expect(page.locator('#preview')).toHaveAttribute('data-preview-state', 'ready');

  await page.evaluate(value => window.previewController.load(value), item('.txt', `${safeRef}?case=truncated`));
  // Query strings cannot enter source refs, so call the safe endpoint behavior through a fetch wrapper.
  await page.evaluate(async () => {
    const original = window.previewController.fetch;
    window.previewController.fetch = (url, options) => original(`${url}?case=truncated`, options);
    await window.previewController.load(window.__uix20Item('.txt'));
    window.previewController.fetch = original;
  });
  await expect(page.locator('#preview')).toHaveAttribute('data-preview-state', 'truncated');

  await page.evaluate(async () => {
    const original = window.previewController.fetch;
    window.previewController.fetch = (url, options) => original(`${url}?case=bad`, options);
    await window.previewController.load(window.__uix20Item('.txt'));
    window.previewController.fetch = original;
  });
  await expect(page.locator('#preview')).toHaveAttribute('data-preview-state', 'mismatch');

  await page.evaluate(async () => {
    window.previewController.fetch = async () => new Response('two', {
      status: 200,
      headers: {
        'cache-control': 'private, no-store', 'content-type': 'text/plain; charset=utf-8', 'content-length': '10',
        'x-content-type-options': 'nosniff', 'x-odysseus-content-schema': 'odysseus.universal_inbox.source_content.v1',
        'x-odysseus-content-state': 'complete', 'x-odysseus-content-truncated': 'false'
      }
    });
    await window.previewController.load(window.__uix20Item('.txt'));
    window.previewController.fetch = window.fetch.bind(window);
  });
  await expect(page.locator('#preview')).toHaveAttribute('data-preview-state', 'mismatch');

  await page.evaluate(async () => {
    window.previewController.destroy();
    window.previewController = new window.HarborInboxPreview.InboxPreviewController({ root: window.previewRoot, maxBytes: 64 });
    const original = window.previewController.fetch;
    window.previewController.fetch = (url, options) => original(`${url}?case=large`, options);
    await window.previewController.load(window.__uix20Item('.txt'));
    window.previewController.fetch = original;
  });
  await expect(page.locator('#preview')).toHaveAttribute('data-preview-state', 'byte_limit');

  await page.evaluate(async () => {
    const original = window.previewController.fetch;
    window.previewController.fetch = (url, options) => original(`${url}?case=range`, options);
    await window.previewController.load(window.__uix20Item('.txt'));
    window.previewController.fetch = original;
  });
  await expect(page.locator('#preview')).toHaveAttribute('data-preview-state', 'truncated');

  await page.evaluate(async () => {
    const original = window.previewController.fetch;
    window.previewController.fetch = (url, options) => original(`${url}?case=bad-range`, options);
    await window.previewController.load(window.__uix20Item('.txt'));
    window.previewController.fetch = original;
  });
  await expect(page.locator('#preview')).toHaveAttribute('data-preview-state', 'mismatch');

  await page.evaluate(async () => {
    let aborted = false;
    window.previewController.destroy();
    window.previewController = new window.HarborInboxPreview.InboxPreviewController({ root: window.previewRoot, maxBytes: 256 });
    const original = window.previewController.fetch;
    window.previewController.fetch = (url, options) => {
      options.signal.addEventListener('abort', () => { aborted = true; }, { once: true });
      return original(`${url}?case=slow`, options);
    };
    const first = window.previewController.load(window.__uix20Item('.txt'));
    await new Promise(resolve => setTimeout(resolve, 20));
    window.previewController.fetch = original;
    await window.previewController.load(window.__uix20Item('.txt'));
    await first;
    window.__uix20Aborted = aborted;
  });
  expect(await page.evaluate(() => window.__uix20Aborted)).toBe(true);
  await expect(page.locator('#preview')).toHaveAttribute('data-preview-state', 'ready');
});

test('PDF object URLs are sandboxed, replaced, and revoked on destroy', async ({ page }) => {
  await setup(page);
  await page.evaluate(async () => {
    const urls = [];
    const revoked = [];
    const urlApi = { createObjectURL: () => `blob:test-${urls.push(1)}`, revokeObjectURL: value => revoked.push(value) };
    window.previewController.destroy();
    window.previewController = new window.HarborInboxPreview.InboxPreviewController({ root: window.previewRoot, maxBytes: 64, urlApi });
    const original = window.previewController.fetch;
    window.previewController.fetch = (url, options) => original(`${url}?case=pdf`, options);
    await window.previewController.load(window.__uix20Item('.pdf'));
    const frame = window.previewRoot.querySelector('iframe');
    const sandbox = frame && frame.getAttribute('sandbox');
    const sandboxTokens = frame ? Array.from(frame.sandbox) : null;
    const referrerPolicy = frame && frame.getAttribute('referrerpolicy');
    await window.previewController.load(window.__uix20Item('.pdf'));
    window.previewController.destroy();
    const failureRoot = document.createElement('main');
    document.body.append(failureRoot);
    const nativeReplace = failureRoot.replaceChildren.bind(failureRoot);
    let replacements = 0;
    failureRoot.replaceChildren = (...nodes) => {
      replacements += 1;
      if (replacements === 3) throw new Error('render_failure');
      nativeReplace(...nodes);
    };
    const failureRevocations = [];
    const fetchPdf = (url, options) => original(`${url}?case=pdf`, options);
    const failing = new window.HarborInboxPreview.InboxPreviewController({
      root: failureRoot, maxBytes: 64, fetchImpl: fetchPdf,
      urlApi: { createObjectURL: () => 'blob:failure', revokeObjectURL: value => failureRevocations.push(value) }
    });
    await failing.load(window.__uix20Item('.pdf'));
    window.__uix20Urls = { urls: urls.length, revoked, sandbox, sandboxTokens, referrerPolicy, failureRevocations, failureState: failureRoot.dataset.previewState };
  });
  const pdf = page.locator('iframe[title="Bounded PDF preview"]');
  await expect(pdf).toHaveCount(0); // destroy removes the boundary after revocation.
  expect(await page.evaluate(() => window.__uix20Urls)).toEqual({ urls: 2, revoked: ['blob:test-1', 'blob:test-2'], sandbox: '', sandboxTokens: [], referrerPolicy: 'no-referrer', failureRevocations: ['blob:failure'], failureState: 'error' });
});

test('DOCX is extraction-only and unsupported/auth/error formats remain closed', async ({ page }) => {
  await setup(page);
  requestedPaths = [];
  await page.evaluate(value => window.previewController.load(value, { extractedText: '# extracted\n<svg onload=alert(1)>', extractionState: 'ready' }), item('.docx'));
  await expect(page.locator('[data-preview-inert-text]')).toContainText('<svg onload=alert(1)>');
  expect(requestedPaths).toEqual([]);
  await page.evaluate(value => window.previewController.load(value, { extractedText: 'x'.repeat(80), extractionState: 'ready' }), item('.docx'));
  await expect(page.locator('#preview')).toHaveAttribute('data-preview-state', 'byte_limit');
  expect(requestedPaths).toEqual([]);

  for (const suffix of ['.html', '.svg', '.xml', '.exe', '.unknown']) {
    await page.evaluate(value => window.previewController.load(value), item(suffix));
    await expect(page.locator('#preview')).toHaveAttribute('data-preview-state', 'unsupported');
  }
  for (const [candidate, expected] of [
    [item('.txt', safeRef, { blocked: true }), 'blocked'],
    [item('.txt', safeRef, { review_required: true }), 'review'],
    [item('.txt', safeRef, {}, null), 'review'],
    [item('.txt', safeRef, {}, capability('.txt', 'review')), 'review'],
    [item('.txt', safeRef, {}, { ...capability('.txt'), original_immutable: false }), 'review']
  ]) {
    await page.evaluate(value => window.previewController.load(value), candidate);
    await expect(page.locator('#preview')).toHaveAttribute('data-preview-state', expected);
  }
  expect(requestedPaths).toEqual([]);
  for (const [scenario, expected] of [['unauthorized', 'unauthorized'], ['unavailable', 'unavailable'], ['error', 'error']]) {
    await page.evaluate(async ({ scenario, expected }) => {
      const original = window.previewController.fetch;
      window.previewController.fetch = (url, options) => original(`${url}?case=${scenario}`, options);
      await window.previewController.load(window.__uix20Item('.txt'));
      window.previewController.fetch = original;
      if (window.previewRoot.dataset.previewState !== expected) throw new Error('unexpected_state');
    }, { scenario, expected });
  }
});

test('desktop and mobile snapshots preserve bounded preview accessibility', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 720 });
  await setup(page);
  await page.evaluate(value => window.previewController.load(value, { extractedText: 'Visible, inert extraction.', extractionState: 'ready' }), item('.docx'));
  await page.screenshot({ path: path.join(screenshots, 'uix20-desktop.png'), fullPage: true });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(screenshots, 'uix20-mobile.png'), fullPage: true });
  const check = await page.locator('#preview').evaluate(node => ({
    overflow: node.scrollWidth <= node.clientWidth + 1,
    live: node.querySelector('[aria-live]')?.textContent || '',
    animations: Array.from(node.querySelectorAll('*')).some(child => parseFloat(getComputedStyle(child).animationDuration) > 0.011)
  }));
  expect(check.overflow).toBe(true);
  expect(check.live).toContain('extraction');
  expect(check.animations).toBe(false);
});
