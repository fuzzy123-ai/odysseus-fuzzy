const { test, expect } = require('playwright/test');
const http = require('node:http');
const path = require('node:path');
const fs = require('node:fs/promises');
const { ForgeApi } = require('../../static/frontpage-v3/forge-api.js');

const repoRoot = path.resolve(__dirname, '..', '..');
const screenshotRoot = path.join(repoRoot, '.abc-test-tmp', 'closure-forge-ui-20261001');
let server;
let baseUrl;

const mimeTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.png': 'image/png',
  '.svg': 'image/svg+xml'
};

test.beforeAll(async () => {
  await fs.mkdir(screenshotRoot, { recursive: true });
  server = http.createServer(async (request, response) => {
    try {
      const relative = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname).replace(/^\/+/, '') || 'static/frontpage-v3/index.html';
      const target = path.resolve(repoRoot, relative);
      if (!target.startsWith(repoRoot + path.sep)) return response.writeHead(403).end('forbidden');
      const body = await fs.readFile(target);
      response.writeHead(200, { 'content-type': mimeTypes[path.extname(target).toLowerCase()] || 'application/octet-stream' });
      response.end(body);
    } catch {
      response.writeHead(404).end('not found');
    }
  });
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

test.afterAll(async () => {
  if (server) await new Promise(resolve => server.close(resolve));
});

function repo(id, title) {
  return { repo_id: id, title, repo_kind: 'project', owner: 'local-user', privacy_class: 'private', provider_scope: 'local_only', remote_count: 0, push_remote_count: 0, allowed_actions: [] };
}

function detail(id, branch = 'main', dirty = false) {
  return {
    repo: { repo_id: id, title: id === 'alpha' ? 'Alpha Workspace' : 'Beta Workspace', current_branch: branch, default_branch: 'main' },
    git: { repo_id: id, current_branch: branch, status: { repo_id: id, branch_line: `## ${branch}`, dirty, entries: dirty ? [' M static/frontpage-v3/index.html'] : [] }, changed_paths: [], commits: [], diff_stat: '', remotes: [] },
    policy: { schema: 'odysseus.project_forge_policy.v1', forge_mode: 'local', sync_on_commit: false, backup_providers: [], nextcloud: {}, github: {} },
    versions: [{ version_id: 'pv_' + 'a'.repeat(32), transaction_id: 'pct_' + 'b'.repeat(32), commit_sha: 'c'.repeat(40), created_at: '2026-09-30T10:00:00Z', version_label: 'Baseline', change_notes: [], artifact_count: 0, manifest_sha256: 'sha256:' + 'd'.repeat(64), verified: true }]
  };
}

async function openAgent(page, repos, selectedDetail = detail('alpha', 'feature/ui', true)) {
  await page.route('**/api/repos', async route => {
    const url = new URL(route.request().url());
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ schema_version: 1, repo_count: repos.length, repos }) });
  });
  await page.route('**/api/repos/**', async route => {
    const url = new URL(route.request().url());
    const repoId = url.pathname.split('/')[3];
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(selectedDetail.repo.repo_id === repoId ? selectedDetail : detail(repoId)) });
  });
  await page.route('**/api/project-versioning/**', route => {
    const pathname = new URL(route.request().url()).pathname;
    const repoId = pathname.split('/')[3];
    const body = pathname.endsWith('/policy')
      ? { repo_id: repoId, policy: selectedDetail.policy }
      : { repo_id: repoId, versions: selectedDetail.versions, count: selectedDetail.versions.length };
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto(`${baseUrl}/static/frontpage-v3/index.html?workspace=agent`);
  await expect(page.locator('[data-forge-status-root]')).toHaveAttribute('data-forge-state', repos.length > 1 ? 'selection' : 'ready');
}

test('renders the selected repository branch, worktree, version and policy from read-only APIs', async ({ page }) => {
  const selectedDetail = detail('alpha', 'feature/ui', true);
  selectedDetail.versions = [
    { ...selectedDetail.versions[0], version_id: 'pv_' + 'b'.repeat(32), created_at: '2026-09-29T10:00:00Z', version_label: 'Older' },
    { ...selectedDetail.versions[0], version_id: 'pv_' + 'a'.repeat(32), created_at: '2026-09-30T10:00:00Z', version_label: 'Current' }
  ];
  await openAgent(page, [repo('alpha', 'Alpha Workspace')], selectedDetail);
  const chip = page.locator('[data-forge-status-root]');
  await expect(chip.locator('[data-forge-branch]')).toHaveText('feature/ui');
  await expect(chip.locator('[data-forge-summary]')).toHaveText('1 changed path');
  await chip.locator('[data-forge-status-toggle]').click();
  await expect(chip.getByText('Current', { exact: false })).toBeVisible();
  await expect(chip.locator('[data-forge-detail-policy]')).toHaveText('local');
  await expect(chip.getByText(/2405|nkatz\/odysseus|Since commit|8 modified|dev/)).toHaveCount(0);
  await page.locator('[data-agent-operation-close]').click();
  await chip.locator('[data-forge-status-toggle]').click();
  const desktopBox = await chip.boundingBox();
  expect(desktopBox).not.toBeNull();
  expect(desktopBox.y + desktopBox.height).toBeLessThanOrEqual(900);
  await page.screenshot({ path: path.join(screenshotRoot, 'forge-status-desktop.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await chip.scrollIntoViewIfNeeded();
  await expect(chip).toBeVisible();
  const mobileBox = await chip.boundingBox();
  await expect(chip.locator('[data-forge-repo-title]')).toHaveText('Alpha Workspace');
  expect(mobileBox).not.toBeNull();
  expect(mobileBox.width).toBeGreaterThan(40);
  expect(mobileBox.y).toBeGreaterThanOrEqual(0);
  expect(mobileBox.x).toBeGreaterThanOrEqual(0);
  expect(mobileBox.x + mobileBox.width).toBeLessThanOrEqual(390);
  expect(mobileBox.y + mobileBox.height).toBeLessThanOrEqual(844);
  await page.screenshot({ path: path.join(screenshotRoot, 'forge-status-mobile.png'), fullPage: true });
});

test('offers a repository picker when the registry contains multiple choices', async ({ page }) => {
  await openAgent(page, [repo('alpha', 'Alpha Workspace'), repo('beta', 'Beta Workspace')]);
  const chip = page.locator('[data-forge-status-root]');
  await chip.locator('[data-forge-status-toggle]').click();
  const picker = chip.locator('[data-forge-repo-select]');
  await expect(picker).toBeVisible();
  await picker.selectOption('alpha');
  await expect(chip.locator('[data-forge-repo-title]')).toHaveText('Alpha Workspace');
  await expect(chip.locator('[data-forge-detail-branch]')).toHaveText('feature/ui');
});

test('late repository responses cannot overwrite the latest selection or error', async ({ page }) => {
  const repos = [repo('alpha', 'Alpha Workspace'), repo('beta', 'Beta Workspace')];
  await page.route('**/api/repos', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ repo_count: 2, repos }) }));
  await page.route('**/api/repos/**', async route => {
    const repoId = new URL(route.request().url()).pathname.split('/')[3];
    if (repoId === 'alpha') await new Promise(resolve => setTimeout(resolve, 250));
    if (repoId === 'beta') return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'forbidden' }) });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(detail('alpha', 'feature/ui', true)) });
  });
  await page.route('**/api/project-versioning/**', async route => {
    const repoId = new URL(route.request().url()).pathname.split('/')[3];
    if (repoId === 'alpha') await new Promise(resolve => setTimeout(resolve, 250));
    if (repoId === 'beta') return route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'forbidden' }) });
    const pathname = new URL(route.request().url()).pathname;
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(pathname.endsWith('/policy')
      ? { repo_id: 'alpha', policy: detail('alpha').policy }
      : { repo_id: 'alpha', versions: detail('alpha').versions, count: 1 }) });
  });
  await page.goto(`${baseUrl}/static/frontpage-v3/index.html?workspace=agent`);
  const chip = page.locator('[data-forge-status-root]');
  const picker = chip.locator('[data-forge-repo-select]');
  await expect(chip).toHaveAttribute('data-forge-state', 'selection');
  await chip.locator('[data-forge-status-toggle]').click();
  await picker.selectOption('alpha');
  await picker.selectOption('beta');
  await expect(chip).toHaveAttribute('data-forge-state', 'error');
  await page.waitForTimeout(350);
  await expect(chip).toHaveAttribute('data-forge-state', 'error');
  await expect(chip.locator('[data-forge-summary]')).toHaveText('Status unavailable');
});

