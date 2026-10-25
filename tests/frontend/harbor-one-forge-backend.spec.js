const { test, expect } = require('playwright/test');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

const repoRoot = path.resolve(__dirname, '..', '..');
const serverScript = path.join(__dirname, 'forge_backend_server.py');
const python = process.platform === 'win32'
  ? path.join(repoRoot, 'venv', 'Scripts', 'python.exe')
  : path.join(repoRoot, 'venv', 'bin', 'python');
const evidenceRoot = path.join(repoRoot, '.abc-test-tmp', 'closure-forge-backend-20261001');
test.setTimeout(180000);

async function startBackend(scenario) {
  fs.mkdirSync(evidenceRoot, { recursive: true });
  const root = fs.mkdtempSync(path.join(evidenceRoot, `${scenario}-`));
  const child = spawn(python, [serverScript, '--root', root, '--scenario', scenario, '--port', '0'], {
    cwd: repoRoot,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    env: { ...process.env, ODYSSEUS_DATA_DIR: path.join(root, 'data'), AUTH_ENABLED: 'true' },
  });
  let output = '';
  child.stderr.on('data', () => {});
  let port;
  try {
    port = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Forge backend did not announce a port')), 120000);
    child.stdout.on('data', (chunk) => {
      output = (output + chunk.toString()).slice(-2000);
      const match = output.match(/PORT=(\d+)/);
      if (match) {
        clearTimeout(timer);
        resolve(Number(match[1]));
      }
    });
    child.once('error', () => { clearTimeout(timer); reject(new Error('Forge backend process failed to start')); });
    child.once('exit', (code) => {
      if (code !== null) {
        clearTimeout(timer);
        reject(new Error(`Forge backend exited before readiness (${Number.isInteger(code) ? code : -1}`));
      }
    });
    });
    const base = `http://127.0.0.1:${port}`;
    const deadline = Date.now() + 10000;
    while (Date.now() < deadline) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 1000);
      try {
        const response = await fetch(`${base}/api/repos`, { signal: controller.signal });
        if ([401, 403, 200].includes(response.status)) return { child, root, base };
      } catch (_) {}
      finally { clearTimeout(timeout); }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error('Forge backend did not answer after announcing its port');
  } catch (error) {
    if (!(await stopChild(child))) throw new Error('Forge backend cleanup unconfirmed');
    throw error;
  }
}

async function stopChild(child) {
  if (!child || child.pid === undefined || child.exitCode !== null || child.signalCode !== null) return true;
  child.kill('SIGTERM');
  const exited = await new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), 3000);
    child.once('exit', () => { clearTimeout(timer); resolve(true); });
  });
  if (exited || child.exitCode !== null || child.signalCode !== null) return true;
  child.kill('SIGKILL');
  return await new Promise((resolve) => {
    const timer = setTimeout(() => resolve(child.exitCode !== null || child.signalCode !== null), 3000);
    child.once('exit', () => { clearTimeout(timer); resolve(true); });
  });
}

async function stopBackend(backend) {
  if (!backend) return;
  if (!(await stopChild(backend.child))) throw new Error('Forge backend cleanup did not confirm process exit');
}

async function openAs(page, backend, role) {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(`${backend.base}/__test__/session/${role}`);
  await page.waitForLoadState('domcontentloaded');
  await page.goto(`${backend.base}/?workspace=agent`);
  await page.waitForLoadState('domcontentloaded');
  const close = page.locator('[data-agent-operation-close]');
  if (await close.isVisible().catch(() => false)) await close.click();
}

test.describe.configure({ mode: 'serial' });

test('real API renders branch, dirty worktree, policy, and verified version', async ({ page }) => {
  const backend = await startBackend('multi');
  try {
    const mutationMethods = [];
    page.on('request', (request) => {
      if (request.url().includes('/api/') && request.method() !== 'GET') mutationMethods.push(request.method());
    });
    await openAs(page, backend, 'admin');
    const chip = page.locator('[data-forge-status-root]');
    await chip.scrollIntoViewIfNeeded();
    await chip.locator('[data-forge-status-toggle]').click();
    await chip.locator('[data-forge-repo-select]').selectOption('alpha');
    await expect(chip.locator('[data-forge-detail-branch]')).toHaveText('feature/ui');
    await expect(chip.locator('[data-forge-detail-worktree]')).toHaveText('1 changed path');
    await expect(chip.locator('[data-forge-detail-policy]')).toContainText('local');
    await expect(chip.locator('[data-forge-detail-version]')).toContainText('Alpha Workspace verified');
    expect(mutationMethods).toEqual([]);
    const box = await chip.locator('[data-forge-status-popover]').boundingBox();
    expect(box).toBeTruthy();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.y).toBeGreaterThanOrEqual(0);
    await page.screenshot({ path: path.join(evidenceRoot, 'real-forge-desktop.png'), fullPage: true });
  } finally {
    await stopBackend(backend);
  }
});

test('multi-repo flow requires selection and permits the first repo', async ({ page }) => {
  const backend = await startBackend('multi');
  try {
    await openAs(page, backend, 'admin');
    const chip = page.locator('[data-forge-status-root]');
    await chip.scrollIntoViewIfNeeded();
    await expect(chip).toHaveAttribute('data-forge-state', 'selection');
    await chip.locator('[data-forge-status-toggle]').click();
    const picker = chip.locator('[data-forge-repo-select]');
    await expect(picker).toHaveValue('');
    await picker.selectOption('alpha');
    await expect(chip.locator('[data-forge-detail-branch]')).toHaveText('feature/ui');
    await expect(chip.locator('[data-forge-repo-title]')).toHaveText('Alpha Workspace');
  } finally {
    await stopBackend(backend);
  }
});

test('admin gate returns 403 for an authenticated non-admin browser', async ({ page }) => {
  const backend = await startBackend('multi');
  try {
    await openAs(page, backend, 'viewer');
    const status = await page.evaluate(async () => (await fetch('/api/repos')).status);
    expect(status).toBe(403);
    await expect(page.locator('[data-forge-summary]')).toHaveText('Status unavailable');
  } finally {
    await stopBackend(backend);
  }
});

test('empty real registry is reported honestly', async ({ page }) => {
  const backend = await startBackend('empty');
  try {
    await openAs(page, backend, 'admin');
    const chip = page.locator('[data-forge-status-root]');
    await expect(chip).toHaveAttribute('data-forge-state', 'empty');
    await expect(chip.locator('[data-forge-message]')).toHaveText('No repositories are available for this account.');
    await page.screenshot({ path: path.join(evidenceRoot, 'real-forge-empty.png'), fullPage: true });
  } finally {
    await stopBackend(backend);
  }
});
