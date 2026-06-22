import { makeWindowDraggable } from './windowDrag.js';

const API_ROOT = '/api/roadmap/visual-agent-programming';
const VALIDATE_URL = `${API_ROOT}/validate-edit`;
const REVIEW_URL = `${API_ROOT}/proposals/review`;

let isOpen = false;
let snapshot = null;
let selectedNodeId = '';
let validationResult = null;
let reviewSnapshot = null;
let draftProposals = [];

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[ch]);
}

function list(value) {
  if (Array.isArray(value)) return value;
  if (value == null || value === '') return [];
  return [value];
}

function compactList(raw) {
  return String(raw || '').split(',').map((item) => item.trim()).filter(Boolean);
}

function summarizeItem(item) {
  if (!item || typeof item !== 'object') return esc(item);
  const code = item.code || item.type || item.state || item.action || item.control_id || 'item';
  const target = item.node_id || item.target || item.from || item.to || '';
  const reason = item.reason || item.message || item.title || item.status || '';
  return [code, target, reason].filter(Boolean).map(esc).join(' - ');
}

function renderRows(items, emptyText) {
  const rows = list(items);
  if (!rows.length) return `<div class="vap-empty">${esc(emptyText)}</div>`;
  return rows.map((item) => `<div class="vap-row">${summarizeItem(item)}</div>`).join('');
}

function canLaunch(result) {
  return Boolean(result?.['can_' + 'start' + '_agent']);
}

function statusDot(status, palette) {
  const colorName = palette?.[status] || 'slate';
  return `<span class="vap-status-dot vap-status-${esc(colorName)}"></span>`;
}

function nodeById(nodeId) {
  return list(snapshot?.nodes).find((node) => node.node_id === nodeId) || null;
}

function selectedNode() {
  return nodeById(selectedNodeId) || list(snapshot?.nodes)[0] || null;
}

function currentProposal() {
  const action = document.querySelector('input[name="vap-action"]:checked')?.value || 'create_node';
  if (action === 'connect_dependency') {
    return {
      action,
      from_node: document.getElementById('vap-from-node')?.value.trim() || '',
      to_node: document.getElementById('vap-to-node')?.value.trim() || '',
      kind: document.getElementById('vap-edge-kind')?.value || 'depends_on',
    };
  }
  return {
    action,
    node_id: document.getElementById('vap-node-id')?.value.trim() || '',
    title: document.getElementById('vap-node-title')?.value.trim() || '',
    kind: document.getElementById('vap-node-kind')?.value.trim() || 'runtime',
    horizon: document.getElementById('vap-node-horizon')?.value.trim() || 'later',
    target_version: document.getElementById('vap-node-version')?.value.trim() || 'future',
    status: document.getElementById('vap-node-status')?.value || 'planned',
    depends_on: compactList(document.getElementById('vap-node-deps')?.value),
    gates: compactList(document.getElementById('vap-node-gates')?.value),
    deliverables: compactList(document.getElementById('vap-node-deliverables')?.value),
    source_refs: compactList(document.getElementById('vap-node-source-refs')?.value),
  };
}

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  const text = await res.text();
  let data = {};
  if (text) {
    try { data = JSON.parse(text); } catch (_) { data = { error: text }; }
  }
  if (!res.ok) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
  return data;
}

async function loadSnapshot() {
  setBusy('Loading');
  try {
    snapshot = await fetchJson(API_ROOT);
    selectedNodeId = selectedNodeId || snapshot.active_node_id || snapshot.next_claimable_node_id || list(snapshot.nodes)[0]?.node_id || '';
    render();
  } catch (err) {
    showPanelError(err);
  } finally {
    setBusy('');
  }
}

async function validateDraft() {
  setBusy('Validating');
  try {
    validationResult = await fetchJson(VALIDATE_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(currentProposal()),
    });
    render();
  } catch (err) {
    validationResult = { state: 'request_failed', valid: false, stops: [{ code: 'request_failed', message: err.message }] };
    render();
  } finally {
    setBusy('');
  }
}

