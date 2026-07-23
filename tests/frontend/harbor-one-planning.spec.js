const { test, expect } = require('playwright/test');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const fs = require('node:fs/promises');

const repoRoot = path.resolve(__dirname, '..', '..');
const screenshotRoot = path.join(os.tmpdir(), 'odysseus-pde05-playwright');
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

async function openPlanning(page, query = 'planningSource=fixture&planningScenario=fixture') {
  await page.goto(`${baseUrl}/static/frontpage-v3/index.html?workspace=planning&${query}`);
  await expect(page.locator('[data-planning-root]')).not.toHaveAttribute('data-planning-scenario', 'loading');
}

function liveContract() {
  const hash = 'sha256:' + '3b'.repeat(32);
  const project = {
    project_id: 'api-project',
    title: 'API Project',
    objective: 'Read one canonical definition.',
    scope: { in: ['Definition authoring'], out: ['Post-handoff operation'] },
    constraints: ['Definition only'],
    roadmap_refs: ['api-roadmap'],
    latest_approved_revision: { 'api-roadmap': { revision: 2, content_hash: hash } },
    draft_refs: []
  };
  const nodes = [
    {
      node_id: 'shape', kind: 'work', title: 'Shape definition', objective: 'Describe the intended change.',
      depends_on: [], gate_ids: [], deliverables: ['Definition'], allowed_paths: ['docs/plans/api-roadmap.json'],
      blocked_paths: [], capability_requirements: ['Repository read'], verification_rule_ids: ['definition-valid']
    },
    {
      node_id: 'acceptance', kind: 'milestone', title: 'Definition accepted', objective: 'Meet the done contract.',
      depends_on: ['shape'], gate_ids: [], deliverables: ['Readback'], allowed_paths: [], blocked_paths: [],
      capability_requirements: [], verification_rule_ids: ['definition-valid']
    }
  ];
  const roadmap = {
    roadmap_id: 'api-roadmap', project_id: project.project_id, revision: 2, content_hash: hash,
    revision_state: 'approved', title: 'API Roadmap', objective: 'Prove the live definition reader.',
    assumptions: ['API is local'], constraints: ['No automatic submission'], nodes,
    edges: [{ from: 'shape', to: 'acceptance', kind: 'depends_on' }], gates: [],
    done_contract: {
      required_node_ids: ['shape', 'acceptance'], required_gate_ids: [],
      verification_rules: [{ rule_id: 'definition-valid', kind: 'static', description: 'Definition validates.' }],
      completion_rule: 'all_required_nodes_and_gates'
    },
    source_refs: ['docs/plans/api-roadmap.json'],
    created_at: '2026-07-15T10:00:00Z', updated_at: '2026-07-15T11:00:00Z'
  };
  const summary = {
    project_id: project.project_id, roadmap_id: roadmap.roadmap_id, title: roadmap.title,
    revision_count: 2, newest_revision: 2, newest_revision_state: 'approved',
    latest_approved_revision: 2, latest_approved_hash: hash, updated_at: roadmap.updated_at
  };
  const readModel = {
    schema: 'odysseus.planning.definition_read_model.v2', project, roadmap,
    graph: { nodes, edges: roadmap.edges, gate_definitions: [] },
    origin: { state: 'live', source: 'planning_revision_store', reason: 'definition_snapshot_loaded', as_of: roadmap.updated_at },
    read_only: true, launch_authorized: false
  };
  return { hash, project, roadmap, summary, readModel };
}

