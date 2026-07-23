const { test, expect } = require('playwright/test');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs/promises');

const repoRoot = path.resolve(__dirname, '..', '..');
const screenshotRoot = path.join(os.tmpdir(), 'odysseus-uix19-playwright');
let server;
let baseUrl;

const mimeTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2'
};

test.beforeAll(async () => {
  await fs.mkdir(screenshotRoot, { recursive: true });
  server = http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://127.0.0.1');
      const relative = decodeURIComponent(url.pathname).replace(/^\/+/, '')
        || 'static/frontpage-v3/index.html';
      const target = path.resolve(repoRoot, relative);
      if (!target.startsWith(repoRoot + path.sep)) {
        response.writeHead(403).end('forbidden');
        return;
      }
      const body = await fs.readFile(target);
      response.writeHead(200, {
        'Cache-Control': 'no-store',
        'Content-Type': mimeTypes[path.extname(target).toLowerCase()]
          || 'application/octet-stream'
      });
      response.end(body);
    } catch {
      response.writeHead(404).end('not found');
    }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

test.afterAll(async () => {
  if (server) await new Promise(resolve => server.close(resolve));
});

async function openInbox(page, query = '') {
  const suffix = query ? `&${query}` : '';
  await page.goto(
    `${baseUrl}/static/frontpage-v3/index.html?workspace=inbox&inboxSource=fixture${suffix}`
  );
  const root = page.locator('[data-inbox-workbench-root]');
  await expect(root).not.toHaveAttribute('data-inbox-mode', 'loading');
  return root;
}

test('accepted three-zone shell is source-bound, document-primary and write-closed', async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 960 });
  const root = await openInbox(page);

  await expect(root).toHaveAttribute('data-inbox-mode', 'fixture');
  await expect(root.getByText('Preview fixture', { exact: true })).toBeVisible();
  await expect(root.getByText('Original protected', { exact: true })).toBeVisible();
  await expect(root.locator('[data-workbench-panel="source"]')).toBeVisible();
  await expect(root.locator('[data-workbench-panel="document"]')).toBeVisible();
  await expect(root.locator('[data-workbench-panel="details"]')).toBeVisible();
  await expect(root.locator('[data-document-heading]').getByText(
    'Preview invoice.pdf',
    { exact: true }
  )).toBeVisible();
  await expect(root.getByRole('tab', { name: 'Original' })).toBeEnabled();
  await expect(root.getByRole('tab', { name: 'Extraction' })).toBeDisabled();
  await expect(root.getByRole('tab', { name: 'Working copy' })).toBeDisabled();
  await expect(root.getByRole('tab', { name: 'Difference' })).toBeDisabled();
  await expect(root.getByText('Live gate closed', { exact: true })).toBeVisible();

  const text = (await root.innerText()).toLowerCase();
  for (const forbidden of [
    'c:\\', '/users/', 'copy to nextcloud', 'move file', 'delete original'
  ]) {
    expect(text).not.toContain(forbidden);
  }
  await expect(root.getByText(
    'No copy, move, delete, overwrite, provider, or memory write is available here.'
  )).toBeVisible();
  const enabledMutations = root.locator('button:not(:disabled)').filter({
    hasText: /export|open working copy|suggest route|copy|move|delete|memory/i
  });
  await expect(enabledMutations).toHaveCount(0);
  await expect(page.locator('.inbox-analysis-window')).toBeHidden();
  await expect(page.locator('.inbox-files-window')).toBeHidden();
  await page.screenshot({
    path: path.join(screenshotRoot, 'uix19-desktop-selected.png'),
    fullPage: true
  });
});

test('review, working-copy and export visual fixtures stay explicit and non-live', async ({ page }) => {
  const cases = [
    ['review', 'Review required', 'Content and write actions remain closed.'],
    ['dirty', 'Dirty', 'unsaved changes'],
    ['saving', 'Saving…', 'Saving working-copy preview fixture.'],
    ['export-success', 'Export ready', 'Browser export preview fixture is ready.']
  ];

  for (const [scenario, visible, announcement] of cases) {
    const root = await openInbox(page, `inboxWorkbenchState=${scenario}`);
    await expect(root.getByText(visible, { exact: true }).first()).toBeVisible();
    await expect(root.locator('[data-inbox-live-region]')).toContainText(announcement);
    await expect(root.getByText('Preview fixture', { exact: true })).toBeVisible();
  }

  const unavailable = await openInbox(page, 'inboxScenario=unavailable');
  await expect(unavailable.getByRole('heading', {
    name: 'Universal Inbox is unavailable'
  })).toBeVisible();
  await expect(unavailable.getByText('Preview invoice.pdf', { exact: true })).toHaveCount(0);
  await page.screenshot({
    path: path.join(screenshotRoot, 'uix19-unavailable.png'),
    fullPage: true
  });
});

