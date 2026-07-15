(function () {
  'use strict';

  const fixtures = window.HarborPlanningFixtures;
  const apiModule = window.HarborPlanningApi;
  const root = document.querySelector('[data-planning-root]');
  if (!root || !fixtures || !apiModule) return;

  const clone = fixtures.clone;
  const api = new apiModule.PlanningDefinitionApi();
  const params = new URLSearchParams(window.location.search);
  const requestedScenario = String(params.get('planningScenario') || '').toLowerCase();
  const sourceMode = String(params.get('planningSource') || 'auto').toLowerCase();
  const definitionNotificationEvents = new Set([
    'project_created',
    'project_deleted',
    'roadmap_created',
    'roadmap_deleted',
    'roadmap_revision_approved',
    'roadmap_revision_conflict',
    'undo_available_after_structural_delete'
  ]);
  const revisionNotificationEvents = new Set([
    'roadmap_revision_approved',
    'roadmap_revision_conflict'
  ]);
  const executionNotificationEvents = new Set([
    'activity_completed', 'activity_failed', 'activity_started',
    'agent_run_completed', 'agent_run_failed', 'agent_run_started',
    'claim_expired', 'gate_blocked', 'gate_unblocked_when_it_changes_available_work',
    'heartbeat_late', 'heartbeat_recovered', 'human_decision_required',
    'workflow_cancelled', 'workflow_paused', 'workflow_resumed'
  ]);

  function notificationId(value) {
    const text = String(value || '');
    return /^[a-z0-9][a-z0-9_-]{0,63}$/.test(text) ? text : '';
  }

  function parseNotificationRoute() {
    const eventType = String(params.get('notificationEvent') || '');
    if (!eventType) return null;
    if (executionNotificationEvents.has(eventType)) {
      return Object.freeze({ workspace: 'agent' });
    }
    if (!definitionNotificationEvents.has(eventType)) {
      return Object.freeze({ workspace: 'invalid', message: 'The notification target was rejected.' });
    }
    const projectId = notificationId(params.get('notificationProject'));
    const roadmapId = notificationId(params.get('notificationRoadmap'));
    const needsRoadmap = !eventType.startsWith('project_');
    const revisionText = String(params.get('notificationRevision') || '');
    const revision = /^\d+$/.test(revisionText) && Number(revisionText) > 0 ? Number(revisionText) : null;
    if (!projectId || (needsRoadmap && !roadmapId) || (revisionNotificationEvents.has(eventType) && !revision)) {
      return Object.freeze({ workspace: 'invalid', message: 'The definition notification reference was incomplete.' });
    }
    return Object.freeze({
      workspace: 'planning',
      eventType,
      projectId,
      roadmapId: roadmapId || null,
      revision
    });
  }

  const state = {
    catalog: null,
    selectedNodeId: '',
    view: 'graph',
    editing: false,
    draft: null,
    validation: { kind: 'unvalidated', errors: [] },
    undoSnapshot: null,
    undoReadbackHash: '',
    localPreviewAccepted: false,
    notification: parseNotificationRoute(),
    message: '',
    search: ''
  };

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function shortHash(value) {
    const text = String(value || '');
    return text.length > 24 ? `${text.slice(0, 15)}…${text.slice(-7)}` : text;
  }

  function list(value) {
    return Array.isArray(value) ? value : [];
  }

  function currentRoadmap() {
    return state.draft || (state.catalog && state.catalog.readModel && state.catalog.readModel.roadmap) || null;
  }

  function currentProject() {
    return state.catalog && (state.catalog.project || state.catalog.readModel && state.catalog.readModel.project);
  }

  function sourceRoadmap() {
    return state.catalog && state.catalog.readModel && state.catalog.readModel.roadmap;
  }

  function sourceDescriptor() {
    const catalog = state.catalog || {};
    const origin = catalog.readModel && catalog.readModel.origin || {};
    if (state.localPreviewAccepted) {
      return { tone: 'preview', label: 'Accepted preview', detail: 'source unchanged' };
    }
    if (catalog.conflict) {
      return { tone: 'conflict', label: 'Revision conflict', detail: 'compare the definition base' };
    }
    if (catalog.source === 'fixture') {
      if (origin.state === 'stale') return { tone: 'stale', label: 'Preview fixture', detail: 'older snapshot scenario' };
      return { tone: 'fixture', label: 'Preview fixture', detail: 'sample definitions · no source writes' };
    }
    if (origin.state === 'stale') return { tone: 'stale', label: 'Definition snapshot', detail: 'may be older than the catalog' };
    return { tone: 'live', label: 'Canonical definition source', detail: 'approved revision read' };
  }

  function renderDefinitionNotification() {
    const notification = state.notification;
    if (!notification || notification.workspace !== 'planning') return '';
    const labels = {
      project_created: 'Project definition created',
      project_deleted: 'Project definition tombstoned',
      roadmap_created: 'Roadmap definition created',
      roadmap_deleted: 'Roadmap definition tombstoned',
      roadmap_revision_approved: 'Roadmap revision approved',
      roadmap_revision_conflict: 'Roadmap revision conflict',
      undo_available_after_structural_delete: 'Definition undo available'
    };
    const revision = notification.revision ? ` · revision ${notification.revision}` : '';
    return `<div class="pde-message" role="status" data-pde-notification-target="planning" data-pde-notification-event="${escapeHtml(notification.eventType)}"><strong>${escapeHtml(labels[notification.eventType])}</strong> · exact definition selected${escapeHtml(revision)}</div>`;
  }

  function renderLoading() {
    root.dataset.planningScenario = 'loading';
    root.innerHTML = `
      <div class="pde-loading" role="status" aria-live="polite">
        <span class="pde-loading-line wide"></span>
        <span class="pde-loading-line"></span>
        <span class="pde-loading-line short"></span>
        <strong>Reading Planning definitions…</strong>
      </div>
    `;
  }

  function renderUnavailable() {
    const catalog = state.catalog || {};
    const scenario = catalog.scenario || 'unavailable';
    const title = scenario === 'empty'
      ? 'No definitions yet'
      : scenario === 'error'
        ? 'Definition response rejected'
        : 'Definition source unavailable';
    const detail = catalog.message || (scenario === 'empty'
      ? 'Create or import a versioned Planning definition to begin.'
      : 'The Planning surface did not substitute sample content for source data.');
    root.dataset.planningScenario = scenario;
    root.innerHTML = `
      <section class="pde-empty" aria-labelledby="pde-empty-title">
        <span class="pde-empty-mark" aria-hidden="true">${scenario === 'error' ? '!' : '◇'}</span>
        <div>
          <h2 id="pde-empty-title">${escapeHtml(title)}</h2>
          <p>${escapeHtml(detail)}</p>
        </div>
        <button class="pde-button primary" type="button" data-pde-action="load-fixture">Load labeled preview</button>
      </section>
    `;
  }

  function render() {
    if (!state.catalog || !state.catalog.readModel) {
      renderUnavailable();
      return;
    }
    const roadmap = currentRoadmap();
    const project = currentProject();
    const source = sourceDescriptor();
    const origin = state.catalog.readModel.origin || {};
    root.dataset.planningScenario = state.catalog.scenario || origin.state || state.catalog.source || 'live';
    root.dataset.originState = origin.state || 'unavailable';
    root.dataset.notificationTarget = state.notification && state.notification.workspace || 'none';
    root.innerHTML = `
      <section class="pde-shell" aria-label="Planning definition editor">
        <header class="pde-source-bar" data-source-tone="${escapeHtml(source.tone)}">
          <span class="pde-source-signal" aria-hidden="true"></span>
          <span class="pde-source-copy"><strong>${escapeHtml(source.label)}</strong><small>${escapeHtml(source.detail)}</small></span>
          <span class="pde-source-boundary">Definition only</span>
        </header>
        ${renderDefinitionNotification()}
        ${state.catalog.conflict ? `<div class="pde-conflict" role="alert"><strong>${escapeHtml(state.catalog.conflict.title)}</strong><span>${escapeHtml(state.catalog.conflict.detail)}</span></div>` : ''}
        ${state.message ? `<div class="pde-message" role="status" aria-live="polite">${escapeHtml(state.message)}</div>` : ''}
        <div class="pde-layout">
          ${renderRail(project, roadmap)}
          <main class="pde-workspace" aria-label="Selected roadmap definition">
            ${renderToolbar(roadmap)}
            ${state.view === 'graph' ? renderGraph(roadmap) : renderDefinition(roadmap, project)}
          </main>
          ${renderInspector(roadmap)}
        </div>
      </section>
    `;
  }

  function renderRail(project, roadmap) {
    const query = state.search.trim().toLowerCase();
    const roadmaps = list(state.catalog.roadmaps).filter(item => {
      if (!query) return true;
      return [item.title, item.roadmap_id].join(' ').toLowerCase().includes(query);
    });
    return `
      <aside class="pde-rail" aria-label="Projects and roadmaps">
        <div class="pde-project-heading">
          <span class="pde-project-monogram" aria-hidden="true">H1</span>
          <span><strong>${escapeHtml(project.title)}</strong><small>${escapeHtml(project.project_id)}</small></span>
        </div>
        <label class="pde-search">
          <span aria-hidden="true">⌕</span>
          <input type="search" value="${escapeHtml(state.search)}" placeholder="Find definition" aria-label="Find roadmap definition" data-pde-search>
        </label>
        <div class="pde-roadmap-list" aria-label="Roadmap definitions">
          ${roadmaps.map(item => `
            <button class="pde-roadmap-row${item.roadmap_id === roadmap.roadmap_id ? ' active' : ''}" type="button" data-pde-roadmap="${escapeHtml(item.roadmap_id)}">
              <span><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.roadmap_id)}</small></span>
              <span class="pde-revision">r${escapeHtml(item.latest_approved_revision || item.newest_revision || '—')}</span>
            </button>
          `).join('') || '<p class="pde-no-match">No matching definitions.</p>'}
        </div>
        <div class="pde-project-scope">
          <strong>Project boundary</strong>
          <span>${escapeHtml(project.objective)}</span>
          <dl>
            <div><dt>In</dt><dd>${list(project.scope && project.scope.in).map(escapeHtml).join(' · ') || '—'}</dd></div>
            <div><dt>Out</dt><dd>${list(project.scope && project.scope.out).map(escapeHtml).join(' · ') || '—'}</dd></div>
          </dl>
        </div>
      </aside>
    `;
  }

  function renderToolbar(roadmap) {
    const validation = state.validation.kind;
    const handoffBlocked = Boolean(state.draft || state.localPreviewAccepted || state.catalog.conflict || roadmap.revision_state !== 'approved');
    return `
      <header class="pde-toolbar">
        <div class="pde-title-block">
          <span>${escapeHtml(roadmap.roadmap_id)}</span>
          <h2>${escapeHtml(roadmap.title)}</h2>
          <div class="pde-revision-line">
            <span>Revision ${escapeHtml(roadmap.revision)}</span>
            <span>${escapeHtml(roadmap.revision_state)}</span>
            <code title="${escapeHtml(roadmap.content_hash)}">${escapeHtml(shortHash(roadmap.content_hash))}</code>
          </div>
        </div>
        <div class="pde-toolbar-actions">
          <div class="pde-view-switch" role="group" aria-label="Definition view">
            <button type="button" class="${state.view === 'graph' ? 'active' : ''}" data-pde-view="graph" aria-pressed="${state.view === 'graph'}">Graph</button>
            <button type="button" class="${state.view === 'definition' ? 'active' : ''}" data-pde-view="definition" aria-pressed="${state.view === 'definition'}">Definition</button>
          </div>
          <span class="pde-validation-pill" data-validation="${escapeHtml(validation)}">${escapeHtml(validation)}</span>
          <button class="pde-button" type="button" data-pde-action="edit" ${state.editing ? 'disabled' : ''}>Edit definition</button>
          <button class="pde-button primary" type="button" data-pde-action="handoff" ${handoffBlocked ? 'disabled' : ''} title="${handoffBlocked ? 'Use the unchanged approved source revision for handoff.' : 'Prepare a composer draft in Agent.'}">Open in Agent</button>
        </div>
      </header>
    `;
  }

  function graphLayout(nodes) {
    const byId = new Map(nodes.map(item => [item.node_id, item]));
    const memo = new Map();
    function level(item, trail) {
      if (memo.has(item.node_id)) return memo.get(item.node_id);
      const seen = new Set(trail || []);
      if (seen.has(item.node_id)) return 0;
      seen.add(item.node_id);
      const dependencies = list(item.depends_on).map(id => byId.get(id)).filter(Boolean);
      const value = dependencies.length ? 1 + Math.max(...dependencies.map(dep => level(dep, seen))) : 0;
      memo.set(item.node_id, value);
      return value;
    }
    const groups = new Map();
    nodes.forEach(item => {
      const itemLevel = level(item);
      if (!groups.has(itemLevel)) groups.set(itemLevel, []);
      groups.get(itemLevel).push(item);
    });
    const maxLevel = Math.max(0, ...groups.keys());
    const maxRows = Math.max(1, ...Array.from(groups.values()).map(group => group.length));
    const width = Math.max(820, 230 + maxLevel * 210);
    const height = Math.max(390, 110 + maxRows * 104);
    const positions = new Map();
    groups.forEach((group, itemLevel) => {
      const offset = (height - group.length * 104) / 2;
      group.forEach((item, index) => positions.set(item.node_id, {
        x: 36 + itemLevel * 210,
        y: offset + index * 104
      }));
    });
    return { width, height, positions };
  }

  function nodeValidation(item) {
    if (state.catalog.conflict && item.node_id === 'revision-editor') return 'conflict';
    if (state.validation.kind === 'invalid' && state.validation.errors.some(error => error.includes(item.node_id))) return 'invalid';
    return state.validation.kind === 'valid' ? 'valid' : 'unvalidated';
  }

  function renderGraph(roadmap) {
    const nodes = list(roadmap.nodes);
    const layout = graphLayout(nodes);
    const byId = new Map(nodes.map(item => [item.node_id, item]));
    const edges = list(roadmap.edges).length
      ? list(roadmap.edges)
      : nodes.flatMap(item => list(item.depends_on).map(dependency => ({ from: dependency, to: item.node_id, kind: 'depends_on' })));
    return `
      <section class="pde-graph-panel" aria-label="Definition dependency graph">
        <div class="pde-graph-context">
          <span><strong>Dependency graph</strong><small>Color describes node kind and definition validation only.</small></span>
          <div class="pde-legend" aria-label="Graph legend">
            <span data-kind="work">Work</span><span data-kind="gate">Gate</span><span data-kind="milestone">Milestone</span><span data-kind="group">Group</span>
          </div>
        </div>
        <div class="pde-graph-scroll">
          <div class="pde-graph-canvas" style="width:${layout.width}px;height:${layout.height}px">
            <svg viewBox="0 0 ${layout.width} ${layout.height}" aria-hidden="true">
              <defs><marker id="pde-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z"></path></marker></defs>
              ${edges.map(edge => {
                const start = layout.positions.get(edge.from);
                const end = layout.positions.get(edge.to);
                if (!start || !end || !byId.has(edge.from) || !byId.has(edge.to)) return '';
                const x1 = start.x + 166;
                const y1 = start.y + 34;
                const x2 = end.x;
                const y2 = end.y + 34;
                const bend = Math.max(34, (x2 - x1) * 0.44);
                return `<path class="pde-edge" data-edge-kind="${escapeHtml(edge.kind || 'depends_on')}" d="M${x1} ${y1} C${x1 + bend} ${y1},${x2 - bend} ${y2},${x2} ${y2}"></path>`;
              }).join('')}
            </svg>
            ${nodes.map(item => {
              const position = layout.positions.get(item.node_id);
              const validation = nodeValidation(item);
              return `
                <button class="pde-node${item.node_id === state.selectedNodeId ? ' selected' : ''}" type="button"
                  style="left:${position.x}px;top:${position.y}px"
                  data-pde-node="${escapeHtml(item.node_id)}"
                  data-node-kind="${escapeHtml(item.kind)}"
                  data-definition-validation="${escapeHtml(validation)}">
                  <span>${escapeHtml(item.kind)}</span>
                  <strong>${escapeHtml(item.title)}</strong>
                  <small>${escapeHtml(item.node_id)}</small>
                </button>
              `;
            }).join('')}
          </div>
        </div>
      </section>
    `;
  }

  function fieldValue(roadmap, field) {
    const value = roadmap[field];
    return Array.isArray(value) ? value.join('\n') : String(value || '');
  }

  function renderDefinition(roadmap, project) {
    const disabled = state.editing ? '' : 'disabled';
    return `
      <section class="pde-definition" aria-label="Roadmap definition document">
        <div class="pde-definition-intro">
          <strong>Roadmap document</strong>
          <span>${state.editing ? 'Local draft · changes remain in this preview.' : 'Approved source revision · select Edit definition to prepare a draft.'}</span>
        </div>
        <div class="pde-form-grid">
          <label><span>Title</span><input class="pde-input" data-pde-field="title" value="${escapeHtml(fieldValue(roadmap, 'title'))}" ${disabled}></label>
          <label class="wide"><span>Objective</span><textarea class="pde-input" data-pde-field="objective" rows="4" ${disabled}>${escapeHtml(fieldValue(roadmap, 'objective'))}</textarea></label>
          <label><span>Assumptions <small>one per line</small></span><textarea class="pde-input" data-pde-field="assumptions" rows="5" ${disabled}>${escapeHtml(fieldValue(roadmap, 'assumptions'))}</textarea></label>
          <label><span>Constraints <small>one per line</small></span><textarea class="pde-input" data-pde-field="constraints" rows="5" ${disabled}>${escapeHtml(fieldValue(roadmap, 'constraints'))}</textarea></label>
        </div>
        <section class="pde-scope-readback">
          <div><strong>Project scope · in</strong><p>${list(project.scope && project.scope.in).map(escapeHtml).join(' · ') || '—'}</p></div>
          <div><strong>Project scope · out</strong><p>${list(project.scope && project.scope.out).map(escapeHtml).join(' · ') || '—'}</p></div>
        </section>
        ${renderDraftPanel()}
      </section>
    `;
  }

  function diffRows() {
    if (!state.draft || !sourceRoadmap()) return [];
    return ['title', 'objective', 'assumptions', 'constraints'].flatMap(field => {
      const before = fieldValue(sourceRoadmap(), field);
      const after = fieldValue(state.draft, field);
      return before === after ? [] : [{ field, before, after }];
    });
  }

  function renderDraftPanel() {
    if (!state.editing && !state.localPreviewAccepted) return '';
    const rows = diffRows();
    const errors = list(state.validation.errors);
    return `
      <section class="pde-draft-panel" data-pde-draft-panel>
        <header><span><strong>${state.localPreviewAccepted ? 'Accepted preview' : 'Draft diff'}</strong><small>${state.localPreviewAccepted ? 'The approved source remains unchanged.' : `${rows.length} changed field${rows.length === 1 ? '' : 's'}`}</small></span></header>
        ${rows.length ? `<div class="pde-diff-list">${rows.map(row => `
          <article><strong>${escapeHtml(row.field)}</strong><del>${escapeHtml(row.before || '∅')}</del><ins>${escapeHtml(row.after || '∅')}</ins></article>
        `).join('')}</div>` : '<p class="pde-diff-empty">No definition fields changed.</p>'}
        ${errors.length ? `<ul class="pde-errors">${errors.map(error => `<li>${escapeHtml(error)}</li>`).join('')}</ul>` : ''}
        <footer>
          ${state.editing ? `
            <button class="pde-button" type="button" data-pde-action="discard">Discard</button>
            <button class="pde-button" type="button" data-pde-action="validate">Validate</button>
            <button class="pde-button primary" type="button" data-pde-action="accept-preview" ${state.validation.kind !== 'valid' || !rows.length ? 'disabled' : ''}>Accept preview</button>
          ` : ''}
          ${state.localPreviewAccepted ? '<button class="pde-button primary" type="button" data-pde-action="undo">Undo preview</button>' : ''}
        </footer>
      </section>
    `;
  }

  function renderInspector(roadmap) {
    const nodes = list(roadmap.nodes);
    const selected = nodes.find(item => item.node_id === state.selectedNodeId) || nodes[0];
    const gates = list(roadmap.gates);
    const selectedGates = gates.filter(gate => list(selected && selected.gate_ids).includes(gate.gate_id) || list(gate.blocks).includes(selected && selected.node_id));
    const done = roadmap.done_contract || {};
    return `
      <aside class="pde-inspector" aria-label="Definition detail">
        <div class="pde-inspector-head"><span>${escapeHtml(selected && selected.kind || 'roadmap')}</span><strong>${escapeHtml(selected && selected.title || roadmap.title)}</strong></div>
        <section>
          <h3>Objective</h3>
          <p>${escapeHtml(selected && selected.objective || roadmap.objective)}</p>
        </section>
        <section>
          <h3>Dependencies</h3>
          ${renderTokens(list(selected && selected.depends_on), 'None')}
        </section>
        <section>
          <h3>Deliverables</h3>
          ${renderList(list(selected && selected.deliverables), 'No deliverables declared.')}
        </section>
        <section>
          <h3>Allowed paths</h3>
          ${renderCodeList(list(selected && selected.allowed_paths), 'No path declared.')}
        </section>
        ${selectedGates.length ? `<section><h3>Gate definitions</h3>${selectedGates.map(renderGate).join('')}</section>` : ''}
        <section class="pde-done-contract">
          <h3>Done contract</h3>
          <dl>
            <div><dt>Required nodes</dt><dd>${list(done.required_node_ids).length}</dd></div>
            <div><dt>Required gates</dt><dd>${list(done.required_gate_ids).length}</dd></div>
            <div><dt>Rule</dt><dd>${escapeHtml(done.completion_rule || '—')}</dd></div>
          </dl>
          ${renderList(list(done.verification_rules).map(rule => rule.description), 'No verification rules declared.')}
        </section>
      </aside>
    `;
  }

  function renderTokens(items, empty) {
    return items.length ? `<div class="pde-tokens">${items.map(item => `<button type="button" data-pde-node="${escapeHtml(item)}">${escapeHtml(item)}</button>`).join('')}</div>` : `<p class="pde-muted">${escapeHtml(empty)}</p>`;
  }

  function renderList(items, empty) {
    return items.length ? `<ul>${items.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : `<p class="pde-muted">${escapeHtml(empty)}</p>`;
  }

  function renderCodeList(items, empty) {
    return items.length ? `<div class="pde-code-list">${items.map(item => `<code>${escapeHtml(item)}</code>`).join('')}</div>` : `<p class="pde-muted">${escapeHtml(empty)}</p>`;
  }

  function renderGate(gate) {
    return `
      <article class="pde-gate-definition">
        <span>${escapeHtml(gate.kind)}</span>
        <strong>${escapeHtml(gate.title)}</strong>
        <p>${escapeHtml(gate.decision_needed)}</p>
        <small>Safe default · ${escapeHtml(gate.safe_default)}</small>
      </article>
    `;
  }

  function validateDraft() {
    const roadmap = state.draft;
    const errors = [];
    if (!roadmap || !String(roadmap.title || '').trim()) errors.push('roadmap: title is required');
    if (!roadmap || !String(roadmap.objective || '').trim()) errors.push('roadmap: objective is required');
    const ids = new Set();
    list(roadmap && roadmap.nodes).forEach(item => {
      if (!item.node_id || ids.has(item.node_id)) errors.push(`${item.node_id || 'node'}: node identifier must be unique`);
      ids.add(item.node_id);
    });
    list(roadmap && roadmap.nodes).forEach(item => {
      list(item.depends_on).forEach(dependency => {
        if (!ids.has(dependency)) errors.push(`${item.node_id}: missing dependency ${dependency}`);
      });
    });
    state.validation = { kind: errors.length ? 'invalid' : 'valid', errors };
    state.message = errors.length ? 'Draft validation found definition errors.' : 'Draft validation passed.';
    render();
  }

  function beginEdit() {
    if (!sourceRoadmap() || state.localPreviewAccepted) return;
    state.draft = clone(sourceRoadmap());
    state.editing = true;
    state.view = 'definition';
    state.validation = { kind: 'unvalidated', errors: [] };
    state.message = 'Local draft opened. The approved source is unchanged.';
    render();
  }

  function discardDraft() {
    state.draft = null;
    state.editing = false;
    state.validation = { kind: 'unvalidated', errors: [] };
    state.message = 'Draft discarded. The approved source is unchanged.';
    render();
  }

  function acceptPreview() {
    if (!state.draft || state.validation.kind !== 'valid' || !diffRows().length) return;
    state.undoSnapshot = clone(sourceRoadmap());
    state.catalog.readModel.roadmap = clone(state.draft);
    state.catalog.readModel.graph = {
      nodes: clone(state.draft.nodes),
      edges: clone(state.draft.edges),
      gate_definitions: clone(state.draft.gates)
    };
    state.draft = null;
    state.editing = false;
    state.undoReadbackHash = '';
    state.localPreviewAccepted = true;
    state.message = 'Preview accepted locally. No definition source was written.';
    render();
  }

  function undoPreview() {
    if (!state.undoSnapshot) return;
    const expectedHash = state.undoSnapshot.content_hash;
    state.catalog.readModel.roadmap = clone(state.undoSnapshot);
    state.catalog.readModel.graph = {
      nodes: clone(state.undoSnapshot.nodes),
      edges: clone(state.undoSnapshot.edges),
      gate_definitions: clone(state.undoSnapshot.gates)
    };
    state.undoSnapshot = null;
    state.localPreviewAccepted = false;
    state.validation = { kind: 'unvalidated', errors: [] };
    state.undoReadbackHash = state.catalog.readModel.roadmap.content_hash;
    state.message = state.undoReadbackHash === expectedHash
      ? `Preview undone. Approved source ${shortHash(state.undoReadbackHash)} reread locally.`
      : 'Undo readback did not match the approved source hash.';
    render();
  }

  async function selectRoadmap(roadmapId) {
    if (!roadmapId || roadmapId === (sourceRoadmap() && sourceRoadmap().roadmap_id)) return;
    state.message = '';
    state.draft = null;
    state.editing = false;
    state.localPreviewAccepted = false;
    state.undoSnapshot = null;
    state.undoReadbackHash = '';
    state.validation = { kind: 'unvalidated', errors: [] };
    if (state.catalog.source === 'fixture') {
      const origin = state.catalog.readModel.origin && state.catalog.readModel.origin.state;
      state.catalog.readModel = fixtures.readModelFor(roadmapId, origin);
      state.selectedNodeId = state.catalog.readModel.roadmap.nodes[0] && state.catalog.readModel.roadmap.nodes[0].node_id;
      render();
      return;
    }
    renderLoading();
    try {
      const summary = list(state.catalog.roadmaps).find(item => item.roadmap_id === roadmapId);
      const revision = summary && (summary.latest_approved_revision || summary.newest_revision);
      state.catalog.readModel = await api.getRoadmap(currentProject().project_id, roadmapId, revision);
      state.selectedNodeId = state.catalog.readModel.roadmap.nodes[0] && state.catalog.readModel.roadmap.nodes[0].node_id;
      render();
    } catch (error) {
      state.catalog = { source: 'live', scenario: 'error', message: error.message, readModel: null };
      renderUnavailable();
    }
  }

  async function openInAgent() {
    const roadmap = sourceRoadmap();
    const project = currentProject();
    if (!roadmap || state.draft || state.localPreviewAccepted || state.catalog.conflict) return;
    const button = root.querySelector('[data-pde-action="handoff"]');
    if (button) {
      button.disabled = true;
      button.textContent = 'Preparing…';
    }
    try {
      const envelope = state.catalog.source === 'live'
        ? await api.createAgentHandoff(project.project_id, roadmap.roadmap_id, roadmap.revision, roadmap.content_hash)
        : {
            schema_id: 'odysseus.agent.plan_handoff.v1',
            project_id: project.project_id,
            roadmap_id: roadmap.roadmap_id,
            revision: roadmap.revision,
            content_hash: roadmap.content_hash,
            title: roadmap.title,
            requested_entrypoint: '/abc',
            composer_text: `/abc run roadmap:${roadmap.roadmap_id}@${roadmap.revision} hash:${roadmap.content_hash}`,
            launch_authorized: false,
            read_only: true
          };
      if (envelope.launch_authorized !== false || envelope.read_only !== true || !String(envelope.composer_text || '').startsWith('/abc run roadmap:')) {
        throw new Error('Planning handoff was not a read-only composer envelope.');
      }
      window.dispatchEvent(new CustomEvent('harbor:planning-agent-handoff', {
        detail: {
          composerText: envelope.composer_text,
          projectId: envelope.project_id,
          roadmapId: envelope.roadmap_id,
          revision: envelope.revision,
          contentHash: envelope.content_hash
        }
      }));
    } catch (error) {
      state.message = error.message || 'The Agent handoff could not be prepared.';
      render();
    }
  }

  root.addEventListener('click', event => {
    const viewButton = event.target.closest('[data-pde-view]');
    if (viewButton) {
      state.view = viewButton.dataset.pdeView;
      render();
      return;
    }
    const nodeButton = event.target.closest('[data-pde-node]');
    if (nodeButton) {
      state.selectedNodeId = nodeButton.dataset.pdeNode;
      if (state.view !== 'graph') state.view = 'graph';
      render();
      return;
    }
    const roadmapButton = event.target.closest('[data-pde-roadmap]');
    if (roadmapButton) {
      selectRoadmap(roadmapButton.dataset.pdeRoadmap);
      return;
    }
    const actionButton = event.target.closest('[data-pde-action]');
    if (!actionButton) return;
    const actions = {
      edit: beginEdit,
      discard: discardDraft,
      validate: validateDraft,
      'accept-preview': acceptPreview,
      undo: undoPreview,
      handoff: openInAgent,
      'load-fixture': () => {
        state.catalog = fixtures.scenario('fixture');
        state.selectedNodeId = state.catalog.readModel.roadmap.nodes[0].node_id;
        state.message = 'Labeled preview loaded. It is not source data.';
        render();
      }
    };
    actions[actionButton.dataset.pdeAction]?.();
  });

  root.addEventListener('input', event => {
    if (event.target.matches('[data-pde-search]')) {
      state.search = event.target.value;
      const selectionStart = event.target.selectionStart;
      render();
      const input = root.querySelector('[data-pde-search]');
      input && input.focus();
      input && input.setSelectionRange(selectionStart, selectionStart);
      return;
    }
    if (!event.target.matches('[data-pde-field]') || !state.draft) return;
    const field = event.target.dataset.pdeField;
    state.draft[field] = field === 'assumptions' || field === 'constraints'
      ? event.target.value.split('\n').map(item => item.trim()).filter(Boolean)
      : event.target.value;
    state.validation = { kind: 'unvalidated', errors: [] };
    const panel = root.querySelector('[data-pde-draft-panel]');
    if (panel) panel.outerHTML = renderDraftPanel();
    const pill = root.querySelector('.pde-validation-pill');
    if (pill) {
      pill.dataset.validation = 'unvalidated';
      pill.textContent = 'unvalidated';
    }
  });

  function applyFixtureNotification(catalog) {
    const notification = state.notification;
    if (!notification || notification.workspace !== 'planning') return catalog;
    const project = catalog.project || catalog.readModel && catalog.readModel.project;
    if (!project || project.project_id !== notification.projectId) {
      throw new Error('The fixture does not contain the requested project definition.');
    }
    if (!notification.roadmapId) return catalog;
    const summary = list(catalog.roadmaps).find(item => item.roadmap_id === notification.roadmapId);
    if (!summary) throw new Error('The fixture does not contain the requested roadmap definition.');
    const origin = catalog.readModel && catalog.readModel.origin && catalog.readModel.origin.state;
    const readModel = fixtures.readModelFor(notification.roadmapId, origin);
    if (notification.revision && readModel.roadmap.revision !== notification.revision) {
      throw new Error('The fixture does not contain the requested definition revision.');
    }
    return { ...catalog, readModel };
  }

  async function applyLiveNotification(catalog) {
    const notification = state.notification;
    if (!notification || notification.workspace !== 'planning') return catalog;
    const sameProject = catalog.project && catalog.project.project_id === notification.projectId;
    const projectModel = sameProject ? { project: catalog.project } : await api.getProject(notification.projectId);
    const roadmapPage = sameProject
      ? { items: catalog.roadmaps }
      : await api.listRoadmaps(notification.projectId);
    const roadmaps = list(roadmapPage.items);
    if (!notification.roadmapId) {
      return { ...catalog, project: projectModel.project, roadmaps };
    }
    const summary = roadmaps.find(item => item.roadmap_id === notification.roadmapId);
    if (!summary) throw new Error('The notification roadmap definition was not found.');
    const revision = notification.revision || summary.latest_approved_revision || summary.newest_revision;
    const readModel = await api.getRoadmap(notification.projectId, notification.roadmapId, revision);
    if (
      !readModel.roadmap ||
      readModel.roadmap.project_id !== notification.projectId ||
      readModel.roadmap.roadmap_id !== notification.roadmapId ||
      (notification.revision && readModel.roadmap.revision !== notification.revision)
    ) {
      throw new Error('The definition notification resolved a different revision.');
    }
    return {
      ...catalog,
      project: projectModel.project,
      roadmaps,
      readModel,
      scenario: readModel.origin && readModel.origin.state || 'live'
    };
  }

  async function boot() {
    renderLoading();
    if (state.notification && state.notification.workspace === 'agent') {
      window.dispatchEvent(new CustomEvent('harbor:notification-workspace-route', {
        detail: { workspace: 'agent', acceptedByPlanning: false }
      }));
      state.catalog = {
        source: 'notification', scenario: 'unavailable', projects: [], project: null,
        roadmaps: [], readModel: null, message: 'Execution notifications are handled in Agent.'
      };
      root.dataset.notificationTarget = 'agent';
      renderUnavailable();
      return;
    }
    if (state.notification && state.notification.workspace === 'invalid') {
      state.catalog = {
        source: 'notification', scenario: 'error', projects: [], project: null,
        roadmaps: [], readModel: null, message: state.notification.message
      };
      root.dataset.notificationTarget = 'invalid';
      renderUnavailable();
      return;
    }
    const fixtureScenarios = new Set(['fixture', 'stale', 'unavailable', 'error', 'conflict', 'empty']);
    if (sourceMode === 'fixture' || fixtureScenarios.has(requestedScenario) || window.location.protocol === 'file:') {
      try {
        state.catalog = applyFixtureNotification(fixtures.scenario(requestedScenario || 'fixture'));
      } catch (error) {
        state.catalog = {
          source: 'fixture', scenario: 'error', projects: [], project: null,
          roadmaps: [], readModel: null, message: error.message
        };
      }
      state.selectedNodeId = state.catalog.readModel && state.catalog.readModel.roadmap.nodes[0] && state.catalog.readModel.roadmap.nodes[0].node_id || '';
      render();
      return;
    }
    try {
      state.catalog = await applyLiveNotification(await api.loadCatalog());
      state.selectedNodeId = state.catalog.readModel && state.catalog.readModel.roadmap.nodes[0] && state.catalog.readModel.roadmap.nodes[0].node_id || '';
      render();
    } catch (error) {
      state.catalog = {
        source: 'live',
        scenario: error && error.code === 'runtime_payload_rejected' ? 'error' : 'unavailable',
        projects: [],
        project: null,
        roadmaps: [],
        readModel: null,
        message: error && error.message || 'The definition source is unavailable.'
      };
      renderUnavailable();
    }
  }

  window.HarborPlanning = Object.freeze({
    boot,
    getState: () => clone(state)
  });
  boot();
})();
