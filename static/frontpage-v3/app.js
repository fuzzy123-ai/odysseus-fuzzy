(() => {
  const stage = document.getElementById('stage');
  const canvas = document.getElementById('grid-brush');
  const ctx = canvas?.getContext('2d');
  const workspaceOrder = ['agent', 'knowledge', 'planning', 'inbox'];
  const workspaceButtons = Array.from(document.querySelectorAll('[data-workspace-target]'));
  const workspaceScreens = Array.from(document.querySelectorAll('[data-workspace-screen]'));
  const workspaceStrip = document.querySelector('[data-workspace-strip]');
  const toolwheel = document.getElementById('toolwheel');
  const wheelArrow = document.getElementById('wheel-arrow');
  const wheelCore = document.getElementById('wheel-core');
  const storageKey = 'harbor-one-frontpage-v3';
  let activeWorkspace = 'agent';
  let nodes = [];
  let raf = null;
  let wheelOpenPointer = null;
  let forgeController = null;

  function persist() {
    try {
      localStorage.setItem(storageKey, JSON.stringify({ activeWorkspace }));
    } catch {
      // File previews can block storage; the UI still works without it.
    }
  }

  function restore() {
    const requestedWorkspace = new URLSearchParams(window.location.search).get('workspace');
    if (workspaceOrder.includes(requestedWorkspace)) {
      activeWorkspace = requestedWorkspace;
      return;
    }
    try {
      const state = JSON.parse(localStorage.getItem(storageKey) || '{}');
      if (workspaceOrder.includes(state.activeWorkspace)) activeWorkspace = state.activeWorkspace;
    } catch {
      activeWorkspace = 'agent';
    }
  }

  function setWorkspace(id) {
    if (!workspaceOrder.includes(id)) return;
    activeWorkspace = id;
    const index = workspaceOrder.indexOf(id);
    workspaceStrip?.style.setProperty('--workspace-offset', '-' + (index * 25) + '%');
    stage.dataset.workspace = id;
    workspaceScreens.forEach(screen => {
      const active = screen.dataset.workspaceScreen === id;
      screen.classList.toggle('active', active);
      screen.setAttribute('aria-hidden', String(!active));
    });
    workspaceButtons.forEach(button => {
      const active = button.dataset.workspaceTarget === id;
      button.classList.toggle('active', active);
      button.setAttribute('aria-expanded', String(button.closest('.workspace-group')?.classList.contains('menu-open') || false));
      if (active) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    persist();
    window.dispatchEvent(new CustomEvent('harbor:workspace-changed', {
      detail: { workspace: id }
    }));
  }

  function closeMenus(except = null) {
    document.querySelectorAll('.workspace-group.menu-open, .tools-cluster.menu-open, .undo-cluster.menu-open, .notification-cluster.menu-open').forEach(group => {
      if (group === except) return;
      group.classList.remove('menu-open');
      group.querySelector('[aria-expanded]')?.setAttribute('aria-expanded', 'false');
    });
  }

  function forgeRoot() {
    return document.querySelector('[data-forge-status-root]');
  }

  function setForgeLoading(root, message) {
    root.dataset.forgeState = 'loading';
    root.querySelector('[data-forge-state]').textContent = 'Loading';
    root.querySelector('[data-forge-message]').textContent = message || 'Reading repository status…';
    root.querySelector('[data-forge-details]').hidden = true;
    root.querySelector('[data-forge-retry]').hidden = true;
  }

  function setForgeError(root, message, error) {
    root.dataset.forgeState = 'error';
    root.dataset.forgeError = error?.code || 'request_failed';
    root.querySelector('[data-forge-state]').textContent = 'Unavailable';
    root.querySelector('[data-forge-summary]').textContent = 'Status unavailable';
    root.querySelector('[data-forge-message]').textContent = message;
    root.querySelector('[data-forge-details]').hidden = true;
    root.querySelector('[data-forge-retry]').hidden = false;
  }

  function setForgeEmpty(root) {
    root.dataset.forgeState = 'empty';
    root.querySelector('[data-forge-state]').textContent = 'No repository';
    root.querySelector('[data-forge-branch]').textContent = 'Repository';
    root.querySelector('[data-forge-summary]').textContent = 'No repository linked';
    root.querySelector('[data-forge-repo-title]').textContent = 'No repository linked';
    root.querySelector('[data-forge-message]').textContent = 'No repositories are available for this account.';
    root.querySelector('[data-forge-details]').hidden = true;
    root.querySelector('[data-forge-retry]').hidden = true;
  }

  function setForgeSelectionRequired(root) {
    root.dataset.forgeState = 'selection';
    root.querySelector('[data-forge-state]').textContent = 'Choose repository';
    root.querySelector('[data-forge-branch]').textContent = 'Repository';
    root.querySelector('[data-forge-summary]').textContent = 'Choose a repository';
    root.querySelector('[data-forge-repo-title]').textContent = 'Choose a repository';
    root.querySelector('[data-forge-message]').textContent = 'Select a repository to read its status.';
    root.querySelector('[data-forge-details]').hidden = true;
    root.querySelector('[data-forge-retry]').hidden = true;
  }

  function formatForgeDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date);
  }

  function renderForgeDetail(root, result) {
    const repo = result.repo || result.selected;
    const git = result.git || {};
    const status = git.status || {};
    const versions = Array.isArray(result.versions) ? result.versions : [];
    const version = versions[0] || null;
    const policy = result.policy || {};
    const branch = git.current_branch || repo.current_branch || repo.default_branch || 'Unknown branch';
    const changedCount = Array.isArray(status.entries) ? status.entries.length : 0;
    const worktree = status.dirty ? `${changedCount} changed ${changedCount === 1 ? 'path' : 'paths'}` : 'Clean';
    const versionLabel = version ? (version.version_label || version.version_id || 'Verified version') : 'No verified version';
    const policyLabel = policy.forge_mode ? `${policy.forge_mode}${policy.sync_on_commit === true ? ' · sync on commit' : ''}` : 'Policy unavailable';
    root.dataset.forgeState = 'ready';
    root.setAttribute('aria-label', `${repo.title || repo.repo_id} repository status`);
    root.querySelector('[data-forge-branch]').textContent = branch;
    root.querySelector('[data-forge-summary]').textContent = worktree;
    root.querySelector('[data-forge-state]').textContent = status.dirty ? 'Changes' : 'Clean';
    root.querySelector('[data-forge-repo-title]').textContent = repo.title || repo.repo_id;
    root.querySelector('[data-forge-message]').textContent = version
      ? `Read-only status for ${repo.repo_id}.`
      : `Read-only status for ${repo.repo_id}; no verified version is recorded.`;
    root.querySelector('[data-forge-detail-branch]').textContent = branch;
    root.querySelector('[data-forge-detail-worktree]').textContent = worktree;
    root.querySelector('[data-forge-detail-version]').textContent = version
      ? `${versionLabel}${formatForgeDate(version.created_at) ? ` · ${formatForgeDate(version.created_at)}` : ''}`
      : 'No verified version';
    root.querySelector('[data-forge-detail-policy]').textContent = policyLabel;
    root.querySelector('[data-forge-details]').hidden = false;
    root.querySelector('[data-forge-retry]').hidden = true;
  }

  function populateForgePicker(root, repos, selectedId) {
    const picker = root.querySelector('[data-forge-repo-picker]');
    const select = root.querySelector('[data-forge-repo-select]');
    picker.hidden = repos.length < 2;
    const options = [];
    if (!selectedId && repos.length > 1) {
      const placeholder = document.createElement('option');
      placeholder.value = '';
      placeholder.textContent = 'Choose a repository';
      placeholder.disabled = true;
      placeholder.selected = true;
      options.push(placeholder);
    }
    select.replaceChildren(...options, ...repos.map(repo => {
      const option = document.createElement('option');
      option.value = repo.repo_id;
      option.textContent = repo.title || repo.repo_id;
      option.selected = repo.repo_id === selectedId;
      return option;
    }));
  }

  async function loadForgeStatus(root, selectedId) {
    if (!forgeController) return;
    const requestId = ++forgeController.requestId;
    setForgeLoading(root, 'Reading repository status…');
    try {
      const result = await forgeController.api.load(selectedId);
      if (requestId !== forgeController.requestId) return;
      populateForgePicker(root, result.summary.repos, result.selected && result.selected.repo_id);
      if (result.requiresSelection) {
        setForgeSelectionRequired(root);
        return;
      }
      if (!result.selected) {
        setForgeEmpty(root);
        return;
      }
      renderForgeDetail(root, result.detail);
      forgeController.selectedRepoId = result.selected.repo_id;
    } catch (error) {
      if (requestId !== forgeController.requestId) return;
      const message = error && error.code === 'unauthorized'
        ? 'Repository access is unavailable for this account.'
        : 'Repository status is unavailable right now.';
      setForgeError(root, message, error);
    }
  }

  function closeForgeStatus(restoreFocus = false) {
    const root = forgeRoot();
    if (!root) return;
    const toggle = root.querySelector('[data-forge-status-toggle]');
    const popover = root.querySelector('[data-forge-status-popover]');
    popover.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    root.classList.remove('is-open');
    if (restoreFocus) toggle.focus();
  }

  function bindForgeStatus() {
    const root = forgeRoot();
    if (!root || !window.HarborForgeApi) return;
    forgeController = { api: new window.HarborForgeApi.ForgeApi(), selectedRepoId: '', requestId: 0 };
    const toggle = root.querySelector('[data-forge-status-toggle]');
    const popover = root.querySelector('[data-forge-status-popover]');
    const select = root.querySelector('[data-forge-repo-select]');
    const retry = root.querySelector('[data-forge-retry]');
    toggle.addEventListener('click', event => {
      event.stopPropagation();
      const open = popover.hidden;
      closeMenus();
      popover.hidden = !open;
      toggle.setAttribute('aria-expanded', String(open));
      root.classList.toggle('is-open', open);
      if (open && select.options.length > 1) select.focus();
    });
    select.addEventListener('change', () => loadForgeStatus(root, select.value));
    retry.addEventListener('click', () => loadForgeStatus(root, forgeController.selectedRepoId));
    loadForgeStatus(root);
  }

  function toggleGroup(group) {
    if (!group) return;
    const open = !group.classList.contains('menu-open');
    closeMenus(group);
    group.classList.toggle('menu-open', open);
    group.querySelector('[aria-expanded]')?.setAttribute('aria-expanded', String(open));
  }

  function openToolwheel(event) {
    if (!toolwheel) return;
    stage.classList.add('toolwheel-active');
    toolwheel.classList.add('open', 'suppress-core-menu');
    toolwheel.setAttribute('aria-hidden', 'false');
    wheelOpenPointer = event && typeof event.clientX === 'number'
      ? { x: event.clientX, y: event.clientY }
      : null;
    updateWheelArrow(event);
  }

  function closeToolwheel() {
    if (!toolwheel) return;
    stage.classList.remove('toolwheel-active');
    toolwheel.classList.remove('open', 'core-new-open', 'suppress-core-menu');
    toolwheel.querySelectorAll('.wheel-node.focused').forEach(node => node.classList.remove('focused'));
    toolwheel.setAttribute('aria-hidden', 'true');
    wheelOpenPointer = null;
  }

  function updateWheelArrow(event) {
    if (!wheelArrow || !event || typeof event.clientX !== 'number') return;
    const panel = toolwheel?.querySelector('.toolwheel-panel');
    const rect = panel?.getBoundingClientRect();
    if (!rect) return;
    const angle = Math.atan2(event.clientY - (rect.top + rect.height / 2), event.clientX - (rect.left + rect.width / 2)) * 180 / Math.PI;
    wheelArrow.style.setProperty('--angle', angle + 'deg');
  }

  function handleToolAction(action) {
    const targetMap = {
      'Overview': 'planning',
      'Open Project': 'planning',
      'New Project': 'planning',
      'Changes': 'planning',
      'Definition Requirements': 'planning',
      'Gate Definitions': 'planning',
      'Search Knowledge': 'knowledge',
      'Graph View': 'knowledge',
      'Memory': 'knowledge',
      'Sources': 'knowledge',
      'Todos': 'planning',
      'Deep Research': 'agent',
      'New Chat': 'agent',
      'New Task': 'agent',
      'New Workspace': 'agent'
    };
    if (targetMap[action]) setWorkspace(targetMap[action]);
    closeToolwheel();
  }

  function resizeCanvas() {
    if (!canvas || !ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = window.innerWidth;
    const height = window.innerHeight;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    canvas.style.width = width + 'px';
    canvas.style.height = height + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    nodes = Array.from({ length: Math.max(130, Math.floor((width * height) / 11000)) }, (_, index) => ({
      x: (Math.sin(index * 19.91) * 0.5 + 0.5) * width,
      y: (Math.cos(index * 11.73) * 0.5 + 0.5) * height,
      vx: ((index % 7) - 3) * 0.018,
      vy: (((index + 3) % 7) - 3) * 0.014,
      r: index % 9 === 0 ? 2.8 : index % 4 === 0 ? 1.8 : 1.1
    }));
  }

  function drawNetwork() {
    if (!canvas || !ctx) return;
    const width = window.innerWidth;
    const height = window.innerHeight;
    ctx.clearRect(0, 0, width, height);
    nodes.forEach(node => {
      node.x += node.vx;
      node.y += node.vy;
      if (node.x < -60) node.x = width + 60;
      if (node.x > width + 60) node.x = -60;
      if (node.y < -60) node.y = height + 60;
      if (node.y > height + 60) node.y = -60;
    });
    for (let i = 0; i < nodes.length; i += 1) {
      for (let j = i + 1; j < Math.min(nodes.length, i + 14); j += 1) {
        const a = nodes[i];
        const b = nodes[j];
        const distance = Math.hypot(a.x - b.x, a.y - b.y);
        if (distance > 170) continue;
        ctx.strokeStyle = `rgba(16, 182, 202, ${Math.max(0, 1 - distance / 170) * 0.15})`;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }
    nodes.forEach(node => {
      const glow = ctx.createRadialGradient(node.x, node.y, 0, node.x, node.y, node.r * 8);
      glow.addColorStop(0, 'rgba(16, 182, 202, 0.3)');
      glow.addColorStop(1, 'rgba(16, 182, 202, 0)');
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(node.x, node.y, node.r * 8, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = 'rgba(16, 182, 202, 0.42)';
      ctx.beginPath();
      ctx.arc(node.x, node.y, node.r, 0, Math.PI * 2);
      ctx.fill();
    });
    raf = requestAnimationFrame(drawNetwork);
  }

  function prepareStaticPanels() {
    document.querySelectorAll('[data-window]').forEach(windowEl => {
      windowEl.removeAttribute('data-window');
      windowEl.removeAttribute('data-drag-prepared');
      windowEl.classList.add('v3-fixed-panel');
    });
    document.querySelectorAll('[data-drag-handle]').forEach(handle => handle.removeAttribute('data-drag-handle'));
    document.querySelectorAll('.window-actions').forEach(actions => actions.setAttribute('aria-hidden', 'true'));
  }

  function preparePlaceholders() {
    const knowledge = document.querySelector('[data-knowledge-graph-default]');
    if (knowledge && !knowledge.children.length) {
      knowledge.innerHTML = '<div class="v3-screen-placeholder"><strong>Knowledge Graph</strong><span>Graph view will become full-screen once memory is connected.</span></div>';
    }
    const planning = document.querySelector('[data-projects-overview-default]');
    if (planning && !planning.matches('[data-planning-root]') && !planning.children.length) {
      planning.innerHTML = '<div class="v3-screen-placeholder"><strong>Planning Overview</strong><span>Graph-first fixed planning surface. We will decide the exact graph layout next.</span></div>';
    }
  }

  function bindEvents() {
    workspaceButtons.forEach(button => {
      button.addEventListener('click', event => {
        const group = button.closest('.workspace-group');
        if (button.dataset.workspaceTarget === activeWorkspace) {
          event.stopPropagation();
          toggleGroup(group);
          return;
        }
        closeMenus();
        setWorkspace(button.dataset.workspaceTarget);
      });
    });

    document.querySelector('[data-brand-home]')?.addEventListener('click', () => setWorkspace('agent'));
    window.addEventListener('harbor:planning-agent-handoff', event => {
      const detail = event.detail || {};
      const composerText = String(detail.composerText || '');
      if (!composerText.startsWith('/abc run roadmap:')) return;
      const composer = document.querySelector('.prompt-input');
      if (!composer) return;
      setWorkspace('agent');
      composer.value = composerText;
      composer.dispatchEvent(new Event('input', { bubbles: true }));
      composer.focus();
      const context = document.querySelector('[data-context-nodges]');
      if (context) {
        context.hidden = false;
        context.textContent = `Planning definition · ${detail.roadmapId || 'roadmap'} r${detail.revision || ''}`;
      }
      window.dispatchEvent(new CustomEvent('harbor:agent-composer-drafted', {
        detail: {
          source: 'planning-definition',
          roadmapId: detail.roadmapId,
          revision: detail.revision,
          contentHash: detail.contentHash
        }
      }));
    });
    document.querySelector('.tools-button')?.addEventListener('click', event => {
      event.stopPropagation();
      toggleGroup(event.currentTarget.closest('.tools-cluster'));
    });
    document.querySelector('[data-global-undo]')?.addEventListener('click', event => {
      event.stopPropagation();
      toggleGroup(event.currentTarget.closest('.undo-cluster'));
    });
    document.querySelector('[data-notifications-toggle]')?.addEventListener('click', event => {
      event.stopPropagation();
      toggleGroup(event.currentTarget.closest('.notification-cluster'));
    });
    document.querySelectorAll('[data-notification-target]').forEach(item => {
      item.addEventListener('click', () => {
        const target = item.dataset.notificationTarget === 'memory' ? 'knowledge'
          : item.dataset.notificationTarget === 'projects' ? 'planning'
          : item.dataset.notificationTarget || 'agent';
        setWorkspace(target);
        closeMenus();
      });
    });
    document.getElementById('privacy-toggle')?.addEventListener('click', event => {
      const pressed = event.currentTarget.getAttribute('aria-pressed') !== 'true';
      event.currentTarget.setAttribute('aria-pressed', String(pressed));
      event.currentTarget.setAttribute('aria-label', pressed ? 'Secure mode locked' : 'Secure mode unlocked');
      event.currentTarget.setAttribute('title', pressed ? 'Secure mode: locked' : 'Secure mode: unlocked');
      const label = event.currentTarget.querySelector('.secure-label');
      if (label) label.textContent = pressed ? 'Locked' : 'Unlocked';
      event.currentTarget.closest('.secure-cluster')?.classList.toggle('secure-on', pressed);
      stage.classList.toggle('privacy-on', pressed);
    });
    document.querySelector('.composer-menu-button')?.addEventListener('click', event => {
      const shell = event.currentTarget.closest('.composer-shell');
      const open = !shell.classList.contains('menu-open');
      shell.classList.toggle('menu-open', open);
      event.currentTarget.setAttribute('aria-expanded', String(open));
    });
    document.querySelectorAll('[data-work-run-toggle]').forEach(button => {
      button.addEventListener('click', () => {
        const run = button.closest('[data-work-run]');
        const collapsed = !run.classList.contains('collapsed');
        run.classList.toggle('collapsed', collapsed);
        button.setAttribute('aria-expanded', String(!collapsed));
        const label = button.querySelector('.work-run-copy span');
        if (label) label.textContent = collapsed ? 'Show work log' : 'Hide work log';
      });
    });
    document.querySelector('.chat-title')?.addEventListener('dblclick', event => {
      const title = event.currentTarget;
      const input = document.querySelector('.chat-title-editor');
      if (!input) return;
      title.hidden = true;
      input.hidden = false;
      input.focus();
      input.select();
    });
    document.querySelector('.chat-title-editor')?.addEventListener('keydown', event => {
      if (event.key !== 'Enter') return;
      const title = document.querySelector('.chat-title');
      title.textContent = event.currentTarget.value.trim() || 'Untitled chat';
      event.currentTarget.hidden = true;
      title.hidden = false;
    });
    stage.addEventListener('contextmenu', event => {
      event.preventDefault();
      toolwheel?.classList.contains('open') ? closeToolwheel() : openToolwheel(event);
    });
    toolwheel?.addEventListener('mousemove', event => {
      if (!toolwheel.classList.contains('open')) return;
      updateWheelArrow(event);
      if (!wheelOpenPointer) return;
      if (Math.hypot(event.clientX - wheelOpenPointer.x, event.clientY - wheelOpenPointer.y) > 14) {
        toolwheel.classList.remove('suppress-core-menu');
      }
    });
    wheelCore?.addEventListener('mouseenter', () => {
      if (!toolwheel.classList.contains('suppress-core-menu')) toolwheel.classList.add('core-new-open');
    });
    wheelCore?.addEventListener('mouseleave', () => {
      setTimeout(() => {
        if (!toolwheel.querySelector('.core-new-tree:hover')) toolwheel.classList.remove('core-new-open');
      }, 90);
    });
    toolwheel?.querySelectorAll('.wheel-node').forEach((node, index) => {
      node.addEventListener('mouseenter', () => {
        toolwheel.classList.remove('suppress-core-menu', 'core-new-open');
        toolwheel.querySelectorAll('.wheel-node.focused').forEach(item => item.classList.remove('focused'));
        node.classList.add('focused');
      });
      node.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          handleToolAction(node.dataset.node || workspaceOrder[index] || 'agent');
        }
      });
    });
    toolwheel?.querySelectorAll('[data-action]').forEach(item => {
      item.addEventListener('click', event => {
        event.stopPropagation();
        handleToolAction(item.dataset.action || item.textContent.trim());
      });
    });
    document.addEventListener('click', event => {
      if (!event.target.closest('.workspace-group,.tools-cluster,.undo-cluster,.notification-cluster,.composer-shell,.forge-status-chip')) {
        closeMenus();
        closeForgeStatus();
        document.querySelector('.composer-shell.menu-open')?.classList.remove('menu-open');
      }
    });
    document.addEventListener('keydown', event => {
      if (event.altKey && event.code === 'Space') {
        event.preventDefault();
        toolwheel?.classList.contains('open') ? closeToolwheel() : openToolwheel();
      }
      if (event.key === 'Escape') {
        closeToolwheel();
        closeMenus();
        if (forgeRoot()?.classList.contains('is-open')) closeForgeStatus(true);
      }
      if (event.ctrlKey && /^[1-4]$/.test(event.key)) {
        event.preventDefault();
        setWorkspace(workspaceOrder[Number(event.key) - 1]);
      }
    });
    window.addEventListener('resize', resizeCanvas);
  }

  restore();
  prepareStaticPanels();
  preparePlaceholders();
  setWorkspace(activeWorkspace);
  resizeCanvas();
  drawNetwork();
  bindEvents();
  bindForgeStatus();
})();