async function routeLiveContract(page, contract) {
  let handoffPosts = 0;
  await page.route('**/api/planning/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    let body;
    if (request.method() === 'POST' && pathname.endsWith('/agent-handoff')) {
      handoffPosts += 1;
      body = {
        schema_id: 'odysseus.agent.plan_handoff.v1',
        project_id: contract.project.project_id,
        roadmap_id: contract.roadmap.roadmap_id,
        revision: contract.roadmap.revision,
        content_hash: contract.hash,
        title: contract.roadmap.title,
        requested_entrypoint: '/abc',
        composer_text: `/abc run roadmap:${contract.roadmap.roadmap_id}@${contract.roadmap.revision} hash:${contract.hash}`,
        launch_authorized: false,
        read_only: true
      };
    } else if (pathname.endsWith('/projects')) {
      body = { items: [{ project_id: contract.project.project_id, title: contract.project.title, roadmap_count: 1, revision_count: 2, latest_updated_at: contract.roadmap.updated_at }], has_more: false, next_cursor: '', raw_private_content_visible: false };
    } else if (pathname.endsWith(`/projects/${contract.project.project_id}`)) {
      body = { schema: 'odysseus.planning.project_read_model.v2', project: contract.project, roadmaps: [contract.summary], origin: contract.readModel.origin, read_only: true, launch_authorized: false };
    } else if (pathname.endsWith('/roadmaps')) {
      body = { items: [contract.summary], has_more: false, next_cursor: '', raw_private_content_visible: false };
    } else if (pathname.endsWith(`/roadmaps/${contract.roadmap.roadmap_id}`)) {
      body = contract.readModel;
    } else {
      await route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not found' }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  return () => handoffPosts;
}

test('fixture surface is explicitly labeled and contains definition semantics only', async ({ page }) => {
  await openPlanning(page);
  const root = page.locator('[data-planning-root]');
  await expect(root).toHaveAttribute('data-origin-state', 'live');
  await expect(root.getByText('Preview fixture', { exact: true })).toBeVisible();
  await expect(root.getByText('Definition only', { exact: true })).toBeVisible();
  await expect(root.getByRole('heading', { name: 'Planning Definition Editor' })).toBeVisible();
  await expect(root.locator('[data-node-kind="work"]')).toHaveCount(4);
  await expect(root.locator('[data-node-kind="gate"]')).toHaveCount(1);
  await expect(root.locator('[data-definition-validation="unvalidated"]')).toHaveCount(7);

  const forbiddenControls = /pause|resume|cancel|terminate|retry|heartbeat|history|claim|lease|worker/i;
  await expect(root.getByRole('button', { name: forbiddenControls })).toHaveCount(0);
  const planningText = (await root.innerText()).toLowerCase();
  for (const label of ['heartbeat', 'workflow id', 'claim id', 'lease id', 'retry count', 'changed files', 'commit id']) {
    expect(planningText).not.toContain(label);
  }
  const persisted = await page.evaluate(() => JSON.stringify(localStorage));
  expect(persisted).not.toContain('planning-definition-editor');
  expect(persisted).not.toContain('sha256:');
});

test('live definition loading is explicit and resolves without substituting fixtures', async ({ page }) => {
  await page.route('**/api/planning/projects?*', async route => {
    await new Promise(resolve => setTimeout(resolve, 350));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        items: [], has_more: false, next_cursor: '', raw_private_content_visible: false
      })
    });
  });
  await page.goto(`${baseUrl}/static/frontpage-v3/index.html?workspace=planning&planningSource=live`);
  const root = page.locator('[data-planning-root]');
  await expect(root.getByText('Reading Planning definitions…', { exact: true })).toBeVisible();
  await expect(root.getByText('No definitions yet', { exact: true })).toBeVisible();
  await expect(root.getByText('Preview fixture', { exact: true })).toHaveCount(0);
});