test('workspace, mobile panel and escape navigation return focus predictably', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(
    `${baseUrl}/static/frontpage-v3/index.html?workspace=agent&inboxSource=fixture`
  );
  const root = page.locator('[data-inbox-workbench-root]');
  await expect(root).toHaveAttribute('data-inbox-mode', 'fixture');

  await page.locator('[data-workspace-target="inbox"]').click();
  await expect(root.locator('[data-document-heading]')).toBeFocused();

  const inboxTab = root.getByRole('tab', { name: 'Inbox' });
  await inboxTab.click();
  await expect(root.locator('[data-workbench-panel="source"]')).toBeVisible();
  await expect(root.locator('[data-workbench-panel="document"]')).toBeHidden();
  await inboxTab.press('ArrowRight');
  await expect(root.getByRole('tab', { name: 'Document' })).toBeFocused();
  await expect(root.locator('[data-workbench-panel="document"]')).toBeVisible();

  await root.getByRole('tab', { name: 'Details' }).click();
  await expect(root.locator('[data-workbench-panel="details"]')).toBeVisible();
  await root.locator(
    '[data-workbench-panel="details"] [data-return-document]'
  ).press('Escape');
  await expect(root.getByRole('tab', { name: 'Document' })).toBeFocused();
  await expect(root.locator('[data-workbench-panel="document"]')).toBeVisible();

  await page.screenshot({
    path: path.join(screenshotRoot, 'uix19-mobile-document.png'),
    fullPage: true
  });
});

test('mobile touch, contrast, overflow and reduced motion meet local acceptance', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.setViewportSize({ width: 390, height: 844 });
  const root = await openInbox(page);

  const undersized = await root.evaluate(element => Array.from(
    element.querySelectorAll('button:not(:disabled), input:not(:disabled)')
  ).flatMap(control => {
    const style = getComputedStyle(control);
    if (
      style.display === 'none'
      || style.visibility === 'hidden'
      || control.getClientRects().length === 0
    ) return [];
    const rect = control.getBoundingClientRect();
    return rect.width < 44 || rect.height < 44
      ? [{ label: control.getAttribute('aria-label') || control.textContent.trim(), width: rect.width, height: rect.height }]
      : [];
  }));
  expect(undersized).toEqual([]);

  const layoutBounds = await root.evaluate(element => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth
  }));
  expect(layoutBounds.scrollWidth).toBeLessThanOrEqual(layoutBounds.clientWidth + 2);

  const activeAnimations = await root.evaluate(element => Array.from(
    element.querySelectorAll('*')
  ).flatMap(node => {
    const style = getComputedStyle(node);
    const durations = style.animationDuration.split(',').map(value => parseFloat(value) || 0);
    return Math.max(...durations) > 0.011
      ? [{ tag: node.tagName.toLowerCase(), animation: style.animationName, duration: style.animationDuration }]
      : [];
  }));
  expect(activeAnimations).toEqual([]);

  const lowContrast = await root.evaluate(element => {
    function rgba(value) {
      const match = String(value || '').match(/rgba?\(([^)]+)\)/);
      if (!match) return null;
      const parts = match[1].split(/[ ,/]+/).filter(Boolean).map(Number);
      return {
        r: parts[0], g: parts[1], b: parts[2],
        a: Number.isFinite(parts[3]) ? parts[3] : 1
      };
    }
    function blend(front, back) {
      const alpha = front.a + back.a * (1 - front.a);
      return {
        r: (front.r * front.a + back.r * back.a * (1 - front.a)) / alpha,
        g: (front.g * front.a + back.g * back.a * (1 - front.a)) / alpha,
        b: (front.b * front.a + back.b * back.a * (1 - front.a)) / alpha,
        a: alpha
      };
    }
    function background(node) {
      const layers = [];
      for (let current = node; current; current = current.parentElement) {
        const color = rgba(getComputedStyle(current).backgroundColor);
        if (color && color.a > 0) layers.push(color);
      }
      let result = { r: 2, g: 6, b: 13, a: 1 };
      layers.reverse().forEach(layer => { result = blend(layer, result); });
      return result;
    }
    function luminance(color) {
      const channel = value => {
        const normalized = value / 255;
        return normalized <= 0.03928
          ? normalized / 12.92
          : Math.pow((normalized + 0.055) / 1.055, 2.4);
      };
      return channel(color.r) * 0.2126
        + channel(color.g) * 0.7152
        + channel(color.b) * 0.0722;
    }
    function ratio(first, second) {
      const a = luminance(first);
      const b = luminance(second);
      return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    }
    return Array.from(element.querySelectorAll('*')).flatMap(node => {
      const ownText = Array.from(node.childNodes).some(
        child => child.nodeType === Node.TEXT_NODE && child.textContent.trim()
      );
      const style = getComputedStyle(node);
      if (
        !ownText || style.display === 'none' || style.visibility === 'hidden'
        || Number(style.opacity) < 0.5
      ) return [];
      const foreground = rgba(style.color);
      if (!foreground) return [];
      const back = background(node);
      const measured = ratio(blend(foreground, back), back);
      const fontSize = parseFloat(style.fontSize);
      const fontWeight = Number(style.fontWeight) || 400;
      const minimum = fontSize >= 24 || (fontSize >= 18.66 && fontWeight >= 700)
        ? 3 : 4.5;
      return measured + 0.01 < minimum
        ? [{ text: node.textContent.trim().slice(0, 60), measured: Number(measured.toFixed(2)), minimum }]
        : [];
    });
  });
  expect(lowContrast, JSON.stringify(lowContrast, null, 2)).toEqual([]);
});
