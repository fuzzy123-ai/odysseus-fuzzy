(function () {
  const stage = document.getElementById('stage');
  const brushCanvas = document.getElementById('grid-brush');
  const brushCtx = brushCanvas.getContext('2d');
  const toolwheel = document.getElementById('toolwheel');
  const workspace = stage.querySelector('.workspace');
  const workspaceStrip = stage.querySelector('[data-workspace-strip]');
  const workspaceTabs = document.getElementById('workspace-tabs');
  const workspaceButtons = Array.from(document.querySelectorAll('[data-workspace-target]'));
  const workspaceScreens = Array.from(document.querySelectorAll('[data-workspace-screen]'));
  const brandHomeButton = stage.querySelector('[data-brand-home]');
  const topline = stage.querySelector('.topline');
  const aurora = stage.querySelector('.aurora');
  const wheelArrow = document.getElementById('wheel-arrow');
  const wheelCore = document.getElementById('wheel-core');
  const coreNewTree = toolwheel.querySelector('.core-new-tree');
  const readout = document.getElementById('wheel-readout');
  const buildState = document.getElementById('build-state');
  const coreWindow = document.querySelector('[data-core]');
  const chatTitle = coreWindow.querySelector('.chat-title');
  const chatTitleEditor = coreWindow.querySelector('.chat-title-editor');
  const headerModelChip = coreWindow.querySelector('[data-header-model-chip]');
  const currentModelLabel = coreWindow.querySelector('[data-current-model-label]');
  const messages = coreWindow.querySelector('.messages');
  const promptInput = coreWindow.querySelector('.prompt-input');
  const composerModelChooser = coreWindow.querySelector('[data-composer-model-chooser]');
  const composerModelButton = coreWindow.querySelector('[data-model-chooser-button]');
  const composerModelMenu = coreWindow.querySelector('[data-model-chooser-menu]');
  const composerModelLabel = coreWindow.querySelector('[data-composer-model-label]');
  const chatHistoryButton = coreWindow.querySelector('[data-chat-history-toggle]');
  const chatHistoryPanel = coreWindow.querySelector('[data-chat-history-panel]');
  const chatHistoryList = coreWindow.querySelector('[data-chat-history-list]');
  const chatHistoryCount = coreWindow.querySelector('[data-chat-history-count]');
  const chatHistorySearch = coreWindow.querySelector('.chat-history-search');
  const contextNodges = coreWindow.querySelector('[data-context-nodges]');
  const leftNodge = document.getElementById('chat-nodge-left');
  const rightNodge = document.getElementById('chat-nodge-right');
  const chatCarousel = document.getElementById('chat-carousel');
  const privacyToggle = document.getElementById('privacy-toggle');
  const notificationRoot = document.querySelector('[data-notifications-root]');
  const notificationButton = document.querySelector('[data-notifications-toggle]');
  const notificationCount = notificationButton?.querySelector('.notification-count');
  const notificationBubble = notificationButton?.querySelector('[data-notification-bubble]');
  const notificationMenu = document.getElementById('notification-menu');
  const notificationMenuCount = notificationMenu?.querySelector('.notification-menu-head span');
  const universalInbox = document.getElementById('universal-inbox');
  const universalInboxTrigger = document.getElementById('universal-inbox-trigger');
  const urlParams = new URLSearchParams(window.location.search);
  const backgroundMode = urlParams.get('bg') === 'grid' ? 'grid' : 'network';
  const wheelOverlayMode = urlParams.get('wheel') === 'dim' ? 'dim' : 'depth';
  const uiStateStorageKey = 'harbor-one:v2-ui-state';

  let brushDpr = 1;
  let gridSignals = [];
  let networkNodes = [];
  let networkEdges = [];
  let networkSignals = [];
  let brushFrame = 0;
  let lastWheelPointer = null;
  let wheelOpenPointer = null;
  let focusedNode = null;
  let coreMenuTimer = null;
  let preparedChats = 0;
  let zTop = 20;
  let activeChatIndex = 0;
  let activeWorkspaceId = 'agent';
  let activePlanningProjectIndex = 0;
  let lastSelectedModel = 'deepseek-v4-flash';
  let inboxHover = false;
  let inboxTriggerHover = false;
  let inboxDragDepth = 0;
  let inboxCloseTimer = null;
  let notificationBubbleTimer = null;
  let uiStateSaveTimer = null;
  let uiStateRestoring = false;
  let workspaceScrollGuardFrame = null;
  let persistedUiState = readPersistedUiState();
  const v2Data = window.HarborV2Data;
  if (!v2Data) {
    throw new Error('HarborV2Data must load before app.js');
  }
  const {
    modelProfiles,
    workspaceOrder,
    workspaceLabels,
    documentSamples,
    codeLanguageSamples,
    historicalChats,
    projectSamples,
    planningRoadmapDemo,
    planningMcpRoadmap,
    globalTodoExtras,
    skillSamples,
    knowledgeGraphPalette,
    knowledgeGraphTypes,
    memoryTypeColors,
    memorySamples,
    settingsCatalog
  } = v2Data;

  activeWorkspaceId = workspaceOrder.includes(persistedUiState.activeWorkspaceId)
    ? persistedUiState.activeWorkspaceId
    : 'agent';
  activePlanningProjectIndex = Number.isFinite(persistedUiState.activePlanningProjectIndex)
    ? Math.max(0, Math.min(projectSamples.length - 1, persistedUiState.activePlanningProjectIndex))
    : 0;
  const restoredWorkspaceId = activeWorkspaceId;





  const chatSpaces = [{
    id: 'chat-1',
    title: chatTitle.textContent.trim(),
    messages: messages.innerHTML,
    prompt: promptInput.value,
    model: lastSelectedModel,
    modelState: 'ready',
    isNew: false,
    lastAnswer: 'now',
    unread: false,
    needsQuestion: false,
    contexts: []
  }];




  const todosViewState = {
    filter: 'All',
    sort: 'Project',
    selectedId: null,
    query: ''
  };

  const skillsViewState = {
    filter: 'all',
    selectedId: 'project-archivist',
    query: ''
  };

  const knowledgeGraphSources = Object.keys(knowledgeGraphPalette);

  const knowledgeGraphState = {
    source: 'All',
    type: 'All',
    query: '',
    density: 72,
    trust: 38,
    cluster: true,
    labels: 'Focus',
    layout: 'Semantic',
    selectedId: 'kg-14'
  };
  let knowledgeGraphNodes = [];
  let knowledgeGraphEdges = [];


  const memoryState = {
    tab: 'All',
    type: 'All',
    source: 'All',
    query: '',
    selectedId: null,
    sortAttentionFirst: true,
    filtersOpen: true,
    detailOpen: false
  };
  const settingsViewState = {
    query: '',
    advanced: false
  };


  function resizeBrushCanvas() {
    brushDpr = Math.min(window.devicePixelRatio || 1, 2);
    brushCanvas.width = Math.floor(window.innerWidth * brushDpr);
    brushCanvas.height = Math.floor(window.innerHeight * brushDpr);
    brushCanvas.style.width = window.innerWidth + 'px';
    brushCanvas.style.height = window.innerHeight + 'px';
    brushCtx.setTransform(brushDpr, 0, 0, brushDpr, 0, 0);
    if (backgroundMode === 'network') {
      setupNetwork();
    }
  }

  function drawExactGrid() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    const grid = 44;
    const fade = brushCtx.createRadialGradient(w * 0.5, h * 0.52, 0, w * 0.5, h * 0.52, Math.max(w, h) * 0.58);
    fade.addColorStop(0, 'rgba(255,255,255,1)');
    fade.addColorStop(0.68, 'rgba(255,255,255,0.72)');
    fade.addColorStop(1, 'rgba(255,255,255,0)');

    brushCtx.save();
    brushCtx.globalCompositeOperation = 'source-over';
    brushCtx.strokeStyle = 'rgba(69, 215, 255, 0.048)';
    brushCtx.lineWidth = 1;

    for (let x = 0.5; x <= w; x += grid) {
      brushCtx.beginPath();
      brushCtx.moveTo(x, 0);
      brushCtx.lineTo(x, h);
      brushCtx.stroke();
    }

    for (let y = 0.5; y <= h; y += grid) {
      brushCtx.beginPath();
      brushCtx.moveTo(0, y);
      brushCtx.lineTo(w, y);
      brushCtx.stroke();
    }

    brushCtx.globalCompositeOperation = 'destination-in';
    brushCtx.fillStyle = fade;
    brushCtx.fillRect(0, 0, w, h);
    brushCtx.restore();
  }

  function addGridSignal() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    const grid = 44;
    const horizontal = Math.random() > 0.38;
    const line = Math.round((horizontal ? h : w) * (0.18 + Math.random() * 0.66) / grid) * grid;
    const dir = Math.random() > 0.5 ? 1 : -1;
    const span = horizontal ? w : h;
    const start = dir > 0 ? -grid : span + grid;
    const speed = 36 + Math.random() * 48;
    const life = (span + grid * 2) / speed * 1000;

    gridSignals.push({
      horizontal,
      line,
      pos: start,
      dir,
      speed,
      age: 0,
      life,
      strength: 0.28 + Math.random() * 0.18,
      tail: 38 + Math.random() * 30
    });
  }

  function setupNetwork() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    const detailCount = Math.max(12, Math.min(22, Math.round((w * h) / 78000)));
    const spreadX = w * 1.48;
    const spreadY = h * 1.44;
    const offsetX = (spreadX - w) / 2;
    const offsetY = (spreadY - h) / 2;
    const columns = Math.max(7, Math.ceil(spreadX / 210));
    const rows = Math.max(5, Math.ceil(spreadY / 185));
    const anchors = [
      [-0.16, 0.18],
      [0.16, -0.14],
      [0.62, -0.18],
      [1.13, 0.14],
      [1.18, 0.58],
      [1.04, 1.12],
      [0.45, 1.18],
      [-0.14, 0.84],
      [-0.2, 0.48]
    ];
    networkSignals = [];
    networkNodes = anchors.map(([x, y], index) => ({
      id: index,
      x: w * x,
      y: h * y,
      r: 1.8 + Math.random() * 2.2,
      phase: Math.random() * Math.PI * 2,
      drift: 0.18 + Math.random() * 0.34
    }));

    const coverageNodes = [];
    for (let row = 0; row < rows; row++) {
      for (let column = 0; column < columns; column++) {
        const cellX = columns <= 1 ? 0.5 : column / (columns - 1);
        const cellY = rows <= 1 ? 0.5 : row / (rows - 1);
        const jitterX = (Math.random() - 0.5) * (spreadX / columns) * 0.68;
        const jitterY = (Math.random() - 0.5) * (spreadY / rows) * 0.68;
        coverageNodes.push({
          id: anchors.length + coverageNodes.length,
          x: cellX * spreadX - offsetX + jitterX,
          y: cellY * spreadY - offsetY + jitterY,
          r: 1.15 + Math.random() * 2.1,
          phase: Math.random() * Math.PI * 2,
          drift: 0.18 + Math.random() * 0.34
        });
      }
    }

    const detailNodes = Array.from({ length: detailCount }, (_, index) => ({
      id: anchors.length + coverageNodes.length + index,
      x: Math.random() * spreadX - offsetX,
      y: Math.random() * spreadY - offsetY,
      r: 1.4 + Math.random() * 2.4,
      phase: Math.random() * Math.PI * 2,
      drift: 0.18 + Math.random() * 0.34
    }));
    networkNodes.push(...coverageNodes, ...detailNodes);

    const edgeMap = new Set();
    networkEdges = [];
    networkNodes.forEach(node => {
      const nearest = networkNodes
        .filter(other => other !== node)
        .map(other => {
          const dx = other.x - node.x;
          const dy = other.y - node.y;
          return { other, distance: Math.hypot(dx, dy) };
        })
        .sort((a, b) => a.distance - b.distance)
        .slice(0, 8);

      nearest.forEach(({ other, distance }) => {
        if (distance > Math.min(w, h) * 0.95) return;
        const key = [node.id, other.id].sort((a, b) => a - b).join(':');
        if (edgeMap.has(key)) return;
        edgeMap.add(key);
        networkEdges.push({ a: node, b: other, distance });
      });
    });
  }

  function addNetworkSignal() {
    if (!networkEdges.length) return;
    const edge = networkEdges[Math.floor(Math.random() * networkEdges.length)];
    networkSignals.push({
      edge,
      age: 0,
      life: 1500 + Math.random() * 1200,
      dir: Math.random() > 0.5 ? 1 : -1,
      strength: 0.28 + Math.random() * 0.22
    });
  }

  function drawNetwork(now) {
    const w = window.innerWidth;
    const h = window.innerHeight;
    const fade = brushCtx.createRadialGradient(w * 0.5, h * 0.52, 0, w * 0.5, h * 0.52, Math.max(w, h) * 0.88);
    fade.addColorStop(0, 'rgba(255,255,255,1)');
    fade.addColorStop(0.78, 'rgba(255,255,255,0.82)');
    fade.addColorStop(1, 'rgba(255,255,255,0)');

    brushCtx.save();
    brushCtx.globalCompositeOperation = 'source-over';
    brushCtx.lineWidth = 1;

    networkEdges.forEach(edge => {
      const alpha = Math.max(0.01, 0.038 - edge.distance / Math.max(w, h) * 0.026);
      brushCtx.strokeStyle = `rgba(69, 215, 255, ${alpha})`;
      brushCtx.beginPath();
      brushCtx.moveTo(edge.a.x, edge.a.y);
      brushCtx.lineTo(edge.b.x, edge.b.y);
      brushCtx.stroke();
    });

    networkNodes.forEach(node => {
      const pulse = 0.55 + Math.sin((now || 0) * 0.0012 + node.phase) * 0.25;
      brushCtx.fillStyle = `rgba(130, 232, 255, ${0.18 + pulse * 0.16})`;
      brushCtx.beginPath();
      brushCtx.arc(node.x, node.y, node.r, 0, Math.PI * 2);
      brushCtx.fill();

      brushCtx.fillStyle = `rgba(22, 217, 245, ${0.035 + pulse * 0.035})`;
      brushCtx.beginPath();
      brushCtx.arc(node.x, node.y, node.r * 3.4, 0, Math.PI * 2);
      brushCtx.fill();
    });

    if (brushFrame % 14 === 0 || networkSignals.length < 9) addNetworkSignal();

    brushCtx.globalCompositeOperation = 'lighter';
    networkSignals = networkSignals.filter(signal => {
      signal.age += 16;
      const p = signal.age / signal.life;
      if (p >= 1) return false;
      const t = signal.dir > 0 ? p : 1 - p;
      const x = signal.edge.a.x + (signal.edge.b.x - signal.edge.a.x) * t;
      const y = signal.edge.a.y + (signal.edge.b.y - signal.edge.a.y) * t;
      const alpha = signal.strength * Math.min(1, p * 5, (1 - p) * 5);

      brushCtx.strokeStyle = `rgba(73, 201, 235, ${alpha * 0.18})`;
      brushCtx.lineWidth = 1.2;
      brushCtx.beginPath();
      brushCtx.moveTo(signal.edge.a.x, signal.edge.a.y);
      brushCtx.lineTo(signal.edge.b.x, signal.edge.b.y);
      brushCtx.stroke();

      brushCtx.fillStyle = `rgba(164, 241, 255, ${alpha})`;
      brushCtx.beginPath();
      brushCtx.arc(x, y, 1.55, 0, Math.PI * 2);
      brushCtx.fill();

      brushCtx.fillStyle = `rgba(35, 214, 246, ${alpha * 0.08})`;
      brushCtx.beginPath();
      brushCtx.arc(x, y, 5.8, 0, Math.PI * 2);
      brushCtx.fill();

      return true;
    });

    brushCtx.globalCompositeOperation = 'destination-in';
    brushCtx.fillStyle = fade;
    brushCtx.fillRect(0, 0, w, h);
    brushCtx.restore();
  }

  function animateGridBrush() {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      if (backgroundMode === 'network') {
        drawNetwork(performance.now());
      } else {
        drawExactGrid();
      }
      return;
    }

    if (!brushCanvas.width) resizeBrushCanvas();
    brushCtx.clearRect(0, 0, window.innerWidth, window.innerHeight);
    if (backgroundMode === 'network') {
      drawNetwork(performance.now());
      brushFrame++;
      requestAnimationFrame(animateGridBrush);
      return;
    }

    drawExactGrid();
    if (brushFrame % 28 === 0 || gridSignals.length < 4) addGridSignal();
    brushFrame++;

    brushCtx.save();
    brushCtx.globalCompositeOperation = 'lighter';
    gridSignals = gridSignals.filter(signal => {
      signal.age += 16;
      signal.pos += signal.dir * signal.speed * 0.016;
      const p = signal.age / signal.life;
      if (p >= 1) return false;
      const edgeFade = Math.min(1, p * 5, (1 - p) * 5);
      const alpha = signal.strength * edgeFade;
      const x = signal.horizontal ? signal.pos : signal.line;
      const y = signal.horizontal ? signal.line : signal.pos;
      const tx = signal.horizontal ? signal.tail * -signal.dir : 0;
      const ty = signal.horizontal ? 0 : signal.tail * -signal.dir;

      brushCtx.strokeStyle = `rgba(73, 201, 235, ${alpha * 0.34})`;
      brushCtx.lineWidth = 1;
      brushCtx.beginPath();
      brushCtx.moveTo(x, y);
      brushCtx.lineTo(x + tx, y + ty);
      brushCtx.stroke();

      brushCtx.fillStyle = `rgba(164, 241, 255, ${alpha})`;
      brushCtx.beginPath();
      brushCtx.arc(x, y, 1.35, 0, Math.PI * 2);
      brushCtx.fill();

      brushCtx.fillStyle = `rgba(35, 214, 246, ${alpha * 0.1})`;
      brushCtx.beginPath();
      brushCtx.arc(x, y, 3.2, 0, Math.PI * 2);
      brushCtx.fill();

      return true;
    });
    brushCtx.restore();
    requestAnimationFrame(animateGridBrush);
  }

  function updateWheelArrow(sourceEvent) {
    if (sourceEvent && typeof sourceEvent.clientX === 'number') {
      lastWheelPointer = { x: sourceEvent.clientX, y: sourceEvent.clientY };
    }
    const panel = toolwheel.querySelector('.toolwheel-panel');
    const rect = panel.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const target = lastWheelPointer || { x: cx, y: cy - 180 };
    const angle = Math.atan2(target.y - cy, target.x - cx) * 180 / Math.PI;
    wheelArrow.style.setProperty('--angle', angle + 'deg');
  }

  function focusWheelNode(index) {
    const nodes = Array.from(toolwheel.querySelectorAll('.wheel-node'));
    nodes.forEach(node => node.classList.remove('focused'));
    const node = nodes[index];
    if (!node) return;
    focusedNode = node;
    node.classList.add('focused');
    readout.textContent = node.dataset.node + ' selected. Press Enter to confirm, or use numbers to jump.';
  }

  function clearWheelFocus() {
    toolwheel.querySelectorAll('.wheel-node').forEach(node => node.classList.remove('focused'));
    focusedNode = null;
    readout.textContent = 'Choose a section. Use hover, numbers, or arrow keys.';
  }

  function openToolwheel(sourceEvent) {
    stage.classList.add('toolwheel-active');
    setToolwheelDepthActive(true);
    toolwheel.classList.add('open');
    toolwheel.classList.add('suppress-core-menu');
    toolwheel.setAttribute('aria-hidden', 'false');
    wheelOpenPointer = sourceEvent && typeof sourceEvent.clientX === 'number'
      ? { x: sourceEvent.clientX, y: sourceEvent.clientY }
      : null;
    clearWheelFocus();
    requestAnimationFrame(() => updateWheelArrow(sourceEvent));
  }

  function closeToolwheel() {
    stage.classList.remove('toolwheel-active');
    setToolwheelDepthActive(false);
    toolwheel.classList.remove('open');
    toolwheel.classList.remove('core-new-open');
    toolwheel.classList.remove('suppress-core-menu');
    coreNewTree.classList.remove('is-open');
    wheelOpenPointer = null;
    clearWheelFocus();
    toolwheel.setAttribute('aria-hidden', 'true');
  }

  function setToolwheelDepthActive(active) {
    if (wheelOverlayMode !== 'depth') return;
    const layers = [
      {
        element: workspace,
        active: {
          filter: 'blur(1.4px) saturate(0.58) contrast(0.82) brightness(0.58)',
          opacity: '0.68',
          transform: 'perspective(1200px) translateY(10px) scale(0.965) rotateX(1.2deg)'
        },
        inactive: {
          filter: 'none',
          opacity: '',
          transform: 'none'
        }
      },
      {
        element: topline,
        active: {
          filter: 'blur(1.2px) saturate(0.62)',
          opacity: '0.34',
          transform: 'translateY(-3px) scale(0.985)'
        },
        inactive: {
          filter: 'none',
          opacity: '',
          transform: 'none'
        }
      },
      {
        element: brushCanvas,
        active: {
          filter: 'blur(2px) saturate(0.88) brightness(0.74)',
          opacity: '0.46'
        },
        inactive: {
          filter: 'none',
          opacity: ''
        }
      },
      {
        element: aurora,
        active: {
          filter: 'blur(4px) saturate(0.92)',
          opacity: '0.48'
        },
        inactive: {
          filter: 'none',
          opacity: ''
        }
      }
    ];

    layers.forEach(layer => {
      if (!layer.element) return;
      const styles = active ? layer.active : layer.inactive;
      layer.element.style.transition = active ? '' : 'none';
      Object.keys(styles).forEach(property => {
        layer.element.style[property] = styles[property];
      });
      if (!active) {
        void layer.element.offsetHeight;
        requestAnimationFrame(() => {
          layer.element.style.transition = '';
        });
      }
    });
  }

  function announceAction(action) {
    readout.textContent = action + ' prepared.';
    buildState.textContent = 'V2 - ' + action;
  }

  function setNotificationCount(count) {
    if (!notificationButton || !notificationCount) return;
    const nextCount = Math.max(0, Number(count) || 0);
    notificationCount.textContent = String(nextCount);
    notificationButton.classList.toggle('has-notifications', nextCount > 0);
    notificationButton.setAttribute('aria-label', nextCount + ' notification' + (nextCount === 1 ? '' : 's'));
    if (notificationMenuCount) notificationMenuCount.textContent = nextCount + ' new';
  }

  function currentNotificationCount() {
    return Number(notificationCount?.textContent || 0) || 0;
  }

  function unreadNotificationCount() {
    return notificationMenu ? notificationMenu.querySelectorAll('.notification-item.unread').length : currentNotificationCount();
  }

  function setNotificationMenuOpen(open) {
    if (!notificationRoot || !notificationButton || !notificationMenu) return;
    notificationRoot.classList.toggle('menu-open', open);
    notificationMenu.classList.toggle('open', open);
    notificationButton.setAttribute('aria-expanded', String(open));
    notificationMenu.setAttribute('aria-hidden', String(!open));
    notificationMenu.style.opacity = open ? '1' : '';
    notificationMenu.style.pointerEvents = open ? 'auto' : '';
    notificationMenu.style.transform = open ? 'translateY(0) scale(1)' : '';
    if (open) {
      notificationButton.classList.remove('bubble-visible', 'notification-ringing');
      notificationBubble?.setAttribute('aria-hidden', 'true');
    }
  }

  function routeNotificationTarget(target) {
    if (target === 'memory') {
      openMemoryWindow();
      return;
    }
    if (target === 'projects') {
      openProjectsOverview();
      return;
    }
    if (target === 'inbox') {
      setActiveWorkspace('inbox');
      const inboxHome = document.querySelector('[data-workspace-screen="inbox"] .inbox-home-window');
      if (inboxHome) {
        setWindowMinimized(inboxHome, false);
        activateWindow(inboxHome);
      }
      buildState.textContent = 'V2 - Inbox notifications';
      return;
    }
    if (target === 'settings') {
      openSettingsWindow({ query: 'notifications reminders', advanced: false });
      return;
    }
    setActiveWorkspace('agent');
    setWindowMinimized(coreWindow, false);
    activateWindow(coreWindow);
    buildState.textContent = 'V2 - Notification opened';
  }

  function addNotificationMenuItem({ message, target = 'agent', meta = 'now' }) {
    if (!notificationMenu) return;
    const head = notificationMenu.querySelector('.notification-menu-head');
    const item = document.createElement('button');
    item.className = 'notification-item unread';
    item.type = 'button';
    item.setAttribute('role', 'menuitem');
    item.dataset.notificationTarget = target;

    const dot = document.createElement('span');
    dot.className = 'notification-item-dot';
    dot.setAttribute('aria-hidden', 'true');

    const copy = document.createElement('span');
    copy.className = 'notification-item-copy';
    const title = document.createElement('strong');
    title.textContent = message || 'New notification';
    const detail = document.createElement('small');
    detail.textContent = meta;
    copy.append(title, detail);
    item.append(dot, copy);

    head?.insertAdjacentElement('afterend', item);
    Array.from(notificationMenu.querySelectorAll('.notification-item')).slice(6).forEach(extra => extra.remove());
    setNotificationCount(unreadNotificationCount());
  }

  function openNotificationItem(item) {
    if (!item) return;
    item.classList.remove('unread');
    setNotificationCount(unreadNotificationCount());
    setNotificationMenuOpen(false);
    routeNotificationTarget(item.dataset.notificationTarget || 'agent');
  }

  function showNotificationBubble(message = 'New notification', count = currentNotificationCount() + 1, target = 'agent') {
    if (!notificationButton || !notificationBubble) return;
    addNotificationMenuItem({ message, target, meta: 'now' });
    setNotificationCount(count);
    notificationBubble.textContent = message;
    notificationBubble.setAttribute('aria-hidden', 'false');
    notificationButton.classList.remove('notification-ringing', 'bubble-visible');
    void notificationButton.offsetHeight;
    notificationButton.classList.add('notification-ringing', 'bubble-visible');
    clearTimeout(notificationBubbleTimer);
    notificationBubbleTimer = setTimeout(() => {
      notificationButton.classList.remove('bubble-visible', 'notification-ringing');
      notificationBubble.setAttribute('aria-hidden', 'true');
    }, 3200);
  }

  window.abcPushNotification = detail => {
    const payload = typeof detail === 'string' ? { message: detail } : (detail || {});
    showNotificationBubble(payload.message || 'New notification', payload.count ?? currentNotificationCount() + 1, payload.target || 'agent');
  };

  window.addEventListener('abc:notification', event => {
    window.abcPushNotification(event.detail || {});
  });

  function activeWorkspaceScreen() {
    return workspaceScreens.find(screen => screen.dataset.workspaceScreen === activeWorkspaceId)
      || workspaceScreens[0]
      || workspace;
  }

  function readPersistedUiState() {
    try {
      const raw = window.localStorage?.getItem(uiStateStorageKey);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (error) {
      console.warn('Unable to read Harbor UI state', error);
      return {};
    }
  }

  function scheduleUiStateSave() {
    if (uiStateRestoring) return;
    clearTimeout(uiStateSaveTimer);
    uiStateSaveTimer = setTimeout(() => {
      try {
        window.localStorage?.setItem(uiStateStorageKey, JSON.stringify(persistedUiState));
      } catch (error) {
        console.warn('Unable to save Harbor UI state', error);
      }
    }, 120);
  }

  function persistWorkspaceState() {
    persistedUiState.activeWorkspaceId = activeWorkspaceId;
    persistedUiState.activePlanningProjectIndex = activePlanningProjectIndex;
    scheduleUiStateSave();
  }

  function setActiveWorkspace(id) {
    if (!workspaceOrder.includes(id)) return;
    activeWorkspaceId = id;
    const index = workspaceOrder.indexOf(id);
    workspaceStrip?.style.setProperty('--workspace-index', String(index));
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
      if (active) {
        button.setAttribute('aria-current', 'page');
      } else {
        button.removeAttribute('aria-current');
      }
    });

    buildState.textContent = 'V2 - ' + (workspaceLabels[id] || id) + ' workspace';
    persistWorkspaceState();
    requestAnimationFrame(() => constrainWorkspaceWindows(id));
  }

  function moveWorkspace(direction) {
    const current = workspaceOrder.indexOf(activeWorkspaceId);
    const next = Math.max(0, Math.min(workspaceOrder.length - 1, current + direction));
    setActiveWorkspace(workspaceOrder[next]);
  }

  function wireInboxFiles() {
    document.querySelectorAll('[data-inbox-files]').forEach(root => {
      if (root.dataset.filesPrepared) return;
      root.dataset.filesPrepared = 'true';
      const search = root.querySelector('[data-files-search]');
      const filter = root.querySelector('[data-files-filter]');
      const mountButtons = Array.from(root.querySelectorAll('[data-mount-target]'));
      const rows = Array.from(root.querySelectorAll('[data-mount]'));
      let activeMount = root.querySelector('[data-mount-target].active')?.dataset.mountTarget || 'project';
      let filteredOnly = false;

      function syncRows() {
        const query = (search?.value || '').trim().toLowerCase();
        rows.forEach(row => {
          const matchesMount = row.dataset.mount === activeMount;
          const matchesQuery = !query || (row.dataset.fileName || row.textContent).toLowerCase().includes(query);
          const matchesFilter = !filteredOnly || !row.classList.contains('folder');
          row.hidden = !(matchesMount && matchesQuery && matchesFilter);
        });
      }

      mountButtons.forEach(button => {
        button.addEventListener('click', () => {
          activeMount = button.dataset.mountTarget || 'project';
          mountButtons.forEach(item => {
            const active = item === button;
            item.classList.toggle('active', active);
            item.setAttribute('aria-selected', String(active));
          });
          syncRows();
          buildState.textContent = 'V2 - Files: ' + button.textContent.trim();
        });
      });

      search?.addEventListener('input', syncRows);

      filter?.addEventListener('click', () => {
        filteredOnly = !filteredOnly;
        filter.classList.toggle('active', filteredOnly);
        filter.setAttribute('aria-pressed', String(filteredOnly));
        syncRows();
        buildState.textContent = filteredOnly ? 'V2 - Files: folders hidden' : 'V2 - Files: all items';
      });

      syncRows();
    });
  }

  function wireInboxStatusTabs() {
    document.querySelectorAll('.inbox-analysis-window').forEach(root => {
      if (root.dataset.statusTabsPrepared) return;
      root.dataset.statusTabsPrepared = 'true';
      const tabs = Array.from(root.querySelectorAll('[data-status-tab]'));
      const panels = Array.from(root.querySelectorAll('[data-status-panel]'));
      if (!tabs.length || !panels.length) return;

      function activateStatusTab(name, focus = false) {
        tabs.forEach(tab => {
          const active = tab.dataset.statusTab === name;
          tab.classList.toggle('active', active);
          tab.setAttribute('aria-selected', String(active));
          tab.tabIndex = active ? 0 : -1;
          if (active && focus) tab.focus();
        });

        panels.forEach(panel => {
          panel.hidden = panel.dataset.statusPanel !== name;
        });
      }

      tabs.forEach((tab, index) => {
        tab.tabIndex = tab.classList.contains('active') ? 0 : -1;
        tab.addEventListener('click', () => activateStatusTab(tab.dataset.statusTab || 'now'));
        tab.addEventListener('keydown', event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          let nextIndex = index;
          if (event.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length;
          if (event.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length;
          if (event.key === 'Home') nextIndex = 0;
          if (event.key === 'End') nextIndex = tabs.length - 1;
          activateStatusTab(tabs[nextIndex].dataset.statusTab || 'now', true);
        });
      });

      activateStatusTab(tabs.find(tab => tab.classList.contains('active'))?.dataset.statusTab || 'now');
    });
  }

  function serializeContextNodges() {
    return Array.from(contextNodges.querySelectorAll('.context-nodge:not([data-context-suggestion="true"])')).map(nodge => ({
      kind: nodge.dataset.contextKind || 'Context',
      label: nodge.dataset.contextLabel || nodge.textContent.trim(),
      path: nodge.dataset.contextPath || '',
      summary: nodge.dataset.contextSummary || '',
      pinned: nodge.dataset.contextPinned === 'true'
    }));
  }

  function contextKindMeta(kind) {
    const normalized = String(kind || 'Context').toLowerCase();
    const map = {
      file: { icon: '[]', hint: 'File attached to this prompt' },
      mount: { icon: '#', hint: 'Folder mount available to this chat' },
      source: { icon: '@', hint: 'Knowledge source added as context' },
      project: { icon: '<>', hint: 'Project context is active' },
      roadmap: { icon: '*', hint: 'Roadmap is pinned to this chat' },
      chat: { icon: '//', hint: 'Chat context is linked' },
      memory: { icon: '*', hint: 'Memory context is active' },
      document: { icon: 'pin', hint: 'Document summary is pinned to this chat' },
      selection: { icon: '+', hint: 'Selection added as context' }
    };
    return {
      key: map[normalized] ? normalized : 'context',
      icon: map[normalized]?.icon || '+',
      hint: map[normalized]?.hint || 'Context attached to this chat'
    };
  }

  function renderContextNodges(contexts) {
    contextNodges.innerHTML = '';
    (contexts || []).forEach(context => {
      const meta = contextKindMeta(context.kind);
      const nodge = document.createElement('span');
      nodge.className = 'context-nodge';
      nodge.dataset.contextKind = context.kind;
      nodge.dataset.contextType = meta.key;
      nodge.dataset.contextLabel = context.label;
      nodge.dataset.contextPinned = context.pinned === false ? 'false' : 'true';
      if (context.path) nodge.dataset.contextPath = context.path;
      if (context.summary) nodge.dataset.contextSummary = context.summary;
      nodge.title = context.kind + ': ' + context.label + ' - ' + (context.summary || meta.hint);

      const icon = document.createElement('span');
      icon.className = 'context-nodge-icon';
      icon.textContent = meta.icon;

      const kind = document.createElement('span');
      kind.className = 'context-nodge-kind';
      kind.textContent = context.kind;

      const label = document.createElement('span');
      label.className = 'context-nodge-label';
      label.textContent = context.label;

      const remove = document.createElement('button');
      remove.className = 'context-nodge-remove';
      remove.type = 'button';
      remove.setAttribute('aria-label', 'Remove ' + context.kind + ' context');
      remove.textContent = 'x';

      const tooltip = document.createElement('span');
      tooltip.className = 'context-nodge-tooltip';
      tooltip.innerHTML = context.summary
        ? '<strong>' + esc(context.label) + '</strong><span>' + esc(context.summary) + '</span>'
        : context.kind + ': ' + meta.hint;

      nodge.append(icon, label, remove, tooltip);
      contextNodges.appendChild(nodge);
    });
    renderOpenDocumentContextSuggestions();
    contextNodges.hidden = !contextNodges.children.length;
  }

  function addContextNodge(kind, label, details = {}) {
    const contexts = serializeContextNodges();
    const exists = contexts.some(context => context.kind === kind && context.label === label && (details.path ? context.path === details.path : true));
    if (!exists) contexts.push({ kind, label, ...details, pinned: true });
    renderContextNodges(contexts);
    const current = chatSpaces[activeChatIndex];
    if (current) current.contexts = contexts;
  }

  function openDocumentContexts() {
    return Array.from(document.querySelectorAll('.document-viewer-window')).map(win => ({
      label: win.dataset.documentTitle || win.querySelector('[data-document-title]')?.textContent?.trim() || basename(win.dataset.documentPath),
      path: win.dataset.documentPath || '',
      summary: win.dataset.documentSummary || 'Imported document summary will appear here after Universal Inbox analysis.',
      type: win.dataset.documentType || 'text'
    })).filter(context => context.path);
  }

  function renderOpenDocumentContextSuggestions() {
    if (!contextNodges) return;
    contextNodges.querySelectorAll('[data-context-suggestion="true"]').forEach(item => item.remove());
    document.getElementById('document-context-tray')?.remove();
    const pinnedPaths = new Set(serializeContextNodges()
      .filter(context => context.kind === 'Document' || context.kind === 'Roadmap')
      .map(context => context.path));
    openDocumentContexts().forEach(context => {
      if (pinnedPaths.has(context.path)) return;
      contextNodges.appendChild(documentSuggestionNodge(context));
    });
    contextNodges.hidden = !contextNodges.children.length;
  }

  function documentSuggestionNodge(context) {
    const nodge = document.createElement('button');
    nodge.type = 'button';
    nodge.className = 'context-nodge context-nodge-suggestion';
    const kind = context.type === 'roadmap' ? 'Roadmap' : 'Document';
    nodge.dataset.contextSuggestion = 'true';
    nodge.dataset.contextKind = kind;
    nodge.dataset.contextType = kind.toLowerCase();
    nodge.dataset.contextLabel = context.label;
    nodge.dataset.contextPath = context.path;
    nodge.dataset.contextSummary = context.summary;
    nodge.setAttribute('aria-label', 'Pin ' + kind.toLowerCase() + ' context ' + context.label);
    nodge.title = 'Pin ' + kind.toLowerCase() + ' context: ' + context.summary;
    nodge.innerHTML = [
      '<span class="context-nodge-icon">+</span>',
      '<span class="context-nodge-label">' + esc(context.label) + '</span>',
      '<span class="context-nodge-tooltip"><strong>' + esc(context.label) + '</strong><span>' + esc(context.summary) + '</span></span>'
    ].join('');
    return nodge;
  }

  function pinDocumentContextFromSuggestion(nodge) {
    addContextNodge(nodge.dataset.contextKind || 'Document', nodge.dataset.contextLabel || 'Document', {
      path: nodge.dataset.contextPath || '',
      summary: nodge.dataset.contextSummary || '',
      pinned: true
    });
    buildState.textContent = 'V2 - Document context pinned';
  }

  function currentShortTime(offsetMinutes = 0) {
    const date = new Date(Date.now() + offsetMinutes * 60000);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function aiMetaRows(message, index) {
    const defaults = [
      { label: 'Time', value: message.dataset.aiTime || currentShortTime(index * 2) },
      { label: 'Model', value: message.dataset.aiModel || 'deepseek-v4-flash' },
      { label: 'Tokens', value: message.dataset.aiTokens || (260 + index * 84) + ' in / ' + (42 + index * 18) + ' out' },
      { label: 'Context', value: message.dataset.aiContext || (18 + index * 3) + '% used' },
      { label: 'Latency', value: message.dataset.aiLatency || (message.classList.contains('working') ? 'running' : (1.1 + index * 0.4).toFixed(1) + 's') }
    ];
    return defaults;
  }

  function ensureAiMetaHotspots() {
    messages.querySelectorAll('.message.ai').forEach((message, index) => {
      if (message.querySelector('.ai-meta-hotspot')) return;
      const rows = aiMetaRows(message, index);
      const summary = rows.map(row => row.label + ': ' + row.value).join(', ');
      const hotspot = document.createElement('span');
      hotspot.className = 'ai-meta-hotspot';
      hotspot.tabIndex = 0;
      hotspot.setAttribute('role', 'button');
      hotspot.setAttribute('aria-label', 'AI response metadata. ' + summary);

      const tooltip = document.createElement('span');
      tooltip.className = 'ai-meta-tooltip';
      tooltip.setAttribute('role', 'tooltip');
      rows.forEach(row => {
        const item = document.createElement('span');
        item.className = 'ai-meta-tooltip-row';
        const label = document.createElement('span');
        label.className = 'ai-meta-tooltip-label';
        label.textContent = row.label;
        const value = document.createElement('span');
        value.className = 'ai-meta-tooltip-value';
        value.textContent = row.value;
        item.append(label, value);
        tooltip.appendChild(item);
      });

      message.prepend(tooltip);
      message.prepend(hotspot);
    });
    syncWorkRuns();
  }

  function workRunSteps(mode) {
    if (mode === 'plan') {
      return [
        { type: 'Read', title: 'Goal clarified', detail: 'Separated the desired outcome from implementation details.' },
        { type: 'Checked', title: 'Relevant context found', detail: 'Looked at matching project state and prior UI decisions.' },
        { type: 'Decided', title: 'Small next step chosen', detail: 'Prepared a plan without changing files yet.' }
      ];
    }
    return [
      { type: 'Read', title: 'Request understood', detail: 'Mapped the prompt to the current chat and workspace state.' },
      { type: 'Checked', title: 'Context inspected', detail: 'Checked the useful UI structure before answering.' },
      { type: 'Prepared', title: 'Safe next action ready', detail: 'Chose the first reversible implementation path.' }
    ];
  }

  function workRunMarkup(status, steps) {
    const isRunning = status === 'running';
    const isAttention = status === 'attention';
    const collapsed = !isRunning;
    const count = steps.length;
    const summary = isRunning
      ? 'ABC is doing ' + count + ' background steps'
      : 'ABC did ' + count + ' background steps';
    const hint = collapsed ? 'Show work log' : 'Hide work log';
    return [
      '<section class="work-run ' + esc(status) + (collapsed ? ' collapsed' : '') + '" data-work-run>',
      '  <button class="work-run-summary" type="button" data-work-run-toggle aria-expanded="' + String(!collapsed) + '">',
      '    <span class="work-run-dot" aria-hidden="true"></span>',
      '    <span class="work-run-copy"><strong>' + esc(summary) + '</strong><span>' + esc(hint) + '</span></span>',
      '  </button>',
      '  <div class="work-run-details">',
      steps.map((step, index) => (
        '    <div class="work-step" data-work-step-type="' + esc(step.type.toLowerCase()) + '">' +
        '<span class="work-step-type">' + esc(step.type) + '</span>' +
        '<strong>' + esc(step.title) + '</strong>' +
        '<small>' + esc(step.detail || 'Finished') + '</small>' +
        '<span class="work-step-index">' + esc(String(index + 1).padStart(2, '0')) + '</span>' +
        '</div>'
      )).join(''),
      '  </div>',
      '</section>'
    ].join('');
  }

  function syncWorkRuns() {
    messages.querySelectorAll('[data-work-run]').forEach(run => {
      const toggle = run.querySelector('[data-work-run-toggle]');
      const copyHint = run.querySelector('.work-run-copy span');
      const isExpanded = !run.classList.contains('collapsed');
      toggle?.setAttribute('aria-expanded', String(isExpanded));
      if (copyHint) copyHint.textContent = isExpanded ? 'Hide work log' : 'Show work log';
    });
  }

  function documentModeForPath(path) {
    const extension = String(path || '').split('.').pop().toLowerCase();
    if (['py', 'js', 'jsx', 'ts', 'tsx', 'html', 'css', 'json', 'yaml', 'yml', 'toml', 'rs', 'go', 'java', 'cs', 'cpp', 'c', 'h', 'sql', 'sh', 'ps1'].includes(extension)) return 'code';
    if (['pdf', 'doc', 'docx'].includes(extension)) return 'pdf';
    return 'text';
  }

  function languageForPath(path) {
    const extension = String(path || '').split('.').pop().toLowerCase();
    const map = {
      py: 'python',
      js: 'javascript',
      jsx: 'javascript',
      ts: 'javascript',
      tsx: 'javascript',
      html: 'html',
      css: 'css',
      json: 'json',
      md: 'markdown',
      txt: 'text'
    };
    return map[extension] || 'text';
  }

  function basename(path) {
    return String(path || 'Untitled').split(/[\\/]/).pop() || 'Untitled';
  }

  function normalizeDocument(pathOrDocument) {
    if (typeof pathOrDocument === 'object' && pathOrDocument) {
      const path = pathOrDocument.path || pathOrDocument.title || 'Untitled';
      return {
        title: pathOrDocument.title || basename(path),
        path,
        type: pathOrDocument.type || documentModeForPath(path),
        language: pathOrDocument.language || languageForPath(path),
        page: pathOrDocument.page || '1 / 1',
        zoom: pathOrDocument.zoom || '100%',
        summary: pathOrDocument.summary || '',
        content: pathOrDocument.content || '',
        roadmapId: pathOrDocument.roadmapId || '',
        sequence: pathOrDocument.sequence || '',
        status: pathOrDocument.status || '',
        project: pathOrDocument.project || '',
        tasks: pathOrDocument.tasks || [],
        gates: pathOrDocument.gates || []
      };
    }

    const path = String(pathOrDocument || '').trim();
    const sample = documentSamples[path] || documentSamples[basename(path)];
    if (sample) return normalizeDocument(sample);
    return {
      title: basename(path),
      path,
      type: documentModeForPath(path),
      language: languageForPath(path),
      page: '1 / 1',
      zoom: '100%',
      summary: 'Imported document summary will appear here after Universal Inbox analysis.',
      content: sampleDocumentContent(path)
    };
  }

  function sampleDocumentContent(path) {
    const type = documentModeForPath(path);
    if (type === 'code') {
      return codeLanguageSamples[languageForPath(path)]?.content || '// File preview will load here.';
    }
    if (type === 'pdf') {
      return 'Document Preview\n\nThis PDF or office document opens with page and zoom tools only.';
    }
    return '# Document Preview\n\nThe selected text document opens here as a quiet writing surface.';
  }

  function renderDocumentHeaderControl(doc) {
    if (doc.type !== 'code') return '';
    const languages = ['python', 'javascript', 'html', 'json', 'diff'];
    const selected = languages.includes(doc.language) ? doc.language : 'python';
    return [
      '<select class="document-language-select" data-doc-language aria-label="Programming language preset">',
      languages.map(language => '<option value="' + esc(language) + '"' + (language === selected ? ' selected' : '') + '>' + esc(languageLabel(language)) + '</option>').join(''),
      '</select>'
    ].join('');
  }

  function languageLabel(language) {
    const labels = {
      python: 'Python',
      javascript: 'JavaScript',
      html: 'HTML',
      json: 'JSON',
      diff: 'Diff'
    };
    return labels[language] || language;
  }

  function renderDocumentBody(doc) {
    if (doc.type === 'roadmap') return renderRoadmapDocument(doc);
    if (doc.type === 'code') return renderCodeDocument(doc);
    if (doc.type === 'pdf') return renderPdfDocument(doc);
    return renderTextDocument(doc);
  }

  function renderRoadmapDocument(doc) {
    const tasks = Array.isArray(doc.tasks) && doc.tasks.length ? doc.tasks : [
      { state: 'open', title: 'Roadmap work', detail: doc.summary || 'Roadmap task details will appear here.' }
    ];
    const gates = Array.isArray(doc.gates) ? doc.gates : [];
    const statusLabel = doc.status === 'future' ? 'open' : (doc.status || 'open');
    return [
      '<section class="document-mode document-roadmap-mode" data-roadmap-document aria-label="Roadmap document">',
      '  <div class="roadmap-document-bar">',
      '    <span class="roadmap-document-path">' + esc(doc.path) + '</span>',
      '    <div class="roadmap-document-tabs" aria-label="Roadmap document mode">',
      '      <button class="active" type="button" data-roadmap-doc-mode="read">Read</button>',
      '      <button type="button" data-roadmap-doc-mode="data">Data</button>',
      '    </div>',
      '  </div>',
      '  <div class="roadmap-document-content" data-roadmap-doc-view="read">',
      '    <article class="roadmap-readable">',
      '      <div class="roadmap-doc-kicker">Roadmap ' + esc(doc.sequence || doc.roadmapId || '') + ' - ' + esc(statusLabel) + '</div>',
      '      <h1>' + esc(String(doc.title || '').replace(/^.* - /, '')) + '</h1>',
      '      <section class="roadmap-doc-summary">',
      '        <div class="roadmap-doc-section-head"><span>Summary</span></div>',
      '        <textarea data-roadmap-summary spellcheck="true">' + esc(doc.summary || '') + '</textarea>',
      '      </section>',
      '      <section class="roadmap-doc-section">',
      '        <h2>Tasks</h2>',
      '        <div class="roadmap-doc-task-list">',
      tasks.map((task, index) => [
        '<article class="roadmap-doc-task">',
        '  <span class="roadmap-doc-check">' + (task.state === 'done' ? '✓' : '') + '</span>',
        '  <span class="roadmap-doc-task-copy"><strong contenteditable="true">' + esc(task.title || ('Task ' + (index + 1))) + '</strong><span contenteditable="true">' + esc(task.detail || '') + '</span></span>',
        '  <span class="roadmap-doc-task-state">' + esc(task.state || 'open') + '</span>',
        '</article>'
      ].join('')).join(''),
      '        </div>',
      '      </section>',
      '      <section class="roadmap-doc-section">',
      '        <h2>Gates</h2>',
      '        <div class="roadmap-doc-gates">',
      (gates.length ? gates : [{ state: 'open', title: 'No blocking gate', label: 'This roadmap has no visible blocking gate in the current graph.' }]).map(gate => [
        '<article class="roadmap-doc-gate ' + esc(gate.state || 'open') + '">',
        '  <span>' + esc(gate.state === 'done' ? 'passed' : gate.state || 'open') + '</span>',
        '  <strong>' + esc(gate.title || gate.id || 'Gate') + '</strong>',
        '</article>'
      ].join('')).join(''),
      '        </div>',
      '      </section>',
      '    </article>',
      '  </div>',
      '  <div class="roadmap-document-content roadmap-document-data" data-roadmap-doc-view="data" hidden>',
      '    <textarea class="roadmap-json-editor" data-roadmap-json spellcheck="false">' + esc(doc.content || '{}') + '</textarea>',
      '  </div>',
      '</section>'
    ].join('');
  }

  function renderTextDocument(doc) {
    const lines = String(doc.content || '').split('\n');
    const body = lines.map(line => {
      if (line.startsWith('# ')) return '<h1>' + esc(line.slice(2)) + '</h1>';
      if (line.startsWith('## ')) return '<h2>' + esc(line.slice(3)) + '</h2>';
      if (line.startsWith('- ')) return '<li>' + esc(line.slice(2)) + '</li>';
      if (!line.trim()) return '';
      return '<p>' + esc(line) + '</p>';
    }).join('');

    return [
      '<section class="document-mode document-text-mode" aria-label="Text document">',
      '  <div class="document-format-bar" aria-label="Text formatting">',
      '    <button class="document-format-button active" type="button" title="Bold">B</button>',
      '    <button class="document-format-button" type="button" title="Italic">I</button>',
      '    <button class="document-format-button" type="button" title="Heading">H</button>',
      '    <button class="document-format-button" type="button" title="List">List</button>',
      '    <button class="document-format-button" type="button" title="Code">`</button>',
      '  </div>',
      '  <article class="document-text-surface">' + body + '</article>',
      '</section>'
    ].join('');
  }

  function renderCodeDocument(doc) {
    const code = String(doc.content || '');
    const lines = code.split('\n').map((_, index) => index + 1).join('\n');
    return [
      '<section class="document-mode document-code-mode" aria-label="Code document">',
      '  <div class="document-code-subhead">',
      '    <span>' + esc(doc.path) + '</span>',
      '  </div>',
      '  <section class="document-code-editor">',
      '    <pre class="document-line-numbers">' + esc(lines) + '</pre>',
      '    <pre class="document-code-content" data-document-code>' + highlightCode(code, doc.language) + '</pre>',
      '  </section>',
      '</section>'
    ].join('');
  }

  function renderPdfDocument(doc) {
    const paragraphs = String(doc.content || '').split('\n\n').filter(Boolean);
    const title = paragraphs.shift() || doc.title;
    return [
      '<section class="document-mode document-pdf-mode" aria-label="PDF or office document">',
      '  <div class="document-pdf-tools" aria-label="PDF tools">',
      '    <div class="document-page-state">Page ' + esc(doc.page || '1 / 1') + '</div>',
      '    <div class="document-zoom-tools">',
      '      <button type="button" title="Zoom out">-</button>',
      '      <button type="button" title="Fit page">Fit</button>',
      '      <button type="button" title="Zoom in">+</button>',
      '    </div>',
      '    <div class="document-zoom-label">' + esc(doc.zoom || '100%') + '</div>',
      '  </div>',
      '  <section class="document-pdf-canvas">',
      '    <article class="document-pdf-page">',
      '      <h1>' + esc(title) + '</h1>',
      paragraphs.map((paragraph, index) => (index === 1 ? '<div class="document-pdf-rule"></div>' : '') + '<p>' + esc(paragraph) + '</p>').join(''),
      '    </article>',
      '  </section>',
      '</section>'
    ].join('');
  }

  function highlightCode(source, language) {
    const escaped = esc(source);
    if (language === 'html') {
      return escaped
        .replace(/(&lt;\/?)([a-z0-9-]+)/gi, '$1<span class="token-tag">$2</span>')
        .replace(/([a-z-]+)=(&quot;[^&]+&quot;)/gi, '<span class="token-attr">$1</span>=<span class="token-str">$2</span>');
    }
    if (language === 'json') {
      return escaped
        .replace(/(&quot;[^&]+&quot;)(?=:)/g, '<span class="token-key">$1</span>')
        .replace(/: (&quot;[^&]+&quot;)/g, ': <span class="token-str">$1</span>')
        .replace(/\b(\d+)\b/g, '<span class="token-num">$1</span>');
    }
    if (language === 'javascript') {
      return escaped
        .replace(/\b(const|let|function|return|if|else|await|async)\b/g, '<span class="token-key">$1</span>')
        .replace(/(&quot;[^&]+&quot;|'[^']*')/g, '<span class="token-str">$1</span>')
        .replace(/\b(\d+)\b/g, '<span class="token-num">$1</span>');
    }
    if (language === 'diff') {
      return escaped
        .replace(/^(\+.*)$/gm, '<span class="token-added">$1</span>')
        .replace(/^(-.*)$/gm, '<span class="token-removed">$1</span>')
        .replace(/^(@@.*)$/gm, '<span class="token-key">$1</span>')
        .replace(/^(diff --git.*)$/gm, '<span class="token-comment">$1</span>');
    }
    return escaped
      .replace(/(#.*)$/gm, '<span class="token-comment">$1</span>')
      .replace(/\b(def|return|if|else|for|in|class|import|from)\b/g, '<span class="token-key">$1</span>')
      .replace(/\b(\d+)\b/g, '<span class="token-num">$1</span>');
  }

  function updateDocumentViewer(win, input) {
    const doc = normalizeDocument(input);
    win.dataset.documentPath = doc.path;
    win.dataset.documentType = doc.type;
    win.dataset.documentTitle = doc.title;
    win.dataset.documentSummary = doc.summary || 'Imported document summary will appear here after Universal Inbox analysis.';
    win.dataset.documentRoadmapId = doc.roadmapId || '';
    win.dataset.documentProject = doc.project || '';
    const title = win.querySelector('[data-document-title]');
    const left = win.querySelector('[data-document-header-left]');
    const body = win.querySelector('.window-body');
    if (title) title.textContent = doc.title;
    if (left) left.innerHTML = renderDocumentHeaderControl(doc);
    if (body) body.innerHTML = renderDocumentBody(doc);
    wireDocumentViewer(win);
    renderOpenDocumentContextSuggestions();
  }

  function openDocumentViewer(input) {
    const doc = normalizeDocument(input);
    let win = Array.from(document.querySelectorAll('.document-viewer-window'))
      .find(item => item.dataset.documentPath === doc.path);
    if (!win) {
      const existingCount = document.querySelectorAll('.document-viewer-window').length;
      win = document.createElement('article');
      win.className = 'floating-window document-viewer-window active';
      win.id = 'document-viewer-window-' + (existingCount + 1);
      win.dataset.window = '';
      win.dataset.windowId = 'document-viewer-' + (existingCount + 1);
      win.dataset.windowCloseMode = 'remove';
      win.setAttribute('aria-label', 'Document Viewer');
      win.innerHTML = [
        '<header class="window-head document-window-head" data-drag-handle>',
        '  <div class="document-header-left" data-document-header-left></div>',
        '  <div class="window-title document-window-title" data-document-title></div>',
        '  <div class="window-actions" aria-label="Window controls">',
        '    <button class="window-control document-spark-control" data-spark-document title="Send document to Agent" aria-label="Send document to Agent">✦</button>',
        '    <button class="window-control" data-window-min title="Minimize" aria-label="Minimize">-</button>',
        '    <button class="window-control" data-window-max title="Maximize" aria-label="Maximize">&#9633;</button>',
        '    <button class="window-control" data-window-close title="Close" aria-label="Close">x</button>',
        '  </div>',
        '</header>',
        '<div class="window-body"></div>'
      ].join('');
      activeWorkspaceScreen().appendChild(win);
      offsetDocumentWindow(win, existingCount);
      prepareFloatingWindow(win);
    } else {
      setWindowMinimized(win, false);
    }
    updateDocumentViewer(win, doc);
    activateWindow(win);
    buildState.textContent = 'V2 - Opened ' + doc.title;
    return win;
  }

  function offsetDocumentWindow(win, index) {
    if (!index) return;
    const width = Math.min(1080, Math.max(620, window.innerWidth - 110));
    const height = Math.min(780, Math.max(420, window.innerHeight - 96));
    const margin = 24;
    const left = Math.min(window.innerWidth - width - margin, Math.max(margin, ((window.innerWidth - width) / 2) + (index * 30)));
    const top = Math.min(window.innerHeight - height - margin, Math.max(margin, ((window.innerHeight - height) / 2) + (index * 24)));
    win.style.left = left + 'px';
    win.style.top = top + 'px';
    win.style.transform = 'none';
  }

  function wireDocumentViewer(win) {
    const select = win.querySelector('[data-doc-language]');
    if (select && !select.dataset.documentPrepared) {
      select.dataset.documentPrepared = 'true';
      select.addEventListener('change', event => {
        const sample = codeLanguageSamples[event.target.value] || codeLanguageSamples.python;
        updateDocumentViewer(win, {
          title: sample.title,
          path: sample.path,
          type: 'code',
          language: event.target.value,
          content: sample.content
        });
      });
    }

    win.querySelectorAll('[data-roadmap-doc-mode]').forEach(button => {
      if (button.dataset.roadmapModePrepared) return;
      button.dataset.roadmapModePrepared = 'true';
      button.addEventListener('click', event => {
        event.preventDefault();
        const mode = button.dataset.roadmapDocMode || 'read';
        win.querySelectorAll('[data-roadmap-doc-mode]').forEach(item => item.classList.toggle('active', item === button));
        win.querySelectorAll('[data-roadmap-doc-view]').forEach(view => {
          view.hidden = view.dataset.roadmapDocView !== mode;
        });
      });
    });

    win.querySelectorAll('[data-spark-document]').forEach(button => {
      if (button.dataset.sparkPrepared) return;
      button.dataset.sparkPrepared = 'true';
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        sparkDocumentToAgent(win, button.dataset);
      });
    });
  }

  function sparkDocumentToAgent(win, data = {}) {
    const kind = data.sparkKind || (win.dataset.documentType === 'roadmap' ? 'Roadmap' : 'Document');
    const section = data.sparkSection ? ' - ' + data.sparkSection : '';
    const label = data.sparkLabel || win.dataset.documentTitle || basename(win.dataset.documentPath);
    const summary = data.sparkSummary || win.dataset.documentSummary || 'Document context is ready for the next prompt.';
    addContextNodge(kind, label + section, {
      path: data.sparkPath || win.dataset.documentPath || '',
      summary,
      pinned: true
    });
    setActiveWorkspace('agent');
    setWindowMinimized(coreWindow, false);
    activateWindow(coreWindow);
    promptInput?.focus();
    buildState.textContent = 'V2 - Spark context sent to Agent';
  }

  function documentPathFromTarget(target) {
    const link = target.closest('[data-document-link]');
    if (!link) return null;
    return link.dataset.documentLink || link.textContent.trim();
  }

  function isDocumentLinkableText(text) {
    return /\.(md|txt|pdf|doc|docx|js|jsx|ts|tsx|py|html|css|json|yaml|yml|toml|rs|go|java|cs|cpp|c|h|sql|sh|ps1)\b/i.test(text);
  }

  function linkifyDocumentReferences(root) {
    const scope = root || messages;
    const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || !node.nodeValue || !isDocumentLinkableText(node.nodeValue)) return NodeFilter.FILTER_REJECT;
        if (parent.closest('button, a, select, textarea, input, [data-document-link]')) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    const pattern = /((?:[A-Za-z0-9_.-]+\/)+(?:[A-Za-z0-9_.-]+)\.(?:md|txt|pdf|docx?|js|jsx|ts|tsx|py|html|css|json|yaml|yml|toml|rs|go|java|cs|cpp|c|h|sql|sh|ps1)|[A-Za-z0-9_.-]+\.(?:md|txt|pdf|docx?|js|jsx|ts|tsx|py|html|css|json|yaml|yml|toml|rs|go|java|cs|cpp|c|h|sql|sh|ps1))/gi;
    nodes.forEach(node => {
      const text = node.nodeValue;
      const fragment = document.createDocumentFragment();
      let last = 0;
      text.replace(pattern, (match, _unused, offset) => {
        fragment.append(document.createTextNode(text.slice(last, offset)));
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'document-inline-link';
        button.dataset.documentLink = match;
        button.textContent = match;
        fragment.append(button);
        last = offset + match.length;
        return match;
      });
      fragment.append(document.createTextNode(text.slice(last)));
      node.replaceWith(fragment);
    });
  }

  function chatIsNew(space) {
    if (!space) return false;
    return Boolean(space.isNew) || !String(space.messages || '').trim();
  }

  function modelStateClass(state) {
    if (state === 'busy') return 'busy';
    if (state === 'offline') return 'offline';
    return 'ready';
  }

  function setModelDotClass(dot, state) {
    if (!dot) return;
    dot.classList.remove('ready', 'busy', 'offline');
    dot.classList.add(modelStateClass(state));
  }

  function updateModelUi() {
    const current = chatSpaces[activeChatIndex];
    const isNew = chatIsNew(current);
    const model = current?.model || lastSelectedModel;
    const profile = modelProfiles[model] || modelProfiles[lastSelectedModel] || modelProfiles['deepseek-v4-flash'];
    const state = current?.modelState || profile.state || 'ready';
    if (currentModelLabel) currentModelLabel.textContent = model;
    if (composerModelLabel) composerModelLabel.textContent = model;
    setModelDotClass(headerModelChip?.querySelector('.model-status-dot'), state);
    setModelDotClass(composerModelButton?.querySelector('.model-status-dot'), state);
    const tooltip = headerModelChip?.querySelector('.chat-model-tooltip');
    if (tooltip) {
      tooltip.innerHTML = [
        ['Route', profile.route],
        ['Context', profile.context],
        ['Tokens', profile.tokens],
        ['Load', state === 'offline' ? 'problem' : profile.load],
        ['Note', profile.note]
      ].map(([label, value]) => `<span class="model-tooltip-row"><span>${esc(label)}</span><span>${esc(value)}</span></span>`).join('');
    }
    if (headerModelChip) headerModelChip.hidden = isNew;
    if (composerModelChooser) {
      composerModelChooser.hidden = !isNew;
      if (!isNew) composerModelChooser.classList.remove('open');
      composerModelButton?.setAttribute('aria-expanded', String(composerModelChooser.classList.contains('open')));
    }
    composerModelMenu?.querySelectorAll('[data-model-name]').forEach(row => {
      row.classList.toggle('selected', row.dataset.modelName === model);
    });
  }

  function renderChatHistory() {
    if (!chatHistoryList) return;
    const query = (chatHistorySearch?.value || '').trim().toLowerCase();
    const activeRows = chatSpaces.map((space, index) => ({
      title: space.title || 'New Chat',
      subtitle: chatIsNew(space) ? 'New chat' : (space.needsQuestion ? 'Needs your decision' : (space.unread ? 'Unread answer' : 'Active chat')),
      age: index === activeChatIndex ? 'now' : (space.lastAnswer || 'open'),
      state: space.needsQuestion ? 'question' : (chatIsWorking(space) ? 'working' : (space.unread ? 'unread' : '')),
      active: index === activeChatIndex,
      index
    }));
    const rows = [...activeRows, ...historicalChats.map(item => ({ ...item, index: -1 }))].filter(item => {
      if (!query) return true;
      return [item.title, item.subtitle, item.age].join(' ').toLowerCase().includes(query);
    });
    if (chatHistoryCount) chatHistoryCount.textContent = (chatSpaces.length + historicalChats.length) + ' chats';
    chatHistoryList.innerHTML = rows.map(item => `
      <button class="chat-history-item${item.active ? ' active' : ''}${item.state ? ' ' + esc(item.state) : ''}" type="button" data-chat-history-index="${item.index}" aria-label="${esc(item.title)} ${esc(item.subtitle)} ${esc(item.age)}">
        <span class="chat-history-text"><strong>${esc(item.title)}</strong><span>${esc(item.subtitle)}</span></span>
        <span class="chat-history-age"><span class="chat-history-state">${esc(item.state || 'open')}</span>${esc(item.age)}</span>
      </button>
    `).join('');
  }

  function setChatHistoryOpen(open) {
    if (!chatHistoryPanel) return;
    chatHistoryPanel.hidden = !open;
    coreWindow.classList.toggle('history-open', open);
    chatHistoryButton?.setAttribute('aria-expanded', String(open));
    if (open) renderChatHistory();
  }

  function startTitleRename() {
    if (!chatTitleEditor) return;
    chatTitleEditor.value = chatTitle.textContent.trim();
    chatTitle.hidden = true;
    chatTitleEditor.hidden = false;
    chatTitleEditor.focus();
    chatTitleEditor.select();
  }

  function commitTitleRename() {
    if (!chatTitleEditor || chatTitleEditor.hidden) return;
    const nextTitle = chatTitleEditor.value.trim() || 'Untitled chat';
    chatTitle.textContent = nextTitle;
    chatTitle.hidden = false;
    chatTitleEditor.hidden = true;
    const current = chatSpaces[activeChatIndex];
    if (current) current.title = nextTitle;
    updateChatCarousel();
    renderChatHistory();
  }

  function cancelTitleRename() {
    if (!chatTitleEditor) return;
    chatTitle.hidden = false;
    chatTitleEditor.hidden = true;
  }

  function selectChatModel(row) {
    const current = chatSpaces[activeChatIndex];
    if (!current || !row) return;
    current.model = row.dataset.modelName || lastSelectedModel;
    current.modelState = row.dataset.modelState || 'ready';
    lastSelectedModel = current.model;
    composerModelChooser?.classList.remove('open');
    composerModelButton?.setAttribute('aria-expanded', 'false');
    updateModelUi();
  }

  function demoAssistantMessage(promptText, mode, model, state) {
    const isPlan = mode === 'plan';
    const modelNote = state === 'busy'
      ? 'Local model is busy, so this would run with a smaller budget.'
      : (state === 'offline' ? 'Selected model has a problem; fallback would be requested.' : 'Model route is ready.');
    const body = isPlan
      ? [
        '<strong>Plan</strong>',
        '<span>1. Clarify the goal from your prompt.</span>',
        '<span>2. Pick the smallest safe next step.</span>',
        '<span>3. Ask before execution if the step changes files or state. Draft: docs/plans/memory-budget.md</span>',
        '<span class="busy-signal">Plan prepared <span class="pixel-wave" data-wave>&#9601;&#9602;&#9603;</span></span>'
      ].join('<br>')
      : [
        '<span>I would start with a focused pass on: ' + esc(promptText) + '</span>',
        '<span>' + esc(modelNote) + '</span>',
        '<span>Likely file to inspect: src/services/search_memory.py</span>',
        '<span class="busy-signal">Running first safe step <span class="pixel-wave" data-wave>&#9601;&#9602;&#9603;</span></span>'
      ].join('<br>');
    return '<div class="message ai" data-ai-time="' + esc(currentShortTime()) + '" data-ai-model="' + esc(model) + '" data-ai-tokens="demo" data-ai-context="' + (isPlan ? 'plan draft' : 'agent demo') + '" data-ai-latency="0.8s">' + body + '</div>';
  }

  function sendPromptDemo() {
    const current = chatSpaces[activeChatIndex];
    const text = promptInput.value.trim();
    if (!current || !text) return false;
    const mode = coreWindow.dataset.mode || 'agent';
    const model = current.model || lastSelectedModel;
    const state = current.modelState || modelProfiles[model]?.state || 'ready';
    const userMessage = '<div class="message user">' + esc(text) + '</div>';
    const separator = String(messages.innerHTML || '').trim() ? '' : '';
    const completedRun = workRunMarkup('complete', workRunSteps(mode));
    messages.innerHTML = String(messages.innerHTML || '').trim() + separator + userMessage + completedRun + demoAssistantMessage(text, mode, model, state);
    linkifyDocumentReferences(messages);
    promptInput.value = '';
    current.isNew = false;
    current.unread = false;
    current.needsQuestion = mode === 'plan';
    current.title = current.title === 'New Chat' ? text.slice(0, 36) : current.title;
    current.messages = messages.innerHTML;
    current.prompt = '';
    current.lastAnswer = 'now';
    autosizePrompt(promptInput);
    renderChatSpace(activeChatIndex);
    requestAnimationFrame(() => {
      messages.scrollTop = messages.scrollHeight;
    });
    return true;
  }

  function saveActiveChatSpace() {
    const current = chatSpaces[activeChatIndex];
    if (!current) return;
    ensureAiMetaHotspots();
    current.title = chatTitle.textContent.trim();
    current.messages = messages.innerHTML;
    current.prompt = promptInput.value;
    current.model = current.model || lastSelectedModel;
    current.contexts = serializeContextNodges();
  }

  function updateChatNodges() {
    if (leftNodge) leftNodge.hidden = true;
    if (rightNodge) rightNodge.hidden = true;
  }

  function chatIsWorking(space) {
    return /\bworking\b/.test(space.messages);
  }

  function updateChatCarousel() {
    if (!chatCarousel) return;
    chatCarousel.hidden = true;
    chatCarousel.innerHTML = '';
  }

  function renderChatSpace(index) {
    const next = chatSpaces[index];
    if (!next) return;
    next.unread = false;
    activeChatIndex = index;
    chatTitle.textContent = next.title;
    chatTitle.hidden = false;
    if (chatTitleEditor) chatTitleEditor.hidden = true;
    messages.innerHTML = next.messages;
    linkifyDocumentReferences(messages);
    ensureAiMetaHotspots();
    promptInput.value = next.prompt;
    renderContextNodges(next.contexts);
    autosizePrompt(promptInput);
    updateModelUi();
    updateChatNodges();
    updateChatCarousel();
    renderChatHistory();
    buildState.textContent = 'V2 - Chat ' + (activeChatIndex + 1) + ' / ' + chatSpaces.length;
  }

  function createChatSpace() {
    saveActiveChatSpace();
    const number = chatSpaces.length + 1;
    chatSpaces.push({
      id: 'chat-' + number,
      title: 'New Chat',
      messages: '',
      prompt: '',
      model: lastSelectedModel,
      modelState: 'ready',
      isNew: true,
      lastAnswer: 'new',
      contexts: []
    });
    setActiveWorkspace('agent');
    setWindowMinimized(coreWindow, false);
    renderChatSpace(chatSpaces.length - 1);
    closeToolwheel();
  }

  function moveChatSpace(direction) {
    saveActiveChatSpace();
    const nextIndex = activeChatIndex + direction;
    if (nextIndex < 0 || nextIndex >= chatSpaces.length) return;
    renderChatSpace(nextIndex);
  }

  function openChatSpace(index) {
    if (index === activeChatIndex) return;
    saveActiveChatSpace();
    renderChatSpace(index);
  }

  function activateWindow(win) {
    document.querySelectorAll('[data-window]').forEach(item => item.classList.remove('active'));
    win.classList.add('active');
    win.style.zIndex = String(++zTop);
    persistedUiState.activeWindowId = win.dataset.windowId || persistedUiState.activeWindowId;
    persistWindowState(win);
  }

  function selectedWindows() {
    return Array.from(document.querySelectorAll('[data-window].selected'))
      .filter(win => !win.classList.contains('minimized') && !win.classList.contains('maximized'));
  }

  function clearWindowSelection() {
    document.querySelectorAll('[data-window].selected').forEach(win => win.classList.remove('selected'));
  }

  function selectOnlyWindow(win) {
    clearWindowSelection();
    win.classList.add('selected');
  }

  function toggleWindowSelection(win) {
    win.classList.toggle('selected');
    buildState.textContent = 'V2 - ' + selectedWindows().length + ' window' + (selectedWindows().length === 1 ? '' : 's') + ' selected';
  }

  function prepareWindowSelection(win, event) {
    if (event.target.closest('button, textarea, .resize-handle')) return;
    if (event.shiftKey) {
      toggleWindowSelection(win);
    } else if (!win.classList.contains('selected')) {
      selectOnlyWindow(win);
    }
    activateWindow(win);
  }

  function activeDragGroup(win) {
    const selection = selectedWindows();
    const workspace = win.closest('[data-workspace-screen]');
    const workspaceSelection = selection.filter(item => item.closest('[data-workspace-screen]') === workspace);
    if (win.classList.contains('selected') && workspaceSelection.length > 1) return workspaceSelection;
    return [win];
  }

  function windowParentOrigin(win) {
    const parent = win.offsetParent || win.parentElement;
    if (!parent || parent === document.body) return { left: 0, top: 0 };
    const rect = parent.getBoundingClientRect();
    return { left: rect.left, top: rect.top };
  }

  function viewportRectToWindowBounds(win, rect) {
    const origin = windowParentOrigin(win);
    return {
      left: rect.left - origin.left,
      top: rect.top - origin.top,
      width: rect.width,
      height: rect.height
    };
  }

  function windowBoundsToViewportRect(win, bounds) {
    const origin = windowParentOrigin(win);
    return {
      left: bounds.left + origin.left,
      top: bounds.top + origin.top,
      width: bounds.width,
      height: bounds.height,
      right: bounds.left + origin.left + bounds.width,
      bottom: bounds.top + origin.top + bounds.height
    };
  }

  function numericBoundsFromRestoreBounds(bounds) {
    if (!bounds) return null;
    return {
      left: parseFloat(bounds.left) || 0,
      top: parseFloat(bounds.top) || 0,
      width: parseFloat(bounds.width) || 340,
      height: parseFloat(bounds.height) || 260
    };
  }

  function parentViewportSize(win) {
    const parent = win.offsetParent || win.parentElement || document.body;
    return {
      width: parent.clientWidth || window.innerWidth,
      height: parent.clientHeight || window.innerHeight
    };
  }

  const windowBoundaryMargin = {
    top: 8,
    right: 0,
    bottom: 0,
    left: 0
  };

  function sanitizeWindowBounds(win, bounds) {
    const size = parentViewportSize(win);
    const margin = windowBoundaryMargin;
    const width = Math.max(340, Math.min(Number(bounds.width) || 340, Math.max(340, size.width - margin.left - margin.right)));
    const height = Math.max(260, Math.min(Number(bounds.height) || 260, Math.max(260, size.height - margin.top - margin.bottom)));
    const maxLeft = Math.max(margin.left, size.width - width - margin.right);
    const maxTop = Math.max(margin.top, size.height - height - margin.bottom);
    return {
      left: Math.max(margin.left, Math.min(maxLeft, Number(bounds.left) || margin.left)),
      top: Math.max(margin.top, Math.min(maxTop, Number(bounds.top) || margin.top)),
      width,
      height
    };
  }

  function constrainWorkspaceWindows(workspaceId = activeWorkspaceId) {
    const screen = workspaceScreens.find(item => item.dataset.workspaceScreen === workspaceId);
    if (!screen) return;
    screen.querySelectorAll('[data-window]').forEach(win => {
      if (win.classList.contains('minimized')) return;
      if (win.classList.contains('maximized')) {
        const bounds = maximizedWindowBounds(win);
        setWindowBounds(win, bounds);
        persistWindowState(win);
        return;
      }
      setWindowBounds(win, currentWindowBounds(win));
      persistWindowState(win);
    });
  }

  function setWindowBounds(win, bounds) {
    const safe = sanitizeWindowBounds(win, bounds);
    win.style.left = safe.left + 'px';
    win.style.top = safe.top + 'px';
    win.style.width = safe.width + 'px';
    win.style.height = safe.height + 'px';
    win.style.transform = 'none';
    return safe;
  }

  function normalRestoreBounds(bounds) {
    return {
      left: bounds.left + 'px',
      top: bounds.top + 'px',
      width: bounds.width + 'px',
      height: bounds.height + 'px',
      transform: 'none'
    };
  }

  function currentWindowBounds(win) {
    const rect = win.getBoundingClientRect();
    return sanitizeWindowBounds(win, viewportRectToWindowBounds(win, rect));
  }

  function persistWindowState(win) {
    if (!win?.dataset.windowId || uiStateRestoring) return;
    const bounds = currentWindowBounds(win);
    const restoreBounds = numericBoundsFromRestoreBounds(win._restoreBounds);
    persistedUiState.windows = persistedUiState.windows || {};
    persistedUiState.windows[win.dataset.windowId] = {
      ...bounds,
      restoreBounds,
      minimized: win.classList.contains('minimized'),
      maximized: win.classList.contains('maximized'),
      zIndex: Number(win.style.zIndex) || 0,
      workspaceId: win.closest('[data-workspace-screen]')?.dataset.workspaceScreen || activeWorkspaceId,
      updatedAt: Date.now()
    };
    scheduleUiStateSave();
  }

  function clearPersistedWindowState(win) {
    if (!win?.dataset.windowId || !persistedUiState.windows) return;
    delete persistedUiState.windows[win.dataset.windowId];
    scheduleUiStateSave();
  }

  function restorePersistedWindowState(win) {
    if (!win?.dataset.windowId || win.dataset.windowRestored) return;
    win.dataset.windowRestored = 'true';
    const state = persistedUiState.windows?.[win.dataset.windowId];
    if (!state) return;

    uiStateRestoring = true;
    const baseBounds = state.maximized && state.restoreBounds ? state.restoreBounds : state;
    const restoredBounds = setWindowBounds(win, baseBounds);
    win._restoreBounds = null;

    if (state.maximized) {
      win._restoreBounds = normalRestoreBounds(restoredBounds);
      const maximized = maximizedWindowBounds(win);
      win.classList.add('maximized');
      setWindowBounds(win, maximized);
    } else {
      win.classList.remove('maximized');
    }

    win.classList.toggle('minimized', Boolean(state.minimized));
    if (state.minimized) {
      dockBubbleFor(win);
      win.classList.remove('active', 'selected');
    } else {
      removeDockBubble(win);
    }

    if (state.zIndex) {
      win.style.zIndex = String(state.zIndex);
      zTop = Math.max(zTop, Number(state.zIndex));
    }

    syncMaximizeControl(win.querySelector('[data-window-max]'), win.classList.contains('maximized'));
    uiStateRestoring = false;
  }

  function restoreLastActiveWindow() {
    const id = persistedUiState.activeWindowId;
    if (!id || !window.CSS?.escape) return;
    const win = document.querySelector('[data-window-id="' + CSS.escape(id) + '"]');
    if (win && !win.classList.contains('minimized')) {
      const workspaceId = win.closest('[data-workspace-screen]')?.dataset.workspaceScreen;
      if (workspaceId && workspaceId !== activeWorkspaceId) setActiveWorkspace(workspaceId);
      activateWindow(win);
      selectOnlyWindow(win);
    }
  }

  function prepareGroupDrag(group) {
    return group.map(win => {
      const rect = win.getBoundingClientRect();
      const bounds = viewportRectToWindowBounds(win, rect);
      win.style.transform = 'none';
      win.style.left = bounds.left + 'px';
      win.style.top = bounds.top + 'px';
      win.style.width = bounds.width + 'px';
      win.style.height = bounds.height + 'px';
      win.style.zIndex = String(++zTop);
      return {
        win,
        left: bounds.left,
        top: bounds.top,
        width: bounds.width,
        height: bounds.height
      };
    });
  }

  function clampDragDelta(groupBounds, dx, dy) {
    if (!groupBounds.length) return { dx, dy };
    const margin = windowBoundaryMargin;
    const size = parentViewportSize(groupBounds[0].win);
    const groupLeft = Math.min(...groupBounds.map(item => item.left));
    const groupTop = Math.min(...groupBounds.map(item => item.top));
    const groupRight = Math.max(...groupBounds.map(item => item.left + item.width));
    const groupBottom = Math.max(...groupBounds.map(item => item.top + item.height));
    const minDx = margin.left - groupLeft;
    const maxDx = size.width - margin.right - groupRight;
    const minDy = margin.top - groupTop;
    const maxDy = size.height - margin.bottom - groupBottom;
    return {
      dx: Math.max(minDx, Math.min(maxDx, dx)),
      dy: Math.max(minDy, Math.min(maxDy, dy))
    };
  }

  function windowTitle(win) {
    return win.querySelector('.chat-title')?.textContent?.trim()
      || win.querySelector('.window-title')?.textContent?.trim()
      || win.getAttribute('aria-label')
      || 'Window';
  }

  function ensureWindowId(win, index) {
    if (!win.dataset.windowId) {
      win.dataset.windowId = 'window-' + (index + 1);
    }
    return win.dataset.windowId;
  }

  function dockBubbleFor(win) {
    const dock = document.getElementById('window-dock');
    if (!dock) return null;
    const id = win.dataset.windowId;
    let bubble = dock.querySelector('[data-restore-window="' + id + '"]');
    if (!bubble) {
      bubble = document.createElement('button');
      bubble.type = 'button';
      bubble.className = 'dock-bubble';
      bubble.dataset.restoreWindow = id;
      bubble.innerHTML = '<span class="dock-bubble-label"></span>';
      dock.appendChild(bubble);
    }
    const label = bubble.querySelector('.dock-bubble-label');
    if (label) label.textContent = windowTitle(win);
    bubble.title = 'Restore ' + windowTitle(win);
    bubble.setAttribute('aria-label', 'Restore ' + windowTitle(win));
    return bubble;
  }

  function removeDockBubble(win) {
    const dock = document.getElementById('window-dock');
    const id = win.dataset.windowId;
    const bubble = dock?.querySelector('[data-restore-window="' + id + '"]');
    if (bubble) bubble.remove();
  }

  function setWindowMinimized(win, minimized) {
    win.classList.toggle('minimized', minimized);
    if (minimized) {
      dockBubbleFor(win);
      win.classList.remove('active');
      win.classList.remove('selected');
    } else {
      removeDockBubble(win);
      selectOnlyWindow(win);
      activateWindow(win);
    }
    persistWindowState(win);
  }

  function setComposerMenuOpen(shell, open) {
    if (!shell) return;
    const button = shell.querySelector('.composer-menu-button');
    const menu = shell.querySelector('.composer-tools-menu');
    shell.classList.toggle('menu-open', open);
    button?.setAttribute('aria-expanded', String(open));
    menu?.setAttribute('aria-hidden', String(!open));
  }

  function setWorkspaceMode(mode) {
    coreWindow.dataset.mode = mode;
    coreWindow.querySelectorAll('[data-mode-option]').forEach(button => {
      const active = button.dataset.modeOption === mode;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    buildState.textContent = mode === 'plan' ? 'V2 - Planning mode mockup' : 'V2 - Agent mode';
  }

  function setVoiceInputMode(active) {
    const composer = coreWindow.querySelector('.composer');
    const button = coreWindow.querySelector('[data-voice-toggle]');
    if (!button || !composer) return;
    composer.classList.toggle('voice-active', active);
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
    button.setAttribute('aria-label', active ? 'Leave voice input mode' : 'Switch to voice input mode');
    buildState.textContent = active ? 'V2 - Voice input mode' : 'V2 - Agent mode';
  }

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    }[char]));
  }

  function slug(value) {
    return String(value || '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/(^-|-$)/g, '');
  }

  function renderSettingsControl(row) {
    const [title, , , level, type, value] = row;
    if (type === 'switch') {
      return `<button class="settings-switch ${value === 'on' ? 'on' : ''}" type="button" aria-label="${esc(title)} toggle"></button>`;
    }
    if (type === 'planning-mcp') {
      return `
        <div class="settings-planning-mcp" aria-label="Planning MCP tool management">
          <div class="settings-planning-mcp-top">
            <span><strong>Server</strong><small>${esc(planningMcpRoadmap.path)}</small></span>
            <button class="settings-switch ${value === 'on' ? 'on' : ''}" type="button" aria-label="Planning MCP toggle"></button>
          </div>
          <div class="settings-planning-tools">
            ${planningMcpRoadmap.tools.map(tool => `
              <button class="settings-planning-tool" type="button" style="--tool-state:${tool.state === 'read-only' ? 'var(--green)' : tool.state === 'dry-run' ? 'var(--blue)' : 'var(--red)'}">
                <span></span>
                <strong>${esc(tool.name)}</strong>
                <small>${esc(tool.state)}</small>
              </button>
            `).join('')}
          </div>
        </div>
      `;
    }
    if (type === 'select') {
      return `<select class="settings-select-shell" aria-label="${esc(title)}"><option>${esc(value)}</option><option>Use recommended</option><option>Ask first</option></select>`;
    }
    if (type === 'input') {
      return `<input class="settings-input-shell" value="${esc(value)}" aria-label="${esc(title)}">`;
    }
    if (type === 'secret') {
      return `<button class="settings-tiny-button" type="button">${esc(value)}</button><span class="settings-inline-tags"><span class="secret">secret-safe</span></span>`;
    }
    if (type === 'stack') {
      return `<div class="settings-status-stack">${String(value).split(';').map(item => {
        const [name, status, tone] = item.split('|');
        const color = tone === 'green' ? 'var(--green)' : tone === 'red' ? 'var(--red)' : tone === 'amber' ? 'var(--blue)' : 'var(--teal)';
        return `<div class="settings-status-row" style="--state:${color}"><strong>${esc(name)}</strong><span>${esc(status)}</span></div>`;
      }).join('')}</div>`;
    }
    return `<button class="settings-tiny-button" type="button">${esc(value || 'Open')}</button>${level === 'advanced' ? '<button class="settings-tiny-button" type="button">Explain</button>' : ''}`;
  }

  function renderSettingsCatalog() {
    return settingsCatalog.map(section => `
      <section class="settings-section-v2" style="--section:${section.color}" data-settings-section="${esc(section.name)}">
        <header class="settings-section-head">
          <h2>${esc(section.name)}</h2>
          <span>legacy: ${esc(section.legacy)}</span>
        </header>
        ${section.rows.map(row => {
          const [title, desc, tags, level, type] = row;
          const id = `setting-${slug(section.name)}-${slug(title)}`;
          const allTags = [...tags, section.name, section.legacy, level].join(' ');
          return `
            <article class="settings-row-v2" id="${id}" data-settings-row data-title="${esc(title)}" data-group="${esc(section.name)}" data-tags="${esc(allTags)}" data-level="${esc(level)}" data-control="${esc(type)}">
              <div class="settings-copy-v2">
                <strong>${esc(title)}</strong>
                <p>${esc(desc)}</p>
                <div class="settings-tags-v2">
                  ${tags.slice(0, 4).map(tag => `<span>${esc(tag)}</span>`).join('')}
                  ${level === 'advanced' ? '<span class="risk">advanced</span>' : ''}
                </div>
              </div>
              <div class="settings-control-v2">${renderSettingsControl(row)}</div>
            </article>
          `;
        }).join('')}
      </section>
    `).join('');
  }

  function settingsRows(win) {
    return Array.from(win.querySelectorAll('[data-settings-row]'));
  }

  function settingsRowText(row) {
    return [row.dataset.title, row.dataset.group, row.dataset.tags, row.textContent]
      .join(' ')
      .toLowerCase();
  }

  function visibleSettingsRows(win) {
    const query = settingsViewState.query.trim().toLowerCase();
    return settingsRows(win).filter(row => {
      const levelOk = settingsViewState.advanced || row.dataset.level !== 'advanced';
      const queryOk = !query || settingsRowText(row).includes(query);
      return levelOk && queryOk;
    });
  }

  function focusSettingsRow(win, row) {
    settingsRows(win).forEach(item => item.classList.remove('highlight'));
    row.hidden = false;
    row.closest('.settings-section-v2').hidden = false;
    row.classList.add('highlight');
    row.scrollIntoView({ block: 'center', behavior: 'smooth' });
    const title = win.querySelector('[data-settings-title]');
    const subtitle = win.querySelector('[data-settings-subtitle]');
    if (title) title.textContent = row.dataset.title;
    if (subtitle) subtitle.textContent = row.querySelector('p')?.textContent || 'Selected setting';
    setTimeout(() => row.classList.remove('highlight'), 1400);
  }

  function refreshSettingsWindow(win) {
    const queryInput = win.querySelector('[data-settings-search]');
    const resultList = win.querySelector('[data-settings-results]');
    const scroll = win.querySelector('[data-settings-scroll]');
    const title = win.querySelector('[data-settings-title]');
    const subtitle = win.querySelector('[data-settings-subtitle]');
    const normalButton = win.querySelector('[data-settings-mode="normal"]');
    const advancedButton = win.querySelector('[data-settings-mode="advanced"]');
    const rows = settingsRows(win);
    const found = visibleSettingsRows(win);
    const query = settingsViewState.query.trim();
    const colors = Object.fromEntries(settingsCatalog.map(section => [section.name, section.color]));

    if (queryInput && queryInput.value !== settingsViewState.query) {
      queryInput.value = settingsViewState.query;
    }
    normalButton?.classList.toggle('active', !settingsViewState.advanced);
    advancedButton?.classList.toggle('active', settingsViewState.advanced);

    rows.forEach(row => {
      row.hidden = !found.includes(row);
    });
    win.querySelectorAll('.settings-section-v2').forEach(section => {
      const hasVisible = Array.from(section.querySelectorAll('[data-settings-row]')).some(row => !row.hidden);
      section.hidden = !hasVisible;
    });
    scroll?.classList.toggle('no-results', found.length === 0);

    if (title) title.textContent = query ? `Search: ${query}` : (settingsViewState.advanced ? 'All settings, advanced' : 'All settings');
    if (subtitle) {
      subtitle.textContent = query
        ? `${found.length} matching settings. Search includes legacy names, new V2 names, admin areas, and plain-language aliases.`
        : (settingsViewState.advanced
          ? 'Advanced mode includes admin, host, secret handoff, plugins, tokens, MCP, feature flags, logs, and destructive actions.'
          : 'Normal mode keeps user-facing controls visible and hides admin-grade controls until needed.');
    }

    if (resultList) {
      const grouped = found.reduce((acc, row) => {
        const group = row.dataset.group || 'Settings';
        acc[group] = acc[group] || [];
        acc[group].push(row);
        return acc;
      }, {});
      resultList.innerHTML = Object.keys(grouped).map(group => {
        const items = grouped[group].map(row => `
          <button class="settings-result-item" style="--item:${colors[group] || 'var(--cyan)'}" type="button" data-settings-target="${row.id}">
            <strong>${esc(row.dataset.title)}</strong>
            <span>${esc(row.dataset.level)} - ${esc(row.dataset.tags.split(' ').slice(0, 2).join(' '))}</span>
          </button>
        `).join('');
        return `<div class="settings-result-group">${esc(group)}</div>${items}`;
      }).join('');
    }
  }

  function renderSettingsWindow() {
    return `
      <div class="settings-window-layout-v2">
        <aside class="settings-rail-v2" aria-label="Settings search and results">
          <div class="settings-search-wrap">
            <label class="settings-search-v2">
              <input data-settings-search type="search" placeholder="Search settings, e.g. model, mount, voice, backup" autocomplete="off">
            </label>
          </div>
          <div class="settings-quick-status" aria-label="System status">
            <div class="settings-status-tile" style="--tile:var(--green)"><strong>Local</strong>ready</div>
            <div class="settings-status-tile" style="--tile:var(--amber)"><strong>GPU</strong>busy</div>
            <div class="settings-status-tile" style="--tile:var(--teal)"><strong>API</strong>online</div>
          </div>
          <div class="settings-result-list" data-settings-results aria-label="Search results"></div>
          <div class="settings-rail-footer">Normal shows user-facing settings. Advanced reveals admin, secret handoff, host, and developer controls.</div>
        </aside>
        <section class="settings-content-v2">
          <header class="settings-content-head">
            <div class="settings-content-title">
              <h1 data-settings-title>All settings</h1>
              <p data-settings-subtitle>A complete searchable map of the legacy settings menu plus the new V2 shell.</p>
            </div>
            <div class="settings-mode-v2" aria-label="Settings detail mode">
              <button class="active" type="button" data-settings-mode="normal">Normal</button>
              <button type="button" data-settings-mode="advanced">Advanced</button>
            </div>
          </header>
          <div class="settings-scroll-v2" data-settings-scroll>
            <div class="settings-empty-v2">No matching setting. Try "model", "endpoint", "email", "mount", "MCP", "shortcut", "backup", or "privacy".</div>
            ${renderSettingsCatalog()}
          </div>
        </section>
      </div>
    `;
  }

  function wireSettingsWindow(win) {
    win.querySelector('[data-settings-search]')?.addEventListener('input', event => {
      settingsViewState.query = event.target.value.trim();
      refreshSettingsWindow(win);
      win.querySelector('[data-settings-search]')?.focus();
    });
    win.querySelector('[data-settings-mode="normal"]')?.addEventListener('click', () => {
      settingsViewState.advanced = false;
      refreshSettingsWindow(win);
    });
    win.querySelector('[data-settings-mode="advanced"]')?.addEventListener('click', () => {
      settingsViewState.advanced = true;
      refreshSettingsWindow(win);
    });
    win.querySelector('[data-settings-results]')?.addEventListener('click', event => {
      const button = event.target.closest('[data-settings-target]');
      if (!button) return;
      const row = win.querySelector('#' + CSS.escape(button.dataset.settingsTarget));
      if (row) focusSettingsRow(win, row);
    });
    win.addEventListener('click', event => {
      const toggle = event.target.closest('.settings-switch');
      if (toggle) toggle.classList.toggle('on');
    });
  }

  function openSettingsWindow(options = {}) {
    const query = options.query ?? settingsViewState.query;
    settingsViewState.query = query || '';
    settingsViewState.advanced = options.advanced ?? settingsViewState.advanced;
    let win = document.getElementById('settings-v2-window');
    if (!win) {
      win = document.createElement('article');
      win.className = 'floating-window settings-v2-window active';
      win.id = 'settings-v2-window';
      win.dataset.window = '';
      win.dataset.windowId = 'settings';
      win.dataset.windowCloseMode = 'remove';
      win.setAttribute('aria-label', 'Settings');
      win.innerHTML = `
        <header class="window-head" data-drag-handle>
          <div class="window-subtitle">system</div>
          <div class="window-title">Settings</div>
          <div class="window-actions" aria-label="Window controls">
            <button class="window-control" data-window-min title="Minimize" aria-label="Minimize">-</button>
            <button class="window-control" data-window-max title="Maximize" aria-label="Maximize">&#9633;</button>
            <button class="window-control" data-window-close title="Close" aria-label="Close">x</button>
          </div>
        </header>
        <div class="window-body">${renderSettingsWindow()}</div>
      `;
      activeWorkspaceScreen().appendChild(win);
      prepareFloatingWindow(win);
      wireSettingsWindow(win);
    } else {
      setWindowMinimized(win, false);
    }
    refreshSettingsWindow(win);
    activateWindow(win);
    buildState.textContent = 'V2 - Settings';
    closeToolwheel();
  }

  function statusChipClass(label) {
    const normalized = String(label || '').toLowerCase();
    if (normalized.includes('ready') || normalized.includes('green')) return ' ready';
    if (normalized.includes('wait') || normalized.includes('held')) return ' waiting';
    if (normalized.includes('block') || normalized.includes('missing')) return ' blocked';
    return '';
  }

  function todoPriority(todo) {
    if (todo.priority) return todo.priority;
    const type = String(todo.type || '').toLowerCase();
    if (type.includes('blocked')) return 'P0';
    if (type.includes('decision') || type.includes('review')) return 'P1';
    if (type.includes('commit') || type.includes('question')) return 'P2';
    return 'P3';
  }

  function todoStatus(todo) {
    if (todo.done) return 'Done';
    const type = String(todo.type || '').toLowerCase();
    if (type.includes('blocked')) return 'Blocked';
    if (type.includes('decision') || type.includes('question')) return 'Waiting';
    return 'Active';
  }

  function todoUpdatedValue(todo) {
    const value = String(todo.updated || todo.source || '').toLowerCase();
    if (value.includes('now')) return 0;
    const match = value.match(/(\d+)/);
    const number = match ? Number(match[1]) : 99;
    if (value.includes('m')) return number;
    if (value.includes('h')) return number * 60;
    if (value.includes('d')) return number * 1440;
    return number + 1000;
  }

  function allTodos() {
    const projectTodos = projectSamples.flatMap((project, projectIndex) => (project.todos || []).map((todo, todoIndex) => ({
      ...todo,
      id: 'project-' + projectIndex + '-' + todoIndex,
      project: project.title,
      projectIndex,
      todoIndex,
      priority: todoPriority(todo),
      updated: todo.source?.match(/\d+[mhdw]|now/)?.[0] || 'now',
      isProjectTodo: true
    })));
    return [...projectTodos, ...globalTodoExtras].map(todo => ({
      ...todo,
      status: todoStatus(todo),
      priority: todoPriority(todo)
    }));
  }

  function visibleTodos() {
    const query = todosViewState.query.toLowerCase();
    const statusRank = { Blocked: 0, Waiting: 1, Active: 2, Done: 3 };
    const priorityRank = { P0: 0, P1: 1, P2: 2, P3: 3 };
    const items = allTodos().filter(todo => {
      const matchesFilter = todosViewState.filter === 'All' || todo.status === todosViewState.filter;
      const haystack = [todo.title, todo.project, todo.type, todo.detail, todo.source].join(' ').toLowerCase();
      return matchesFilter && (!query || haystack.includes(query));
    });

    return items.sort((a, b) => {
      if (todosViewState.sort === 'Project') {
        return a.project.localeCompare(b.project)
          || (priorityRank[a.priority] ?? 9) - (priorityRank[b.priority] ?? 9)
          || a.title.localeCompare(b.title);
      }
      if (todosViewState.sort === 'Updated') return todoUpdatedValue(a) - todoUpdatedValue(b);
      if (todosViewState.sort === 'Status') {
        return (statusRank[a.status] ?? 9) - (statusRank[b.status] ?? 9)
          || (priorityRank[a.priority] ?? 9) - (priorityRank[b.priority] ?? 9);
      }
      return (priorityRank[a.priority] ?? 9) - (priorityRank[b.priority] ?? 9)
        || a.project.localeCompare(b.project);
    });
  }

  function renderTodoFilters() {
    const filters = ['All', 'Active', 'Waiting', 'Blocked', 'Done'];
    const todos = allTodos();
    return filters.map(filter => {
      const count = filter === 'All' ? todos.length : todos.filter(todo => todo.status === filter).length;
      return `<button class="todos-filter${todosViewState.filter === filter ? ' active' : ''}" type="button" data-todos-filter="${filter}"><span>${filter}</span><span>${count}</span></button>`;
    }).join('');
  }

  function renderTodosList(items) {
    if (!items.length) {
      return '<div class="todos-empty">No matching todos.</div>';
    }

    let lastProject = '';
    return items.map((todo, index) => {
      const group = todosViewState.sort === 'Project' && todo.project !== lastProject
        ? `<div class="todos-project-group">${esc(todo.project)}</div>`
        : '';
      lastProject = todo.project;
      return group + `
        <button class="global-todo-row${todo.id === todosViewState.selectedId ? ' active' : ''}" style="--todo:${todo.color || 'var(--cyan)'}" type="button" data-global-todo-id="${esc(todo.id)}">
          <span class="global-todo-dot"></span>
          <span class="global-todo-copy">
            <span class="global-todo-title-line"><strong>${esc(todo.title)}</strong><span>${esc(todo.status)}</span></span>
            <span class="global-todo-meta"><span>${esc(todo.project)}</span><span>${esc(todo.source)}</span><span>${esc(todo.priority)}</span></span>
          </span>
        </button>
      `;
    }).join('');
  }

  function renderTodoDetail(todo) {
    if (!todo) {
      return '<div class="todos-detail-empty">Select a todo.</div>';
    }
    return `
      <div class="todos-detail-head">
        <div class="todos-detail-kicker"><span>${esc(todo.project)}</span><span>${esc(todo.status)}</span></div>
        <h2>${esc(todo.title)}</h2>
        <p>${esc(todo.detail)}</p>
      </div>
      <div class="todos-detail-body">
        <div class="todos-props">
          <div><span>Priority</span><strong>${esc(todo.priority)}</strong></div>
          <div><span>Source</span><strong>${esc(todo.source)}</strong></div>
          <div><span>Project</span><strong>${esc(todo.project)}</strong></div>
          <div><span>Updated</span><strong>${esc(todo.updated || 'now')}</strong></div>
        </div>
      </div>
      <div class="todos-detail-actions">
        <button type="button" data-todos-focus-source>Open Source</button>
        <button class="primary" type="button" data-todos-toggle-done>${todo.done ? 'Reopen' : 'Mark Done'}</button>
      </div>
    `;
  }

  function renderTodosWindow() {
    const items = visibleTodos();
    if (!todosViewState.selectedId || !items.some(todo => todo.id === todosViewState.selectedId)) {
      todosViewState.selectedId = items[0]?.id || null;
    }
    const selected = allTodos().find(todo => todo.id === todosViewState.selectedId);
    return `
      <div class="todos-window-layout">
        <section class="todos-main" aria-label="Todos">
          <div class="todos-toolbar">
            <div class="todos-toolbar-top">
              <label class="todos-search">
                <input type="search" value="${esc(todosViewState.query)}" placeholder="find todos, projects, blockers" data-todos-search>
              </label>
              <button class="todos-sort" type="button" data-todos-sort>Sort: ${esc(todosViewState.sort)}</button>
              <button class="todos-new" type="button" data-todos-new>New Todo</button>
            </div>
            <div class="todos-filters">${renderTodoFilters()}</div>
          </div>
          <div class="global-todo-list">${renderTodosList(items)}</div>
        </section>
        <aside class="todos-detail" aria-label="Todo details">${renderTodoDetail(selected)}</aside>
      </div>
    `;
  }

  function refreshTodosWindow(win) {
    const body = win.querySelector('.window-body');
    body.innerHTML = renderTodosWindow();
    wireTodosWindow(win);
  }

  function toggleTodoDone(todo) {
    if (!todo) return;
    if (todo.isProjectTodo) {
      const source = projectSamples[todo.projectIndex]?.todos?.[todo.todoIndex];
      if (source) source.done = !source.done;
      return;
    }
    const extra = globalTodoExtras.find(item => item.id === todo.id);
    if (extra) extra.done = !extra.done;
  }

  function wireTodosWindow(win) {
    win.querySelector('[data-todos-search]')?.addEventListener('input', event => {
      todosViewState.query = event.target.value.trim();
      refreshTodosWindow(win);
      win.querySelector('[data-todos-search]')?.focus();
    });
    win.querySelector('[data-todos-sort]')?.addEventListener('click', () => {
      const modes = ['Project', 'Priority', 'Updated', 'Status'];
      const next = (modes.indexOf(todosViewState.sort) + 1) % modes.length;
      todosViewState.sort = modes[next];
      refreshTodosWindow(win);
    });
    win.querySelectorAll('[data-todos-filter]').forEach(button => {
      button.addEventListener('click', () => {
        todosViewState.filter = button.dataset.todosFilter || 'All';
        todosViewState.selectedId = null;
        refreshTodosWindow(win);
      });
    });
    win.querySelectorAll('[data-global-todo-id]').forEach(button => {
      button.addEventListener('click', () => {
        todosViewState.selectedId = button.dataset.globalTodoId;
        refreshTodosWindow(win);
      });
    });
    win.querySelector('[data-todos-toggle-done]')?.addEventListener('click', () => {
      toggleTodoDone(allTodos().find(todo => todo.id === todosViewState.selectedId));
      refreshTodosWindow(win);
    });
    win.querySelector('[data-todos-focus-source]')?.addEventListener('click', () => {
      const selected = allTodos().find(todo => todo.id === todosViewState.selectedId);
      if (selected?.isProjectTodo) {
        openProjectsOverview(selected.projectIndex);
      }
      buildState.textContent = 'V2 - Todo source focused';
    });
    win.querySelector('[data-todos-new]')?.addEventListener('click', () => {
      globalTodoExtras.unshift({
        id: 'global-manual-' + Date.now(),
        project: 'Inbox',
        type: 'Manual',
        title: 'Untitled todo',
        detail: 'New manual todo placeholder.',
        source: 'manual',
        color: 'var(--cyan)',
        priority: 'P3',
        updated: 'now',
        done: false
      });
      todosViewState.filter = 'All';
      todosViewState.sort = 'Project';
      todosViewState.selectedId = globalTodoExtras[0].id;
      refreshTodosWindow(win);
    });
  }

  function skillHealthClass(skill) {
    if (skill.health === 'review') return 'review';
    if (skill.health === 'draft') return 'draft';
    if (skill.health === 'blocked') return 'blocked';
    return 'ready';
  }

  function visibleSkills() {
    const query = skillsViewState.query.toLowerCase();
    return skillSamples.filter(skill => {
      const matchesFilter = skillsViewState.filter === 'all'
        || skill.category === skillsViewState.filter
        || (skillsViewState.filter === 'review' && skill.health !== 'ready');
      const haystack = [
        skill.name,
        skill.category,
        skill.summary,
        skill.purpose,
        skill.used,
        skill.rules,
        skill.healthLabel,
        skill.trust,
        skill.healthReason,
        skill.trustReason,
        skill.reviewAction
      ].join(' ').toLowerCase();
      return matchesFilter && (!query || haystack.includes(query));
    });
  }

  function renderSkillFilters() {
    const filters = [
      ['all', 'All'],
      ['cloud', 'Cloud'],
      ['memory', 'Memory'],
      ['projects', 'Projects'],
      ['code', 'Code'],
      ['automation', 'Automation'],
      ['review', 'Needs review']
    ];
    return filters.map(([id, label]) => `
      <button class="skills-filter${skillsViewState.filter === id ? ' active' : ''}${id === 'review' ? ' review' : ''}" type="button" data-skills-filter="${esc(id)}">${esc(label)}</button>
    `).join('');
  }

  function renderSkillsList(items) {
    if (!items.length) {
      return '<div class="skills-empty">No matching skills.</div>';
    }
    return items.map(skill => `
      <button class="skills-row${skill.id === skillsViewState.selectedId ? ' active' : ''}" type="button" data-skill-id="${esc(skill.id)}">
        <span class="skills-health-dot ${esc(skillHealthClass(skill))}" aria-hidden="true"></span>
        <span class="skills-row-copy">
          <strong>${esc(skill.name)}</strong>
          <span>${esc(skill.summary)}</span>
        </span>
        <span class="skills-state-pill">${esc(skill.healthLabel)}</span>
      </button>
    `).join('');
  }

  function renderSkillDetail(skill) {
    if (!skill) {
      return '<div class="skills-detail-empty">Select a skill.</div>';
    }
    return `
      <div class="skills-detail-head">
        <span>${esc(skill.category)}</span>
        <h2>${esc(skill.name)}</h2>
      </div>
      <section class="skills-health-panel" aria-label="Skill health">
        <article class="skills-health-card ${esc(skillHealthClass(skill))}">
          <span>Skill Health</span>
          <strong>${esc(skill.healthLabel)}</strong>
          <p>${esc(skill.healthReason)}</p>
        </article>
        <article class="skills-health-card ${esc(skillHealthClass(skill))}">
          <span>Trust</span>
          <strong>${esc(skill.trust)}</strong>
          <p>${esc(skill.trustReason)}</p>
        </article>
        <article class="skills-health-card">
          <span>Last checked</span>
          <strong>${esc(skill.checked)}</strong>
          <p>${esc(skill.reviewAction)}</p>
        </article>
      </section>
      <section class="skills-detail-grid">
        <article class="skills-section wide">
          <h3>Purpose</h3>
          <p>${esc(skill.purpose)}</p>
        </article>
        <article class="skills-section">
          <h3>Used when</h3>
          <p>${esc(skill.used)}</p>
        </article>
        <article class="skills-section">
          <h3>Allowed</h3>
          <ul>${skill.allowed.map(item => `<li>${esc(item)}</li>`).join('')}</ul>
        </article>
        <article class="skills-section">
          <h3>Never</h3>
          <ul class="never">${skill.never.map(item => `<li>${esc(item)}</li>`).join('')}</ul>
        </article>
        <article class="skills-section">
          <h3>Recent activity</h3>
          <p>${esc(skill.activity)}</p>
        </article>
        <article class="skills-section wide">
          <h3>Rules</h3>
          <p>${esc(skill.rules)}</p>
        </article>
      </section>
      <div class="skills-actions">
        <button class="primary" type="button" data-skill-review>Run skill review</button>
        <button type="button" data-skill-edit-rules>Edit skill rules</button>
        <button type="button" data-skill-open-activity>Open activity</button>
        <button type="button" data-skill-disable>Disable skill</button>
      </div>
    `;
  }

  function renderSkillsWindow() {
    const items = visibleSkills();
    if (!skillsViewState.selectedId || !skillSamples.some(skill => skill.id === skillsViewState.selectedId)) {
      skillsViewState.selectedId = items[0]?.id || skillSamples[0]?.id || null;
    }
    if (items.length && !items.some(skill => skill.id === skillsViewState.selectedId)) {
      skillsViewState.selectedId = items[0].id;
    }
    const selected = skillSamples.find(skill => skill.id === skillsViewState.selectedId);
    const needsReview = skillSamples.filter(skill => skill.health !== 'ready').length;
    return `
      <div class="skills-window-layout">
        <div class="skills-toolbar">
          <div class="skills-toolbar-summary"><span class="model-status-dot ready" aria-hidden="true"></span>${skillSamples.length} skills · ${needsReview} need review</div>
          <nav class="skills-filters" aria-label="Skill filters">${renderSkillFilters()}</nav>
          <label class="skills-search">
            <input type="search" value="${esc(skillsViewState.query)}" placeholder="Search skills, actions, rules, tools" data-skills-search>
          </label>
          <span class="skills-rules-chip">Rules editable here</span>
        </div>
        <div class="skills-content">
          <aside class="skills-list" aria-label="Skills">${renderSkillsList(items)}</aside>
          <section class="skills-detail" aria-label="Skill details">${renderSkillDetail(selected)}</section>
        </div>
      </div>
    `;
  }

  function refreshSkillsWindow(win) {
    win.querySelector('.window-body').innerHTML = renderSkillsWindow();
    wireSkillsWindow(win);
  }

  function wireSkillsWindow(win) {
    win.querySelector('[data-skills-search]')?.addEventListener('input', event => {
      skillsViewState.query = event.target.value.trim();
      refreshSkillsWindow(win);
      win.querySelector('[data-skills-search]')?.focus();
    });
    win.querySelectorAll('[data-skills-filter]').forEach(button => {
      button.addEventListener('click', () => {
        skillsViewState.filter = button.dataset.skillsFilter || 'all';
        refreshSkillsWindow(win);
      });
    });
    win.querySelectorAll('[data-skill-id]').forEach(button => {
      button.addEventListener('click', () => {
        skillsViewState.selectedId = button.dataset.skillId;
        refreshSkillsWindow(win);
      });
    });
    win.querySelector('[data-skill-review]')?.addEventListener('click', () => {
      const skill = skillSamples.find(item => item.id === skillsViewState.selectedId);
      buildState.textContent = 'V2 - Skill review queued for ' + (skill?.name || 'selected skill');
    });
    win.querySelector('[data-skill-edit-rules]')?.addEventListener('click', () => {
      const skill = skillSamples.find(item => item.id === skillsViewState.selectedId);
      buildState.textContent = 'V2 - Editing rules for ' + (skill?.name || 'selected skill');
    });
    win.querySelector('[data-skill-open-activity]')?.addEventListener('click', () => {
      const skill = skillSamples.find(item => item.id === skillsViewState.selectedId);
      buildState.textContent = 'V2 - Activity opened for ' + (skill?.name || 'selected skill');
    });
    win.querySelector('[data-skill-disable]')?.addEventListener('click', () => {
      const skill = skillSamples.find(item => item.id === skillsViewState.selectedId);
      buildState.textContent = 'V2 - Disable skill placeholder: ' + (skill?.name || 'selected skill');
    });
  }

  function openSkillsWindow(options = {}) {
    if (options.skillId && skillSamples.some(skill => skill.id === options.skillId)) {
      skillsViewState.selectedId = options.skillId;
    }
    if (options.filter) skillsViewState.filter = options.filter;
    let win = document.getElementById('skills-window');
    if (!win) {
      win = document.createElement('article');
      win.className = 'floating-window skills-window active';
      win.id = 'skills-window';
      win.dataset.window = '';
      win.dataset.windowId = 'skills';
      win.dataset.windowCloseMode = 'remove';
      win.setAttribute('aria-label', 'Skills');
      win.innerHTML = `
        <header class="window-head" data-drag-handle>
          <div class="window-subtitle">automation</div>
          <div class="window-title">Skills</div>
          <div class="window-actions" aria-label="Window controls">
            <button class="window-control" data-window-min title="Minimize" aria-label="Minimize">-</button>
            <button class="window-control" data-window-max title="Maximize" aria-label="Maximize">&#9633;</button>
            <button class="window-control" data-window-close title="Close" aria-label="Close">x</button>
          </div>
        </header>
        <div class="window-body">${renderSkillsWindow()}</div>
      `;
      activeWorkspaceScreen().appendChild(win);
      prepareFloatingWindow(win);
      installResizeHandles(win);
      wireSkillsWindow(win);
    } else {
      setWindowMinimized(win, false);
      win.querySelector('.window-body').innerHTML = renderSkillsWindow();
      wireSkillsWindow(win);
    }
    activateWindow(win);
    buildState.textContent = 'V2 - Skills';
    closeToolwheel();
  }

  function gateIconSvg(state = 'open') {
    const closed = state === 'blocked';
    return `
      <svg class="gate-icon" viewBox="0 0 32 32" aria-hidden="true">
        <path d="M7 27V15.5C7 10.25 11.03 6 16 6s9 4.25 9 9.5V27" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"></path>
        <path d="M10 27h12" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"></path>
        ${closed ? '<path d="M11 16h10v11H11z" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"></path><path d="M16 20v3" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"></path>' : '<path d="M12 27V16h8v11" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"></path>'}
      </svg>
    `;
  }

  function renderPlanningProjects(activeIndex = 0) {
    return `
      <div class="planning-projects-body">
        <div class="planning-project-tools">
          <label class="planning-project-search">
            <span>⌕</span>
            <input type="search" placeholder="Find project, roadmap, gate" data-project-search>
          </label>
          <button class="planning-project-add" type="button" data-open-new-roadmap title="New roadmap" aria-label="New roadmap">+</button>
        </div>
        <div class="planning-project-list" data-planning-project-list>
          ${projectSamples.map((project, index) => {
            const chips = projectStatusChips(project, index, activeIndex);
            const progress = projectProgressPercent(project);
            return `
            <button class="planning-project-row${index === activeIndex ? ' active' : ''}" style="--state:${project.statusColor}" type="button" data-project-index="${index}">
              <span class="project-card-top">
                <span class="project-copy"><strong>${esc(project.title)}</strong><span>${esc(project.subtitle)}</span></span>
                <span class="project-count"><span>${esc(project.progress)}</span>${index === activeIndex ? '<span class="bubble">1</span>' : ''}</span>
              </span>
              <span class="project-chip-row">
                ${chips.map(chip => `<span class="chip ${esc(chip.className)}">${esc(chip.label)}</span>`).join('')}
              </span>
              <span class="project-progress-line"><span style="width:${progress}%;"></span></span>
            </button>
          `;
          }).join('')}
        </div>
      </div>
    `;
  }

  function renderRoadmapGate(gate) {
    return `
      <button class="roadmap-gate ${esc(gate.state)}" style="left:${gate.x / 10}%; top:${gate.y / 6.2}%;" type="button" data-gate-target="${esc(gate.id)}" data-gate-tooltip="${esc(gate.id + ': ' + gate.label)}" aria-label="${esc(gate.id)}">
        ${gateIconSvg(gate.state)}
      </button>
    `;
  }

  function roadmapNodeStateClass(state = '') {
    const normalized = String(state).toLowerCase();
    if (normalized.includes('blocked')) return 'blocked';
    if (normalized.includes('done') || normalized.includes('passed') || normalized.includes('verified')) return 'done';
    if (normalized.includes('work') || normalized.includes('active') || normalized.includes('draft') || normalized.includes('progress')) return 'working';
    return 'future';
  }

  function roadmapGateStateLabel(state = '') {
    const stateClass = roadmapNodeStateClass(state);
    if (stateClass === 'done') return 'Passed';
    if (stateClass === 'blocked') return 'Blocked';
    if (stateClass === 'working') return 'Checking';
    return 'Open';
  }

  function roadmapNodeById(id) {
    return planningRoadmapDemo.nodes.find(node => node.id === id);
  }

  function roadmapSummaryForNode(node) {
    if (node.kind === 'gate') return node.label || node.title || node.id;
    if (node.kind === 'version') {
      if (node.id === planningRoadmapDemo.fromVersion) return 'Starting point for this project slice. The visible planning history begins here.';
      if (node.id === planningRoadmapDemo.toVersion) return 'Next target version. Everything to the left prepares this release.';
      return 'Version marker in the planning timeline.';
    }
    const row = (planningRoadmapDemo.rows || []).find(item => item[0] === node.id);
    return row?.[2] || node.title || node.id;
  }

  function roadmapNodeLabel(node) {
    if (node.kind === 'version') return node.id;
    return node.id;
  }

  function roadmapNodeDimensions(node) {
    if (!node) return { width: 0, height: 0 };
    if (node.kind === 'gate') return { width: 34, height: 34 };
    if (node.kind === 'version') return { width: 84, height: 36 };
    return { width: 78, height: 36 };
  }

  function projectStatusChips(project, index, activeIndex) {
    const chips = [];
    if (index === activeIndex) chips.push({ label: 'active', className: 'warn' });
    if (project.status) chips.push({ label: project.status, className: roadmapNodeStateClass(project.status) === 'done' ? 'good' : roadmapNodeStateClass(project.status) === 'blocked' ? 'bad' : 'warn' });
    if (project.progress) chips.push({ label: project.progress, className: '' });
    (project.chips || []).slice(0, 2).forEach(label => chips.push({ label, className: '' }));
    return chips.slice(0, 4);
  }

  function projectProgressPercent(project) {
    const match = String(project.progress || '').match(/(\d+)\s*\/\s*(\d+)/);
    if (!match) return 48;
    const done = Number(match[1]);
    const total = Math.max(1, Number(match[2]));
    return Math.max(8, Math.min(100, Math.round(done / total * 100)));
  }

  function roadmapListItems() {
    const rowMap = new Map((planningRoadmapDemo.rows || []).map(row => [row[0], row]));
    const sequenceLabels = roadmapSequenceLabels();
    const sequenceValue = label => String(label || '0')
      .split('.')
      .reduce((total, part, index) => total + (Number(part) || 0) / Math.pow(100, index), 0);
    return (planningRoadmapDemo.nodes || [])
      .filter(node => node.kind === 'roadmap')
      .map(node => {
        const row = rowMap.get(node.id);
        const sequence = sequenceLabels.get(node.id) || '0';
        return {
          id: node.id,
          sequence,
          title: row?.[1] || node.title || node.id,
          summary: row?.[2] || roadmapSummaryForNode(node),
          state: roadmapNodeStateClass(row?.[3] || node.state),
          rawState: row?.[3] || node.state || 'open'
        };
      })
      .sort((a, b) => sequenceValue(a.sequence) - sequenceValue(b.sequence));
  }

  function roadmapDocumentForId(id) {
    const node = roadmapNodeById(id);
    if (!node || node.kind !== 'roadmap') return null;
    const item = roadmapListItems().find(entry => entry.id === id);
    const path = 'projects/' + (projectSamples[activePlanningProjectIndex]?.title || 'project')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '')
      + '/roadmaps/' + id.toLowerCase().replace(/[^a-z0-9]+/g, '-') + '.json';
    const tasks = [
      { state: item?.state === 'done' ? 'done' : 'open', title: 'Clarify target', detail: 'Keep the roadmap goal, scope and exit condition readable.' },
      { state: item?.state === 'blocked' ? 'blocked' : item?.state === 'done' ? 'done' : 'open', title: 'Work through implementation slices', detail: item?.summary || roadmapSummaryForNode(node) },
      { state: item?.state === 'done' ? 'done' : 'open', title: 'Update planning state', detail: 'Write progress, gates and links back into the project roadmap JSON.' }
    ];
    const gates = (planningRoadmapDemo.nodes || [])
      .filter(gate => gate.kind === 'gate' && (gate.affects || []).includes(id))
      .map(gate => ({
        id: gate.id,
        state: roadmapNodeStateClass(gate.state),
        title: gate.title || gate.id,
        label: gate.label || roadmapSummaryForNode(gate)
      }));
    return {
      title: (item?.id || id) + ' - ' + (item?.title || node.title || id),
      roadmapId: id,
      sequence: item?.sequence || id,
      status: item?.state || roadmapNodeStateClass(node.state),
      project: projectSamples[activePlanningProjectIndex]?.title || 'Active project',
      path,
      type: 'roadmap',
      language: 'json',
      summary: item?.summary || roadmapSummaryForNode(node),
      content: JSON.stringify({
        id,
        title: item?.title || node.title || id,
        status: item?.state || roadmapNodeStateClass(node.state),
        sequence: item?.sequence || id,
        project: projectSamples[activePlanningProjectIndex]?.title || 'Active project',
        summary: item?.summary || roadmapSummaryForNode(node),
        tasks,
        gates
      }, null, 2),
      tasks,
      gates
    };
  }

  function openRoadmapDocument(id, options = {}) {
    const doc = roadmapDocumentForId(id);
    if (!doc) return null;
    const win = openDocumentViewer(doc);
    if (options.spark) sparkDocumentToAgent(win, options.context || {});
    return win;
  }

  function roadmapSequenceLabels() {
    const nodes = planningRoadmapDemo.nodes || [];
    const edges = planningRoadmapDemo.edges || [];
    const nodeMap = new Map(nodes.map(node => [node.id, node]));
    const spineY = nodeMap.get(planningRoadmapDemo.fromVersion)?.y
      ?? nodeMap.get(planningRoadmapDemo.toVersion)?.y
      ?? 318;
    const roadmaps = nodes.filter(node => node.kind === 'roadmap');
    const spine = roadmaps
      .filter(node => Math.abs((node.y ?? spineY) - spineY) <= 46)
      .sort((a, b) => (a.x - b.x) || (a.y - b.y));
    const labels = new Map(spine.map((node, index) => [node.id, String(index + 1)]));
    const incoming = new Map();
    edges.forEach(edge => {
      if (!incoming.has(edge.to)) incoming.set(edge.to, []);
      incoming.get(edge.to).push(edge.from);
    });

    const findSpineSource = (id, seen = new Set()) => {
      if (seen.has(id)) return '';
      seen.add(id);
      for (const sourceId of incoming.get(id) || []) {
        if (labels.has(sourceId)) return sourceId;
        const nested = findSpineSource(sourceId, seen);
        if (nested) return nested;
      }
      const node = nodeMap.get(id);
      return spine
        .filter(item => item.x < (node?.x ?? 0))
        .sort((a, b) => b.x - a.x)[0]?.id || spine[0]?.id || '';
    };

    const branchCounts = new Map();
    roadmaps
      .filter(node => !labels.has(node.id))
      .sort((a, b) => (a.x - b.x) || (a.y - b.y))
      .forEach(node => {
        const source = findSpineSource(node.id);
        const parentLabel = labels.get(source) || String(labels.size + 1);
        const next = (branchCounts.get(parentLabel) || 0) + 1;
        branchCounts.set(parentLabel, next);
        labels.set(node.id, `${parentLabel}.${next}`);
      });
    return labels;
  }

  function activeRoadmapId() {
    const working = roadmapListItems().find(item => item.state === 'working');
    if (working) return working.id;
    return roadmapListItems()[0]?.id || '';
  }

  function roadmapGraphModel() {
    const canvas = planningRoadmapDemo.canvas || { width: 1000, height: 620 };
    const nodes = planningRoadmapDemo.nodes || [];
    return {
      canvas,
      nodes,
      nodeMap: new Map(nodes.map(node => [node.id, node]))
    };
  }

  function roadmapAnchor(node, target) {
    const size = roadmapNodeDimensions(node);
    const dx = (target?.x || node.x) - node.x;
    const dy = (target?.y || node.y) - node.y;
    if (Math.abs(dx) >= Math.abs(dy) * 0.78) {
      return {
        x: node.x + (dx >= 0 ? size.width / 2 : -size.width / 2),
        y: node.y
      };
    }
    return {
      x: node.x,
      y: node.y + (dy >= 0 ? size.height / 2 : -size.height / 2)
    };
  }

  function roadmapEdgePath(edge, model) {
    if (edge.curve) return edge.curve;
    const source = model.nodeMap.get(edge.from);
    const target = model.nodeMap.get(edge.to);
    if (!source || !target) return '';
    const start = roadmapAnchor(source, target);
    const end = roadmapAnchor(target, source);
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const horizontalBias = Math.min(190, Math.max(46, Math.abs(dx) * 0.42));
    const verticalBias = Math.min(96, Math.max(28, Math.abs(dy) * 0.34));
    const c1x = start.x + (dx >= 0 ? horizontalBias : -horizontalBias);
    const c2x = end.x - (dx >= 0 ? horizontalBias : -horizontalBias);
    const c1y = start.y + (Math.abs(dy) > 56 ? (dy >= 0 ? verticalBias : -verticalBias) : 0);
    const c2y = end.y - (Math.abs(dy) > 56 ? (dy >= 0 ? verticalBias : -verticalBias) : 0);
    return `M${start.x.toFixed(1)} ${start.y.toFixed(1)} C${c1x.toFixed(1)} ${c1y.toFixed(1)} ${c2x.toFixed(1)} ${c2y.toFixed(1)} ${end.x.toFixed(1)} ${end.y.toFixed(1)}`;
  }

  function renderRoadmapEdge(edge, model) {
    const target = model.nodeMap.get(edge.to);
    const stateClass = roadmapNodeStateClass(edge.targetState || target?.state || 'future');
    const affectedIds = new Set([edge.from, edge.to].filter(Boolean));
    if (target?.kind === 'gate') (target.affects || []).forEach(id => affectedIds.add(id));
    const affected = [...affectedIds].join(',');
    const path = roadmapEdgePath(edge, model);
    if (!path) return '';
    return `
      <path class="roadmap-edge ${esc(stateClass)}${edge.dashed ? ' dashed' : ''}${edge.arrow ? ' arrow' : ''}"
        d="${esc(path)}"
        data-edge-from="${esc(edge.from)}"
        data-edge-to="${esc(edge.to)}"
        data-edge-affects="${esc(affected)}"></path>
    `;
  }

  function renderRoadmapNode(node) {
    const stateClass = roadmapNodeStateClass(node.state);
    const isGate = node.kind === 'gate';
    const isVersion = node.kind === 'version';
    const summary = roadmapSummaryForNode(node);
    const style = `left:${node.x}px; top:${node.y}px;`;
    const affected = isGate ? (node.affects || [node.id]).join(',') : '';
    const gateTooltip = isGate
      ? `<span class="gate-tooltip" role="tooltip"><b>${esc(roadmapGateStateLabel(node.state))}</b><span>${esc(node.label || node.title)}</span></span>`
      : '';
    return `
      <button class="roadmap-node ${isGate ? 'gate-node' : 'roadmap-box'}${isVersion ? ' version-node' : ''} ${esc(stateClass)}"
        style="${style}"
        type="button"
        data-roadmap-node="${esc(node.id)}"
        data-node-kind="${esc(node.kind)}"
        data-gate-affects="${esc(affected)}"
        data-gate-tooltip="${esc(summary)}">
        ${isGate ? gateIconSvg(node.state) : ''}
        ${gateTooltip}
        <strong>${esc(roadmapNodeLabel(node))}</strong>
        <span class="roadmap-node-summary">${esc(summary)}</span>
      </button>
    `;
  }

  function renderRoadmapList(activeIndex = 0) {
    const project = projectSamples[activeIndex] || projectSamples[0];
    const activeId = activeRoadmapId();
    const items = roadmapListItems();
    return `
      <div class="roadmap-list-body" data-active-project="${activeIndex}">
        <label class="roadmap-list-search">
          <span>⌕</span>
          <input type="search" placeholder="Find roadmap, gate, summary" data-roadmap-list-search>
        </label>
        <div class="roadmap-list-filters" aria-label="Roadmap filters">
          <button class="active" type="button">All</button>
          <button type="button">Open</button>
          <button type="button">Blocked</button>
          <button type="button">Gates</button>
        </div>
        <section class="planning-roadmap-list active" data-list-view aria-label="Roadmap list">
          <button class="roadmap-list-row pending" type="button" data-roadmap-row="pending-roadmap" data-roadmap-search-text="AI is creating roadmap project context codebase memory gates links">
            <span class="roadmap-card-top"><span class="roadmap-seq">...</span><span><strong>AI is creating roadmap</strong><small>Researching ${esc(project.title)}, codebase, memory, gates, and links.</small></span><b class="roadmap-id">running</b></span>
          </button>
          ${items.map(item => `
            <button class="roadmap-list-row ${esc(item.state)}${item.id === activeId ? ' active-roadmap' : ''}" style="--row:var(--${item.state === 'done' ? 'green' : item.state === 'working' ? 'amber' : item.state === 'blocked' ? 'red' : 'blue'})" type="button" data-roadmap-row="${esc(item.id)}" data-roadmap-search-text="${esc([item.id, item.title, item.summary, item.rawState].join(' '))}">
              <span class="roadmap-card-top"><span class="roadmap-seq">${esc(item.sequence)}</span><span><strong>${esc(item.title)}</strong><small>${esc(item.summary)}</small></span><b class="roadmap-id">${esc(item.id)}</b></span>
            </button>
          `).join('')}
        </section>
      </div>
    `;
  }

  function renderPlanningOverview(activeIndex = 0) {
    const project = projectSamples[activeIndex] || projectSamples[0];
    const graphModel = roadmapGraphModel();
    const canvas = graphModel.canvas;
    const projectName = project.title === 'Agent Autonomy' ? 'Agent Autonomy' : project.title;
    return `
      <div class="planning-overview-body" data-active-project="${activeIndex}">
        <section class="planning-roadmap-graph" data-graph-view aria-label="Roadmap graph">
          <div class="roadmap-search-shell" data-roadmap-search>
            <button class="roadmap-search-toggle" type="button" data-roadmap-search-toggle aria-label="Search roadmaps">⌕</button>
            <input class="roadmap-search-input" type="search" data-roadmap-search-input placeholder="Search roadmap summaries" aria-label="Search roadmap summaries">
          </div>
          <h3 class="roadmap-project-title">${esc(projectName)}</h3>
          <div class="roadmap-scroll" data-roadmap-scroll>
            <div class="roadmap-canvas" style="--roadmap-width:${canvas.width}px; --roadmap-height:${canvas.height}px;">
              <svg class="roadmap-lines" viewBox="0 0 ${canvas.width} ${canvas.height}" preserveAspectRatio="none" aria-label="Roadmap connections and gates">
                <defs>
                  <marker id="roadmap-arrowhead" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                    <path d="M 0 0 L 10 5 L 0 10 z"></path>
                  </marker>
                </defs>
                ${planningRoadmapDemo.edges.map(edge => renderRoadmapEdge(edge, graphModel)).join('')}
              </svg>
              <span class="history-label" style="left:18px; top:258px;">foundation roadmaps off-canvas</span>
              <span class="spine-caption" style="left:540px; top:342px;">${esc(project.title === 'Agent Autonomy' ? 'JSON master spine' : 'JSON master spine with branches')}</span>
              ${graphModel.nodes.map(node => renderRoadmapNode(node)).join('')}
            </div>
          </div>
        </section>
        <footer class="planning-schema-strip">
          <span class="gate-legend">
            <span style="--legend:var(--green)">${gateIconSvg('done')} done</span>
            <span style="--legend:var(--blue)">${gateIconSvg('open')} open</span>
            <span style="--legend:var(--red)">${gateIconSvg('blocked')} blocked</span>
          </span>
        </footer>
      </div>
    `;
  }

  function renderNewRoadmapWindow(target = 'Roadmap') {
    const activeProject = projectSamples[activePlanningProjectIndex] || projectSamples[0];
    const attachOptions = [
      { label: target && target !== 'Roadmap' ? 'After ' + target : 'Current roadmap', meta: target && target !== 'Roadmap' ? 'selected node' : 'active context' },
      { label: 'New branch from current', meta: 'branch' },
      { label: 'After schema gate', meta: 'gate' },
      { label: 'Next version spine', meta: planningRoadmapDemo.toVersion || 'next' }
    ];
    return `
      <article class="floating-window new-roadmap-window active" data-window data-window-id="new-roadmap" data-window-close-mode="remove" aria-label="New Roadmap">
        <header class="window-head" data-drag-handle>
          <div class="window-subtitle">attach to ${esc(target)}</div>
          <div class="window-title">New Roadmap</div>
          <div class="window-actions" aria-label="Window controls">
            <button class="window-control" data-window-min title="Minimize" aria-label="Minimize">-</button>
            <button class="window-control" data-window-max title="Maximize" aria-label="Maximize">&#9633;</button>
            <button class="window-control" data-window-close title="Close" aria-label="Close">x</button>
          </div>
        </header>
        <div class="window-body new-roadmap-body">
          <div class="new-roadmap-context">
            <div class="roadmap-picker">
              <span>Project</span>
              <button class="roadmap-picker-button" type="button" data-roadmap-picker="project">
                <span><strong>${esc(activeProject.title)}</strong><small>${esc(activeProject.progress)} - ${esc(activeProject.status)}</small></span>
                <em>⌄</em>
              </button>
              <div class="roadmap-picker-menu" hidden data-roadmap-picker-menu="project">
                ${projectSamples.map((project, index) => `
                  <button class="${index === activePlanningProjectIndex ? 'active' : ''}" type="button" data-picker-label="${esc(project.title)}" data-picker-detail="${esc(project.progress + ' - ' + project.status)}">
                    <span>${esc(project.title)}</span><em>${esc(project.progress)}</em>
                  </button>
                `).join('')}
              </div>
            </div>
            <div class="roadmap-picker">
              <span>Attach point</span>
              <button class="roadmap-picker-button" type="button" data-roadmap-picker="attach">
                <span><strong>${esc(attachOptions[0].label)}</strong><small>${esc(attachOptions[0].meta)}</small></span>
                <em>⌄</em>
              </button>
              <div class="roadmap-picker-menu" hidden data-roadmap-picker-menu="attach">
                ${attachOptions.map((option, index) => `
                  <button class="${index === 0 ? 'active' : ''}" type="button" data-picker-label="${esc(option.label)}" data-picker-detail="${esc(option.meta)}">
                    <span>${esc(option.label)}</span><em>${esc(option.meta)}</em>
                  </button>
                `).join('')}
              </div>
            </div>
            <div class="roadmap-picker">
              <span>Kind</span>
              <button class="roadmap-picker-button" type="button" data-roadmap-picker="kind">
                <span><strong>Feature</strong><small>roadmap</small></span>
                <em>⌄</em>
              </button>
              <div class="roadmap-picker-menu" hidden data-roadmap-picker-menu="kind">
                ${[
                  ['Feature', 'plan'],
                  ['Fix', 'repair'],
                  ['Research', 'learn'],
                  ['Release', 'ship']
                ].map((option, index) => `
                  <button class="${index === 0 ? 'active' : ''}" type="button" data-picker-label="${esc(option[0])}" data-picker-detail="${esc(option[1])}">
                    <span>${esc(option[0])}</span><em>${esc(option[1])}</em>
                  </button>
                `).join('')}
              </div>
            </div>
          </div>
          <label class="new-roadmap-prompt-field">
            <span>What should be planned?</span>
            <textarea class="new-roadmap-prompt" placeholder="Example: Turn the Planning MCP workflow into a roadmap. It should let agents create, update, retrieve, and attach project roadmaps through MCP."></textarea>
          </label>
          <div class="new-roadmap-actions">
            <button type="button" data-window-close>Cancel</button>
            <button class="primary" type="button" data-roadmap-generate>Generate roadmap</button>
          </div>
        </div>
      </article>
    `;
  }

  function renderProjectsOverview(activeIndex = 0) {
    return renderPlanningOverview(activeIndex);
  }

  function refreshPlanningWindows(activeIndex = activePlanningProjectIndex) {
    activePlanningProjectIndex = activeIndex;
    persistWorkspaceState();
    const projectsWin = document.getElementById('planning-projects-window');
    const overviewWin = document.getElementById('projects-overview-window');
    const listWin = document.getElementById('roadmap-list-window');
    if (projectsWin) {
      projectsWin.querySelector('.window-body').innerHTML = renderPlanningProjects(activeIndex);
      wirePlanningProjectsWindow(projectsWin);
    }
    if (overviewWin) {
      overviewWin.querySelector('.window-body').innerHTML = renderPlanningOverview(activeIndex);
      overviewWin.querySelector('.window-title').textContent = 'Overview';
      overviewWin.querySelector('.window-subtitle').textContent = 'planning';
      wireProjectsOverview(overviewWin);
    }
    if (listWin) {
      listWin.querySelector('.window-body').innerHTML = renderRoadmapList(activeIndex);
      listWin.querySelector('.window-title').textContent = 'Roadmaps';
      listWin.querySelector('.window-subtitle').textContent = (projectSamples[activeIndex] || projectSamples[0]).title;
      wireRoadmapListWindow(listWin);
    }
  }

  function wirePlanningProjectsWindow(win) {
    win.querySelector('[data-open-new-roadmap]')?.addEventListener('click', event => {
      event.preventDefault();
      openNewRoadmapWindow('project roadmap');
    });
    win.querySelector('[data-project-search]')?.addEventListener('input', event => {
      const query = event.currentTarget.value.trim().toLowerCase();
      win.querySelectorAll('[data-project-index]').forEach(row => {
        const text = row.textContent.toLowerCase();
        row.hidden = Boolean(query) && !text.includes(query);
      });
    });
    win.querySelectorAll('[data-project-index]').forEach(button => {
      button.addEventListener('click', event => {
        event.preventDefault();
        refreshPlanningWindows(Number(button.dataset.projectIndex || 0));
      });
    });
  }

  function wireRoadmapScroller(win) {
    const scroller = win.querySelector('[data-roadmap-scroll]');
    if (!scroller || scroller.dataset.wheelScrollReady) return;
    scroller.dataset.wheelScrollReady = 'true';
    scroller.addEventListener('wheel', event => {
      const canScrollHorizontally = scroller.scrollWidth > scroller.clientWidth + 1;
      if (!canScrollHorizontally) return;
      const horizontalDelta = Math.abs(event.deltaX) > Math.abs(event.deltaY)
        ? event.deltaX
        : event.deltaY;
      if (!horizontalDelta) return;
      event.preventDefault();
      scroller.scrollLeft += horizontalDelta;
    }, { passive: false });
  }

  function roadmapSearchText(id) {
    const node = roadmapNodeById(id);
    const row = planningRoadmapDemo.rows.find(item => item[0] === id);
    return [id, node?.title, node?.label, row?.join(' ')].filter(Boolean).join(' ').toLowerCase();
  }

  function applyRoadmapSearch(query) {
    const normalized = String(query || '').trim().toLowerCase();
    const hasQuery = normalized.length > 0;
    document.querySelectorAll('[data-roadmap-node]').forEach(node => {
      const isHit = hasQuery && roadmapSearchText(node.dataset.roadmapNode).includes(normalized);
      node.classList.toggle('is-search-hit', isHit);
      node.classList.toggle('is-search-dimmed', hasQuery && !isHit);
    });
    document.querySelectorAll('[data-roadmap-row]').forEach(row => {
      const text = (row.dataset.roadmapSearchText || row.textContent || '').toLowerCase();
      const isHit = hasQuery && text.includes(normalized);
      row.classList.toggle('is-search-hit', isHit);
      row.classList.toggle('is-search-dimmed', hasQuery && !isHit);
    });
  }

  function lockWorkspaceScroll() {
    window.scrollTo(0, 0);
    document.documentElement.scrollLeft = 0;
    document.documentElement.scrollTop = 0;
    document.body.scrollLeft = 0;
    document.body.scrollTop = 0;
    workspace.scrollLeft = 0;
    workspace.scrollTop = 0;
    requestAnimationFrame(() => {
      window.scrollTo(0, 0);
      document.documentElement.scrollLeft = 0;
      document.documentElement.scrollTop = 0;
      document.body.scrollLeft = 0;
      document.body.scrollTop = 0;
      workspace.scrollLeft = 0;
      workspace.scrollTop = 0;
    });
  }

  workspace.addEventListener('scroll', () => {
    if (workspace.scrollLeft === 0 && workspace.scrollTop === 0) return;
    workspace.scrollLeft = 0;
    workspace.scrollTop = 0;
    if (workspaceScrollGuardFrame) return;
    workspaceScrollGuardFrame = requestAnimationFrame(() => {
      workspaceScrollGuardFrame = null;
      lockWorkspaceScroll();
    });
  }, { passive: true });

  function wireProjectsOverview(win) {
    wireRoadmapScroller(win);

    const searchRoot = win.querySelector('[data-roadmap-search]');
    const searchInput = win.querySelector('[data-roadmap-search-input]');
    win.querySelector('[data-roadmap-search-toggle]')?.addEventListener('click', event => {
      event.preventDefault();
      searchRoot?.classList.toggle('open');
      if (searchRoot?.classList.contains('open')) searchInput?.focus();
      lockWorkspaceScroll();
    });
    searchInput?.addEventListener('focus', lockWorkspaceScroll);
    searchInput?.addEventListener('input', () => {
      lockWorkspaceScroll();
      searchRoot?.classList.toggle('has-query', Boolean(searchInput.value.trim()));
      applyRoadmapSearch(searchInput.value);
    });

    win.querySelectorAll('[data-gate-target]').forEach(gate => {
      gate.addEventListener('click', event => {
        event.preventDefault();
        focusRoadmapItem(win, gate.dataset.gateTarget);
      });
    });

    win.querySelectorAll('[data-roadmap-node]').forEach(node => {
      node.addEventListener('mouseenter', () => node.classList.add('is-expanded'));
      node.addEventListener('mouseleave', () => node.classList.remove('is-expanded'));
      node.addEventListener('focus', () => node.classList.add('is-expanded'));
      node.addEventListener('blur', () => node.classList.remove('is-expanded'));
      if (node.dataset.nodeKind === 'gate') {
        node.addEventListener('mouseenter', () => setGateFocus(win, node, true));
        node.addEventListener('mouseleave', () => setGateFocus(win, node, false));
        node.addEventListener('focus', () => setGateFocus(win, node, true));
        node.addEventListener('blur', () => setGateFocus(win, node, false));
      }
      node.addEventListener('click', () => focusRoadmapItem(win, node.dataset.roadmapNode));
      node.addEventListener('contextmenu', event => {
        event.preventDefault();
        event.stopPropagation();
        openPlanningContextMenu(event, node.dataset.roadmapNode || 'Roadmap');
      });
    });

    win.querySelector('[data-graph-view]')?.addEventListener('click', event => {
      if (event.target.closest('[data-roadmap-node], [data-roadmap-search]')) return;
      clearRoadmapFocus();
    });

  }

  function wireRoadmapListWindow(win) {
    win.querySelector('[data-roadmap-list-search]')?.addEventListener('input', event => {
      applyRoadmapSearch(event.currentTarget.value);
    });
    win.querySelectorAll('[data-roadmap-row]').forEach(row => {
      row.addEventListener('click', () => {
        if (row.dataset.roadmapRow === 'pending-roadmap') return;
        focusRoadmapItem(document.getElementById('projects-overview-window') || win, row.dataset.roadmapRow);
        win.querySelectorAll('[data-roadmap-row]').forEach(item => item.classList.toggle('selected', item === row));
      });
    });
  }

  function setRoadmapGenerationPending() {
    let listWin = document.getElementById('roadmap-list-window');
    if (!listWin) {
      listWin = openRoadmapListWindow(activePlanningProjectIndex, { activate: false });
    }
    listWin?.querySelector('[data-list-view]')?.classList.add('is-generating');
    buildState.textContent = 'V2 - AI roadmap pipeline started';
  }

  function closeRoadmapPickers(except = null) {
    document.querySelectorAll('[data-roadmap-picker-menu]').forEach(menu => {
      if (menu !== except) menu.hidden = true;
    });
  }

  function wireNewRoadmapWindow(win) {
    win.querySelectorAll('[data-roadmap-picker]').forEach(button => {
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        const menu = win.querySelector('[data-roadmap-picker-menu="' + button.dataset.roadmapPicker + '"]');
        if (!menu) return;
        const shouldOpen = menu.hidden;
        closeRoadmapPickers(menu);
        menu.hidden = !shouldOpen;
      });
    });

    win.querySelectorAll('[data-roadmap-picker-menu] button').forEach(option => {
      option.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        const menu = option.closest('[data-roadmap-picker-menu]');
        const picker = win.querySelector('[data-roadmap-picker="' + menu.dataset.roadmapPickerMenu + '"]');
        menu.querySelectorAll('button').forEach(item => item.classList.remove('active'));
        option.classList.add('active');
        if (picker) {
          const label = option.dataset.pickerLabel || option.querySelector('span')?.textContent || option.textContent.trim();
          const detail = option.dataset.pickerDetail || option.querySelector('em')?.textContent || '';
          picker.querySelector('span').innerHTML = '<strong>' + esc(label) + '</strong><small>' + esc(detail) + '</small>';
        }
        menu.hidden = true;
      });
    });

    win.querySelector('[data-roadmap-generate]')?.addEventListener('click', event => {
      event.preventDefault();
      closeRoadmapPickers();
      setRoadmapGenerationPending();
      removeDockBubble(win);
      clearPersistedWindowState(win);
      win.remove();
    });
  }

  function setGateFocus(win, gateNode, active) {
    const graph = win.querySelector('.planning-roadmap-graph');
    if (!graph) return;
    const affected = new Set((gateNode.dataset.gateAffects || '').split(',').filter(Boolean));
    affected.add(gateNode.dataset.roadmapNode);
    graph.classList.toggle('gate-focus-active', active);
    graph.querySelectorAll('[data-roadmap-node]').forEach(node => {
      const isRelated = affected.has(node.dataset.roadmapNode);
      node.classList.toggle('is-gate-related', active && isRelated);
      node.classList.toggle('is-gate-dimmed', active && !isRelated);
    });
    graph.querySelectorAll('[data-edge-affects]').forEach(edge => {
      const edgeIds = (edge.dataset.edgeAffects || '').split(',').filter(Boolean);
      const isRelated = edgeIds.length > 0 && edgeIds.every(id => affected.has(id));
      edge.classList.toggle('is-gate-related', active && isRelated);
      edge.classList.toggle('is-gate-dimmed', active && !isRelated);
    });
  }

  function clearRoadmapFocus() {
    document.querySelectorAll('[data-roadmap-node], [data-roadmap-row]').forEach(item => {
      item.classList.remove('selected');
    });
    document.querySelectorAll('.planning-roadmap-graph').forEach(graph => {
      graph.classList.remove('selection-focus-active', 'gate-focus-active');
      graph.querySelectorAll('[data-roadmap-node]').forEach(node => {
        node.classList.remove('is-selection-dimmed', 'is-selection-related', 'is-gate-dimmed', 'is-gate-related');
      });
      graph.querySelectorAll('.roadmap-edge').forEach(edge => {
        edge.classList.remove('is-selection-dimmed', 'is-selection-related', 'is-gate-dimmed', 'is-gate-related');
      });
    });
  }

  function centerRoadmapNodeInGraph(graph, selectedNode) {
    const scroller = graph?.querySelector('[data-roadmap-scroll]');
    if (!scroller || !selectedNode) return;
    const scrollerRect = scroller.getBoundingClientRect();
    const nodeRect = selectedNode.getBoundingClientRect();
    const nodeCenter = nodeRect.left - scrollerRect.left + scroller.scrollLeft + (nodeRect.width / 2);
    const maxLeft = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
    const targetLeft = Math.max(0, Math.min(maxLeft, nodeCenter - (scroller.clientWidth / 2)));
    scroller.scrollTo({ left: targetLeft, behavior: 'smooth' });
    lockWorkspaceScroll();
  }

  function focusRoadmapItem(win, target) {
    if (!target) return;
    const node = roadmapNodeById(target);
    const kind = node?.kind || 'roadmap';
    const alreadySelected = Boolean(document.querySelector(`[data-roadmap-node="${CSS.escape(target)}"].selected, [data-roadmap-row="${CSS.escape(target)}"].selected`));

    if (alreadySelected || kind !== 'roadmap') {
      clearRoadmapFocus();
      if (kind === 'version') {
        buildState.textContent = 'V2 - Version marker: ' + target;
      } else if (kind === 'gate') {
        buildState.textContent = 'V2 - Gate: ' + target;
      }
      return;
    }

    clearRoadmapFocus();
    document.querySelectorAll('[data-roadmap-node], [data-roadmap-row]').forEach(item => {
      item.classList.toggle('selected', item.dataset.roadmapNode === target || item.dataset.roadmapRow === target);
    });
    document.querySelectorAll('.planning-roadmap-graph').forEach(graph => {
      const selectedNode = graph.querySelector(`[data-roadmap-node="${CSS.escape(target)}"]`);
      graph.classList.toggle('selection-focus-active', Boolean(selectedNode));
      graph.querySelectorAll('[data-roadmap-node]').forEach(node => {
        node.classList.toggle('is-selection-dimmed', Boolean(selectedNode) && node !== selectedNode);
      });
      graph.querySelectorAll('.roadmap-edge').forEach(edge => {
        const related = edge.dataset.edgeFrom === target || edge.dataset.edgeTo === target;
        edge.classList.toggle('is-selection-related', Boolean(selectedNode) && related);
        edge.classList.toggle('is-selection-dimmed', Boolean(selectedNode) && !related);
      });
      centerRoadmapNodeInGraph(graph, selectedNode);
    });
    openRoadmapDocument(target);
    buildState.textContent = 'V2 - Roadmap: ' + target;
  }

  function openPlanningContextMenu(event, target) {
    let menu = document.getElementById('planning-context-menu');
    if (!menu) {
      menu = document.createElement('div');
      menu.id = 'planning-context-menu';
      menu.className = 'planning-context-menu';
      menu.innerHTML = `
        <div class="context-menu-head">
          <strong data-planning-context-title>Roadmap</strong>
          <span>roadmap node</span>
        </div>
        <button class="primary" type="button" data-planning-context-action="attach">Attach roadmap</button>
        <button type="button" data-planning-context-action="open">Open roadmap</button>
        <button type="button" data-planning-context-action="copy-id">Copy roadmap id</button>
        <button type="button" data-planning-context-action="copy-link">Copy roadmap link</button>
        <div class="context-status" data-planning-context-status>Roadmap node actions</div>
      `;
      stage.appendChild(menu);
      menu.addEventListener('click', contextEvent => {
        const button = contextEvent.target.closest('[data-planning-context-action]');
        if (!button) return;
        const activeTarget = menu.dataset.target || 'Roadmap';
        const id = activeTarget.toLowerCase().replace(/\s+/g, '-');
        if (button.dataset.planningContextAction === 'attach') {
          openNewRoadmapWindow(activeTarget);
          closePlanningContextMenu();
        }
        if (button.dataset.planningContextAction === 'open') {
          openRoadmapDocument(activeTarget) || (document.getElementById('projects-overview-window') && focusRoadmapItem(document.getElementById('projects-overview-window'), activeTarget));
          menu.querySelector('[data-planning-context-status]').textContent = 'Opened roadmap';
        }
        if (button.dataset.planningContextAction === 'copy-id') {
          copyTextToClipboard('roadmap:' + id, menu.querySelector('[data-planning-context-status]'), 'Roadmap id');
        }
        if (button.dataset.planningContextAction === 'copy-link') {
          copyTextToClipboard('harbor://planning/' + id, menu.querySelector('[data-planning-context-status]'), 'Roadmap link');
        }
      });
    }
    menu.dataset.target = target;
    menu.querySelector('[data-planning-context-title]').textContent = target;
    menu.querySelector('[data-planning-context-status]').textContent = 'Attach, open, or copy roadmap references';
    menu.classList.add('open');
    const width = menu.offsetWidth || 220;
    const height = menu.offsetHeight || 200;
    menu.style.left = Math.max(14, Math.min(window.innerWidth - width - 14, event.clientX + 2)) + 'px';
    menu.style.top = Math.max(14, Math.min(window.innerHeight - height - 14, event.clientY + 2)) + 'px';
  }

  function closePlanningContextMenu() {
    document.getElementById('planning-context-menu')?.classList.remove('open');
  }

  async function copyTextToClipboard(text, statusEl, label) {
    try {
      await navigator.clipboard.writeText(text);
      if (statusEl) statusEl.textContent = label + ' copied';
    } catch {
      if (statusEl) statusEl.textContent = label + ': ' + text;
    }
  }

  function openNewRoadmapWindow(target = 'Roadmap') {
    let win = document.getElementById('new-roadmap-window');
    if (win) {
      removeDockBubble(win);
      clearPersistedWindowState(win);
      win.remove();
    }
    const wrap = document.createElement('div');
    wrap.innerHTML = renderNewRoadmapWindow(target);
    win = wrap.firstElementChild;
    win.id = 'new-roadmap-window';
    activeWorkspaceScreen().appendChild(win);
    prepareFloatingWindow(win);
    installResizeHandles(win);
    wireNewRoadmapWindow(win);
    activateWindow(win);
    buildState.textContent = 'V2 - New Roadmap';
  }

  function openProjectsOverview(activeIndex = activePlanningProjectIndex) {
    setActiveWorkspace('planning');
    activePlanningProjectIndex = activeIndex;
    persistWorkspaceState();
    openPlanningProjectsWindow(activeIndex);
    openRoadmapListWindow(activeIndex, { activate: false });
    let win = document.getElementById('projects-overview-window');
    if (!win) {
      win = document.createElement('article');
      win.className = 'floating-window projects-overview-window active';
      win.id = 'projects-overview-window';
      win.dataset.window = '';
      win.dataset.windowId = 'projects-overview';
      win.dataset.windowCloseMode = 'remove';
      win.setAttribute('aria-label', 'Project overview');
      win.innerHTML = `
        <header class="window-head" data-drag-handle>
          <div class="window-subtitle">planning</div>
          <div class="window-title">Overview</div>
          <div class="window-actions" aria-label="Window controls">
            <button class="window-control" data-window-min title="Minimize" aria-label="Minimize">-</button>
            <button class="window-control" data-window-max title="Maximize" aria-label="Maximize">&#9633;</button>
            <button class="window-control" data-window-close title="Close" aria-label="Close">x</button>
          </div>
        </header>
        <div class="window-body">${renderProjectsOverview(activeIndex)}</div>
      `;
      activeWorkspaceScreen().appendChild(win);
      prepareFloatingWindow(win);
      installResizeHandles(win);
      wireProjectsOverview(win);
    } else {
      setWindowMinimized(win, false);
      win.querySelector('.window-body').innerHTML = renderProjectsOverview(activeIndex);
      win.querySelector('.window-body').dataset.projectsOverviewReady = 'true';
      wireProjectsOverview(win);
    }
    activateWindow(win);
    buildState.textContent = 'V2 - Planning Overview';
    closeToolwheel();
  }

  function openPlanningProjectsWindow(activeIndex = activePlanningProjectIndex) {
    setActiveWorkspace('planning');
    activePlanningProjectIndex = activeIndex;
    persistWorkspaceState();
    let win = document.getElementById('planning-projects-window');
    if (!win) {
      win = document.createElement('article');
      win.className = 'floating-window planning-projects-window active';
      win.id = 'planning-projects-window';
      win.dataset.window = '';
      win.dataset.windowId = 'planning-projects';
      win.dataset.windowCloseMode = 'remove';
      win.setAttribute('aria-label', 'Projects');
      win.innerHTML = `
        <header class="window-head" data-drag-handle>
          <div class="window-subtitle">master-roadmap.json</div>
          <div class="window-title">Projects</div>
          <div class="window-actions" aria-label="Window controls">
            <button class="window-control" data-window-min title="Minimize" aria-label="Minimize">-</button>
            <button class="window-control" data-window-max title="Maximize" aria-label="Maximize">&#9633;</button>
            <button class="window-control" data-window-close title="Close" aria-label="Close">x</button>
          </div>
        </header>
        <div class="window-body">${renderPlanningProjects(activeIndex)}</div>
      `;
      activeWorkspaceScreen().appendChild(win);
      prepareFloatingWindow(win);
      installResizeHandles(win);
      wirePlanningProjectsWindow(win);
    } else {
      setWindowMinimized(win, false);
      win.querySelector('.window-body').innerHTML = renderPlanningProjects(activeIndex);
      wirePlanningProjectsWindow(win);
    }
    return win;
  }

  function openRoadmapListWindow(activeIndex = activePlanningProjectIndex, options = {}) {
    setActiveWorkspace('planning');
    activePlanningProjectIndex = activeIndex;
    persistWorkspaceState();
    let win = document.getElementById('roadmap-list-window');
    if (!win) {
      win = document.createElement('article');
      win.className = 'floating-window roadmap-list-window active';
      win.id = 'roadmap-list-window';
      win.dataset.window = '';
      win.dataset.windowId = 'roadmap-list';
      win.dataset.windowCloseMode = 'remove';
      win.setAttribute('aria-label', 'Roadmap list');
      win.innerHTML = `
        <header class="window-head" data-drag-handle>
          <div class="window-subtitle">${esc((projectSamples[activeIndex] || projectSamples[0]).title)}</div>
          <div class="window-title">Roadmaps</div>
          <div class="window-actions" aria-label="Window controls">
            <button class="window-control" data-window-min title="Minimize" aria-label="Minimize">-</button>
            <button class="window-control" data-window-max title="Maximize" aria-label="Maximize">&#9633;</button>
            <button class="window-control" data-window-close title="Close" aria-label="Close">x</button>
          </div>
        </header>
        <div class="window-body">${renderRoadmapList(activeIndex)}</div>
      `;
      activeWorkspaceScreen().appendChild(win);
      prepareFloatingWindow(win);
      installResizeHandles(win);
      wireRoadmapListWindow(win);
    } else {
      setWindowMinimized(win, false);
      win.querySelector('.window-body').innerHTML = renderRoadmapList(activeIndex);
      wireRoadmapListWindow(win);
    }
    if (options.activate !== false) {
      activateWindow(win);
      buildState.textContent = 'V2 - Roadmap List';
    }
    return win;
  }

  function installDefaultProjectsOverviewWindow() {
    const win = document.getElementById('projects-overview-window');
    openPlanningProjectsWindow(activePlanningProjectIndex);
    openRoadmapListWindow(activePlanningProjectIndex, { activate: false });
    if (!win) return;
    win.querySelector('.window-title').textContent = 'Overview';
    win.querySelector('.window-subtitle').textContent = 'planning';
    const body = win.querySelector('[data-projects-overview-default]') || win.querySelector('.window-body');
    if (body && !body.dataset.projectsOverviewReady) {
      body.innerHTML = renderProjectsOverview(activePlanningProjectIndex);
      body.dataset.projectsOverviewReady = 'true';
      wireProjectsOverview(win);
    }
    prepareFloatingWindow(win);
    installResizeHandles(win);
  }

  function openTodosWindow() {
    let win = document.getElementById('todos-window');
    if (!win) {
      win = document.createElement('article');
      win.className = 'floating-window todos-window active';
      win.id = 'todos-window';
      win.dataset.window = '';
      win.dataset.windowId = 'todos';
      win.dataset.windowCloseMode = 'remove';
      win.setAttribute('aria-label', 'Todos');
      win.innerHTML = `
        <header class="window-head" data-drag-handle>
          <div class="window-subtitle">global</div>
          <div class="window-title">Todos</div>
          <div class="window-actions" aria-label="Window controls">
            <button class="window-control" data-window-min title="Minimize" aria-label="Minimize">-</button>
            <button class="window-control" data-window-max title="Maximize" aria-label="Maximize">&#9633;</button>
            <button class="window-control" data-window-close title="Close" aria-label="Close">x</button>
          </div>
        </header>
        <div class="window-body">${renderTodosWindow()}</div>
      `;
      activeWorkspaceScreen().appendChild(win);
      prepareFloatingWindow(win);
      wireTodosWindow(win);
    } else {
      setWindowMinimized(win, false);
      refreshTodosWindow(win);
    }
    activateWindow(win);
    buildState.textContent = 'V2 - Todos';
    closeToolwheel();
  }

  function knowledgeGraphRandom(seed) {
    let value = seed % 2147483647;
    return () => {
      value = value * 16807 % 2147483647;
      return (value - 1) / 2147483646;
    };
  }

  function ensureKnowledgeGraphData() {
    if (knowledgeGraphNodes.length) return;
    const random = knowledgeGraphRandom(428);
    knowledgeGraphNodes = Array.from({ length: 260 }, (_, index) => {
      const source = knowledgeGraphSources[index % knowledgeGraphSources.length];
      const type = knowledgeGraphTypes[(index * 7) % knowledgeGraphTypes.length];
      const ring = Math.floor(index / 38);
      const angle = random() * Math.PI * 2;
      const radius = 82 + ring * 54 + random() * 118;
      const topic = index % 11 === 0 ? 'Memory retrieval' : source;
      return {
        id: 'kg-' + index,
        title: topic + ' ' + type + ' ' + (index + 1),
        source,
        type,
        cluster: source + ' / ' + (type === 'Cluster' ? 'Core' : type),
        trust: Math.round(26 + random() * 74),
        age: Math.round(random() * 96) + 'h',
        x: Math.cos(angle) * radius + random() * 72,
        y: Math.sin(angle) * radius + random() * 72,
        size: type === 'Cluster' ? 8 + random() * 8 : 3.5 + random() * 5,
        summary: 'Derived knowledge node with linked sources, summaries, memory candidates and project context.'
      };
    });

    knowledgeGraphEdges = [];
    for (let index = 0; index < knowledgeGraphNodes.length; index += 1) {
      const links = 1 + (index % 4);
      for (let step = 1; step <= links; step += 1) {
        knowledgeGraphEdges.push({
          from: knowledgeGraphNodes[index].id,
          to: knowledgeGraphNodes[(index + step * (7 + index % 9)) % knowledgeGraphNodes.length].id,
          weight: 0.2 + step / 5
        });
      }
    }
  }

  function visibleKnowledgeGraphNodes() {
    ensureKnowledgeGraphData();
    const query = knowledgeGraphState.query.toLowerCase();
    return knowledgeGraphNodes.filter(node => {
      const sourceOk = knowledgeGraphState.source === 'All' || node.source === knowledgeGraphState.source;
      const typeOk = knowledgeGraphState.type === 'All' || node.type === knowledgeGraphState.type;
      const trustOk = node.trust >= knowledgeGraphState.trust;
      const queryOk = !query || [node.title, node.source, node.type, node.cluster].join(' ').toLowerCase().includes(query);
      return sourceOk && typeOk && trustOk && queryOk;
    }).slice(0, Math.max(20, Math.round(knowledgeGraphNodes.length * knowledgeGraphState.density / 100)));
  }

  function visibleKnowledgeGraphEdges(nodes) {
    const ids = new Set(nodes.map(node => node.id));
    return knowledgeGraphEdges.filter(edge => ids.has(edge.from) && ids.has(edge.to));
  }

  function knowledgeGraphColor(node) {
    return knowledgeGraphPalette[node.source] || '#16d9f5';
  }

  function projectKnowledgeGraphNode(node, rect) {
    const scale = Math.min(rect.width, rect.height) / 760;
    let x = node.x;
    let y = node.y;

    if (knowledgeGraphState.layout === 'Source') {
      const sourceIndex = knowledgeGraphSources.indexOf(node.source);
      const angle = (sourceIndex / knowledgeGraphSources.length) * Math.PI * 2 - Math.PI / 2;
      x = Math.cos(angle) * 240 + node.x * 0.38;
      y = Math.sin(angle) * 190 + node.y * 0.38;
    } else if (knowledgeGraphState.layout === 'Time') {
      const typeIndex = knowledgeGraphTypes.indexOf(node.type);
      x = (node.trust - 58) * 7 + node.x * 0.34;
      y = (typeIndex - 2.5) * 76 + node.y * 0.28;
    }

    return {
      x: rect.width / 2 + x * scale,
      y: rect.height / 2 + y * scale
    };
  }

  function renderKnowledgeGraphFacets(win) {
    ensureKnowledgeGraphData();
    const sourceTarget = win.querySelector('[data-kg-source-facets]');
    const typeTarget = win.querySelector('[data-kg-type-facets]');
    if (!sourceTarget || !typeTarget) return;

    sourceTarget.innerHTML = [['All', '#16d9f5'], ...knowledgeGraphSources.map(source => [source, knowledgeGraphPalette[source]])].map(([label, color]) => `
      <button class="kg-facet${knowledgeGraphState.source === label ? ' active' : ''}" style="--facet:${color}" type="button" data-kg-source="${esc(label)}">
        <strong>${esc(label)}</strong><span>${label === 'All' ? knowledgeGraphNodes.length : knowledgeGraphNodes.filter(node => node.source === label).length}</span>
      </button>
    `).join('');

    typeTarget.innerHTML = [['All', '#16d9f5'], ...knowledgeGraphTypes.map(type => [type, '#22d3b6'])].map(([label, color]) => `
      <button class="kg-facet${knowledgeGraphState.type === label ? ' active' : ''}" style="--facet:${color}" type="button" data-kg-type="${esc(label)}">
        <strong>${esc(label)}</strong><span>${label === 'All' ? knowledgeGraphNodes.length : knowledgeGraphNodes.filter(node => node.type === label).length}</span>
      </button>
    `).join('');

    win.querySelector('[data-kg-source-label]').textContent = knowledgeGraphState.source;
    win.querySelector('[data-kg-type-label]').textContent = knowledgeGraphState.type;
  }

  function renderKnowledgeGraphInspector(win, node) {
    const inspector = win.querySelector('[data-kg-inspector]');
    if (!inspector) return;
    if (!node) {
      inspector.innerHTML = '<div class="kg-inspector-empty">No node selected.</div>';
      return;
    }
    const links = knowledgeGraphEdges.filter(edge => edge.from === node.id || edge.to === node.id).length;
    inspector.innerHTML = `
      <div class="kg-inspector-head">
        <div class="kg-inspector-kicker"><span>${esc(node.source)}</span><span>${node.trust}% trust</span></div>
        <h2>${esc(node.title)}</h2>
        <p>${esc(node.summary)}</p>
      </div>
      <div class="kg-inspector-body">
        <div class="kg-props">
          <div><span>Type</span><strong>${esc(node.type)}</strong></div>
          <div><span>Cluster</span><strong>${esc(node.cluster)}</strong></div>
          <div><span>Age</span><strong>${esc(node.age)}</strong></div>
          <div><span>Links</span><strong>${links}</strong></div>
          <div><span>Render</span><strong>Canvas node</strong></div>
        </div>
      </div>
      <div class="kg-inspector-actions">
        <button type="button" data-kg-action="Open Source">Open Source</button>
        <button type="button" data-kg-action="Attach">Attach</button>
        <button class="primary" type="button" data-kg-action="Use Cluster">Use Cluster</button>
      </div>
    `;
  }

  function drawKnowledgeGraphClusters(ctx, nodes, rect) {
    const groups = new Map();
    nodes.forEach(node => {
      if (!groups.has(node.source)) groups.set(node.source, []);
      groups.get(node.source).push(node);
    });

    groups.forEach((group, source) => {
      const center = group.reduce((acc, node) => {
        const point = projectKnowledgeGraphNode(node, rect);
        acc.x += point.x;
        acc.y += point.y;
        return acc;
      }, { x: 0, y: 0 });
      center.x /= group.length;
      center.y /= group.length;
      const radius = Math.min(92, 24 + group.length * 0.82);
      ctx.beginPath();
      ctx.strokeStyle = knowledgeGraphPalette[source] + '36';
      ctx.fillStyle = knowledgeGraphPalette[source] + '0f';
      ctx.arc(center.x, center.y, radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    });
  }

  function drawKnowledgeGraphMinimap(win, nodes) {
    const canvas = win.querySelector('[data-kg-minimap]');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = 132;
    const height = 92;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = 'rgba(2, 9, 16, 0.72)';
    ctx.fillRect(0, 0, width, height);
    nodes.forEach(node => {
      ctx.fillStyle = knowledgeGraphColor(node) + '99';
      ctx.beginPath();
      ctx.arc(66 + node.x / 12, 46 + node.y / 12, 1.45, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.strokeStyle = 'rgba(22, 217, 245, 0.28)';
    ctx.strokeRect(26, 18, 80, 56);
  }

  function drawKnowledgeGraph(win) {
    const canvas = win.querySelector('[data-kg-canvas]');
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const sizeKey = rect.width + 'x' + rect.height + '@' + dpr;
    if (canvas.dataset.sizeKey !== sizeKey) {
      canvas.width = Math.floor(rect.width * dpr);
      canvas.height = Math.floor(rect.height * dpr);
      canvas.dataset.sizeKey = sizeKey;
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);
    ctx.fillStyle = 'rgba(2, 9, 16, 0.24)';
    ctx.fillRect(0, 0, rect.width, rect.height);

    const nodes = visibleKnowledgeGraphNodes();
    const edges = visibleKnowledgeGraphEdges(nodes);
    const byId = new Map(nodes.map(node => [node.id, node]));
    edges.forEach(edge => {
      const a = byId.get(edge.from);
      const b = byId.get(edge.to);
      if (!a || !b) return;
      const pa = projectKnowledgeGraphNode(a, rect);
      const pb = projectKnowledgeGraphNode(b, rect);
      ctx.strokeStyle = 'rgba(22, 217, 245, 0.09)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pa.x, pa.y);
      ctx.lineTo(pb.x, pb.y);
      ctx.stroke();
    });

    if (knowledgeGraphState.cluster && nodes.length > 80) {
      drawKnowledgeGraphClusters(ctx, nodes, rect);
    }

    nodes.forEach(node => {
      const p = projectKnowledgeGraphNode(node, rect);
      const color = knowledgeGraphColor(node);
      const selected = node.id === knowledgeGraphState.selectedId;
      ctx.beginPath();
      ctx.fillStyle = selected ? color : color + '64';
      ctx.strokeStyle = color;
      ctx.lineWidth = selected ? 2 : 1;
      ctx.shadowColor = selected ? color : 'transparent';
      ctx.shadowBlur = selected ? 15 : 0;
      ctx.arc(p.x, p.y, selected ? node.size + 4 : node.size, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.shadowBlur = 0;

      const shouldLabel = selected || knowledgeGraphState.labels === 'All' || (knowledgeGraphState.labels === 'Focus' && node.type === 'Cluster' && nodes.length < 150);
      if (shouldLabel) {
        ctx.fillStyle = 'rgba(215, 247, 255, 0.82)';
        ctx.font = '10px Fira Code, SFMono-Regular, Consolas, monospace';
        ctx.fillText(node.title, p.x + 10, p.y - 8);
      }
    });

    drawKnowledgeGraphMinimap(win, nodes);
    win.querySelector('[data-kg-visible]').textContent = nodes.length + ' visible';
    win.querySelector('[data-kg-links]').textContent = edges.length + ' links';
    win.querySelector('[data-kg-clusters]').textContent = new Set(nodes.map(node => node.cluster)).size + ' clusters';
    renderKnowledgeGraphInspector(win, nodes.find(node => node.id === knowledgeGraphState.selectedId) || nodes[0]);
  }

  function renderKnowledgeGraphWindow() {
    return `
      <div class="knowledge-graph-layout">
        <div class="kg-toolbar">
          <label class="kg-search">
            <input type="search" data-kg-search placeholder="find memories, sources, projects" value="${esc(knowledgeGraphState.query)}">
          </label>
          <button class="kg-toolbar-button${knowledgeGraphState.cluster ? ' active' : ''}" type="button" data-kg-cluster>Cluster mode</button>
          <button class="kg-toolbar-button" type="button" data-kg-labels>Labels: ${esc(knowledgeGraphState.labels)}</button>
          <button class="kg-toolbar-button" type="button" data-kg-layout>Layout: ${esc(knowledgeGraphState.layout)}</button>
        </div>
        <div class="kg-body">
          <aside class="kg-filter-rail">
            <section class="kg-filter-section">
              <div class="kg-filter-title"><span>Source</span><span data-kg-source-label>All</span></div>
              <div class="kg-facet-stack" data-kg-source-facets></div>
            </section>
            <section class="kg-filter-section">
              <div class="kg-filter-title"><span>Type</span><span data-kg-type-label>All</span></div>
              <div class="kg-facet-stack" data-kg-type-facets></div>
            </section>
            <section class="kg-filter-section">
              <div class="kg-filter-title"><span>Scale</span><span>LOD</span></div>
              <div class="kg-range-block">
                <label><span>Density</span><span data-kg-density-value>${knowledgeGraphState.density}%</span></label>
                <input type="range" min="18" max="100" value="${knowledgeGraphState.density}" data-kg-density>
                <label><span>Min trust</span><span data-kg-trust-value>${knowledgeGraphState.trust}%</span></label>
                <input type="range" min="0" max="95" value="${knowledgeGraphState.trust}" data-kg-trust>
              </div>
            </section>
          </aside>
          <section class="kg-canvas-area" aria-label="Knowledge graph canvas">
            <canvas class="kg-canvas" data-kg-canvas></canvas>
            <div class="kg-hud">
              <span class="kg-hud-chip" data-kg-visible>0 visible</span>
              <span class="kg-hud-chip" data-kg-links>0 links</span>
              <span class="kg-hud-chip" data-kg-clusters>0 clusters</span>
              <span class="kg-hud-chip">Canvas render</span>
            </div>
            <div class="kg-minimap"><canvas data-kg-minimap></canvas></div>
            <div class="kg-lod-note">Large graph mode: labels reduce first, distant nodes become clusters, and filters define the render budget.</div>
          </section>
          <aside class="kg-inspector" data-kg-inspector></aside>
        </div>
      </div>
    `;
  }

  function wireKnowledgeGraphWindow(win) {
    ensureKnowledgeGraphData();
    renderKnowledgeGraphFacets(win);

    win.querySelector('[data-kg-search]')?.addEventListener('input', event => {
      knowledgeGraphState.query = event.target.value.trim();
      requestAnimationFrame(() => drawKnowledgeGraph(win));
    });

    win.querySelector('[data-kg-density]')?.addEventListener('input', event => {
      knowledgeGraphState.density = Number(event.target.value);
      win.querySelector('[data-kg-density-value]').textContent = knowledgeGraphState.density + '%';
      drawKnowledgeGraph(win);
    });

    win.querySelector('[data-kg-trust]')?.addEventListener('input', event => {
      knowledgeGraphState.trust = Number(event.target.value);
      win.querySelector('[data-kg-trust-value]').textContent = knowledgeGraphState.trust + '%';
      drawKnowledgeGraph(win);
    });

    win.querySelector('.kg-filter-rail')?.addEventListener('click', event => {
      const source = event.target.closest('[data-kg-source]');
      const type = event.target.closest('[data-kg-type]');
      if (!source && !type) return;
      if (source) knowledgeGraphState.source = source.dataset.kgSource;
      if (type) knowledgeGraphState.type = type.dataset.kgType;
      renderKnowledgeGraphFacets(win);
      drawKnowledgeGraph(win);
    });

    win.querySelector('[data-kg-cluster]')?.addEventListener('click', event => {
      knowledgeGraphState.cluster = !knowledgeGraphState.cluster;
      event.currentTarget.classList.toggle('active', knowledgeGraphState.cluster);
      drawKnowledgeGraph(win);
    });

    win.querySelector('[data-kg-labels]')?.addEventListener('click', event => {
      knowledgeGraphState.labels = knowledgeGraphState.labels === 'Focus' ? 'All' : 'Focus';
      event.currentTarget.textContent = 'Labels: ' + knowledgeGraphState.labels;
      drawKnowledgeGraph(win);
    });

    win.querySelector('[data-kg-layout]')?.addEventListener('click', event => {
      const modes = ['Semantic', 'Source', 'Time'];
      knowledgeGraphState.layout = modes[(modes.indexOf(knowledgeGraphState.layout) + 1) % modes.length];
      event.currentTarget.textContent = 'Layout: ' + knowledgeGraphState.layout;
      drawKnowledgeGraph(win);
    });

    win.querySelector('[data-kg-canvas]')?.addEventListener('click', event => {
      const canvas = event.currentTarget;
      const rect = canvas.getBoundingClientRect();
      const nodes = visibleKnowledgeGraphNodes();
      let best = null;
      let bestDistance = Infinity;
      nodes.forEach(node => {
        const point = projectKnowledgeGraphNode(node, rect);
        const distance = Math.hypot(event.clientX - rect.left - point.x, event.clientY - rect.top - point.y);
        if (distance < bestDistance) {
          bestDistance = distance;
          best = node;
        }
      });
      if (best && bestDistance < 28) {
        knowledgeGraphState.selectedId = best.id;
        drawKnowledgeGraph(win);
      }
    });

    win.querySelector('[data-kg-inspector]')?.addEventListener('click', event => {
      const button = event.target.closest('[data-kg-action]');
      if (!button) return;
      if (button.dataset.kgAction === 'Attach') {
        const selected = knowledgeGraphNodes.find(node => node.id === knowledgeGraphState.selectedId);
        if (selected) addContextNodge('Memory', selected.title);
      }
      announceAction(button.dataset.kgAction);
    });

    if (window.ResizeObserver && !win._knowledgeGraphResizeObserver) {
      win._knowledgeGraphResizeObserver = new ResizeObserver(() => {
        requestAnimationFrame(() => drawKnowledgeGraph(win));
      });
      win._knowledgeGraphResizeObserver.observe(win.querySelector('.kg-canvas-area'));
    }
    requestAnimationFrame(() => drawKnowledgeGraph(win));
  }

  function installDefaultKnowledgeGraphWindow() {
    const win = document.getElementById('knowledge-graph-window');
    if (!win) return;
    const body = win.querySelector('[data-knowledge-graph-default]') || win.querySelector('.window-body');
    if (body && !body.dataset.knowledgeGraphReady) {
      body.innerHTML = renderKnowledgeGraphWindow();
      body.dataset.knowledgeGraphReady = 'true';
      wireKnowledgeGraphWindow(win);
    }
    prepareFloatingWindow(win);
    requestAnimationFrame(() => drawKnowledgeGraph(win));
  }

  function memoryStatusColor(memory) {
    const colors = {
      attention: 'var(--red)',
      learned: 'var(--blue)',
      pinned: 'var(--cyan)',
      confirmed: 'var(--green)',
      private: 'var(--teal)',
      forgotten: 'var(--muted)'
    };
    return colors[memory?.status] || 'var(--cyan)';
  }

  function memoryMatchesTab(memory) {
    if (memoryState.tab === 'All') return true;
    if (memoryState.tab === 'Attention') return memory.status === 'attention';
    if (memoryState.tab === 'Pinned') return memory.status === 'pinned';
    if (memoryState.tab === 'Learned') return memory.status === 'learned';
    if (memoryState.tab === 'History') return memory.status === 'forgotten';
    return true;
  }

  function visibleMemories() {
    const query = memoryState.query.toLowerCase();
    const weight = { attention: 0, private: 1, learned: 2, pinned: 3, confirmed: 4, forgotten: 5 };
    return memorySamples
      .filter(memory => {
        const typeOk = memoryState.type === 'All' || memory.type === memoryState.type;
        const sourceOk = memoryState.source === 'All' || memory.source === memoryState.source;
        const queryOk = !query || [memory.title, memory.text, memory.type, memory.source, memory.project].join(' ').toLowerCase().includes(query);
        return typeOk && sourceOk && memoryMatchesTab(memory) && queryOk;
      })
      .sort((a, b) => {
        if (memoryState.sortAttentionFirst) return (weight[a.status] ?? 9) - (weight[b.status] ?? 9);
        return b.confidence - a.confidence;
      });
  }

  function renderMemoryTabs() {
    const tabs = [
      ['All', memorySamples.length],
      ['Attention', memorySamples.filter(memory => memory.status === 'attention').length],
      ['Pinned', memorySamples.filter(memory => memory.status === 'pinned').length],
      ['Learned', memorySamples.filter(memory => memory.status === 'learned').length],
      ['History', memorySamples.filter(memory => memory.status === 'forgotten').length]
    ];
    return tabs.map(([tab, count]) => `
      <button class="memory-tab${memoryState.tab === tab ? ' active' : ''}" type="button" data-memory-tab="${esc(tab)}">
        <span>${tab === 'Attention' ? 'Needs attention' : esc(tab)}</span><span>${count}</span>
      </button>
    `).join('');
  }

  function renderMemoryFacet(items, active, key, colors = {}) {
    return ['All', ...items].map(item => {
      const count = item === 'All' ? memorySamples.length : memorySamples.filter(memory => memory[key] === item).length;
      const color = item === 'All' ? 'var(--cyan)' : (colors[item] || 'var(--teal)');
      return `
        <button class="memory-facet${active === item ? ' active' : ''}" style="--facet:${color}" type="button" data-memory-${key}="${esc(item)}">
          <strong>${esc(item)}</strong><span>${count}</span>
        </button>
      `;
    }).join('');
  }

  function renderMemoryRows(items) {
    if (!items.length) return '<div class="memory-empty">Nothing needs attention here.</div>';
    return items.map(memory => `
      <button class="memory-row${memoryState.detailOpen && memory.id === memoryState.selectedId ? ' active' : ''}" style="--memory:${memoryStatusColor(memory)}" type="button" data-memory-id="${esc(memory.id)}">
        <span class="memory-dot"></span>
        <span class="memory-copy">
          <span class="memory-title-line"><strong>${esc(memory.title)}</strong><span>${memory.status === 'attention' ? 'asks' : esc(memory.status)}</span></span>
          <span class="memory-text">${esc(memory.text)}</span>
          <span class="memory-meta"><span>${esc(memory.type)}</span><span>${esc(memory.project)}</span><span>${memory.confidence}%</span><span>${esc(memory.lastUsed)}</span></span>
        </span>
      </button>
    `).join('');
  }

  function renderMemoryDetail(memory) {
    if (!memory) {
      return '<div class="memory-detail-empty">Select a memory.</div>';
    }
    const attention = memory.status === 'attention';
    return `
      <div class="memory-detail-head">
        <div class="memory-detail-kicker"><span>${esc(memory.type)}</span><span>${memory.confidence}% confidence</span></div>
        <h2>${esc(memory.title)}</h2>
        <p>${esc(memory.text)}</p>
      </div>
      <div class="memory-detail-body">
        <div class="memory-props">
          <div><span>Status</span><strong>${attention ? 'Needs attention' : esc(memory.status)}</strong></div>
          <div><span>Project</span><strong>${esc(memory.project)}</strong></div>
          <div><span>Source</span><strong>${esc(memory.source)}</strong></div>
          <div><span>Last used</span><strong>${esc(memory.lastUsed)}</strong></div>
        </div>
        <section class="memory-evidence">
          <div class="memory-evidence-title">${attention ? 'Why ABC is asking' : 'Why ABC believes this'}</div>
          <p>${esc(memory.ask)}</p>
          ${memory.evidence.map(item => `<span>${esc(item)}</span>`).join('')}
        </section>
        <section class="memory-learning">
          <div class="memory-evidence-title">What ABC learns</div>
          <p>${esc(memory.learn)}</p>
        </section>
      </div>
      <div class="memory-detail-actions">
        <button type="button" data-memory-action="open">Open source</button>
        <button type="button" data-memory-action="scope">Scope</button>
        <button type="button" data-memory-action="pin">${memory.status === 'pinned' ? 'Unpin' : 'Pin'}</button>
        <button class="primary" type="button" data-memory-action="confirm">Confirm & teach</button>
        <button class="danger" type="button" data-memory-action="forget">Forget & learn</button>
      </div>
    `;
  }

  function renderMemoryWindow() {
    const items = visibleMemories();
    let selected = null;
    if (memoryState.detailOpen) {
      selected = items.find(memory => memory.id === memoryState.selectedId) || null;
      if (!selected) {
        memoryState.selectedId = null;
        memoryState.detailOpen = false;
      }
    }
    const types = [...new Set(memorySamples.map(memory => memory.type))];
    const sources = [...new Set(memorySamples.map(memory => memory.source))];
    const autopilotCount = memorySamples.filter(memory => ['confirmed', 'pinned', 'private'].includes(memory.status)).length;
    const learnedCount = memorySamples.filter(memory => memory.status === 'learned').length;
    const attentionCount = memorySamples.filter(memory => memory.status === 'attention').length;

    return `
      <div class="memory-window-layout">
        <aside class="memory-rail" aria-label="Memory filters">
          <section class="memory-autopilot">
            <div class="memory-autopilot-head"><span>Memory autopilot</span><span>local</span></div>
            <div class="memory-autopilot-ring" aria-label="${autopilotCount} memories handled automatically">${autopilotCount}</div>
            <div class="memory-autopilot-copy">ABC only asks when a memory is uncertain, risky, or needs your intent.</div>
            <div class="memory-autopilot-stats">
              <span><strong>${attentionCount}</strong> needs attention</span>
              <span><strong>${learnedCount}</strong> learned rules</span>
            </div>
          </section>
          <div class="memory-facet-section">
            <div class="memory-section-title"><span>Type</span><span>${esc(memoryState.type)}</span></div>
            <div class="memory-facet-stack">${renderMemoryFacet(types, memoryState.type, 'type', memoryTypeColors)}</div>
          </div>
          <div class="memory-facet-section">
            <div class="memory-section-title"><span>Source</span><span>${esc(memoryState.source)}</span></div>
            <div class="memory-facet-stack">${renderMemoryFacet(sources, memoryState.source, 'source')}</div>
          </div>
        </aside>
        <section class="memory-main" aria-label="Memory list">
          <div class="memory-toolbar">
            <label class="memory-search">
              <input type="search" value="${esc(memoryState.query)}" placeholder="find memory, source, project" data-memory-search>
            </label>
            <button class="memory-sort" type="button" data-memory-sort>${memoryState.sortAttentionFirst ? 'Attention first' : 'Confidence'}</button>
          </div>
          <div class="memory-tabs" aria-label="Memory views">${renderMemoryTabs()}</div>
          <div class="memory-list">${renderMemoryRows(items)}</div>
        </section>
        <aside class="memory-detail" aria-label="Memory detail">${renderMemoryDetail(selected)}</aside>
      </div>
    `;
  }

  function refreshMemoryWindow(win) {
    win.querySelector('.window-body').innerHTML = renderMemoryWindow();
    wireMemoryWindow(win);
  }

  function updateMemoryPanelState(win) {
    if (!win) return;
    win.classList.toggle('filters-collapsed', !memoryState.filtersOpen);
    win.classList.toggle('detail-collapsed', !memoryState.detailOpen);
    win.querySelectorAll('[data-memory-panel]').forEach(button => {
      const panel = button.dataset.memoryPanel;
      const active = panel === 'filters' ? memoryState.filtersOpen : memoryState.detailOpen;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
      button.title = (active ? 'Hide ' : 'Show ') + (panel === 'filters' ? 'filters' : 'details');
    });
  }

  function keepMemoryWindowInViewport(win) {
    if (!win || !memoryState.detailOpen || win.classList.contains('maximized')) return;
    requestAnimationFrame(() => {
      const rect = win.getBoundingClientRect();
      const margin = 18;
      if (rect.right <= window.innerWidth - margin) return;
      win.style.transform = 'none';
      win.style.left = Math.max(margin, window.innerWidth - margin - rect.width) + 'px';
      win.style.top = rect.top + 'px';
      win.style.width = rect.width + 'px';
      win.style.height = rect.height + 'px';
    });
  }

  function applyMemoryAction(memory, action) {
    if (!memory) return;
    if (action === 'confirm') {
      memory.status = 'learned';
      memory.confidence = Math.max(memory.confidence, 90);
      memory.lastUsed = 'now';
      buildState.textContent = 'V2 - Memory learned from confirmation';
      return;
    }
    if (action === 'forget') {
      memory.status = 'forgotten';
      memory.confidence = Math.max(42, memory.confidence - 22);
      memory.lastUsed = 'now';
      buildState.textContent = 'V2 - Memory learned from forget';
      return;
    }
    if (action === 'pin') {
      memory.status = memory.status === 'pinned' ? 'confirmed' : 'pinned';
      memory.lastUsed = 'now';
      buildState.textContent = 'V2 - Memory pin preference learned';
      return;
    }
    if (action === 'scope') {
      addContextNodge('Memory', memory.title);
      buildState.textContent = 'V2 - Memory scoped to chat';
      return;
    }
    buildState.textContent = 'V2 - Memory source focused';
  }

  function wireMemoryWindow(win) {
    updateMemoryPanelState(win);
    win.querySelectorAll('[data-memory-panel]').forEach(button => {
      if (button.dataset.memoryPanelReady) return;
      button.dataset.memoryPanelReady = 'true';
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        const panel = button.dataset.memoryPanel;
        if (panel === 'filters') memoryState.filtersOpen = !memoryState.filtersOpen;
        if (panel === 'detail') memoryState.detailOpen = !memoryState.detailOpen;
        updateMemoryPanelState(win);
        buildState.textContent = 'V2 - Memory ' + (panel === 'filters' ? 'filters' : 'details') + (button.classList.contains('active') ? ' shown' : ' hidden');
      });
    });
    win.querySelector('[data-memory-search]')?.addEventListener('input', event => {
      memoryState.query = event.target.value.trim();
      refreshMemoryWindow(win);
      win.querySelector('[data-memory-search]')?.focus();
    });
    win.querySelector('[data-memory-sort]')?.addEventListener('click', () => {
      memoryState.sortAttentionFirst = !memoryState.sortAttentionFirst;
      refreshMemoryWindow(win);
    });
    win.querySelectorAll('[data-memory-tab]').forEach(button => {
      button.addEventListener('click', () => {
        memoryState.tab = button.dataset.memoryTab || 'All';
        memoryState.selectedId = null;
        refreshMemoryWindow(win);
      });
    });
    win.querySelectorAll('[data-memory-type]').forEach(button => {
      button.addEventListener('click', () => {
        memoryState.type = button.dataset.memoryType || 'All';
        memoryState.selectedId = null;
        refreshMemoryWindow(win);
      });
    });
    win.querySelectorAll('[data-memory-source]').forEach(button => {
      button.addEventListener('click', () => {
        memoryState.source = button.dataset.memorySource || 'All';
        memoryState.selectedId = null;
        refreshMemoryWindow(win);
      });
    });
    win.querySelectorAll('[data-memory-id]').forEach(button => {
      button.addEventListener('click', () => {
        const selectedAgain = memoryState.detailOpen && memoryState.selectedId === button.dataset.memoryId;
        memoryState.selectedId = selectedAgain ? null : button.dataset.memoryId;
        memoryState.detailOpen = !selectedAgain;
        refreshMemoryWindow(win);
        keepMemoryWindowInViewport(win);
      });
    });
    win.querySelectorAll('[data-memory-action]').forEach(button => {
      button.addEventListener('click', () => {
        const memory = memorySamples.find(item => item.id === memoryState.selectedId);
        applyMemoryAction(memory, button.dataset.memoryAction);
        refreshMemoryWindow(win);
      });
    });
  }

  function openMemoryWindow() {
    setActiveWorkspace('knowledge');
    let win = document.getElementById('memory-window');
    if (!win) {
      win = document.createElement('article');
      win.className = 'floating-window memory-window active';
      win.id = 'memory-window';
      win.dataset.window = '';
      win.dataset.windowId = 'memory';
      win.dataset.windowCloseMode = 'remove';
      win.setAttribute('aria-label', 'Memory');
      win.innerHTML = `
        <header class="window-head" data-drag-handle>
          <div class="window-subtitle">knowledge</div>
          <div class="window-title">Memory</div>
          <div class="window-actions" aria-label="Window controls">
            <button class="window-control" data-window-min title="Minimize" aria-label="Minimize">-</button>
            <button class="window-control" data-window-max title="Maximize" aria-label="Maximize">&#9633;</button>
            <button class="window-control" data-window-close title="Close" aria-label="Close">x</button>
          </div>
        </header>
        <button class="memory-filter-handle active" type="button" data-memory-panel="filters" aria-pressed="true" aria-label="Toggle filters" title="Hide filters">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 6h16"></path>
            <path d="M7 12h10"></path>
            <path d="M10 18h4"></path>
          </svg>
        </button>
        <div class="window-body">${renderMemoryWindow()}</div>
      `;
      activeWorkspaceScreen().appendChild(win);
      prepareFloatingWindow(win);
      wireMemoryWindow(win);
    } else {
      setWindowMinimized(win, false);
      refreshMemoryWindow(win);
    }
    activateWindow(win);
    buildState.textContent = 'V2 - Memory autopilot';
    closeToolwheel();
  }

  function openKnowledgeGraphWindow() {
    setActiveWorkspace('knowledge');
    let win = document.getElementById('knowledge-graph-window');
    if (!win) {
      win = document.createElement('article');
      win.className = 'floating-window knowledge-graph-window active';
      win.id = 'knowledge-graph-window';
      win.dataset.window = '';
      win.dataset.windowId = 'knowledge-graph';
      win.dataset.windowCloseMode = 'remove';
      win.setAttribute('aria-label', 'Knowledge Graph');
      win.innerHTML = `
        <header class="window-head" data-drag-handle>
          <div class="window-subtitle">knowledge</div>
          <div class="window-title">Knowledge Graph</div>
          <div class="window-actions" aria-label="Window controls">
            <button class="window-control" data-window-min title="Minimize" aria-label="Minimize">-</button>
            <button class="window-control" data-window-max title="Maximize" aria-label="Maximize">&#9633;</button>
            <button class="window-control" data-window-close title="Close" aria-label="Close">x</button>
          </div>
        </header>
        <div class="window-body">${renderKnowledgeGraphWindow()}</div>
      `;
      activeWorkspaceScreen().appendChild(win);
      prepareFloatingWindow(win);
      wireKnowledgeGraphWindow(win);
    } else {
      setWindowMinimized(win, false);
      if (win.querySelector('[data-knowledge-graph-default]') && !win.querySelector('[data-knowledge-graph-default]').dataset.knowledgeGraphReady) {
        installDefaultKnowledgeGraphWindow();
      }
      requestAnimationFrame(() => drawKnowledgeGraph(win));
    }
    activateWindow(win);
    buildState.textContent = 'V2 - Knowledge Graph';
    closeToolwheel();
  }

  function openComposerContextMenu() {
    setActiveWorkspace('agent');
    setWindowMinimized(coreWindow, false);
    activateWindow(coreWindow);
    const shell = coreWindow.querySelector('.composer-shell');
    if (shell) setComposerMenuOpen(shell, true);
  }

  function closeHeaderMenus() {
    document.querySelectorAll('.workspace-group, .tools-cluster, .undo-cluster').forEach(group => {
      clearTimeout(group._menuHoverTimer);
      group.classList.remove('menu-hover', 'menu-open');
      group.querySelector('.workspace-tab, .tools-button, [data-global-undo]')?.setAttribute('aria-expanded', 'false');
    });
    setNotificationMenuOpen(false);
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
  }

  function setHeaderPopupOpen(group, open, options = {}) {
    if (!group) return;
    const trigger = group.querySelector('.workspace-tab, .tools-button, [data-global-undo]');
    clearTimeout(group._menuHoverTimer);

    if (open) {
      group.classList.add('menu-hover');
      if (options.pinned) group.classList.add('menu-open');
      trigger?.setAttribute('aria-expanded', 'true');
      return;
    }

    if (group.classList.contains('menu-open') && !options.force) return;
    group._menuHoverTimer = setTimeout(() => {
      group.classList.remove('menu-hover');
      if (options.force) group.classList.remove('menu-open');
      if (!group.classList.contains('menu-open')) {
        trigger?.setAttribute('aria-expanded', 'false');
      }
    }, 220);
  }

  function togglePinnedHeaderPopup(group) {
    if (!group) return;
    const wasPinned = group.classList.contains('menu-open');
    document.querySelectorAll('.workspace-group.menu-open, .tools-cluster.menu-open').forEach(item => {
      if (item !== group) setHeaderPopupOpen(item, false, { force: true });
    });
    if (wasPinned) {
      setHeaderPopupOpen(group, false, { force: true });
    } else {
      setHeaderPopupOpen(group, true, { pinned: true });
    }
  }

  function installHeaderPopupHover() {
    document.querySelectorAll('.workspace-group, .tools-cluster, .undo-cluster').forEach(group => {
      if (group.dataset.hoverPopupPrepared) return;
      group.dataset.hoverPopupPrepared = 'true';
      group.addEventListener('mouseenter', () => setHeaderPopupOpen(group, true));
      group.addEventListener('mouseleave', () => setHeaderPopupOpen(group, false));
      group.addEventListener('focusin', () => setHeaderPopupOpen(group, true));
      group.addEventListener('focusout', event => {
        if (!group.contains(event.relatedTarget)) setHeaderPopupOpen(group, false);
      });
    });
  }

  let hoverTooltip = null;
  let hoverTooltipTarget = null;
  let hoverTooltipTimer = null;

  function ensureHoverTooltip() {
    if (hoverTooltip) return hoverTooltip;
    hoverTooltip = document.createElement('div');
    hoverTooltip.className = 'hover-tooltip';
    hoverTooltip.setAttribute('role', 'tooltip');
    hoverTooltip.addEventListener('mouseenter', () => clearTimeout(hoverTooltipTimer));
    hoverTooltip.addEventListener('mouseleave', scheduleHoverTooltipHide);
    document.body.appendChild(hoverTooltip);
    return hoverTooltip;
  }

  function hoverTooltipText(target) {
    const title = target.getAttribute('title');
    if (title) {
      target.dataset.tooltip = title;
      target.removeAttribute('title');
    }
    return target.dataset.tooltip || target.getAttribute('aria-label') || '';
  }

  function positionHoverTooltip(target) {
    if (!hoverTooltip) return;
    const rect = target.getBoundingClientRect();
    const tooltipRect = hoverTooltip.getBoundingClientRect();
    const margin = 10;
    let left = rect.left + (rect.width / 2) - (tooltipRect.width / 2);
    let top = rect.bottom + 9;
    left = Math.max(margin, Math.min(window.innerWidth - tooltipRect.width - margin, left));
    if (top + tooltipRect.height > window.innerHeight - margin) {
      top = Math.max(margin, rect.top - tooltipRect.height - 9);
    }
    hoverTooltip.style.left = left + 'px';
    hoverTooltip.style.top = top + 'px';
  }

  function showHoverTooltip(target) {
    const text = hoverTooltipText(target);
    if (!text) return;
    clearTimeout(hoverTooltipTimer);
    const tooltip = ensureHoverTooltip();
    hoverTooltipTarget = target;
    tooltip.textContent = text;
    tooltip.classList.add('visible');
    tooltip.setAttribute('aria-hidden', 'false');
    requestAnimationFrame(() => positionHoverTooltip(target));
  }

  function scheduleHoverTooltipHide() {
    clearTimeout(hoverTooltipTimer);
    hoverTooltipTimer = setTimeout(() => {
      hoverTooltip?.classList.remove('visible');
      hoverTooltip?.setAttribute('aria-hidden', 'true');
      hoverTooltipTarget = null;
    }, 220);
  }

  function installHoverTooltips(root = document) {
    root.querySelectorAll('[title], [data-tooltip]').forEach(target => {
      if (target.closest('.header-dropdown, .history-actions-menu, .notification-menu')) return;
      if (target.dataset.tooltipPrepared) return;
      target.dataset.tooltipPrepared = 'true';
      hoverTooltipText(target);
      target.addEventListener('mouseenter', () => showHoverTooltip(target));
      target.addEventListener('mouseleave', scheduleHoverTooltipHide);
      target.addEventListener('focus', () => showHoverTooltip(target));
      target.addEventListener('blur', scheduleHoverTooltipHide);
    });
  }

  function showPlaceholderAction(label, workspaceId = activeWorkspaceId) {
    if (workspaceId) setActiveWorkspace(workspaceId);
    announceAction(label);
  }

  function handleHeaderAction(action) {
    if (!action) return;

    if (action === 'New Chat') {
      preparedChats++;
      createChatSpace();
      closeHeaderMenus();
      return;
    }

    if (action === 'Chat History') {
      setActiveWorkspace('agent');
      setWindowMinimized(coreWindow, false);
      activateWindow(coreWindow);
      setChatHistoryOpen(true);
      buildState.textContent = 'V2 - Chat history';
      closeHeaderMenus();
      return;
    }

    if (action === 'Run Task') {
      setActiveWorkspace('agent');
      setWindowMinimized(coreWindow, false);
      activateWindow(coreWindow);
      buildState.textContent = 'V2 - Ready for task';
      closeHeaderMenus();
      promptInput?.focus();
      return;
    }

    if (action === 'Attach Context') {
      openComposerContextMenu();
      buildState.textContent = 'V2 - Choose context';
      closeHeaderMenus();
      return;
    }

    if (action === 'Knowledge Graph') {
      openKnowledgeGraphWindow();
      closeHeaderMenus();
      return;
    }

    if (action === 'Search Knowledge' || action === 'Sources') {
      showPlaceholderAction(action === 'Search Knowledge' ? 'Search all' : 'Sources', 'knowledge');
      closeHeaderMenus();
      return;
    }

    if (action === 'Memory') {
      openMemoryWindow();
      closeHeaderMenus();
      return;
    }

    if (action === 'Projects Overview') {
      openProjectsOverview();
      closeHeaderMenus();
      return;
    }

    if (action === 'Roadmap List') {
      openRoadmapListWindow();
      closeHeaderMenus();
      return;
    }

    if (action === 'Todos') {
      openTodosWindow();
      closeHeaderMenus();
      return;
    }

    if (action === 'Decisions' || action === 'Create Plan') {
      showPlaceholderAction(action, 'planning');
      closeHeaderMenus();
      return;
    }

    if (action === 'Import' || action === 'Recent Imports' || action === 'Routing Rules' || action === 'Inbox Activity') {
      setActiveWorkspace('inbox');
      const inboxHome = document.querySelector('[data-workspace-screen="inbox"] .inbox-home-window');
      if (inboxHome) {
        setWindowMinimized(inboxHome, false);
        activateWindow(inboxHome);
      }
      buildState.textContent = 'V2 - ' + action;
      closeHeaderMenus();
      return;
    }

    if (action === 'Settings' || action === 'Window Rules') {
      openSettingsWindow({ query: action === 'Window Rules' ? 'window snap minimize restore' : '', advanced: action === 'Window Rules' });
      closeHeaderMenus();
      return;
    }

    if (action === 'Skills') {
      openSkillsWindow();
      closeHeaderMenus();
      return;
    }

    if (action === 'Hooks') {
      openSettingsWindow({ query: 'hooks triggers guardrails', advanced: true });
      closeHeaderMenus();
      return;
    }

    if (action === 'Command Search') {
      openToolwheel();
      closeHeaderMenus();
      return;
    }

    if (action === 'Terminal' || action === 'Cloud Control') {
      showPlaceholderAction(action + ' window');
      closeHeaderMenus();
      return;
    }

    showPlaceholderAction(action);
    closeHeaderMenus();
  }

  function handleToolwheelAction(item) {
    const action = item.dataset.action;
    const type = item.dataset.actionType || 'Instant';
    if (!action) return;

    if (action === 'Todos' || action === 'New Task') {
      openTodosWindow();
      return;
    }

    if (action === 'Overview' || action === 'Open Project' || action === 'Project Atlas' || action === 'Memory Debug' || action === 'Frontend V2' || action === 'Local Models') {
      openProjectsOverview();
      return;
    }

    if (action === 'Graph View') {
      openKnowledgeGraphWindow();
      return;
    }

    if (action === 'Memory') {
      openMemoryWindow();
      return;
    }

    if (action === 'Skills') {
      openSkillsWindow();
      return;
    }

    if (['Model', 'Appearance', 'Shortcuts', 'Voice', 'Settings More'].includes(action)) {
      const queries = {
        Model: 'model',
        Appearance: 'appearance theme background',
        Shortcuts: 'shortcuts keyboard',
        Voice: 'voice speech STT TTS',
        'Settings More': ''
      };
      openSettingsWindow({ query: queries[action], advanced: action === 'Settings More' });
      return;
    }

    if (action === 'New Chat') {
      preparedChats++;
      createChatSpace();
      return;
    }

    if (type === 'Attach') {
      const kind = item.dataset.nodgeKind || 'Context';
      addContextNodge(kind, action);
      announceAction(kind + ' attached');
      closeToolwheel();
      return;
    }

    if (type === 'Open') {
      announceAction(action + ' window');
      closeToolwheel();
      return;
    }

    if (type === 'More') {
      announceAction(action);
      return;
    }

    announceAction(action);
    closeToolwheel();
  }

  function installResizeHandles(root = document) {
    const dirs = ['n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw'];
    root.querySelectorAll('[data-window]').forEach(win => {
      if (win.querySelector('.resize-handle')) return;
      dirs.forEach(dir => {
        const handle = document.createElement('span');
        handle.className = 'resize-handle';
        handle.dataset.resizeDir = dir;
        win.appendChild(handle);
      });
    });
  }

  function storeWindowBounds(win) {
    if (win._restoreBounds) return;
    const rect = win.getBoundingClientRect();
    const bounds = viewportRectToWindowBounds(win, rect);
    win._restoreBounds = {
      left: bounds.left + 'px',
      top: bounds.top + 'px',
      width: bounds.width + 'px',
      height: bounds.height + 'px',
      transform: 'none'
    };
  }

  function restoreWindowBounds(win) {
    if (!win._restoreBounds) return;
    win.style.left = win._restoreBounds.left;
    win.style.top = win._restoreBounds.top;
    win.style.width = win._restoreBounds.width;
    win.style.height = win._restoreBounds.height;
    win.style.transform = win._restoreBounds.transform;
    win._restoreBounds = null;
  }

  function maximizedWindowBounds(win) {
    const parent = win.offsetParent || win.parentElement || document.body;
    const parentRect = parent.getBoundingClientRect();
    const origin = windowParentOrigin(win);
    const margin = 14;
    const left = Math.max(parentRect.left, 0) + margin;
    const top = Math.max(parentRect.top, 0) + margin;
    const right = Math.min(parentRect.right, window.innerWidth) - margin;
    const bottom = Math.min(parentRect.bottom, window.innerHeight) - margin;
    return {
      left: left - origin.left,
      top: top - origin.top,
      width: Math.max(340, right - left),
      height: Math.max(260, bottom - top)
    };
  }

  function syncMaximizeControl(control, maximized) {
    if (!control) return;
    control.classList.toggle('is-restore', maximized);
    control.dataset.tooltip = maximized ? 'Restore' : 'Maximize';
    control.removeAttribute('title');
    control.setAttribute('aria-label', maximized ? 'Restore' : 'Maximize');
    control.textContent = maximized ? 'Restore' : 'Maximize';
  }

  function maximizeWindow(win) {
    storeWindowBounds(win);
    const bounds = maximizedWindowBounds(win);
    win.classList.add('maximized');
    win.style.left = bounds.left + 'px';
    win.style.top = bounds.top + 'px';
    win.style.width = bounds.width + 'px';
    win.style.height = bounds.height + 'px';
    win.style.transform = 'none';
    syncMaximizeControl(win.querySelector('[data-window-max]'), true);
    persistWindowState(win);
  }

  function restoreMaximizedWindow(win) {
    win.classList.remove('maximized');
    restoreWindowBounds(win);
    syncMaximizeControl(win.querySelector('[data-window-max]'), false);
    persistWindowState(win);
  }

  function refreshMaximizedWindows() {
    document.querySelectorAll('[data-window].maximized').forEach(win => {
      const bounds = maximizedWindowBounds(win);
      win.style.left = bounds.left + 'px';
      win.style.top = bounds.top + 'px';
      win.style.width = bounds.width + 'px';
      win.style.height = bounds.height + 'px';
      win.style.transform = 'none';
      persistWindowState(win);
    });
  }

  function autosizePrompt(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.max(96, textarea.scrollHeight) + 'px';
    const composer = textarea.closest('.composer');
    if (composer) {
      composer.style.minHeight = textarea.style.height;
    }
  }

  function rectsOverlap(startA, endA, startB, endB) {
    return Math.max(0, Math.min(endA, endB) - Math.max(startA, startB));
  }

  function workspaceViewportRect(win) {
    const parent = win.offsetParent || win.parentElement || document.body;
    const rect = parent.getBoundingClientRect();
    return {
      left: rect.left,
      top: rect.top,
      right: rect.right,
      bottom: rect.bottom,
      width: rect.width,
      height: rect.height
    };
  }

  function clampAxisPosition(value, size, min, max, startMargin, endMargin) {
    return Math.max(min + startMargin, Math.min(max - size - endMargin, value));
  }

  function buildSnapTarget(target, lockedAxis, dragWin) {
    const next = { ...target };
    const workspaceRect = workspaceViewportRect(dragWin);
    const margin = windowBoundaryMargin;
    if (lockedAxis === 'x') {
      next.top = clampAxisPosition(next.top, next.height, workspaceRect.top, workspaceRect.bottom, margin.top, margin.bottom);
    } else {
      next.left = clampAxisPosition(next.left, next.width, workspaceRect.left, workspaceRect.right, margin.left, margin.right);
    }
    return next;
  }

  function snapTargetFits(target, dragWin) {
    const margin = windowBoundaryMargin;
    const workspaceRect = workspaceViewportRect(dragWin);
    return target.left >= workspaceRect.left + margin.left
      && target.top >= workspaceRect.top + margin.top
      && target.left + target.width <= workspaceRect.right - margin.right
      && target.top + target.height <= workspaceRect.bottom - margin.bottom;
  }

  function snapPreviewElement() {
    let preview = document.getElementById('window-snap-preview');
    if (!preview) {
      preview = document.createElement('div');
      preview.id = 'window-snap-preview';
      preview.className = 'window-snap-preview';
      preview.innerHTML = '<span>SNAP</span>';
      stage.appendChild(preview);
    }
    return preview;
  }

  function hideSnapPreview() {
    const preview = document.getElementById('window-snap-preview');
    preview?.classList.remove('visible', 'vertical', 'horizontal');
  }

  function showSnapPreview(candidate) {
    const preview = snapPreviewElement();
    preview.classList.toggle('vertical', candidate.orientation === 'vertical');
    preview.classList.toggle('horizontal', candidate.orientation === 'horizontal');
    preview.style.left = candidate.preview.left + 'px';
    preview.style.top = candidate.preview.top + 'px';
    preview.style.width = candidate.preview.width + 'px';
    preview.style.height = candidate.preview.height + 'px';
    const label = preview.querySelector('span');
    if (label) label.textContent = candidate.label || 'SNAP';
    preview.classList.add('visible');
  }

  function findSnapCandidate(dragWin, rect) {
    const threshold = 62;
    const gap = 4;
    const alignThreshold = 96;
    const workspace = dragWin.closest('[data-workspace-screen]');
    const current = {
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height,
      right: rect.left + rect.width,
      bottom: rect.top + rect.height
    };
    let best = null;

    document.querySelectorAll('[data-window]').forEach(other => {
      if (other === dragWin || other.classList.contains('minimized') || other.classList.contains('maximized')) return;
      if (other.closest('[data-workspace-screen]') !== workspace) return;
      const otherRect = other.getBoundingClientRect();
      const verticalOverlap = rectsOverlap(current.top, current.bottom, otherRect.top, otherRect.bottom);
      const horizontalOverlap = rectsOverlap(current.left, current.right, otherRect.left, otherRect.right);
      const minVerticalOverlap = Math.min(52, Math.max(28, Math.min(current.height, otherRect.height) * 0.18));
      const minHorizontalOverlap = Math.min(52, Math.max(28, Math.min(current.width, otherRect.width) * 0.18));
      const topAlignment = Math.abs(current.top - otherRect.top);
      const bottomAlignment = Math.abs(current.bottom - otherRect.bottom);
      const leftAlignment = Math.abs(current.left - otherRect.left);
      const rightAlignment = Math.abs(current.right - otherRect.right);

      const rightTarget = buildSnapTarget({
        left: otherRect.right + gap,
        top: topAlignment <= alignThreshold ? otherRect.top : bottomAlignment <= alignThreshold ? otherRect.bottom - current.height : current.top,
        width: current.width,
        height: current.height
      }, 'x', dragWin);
      const leftTarget = buildSnapTarget({
        left: otherRect.left - gap - current.width,
        top: topAlignment <= alignThreshold ? otherRect.top : bottomAlignment <= alignThreshold ? otherRect.bottom - current.height : current.top,
        width: current.width,
        height: current.height
      }, 'x', dragWin);
      const bottomTarget = buildSnapTarget({
        left: leftAlignment <= alignThreshold ? otherRect.left : rightAlignment <= alignThreshold ? otherRect.right - current.width : current.left,
        top: otherRect.bottom + gap,
        width: current.width,
        height: current.height
      }, 'y', dragWin);
      const topTarget = buildSnapTarget({
        left: leftAlignment <= alignThreshold ? otherRect.left : rightAlignment <= alignThreshold ? otherRect.right - current.width : current.left,
        top: otherRect.top - gap - current.height,
        width: current.width,
        height: current.height
      }, 'y', dragWin);

      const candidates = [
        {
          side: 'right',
          distance: Math.abs(current.left - (otherRect.right + gap)),
          alignment: Math.min(topAlignment, bottomAlignment),
          valid: verticalOverlap >= minVerticalOverlap && snapTargetFits(rightTarget, dragWin),
          target: rightTarget,
          preview: {
            left: otherRect.right + Math.floor(gap / 2),
            top: Math.max(current.top, otherRect.top),
            width: 2,
            height: Math.max(86, Math.min(current.bottom, otherRect.bottom) - Math.max(current.top, otherRect.top))
          },
          orientation: 'vertical'
        },
        {
          side: 'left',
          distance: Math.abs(current.right - (otherRect.left - gap)),
          alignment: Math.min(topAlignment, bottomAlignment),
          valid: verticalOverlap >= minVerticalOverlap && snapTargetFits(leftTarget, dragWin),
          target: leftTarget,
          preview: {
            left: otherRect.left - Math.floor(gap / 2),
            top: Math.max(current.top, otherRect.top),
            width: 2,
            height: Math.max(86, Math.min(current.bottom, otherRect.bottom) - Math.max(current.top, otherRect.top))
          },
          orientation: 'vertical'
        },
        {
          side: 'bottom',
          distance: Math.abs(current.top - (otherRect.bottom + gap)),
          alignment: Math.min(leftAlignment, rightAlignment),
          valid: horizontalOverlap >= minHorizontalOverlap && snapTargetFits(bottomTarget, dragWin),
          target: bottomTarget,
          preview: {
            left: Math.max(current.left, otherRect.left),
            top: otherRect.bottom + Math.floor(gap / 2),
            width: Math.max(86, Math.min(current.right, otherRect.right) - Math.max(current.left, otherRect.left)),
            height: 2
          },
          orientation: 'horizontal'
        },
        {
          side: 'top',
          distance: Math.abs(current.bottom - (otherRect.top - gap)),
          alignment: Math.min(leftAlignment, rightAlignment),
          valid: horizontalOverlap >= minHorizontalOverlap && snapTargetFits(topTarget, dragWin),
          target: topTarget,
          preview: {
            left: Math.max(current.left, otherRect.left),
            top: otherRect.top - Math.floor(gap / 2),
            width: Math.max(86, Math.min(current.right, otherRect.right) - Math.max(current.left, otherRect.left)),
            height: 2
          },
          orientation: 'horizontal'
        }
      ];

      candidates.forEach(candidate => {
        if (!candidate.valid || candidate.distance > threshold) return;
        candidate.score = candidate.distance + Math.min(candidate.alignment, alignThreshold) * 0.18;
        if (!best || candidate.score < best.score) {
          best = candidate;
        }
      });
    });

    return best;
  }

  function findResizeSnapCandidate(win, dir, bounds) {
    const threshold = 18;
    const gap = 4;
    const minW = 340;
    const minH = 260;
    const workspace = win.closest('[data-workspace-screen]');
    const current = windowBoundsToViewportRect(win, sanitizeWindowBounds(win, bounds));
    const candidates = [];

    function addCandidate(side, targetValue, label = 'SNAP') {
      const target = { ...current };
      let distance = Infinity;
      let orientation = 'vertical';
      let preview = null;

      if (side === 'right' && dir.includes('e')) {
        distance = Math.abs(current.right - targetValue);
        target.width = Math.max(minW, targetValue - current.left);
        preview = { left: targetValue, top: current.top, width: 2, height: current.height };
      } else if (side === 'left' && dir.includes('w')) {
        distance = Math.abs(current.left - targetValue);
        target.left = Math.min(current.right - minW, targetValue);
        target.width = Math.max(minW, current.right - target.left);
        preview = { left: target.left, top: current.top, width: 2, height: current.height };
      } else if (side === 'bottom' && dir.includes('s')) {
        distance = Math.abs(current.bottom - targetValue);
        target.height = Math.max(minH, targetValue - current.top);
        orientation = 'horizontal';
        preview = { left: current.left, top: targetValue, width: current.width, height: 2 };
      } else if (side === 'top' && dir.includes('n')) {
        distance = Math.abs(current.top - targetValue);
        target.top = Math.min(current.bottom - minH, targetValue);
        target.height = Math.max(minH, current.bottom - target.top);
        orientation = 'horizontal';
        preview = { left: current.left, top: target.top, width: current.width, height: 2 };
      }

      if (!preview || distance > threshold || !snapTargetFits(target, win)) return;
      candidates.push({
        side: 'resize-' + side,
        distance,
        score: distance,
        target,
        preview,
        orientation,
        label
      });
    }

    document.querySelectorAll('[data-window]').forEach(other => {
      if (other === win || other.classList.contains('minimized') || other.classList.contains('maximized')) return;
      if (other.closest('[data-workspace-screen]') !== workspace) return;
      const otherRect = other.getBoundingClientRect();
      const verticalOverlap = rectsOverlap(current.top, current.bottom, otherRect.top, otherRect.bottom);
      const horizontalOverlap = rectsOverlap(current.left, current.right, otherRect.left, otherRect.right);
      const minVerticalOverlap = Math.min(56, Math.max(28, Math.min(current.height, otherRect.height) * 0.2));
      const minHorizontalOverlap = Math.min(56, Math.max(28, Math.min(current.width, otherRect.width) * 0.2));

      if (verticalOverlap >= minVerticalOverlap) {
        addCandidate('right', otherRect.left - gap);
        addCandidate('right', otherRect.right);
        addCandidate('left', otherRect.right + gap);
        addCandidate('left', otherRect.left);
      }

      if (horizontalOverlap >= minHorizontalOverlap) {
        addCandidate('bottom', otherRect.top - gap);
        addCandidate('bottom', otherRect.bottom);
        addCandidate('top', otherRect.bottom + gap);
        addCandidate('top', otherRect.top);
      }
    });

    return candidates.sort((a, b) => a.score - b.score)[0] || null;
  }

  function applySnapCandidate(win, candidate) {
    if (!candidate) return;
    const bounds = viewportRectToWindowBounds(win, candidate.target);
    setWindowBounds(win, bounds);
    persistWindowState(win);
    buildState.textContent = 'V2 - Window snapped';
  }

  function setInboxOpen(open, reason = '') {
    if (!universalInbox) return;
    stage.classList.remove('inbox-open');
    universalInbox.setAttribute('aria-hidden', 'true');
    if (open && reason) buildState.textContent = 'V2 - Inbox: ' + reason;
  }

  function isInboxFileDrag(event) {
    const transfer = event.dataTransfer;
    if (!transfer) return false;
    const types = Array.from(transfer.types || []);
    return types.includes('Files') || transfer.files?.length > 0;
  }

  function setInboxDropActive(active, reason = '') {
    clearTimeout(inboxCloseTimer);
    stage.classList.toggle('inbox-dragging', active);
    if (universalInbox) universalInbox.setAttribute('aria-hidden', 'true');

    if (active) {
      setActiveWorkspace('inbox');
      const inboxHome = document.querySelector('[data-workspace-screen="inbox"] .inbox-home-window');
      if (inboxHome) {
        setWindowMinimized(inboxHome, false);
        activateWindow(inboxHome);
      }
      if (reason) buildState.textContent = 'V2 - Inbox: ' + reason;
    }
  }

  function scheduleInboxClose(delay = 420) {
    clearTimeout(inboxCloseTimer);
    inboxCloseTimer = setTimeout(() => {
      inboxDragDepth = 0;
      setInboxDropActive(false);
    }, delay);
  }

  function installUniversalInbox() {
    if (universalInbox) universalInbox.setAttribute('aria-hidden', 'true');
    if (universalInboxTrigger) universalInboxTrigger.setAttribute('aria-hidden', 'true');

    document.addEventListener('dragenter', event => {
      if (!isInboxFileDrag(event)) return;
      inboxDragDepth += 1;
      setInboxDropActive(true, 'drop file here');
      event.preventDefault();
    });

    document.addEventListener('dragover', event => {
      if (!isInboxFileDrag(event)) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
    });

    document.addEventListener('dragleave', event => {
      if (!isInboxFileDrag(event) && inboxDragDepth <= 0) return;
      inboxDragDepth = Math.max(0, inboxDragDepth - 1);
      if (inboxDragDepth === 0) {
        scheduleInboxClose();
      }
    });

    document.addEventListener('drop', event => {
      if (!isInboxFileDrag(event)) return;
      event.preventDefault();
      const files = event.dataTransfer?.files?.length || 0;
      const itemCount = files || event.dataTransfer?.items?.length || 1;
      inboxDragDepth = 0;
      setInboxDropActive(true, itemCount + ' item' + (itemCount === 1 ? '' : 's') + ' added');
      scheduleInboxClose(900);
    });
  }

  function prepareFloatingWindow(win, index = document.querySelectorAll('[data-window]').length) {
    ensureWindowId(win, index);
    installResizeHandles(win.parentElement || document);
    restorePersistedWindowState(win);
    syncMaximizeControl(win.querySelector('[data-window-max]'), win.classList.contains('maximized'));
    installHoverTooltips(win);

    if (!win.dataset.windowPrepared) {
      win.addEventListener('pointerdown', event => {
        if (event.windowSelectionHandled) return;
        prepareWindowSelection(win, event);
      });
      win.dataset.windowPrepared = 'true';
    }

    win.querySelectorAll('[data-drag-handle]').forEach(handle => {
      if (handle.dataset.dragPrepared) return;
      handle.dataset.dragPrepared = 'true';
      handle.addEventListener('pointerdown', event => {
        if (event.target.closest('button, textarea, input, .chat-title, .chat-title-editor, .resize-handle')) return;
        const dragWin = handle.closest('[data-window]');
        if (!dragWin || dragWin.classList.contains('maximized')) return;
        event.windowSelectionHandled = true;
        prepareWindowSelection(dragWin, event);
        const rect = dragWin.getBoundingClientRect();
        const startX = event.clientX;
        const startY = event.clientY;
        const offsetX = startX - rect.left;
        const offsetY = startY - rect.top;
        const dragGroup = activeDragGroup(dragWin);
        const groupBounds = prepareGroupDrag(dragGroup);
        const isGroupDrag = dragGroup.length > 1;
        handle.setPointerCapture(event.pointerId);
        let snapCandidate = null;

        function move(e) {
          const dx = e.clientX - startX;
          const dy = e.clientY - startY;
          const clamped = clampDragDelta(groupBounds, dx, dy);
          let nextRect = null;
          groupBounds.forEach(item => {
            const nextBounds = {
              left: item.left + clamped.dx,
              top: item.top + clamped.dy,
              width: item.width,
              height: item.height
            };
            item.win.style.left = nextBounds.left + 'px';
            item.win.style.top = nextBounds.top + 'px';
            if (item.win === dragWin) {
              nextRect = windowBoundsToViewportRect(dragWin, nextBounds);
            }
          });
          snapCandidate = isGroupDrag ? null : findSnapCandidate(dragWin, nextRect);
          if (!isGroupDrag && snapCandidate) {
            showSnapPreview(snapCandidate);
          } else {
            hideSnapPreview();
          }
        }

        function up() {
          handle.releasePointerCapture(event.pointerId);
          document.removeEventListener('pointermove', move);
          document.removeEventListener('pointerup', up);
          if (isGroupDrag) {
            buildState.textContent = 'V2 - ' + dragGroup.length + ' windows moved';
            dragGroup.forEach(win => {
              setWindowBounds(win, currentWindowBounds(win));
              persistWindowState(win);
            });
          } else {
            applySnapCandidate(dragWin, snapCandidate);
            if (!snapCandidate) {
              setWindowBounds(dragWin, currentWindowBounds(dragWin));
              persistWindowState(dragWin);
            }
          }
          hideSnapPreview();
        }

        document.addEventListener('pointermove', move);
        document.addEventListener('pointerup', up);
      });
    });
  }

  function installWindowInteractions() {
    document.querySelectorAll('[data-window]').forEach((win, index) => {
      prepareFloatingWindow(win, index);
    });

    document.addEventListener('pointerdown', event => {
      const handle = event.target.closest('.resize-handle');
      if (!handle) return;
      const win = handle.closest('[data-window]');
      if (!win || win.classList.contains('maximized')) return;
      event.preventDefault();
      activateWindow(win);
      const dir = handle.dataset.resizeDir;
      const rect = win.getBoundingClientRect();
      const startX = event.clientX;
      const startY = event.clientY;
      const minW = 340;
      const minH = 260;
      const bounds = viewportRectToWindowBounds(win, rect);
      win.style.transform = 'none';
      win.style.left = bounds.left + 'px';
      win.style.top = bounds.top + 'px';
      win.style.width = bounds.width + 'px';
      win.style.height = bounds.height + 'px';
      handle.setPointerCapture(event.pointerId);
      let snapCandidate = null;

      function move(e) {
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        let left = bounds.left;
        let top = bounds.top;
        let width = bounds.width;
        let height = bounds.height;

        if (dir.includes('e')) width = Math.max(minW, bounds.width + dx);
        if (dir.includes('s')) height = Math.max(minH, bounds.height + dy);
        if (dir.includes('w')) {
          width = Math.max(minW, bounds.width - dx);
          left = bounds.left + bounds.width - width;
        }
        if (dir.includes('n')) {
          height = Math.max(minH, bounds.height - dy);
          top = bounds.top + bounds.height - height;
        }

        const nextBounds = { left, top, width, height };
        setWindowBounds(win, nextBounds);
        snapCandidate = findResizeSnapCandidate(win, dir, nextBounds);
        if (snapCandidate) {
          showSnapPreview(snapCandidate);
        } else {
          hideSnapPreview();
        }
      }

      function up() {
        handle.releasePointerCapture(event.pointerId);
        document.removeEventListener('pointermove', move);
        document.removeEventListener('pointerup', up);
        applySnapCandidate(win, snapCandidate);
        if (!snapCandidate) {
          setWindowBounds(win, currentWindowBounds(win));
          persistWindowState(win);
        }
        hideSnapPreview();
      }

      document.addEventListener('pointermove', move);
      document.addEventListener('pointerup', up);
    });

    document.addEventListener('click', event => {
      const control = event.target.closest('[data-window-min], [data-window-max], [data-window-close]');
      if (!control) return;
      event.preventDefault();
      event.stopPropagation();
      const win = control.closest('[data-window]');
      if (!win) return;
      activateWindow(win);

      if (control.matches('[data-window-min]')) {
        setWindowMinimized(win, !win.classList.contains('minimized'));
        return;
      }

      if (control.matches('[data-window-close]')) {
        if (win.dataset.windowCloseMode === 'remove') {
          removeDockBubble(win);
          clearPersistedWindowState(win);
          win.remove();
          renderOpenDocumentContextSuggestions();
          buildState.textContent = 'V2 - Window closed';
          return;
        }
        setWindowMinimized(win, true);
        return;
      }

      if (control.matches('[data-window-max]')) {
        const shouldMaximize = !win.classList.contains('maximized');
        if (shouldMaximize) {
          maximizeWindow(win);
        } else {
          restoreMaximizedWindow(win);
        }
      }
    });

    document.querySelectorAll('.prompt-input').forEach(textarea => {
      autosizePrompt(textarea);
      textarea.addEventListener('input', () => {
        autosizePrompt(textarea);
        saveActiveChatSpace();
      });
    });

    chatTitle.addEventListener('dblclick', event => {
      event.preventDefault();
      event.stopPropagation();
      startTitleRename();
    });

    chatTitleEditor?.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        commitTitleRename();
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        cancelTitleRename();
      }
    });

    chatTitleEditor?.addEventListener('blur', commitTitleRename);

    chatHistoryButton?.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      setChatHistoryOpen(chatHistoryPanel?.hidden);
    });

    chatHistorySearch?.addEventListener('input', renderChatHistory);

    chatHistoryList?.addEventListener('click', event => {
      const item = event.target.closest('[data-chat-history-index]');
      if (!item) return;
      const index = Number(item.dataset.chatHistoryIndex);
      if (index >= 0) openChatSpace(index);
    });

    composerModelButton?.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      if (composerModelChooser?.hidden) return;
      composerModelChooser.classList.toggle('open');
      composerModelButton.setAttribute('aria-expanded', String(composerModelChooser.classList.contains('open')));
    });

    composerModelMenu?.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      const row = event.target.closest('[data-model-name]');
      if (row) selectChatModel(row);
    });

    coreWindow.querySelector('.send-btn')?.addEventListener('click', event => {
      if (sendPromptDemo()) event.preventDefault();
    });

    document.addEventListener('click', event => {
      const suggestion = event.target.closest('[data-context-suggestion="true"]');
      if (suggestion) {
        event.preventDefault();
        event.stopPropagation();
        pinDocumentContextFromSuggestion(suggestion);
        return;
      }

      const path = documentPathFromTarget(event.target);
      if (!path) return;
      event.preventDefault();
      event.stopPropagation();
      openDocumentViewer(path);
    });

    document.addEventListener('keydown', event => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      const suggestion = event.target.closest('[data-context-suggestion="true"]');
      if (suggestion) {
        event.preventDefault();
        event.stopPropagation();
        pinDocumentContextFromSuggestion(suggestion);
        return;
      }

      const path = documentPathFromTarget(event.target);
      if (!path) return;
      event.preventDefault();
      openDocumentViewer(path);
    });

    messages.addEventListener('click', event => {
      const toggle = event.target.closest('[data-work-run-toggle]');
      if (!toggle) return;
      const run = toggle.closest('[data-work-run]');
      run?.classList.toggle('collapsed');
      syncWorkRuns();
      saveActiveChatSpace();
    });

    messages.addEventListener('keydown', event => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      const toggle = event.target.closest('[data-work-run-toggle]');
      if (!toggle) return;
      event.preventDefault();
      const run = toggle.closest('[data-work-run]');
      run?.classList.toggle('collapsed');
      syncWorkRuns();
      saveActiveChatSpace();
    });

    document.getElementById('window-dock')?.addEventListener('click', event => {
      const bubble = event.target.closest('[data-restore-window]');
      if (!bubble) return;
      const win = document.querySelector('[data-window-id="' + bubble.dataset.restoreWindow + '"]');
      if (!win) return;
      setWindowMinimized(win, false);
    });

    document.querySelectorAll('.composer-menu-button').forEach(button => {
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        const shell = button.closest('.composer-shell');
        setComposerMenuOpen(shell, !shell.classList.contains('menu-open'));
      });
    });

    document.querySelectorAll('.composer-tool').forEach(tool => {
      tool.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        const action = tool.dataset.composerTool || tool.textContent.trim();
        if (tool.dataset.composerType === 'Attach') {
          const kind = tool.dataset.nodgeKind || 'Context';
          addContextNodge(kind, action);
          announceAction(kind + ' attached');
          setComposerMenuOpen(tool.closest('.composer-shell'), false);
          return;
        }
        if (action === 'Skills') {
          openSkillsWindow();
          setComposerMenuOpen(tool.closest('.composer-shell'), false);
          return;
        }
        announceAction(action);
        setComposerMenuOpen(tool.closest('.composer-shell'), false);
      });
    });

    document.querySelectorAll('[data-mode-option]').forEach(button => {
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        setWorkspaceMode(button.dataset.modeOption);
      });
    });

    document.querySelectorAll('[data-voice-toggle]').forEach(button => {
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        setVoiceInputMode(button.getAttribute('aria-pressed') !== 'true');
      });
    });

    document.querySelectorAll('[data-repo-commit-request]').forEach(button => {
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        buildState.textContent = 'V2 - Clean commit request prepared';
      });
    });

    document.querySelectorAll('[data-open-code-changes]').forEach(button => {
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        openDocumentViewer('repo://code-changes');
      });
    });

    document.querySelector('[data-notifications-toggle]')?.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      const open = !notificationRoot?.classList.contains('menu-open');
      setNotificationMenuOpen(open);
      buildState.textContent = open ? 'V2 - Notifications' : 'V2 - ' + (workspaceLabels[activeWorkspaceId] || 'Active') + ' workspace';
    });

    notificationMenu?.addEventListener('click', event => {
      const item = event.target.closest('[data-notification-target]');
      if (!item) return;
      event.preventDefault();
      event.stopPropagation();
      openNotificationItem(item);
    });

    privacyToggle?.addEventListener('click', () => {
      const active = privacyToggle.getAttribute('aria-pressed') !== 'true';
      privacyToggle.setAttribute('aria-pressed', String(active));
      privacyToggle.setAttribute('aria-label', active ? 'Secure mode on' : 'Secure mode off');
      privacyToggle.dataset.tooltip = active ? 'Secure mode on' : 'Secure mode off';
      stage.classList.toggle('privacy-on', active);
      const secureCluster = privacyToggle.closest('.secure-cluster');
      secureCluster?.classList.toggle('secure-on', active);
      const secureHologram = secureCluster?.querySelector('.secure-hologram');
      if (secureHologram) {
        secureHologram.style.opacity = active ? '1' : '';
        secureHologram.style.transform = active ? 'translateY(0) scale(1)' : '';
      }
      buildState.textContent = active ? 'V2 - Secure mode active' : 'V2 - ' + (workspaceLabels[activeWorkspaceId] || 'Active') + ' workspace';
    });

    document.querySelector('[data-global-undo]')?.addEventListener('click', () => {
      buildState.textContent = 'V2 - Undo menu ready';
    });

    document.querySelector('[data-global-redo]')?.addEventListener('click', () => {
      buildState.textContent = 'V2 - Redo ready';
    });

    contextNodges.addEventListener('click', event => {
      const suggestion = event.target.closest('[data-context-suggestion="true"]');
      if (suggestion) {
        event.preventDefault();
        pinDocumentContextFromSuggestion(suggestion);
        return;
      }

      const remove = event.target.closest('.context-nodge-remove');
      if (!remove) return;
      event.preventDefault();
      const nodge = remove.closest('.context-nodge');
      nodge?.remove();
      const current = chatSpaces[activeChatIndex];
      if (current) current.contexts = serializeContextNodges();
      renderOpenDocumentContextSuggestions();
      contextNodges.hidden = !contextNodges.children.length;
    });

    document.addEventListener('click', event => {
      if (!event.target.closest('.workspace-group, .tools-cluster')) {
        document.querySelectorAll('.workspace-group.menu-open, .tools-cluster.menu-open').forEach(group => {
          setHeaderPopupOpen(group, false, { force: true });
        });
      }
      if (notificationRoot && !notificationRoot.contains(event.target)) {
        setNotificationMenuOpen(false);
      }
      document.querySelectorAll('.composer-shell.menu-open').forEach(shell => {
        if (!shell.contains(event.target)) {
          setComposerMenuOpen(shell, false);
        }
      });
      if (composerModelChooser && !composerModelChooser.contains(event.target)) {
        composerModelChooser.classList.remove('open');
        composerModelButton?.setAttribute('aria-expanded', 'false');
      }
    });

    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      closeHeaderMenus();
      setNotificationMenuOpen(false);
      document.querySelectorAll('.composer-shell.menu-open').forEach(shell => {
        setComposerMenuOpen(shell, false);
      });
    });

    workspaceTabs?.addEventListener('click', event => {
      const tab = event.target.closest('[data-workspace-target]');
      if (!tab) return;
      event.preventDefault();
      const group = tab.closest('.workspace-group');
      if (tab.dataset.workspaceTarget === activeWorkspaceId) {
        togglePinnedHeaderPopup(group);
        return;
      }
      closeHeaderMenus();
      setActiveWorkspace(tab.dataset.workspaceTarget);
    });

    brandHomeButton?.addEventListener('click', event => {
      event.preventDefault();
      setActiveWorkspace('agent');
      setWindowMinimized(coreWindow, false);
      activateWindow(coreWindow);
      buildState.textContent = 'V2 - Agent home';
    });

    document.querySelector('.tools-cluster .tools-button')?.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      togglePinnedHeaderPopup(event.currentTarget.closest('.tools-cluster'));
    });

    installHeaderPopupHover();
    installHoverTooltips();

    document.querySelectorAll('[data-header-action]').forEach(button => {
      button.addEventListener('click', event => {
        event.stopPropagation();
        handleHeaderAction(button.dataset.headerAction);
      });
    });
  }

  stage.addEventListener('contextmenu', event => {
    event.preventDefault();
    toolwheel.classList.contains('open') ? closeToolwheel() : openToolwheel(event);
  });

  toolwheel.addEventListener('mousemove', event => {
    if (!toolwheel.classList.contains('open')) return;
    if (toolwheel.classList.contains('suppress-core-menu')) {
      if (!wheelOpenPointer) {
        toolwheel.classList.remove('suppress-core-menu');
      } else {
        const distance = Math.hypot(event.clientX - wheelOpenPointer.x, event.clientY - wheelOpenPointer.y);
        if (distance > 14) toolwheel.classList.remove('suppress-core-menu');
      }
    }
    updateWheelArrow(event);
  });

  wheelCore.addEventListener('mouseenter', () => {
    if (toolwheel.classList.contains('suppress-core-menu')) return;
    clearTimeout(coreMenuTimer);
    toolwheel.classList.add('core-new-open');
    coreNewTree.classList.add('is-open');
  });

  wheelCore.addEventListener('mouseleave', () => {
    coreMenuTimer = setTimeout(() => {
      if (!coreNewTree.matches(':hover')) {
        toolwheel.classList.remove('core-new-open');
        coreNewTree.classList.remove('is-open');
      }
    }, 220);
  });

  coreNewTree.addEventListener('mouseenter', () => {
    clearTimeout(coreMenuTimer);
    coreNewTree.classList.add('is-open');
  });

  coreNewTree.addEventListener('mouseleave', () => {
    toolwheel.classList.remove('core-new-open');
    coreNewTree.classList.remove('is-open');
  });

  wheelCore.addEventListener('click', event => {
    event.preventDefault();
    preparedChats++;
    createChatSpace();
  });

  toolwheel.querySelectorAll('.wheel-node').forEach((node, index) => {
    node.addEventListener('mouseenter', () => {
      toolwheel.classList.remove('suppress-core-menu');
      toolwheel.classList.remove('core-new-open');
      coreNewTree.classList.remove('is-open');
      focusWheelNode(index);
    });

    node.addEventListener('focus', () => {
      toolwheel.classList.remove('core-new-open');
      coreNewTree.classList.remove('is-open');
      focusWheelNode(index);
    });

    node.addEventListener('click', () => {
      if (node.dataset.node === 'Projects') {
        openProjectsOverview();
        return;
      }
      if (node.dataset.node === 'Knowledge') {
        openKnowledgeGraphWindow();
        return;
      }
      if (node.dataset.node === 'Settings') {
        openSettingsWindow({ query: '', advanced: false });
        return;
      }
      announceAction(node.dataset.node);
    });

    node.addEventListener('keydown', event => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      if (node.dataset.node === 'Projects') {
        openProjectsOverview();
        return;
      }
      if (node.dataset.node === 'Knowledge') {
        openKnowledgeGraphWindow();
        return;
      }
      if (node.dataset.node === 'Settings') {
        openSettingsWindow({ query: '', advanced: false });
        return;
      }
      announceAction(node.dataset.node);
    });
  });

  toolwheel.querySelectorAll('[data-action]').forEach(item => {
    item.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      handleToolwheelAction(item);
    });
  });

  document.addEventListener('keydown', event => {
    if (event.ctrlKey && event.key === 'Tab') {
      event.preventDefault();
      moveWorkspace(event.shiftKey ? -1 : 1);
      return;
    }

    if (event.ctrlKey && /^[1-4]$/.test(event.key)) {
      event.preventDefault();
      setActiveWorkspace(workspaceOrder[Number(event.key) - 1]);
      return;
    }

    if (event.altKey && event.code === 'Space') {
      event.preventDefault();
      toolwheel.classList.contains('open') ? closeToolwheel() : openToolwheel();
      return;
    }

    if (!toolwheel.classList.contains('open')) return;

    if (event.key === 'Escape') {
      closeToolwheel();
      return;
    }

    if (/^[1-4]$/.test(event.key)) {
      focusWheelNode(Number(event.key) - 1);
      return;
    }

    if (event.key === 'Enter' && focusedNode) {
      if (focusedNode.dataset.node === 'Projects') {
        openProjectsOverview();
        return;
      }
      if (focusedNode.dataset.node === 'Knowledge') {
        openKnowledgeGraphWindow();
        return;
      }
      if (focusedNode.dataset.node === 'Settings') {
        openSettingsWindow({ query: '', advanced: false });
        return;
      }
      announceAction(focusedNode.dataset.node);
      return;
    }

    if (event.key === 'ArrowRight' || event.key === 'Tab') {
      event.preventDefault();
      const nodes = Array.from(toolwheel.querySelectorAll('.wheel-node'));
      focusWheelNode((nodes.indexOf(focusedNode) + 1) % nodes.length);
    }

    if (event.key === 'ArrowLeft') {
      const nodes = Array.from(toolwheel.querySelectorAll('.wheel-node'));
      focusWheelNode((nodes.indexOf(focusedNode) - 1 + nodes.length) % nodes.length);
    }
  });

  resizeBrushCanvas();
  stage.classList.add('wheel-' + wheelOverlayMode + '-mode');
  buildState.textContent = 'V2 - Background: ' + (backgroundMode === 'network' ? 'Network' : 'Grid');
  installDefaultKnowledgeGraphWindow();
  installDefaultProjectsOverviewWindow();
  installWindowInteractions();
  installUniversalInbox();
  wireInboxFiles();
  wireInboxStatusTabs();
  linkifyDocumentReferences(messages);
  ensureAiMetaHotspots();
  setActiveWorkspace(restoredWorkspaceId);
  setWorkspaceMode('agent');
  updateModelUi();
  renderChatHistory();
  updateChatNodges();
  updateChatCarousel();
  restoreLastActiveWindow();
  window.addEventListener('resize', () => {
    resizeBrushCanvas();
    refreshMaximizedWindows();
  });
  requestAnimationFrame(animateGridBrush);
})();