test('visible Planning text meets WCAG AA contrast and mobile controls are touch safe', async ({ page }) => {
  await openPlanning(page);
  const contrastViolations = await page.locator('[data-planning-root]').evaluate(root => {
    function parseColor(value) {
      const match = String(value || '').match(/rgba?\(([^)]+)\)/);
      if (!match) return null;
      const parts = match[1].split(/[ ,/]+/).filter(Boolean).map(Number);
      return { r: parts[0], g: parts[1], b: parts[2], a: Number.isFinite(parts[3]) ? parts[3] : 1 };
    }
    function blend(foreground, background) {
      const alpha = foreground.a + background.a * (1 - foreground.a);
      if (!alpha) return { r: 0, g: 0, b: 0, a: 0 };
      return {
        r: (foreground.r * foreground.a + background.r * background.a * (1 - foreground.a)) / alpha,
        g: (foreground.g * foreground.a + background.g * background.a * (1 - foreground.a)) / alpha,
        b: (foreground.b * foreground.a + background.b * background.a * (1 - foreground.a)) / alpha,
        a: alpha
      };
    }
    function backgroundFor(element) {
      const layers = [];
      for (let current = element; current; current = current.parentElement) {
        const color = parseColor(getComputedStyle(current).backgroundColor);
        if (color && color.a > 0) layers.push(color);
      }
      let result = { r: 2, g: 6, b: 13, a: 1 };
      layers.reverse().forEach(layer => { result = blend(layer, result); });
      return result;
    }
    function luminance(color) {
      const channel = value => {
        const normalized = value / 255;
        return normalized <= 0.03928 ? normalized / 12.92 : Math.pow((normalized + 0.055) / 1.055, 2.4);
      };
      return channel(color.r) * 0.2126 + channel(color.g) * 0.7152 + channel(color.b) * 0.0722;
    }
    function ratio(a, b) {
      const first = luminance(a);
      const second = luminance(b);
      return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
    }
    return Array.from(root.querySelectorAll('*')).flatMap(element => {
      const ownText = Array.from(element.childNodes).some(node => node.nodeType === Node.TEXT_NODE && node.textContent.trim());
      const style = getComputedStyle(element);
      if (!ownText || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) < 0.5) return [];
      const foreground = parseColor(style.color);
      if (!foreground) return [];
      const background = backgroundFor(element);
      const effective = blend(foreground, background);
      const fontSize = parseFloat(style.fontSize);
      const fontWeight = Number(style.fontWeight) || 400;
      const large = fontSize >= 24 || (fontSize >= 18.66 && fontWeight >= 700);
      const minimum = large ? 3 : 4.5;
      const measured = ratio(effective, background);
      return measured + 0.01 < minimum ? [{
        tag: element.tagName.toLowerCase(),
        text: element.textContent.trim().slice(0, 80),
        contrast: Number(measured.toFixed(2)),
        minimum,
        color: style.color,
        background: style.backgroundColor
      }] : [];
    });
  });
  expect(contrastViolations, JSON.stringify(contrastViolations, null, 2)).toEqual([]);

  await page.setViewportSize({ width: 390, height: 844 });
  await openPlanning(page);
  const undersized = await page.locator('[data-planning-root]').evaluate(root => Array.from(
    root.querySelectorAll('button:not(:disabled), input:not(:disabled), textarea:not(:disabled)')
  ).flatMap(element => {
    const style = getComputedStyle(element);
    if (style.display === 'none' || style.visibility === 'hidden') return [];
    const rect = element.getBoundingClientRect();
    return rect.width + 0.1 < 44 || rect.height + 0.1 < 44
      ? [{ label: element.getAttribute('aria-label') || element.textContent.trim().slice(0, 60), width: rect.width, height: rect.height }]
      : [];
  }));
  expect(undersized, JSON.stringify(undersized, null, 2)).toEqual([]);
});