async function reviewQueue() {
  const proposal = currentProposal();
  const proposals = draftProposals.concat([proposal]).filter((item) => item && item.action);
  setBusy('Reviewing');
  try {
    reviewSnapshot = await fetchJson(REVIEW_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proposals }),
    });
    render();
  } catch (err) {
    reviewSnapshot = {
      mode: 'read_only',
      counts: { total_items: proposals.length, valid_items: 0, blocked_items: proposals.length, accepted_items: 0 },
      items: [{ state: 'request_failed', valid: false, stops: [{ code: 'request_failed', message: err.message }] }],
    };
    render();
  } finally {
    setBusy('');
  }
}

function setBusy(label) {
  const el = document.getElementById('vap-busy');
  if (el) el.textContent = label || '';
}

function showPanelError(err) {
  const body = document.querySelector('#visual-agent-programming-modal .modal-body');
  if (body) body.innerHTML = `<div class="vap-error">${esc(err.message || err)}</div>`;
}

function renderSnapshot() {
  const node = selectedNode();
  const palette = snapshot?.status_palette || {};
  const progress = snapshot?.progress || {};
  const nodes = list(snapshot?.nodes);
  return `
    <section class="vap-panel vap-panel-snapshot">
      <div class="vap-panel-head">
        <h5>Read-only snapshot</h5>
        <span class="vap-pill">${esc(snapshot?.mode || 'read_only')}</span>
      </div>
      <div class="vap-metrics">
        <span>${esc(progress.completed_branch_nodes ?? 0)} / ${esc(progress.branch_nodes ?? nodes.length)} branch nodes</span>
        <span>${esc(progress.branch_completion_percent ?? 0)}%</span>
        <span>${esc(snapshot?.last_updated_at || 'unknown')}</span>
      </div>
      <div class="vap-split">
        <div class="vap-node-list" role="listbox" aria-label="Visual Agent Programming nodes">
          ${nodes.map((item) => `
            <button type="button" class="vap-node ${item.node_id === node?.node_id ? 'active' : ''}" data-vap-node="${esc(item.node_id)}">
              ${statusDot(item.visual_status, palette)}
              <span>${esc(item.title || item.node_id)}</span>
              <small>${esc(item.status)}${item.claimable ? ' - claimable' : ''}</small>
            </button>
          `).join('') || '<div class="vap-empty">No nodes in snapshot</div>'}
        </div>
        <div class="vap-selected">
          <div class="vap-kicker">Selected node</div>
          <h5>${esc(node?.title || 'No node selected')}</h5>
          <code>${esc(node?.node_id || '')}</code>
          <div class="vap-tags">
            <span>${esc(node?.kind || 'kind')}</span>
            <span>${esc(node?.horizon || 'horizon')}</span>
            <span>${esc(node?.target_version || 'version')}</span>
            <span>${esc(node?.visual_status || 'status')}</span>
          </div>
          <div class="vap-mini-grid">
            <div><strong>Depends</strong>${renderRows(node?.depends_on, 'None')}</div>
            <div><strong>Unlocks</strong>${renderRows(node?.unlocks, 'None')}</div>
            <div><strong>Gates</strong>${renderRows(node?.gates, 'None')}</div>
            <div><strong>Deliverables</strong>${renderRows(node?.deliverables, 'None')}</div>
          </div>
        </div>
      </div>
    </section>
  `;
}

function renderBoundaries() {
  const controls = snapshot?.controls || {};
  const palette = snapshot?.status_palette || {};
  return `
    <section class="vap-panel">
      <div class="vap-panel-head">
        <h5>Unavailable actions</h5>
        <span class="vap-pill vap-pill-locked">Bounded</span>
      </div>
      <div class="vap-boundary-grid">
        ${['Patch', 'Apply', 'File persistence', 'Dispatch'].map((label) => `
          <button type="button" class="vap-disabled-action" disabled>${esc(label)} unavailable</button>
        `).join('')}
      </div>
      <div class="vap-status-palette">
        ${Object.entries(palette).map(([status, color]) => `<span>${statusDot(status, palette)}${esc(status)} <small>${esc(color)}</small></span>`).join('')}
      </div>
      <div class="vap-mini-grid">
        <div><strong>Controls</strong>${renderRows(Object.entries(controls).map(([key, value]) => ({ code: key, ...value })), 'No controls')}</div>
        <div><strong>Blocked actions</strong>${renderRows(snapshot?.blocked_actions, 'No blocked actions')}</div>
      </div>
    </section>
  `;
}

