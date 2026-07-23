(function () {
  'use strict';

  const root = document.querySelector('[data-ai-lens-root]');
  const previewSetting = new URLSearchParams(window.location.search).get('ai-lens-preview');
  const previewRequested = (window.location.protocol === 'file:' && previewSetting !== 'off') || previewSetting === 'fixture';
  const PreviewApi = window.HarborAiLensPreview?.AiLensPreviewApi;
  const Api = previewRequested && PreviewApi ? PreviewApi : window.HarborAiLensApi?.AiLensApi;
  if (!root) return;

  const PHASE_ORDER = ['session', 'input', 'embedding', 'retrieval', 'context', 'model', 'tool', 'safety', 'response', 'replay', 'local_model'];
  const POLL_INTERVAL_MS = 15000;
  const state = {
    api: Api ? new Api() : null,
    previewMode: previewRequested && Boolean(PreviewApi),
    service: null,
    sessions: [],
    snapshot: null,
    graphBundle: null,
    events: [],
    selectedSessionId: '',
    selectedEventId: '',
    autoFollowLatest: true,
    streamState: 'loading',
    closeStream: null,
    requestToken: 0,
    loading: false,
    lastRefreshAt: 0
  };

  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function safeClass(value) {
    return String(value || '').toLowerCase().replace(/[^a-z0-9_-]/g, '-');
  }

  function number(value) {
    return new Intl.NumberFormat('en-US').format(Number(value) || 0);
  }

  function bytes(value) {
    const amount = Number(value) || 0;
    if (amount < 1024) return `${amount} B`;
    if (amount < 1024 * 1024) return `${(amount / 1024).toFixed(amount < 10240 ? 1 : 0)} KB`;
    return `${(amount / (1024 * 1024)).toFixed(1)} MB`;
  }

  function time(value, withDate) {
    if (!value) return '-';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value);
    return new Intl.DateTimeFormat('en-GB', {
      ...(withDate ? { day: '2-digit', month: 'short' } : {}),
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: false
    }).format(parsed);
  }

  function jsonValue(value) {
    return typeof value === 'string' ? value : JSON.stringify(value);
  }

  function sortedEvents(events) {
    return [...events].sort((a, b) => {
      const turn = String(a.turn_id).localeCompare(String(b.turn_id));
      return turn || a.sequence - b.sequence || String(a.created_at).localeCompare(String(b.created_at));
    });
  }

  function sortSessions(sessions) {
    return [...sessions].sort((a, b) => {
      const timestamp = String(b.last_retained_at || '').localeCompare(String(a.last_retained_at || ''));
      return timestamp || String(b.session_id).localeCompare(String(a.session_id));
    });
  }

  function renderState(kind, title, copy) {
    root.setAttribute('aria-busy', String(kind === 'loading'));
    root.innerHTML = `
      <div class="lens-state lens-state-${safeClass(kind)}" role="status">
        <span class="lens-state-signal" aria-hidden="true"></span>
        <div><strong>${escapeHtml(title)}</strong><span>${escapeHtml(copy)}</span></div>
      </div>`;
  }

  function selectedEvent() {
    return state.events.find(event => event.event_id === state.selectedEventId) || state.events[state.events.length - 1] || null;
  }

  function renderSessions() {
    return state.sessions.map((session, index) => {
      const active = session.session_id === state.selectedSessionId;
      const eventCount = session.event_count ?? session.retained_event_count ?? 0;
      return `
        <button class="lens-session${active ? ' active' : ''}" type="button" data-lens-session="${escapeHtml(session.session_id)}" aria-pressed="${active}">
          <span class="lens-session-id">${index === 0 ? 'latest' : escapeHtml(session.session_id)}</span>
          <span class="lens-session-meta">${escapeHtml(time(session.last_retained_at, true))} · ${number(eventCount)} evt</span>
        </button>`;
    }).join('');
  }

  function graphParts() {
    const bundle = state.graphBundle;
    if (!bundle || !bundle.graph || !bundle.layout || !bundle.trace) return null;
    const nodes = Array.isArray(bundle.graph.nodes) ? bundle.graph.nodes : [];
    const edges = Array.isArray(bundle.graph.edges) ? bundle.graph.edges : [];
    const coordinates = Array.isArray(bundle.layout.coordinates) ? bundle.layout.coordinates : [];
    const points = new Map(coordinates.map(point => [point.node_id, point]));
    const chunks = bundle.chunks && typeof bundle.chunks === 'object' && !Array.isArray(bundle.chunks) ? bundle.chunks : {};
    if (!nodes.length || nodes.some(node => !points.has(node.node_id))) return null;
    return { bundle, nodes, edges, points, chunks, trace: bundle.trace };
  }

  function edgePath(from, to) {
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    const bend = Math.min(74, 18 + Math.abs(dx) * 0.07 + Math.abs(dy) * 0.04);
    const direction = String(from.node_id || '').localeCompare(String(to.node_id || '')) > 0 ? -1 : 1;
    const cx = (from.x + to.x) / 2 - (dy / Math.max(1, Math.hypot(dx, dy))) * bend * direction;
    const cy = (from.y + to.y) / 2 + (dx / Math.max(1, Math.hypot(dx, dy))) * bend * direction;
    return `M ${from.x} ${from.y} Q ${cx.toFixed(1)} ${cy.toFixed(1)} ${to.x} ${to.y}`;
  }

  function renderKnowledgeGraph() {
    const parts = graphParts();
    if (!parts) {
      return `
        <div class="lens-map-unavailable" role="status">
          <span class="lens-map-unavailable-mark" aria-hidden="true"></span>
          <div>
            <strong>Chunk graph source not connected</strong>
            <span>The runtime trace remains available. The complete set of chunks and chunk relations is not inferred from event data.</span>
          </div>
        </div>`;
    }

    const { bundle, nodes, edges, points, chunks, trace } = parts;
    const traceNodes = new Set(trace.node_ids || []);
    const traceEdges = new Set(trace.edge_ids || []);
    const eventNodeMap = trace.event_node_map || {};
    const selectedNodeId = eventNodeMap[state.selectedEventId] || '';
    const eventByNode = new Map();
    Object.entries(eventNodeMap).forEach(([eventId, nodeId]) => eventByNode.set(nodeId, eventId));
    const width = Number(bundle.layout.width) || 1200;
    const height = Number(bundle.layout.height) || 640;
    const clusters = Array.isArray(bundle.layout.clusters) ? bundle.layout.clusters : [];
    const collectionCounts = nodes.reduce((counts, node) => {
      const collection = chunks[node.node_id]?.collection || 'chunks';
      counts[collection] = (counts[collection] || 0) + 1;
      return counts;
    }, {});
    const renderedClusters = clusters.map(cluster => `
      <ellipse cx="${Number(cluster.x) || 0}" cy="${Number(cluster.y) || 0}" rx="${Number(cluster.rx) || 0}" ry="${Number(cluster.ry) || 0}"></ellipse>
      <text x="${(Number(cluster.x) || 0) - (Number(cluster.rx) || 0) + 14}" y="${(Number(cluster.y) || 0) - (Number(cluster.ry) || 0) + 18}">${escapeHtml(cluster.label)} · ${number(collectionCounts[cluster.cluster_id])}</text>`).join('');

    const baseEdges = edges.map(edge => {
      const from = points.get(edge.source_id);
      const to = points.get(edge.target_id);
      if (!from || !to) return '';
      return `<path class="lens-map-edge${traceEdges.has(edge.edge_id) ? ' is-trace-base' : ''}" d="${edgePath({ ...from, node_id: edge.source_id }, { ...to, node_id: edge.target_id })}"><title>${escapeHtml(edge.edge_type)}</title></path>`;
    }).join('');

    const tracedEdges = edges.filter(edge => traceEdges.has(edge.edge_id)).map(edge => {
      const from = points.get(edge.source_id);
      const to = points.get(edge.target_id);
      if (!from || !to) return '';
      return `<path class="lens-map-trace-edge" d="${edgePath({ ...from, node_id: edge.source_id }, { ...to, node_id: edge.target_id })}" marker-end="url(#lens-trace-arrow)"><title>${escapeHtml(edge.edge_type)}</title></path>`;
    }).join('');

    const renderedNodes = nodes.map(node => {
      const point = points.get(node.node_id);
      const chunk = chunks[node.node_id] || {};
      const relevant = traceNodes.has(node.node_id);
      const selected = node.node_id === selectedNodeId;
      const eventId = eventByNode.get(node.node_id) || '';
      const anchorEnd = point.x > width - 150;
      const labelX = anchorEnd ? -12 : 12;
      const labelY = relevant ? -4 : 3;
      const radius = relevant ? 7.5 : Math.max(3.2, Math.min(5.8, 3 + Number(node.score || 0) * 3));
      const semantics = eventId ? ` tabindex="0" role="button" data-lens-event="${escapeHtml(eventId)}"` : '';
      const collection = chunk.collection || 'chunk';
      const status = chunk.status || 'active';
      const accessibleLabel = `${node.label}, chunk, ${collection}${relevant ? ', relevant to this AI Lens path' : ''}`;
      return `
        <g class="lens-map-node type-chunk collection-${safeClass(collection)} status-${safeClass(status)}${relevant ? ' is-relevant' : ''}${selected ? ' is-selected' : ''}" transform="translate(${point.x} ${point.y})"${semantics} aria-label="${escapeHtml(accessibleLabel)}">
          <title>${escapeHtml(`${node.label} · ${chunk.title || 'Knowledge chunk'} · ${collection}`)}</title>
          <circle class="lens-map-node-halo" r="${relevant ? 15 : 9}"></circle>
          <circle class="lens-map-node-body" r="${radius}"></circle>
          <text class="lens-map-node-label" x="${labelX}" y="${labelY}" text-anchor="${anchorEnd ? 'end' : 'start'}">${escapeHtml(node.label)}</text>
          ${relevant ? `<text class="lens-map-node-type" x="${labelX}" y="10" text-anchor="${anchorEnd ? 'end' : 'start'}">${escapeHtml(collection)} chunk</text>` : ''}
        </g>`;
    }).join('');

    return `
      <svg class="lens-map" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" aria-label="Complete simulated chunk graph with all chunk relations and the AI Lens path highlighted">
        <defs>
          <marker id="lens-trace-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 0 L 8 4 L 0 8 z"></path>
          </marker>
          <filter id="lens-node-glow" x="-100%" y="-100%" width="300%" height="300%">
            <feGaussianBlur stdDeviation="4" result="blur"></feGaussianBlur>
            <feMerge><feMergeNode in="blur"></feMergeNode><feMergeNode in="SourceGraphic"></feMergeNode></feMerge>
          </filter>
        </defs>
        <g class="lens-map-zones" aria-hidden="true">${renderedClusters}</g>
        <g class="lens-map-edges">${baseEdges}</g>
        <g class="lens-map-trace">${tracedEdges}</g>
        <g class="lens-map-nodes">${renderedNodes}</g>
      </svg>`;
  }

  function graphPhases(events) {
    const present = new Set(events.map(event => event.phase));
    return [...PHASE_ORDER.filter(phase => present.has(phase)), ...[...present].filter(phase => !PHASE_ORDER.includes(phase)).sort()];
  }

  function renderEventTrace() {
    const events = sortedEvents(state.events);
    const phases = graphPhases(events);
    if (!events.length) return '<p class="lens-empty-copy">No retained events are available.</p>';
    const width = Math.max(880, 150 + events.length * 76);
    const height = Math.max(190, 30 + phases.length * 32);
    const startX = 122;
    const endX = width - 34;
    const phaseY = new Map(phases.map((phase, index) => [phase, 24 + index * 32]));
    const positions = new Map(events.map((event, index) => [event.event_id, {
      x: events.length === 1 ? (startX + endX) / 2 : startX + (index / (events.length - 1)) * (endX - startX),
      y: phaseY.get(event.phase)
    }]));
    const lanes = phases.map(phase => {
      const y = phaseY.get(phase);
      return `<text class="lens-trace-lane-label" x="12" y="${y + 3}">${escapeHtml(phase)}</text><line class="lens-trace-lane-line" x1="94" y1="${y}" x2="${endX}" y2="${y}"></line>`;
    }).join('');
    const paths = [];
    const byTurn = new Map();
    events.forEach(event => {
      if (!byTurn.has(event.turn_id)) byTurn.set(event.turn_id, []);
      byTurn.get(event.turn_id).push(event);
    });
    byTurn.forEach(turnEvents => turnEvents.forEach((event, index) => {
      if (!index) return;
      const from = positions.get(turnEvents[index - 1].event_id);
      const to = positions.get(event.event_id);
      const mid = (from.x + to.x) / 2;
      paths.push(`<path class="lens-event-path" d="M ${from.x} ${from.y} C ${mid} ${from.y}, ${mid} ${to.y}, ${to.x} ${to.y}"></path>`);
    }));
    const nodes = events.map(event => {
      const point = positions.get(event.event_id);
      const active = event.event_id === state.selectedEventId;
      const label = `${event.sequence} ${event.event_type}, ${event.status}`;
      return `
        <g class="lens-event-node is-${safeClass(event.status)}${active ? ' active' : ''}" tabindex="0" role="button" data-lens-event="${escapeHtml(event.event_id)}" aria-label="${escapeHtml(label)}" transform="translate(${point.x} ${point.y})">
          <title>${escapeHtml(label)}</title>
          <circle class="lens-event-node-hit" r="11"></circle>
          <circle class="lens-event-node-core" r="3.5"></circle>
          <text class="lens-event-node-index" y="-15">${escapeHtml(event.sequence)}</text>
        </g>`;
    }).join('');
    return `<svg class="lens-event-graph" style="min-width:${width}px" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMinYMid meet" aria-label="Chronological AI Lens event trace grouped by phase">${lanes}${paths.join('')}${nodes}</svg>`;
  }

  function selectedGraphNode() {
    const parts = graphParts();
    const nodeId = parts?.trace?.event_node_map?.[state.selectedEventId];
    const node = parts?.nodes.find(item => item.node_id === nodeId) || null;
    return node ? { ...node, chunk: parts.chunks[node.node_id] || {} } : null;
  }

  function renderInspectorBody(event) {
    if (!event) return '<p class="lens-empty-copy">No event is available for inspection.</p>';
    const graphNode = selectedGraphNode();
    const meta = [
      ['Event ID', event.event_id], ['Turn ID', event.turn_id], ['Created', time(event.created_at, true)],
      ['Latency', `${number(event.latency_ms)} ms`], ['Truth', event.truth_level], ['Origin', event.observation_origin],
      ['Privacy', event.privacy_level], ['Redaction', event.redaction_level]
    ];
    if (event.model_id) meta.splice(4, 0, ['Model', event.model_id]);
    const payloadRows = Object.entries(event.payload || {}).map(([key, value]) => `
      <div class="lens-payload-row"><span class="lens-payload-key">${escapeHtml(key)}</span><span class="lens-payload-value">${escapeHtml(jsonValue(value))}</span></div>`).join('');
    const sourceRows = (event.source_refs || []).map(source => `
      <div class="lens-source-row">
        <div class="lens-source-topline"><span class="lens-source-kind">${escapeHtml(source.kind)}</span><span class="lens-source-redaction">${escapeHtml(source.redaction_level)}</span></div>
        <span class="lens-source-id">${escapeHtml(source.source_id)}</span>
        ${source.redacted_preview ? `<span class="lens-source-preview">${escapeHtml(source.redacted_preview)}</span>` : ''}
      </div>`).join('');
    return `
      ${graphNode ? `<div class="lens-node-reference"><span>Highlighted chunk</span><strong>${escapeHtml(graphNode.label)}</strong><small>${escapeHtml(graphNode.chunk.title || 'Knowledge chunk')} · ${escapeHtml(graphNode.chunk.collection || 'chunk')}</small></div>` : ''}
      <p class="lens-inspector-summary">${escapeHtml(event.summary || 'No summary was emitted for this event.')}</p>
      <div class="lens-meta-grid">${meta.map(([label, value]) => `<div class="lens-meta-item"><span>${escapeHtml(label)}</span><strong title="${escapeHtml(value)}">${escapeHtml(value)}</strong></div>`).join('')}</div>
      <section class="lens-inspector-group" aria-label="Bounded payload"><h3>Bounded payload · ${number(Object.keys(event.payload || {}).length)}</h3>${payloadRows || '<p class="lens-empty-copy">No payload fields were emitted.</p>'}</section>
      <section class="lens-inspector-group" aria-label="Source references"><h3>Source refs · ${number((event.source_refs || []).length)}</h3>${sourceRows || '<p class="lens-empty-copy">No source references were emitted.</p>'}</section>`;
  }

  function streamLabel() {
    if (state.streamState === 'streaming') return 'bounded stream';
    if (state.streamState === 'snapshot') return 'snapshot';
    if (state.streamState === 'error') return 'unavailable';
    return 'connecting';
  }

  function render() {
    if (!state.snapshot || !state.service) return;
    root.removeAttribute('aria-live');
    root.setAttribute('aria-busy', 'false');
    const event = selectedEvent();
    const graph = graphParts();
    const fixture = state.snapshot.fixture_mode === true;
    const preview = state.previewMode || state.service.preview_mode === true;
    const visibleCount = state.events.length;
    const retainedCount = state.snapshot.retained_event_count ?? state.snapshot.event_count ?? visibleCount;
    const returnedCount = state.snapshot.returned_event_count ?? visibleCount;
    const truncation = Array.isArray(state.snapshot.truncation_reasons) ? state.snapshot.truncation_reasons : [];
    const traceNodeCount = graph?.trace?.node_ids?.length || 0;
    root.innerHTML = `
      <section class="lens-shell" aria-label="Knowledge graph with AI Lens runtime trace">
        <header class="lens-commandbar">
          <div class="lens-identity"><span class="lens-identity-mark">L</span><strong>AI Lens</strong><small>${preview ? 'local simulation · API-compatible' : fixture ? 'synthetic fixture' : 'bounded runtime observation'}</small></div>
          <div class="lens-command-facts" aria-label="Lens facts">
            <div class="lens-fact"><span>Graph</span><strong>${graph ? `${number(graph.nodes.length)} chunks` : 'not connected'}</strong></div>
            <div class="lens-fact"><span>Path</span><strong>${graph ? `${number(traceNodeCount)} chunks` : 'trace only'}</strong></div>
            <div class="lens-fact"><span>Events</span><strong>${number(visibleCount)} visible</strong></div>
          </div>
          ${preview ? '<span class="lens-pill is-warning">simulation</span>' : fixture ? '<span class="lens-pill is-warning">fixture</span>' : ''}
          <span class="lens-stream-state" data-state="${safeClass(state.streamState)}">${escapeHtml(streamLabel())}</span>
        </header>

        <nav class="lens-session-strip" aria-label="Observed AI Lens sessions">
          <span class="lens-session-strip-label">Observed session</span>
          <div class="lens-session-list">${renderSessions()}</div>
        </nav>

        <div class="lens-workbench">
          <main class="lens-primary">
            <section class="lens-map-panel" aria-label="Complete chunk graph overview">
              <header class="lens-panel-heading">
                <div><strong>Chunk graph</strong><span>${graph ? `all chunks · ${number(graph.nodes.length)} chunks · ${number(graph.edges.length)} chunk relations` : 'chunk overview unavailable'}</span></div>
                <span class="lens-panel-truth">${preview ? 'simulated complete set' : graph ? 'bounded complete set' : 'trace remains live'}</span>
              </header>
              <div class="lens-map-wrap">${renderKnowledgeGraph()}</div>
              ${graph ? '<div class="lens-map-legend" aria-label="Graph legend"><span><i class="is-path"></i>retrieval path</span><span><i class="is-related"></i>relevant chunk</span><span><i></i>all chunks</span></div>' : ''}
            </section>

            <section class="lens-event-panel" aria-label="Event trace">
              <header class="lens-panel-heading lens-event-heading">
                <div><strong>Event trace</strong><span>${escapeHtml(state.snapshot.session_id)} · chronology by phase and turn</span></div>
                <span class="lens-selected-event">${event ? `${escapeHtml(event.sequence)} · ${escapeHtml(event.event_type)} · ${escapeHtml(event.status)}` : 'no event'}</span>
              </header>
              <div class="lens-event-wrap">${renderEventTrace()}</div>
            </section>
          </main>

          <aside class="lens-inspector" aria-label="Selected event metadata">
            <header class="lens-inspector-heading"><div><strong>${escapeHtml(event?.event_type || 'No event')}</strong><span>${event ? `${escapeHtml(event.phase)} · sequence ${escapeHtml(event.sequence)}` : 'No retained event'}</span></div></header>
            <div class="lens-inspector-scroll">${renderInspectorBody(event)}</div>
          </aside>
        </div>

        <footer class="lens-footline" aria-label="Snapshot bounds">
          <span>${number(returnedCount)} returned / ${number(retainedCount)} retained</span>
          <span>${bytes(state.snapshot.snapshot_bytes)}</span>
          <span>raw content ${state.snapshot.raw_content_visible === false ? 'hidden' : 'unknown'}</span>
          ${state.service.write_endpoint_available === false ? '<span>read only</span>' : ''}
          ${preview ? '<span class="lens-footline-alert">local simulation</span>' : ''}
          ${state.snapshot.incomplete || state.snapshot.truncated ? `<span class="lens-footline-alert">${escapeHtml(truncation.join(' · ') || 'bounded history')}</span>` : ''}
        </footer>
      </section>`;
    bindRenderedEvents();
  }

  function chooseEvent(eventId) {
    if (!state.events.some(event => event.event_id === eventId)) return;
    state.selectedEventId = eventId;
    render();
    const selector = window.CSS?.escape ? `[data-lens-event="${window.CSS.escape(eventId)}"]` : '[data-lens-event]';
    root.querySelector(selector)?.focus({ preventScroll: true });
  }

  function bindRenderedEvents() {
    root.querySelectorAll('[data-lens-session]').forEach(button => button.addEventListener('click', () => {
      const sessionId = button.dataset.lensSession;
      if (!sessionId || sessionId === state.selectedSessionId) return;
      state.autoFollowLatest = sessionId === state.sessions[0]?.session_id;
      loadSession(sessionId);
    }));
    root.querySelectorAll('[data-lens-event]').forEach(control => {
      control.addEventListener('click', () => chooseEvent(control.dataset.lensEvent));
      control.addEventListener('keydown', event => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        chooseEvent(control.dataset.lensEvent);
      });
    });
  }

  function stopStream() {
    state.closeStream?.();
    state.closeStream = null;
  }

  function mergeStreamEvent(event) {
    if (event.session_id !== state.selectedSessionId) return;
    const index = state.events.findIndex(item => item.event_id === event.event_id);
    if (index === -1) state.events.push(event);
    else state.events[index] = event;
    state.events = sortedEvents(state.events);
    if (!state.selectedEventId) state.selectedEventId = event.event_id;
    render();
  }

  function startStream(sessionId, eventLimit, token) {
    stopStream();
    state.streamState = 'streaming';
    render();
    state.closeStream = state.api.streamSession(sessionId, {
      eventLimit,
      heartbeatEvery: 8,
      onEvent: event => { if (token === state.requestToken) mergeStreamEvent(event); },
      onEnd: () => {
        if (token !== state.requestToken) return;
        state.streamState = 'snapshot';
        state.closeStream = null;
        render();
      },
      onError: () => {
        if (token !== state.requestToken) return;
        state.streamState = 'snapshot';
        state.closeStream = null;
        render();
      }
    });
  }

  async function loadSession(sessionId) {
    const token = ++state.requestToken;
    stopStream();
    state.selectedSessionId = sessionId;
    state.streamState = 'loading';
    state.graphBundle = null;
    if (!state.snapshot) renderState('loading', 'Reading bounded snapshot', sessionId);
    try {
      const limit = Math.min(128, Math.max(1, Number(state.service?.limits?.max_snapshot_events) || 128));
      const graphRequest = typeof state.api.getKnowledgeGraph === 'function'
        ? state.api.getKnowledgeGraph(sessionId).catch(() => null)
        : Promise.resolve(null);
      const [snapshot, graphBundle] = await Promise.all([state.api.getSnapshot(sessionId, limit), graphRequest]);
      if (token !== state.requestToken) return;
      if (snapshot.session_id !== sessionId) throw new Error('AI Lens returned a different session.');
      state.snapshot = snapshot;
      state.graphBundle = graphBundle;
      state.events = sortedEvents(snapshot.events);
      state.selectedEventId = state.events.some(event => event.event_id === state.selectedEventId)
        ? state.selectedEventId
        : state.events[state.events.length - 1]?.event_id || '';
      startStream(sessionId, limit, token);
    } catch (error) {
      if (token !== state.requestToken) return;
      state.snapshot = null;
      state.graphBundle = null;
      state.events = [];
      const denied = error?.status === 401 || error?.status === 403;
      renderState('error', denied ? 'Admin access required' : 'AI Lens snapshot unavailable', denied ? 'This read-only surface is restricted to authenticated administrators.' : String(error?.message || 'The bounded snapshot could not be read.'));
    }
  }

  async function refreshCatalog(options) {
    if (!state.api || state.loading) return;
    const settings = options || {};
    state.loading = true;
    if (!settings.silent && !state.snapshot) renderState('loading', 'Connecting to AI Lens', 'Reading service and retained session metadata.');
    try {
      const [service, page] = await Promise.all([state.api.getService(), state.api.listSessions()]);
      const previousSession = state.sessions.find(item => item.session_id === state.selectedSessionId);
      state.service = service;
      state.sessions = sortSessions(page.sessions);
      state.lastRefreshAt = Date.now();
      if (!state.sessions.length) {
        stopStream();
        state.snapshot = null;
        state.graphBundle = null;
        state.events = [];
        renderState('empty', 'No retained AI Lens sessions', 'The Knowledge workspace will populate when runtime instrumentation emits a bounded session.');
        return;
      }
      const latestId = state.sessions[0].session_id;
      const selectedStillExists = state.sessions.some(item => item.session_id === state.selectedSessionId);
      const nextSessionId = state.autoFollowLatest || !selectedStillExists ? latestId : state.selectedSessionId;
      const nextSession = state.sessions.find(item => item.session_id === nextSessionId);
      const unchanged = settings.silent && state.snapshot && nextSessionId === state.selectedSessionId && previousSession
        && previousSession.last_retained_at === nextSession?.last_retained_at
        && previousSession.event_count === nextSession?.event_count;
      state.selectedSessionId = nextSessionId;
      if (unchanged) render();
      else await loadSession(nextSessionId);
    } catch (error) {
      state.lastRefreshAt = Date.now();
      if (state.snapshot) {
        state.streamState = 'snapshot';
        render();
      } else {
        const denied = error?.status === 401 || error?.status === 403;
        renderState('error', denied ? 'Admin access required' : 'AI Lens unavailable', denied ? 'This read-only surface is restricted to authenticated administrators.' : String(error?.message || 'The service metadata could not be read.'));
      }
    } finally {
      state.loading = false;
    }
  }

  function knowledgeIsActive() {
    return document.querySelector('.stage')?.dataset.workspace === 'knowledge';
  }

  if (!state.api) {
    renderState('error', 'AI Lens client unavailable', 'The V3 API client did not load.');
    return;
  }

  if (knowledgeIsActive()) refreshCatalog();
  const poller = window.setInterval(() => { if (knowledgeIsActive()) refreshCatalog({ silent: true }); }, POLL_INTERVAL_MS);
  const stage = document.querySelector('.stage');
  const observer = stage ? new MutationObserver(() => {
    if (knowledgeIsActive() && Date.now() - state.lastRefreshAt > 10000) refreshCatalog({ silent: true });
    if (!knowledgeIsActive()) stopStream();
  }) : null;
  observer?.observe(stage, { attributes: true, attributeFilter: ['data-workspace'] });
  window.addEventListener('pagehide', () => {
    window.clearInterval(poller);
    observer?.disconnect();
    stopStream();
  }, { once: true });
})();