test('draft diff validates, accepts as preview, discards and undoes without source write', async ({ page }) => {
  await openPlanning(page);
  const root = page.locator('[data-planning-root]');
  const approvedHash = await page.evaluate(() => window.HarborPlanning.getState().catalog.readModel.roadmap.content_hash);
  await root.getByRole('button', { name: 'Edit definition' }).click();
  const objective = root.locator('[data-pde-field="objective"]');
  await objective.fill('Refine the definition editor while preserving the Planning boundary.');
  await expect(root.getByText('Draft diff', { exact: true })).toBeVisible();
  await expect(root.locator('.pde-diff-list article')).toHaveCount(1);
  await root.getByRole('button', { name: 'Validate' }).click();
  await expect(root.locator('.pde-validation-pill')).toHaveText('valid');
  await root.getByRole('button', { name: 'Accept preview' }).click();
  await expect(root.getByText('Accepted preview', { exact: true }).first()).toBeVisible();
  await expect(root.getByText('source unchanged', { exact: true })).toBeVisible();
  await expect(root.getByRole('button', { name: 'Open in Agent' })).toBeDisabled();
  await root.getByRole('button', { name: 'Undo preview' }).click();
  await expect(root.getByText('Preview fixture', { exact: true })).toBeVisible();
  await expect(root.getByRole('button', { name: 'Open in Agent' })).toBeEnabled();
  const undoReadback = await page.evaluate(() => {
    const state = window.HarborPlanning.getState();
    return {
      hash: state.catalog.readModel.roadmap.content_hash,
      undoReadbackHash: state.undoReadbackHash,
      localPreviewAccepted: state.localPreviewAccepted
    };
  });
  expect(undoReadback).toEqual({
    hash: approvedHash,
    undoReadbackHash: approvedHash,
    localPreviewAccepted: false
  });

  await root.getByRole('button', { name: 'Edit definition' }).click();
  await root.locator('[data-pde-field="title"]').fill('Discarded title');
  await root.getByRole('button', { name: 'Discard' }).click();
  await expect(root.getByRole('heading', { name: 'Planning Definition Editor' })).toBeVisible();
});

test('fixture handoff drafts the exact approved revision in Agent and never submits it', async ({ page }) => {
  await openPlanning(page);
  await page.evaluate(() => {
    window.__pdeDraftEvents = 0;
    window.__pdeSendClicks = 0;
    window.addEventListener('harbor:agent-composer-drafted', () => { window.__pdeDraftEvents += 1; });
    document.querySelector('.send-btn').addEventListener('click', () => { window.__pdeSendClicks += 1; });
  });
  await page.locator('[data-planning-root]').getByRole('button', { name: 'Open in Agent' }).click();
  await expect(page.locator('[data-workspace-screen="agent"]')).toHaveClass(/active/);
  const expected = `/abc run roadmap:planning-definition-editor@5 hash:${'sha256:' + '7a'.repeat(32)}`;
  await expect(page.locator('.prompt-input')).toHaveValue(expected);
  const counters = await page.evaluate(() => ({ drafts: window.__pdeDraftEvents, sends: window.__pdeSendClicks }));
  expect(counters).toEqual({ drafts: 1, sends: 0 });
});

test('canonical API read and handoff preserve the definition boundary', async ({ page }) => {
  const contract = liveContract();
  const handoffPosts = await routeLiveContract(page, contract);
  await openPlanning(page, 'planningSource=live');
  const root = page.locator('[data-planning-root]');
  await expect(root.getByText('Canonical definition source', { exact: true })).toBeVisible();
  await expect(root.getByRole('heading', { name: contract.roadmap.title })).toBeVisible();
  await root.getByRole('button', { name: 'Open in Agent' }).click();
  await expect(page.locator('.prompt-input')).toHaveValue(`/abc run roadmap:api-roadmap@2 hash:${contract.hash}`);
  expect(handoffPosts()).toBe(1);
});

test('definition notification deep link opens the exact Planning revision', async ({ page }) => {
  const contract = liveContract();
  const requested = [];
  page.on('request', request => {
    if (request.url().includes(`/roadmaps/${contract.roadmap.roadmap_id}`)) requested.push(request.url());
  });
  await routeLiveContract(page, contract);
  await openPlanning(page, [
    'planningSource=live',
    'notificationEvent=roadmap_revision_approved',
    `notificationProject=${contract.project.project_id}`,
    `notificationRoadmap=${contract.roadmap.roadmap_id}`,
    `notificationRevision=${contract.roadmap.revision}`
  ].join('&'));

  const root = page.locator('[data-planning-root]');
  await expect(root).toHaveAttribute('data-notification-target', 'planning');
  await expect(root.locator('[data-pde-notification-event="roadmap_revision_approved"]')).toContainText('exact definition selected');
  await expect(root.getByRole('heading', { name: contract.roadmap.title })).toBeVisible();
  const selected = await page.evaluate(() => {
    const state = window.HarborPlanning.getState();
    return {
      projectId: state.catalog.readModel.roadmap.project_id,
      roadmapId: state.catalog.readModel.roadmap.roadmap_id,
      revision: state.catalog.readModel.roadmap.revision
    };
  });
  expect(selected).toEqual({ projectId: 'api-project', roadmapId: 'api-roadmap', revision: 2 });
  expect(requested.some(url => new URL(url).searchParams.get('revision') === '2')).toBe(true);
});