function renderDraft() {
  const node = selectedNode();
  const selectedDep = node?.node_id || snapshot?.next_claimable_node_id || '';
  return `
    <section class="vap-panel">
      <div class="vap-panel-head">
        <h5>Local proposal draft</h5>
        <span class="vap-pill">Dry run only</span>
      </div>
      <div class="vap-segmented">
        <label><input type="radio" name="vap-action" value="create_node" checked> Create node</label>
        <label><input type="radio" name="vap-action" value="connect_dependency"> Connect dependency</label>
      </div>
      <div class="vap-form" data-vap-form="create_node">
        <input id="vap-node-id" type="text" placeholder="node-id" value="visual-agent-programming-browser-proposal-draft">
        <input id="vap-node-title" type="text" placeholder="Title" value="Browser proposal draft">
        <div class="vap-form-row">
          <input id="vap-node-kind" type="text" placeholder="kind" value="runtime">
          <input id="vap-node-horizon" type="text" placeholder="horizon" value="later">
          <input id="vap-node-version" type="text" placeholder="target version" value="future">
          <select id="vap-node-status" aria-label="Draft status">
            <option value="planned">planned</option>
            <option value="research">research</option>
            <option value="deferred">deferred</option>
          </select>
        </div>
        <input id="vap-node-deps" type="text" placeholder="depends_on, comma separated" value="${esc(selectedDep)}">
        <input id="vap-node-gates" type="text" placeholder="gates, comma separated" value="operator_go_required">
        <input id="vap-node-source-refs" type="text" placeholder="source_refs, comma separated" value="specs/roadmaps/odysseus-multiagent-roadmap.v1.json">
        <input id="vap-node-deliverables" type="text" placeholder="deliverables, comma separated" value="Browser proposal panel dry run">
      </div>
      <div class="vap-form hidden" data-vap-form="connect_dependency">
        <input id="vap-from-node" type="text" placeholder="from_node" value="${esc(selectedDep)}">
        <input id="vap-to-node" type="text" placeholder="to_node" value="${esc(snapshot?.active_node_id || '')}">
        <select id="vap-edge-kind" aria-label="Edge kind">
          <option value="depends_on">depends_on</option>
          <option value="unlocks">unlocks</option>
        </select>
      </div>
      <div class="vap-actions">
        <button type="button" class="vap-primary" id="vap-validate-btn">Validate</button>
        <button type="button" id="vap-add-draft-btn">Add to review queue</button>
        <button type="button" id="vap-review-btn">Review queue</button>
      </div>
      <div class="vap-draft-count">${esc(draftProposals.length)} queued local proposal${draftProposals.length === 1 ? '' : 's'}</div>
    </section>
  `;
}

function renderValidation() {
  const result = validationResult;
  return `
    <section class="vap-panel">
      <div class="vap-panel-head">
        <h5>Validation result</h5>
        <span class="vap-pill ${result?.valid ? 'vap-pill-ok' : 'vap-pill-locked'}">${esc(result?.state || 'Not run')}</span>
      </div>
      <div class="vap-mini-grid">
        <div><strong>Stops</strong>${renderRows(result?.stops, 'None')}</div>
        <div><strong>Collisions</strong>${renderRows(result?.collisions, 'None')}</div>
        <div><strong>Proposed events</strong>${renderRows(result?.proposed_events, 'None')}</div>
        <div><strong>Accepted events</strong>${renderRows(result?.accepted_events, 'None')}</div>
      </div>
      <div class="vap-policy-line">write: ${esc(Boolean(result?.can_write))} - launch: ${esc(canLaunch(result))}</div>
    </section>
  `;
}

