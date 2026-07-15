const { test, expect } = require('playwright/test');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs/promises');

const repoRoot = path.resolve(__dirname, '..', '..');
const screenshotRoot = path.join(os.tmpdir(), 'odysseus-tlr07-playwright');
let server;
let baseUrl;

const mimeTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.woff2': 'font/woff2'
};

test.beforeAll(async () => {
  await fs.mkdir(screenshotRoot, { recursive: true });
  server = http.createServer(async (request, response) => {
    try {
      const url = new URL(request.url, 'http://127.0.0.1');
      const relative = decodeURIComponent(url.pathname).replace(/^\/+/, '') || 'static/frontpage-v3/index.html';
      const target = path.resolve(repoRoot, relative);
      if (!target.startsWith(repoRoot + path.sep)) {
        response.writeHead(403).end('forbidden');
        return;
      }
      const body = await fs.readFile(target);
      response.writeHead(200, {
        'Cache-Control': 'no-store',
        'Content-Type': mimeTypes[path.extname(target).toLowerCase()] || 'application/octet-stream'
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

async function openAgent(page, scenario = 'running') {
  await page.goto(`${baseUrl}/static/frontpage-v3/index.html?workspace=agent&agent-fixture=${scenario}`);
  await expect(page.locator('[data-agent-operations-panel]')).toBeVisible();
  await expect(page.locator('[data-agent-run-state]')).not.toHaveText('unknown');
}

test('Agent renders the pinned run, server-projected controls and no Planning runtime overlay', async ({ page }) => {
  await openAgent(page);
  const panel = page.locator('[data-agent-operations-panel]');
  await expect(panel.getByText('Preview fixture · running', { exact: true })).toBeVisible();
  await expect(panel.locator('[data-agent-run-state]')).toHaveText('running');
  await expect(panel.getByText('heartbeat healthy', { exact: true })).toBeVisible();
  await expect(panel.getByRole('button', { name: 'Pause' })).toBeEnabled();
  await expect(panel.getByRole('button', { name: 'Cancel run' })).toBeEnabled();
  await expect(panel.getByRole('button', { name: 'Send steering' })).toBeEnabled();
  await expect(panel.locator('[data-agent-plan-link]')).toHaveAttribute('href', /workspace=planning/);
  await expect(panel.locator('[data-agent-plan-link]')).toHaveAttribute('href', /revision=12/);

  const planningText = ((await page.locator('[data-planning-root]').textContent()) || '').toLowerCase();
  for (const forbidden of ['heartbeat healthy', 'cancel run', 'temporal-demo-run', 'claim-tlr07']) {
    expect(planningText).not.toContain(forbidden);
  }
  const persisted = await page.evaluate(() => JSON.stringify(localStorage));
  expect(persisted).not.toContain('arun-');
  expect(persisted).not.toContain('sha256:');
  expect(persisted).not.toContain('workflow_run_id');
});

test('every run and heartbeat preview state is explicit', async ({ page }) => {
  await openAgent(page);
  const panel = page.locator('[data-agent-operations-panel]');
  const select = panel.locator('[data-agent-fixture-select]');
  const states = [
    ['running', 'running', 'heartbeat healthy'],
    ['waiting_gate', 'waiting_gate', 'heartbeat late'],
    ['paused', 'paused', 'heartbeat not_expected'],
    ['retry_wait', 'running', 'heartbeat stale'],
    ['waiting_signal', 'waiting_signal', 'heartbeat not_expected'],
    ['completed', 'completed', 'heartbeat not_expected'],
    ['failed', 'failed', 'heartbeat not_expected'],
    ['timed_out', 'timed_out', 'heartbeat not_expected'],
    ['cancelled', 'cancelled', 'heartbeat not_expected']
  ];
  for (const [scenario, runState, heartbeat] of states) {
    await select.selectOption(scenario);
    await expect(panel.locator('[data-agent-run-state]')).toHaveText(runState);
    await expect(panel.getByText(heartbeat, { exact: true })).toBeVisible();
  }
});

test('commands apply through the API adapter and reread authoritative state', async ({ page }) => {
  await openAgent(page);
  const panel = page.locator('[data-agent-operations-panel]');
  await panel.getByRole('button', { name: 'Pause' }).click();
  await expect(panel.locator('[data-agent-run-state]')).toHaveText('paused');
  await expect(panel.getByRole('button', { name: 'Resume' })).toBeEnabled();
  await expect(panel.locator('[data-agent-command-receipt]')).toContainText('applied · v19');
  await panel.getByRole('button', { name: 'Resume' }).click();
  await expect(panel.locator('[data-agent-run-state]')).toHaveText('running');
});

test('history reconnect appends complete events once and remains bounded by contract', async ({ page }) => {
  await openAgent(page);
  const panel = page.locator('[data-agent-operations-panel]');
  await expect(panel.locator('[data-agent-stream-state]')).toHaveText('live · reconnect safe');
  await expect(panel.locator('[data-event-id="h1:3"]')).toHaveCount(1);
  await panel.getByRole('button', { name: 'Refresh run projection' }).click();
  await expect(panel.locator('[data-event-id="h1:3"]')).toHaveCount(1);
  expect(await panel.locator('[data-agent-history-list] > li').count()).toBeLessThanOrEqual(300);
  expect(await page.evaluate(() => window.HarborAgentOperations.MAX_RENDERED_EVENTS)).toBe(300);
});

test('hour 6 and hour 18 reconnects follow history segments without duplicate or unbounded DOM rows', async ({ page }) => {
  await openAgent(page);
  await page.evaluate(() => {
    const prototype = window.HarborAgentOperationFixtures.FixtureAgentOperationsApi.prototype;
    prototype.getRun = function () {
      this.reconnectCount = (this.reconnectCount || 0) + 1;
      this.projection.run.history_segment = this.reconnectCount === 1 ? 1 : 3;
      this.projection.run.version = this.reconnectCount === 1 ? 24 : 72;
      return Promise.resolve(JSON.parse(JSON.stringify(this.projection)));
    };
    prototype.getHistory = function () {
      const events = [];
      for (let segment = 0; segment <= this.projection.run.history_segment; segment += 1) {
        for (let eventId = 1; eventId <= 80; eventId += 1) {
          events.push({
            event_id: `h${segment}:${eventId}`,
            event_type: 'heartbeat_opportunity_window',
            occurred_at: `2026-07-15T${String(8 + segment).padStart(2, '0')}:00:00Z`,
            node_id: 'TLR-09-24h-time-skipping-acceptance',
            activity_id: 'activity-tlr09-acceptance',
            summary: 'Bounded reconnect history event.',
            ref_ids: [`segment-${segment}`]
          });
        }
      }
      return Promise.resolve({
        cursor: '',
        next_cursor: events.at(-1).event_id,
        has_more: false,
        events
      });
    };
  });

  const panel = page.locator('[data-agent-operations-panel]');
  await panel.getByRole('button', { name: 'Refresh run projection' }).click();
  await expect(panel.locator('[data-agent-segment]')).toContainText('segment 1');
  await panel.getByRole('button', { name: 'Refresh run projection' }).click();
  await expect(panel.locator('[data-agent-segment]')).toContainText('segment 3');

  const rows = panel.locator('[data-agent-history-list] > li');
  expect(await rows.count()).toBe(300);
  const ids = await rows.evaluateAll(elements => elements.map(element => element.dataset.eventId));
  expect(new Set(ids).size).toBe(ids.length);
  expect(ids.at(-1)).toBe('h3:80');
  const persisted = await page.evaluate(() => JSON.stringify(localStorage));
  expect(persisted).not.toContain('history_segment');
  expect(persisted).not.toContain('workflow_run_id');
});

test('Agent API rejects malformed projections before rendering', async ({ page }) => {
  await openAgent(page);
  const result = await page.evaluate(async () => {
    const response = body => ({ ok: true, status: 200, json: async () => body });
    const api = new window.HarborAgentApi.AgentOperationsApi({
      fetchImpl: async () => response({ schema_id: 'wrong', run: {}, activities: [], claims: [], gates: [], evidence: [] })
    });
    try {
      await api.getRun('arun-' + '1'.repeat(32));
      return 'accepted';
    } catch (error) {
      return error.code;
    }
  });
  expect(result).toBe('invalid_projection');
});

test('desktop, mobile and 200 percent zoom preserve readable operation controls', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await openAgent(page);
  await page.screenshot({ path: path.join(screenshotRoot, 'tlr07-desktop.png'), fullPage: true });
  await expect(page.locator('[data-agent-operations-panel]')).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await openAgent(page, 'waiting_gate');
  const mobilePanel = page.locator('[data-agent-operations-panel]');
  await expect(mobilePanel.getByRole('button', { name: 'Approve gate' })).toBeVisible();
  const touchTargets = await mobilePanel.locator('button, select, input').evaluateAll(elements => elements.filter(element => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return style.display !== 'none' && style.visibility !== 'hidden' && (rect.width < 44 || rect.height < 44);
  }).map(element => ({ label: element.getAttribute('aria-label') || element.textContent.trim(), rect: element.getBoundingClientRect().toJSON() })));
  expect(touchTargets).toEqual([]);
  await page.screenshot({ path: path.join(screenshotRoot, 'tlr07-mobile.png'), fullPage: true });

  await page.setViewportSize({ width: 720, height: 450 });
  await openAgent(page, 'retry_wait');
  const zoomPanel = page.locator('[data-agent-operations-panel]');
  await expect(zoomPanel.getByText('heartbeat stale', { exact: true })).toBeVisible();
  const bounds = await zoomPanel.evaluate(element => ({ clientWidth: element.clientWidth, scrollWidth: element.scrollWidth }));
  expect(bounds.scrollWidth).toBeLessThanOrEqual(bounds.clientWidth + 2);
  await page.screenshot({ path: path.join(screenshotRoot, 'tlr07-zoom-200.png'), fullPage: true });
});