test('fails honestly for an unauthorized registry and closes with Escape', async ({ page }) => {
  await page.route('**/api/repos', route => route.fulfill({ status: 403, contentType: 'application/json', body: JSON.stringify({ detail: 'forbidden' }) }));
  await page.goto(`${baseUrl}/static/frontpage-v3/index.html?workspace=agent`);
  const chip = page.locator('[data-forge-status-root]');
  await expect(chip).toHaveAttribute('data-forge-state', 'error');
  await expect(chip.locator('[data-forge-summary]')).toHaveText('Status unavailable');
  await chip.locator('[data-forge-status-toggle]').click();
  await expect(chip.locator('[data-forge-status-popover]')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(chip.locator('[data-forge-status-popover]')).toBeHidden();
  await expect(chip.locator('[data-forge-status-toggle]')).toBeFocused();
});

function jsonResponse(payload) {
  const body = JSON.stringify(payload);
  return { ok: true, status: 200, text: async () => body };
}

test('rejects empty malformed and mismatched read payloads instead of filtering them into success', async () => {
  const emptyApi = new ForgeApi({ fetchImpl: async () => jsonResponse({ repos: [{}] }) });
  await expect(emptyApi.listRepos()).rejects.toMatchObject({ code: 'invalid_payload' });

  const mismatchApi = new ForgeApi({ fetchImpl: async () => jsonResponse({ repo: { repo_id: 'other' }, git: { repo_id: 'other', current_branch: 'main', status: { dirty: false, entries: [] } } }) });
  await expect(mismatchApi.readRepo('alpha')).rejects.toMatchObject({ code: 'repo_mismatch' });
});

test('fails closed for oversized and timed out responses', async () => {
  const oversizedApi = new ForgeApi({ fetchImpl: async () => jsonResponse({ repos: 'x'.repeat(256 * 1024 + 1) }) });
  await expect(oversizedApi.listRepos()).rejects.toMatchObject({ code: 'response_too_large' });

  const timeoutApi = new ForgeApi({ timeoutMs: 100, fetchImpl: async (_path, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => { const error = new Error('aborted'); error.name = 'AbortError'; reject(error); });
  }) });
  await expect(timeoutApi.listRepos()).rejects.toMatchObject({ code: 'request_timeout' });
});

test('shows an explicit choice when the registry has no prior selection', async ({ page }) => {
  await page.route('**/api/repos', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ repo_count: 0, repos: [] }) }));
  await page.goto(`${baseUrl}/static/frontpage-v3/index.html?workspace=agent`);
  const chip = page.locator('[data-forge-status-root]');
  await expect(chip).toHaveAttribute('data-forge-state', 'empty');
  await expect(chip.locator('[data-forge-message]')).toHaveText('No repositories are available for this account.');
});