test('execution notification routes to Agent and never renders execution data in Planning', async ({ page }) => {
  await page.addInitScript(() => {
    window.__notificationWorkspaceRoutes = [];
    window.addEventListener('harbor:notification-workspace-route', event => {
      window.__notificationWorkspaceRoutes.push(event.detail);
    });
  });
  await openPlanning(page, 'planningSource=fixture&notificationEvent=heartbeat_late');
  const root = page.locator('[data-planning-root]');
  await expect(root).toHaveAttribute('data-notification-target', 'agent');
  await expect(root.getByText('Execution notifications are handled in Agent.', { exact: true })).toBeVisible();
  expect((await root.textContent()).toLowerCase()).not.toContain('heartbeat');
  const routes = await page.evaluate(() => window.__notificationWorkspaceRoutes);
  expect(routes).toEqual([{ workspace: 'agent', acceptedByPlanning: false }]);
});

test('runtime-shaped API payloads fail closed before rendering', async ({ page }) => {
  await openPlanning(page);
  const results = await page.evaluate(() => {
    const check = window.HarborPlanningApi.assertDefinitionPayload;
    const capture = value => {
      try { check(value); return 'accepted'; } catch (error) { return error.code; }
    };
    return [
      capture({ roadmap: { heartbeat_at: 'now' } }),
      capture({ gates: [{ gate_id: 'g', state: 'approved' }] }),
      capture({ roadmap: { title: 'Definition only' } })
    ];
  });
  expect(results).toEqual(['runtime_payload_rejected', 'runtime_payload_rejected', 'accepted']);
});

for (const [scenario, expected] of [
  ['stale', 'older snapshot scenario'],
  ['unavailable', 'Definition source unavailable'],
  ['error', 'Definition response rejected'],
  ['conflict', 'Revision conflict'],
  ['empty', 'No definitions yet']
]) {
  test(`${scenario} definition scenario is explicit`, async ({ page }) => {
    await openPlanning(page, `planningSource=fixture&planningScenario=${scenario}`);
    const root = page.locator('[data-planning-root]');
    await expect(root.getByText(expected, { exact: true }).first()).toBeVisible();
    if (['unavailable', 'error', 'empty'].includes(scenario)) {
      await expect(root.getByRole('button', { name: 'Load labeled preview' })).toBeVisible();
    }
  });
}

test('desktop, mobile and 200 percent zoom remain readable', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await openPlanning(page);
  await page.screenshot({ path: path.join(screenshotRoot, 'pde05-desktop.png'), fullPage: true });
  await expect(page.locator('[data-planning-root] .pde-layout')).toBeVisible();

  await page.setViewportSize({ width: 390, height: 844 });
  await openPlanning(page);
  await expect(page.locator('[data-planning-root] .pde-toolbar')).toBeVisible();
  await expect(page.locator('[data-planning-root] .pde-roadmap-list')).toBeVisible();
  await page.screenshot({ path: path.join(screenshotRoot, 'pde05-mobile.png'), fullPage: true });

  // A 1440x900 display at 200% browser zoom exposes a 720x450 CSS viewport.
  await page.setViewportSize({ width: 720, height: 450 });
  await openPlanning(page);
  await expect(page.locator('[data-planning-root] .pde-source-bar')).toBeVisible();
  await expect(page.locator('[data-planning-root] .pde-toolbar')).toBeVisible();
  await page.screenshot({ path: path.join(screenshotRoot, 'pde05-zoom-200.png'), fullPage: true });

  const bounds = await page.locator('[data-planning-root]').evaluate(element => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth
  }));
  expect(bounds.scrollWidth).toBeLessThanOrEqual(bounds.clientWidth + 2);
});
