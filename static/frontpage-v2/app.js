(function () {
  const stage = document.getElementById('stage');
  const brushCanvas = document.getElementById('grid-brush');
  const brushCtx = brushCanvas.getContext('2d');
  const toolwheel = document.getElementById('toolwheel');
  const workspace = stage.querySelector('.workspace');
  const topline = stage.querySelector('.topline');
  const aurora = stage.querySelector('.aurora');
  const wheelArrow = document.getElementById('wheel-arrow');
  const wheelCore = document.getElementById('wheel-core');
  const coreNewTree = toolwheel.querySelector('.core-new-tree');
  const readout = document.getElementById('wheel-readout');
  const buildState = document.getElementById('build-state');
  const coreWindow = document.querySelector('[data-core]');
  const chatTitle = coreWindow.querySelector('.chat-title');
  const messages = coreWindow.querySelector('.messages');
  const promptInput = coreWindow.querySelector('.prompt-input');
  const contextNodges = coreWindow.querySelector('[data-context-nodges]');
  const leftNodge = document.getElementById('chat-nodge-left');
  const rightNodge = document.getElementById('chat-nodge-right');
  const chatCarousel = document.getElementById('chat-carousel');
  const urlParams = new URLSearchParams(window.location.search);
  const backgroundMode = urlParams.get('bg') === 'grid' ? 'grid' : 'network';
  const wheelOverlayMode = urlParams.get('wheel') === 'dim' ? 'dim' : 'depth';

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
  const chatSpaces = [{
    id: 'chat-1',
    title: chatTitle.textContent.trim(),
    messages: messages.innerHTML,
    prompt: promptInput.value,
    contexts: []
  }];

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

  function serializeContextNodges() {
    return Array.from(contextNodges.querySelectorAll('.context-nodge')).map(nodge => ({
      kind: nodge.dataset.contextKind || 'Context',
      label: nodge.dataset.contextLabel || nodge.textContent.trim()
    }));
  }

  function contextKindMeta(kind) {
    const normalized = String(kind || 'Context').toLowerCase();
    const map = {
      file: { icon: '[]', hint: 'File attached to this prompt' },
      mount: { icon: '#', hint: 'Folder mount available to this chat' },
      source: { icon: '@', hint: 'Knowledge source added as context' },
      project: { icon: '<>', hint: 'Project context is active' },
      chat: { icon: '//', hint: 'Chat context is linked' },
      memory: { icon: '*', hint: 'Memory context is active' },
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
      nodge.title = context.kind + ': ' + context.label + ' - ' + meta.hint;

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
      tooltip.textContent = context.kind + ': ' + meta.hint;

      nodge.append(icon, label, remove, tooltip);
      contextNodges.appendChild(nodge);
    });
    contextNodges.hidden = !contextNodges.children.length;
  }

  function addContextNodge(kind, label) {
    const contexts = serializeContextNodges();
    const exists = contexts.some(context => context.kind === kind && context.label === label);
    if (!exists) contexts.push({ kind, label });
    renderContextNodges(contexts);
    const current = chatSpaces[activeChatIndex];
    if (current) current.contexts = contexts;
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
  }

  function saveActiveChatSpace() {
    const current = chatSpaces[activeChatIndex];
    if (!current) return;
    ensureAiMetaHotspots();
    current.title = chatTitle.textContent.trim();
    current.messages = messages.innerHTML;
    current.prompt = promptInput.value;
    current.contexts = serializeContextNodges();
  }

  function updateChatNodges() {
    const hasMultipleChats = chatSpaces.length > 1;
    const hasPrevious = hasMultipleChats && activeChatIndex > 0;
    const hasNext = hasMultipleChats && activeChatIndex < chatSpaces.length - 1;
    leftNodge.hidden = !hasPrevious;
    rightNodge.hidden = !hasNext;
    leftNodge.setAttribute('aria-label', hasPrevious ? 'Previous chat: ' + chatSpaces[activeChatIndex - 1].title : 'Previous chat');
    rightNodge.setAttribute('aria-label', hasNext ? 'Next chat: ' + chatSpaces[activeChatIndex + 1].title : 'Next chat');
  }

  function chatIsWorking(space) {
    return /\bworking\b/.test(space.messages);
  }

  function updateChatCarousel() {
    chatCarousel.hidden = chatSpaces.length <= 1;
    chatCarousel.innerHTML = '';
    if (chatCarousel.hidden) return;

    chatSpaces.forEach((space, index) => {
      const relative = index - activeChatIndex;
      const distance = Math.min(Math.abs(relative), 4);
      const tile = document.createElement('button');
      tile.type = 'button';
      tile.className = 'carousel-tile' + (index === activeChatIndex ? ' active' : '');
      tile.dataset.chatIndex = String(index);
      tile.style.left = 'calc(50% + ' + (relative * 24) + 'px)';
      tile.style.setProperty('--tile-x', relative * 24 + 'px');
      tile.style.setProperty('--tile-z', -distance * 18 + 'px');
      tile.style.setProperty('--tile-rotate', relative * -18 + 'deg');
      tile.style.setProperty('--tile-scale', String(Math.max(0.62, 1 - distance * 0.12)));
      tile.style.setProperty('--tile-opacity', String(Math.max(0.22, 1 - distance * 0.18)));
      tile.setAttribute('aria-label', 'Open ' + space.title);
      tile.innerHTML = [
        '<span class="carousel-card">',
        '<span class="carousel-number">' + (index + 1) + '</span>',
        '<span class="carousel-glyph" aria-hidden="true">' + (index === 0 ? '&#9678;' : '&#9635;') + '</span>',
        chatIsWorking(space) ? '<span class="carousel-work" aria-hidden="true"><span></span><span></span><span></span></span>' : '',
        '</span>'
      ].join('');
      tile.addEventListener('click', event => {
        event.preventDefault();
        openChatSpace(index);
      });
      chatCarousel.appendChild(tile);
    });
  }

  function renderChatSpace(index) {
    const next = chatSpaces[index];
    if (!next) return;
    activeChatIndex = index;
    chatTitle.textContent = next.title;
    messages.innerHTML = next.messages;
    ensureAiMetaHotspots();
    promptInput.value = next.prompt;
    renderContextNodges(next.contexts);
    autosizePrompt(promptInput);
    updateChatNodges();
    updateChatCarousel();
    buildState.textContent = 'V2 - Chat ' + (activeChatIndex + 1) + ' / ' + chatSpaces.length;
  }

  function createChatSpace() {
    saveActiveChatSpace();
    const number = chatSpaces.length + 1;
    chatSpaces.push({
      id: 'chat-' + number,
      title: 'Chat ' + number,
      messages: [
        '<div class="message user">Start a fresh chat space.</div>',
        '<div class="message ai working" data-ai-time="' + currentShortTime() + '" data-ai-model="deepseek-v4-flash" data-ai-tokens="live" data-ai-context="fresh" data-ai-latency="waiting"><span class="busy-signal">Ready for a new task <span class="pixel-wave" data-wave">&#9601;&#9602;&#9603;</span></span></div>'
      ].join(''),
      prompt: '',
      contexts: []
    });
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
  }

  function windowTitle(win) {
    return win.querySelector('.chat-title')?.textContent?.trim() || 'Window';
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
    } else {
      removeDockBubble(win);
      activateWindow(win);
    }
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

  function handleToolwheelAction(item) {
    const action = item.dataset.action;
    const type = item.dataset.actionType || 'Instant';
    if (!action) return;

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

  function installResizeHandles() {
    const dirs = ['n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw'];
    document.querySelectorAll('[data-window]').forEach(win => {
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
    win._restoreBounds = {
      left: rect.left + 'px',
      top: rect.top + 'px',
      width: rect.width + 'px',
      height: rect.height + 'px',
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

  function autosizePrompt(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.max(96, textarea.scrollHeight) + 'px';
    const composer = textarea.closest('.composer');
    if (composer) {
      composer.style.minHeight = textarea.style.height;
    }
  }

  function installWindowInteractions() {
    installResizeHandles();

    document.querySelectorAll('[data-window]').forEach((win, index) => {
      ensureWindowId(win, index);
      win.addEventListener('pointerdown', () => activateWindow(win));
    });

    document.querySelectorAll('[data-drag-handle]').forEach(handle => {
      handle.addEventListener('pointerdown', event => {
        if (event.target.closest('button, textarea, .resize-handle')) return;
        const win = handle.closest('[data-window]');
        if (!win || win.classList.contains('maximized')) return;
        activateWindow(win);
        const rect = win.getBoundingClientRect();
        const startX = event.clientX;
        const startY = event.clientY;
        const offsetX = startX - rect.left;
        const offsetY = startY - rect.top;
        win.style.transform = 'none';
        win.style.left = rect.left + 'px';
        win.style.top = rect.top + 'px';
        win.style.width = rect.width + 'px';
        win.style.height = rect.height + 'px';
        handle.setPointerCapture(event.pointerId);

        function move(e) {
          win.style.left = e.clientX - offsetX + 'px';
          win.style.top = e.clientY - offsetY + 'px';
        }

        function up() {
          handle.releasePointerCapture(event.pointerId);
          document.removeEventListener('pointermove', move);
          document.removeEventListener('pointerup', up);
        }

        document.addEventListener('pointermove', move);
        document.addEventListener('pointerup', up);
      });
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
      win.style.transform = 'none';
      win.style.left = rect.left + 'px';
      win.style.top = rect.top + 'px';
      win.style.width = rect.width + 'px';
      win.style.height = rect.height + 'px';
      handle.setPointerCapture(event.pointerId);

      function move(e) {
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        let left = rect.left;
        let top = rect.top;
        let width = rect.width;
        let height = rect.height;

        if (dir.includes('e')) width = Math.max(minW, rect.width + dx);
        if (dir.includes('s')) height = Math.max(minH, rect.height + dy);
        if (dir.includes('w')) {
          width = Math.max(minW, rect.width - dx);
          left = rect.right - width;
        }
        if (dir.includes('n')) {
          height = Math.max(minH, rect.height - dy);
          top = rect.bottom - height;
        }

        win.style.left = left + 'px';
        win.style.top = top + 'px';
        win.style.width = width + 'px';
        win.style.height = height + 'px';
      }

      function up() {
        handle.releasePointerCapture(event.pointerId);
        document.removeEventListener('pointermove', move);
        document.removeEventListener('pointerup', up);
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
        setWindowMinimized(win, true);
        return;
      }

      if (control.matches('[data-window-max]')) {
        const shouldMaximize = !win.classList.contains('maximized');
        if (shouldMaximize) {
          storeWindowBounds(win);
          win.classList.add('maximized');
          control.textContent = '\u25A2';
          control.title = 'Restore';
          control.setAttribute('aria-label', 'Restore');
        } else {
          win.classList.remove('maximized');
          restoreWindowBounds(win);
          control.textContent = '\u25A1';
          control.title = 'Maximize';
          control.setAttribute('aria-label', 'Maximize');
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

    contextNodges.addEventListener('click', event => {
      const remove = event.target.closest('.context-nodge-remove');
      if (!remove) return;
      event.preventDefault();
      const nodge = remove.closest('.context-nodge');
      nodge?.remove();
      contextNodges.hidden = !contextNodges.children.length;
      const current = chatSpaces[activeChatIndex];
      if (current) current.contexts = serializeContextNodges();
    });

    document.addEventListener('click', event => {
      document.querySelectorAll('.composer-shell.menu-open').forEach(shell => {
        if (!shell.contains(event.target)) {
          setComposerMenuOpen(shell, false);
        }
      });
    });

    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      document.querySelectorAll('.composer-shell.menu-open').forEach(shell => {
        setComposerMenuOpen(shell, false);
      });
    });

    leftNodge.addEventListener('click', () => moveChatSpace(-1));
    rightNodge.addEventListener('click', () => moveChatSpace(1));

    chatCarousel.addEventListener('click', event => {
      const tile = event.target.closest('[data-chat-index]');
      if (!tile) return;
      openChatSpace(Number(tile.dataset.chatIndex));
    });

    chatCarousel.addEventListener('wheel', event => {
      if (chatSpaces.length <= 1) return;
      event.preventDefault();
      moveChatSpace(event.deltaY > 0 ? 1 : -1);
    }, { passive: false });
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
      announceAction(node.dataset.node);
    });

    node.addEventListener('keydown', event => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
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
      moveChatSpace(event.shiftKey ? -1 : 1);
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
  installWindowInteractions();
  ensureAiMetaHotspots();
  setWorkspaceMode('agent');
  updateChatNodges();
  updateChatCarousel();
  window.addEventListener('resize', resizeBrushCanvas);
  requestAnimationFrame(animateGridBrush);
})();