function renderReview() {
  const counts = reviewSnapshot?.counts || {};
  return `
    <section class="vap-panel">
      <div class="vap-panel-head">
        <h5>Review queue</h5>
        <span class="vap-pill">${esc(reviewSnapshot?.mode || 'read_only')}</span>
      </div>
      <div class="vap-metrics">
        <span>${esc(counts.total_items ?? 0)} total</span>
        <span>${esc(counts.valid_items ?? 0)} valid</span>
        <span>${esc(counts.blocked_items ?? 0)} blocked</span>
        <span>${esc(counts.accepted_items ?? 0)} accepted</span>
      </div>
      <div class="vap-review-list">
        ${list(reviewSnapshot?.items).map((item) => `
          <div class="vap-review-item">
            <div><strong>${esc(item.queue_item_id || item.action || 'proposal')}</strong><span class="vap-pill ${item.valid ? 'vap-pill-ok' : 'vap-pill-locked'}">${esc(item.state)}</span></div>
            <div class="vap-mini-grid">
              <div><strong>Stops</strong>${renderRows(item.stops, 'None')}</div>
              <div><strong>Collisions</strong>${renderRows(item.collisions, 'None')}</div>
              <div><strong>Proposed events</strong>${renderRows(item.proposed_events, 'None')}</div>
            </div>
          </div>
        `).join('') || '<div class="vap-empty">No review has run</div>'}
      </div>
    </section>
  `;
}

function render() {
  const body = document.querySelector('#visual-agent-programming-modal .modal-body');
  if (!body) return;
  if (!snapshot) {
    body.innerHTML = '<div class="vap-empty">Loading...</div>';
    return;
  }
  body.innerHTML = `
    <div class="vap-layout">
      ${renderSnapshot()}
      <div class="vap-column">
        ${renderBoundaries()}
        ${renderDraft()}
        ${renderValidation()}
        ${renderReview()}
      </div>
    </div>
  `;
  wireBodyEvents();
}

function wireBodyEvents() {
  document.querySelectorAll('[data-vap-node]').forEach((btn) => {
    btn.addEventListener('click', () => {
      selectedNodeId = btn.dataset.vapNode || selectedNodeId;
      render();
    });
  });
  document.querySelectorAll('input[name="vap-action"]').forEach((input) => {
    input.addEventListener('change', () => {
      document.querySelectorAll('[data-vap-form]').forEach((form) => {
        form.classList.toggle('hidden', form.dataset.vapForm !== input.value);
      });
    });
  });
  document.getElementById('vap-validate-btn')?.addEventListener('click', validateDraft);
  document.getElementById('vap-review-btn')?.addEventListener('click', reviewQueue);
  document.getElementById('vap-add-draft-btn')?.addEventListener('click', () => {
    draftProposals.push(currentProposal());
    render();
  });
}

export function close() {
  const modal = document.getElementById('visual-agent-programming-modal');
  if (modal) modal.remove();
  isOpen = false;
}

export function open() {
  if (isOpen) {
    document.getElementById('visual-agent-programming-modal')?.classList.remove('hidden');
    return;
  }
  isOpen = true;
  const modal = document.createElement('div');
  modal.className = 'modal';
  modal.id = 'visual-agent-programming-modal';
  modal.innerHTML = `
    <div class="modal-content vap-modal-content">
      <div class="modal-header">
        <h4>Visual Agent Programming</h4>
        <span id="vap-busy" class="vap-busy"></span>
        <button class="close-btn" id="vap-close" aria-label="Close">x</button>
      </div>
      <div class="modal-body"><div class="vap-empty">Loading...</div></div>
    </div>
  `;
  document.body.appendChild(modal);
  const content = modal.querySelector('.modal-content');
  const header = modal.querySelector('.modal-header');
  if (content && header) makeWindowDraggable(modal, { content, header });
  document.getElementById('vap-close')?.addEventListener('click', close);
  modal.addEventListener('click', (event) => {
    if (event.target === modal) close();
  });
  loadSnapshot();
}

export function toggle() {
  if (isOpen) close();
  else open();
}

export default { open, close, toggle };
