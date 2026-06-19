/**
 * Obsidian Plugin for Odysseus — Panel-based UI (Option B)
 * 
 * Injects a right-docked panel (like Notes) instead of a centered modal.
 * Features: File tree, Split Editor with Live Preview, Wiki-Links, Autosave, Fulltext Search
 * 
 * Architecture:
 *   - Panel is a fixed-position div on the right side
 *   - Toggle via body class "obsidian-open" (adds CSS transition)
 *   - Chat content shrinks when panel opens (via CSS)
 */

import { styledConfirm, styledPrompt, showToast } from '/static/js/ui.js';
import * as Modals from '/static/js/modalManager.js';
import { makeWindowDraggable } from '/static/js/windowDrag.js';
import { clearDockSide } from '/static/js/modalSnap.js';
import { createWhirlpool } from '/static/js/spinner.js';
import { mdToHtml, renderMermaid } from '/static/js/markdown.js';

// Dynamic stylesheet insertion
const link = document.createElement('link');
link.rel = 'stylesheet';
link.href = '/api/plugins/obsidian/web/style.css?v=project-sessions-v1';
document.head.appendChild(link);

// ─── State ───────────────────────────────────────────────────────────────────
let currentNotePath = null;
let selectedTreePath = null;
let inlineRenamePath = null;
let vaultFiles = [];
const expandedFolders = new Set();
let autosaveTimeout = null;
let searchTimeout = null;
let vaultEvents = null;
let vaultRefreshTimeout = null;
let isPanelOpen = false;
let currentViewMode = 'document';
let tagCache = null;
let autocompleteState = null;
const OBSIDIAN_GRAPH_FILTERS_KEY_PREFIX = 'odysseus.obsidian.graphFilters';
const OBSIDIAN_GRAPH_LENS_KEY = 'odysseus.obsidian.graphLensMode';
let graphViewState = createGraphViewState();
let graphReflowFrame = 0;
let memoryTagPendingRemoval = '';
let currentNoteDocumentIntelligence = { state: 'empty' };

function currentNoteLensMeta(path = currentNotePath) {
  const normalized = String(path || '');
  if (!normalized) {
    return {
      kind: 'none',
      label: '',
      description: '',
      readOnly: false,
    };
  }
  if (normalized === 'AI Memory/Published Views.md' || normalized.startsWith('AI Memory/Published Views/')) {
    return {
      kind: 'published',
      label: 'Published view',
      description: 'Generated Lens output. Treat this as a materialized view, not as a primary source note.',
      readOnly: true,
    };
  }
  if (normalized === 'AI Memory/Review Queue.md' || normalized.startsWith('AI Memory/Review Queue/')) {
    return {
      kind: 'queue',
      label: 'Review queue',
      description: 'Staged review artifact. This is not settled canonical memory yet.',
      readOnly: false,
    };
  }
  if (normalized === 'AI Memory/Canonical.md' || normalized.startsWith('AI Memory/Canonical/')) {
    return {
      kind: 'canonical',
      label: 'Canonical memory',
      description: 'Curated memory note inside the vault.',
      readOnly: false,
    };
  }
  return {
    kind: 'source',
    label: 'Source note',
    description: 'User-authored or source-backed note.',
    readOnly: false,
  };
}

function parseFrontmatterBlock(content) {
  const text = String(content || '');
  const match = text.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
  if (!match) return {};
  const frontmatter = {};
  let activeListKey = '';
  match[1].split(/\r?\n/).forEach(line => {
    if (!line.trim()) return;
    const listItem = line.match(/^\s*-\s+(.+?)\s*$/);
    if (listItem && activeListKey) {
      if (!Array.isArray(frontmatter[activeListKey])) frontmatter[activeListKey] = [];
      frontmatter[activeListKey].push(listItem[1].trim());
      return;
    }
    const entry = line.match(/^([A-Za-z0-9_-]+)\s*:\s*(.*)$/);
    if (!entry) {
      activeListKey = '';
      return;
    }
    const key = entry[1].trim().toLowerCase();
    const rawValue = entry[2].trim();
    if (!rawValue) {
      frontmatter[key] = [];
      activeListKey = key;
      return;
    }
    activeListKey = '';
    if (rawValue.startsWith('[') && rawValue.endsWith(']')) {
      frontmatter[key] = rawValue.slice(1, -1).split(',').map(item => item.trim()).filter(Boolean);
      return;
    }
    frontmatter[key] = rawValue.replace(/^["']|["']$/g, '');
  });
  return frontmatter;
}

function frontmatterFirstValue(frontmatter, keys = []) {
  for (const key of keys) {
    const value = frontmatter?.[key];
    if (Array.isArray(value)) {
      const first = value.find(item => String(item || '').trim());
      if (first) return String(first).trim();
      continue;
    }
    if (String(value || '').trim()) return String(value).trim();
  }
  return '';
}

function frontmatterListValue(frontmatter, keys = []) {
  for (const key of keys) {
    const value = frontmatter?.[key];
    if (Array.isArray(value)) {
      return value.map(item => String(item || '').trim()).filter(Boolean);
    }
    if (String(value || '').trim()) {
      return String(value).split(',').map(item => item.trim()).filter(Boolean);
    }
  }
  return [];
}

function normalizeDocumentIntelligenceState(value) {
  const normalized = String(value || '').trim().toLowerCase().replace(/[_\s]+/g, '-');
  if (['loading', 'empty', 'error', 'stale', 'needs-review', 'unknown', 'default'].includes(normalized)) {
    return normalized;
  }
  return 'unknown';
}

function documentIntelligenceMemoryState(frontmatter, noteMeta) {
  const explicit = normalizeDocumentIntelligenceState(frontmatterFirstValue(frontmatter, ['memory_state', 'memory-status', 'memory_status']));
  if (explicit !== 'unknown') return explicit;
  const status = String(frontmatterFirstValue(frontmatter, ['status', 'review_state']) || '').trim().toLowerCase();
  if (['review', 'needs_review', 'needs-review'].includes(status)) return 'needs-review';
  if (noteMeta.kind === 'queue') return 'needs-review';
  return 'unknown';
}

function documentIntelligenceProject(frontmatter) {
  const project = frontmatterFirstValue(frontmatter, ['project', 'project_ref']);
  return project ? { label: project, state: 'default' } : { label: 'Unknown', state: 'unknown' };
}

function documentIntelligenceDate(frontmatter) {
  const value = frontmatterFirstValue(frontmatter, ['date', 'updated', 'updated_at', 'created', 'created_at']);
  return value ? { label: value, state: 'default' } : { label: 'Empty', state: 'empty' };
}

function documentIntelligenceStatus(frontmatter) {
  const value = frontmatterFirstValue(frontmatter, ['status']);
  return value ? { label: readabilityLabel(value), state: 'default' } : { label: 'Unknown', state: 'unknown' };
}

function documentIntelligenceType(frontmatter, noteMeta) {
  const value = frontmatterFirstValue(frontmatter, ['type', 'note_type', 'kind']);
  if (value) return { label: readabilityLabel(value), state: 'default' };
  if (noteMeta.label) return { label: noteMeta.label, state: 'default' };
  return { label: 'Unknown', state: 'unknown' };
}

function documentIntelligenceRelations(frontmatter) {
  const links = frontmatterListValue(frontmatter, ['links', 'related', 'relations']);
  if (links.length) return { label: `${links.length} relations`, state: 'default', links };
  return { label: 'Graph jump', state: 'empty', links: [] };
}

function computeDocumentIntelligence(path, content) {
  const noteMeta = currentNoteLensMeta(path);
  const frontmatter = parseFrontmatterBlock(content);
  const tags = frontmatterListValue(frontmatter, ['tags']);
  const memoryState = documentIntelligenceMemoryState(frontmatter, noteMeta);
  return {
    state: 'default',
    type: documentIntelligenceType(frontmatter, noteMeta),
    project: documentIntelligenceProject(frontmatter),
    status: documentIntelligenceStatus(frontmatter),
    date: documentIntelligenceDate(frontmatter),
    tags,
    relations: documentIntelligenceRelations(frontmatter),
    memoryState,
  };
}

function documentIntelligenceStateLabel(state) {
  switch (normalizeDocumentIntelligenceState(state)) {
    case 'needs-review':
      return 'Needs review';
    case 'stale':
      return 'Stale';
    case 'empty':
      return 'Empty';
    case 'loading':
      return 'Loading';
    case 'error':
      return 'Error';
    case 'default':
      return 'Ready';
    default:
      return 'Unknown';
  }
}

function renderDocumentIntelligenceTagChips(tags = []) {
  if (!tags.length) {
    return '<span class="obsidian-document-intelligence-empty" data-document-intelligence-state="empty">Empty</span>';
  }
  return `
    <div class="obsidian-document-intelligence-tags">
      ${tags.map(tag => `<span class="obsidian-document-tag-chip obsidian-tag-chip">${escapeHtml(tag.startsWith('#') ? tag : `#${tag}`)}</span>`).join('')}
    </div>
  `;
}

function renderDocumentIntelligencePill(label, value, state = 'default') {
  return `
    <span class="obsidian-document-intelligence-pill" data-document-intelligence-state="${escapeHtml(state)}">
      <strong>${escapeHtml(label)}</strong>
      <span>${escapeHtml(value)}</span>
    </span>
  `;
}

function renderDocumentIntelligenceBar() {
  const intelligence = currentNoteDocumentIntelligence || { state: 'empty' };
  if (intelligence.state === 'loading') {
    return '<div class="obsidian-document-intelligence-bar" data-document-intelligence-state="loading"><span class="obsidian-document-intelligence-status">Loading metadata...</span></div>';
  }
  if (intelligence.state === 'error') {
    return '<div class="obsidian-document-intelligence-bar" data-document-intelligence-state="error"><span class="obsidian-document-intelligence-status">Metadata error</span></div>';
  }
  if (intelligence.state === 'empty') {
    return '<div class="obsidian-document-intelligence-bar" data-document-intelligence-state="empty"><span class="obsidian-document-intelligence-status">No document metadata yet.</span></div>';
  }
  return `
    <div class="obsidian-document-intelligence-bar" data-document-intelligence-state="default">
      <div class="obsidian-document-intelligence-meta">
        ${renderDocumentIntelligencePill('Type', intelligence.type.label, intelligence.type.state)}
        ${renderDocumentIntelligencePill('Project', intelligence.project.label, intelligence.project.state)}
        ${renderDocumentIntelligencePill('Status', intelligence.status.label, intelligence.status.state)}
        ${renderDocumentIntelligencePill('Date', intelligence.date.label, intelligence.date.state)}
        ${renderDocumentIntelligencePill('Memory', documentIntelligenceStateLabel(intelligence.memoryState), normalizeDocumentIntelligenceState(intelligence.memoryState))}
      </div>
      <div class="obsidian-document-intelligence-secondary">
        <div class="obsidian-document-intelligence-block" data-document-intelligence-kind="tags">
          <strong>Tags</strong>
          ${renderDocumentIntelligenceTagChips(intelligence.tags)}
        </div>
        <div class="obsidian-document-intelligence-block" data-document-intelligence-kind="relations">
          <strong>Relations</strong>
          <div class="obsidian-document-intelligence-relations">
            ${renderDocumentIntelligencePill('Graph', intelligence.relations.label, intelligence.relations.state)}
            <button type="button" class="btn btn-secondary obsidian-document-graph-jump" id="obsidian-document-graph-jump">Graph jump</button>
          </div>
        </div>
      </div>
    </div>
  `;
}

function createDefaultGraphFilterState() {
  return {
    mode: 'highlight',
    nodes: { markdown: true, folder: true },
    edges: { wiki_link: true, filename_mention: true, shared_tag: true, manual: true, relates_to: true, depends_on: true, blocks: true, supports: true },
    search: '',
    tags: []
  };
}

function normalizeGraphFilterState(input = {}) {
  const defaults = createDefaultGraphFilterState();
  const parsed = input && typeof input === 'object' ? input : {};
  return {
    mode: parsed.mode || defaults.mode,
    nodes: { ...defaults.nodes, ...(parsed.nodes || {}) },
    edges: { ...defaults.edges, ...(parsed.edges || {}) },
    search: parsed.search || '',
    tags: Array.isArray(parsed.tags) ? [...parsed.tags] : [],
  };
}

function createGraphViewState(overrides = {}) {
  return {
    filters: normalizeGraphFilterState(overrides.filters),
    lensMode: overrides.lensMode || 'overview',
    filterPanelOpen: false,
    outsideClickHandler: null,
    cytoscapeInstance: null,
    renderToken: 0,
  };
}

function graphStorageScope({ lensMode = graphViewState.lensMode, focusPath = graphFocusPath() } = {}) {
  const scope = [lensMode || 'overview', focusPath || 'whole-vault']
    .map(part => String(part).trim().replace(/[^A-Za-z0-9_.-]+/g, '_'))
    .filter(Boolean)
    .join(':');
  return scope || 'overview:whole-vault';
}

function graphFilterScopeKey(scope = graphStorageScope()) {
  return `${OBSIDIAN_GRAPH_FILTERS_KEY_PREFIX}:${scope}`;
}

function resetGraphFilterState() {
  graphViewState.filters = createDefaultGraphFilterState();
}

function loadGraphFilterState() {
  try {
    const stored = localStorage.getItem(graphFilterScopeKey());
    if (stored) {
      graphViewState.filters = normalizeGraphFilterState(JSON.parse(stored));
      return;
    }
  } catch (e) {
    console.warn('Failed to load graph filter state:', e);
  }
  resetGraphFilterState();
}

function saveGraphFilterState() {
  try {
    localStorage.setItem(graphFilterScopeKey(), JSON.stringify(graphViewState.filters));
  } catch (e) {
    console.warn('Failed to save graph filter state:', e);
  }
}

function loadGraphLensMode() {
  try {
    const stored = localStorage.getItem(OBSIDIAN_GRAPH_LENS_KEY);
    if (['overview', 'current_source', 'review_queue'].includes(stored)) {
      graphViewState.lensMode = stored;
      return;
    }
  } catch (e) {
    console.warn('Failed to load graph lens mode:', e);
  }
  graphViewState.lensMode = 'overview';
}

function saveGraphLensMode() {
  try {
    localStorage.setItem(OBSIDIAN_GRAPH_LENS_KEY, graphViewState.lensMode);
  } catch (e) {
    console.warn('Failed to save graph lens mode:', e);
  }
}

function isNodeMatchingFilter(node) {
  const graphFilterState = graphViewState.filters;
  if (node.type === 'markdown' && graphFilterState.nodes.markdown === false) return false;
  if (node.type === 'folder' && graphFilterState.nodes.folder === false) return false;

  if (graphFilterState.search) {
    const q = graphFilterState.search.toLowerCase();
    const label = (node.label || node.id || '').toLowerCase();
    const tagsStr = (node.tags || []).join(' ').toLowerCase();
    if (!label.includes(q) && !node.id.toLowerCase().includes(q) && !tagsStr.includes(q)) {
      return false;
    }
  }

  if (graphFilterState.tags && graphFilterState.tags.length > 0) {
    const nodeTags = node.tags || [];
    const hasTagMatch = graphFilterState.tags.some(t => {
      const cleanT = t.startsWith('#') ? t.slice(1).toLowerCase() : t.toLowerCase();
      return nodeTags.some(nt => nt.toLowerCase().includes(cleanT));
    });
    if (!hasTagMatch) return false;
  }

  return true;
}

function isEdgeMatchingFilter(edge) {
  const graphFilterState = graphViewState.filters;
  const type = edge.type || 'link';
  if (graphFilterState.edges[type] === false) return false;
  return true;
}

let graphCytoscapeLoadPromise = null;
let projectPlanPreview = null;
let projectPlanSessions = [];
let activeProjectPlanSessionId = null;
let activeProjectPlanSession = null;
let memoryReviewPreview = null;
let memoryReviewDestination = { type: '', path: '' };
let memoryReviewTags = [];
let memoryTagPickerState = { index: 0, items: [] };
let memoryDestinationPickerTab = 'folders';
let sparkPlan = null;
let sparkHealth = null;
let sparkSelectedActions = new Set();
let sparkActiveTab = 'health';
let memoryWorkspaceTab = 'read';
let sparkQueryStatus = null;
let sparkQueryResult = null;
let sparkQueryText = '';
let sparkQueryLoading = false;
let sparkQueryError = '';
let memoryTreeReport = null;
let knowledgeAuditReport = null;
let quarantineReport = null;
let raptorReport = null;
let memoryStatusReport = null;
let memoryTreeActiveTab = 'tree';
let memoryTreeLoading = false;
let memoryTreeError = '';
let activityLoading = false;
let activityError = '';
let projectTemplateOptions = null;
let gameDevConceptDraft = null;
let projectPlanPreviewStreaming = false;
let minimizedSurfaceMode = null;
const OBSIDIAN_GRAPH_RENDERER_KEY = 'odysseus.obsidian.graphRenderer';
const OBSIDIAN_GRAPH_RENDERER_CYTOSCAPE = 'cytoscape';
const OBSIDIAN_GRAPH_RENDERER_SVG = 'svg';
const OBSIDIAN_CYTOSCAPE_ASSET = '/api/plugins/obsidian/web/cytoscape.min.js';
const OBSIDIAN_GRAPH_WHEEL_SENSITIVITY = 0.55;
const VAULT_ROOT_TREE_PATH = '__vault_root__';
const OBSIDIAN_SURFACE_MODE_KEY = 'odysseus.obsidian.surfaceMode';
const OBSIDIAN_SURFACE_DEFAULT = 'sidebar';
const OBSIDIAN_SURFACE_MODES = ['sidebar', 'overlay', 'fullscreen'];
const OBSIDIAN_MODAL_ID = 'obsidian-modal';
const NEW_PROJECT_FOLDER_SENTINEL = '__new_project_folder__';
const OBSIDIAN_PANEL_WIDTH_KEY = 'odysseus.obsidian.panelWidth';
const OBSIDIAN_SIDEBAR_WIDTH_KEY = 'odysseus.obsidian.sidebarWidth';
const DEFAULT_PANEL_WIDTH = 0;
const DEFAULT_SIDEBAR_WIDTH = 220;
const MIN_PANEL_WIDTH = 540;
const MAX_PANEL_WIDTH = 1200;
const MIN_SIDEBAR_WIDTH = 160;
const MAX_SIDEBAR_WIDTH = 420;

// ─── Helpers ─────────────────────────────────────────────────────────────────
function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function renderLensStateCard(area, state, message, actionLabel, actionId, options = {}) {
  const actionClass = options.actionClass || 'btn btn-primary';
  const disabled = options.disabled ? 'disabled' : '';
  const detail = options.detail ? `<small>${escapeHtml(options.detail)}</small>` : '';
  return `
    <div class="obsidian-lens-state-card" data-lens-area="${escapeHtml(area)}" data-lens-state="${escapeHtml(state)}">
      <strong>${escapeHtml(message)}</strong>
      ${detail}
      <button type="button" class="${escapeHtml(actionClass)}" id="${escapeHtml(actionId)}" ${disabled}>${escapeHtml(actionLabel)}</button>
    </div>
  `;
}

function preprocessWikiLinks(text) {
  if (!text) return '';
  return text.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (match, notePath, label) => {
    const cleanPath = notePath.trim();
    const displayLabel = (label || notePath).trim();
    const encodedPath = encodeURIComponent(cleanPath);
    return `[${displayLabel}](#obsidian-link-${encodedPath})`;
  });
}

function normalizeMarkdownTags(content) {
  let inFence = false;
  return String(content || '').split('\n').map(line => {
    const stripped = line.trimStart();
    if (stripped.startsWith('```') || stripped.startsWith('~~~')) {
      inFence = !inFence;
      return line;
    }
    if (inFence || /^#{1,6}\s/.test(stripped)) return line;
    return line.split(/(`[^`\n]*`)/g).map((segment, index) => {
      if (index % 2 === 1) return segment;
      return segment.replace(/(^|[\s(])#\s+([A-Za-z0-9][A-Za-z0-9_/-]*)/g, '$1#$2');
    }).join('');
  }).join('\n');
}

function displayTagLabel(tag) {
  const clean = String(tag || '').replace(/^#+/, '');
  return clean ? `# ${clean}` : '#';
}

function tagMetaPath(tag) {
  const clean = String(tag || '').replace(/^#+/, '').replace(/^\/+|\/+$/g, '');
  return clean ? `Tags/${clean}.md` : 'Tags/untitled.md';
}

function shouldSkipTagEnhance(node) {
  const parent = node.parentElement;
  return !parent || Boolean(parent.closest('a, code, pre, script, style, textarea, .obsidian-tag-badge'));
}

function enhancePreviewTags(container) {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const targets = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!node.nodeValue || shouldSkipTagEnhance(node)) continue;
    if (/(^|[^\w/#])#[A-Za-z0-9][A-Za-z0-9_/-]*/.test(node.nodeValue)) targets.push(node);
  }
  targets.forEach(node => {
    const fragment = document.createDocumentFragment();
    const text = node.nodeValue;
    const re = /(^|[^\w/#])#([A-Za-z0-9][A-Za-z0-9_/-]*)/g;
    let last = 0;
    let match;
    while ((match = re.exec(text))) {
      const prefix = match[1] || '';
      const start = match.index + prefix.length;
      if (start > last) fragment.appendChild(document.createTextNode(text.slice(last, start)));
      const tag = match[2];
      const badge = document.createElement('button');
      badge.type = 'button';
      badge.className = 'obsidian-tag-badge';
      badge.dataset.obsidianTag = tag;
      badge.textContent = displayTagLabel(tag);
      fragment.appendChild(badge);
      last = start + tag.length + 1;
    }
    if (last < text.length) fragment.appendChild(document.createTextNode(text.slice(last)));
    node.parentNode.replaceChild(fragment, node);
  });
}

function resolveMarkdownFileLink(rawHref) {
  const href = decodeURIComponent(String(rawHref || '').split('#')[0]).replace(/^\.\/+/, '').replace(/\\/g, '/');
  if (!href || /^https?:\/\//i.test(href) || !/\.md$|\.markdown$/i.test(href)) return '';
  const normalized = href.replace(/\.markdown$/i, '.md');
  const localPath = currentNotePath && !normalized.includes('/')
    ? joinPath(getParentDir(currentNotePath), normalized)
    : normalized;
  const localFound = findFileInTree(vaultFiles, localPath);
  if (localFound) return localFound;
  return findFileInTree(vaultFiles, normalized) || findFileInTree(vaultFiles, normalizeNotePath(normalized)) || normalized;
}

function renderEditorPreview(content) {
  const preview = document.getElementById('obsidian-rendered-preview');
  if (!preview) return;
  const prepared = preprocessWikiLinks(content || '');
  preview.innerHTML = mdToHtml(prepared || '', { allowHtml: false });
  enhancePreviewTags(preview);
  renderMermaid(preview);
}

function closeTagDetails() {
  document.querySelector('.obsidian-tag-detail-popover')?.remove();
}

async function openTagDetails(tag, anchor) {
  closeTagDetails();
  const clean = String(tag || '').replace(/^#+/, '');
  const metaPath = tagMetaPath(clean);
  const tags = await getVaultTags();
  const info = tags.find(item => item.name === clean) || { name: clean, files: [] };
  const metaExists = Boolean(findFileInTree(vaultFiles, metaPath));
  const popover = document.createElement('div');
  popover.className = 'obsidian-tag-detail-popover';
  popover.innerHTML = `
    <div class="obsidian-tag-detail-head">
      <strong>${escapeHtml(displayTagLabel(clean))}</strong>
      <button type="button" data-tag-detail-close aria-label="Close">x</button>
    </div>
    <div class="obsidian-tag-detail-meta">${escapeHtml((info.files || []).length)} linked notes</div>
    <div class="obsidian-tag-detail-files">
      ${(info.files || []).slice(0, 8).map(path => `<button type="button" data-tag-note="${escapeHtml(path)}">${escapeHtml(path)}</button>`).join('') || '<span>No notes yet</span>'}
    </div>
    <button type="button" class="obsidian-tag-meta-action" data-tag-meta-path="${escapeHtml(metaPath)}">${metaExists ? 'Open meta note' : 'Create meta note'}</button>
  `;
  document.body.appendChild(popover);
  const rect = anchor?.getBoundingClientRect?.() || { left: 20, bottom: 20 };
  popover.style.left = `${Math.min(rect.left, window.innerWidth - 280)}px`;
  popover.style.top = `${Math.min(rect.bottom + 6, window.innerHeight - 220)}px`;
  popover.querySelector('[data-tag-detail-close]')?.addEventListener('click', closeTagDetails);
  popover.querySelectorAll('[data-tag-note]').forEach(btn => {
    btn.addEventListener('click', async () => {
      closeTagDetails();
      await openNote(btn.dataset.tagNote);
    });
  });
  popover.querySelector('[data-tag-meta-path]')?.addEventListener('click', async (e) => {
    const path = e.currentTarget.dataset.tagMetaPath;
    if (!findFileInTree(vaultFiles, path)) {
      const res = await fetch('/api/plugins/obsidian/file', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path,
          content: `# ${displayTagLabel(clean)}\n\nTag: #${clean}\n\n## Linked notes\n${(info.files || []).map(file => `- [[${file.replace(/\.md$/i, '')}]]`).join('\n')}\n`,
        }),
      });
      if (!res.ok) {
        showToast('Failed to create tag meta note');
        return;
      }
      await loadVaultFiles();
    }
    closeTagDetails();
    await openNote(path);
  });
}

function flattenNotes(nodes, out = []) {
  nodes.forEach(node => {
    if (node.is_dir && node.children) {
      flattenNotes(node.children, out);
    } else if (!node.is_dir && node.path.toLowerCase().endsWith('.md')) {
      out.push(node.path);
    }
  });
  return out;
}

function normalizeNotePath(path) {
  const clean = (path || '').trim().replace(/\\/g, '/').replace(/^\/+/, '');
  return clean.toLowerCase().endsWith('.md') ? clean : `${clean}.md`;
}

function getParentDir(path) {
  if (!path || !path.includes('/')) return '';
  return path.substring(0, path.lastIndexOf('/'));
}

function getBaseName(path) {
  return (path || '').split('/').pop() || '';
}

function joinPath(dir, name) {
  return [dir, name].filter(Boolean).join('/').replace(/\/+/g, '/');
}

function flattenTree(nodes, out = []) {
  nodes.forEach(node => {
    out.push(node);
    if (node.is_dir && node.children) {
      flattenTree(node.children, out);
    }
  });
  return out;
}

function cloneTreeNodes(nodes) {
  return (nodes || []).map(node => ({
    ...node,
    children: node.children ? cloneTreeNodes(node.children) : node.children,
  }));
}

function sessionTreePath(session) {
  return `__project_session__/${session.id}`;
}

function sessionDisplayName(session) {
  const folderName = getBaseName(session.target_preview_path || session.target_folder || '');
  return folderName || session.title || 'Project planning';
}

function sessionParentPath(session) {
  return getParentDir(session.target_preview_path || session.target_folder || '');
}

function sessionStatusLabel(status) {
  switch (String(status || '').toLowerCase()) {
    case 'draft':
      return 'Draft';
    case 'generating':
      return 'Generating';
    case 'ready':
      return 'Ready';
    case 'applying':
      return 'Applying';
    case 'error':
      return 'Error';
    default:
      return 'Planning';
  }
}

function projectPlanSessionNode(session) {
  const planFiles = session.plan?.files || [];
  return {
    name: sessionDisplayName(session),
    path: sessionTreePath(session),
    is_dir: true,
    is_virtual_project_session: true,
    session_id: session.id,
    wip_status: session.status || 'draft',
    children: planFiles.map(file => ({
      name: getBaseName(file.path),
      path: `${sessionTreePath(session)}/${file.path}`,
      is_dir: false,
      is_virtual_project_session_file: true,
      session_id: session.id,
      source_path: file.path,
    })),
  };
}

function insertSessionNode(nodes, sessionNode, parentPath) {
  if (!parentPath) {
    nodes.push(sessionNode);
    return true;
  }
  for (const node of nodes) {
    if (node.is_dir && node.path === parentPath) {
      node.children = node.children || [];
      node.children.push(sessionNode);
      expandedFolders.add(node.path);
      return true;
    }
    if (node.is_dir && node.children && insertSessionNode(node.children, sessionNode, parentPath)) {
      return true;
    }
  }
  nodes.push(sessionNode);
  return false;
}

function treeWithProjectPlanSessions(nodes) {
  const merged = cloneTreeNodes(nodes);
  projectPlanSessions.forEach(session => {
    const status = String(session.status || '').toLowerCase();
    if (['created', 'cancelled'].includes(status)) return;
    const node = projectPlanSessionNode(session);
    insertSessionNode(merged, node, sessionParentPath(session));
  });
  return merged;
}

function visibleTreeNodes(nodes) {
  return [
    {
      name: 'Vault root',
      path: VAULT_ROOT_TREE_PATH,
      is_dir: true,
      is_virtual_root: true,
      children: [],
    },
    ...treeWithProjectPlanSessions(nodes),
  ];
}

function findTreeNode(path) {
  if (path === VAULT_ROOT_TREE_PATH) {
    return { name: 'Vault root', path: VAULT_ROOT_TREE_PATH, is_dir: true, is_virtual_root: true };
  }
  return flattenTree(visibleTreeNodes(vaultFiles)).find(node => node.path === path) || null;
}

function selectedFolderPath() {
  const selected = selectedTreePath ? findTreeNode(selectedTreePath) : null;
  if (selected?.is_virtual_root) return '';
  if (selected?.is_virtual_project_session || selected?.is_virtual_project_session_file) return '';
  if (selected?.is_dir) return selected.path;
  return '';
}

function isVaultRootSelected() {
  return selectedTreePath === VAULT_ROOT_TREE_PATH;
}

function graphFocusPath() {
  if (isVaultRootSelected()) return '';
  const selected = selectedTreePath ? findTreeNode(selectedTreePath) : null;
  if (selected?.is_dir) return '';
  return currentNotePath || '';
}

function focusedGraphFolderPath() {
  if (isVaultRootSelected()) return null;
  const selected = selectedTreePath ? findTreeNode(selectedTreePath) : null;
  if (selected?.is_dir) return selected.path;
  return currentNotePath ? directFolderForPath(currentNotePath) : null;
}

function selectTreeItem(path) {
  selectedTreePath = path;
  document.querySelectorAll('.tree-item').forEach(el => el.classList.remove('active'));
  const activeEl = document.querySelector(`.tree-item[data-path="${CSS.escape(path)}"]`);
  if (activeEl) activeEl.classList.add('active');
}

function triggerEditorInput() {
  const textarea = document.getElementById('obsidian-textarea');
  textarea?.dispatchEvent(new Event('input', { bubbles: true }));
}

function replaceSelection(before, after = '', placeholder = '') {
  const textarea = document.getElementById('obsidian-textarea');
  if (!textarea || currentViewMode === 'graph') return;
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const selected = textarea.value.slice(start, end) || placeholder;
  const next = `${before}${selected}${after}`;
  textarea.setRangeText(next, start, end, 'select');
  textarea.selectionStart = start + before.length;
  textarea.selectionEnd = start + before.length + selected.length;
  textarea.focus();
  triggerEditorInput();
  updateAutocomplete();
}

function prefixSelectedLines(prefix) {
  const textarea = document.getElementById('obsidian-textarea');
  if (!textarea || currentViewMode === 'graph') return;
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const lineStart = textarea.value.lastIndexOf('\n', start - 1) + 1;
  const selected = textarea.value.slice(lineStart, end);
  const replaced = selected.split('\n').map(line => line ? `${prefix}${line}` : prefix.trimEnd()).join('\n');
  textarea.setRangeText(replaced, lineStart, end, 'end');
  textarea.focus();
  triggerEditorInput();
}

function applyMarkdownAction(action) {
  const textarea = document.getElementById('obsidian-textarea');
  if (!textarea) return;
  const selected = textarea.value.slice(textarea.selectionStart, textarea.selectionEnd);
  switch (action) {
    case 'bold':
      replaceSelection('**', '**', 'bold text');
      break;
    case 'italic':
      replaceSelection('*', '*', 'italic text');
      break;
    case 'inline-code':
      replaceSelection('`', '`', 'code');
      break;
    case 'codeblock':
      replaceSelection('```\n', '\n```', selected || 'code');
      break;
    case 'heading':
      prefixSelectedLines('# ');
      break;
    case 'list':
      prefixSelectedLines('- ');
      break;
    case 'checkbox':
      prefixSelectedLines('- [ ] ');
      break;
    case 'quote':
      prefixSelectedLines('> ');
      break;
    case 'link':
      replaceSelection('[', '](https://)', selected || 'link text');
      break;
    case 'wikilink':
      replaceSelection('[[', ']]', selected || 'Note');
      break;
    case 'tag':
      replaceSelection('#', '', selected || 'tag');
      break;
    case 'table':
      textarea.setRangeText('| Column | Value |\n| --- | --- |\n|  |  |', textarea.selectionStart, textarea.selectionEnd, 'end');
      textarea.focus();
      triggerEditorInput();
      break;
    default:
      break;
  }
}

async function getVaultTags() {
  if (tagCache) return tagCache;
  try {
    const res = await fetch('/api/plugins/obsidian/tags');
    if (!res.ok) return [];
    tagCache = await res.json();
    return tagCache;
  } catch (e) {
    console.error('Failed to load tags:', e);
    return [];
  }
}

function isInSuppressedAutocompleteContext(text, caret) {
  const before = text.slice(0, caret);
  const fenceCount = (before.match(/(^|\n)(```|~~~)/g) || []).length;
  if (fenceCount % 2 === 1) return true;

  const lineStart = before.lastIndexOf('\n') + 1;
  const lineBeforeCaret = before.slice(lineStart);
  const inlineTicks = (lineBeforeCaret.match(/`/g) || []).length;
  if (inlineTicks % 2 === 1) return true;

  const lastToken = lineBeforeCaret.split(/\s/).pop() || '';
  return /^https?:\/\//i.test(lastToken);
}

function positionAutocompleteMenu(textarea, menu) {
  const pane = textarea.closest('.obsidian-editor-pane');
  if (!pane) return;

  const style = window.getComputedStyle(textarea);
  const mirror = document.createElement('div');
  const marker = document.createElement('span');
  const mirrorStyle = mirror.style;
  mirrorStyle.position = 'absolute';
  mirrorStyle.visibility = 'hidden';
  mirrorStyle.whiteSpace = 'pre-wrap';
  mirrorStyle.wordWrap = 'break-word';
  mirrorStyle.overflow = 'hidden';
  mirrorStyle.boxSizing = style.boxSizing;
  mirrorStyle.width = `${textarea.clientWidth}px`;
  mirrorStyle.font = style.font;
  mirrorStyle.lineHeight = style.lineHeight;
  mirrorStyle.padding = style.padding;
  mirrorStyle.border = style.border;
  mirror.textContent = textarea.value.slice(0, textarea.selectionStart);
  marker.textContent = '\u200b';
  mirror.appendChild(marker);
  pane.appendChild(mirror);

  const paneRect = pane.getBoundingClientRect();
  const markerRect = marker.getBoundingClientRect();
  const top = markerRect.top - paneRect.top - textarea.scrollTop + parseFloat(style.lineHeight || '20') + 4;
  const left = markerRect.left - paneRect.left - textarea.scrollLeft;
  pane.removeChild(mirror);

  menu.style.top = `${Math.max(8, top)}px`;
  menu.style.left = `${Math.max(8, left)}px`;
  menu.style.right = 'auto';
}

function hideAutocomplete() {
  autocompleteState = null;
  const menu = document.getElementById('obsidian-autocomplete');
  if (menu) {
    menu.classList.add('hidden');
    menu.innerHTML = '';
  }
}

function renderAutocomplete() {
  const menu = document.getElementById('obsidian-autocomplete');
  const textarea = document.getElementById('obsidian-textarea');
  if (!menu || !autocompleteState || !autocompleteState.items.length) {
    hideAutocomplete();
    return;
  }
  menu.innerHTML = '';
  autocompleteState.items.slice(0, 8).forEach((item, index) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `obsidian-autocomplete-item ${index === autocompleteState.index ? 'active' : ''}`;
    btn.setAttribute('role', 'option');
    btn.innerHTML = `
      <span class="obsidian-autocomplete-label">${escapeHtml(item.label)}</span>
      <span class="obsidian-autocomplete-meta">${escapeHtml(item.meta || '')}</span>
    `;
    btn.addEventListener('mousedown', (e) => {
      e.preventDefault();
      applyAutocompleteItem(index);
    });
    menu.appendChild(btn);
  });
  if (textarea) {
    positionAutocompleteMenu(textarea, menu);
  }
  menu.classList.remove('hidden');
}

async function updateAutocomplete() {
  const textarea = document.getElementById('obsidian-textarea');
  if (!textarea || document.activeElement !== textarea) {
    hideAutocomplete();
    return;
  }
  const caret = textarea.selectionStart;
  if (isInSuppressedAutocompleteContext(textarea.value, caret)) {
    hideAutocomplete();
    return;
  }
  const before = textarea.value.slice(0, caret);
  const wikiMatch = before.match(/\[\[([^\]\n]*)$/);
  if (wikiMatch) {
    const query = wikiMatch[1].toLowerCase();
    const notes = flattenNotes(vaultFiles)
      .filter(path => path.toLowerCase().includes(query))
      .slice(0, 8)
      .map(path => ({ value: path.replace(/\.md$/i, ''), label: path.replace(/\.md$/i, ''), meta: getParentDir(path) }));
    autocompleteState = {
      mode: 'wikilink',
      start: caret - wikiMatch[1].length,
      end: caret,
      index: 0,
      items: notes,
    };
    renderAutocomplete();
    return;
  }

  const tagMatch = before.match(/(^|[\s(])#([A-Za-z0-9_/-]*)$/);
  if (tagMatch) {
    const query = tagMatch[2].toLowerCase();
    const tags = (await getVaultTags())
      .filter(tag => tag.name.toLowerCase().includes(query))
      .slice(0, 8)
      .map(tag => ({ value: tag.name, label: `#${tag.name}`, meta: `${tag.files.length} notes` }));
    autocompleteState = {
      mode: 'tag',
      start: caret - tagMatch[2].length,
      end: caret,
      index: 0,
      items: tags,
    };
    renderAutocomplete();
    return;
  }
  hideAutocomplete();
}

function applyAutocompleteItem(index = autocompleteState?.index || 0) {
  const textarea = document.getElementById('obsidian-textarea');
  if (!textarea || !autocompleteState) return;
  const item = autocompleteState.items[index];
  if (!item) return;
  const inserted = autocompleteState.mode === 'wikilink' ? `${item.value}]]` : item.value;
  textarea.setSelectionRange(autocompleteState.start, autocompleteState.end);
  textarea.setRangeText(inserted, autocompleteState.start, autocompleteState.end, 'end');
  textarea.focus();
  triggerEditorInput();
  hideAutocomplete();
}

function handleAutocompleteKey(e) {
  if (!autocompleteState || !autocompleteState.items.length) return false;
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    autocompleteState.index = (autocompleteState.index + 1) % autocompleteState.items.length;
    renderAutocomplete();
    return true;
  }
  if (e.key === 'ArrowUp') {
    e.preventDefault();
    autocompleteState.index = (autocompleteState.index - 1 + autocompleteState.items.length) % autocompleteState.items.length;
    renderAutocomplete();
    return true;
  }
  if (e.key === 'Enter' || e.key === 'Tab') {
    e.preventDefault();
    applyAutocompleteItem();
    return true;
  }
  if (e.key === 'Escape') {
    e.preventDefault();
    hideAutocomplete();
    return true;
  }
  return false;
}

async function moveVaultItem(oldPath, targetFolder) {
  if (!oldPath && oldPath !== '') return;
  const baseName = getBaseName(oldPath);
  const newPath = joinPath(targetFolder, baseName);
  if (!newPath || newPath === oldPath) return;
  if (targetFolder && (targetFolder === oldPath || targetFolder.startsWith(`${oldPath}/`))) {
    showToast('Cannot move a folder into itself');
    return;
  }
  try {
    const res = await fetch('/api/plugins/obsidian/rename', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_path: oldPath, new_path: newPath }),
    });
    if (res.ok) {
      showToast('Moved item');
      if (selectedTreePath === oldPath || selectedTreePath?.startsWith(`${oldPath}/`)) {
        selectedTreePath = selectedTreePath.replace(oldPath, newPath);
      }
      if (currentNotePath === oldPath || currentNotePath?.startsWith(`${oldPath}/`)) {
        currentNotePath = currentNotePath.replace(oldPath, newPath);
      }
      await loadVaultFiles();
      if (currentNotePath && !currentNotePath.endsWith('/')) {
        await openNote(currentNotePath);
      }
    } else {
      const err = await res.json();
      showToast(err.detail || 'Failed to move item');
    }
  } catch (e) {
    console.error('Move failed:', e);
    showToast('Error moving item');
  }
}

async function importDroppedMarkdownFiles(files, targetFolder) {
  const markdownFiles = [...files].filter(file => file.name.toLowerCase().endsWith('.md'));
  if (!markdownFiles.length) return;
  for (const file of markdownFiles) {
    const content = await file.text();
    const path = joinPath(targetFolder, file.name);
    const res = await fetch('/api/plugins/obsidian/file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, content }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      showToast(err.detail || `Failed to import ${file.name}`);
      continue;
    }
  }
  tagCache = null;
  await loadVaultFiles();
  showToast('Markdown file imported');
}

function closeSettingsMenu() {
  document.getElementById('obsidian-settings-menu')?.classList.add('hidden');
}

function closeGraphFilterPanel() {
  graphViewState.filterPanelOpen = false;
  document.getElementById('obsidian-graph-filter-panel')?.classList.add('hidden');
  if (graphViewState.outsideClickHandler) {
    document.removeEventListener('click', graphViewState.outsideClickHandler);
    graphViewState.outsideClickHandler = null;
  }
}

function clearObsidianTransientShellState() {
  document.body.classList.remove('obsidian-resizing');
  document.getElementById('obsidian-panel-resize-handle')?.classList.remove('resizing');
  document.getElementById('obsidian-split-resize-handle')?.classList.remove('resizing');
  closeSettingsMenu();
  closeGraphFilterPanel();
  closeTagDetails();
}

function requestGraphReflow(reason = 'shell') {
  if (graphReflowFrame) {
    cancelAnimationFrame(graphReflowFrame);
    graphReflowFrame = 0;
  }
  graphReflowFrame = requestAnimationFrame(() => {
    graphReflowFrame = 0;
    if (!isPanelOpen || currentViewMode !== 'graph') return;
    const graph = document.getElementById('obsidian-graph-view');
    if (!graph || graph.classList.contains('hidden')) return;
    renderGraphView();
  });
}

function shortAiModelName(model) {
  const text = String(model || '').trim();
  if (!text) return '';
  return text.split('/').pop();
}

function memoryWorkspaceTitle() {
  return memoryWorkspaceTab === 'maintain' ? 'Gedaechtnis Pflegen' : 'Gedaechtnis Lesen';
}

function memoryWorkspaceSubtitle() {
  if (memoryWorkspaceTab === 'maintain') {
    return 'Review, Kuration, Uebernahme und Review Queue bleiben im Pflegekontext gebuendelt.';
  }
  return 'Fragen, Quellen, Confidence und Graph-Jumps bleiben im Lesekontext gebuendelt.';
}

async function refreshObsidianAiStatus() {
  const valueEl = document.getElementById('obsidian-ai-model-value');
  const hintEl = document.getElementById('obsidian-ai-model-hint');
  if (!valueEl || !hintEl) return;
  valueEl.textContent = 'Loading...';
  valueEl.removeAttribute('title');
  hintEl.textContent = '';
  try {
    const res = await fetch('/api/plugins/obsidian/ai-status', { credentials: 'same-origin' });
    if (!res.ok) throw new Error('AI status unavailable');
    const status = await res.json();
    if (!status.available || !status.model) {
      valueEl.textContent = 'No model configured';
      hintEl.textContent = '';
      return;
    }
    valueEl.textContent = shortAiModelName(status.model);
    valueEl.title = status.model;
    hintEl.textContent = status.role === 'utility' ? 'Utility' : 'Default fallback';
  } catch (e) {
    valueEl.textContent = 'Unable to load model';
    hintEl.textContent = '';
  }
}

function toggleSettingsMenu() {
  const menu = document.getElementById('obsidian-settings-menu');
  if (!menu) return;
  menu.classList.toggle('hidden');
  if (!menu.classList.contains('hidden')) {
    refreshObsidianAiStatus();
  }
}

function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result || '');
      resolve(result.includes(',') ? result.split(',').pop() : result);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function handleVaultSettingsAction(action) {
  closeSettingsMenu();
  try {
    if (action === 'export') {
      const usePassword = await styledConfirm('Encrypt exported vault archive with a password?', { confirmText: 'Encrypt' });
      let password = null;
      if (usePassword) {
        password = await styledPrompt('Export password:', { confirmText: 'Export' });
        if (!password) return;
      }
      const res = await fetch('/api/plugins/obsidian/vault/export', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || 'Export failed');
      const data = await res.json();
      const link = document.createElement('a');
      link.href = `data:application/zip;base64,${data.archive_base64}`;
      link.download = data.filename || 'obsidian-vault.zip';
      link.click();
      showToast('Vault exported');
      return;
    }

    if (action === 'import') {
      document.getElementById('obsidian-import-input')?.click();
      return;
    }

    if (action === 'set-password') {
      const confirmed = await styledConfirm(
        'Set or replace plugin password protection for this vault? This limits access through the plugin and tools, but it is not full disk encryption and does not automatically encrypt existing vault files at rest on disk.',
        { confirmText: 'Continue' }
      );
      if (!confirmed) return;
      const password = await styledPrompt(
        'Vault password for plugin access only. Existing vault files already on disk are not automatically encrypted at rest by this step:',
        { confirmText: 'Set password' }
      );
      if (!password) return;
      const res = await fetch('/api/plugins/obsidian/vault/password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || 'Password update failed');
      showToast('Vault password updated');
      return;
    }

    if (action === 'remove-password') {
      const confirmed = await styledConfirm('Remove password protection from this vault?', { confirmText: 'Remove', danger: true });
      if (!confirmed) return;
      const password = await styledPrompt('Current vault password:', { confirmText: 'Remove' });
      if (!password) return;
      const res = await fetch('/api/plugins/obsidian/vault/password', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || 'Password removal failed');
      showToast('Vault password removed');
      return;
    }

    if (action === 'reset-graph') {
      resetGraphFilterState();
      saveGraphFilterState();
      setViewMode('graph');
      renderGraphView();
      showToast('Graph view reset');
    }
  } catch (e) {
    console.error('Vault settings action failed:', e);
    showToast(e.message || 'Vault settings action failed');
  }
}

// ─── Panel UI Injection ──────────────────────────────────────────────────────

function isStandaloneMode() {
  return window.ODYSSEUS_OBSIDIAN_STANDALONE === true
    || document.body?.dataset.obsidianStandalone === 'true'
    || window.location.pathname === '/api/plugins/obsidian/app';
}

function normalizeSurfaceMode(mode) {
  return OBSIDIAN_SURFACE_MODES.includes(mode) ? mode : OBSIDIAN_SURFACE_DEFAULT;
}

function getStoredSurfaceMode() {
  if (isStandaloneMode()) return 'fullscreen';
  try {
    return normalizeSurfaceMode(localStorage.getItem(OBSIDIAN_SURFACE_MODE_KEY));
  } catch (_) {
    return OBSIDIAN_SURFACE_DEFAULT;
  }
}

function saveSurfaceMode(mode) {
  if (isStandaloneMode()) return;
  try {
    localStorage.setItem(OBSIDIAN_SURFACE_MODE_KEY, normalizeSurfaceMode(mode));
  } catch (_) {}
}

function syncSurfaceModeControls(mode = getStoredSurfaceMode()) {
  const normalized = normalizeSurfaceMode(mode);
  document.querySelectorAll('[data-obsidian-surface-mode]').forEach(btn => {
    const active = btn.dataset.obsidianSurfaceMode === normalized;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-checked', active ? 'true' : 'false');
  });
}

function setLauncherActive(on) {
  document.getElementById('tool-obsidian-btn')?.classList.toggle('active', on);
  document.getElementById('rail-obsidian')?.classList.toggle('active-section', on);
}

function getObsidianModal() {
  return document.getElementById(OBSIDIAN_MODAL_ID);
}

function getObsidianPanelContent() {
  return document.querySelector('#obsidian-panel .obsidian-panel-content');
}

function clearObsidianSurfaceClasses() {
  document.body.classList.remove(
    'obsidian-surface-sidebar',
    'obsidian-surface-overlay',
    'obsidian-surface-fullscreen',
    'obsidian-fullscreen'
  );
}

function applyObsidianSurfaceMode(mode) {
  const normalized = normalizeSurfaceMode(mode);
  clearObsidianSurfaceClasses();
  document.body.classList.add(`obsidian-surface-${normalized}`);
  document.body.classList.toggle('obsidian-fullscreen', normalized === 'fullscreen');
  const panel = document.getElementById('obsidian-panel');
  if (panel) panel.dataset.surfaceMode = normalized;
  syncSurfaceModeControls(normalized);
  return normalized;
}

function initializeClosedObsidianSurface(mode = getStoredSurfaceMode()) {
  const normalized = normalizeSurfaceMode(mode);
  isPanelOpen = false;
  minimizedSurfaceMode = null;
  clearObsidianTransientShellState();
  clearObsidianSurfaceClasses();
  document.body.classList.remove('obsidian-open');
  const panel = document.getElementById('obsidian-panel');
  if (panel) panel.dataset.surfaceMode = normalized;
  getObsidianModal()?.classList.add('hidden');
  resetObsidianWindowStyles();
  syncSurfaceModeControls(normalized);
  setLauncherActive(false);
}

function resetObsidianWindowStyles() {
  const modal = getObsidianModal();
  const content = getObsidianPanelContent();
  if (modal) {
    try { clearDockSide('left', modal); } catch (_) {}
    try { clearDockSide('right', modal); } catch (_) {}
  }
  modal?.classList.remove('obsidian-overlay-fullscreen', 'modal-left-docked', 'modal-right-docked');
  if (modal) {
    modal.style.display = '';
    modal.style.zIndex = '';
  }
  if (content) {
    [
      'position', 'left', 'top', 'right', 'bottom', 'width', 'height',
      'max-width', 'max-height', 'min-height', 'margin', 'transform',
      'animation', 'transition', 'opacity'
    ].forEach(prop => content.style.removeProperty(prop));
  }
}

function ensureObsidianModalRegistered() {
  if (Modals.isRegistered(OBSIDIAN_MODAL_ID)) return;
  Modals.register(OBSIDIAN_MODAL_ID, {
    railBtnId: 'rail-obsidian',
    sidebarBtnId: 'tool-obsidian-btn',
    label: 'Obsidian',
    icon: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="6 2 18 2 18 6 6 6"/><rect x="3" y="6" width="18" height="16" rx="2"/><path d="M8 11h8M8 15h5"/></svg>',
    restoreFn: () => {
      const mode = normalizeSurfaceMode(minimizedSurfaceMode || getStoredSurfaceMode());
      minimizedSurfaceMode = null;
      showObsidianSurface(mode);
    },
    closeFn: () => {
      hideObsidianSurface({ unregisterOverlay: false, resetWindow: true });
    },
  });
}

function unregisterObsidianModal() {
  if (Modals.isRegistered(OBSIDIAN_MODAL_ID)) {
    Modals.unregister(OBSIDIAN_MODAL_ID);
  }
}

function wireObsidianOverlayWindow() {
  const modal = getObsidianModal();
  const content = getObsidianPanelContent();
  const header = content?.querySelector('.obsidian-panel-header');
  if (!modal || !content || !header || modal.dataset.overlayWindowWired === '1') return;
  modal.dataset.overlayWindowWired = '1';
  makeWindowDraggable(modal, {
    content,
    header,
    fsClass: 'obsidian-overlay-fullscreen',
    skipSelector: 'button, input, select, textarea, label, [role="menu"], .obsidian-panel-resize-handle, .obsidian-split-resize-handle',
    minWidth: 540,
    minHeight: 420,
    resizeStorageKey: 'winsize-obsidian-modal',
    onEnterFullscreen: () => {
      modal.classList.add('obsidian-overlay-fullscreen');
      content.style.position = 'fixed';
      content.style.left = '0';
      content.style.top = '0';
      content.style.width = '100vw';
      content.style.height = '100vh';
      content.style.maxWidth = 'none';
      content.style.maxHeight = 'none';
      content.style.margin = '0';
      content.style.transform = 'none';
    },
    onExitFullscreen: () => {
      modal.classList.remove('obsidian-overlay-fullscreen');
    },
  });
}

function hideObsidianSurface({ unregisterOverlay = true, resetWindow = false } = {}) {
  if (isStandaloneMode()) {
    document.body.classList.add('obsidian-open');
    return;
  }
  isPanelOpen = false;
  clearObsidianTransientShellState();
  clearObsidianSurfaceClasses();
  document.body.classList.remove('obsidian-open', 'obsidian-fullscreen');
  minimizedSurfaceMode = null;
  stopVaultEventStream();
  setLauncherActive(false);
  const panel = document.getElementById('obsidian-panel');
  if (panel) panel.dataset.surfaceMode = normalizeSurfaceMode(getStoredSurfaceMode());
  const modal = getObsidianModal();
  if (modal) {
    modal.classList.remove('modal-minimized');
    modal.classList.add('hidden');
  }
  if (resetWindow) resetObsidianWindowStyles();
  if (unregisterOverlay) unregisterObsidianModal();
}

function showObsidianSurface(mode = getStoredSurfaceMode()) {
  const normalized = isStandaloneMode() ? 'fullscreen' : normalizeSurfaceMode(mode);
  clearObsidianTransientShellState();
  if (normalized === 'overlay' && Modals.isRegistered(OBSIDIAN_MODAL_ID) && Modals.isMinimized(OBSIDIAN_MODAL_ID)) {
    Modals.restore(OBSIDIAN_MODAL_ID);
    return;
  }
  if (normalized !== 'overlay' && Modals.isRegistered(OBSIDIAN_MODAL_ID) && Modals.isMinimized(OBSIDIAN_MODAL_ID)) {
    Modals.close(OBSIDIAN_MODAL_ID);
  }

  applyObsidianSurfaceMode(normalized);
  resetObsidianWindowStyles();
  const modal = getObsidianModal();
  modal?.classList.remove('hidden', 'modal-minimized');

  if (normalized === 'overlay') {
    ensureObsidianModalRegistered();
    wireObsidianOverlayWindow();
  } else {
    unregisterObsidianModal();
  }

  isPanelOpen = true;
  document.body.classList.add('obsidian-open');
  setLauncherActive(true);
  startVaultEventStream();
  Promise.resolve(loadVaultFiles()).finally(() => {
    requestGraphReflow('show_surface');
  });
}

function changeObsidianSurfaceMode(mode) {
  const normalized = normalizeSurfaceMode(mode);
  saveSurfaceMode(normalized);
  syncSurfaceModeControls(normalized);
  clearObsidianTransientShellState();
  if (isStandaloneMode()) return;
  if (isPanelOpen) {
    showObsidianSurface(normalized);
  }
}

function clampNumber(value, min, max) {
  const number = Number.parseFloat(value);
  if (!Number.isFinite(number)) return min;
  return Math.max(min, Math.min(max, number));
}

function panelWidthBounds() {
  const viewport = window.innerWidth || 1024;
  return {
    min: Math.min(MIN_PANEL_WIDTH, Math.max(320, viewport - 48)),
    max: Math.max(MIN_PANEL_WIDTH, Math.min(MAX_PANEL_WIDTH, viewport - 48)),
  };
}

function currentPanelWidth() {
  const content = document.querySelector('.obsidian-panel-content');
  return content?.getBoundingClientRect().width || Math.min(960, Math.round((window.innerWidth || 1200) * 0.55));
}

function setObsidianPanelCssVar(name, value) {
  const panel = document.getElementById('obsidian-panel');
  const target = panel || document.documentElement;
  target.style.setProperty(name, value);
}

function applyPanelWidth(width, { persist = false } = {}) {
  if (isStandaloneMode() || window.innerWidth <= 640) return;
  const bounds = panelWidthBounds();
  const next = clampNumber(width, bounds.min, bounds.max);
  setObsidianPanelCssVar('--obsidian-panel-width', `${next}px`);
  if (persist) localStorage.setItem(OBSIDIAN_PANEL_WIDTH_KEY, String(Math.round(next)));
}

function applySidebarWidth(width, { persist = false } = {}) {
  const panelWidth = currentPanelWidth();
  const maxByPanel = Math.max(MIN_SIDEBAR_WIDTH, Math.floor(panelWidth * 0.45));
  const next = clampNumber(width, MIN_SIDEBAR_WIDTH, Math.min(MAX_SIDEBAR_WIDTH, maxByPanel));
  setObsidianPanelCssVar('--obsidian-sidebar-width', `${next}px`);
  if (persist) localStorage.setItem(OBSIDIAN_SIDEBAR_WIDTH_KEY, String(Math.round(next)));
}

function restoreObsidianResizeState() {
  const savedPanel = localStorage.getItem(OBSIDIAN_PANEL_WIDTH_KEY);
  const savedSidebar = localStorage.getItem(OBSIDIAN_SIDEBAR_WIDTH_KEY);
  if (savedPanel) applyPanelWidth(savedPanel);
  applySidebarWidth(savedSidebar || DEFAULT_SIDEBAR_WIDTH);
}

function bindResizeHandle(handle, callbacks) {
  if (!handle || handle.dataset.resizeBound) return;
  handle.dataset.resizeBound = 'true';
  handle.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const start = callbacks.start(e);
    document.body.classList.add('obsidian-resizing');
    handle.classList.add('resizing');
    handle.setPointerCapture?.(e.pointerId);
    const onMove = (moveEvent) => callbacks.move(moveEvent, start);
    const onEnd = (endEvent) => {
      callbacks.end?.(endEvent, start);
      document.body.classList.remove('obsidian-resizing');
      handle.classList.remove('resizing');
      handle.releasePointerCapture?.(e.pointerId);
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onEnd);
      window.removeEventListener('pointercancel', onEnd);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onEnd);
    window.addEventListener('pointercancel', onEnd);
  });
}

function setupObsidianResizers() {
  restoreObsidianResizeState();
  bindResizeHandle(document.getElementById('obsidian-panel-resize-handle'), {
    start: (e) => ({ startX: e.clientX, startWidth: currentPanelWidth() }),
    move: (e, start) => {
      const delta = start.startX - e.clientX;
      applyPanelWidth(start.startWidth + delta);
      applySidebarWidth(localStorage.getItem(OBSIDIAN_SIDEBAR_WIDTH_KEY) || DEFAULT_SIDEBAR_WIDTH);
    },
    end: (e, start) => {
      const delta = start.startX - e.clientX;
      applyPanelWidth(start.startWidth + delta, { persist: true });
      applySidebarWidth(localStorage.getItem(OBSIDIAN_SIDEBAR_WIDTH_KEY) || DEFAULT_SIDEBAR_WIDTH, { persist: true });
      requestGraphReflow('panel_resize_end');
    },
  });
  bindResizeHandle(document.getElementById('obsidian-split-resize-handle'), {
    start: (e) => ({
      startX: e.clientX,
      startWidth: document.querySelector('.obsidian-sidebar')?.getBoundingClientRect().width || DEFAULT_SIDEBAR_WIDTH,
    }),
    move: (e, start) => applySidebarWidth(start.startWidth + (e.clientX - start.startX)),
    end: (e, start) => {
      applySidebarWidth(start.startWidth + (e.clientX - start.startX), { persist: true });
      requestGraphReflow('sidebar_resize_end');
    },
  });
  if (!window.__obsidianResizeViewportBound) {
    window.__obsidianResizeViewportBound = true;
    window.addEventListener('resize', () => {
      restoreObsidianResizeState();
      requestGraphReflow('viewport_resize');
    });
  }
}

function injectUIElements() {
  // 1. Sidebar tool section
  const toolsSection = document.getElementById('tools-section');
  if (toolsSection && !document.getElementById('tool-obsidian-btn')) {
    const btn = document.createElement('div');
    btn.className = 'list-item';
    btn.id = 'tool-obsidian-btn';
    btn.innerHTML = `
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;opacity:0.5;">
        <polygon points="6 2 18 2 18 6 6 6" />
        <rect x="3" y="6" width="18" height="16" rx="2" />
        <path d="M8 11h8M8 15h5" />
      </svg>
      <span class="grow">Obsidian</span>
    `;
    toolsSection.appendChild(btn);
  }

  // 2. Icon rail
  const iconRail = document.getElementById('icon-rail');
  if (iconRail && !document.getElementById('rail-obsidian')) {
    const settingsBtn = document.getElementById('rail-settings');
    const btn = document.createElement('button');
    btn.className = 'icon-rail-btn';
    btn.id = 'rail-obsidian';
    btn.title = 'Obsidian Vault';
    btn.innerHTML = `
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polygon points="6 2 18 2 18 6 6 6" />
        <rect x="3" y="6" width="18" height="16" rx="2" />
        <path d="M8 11h8M8 15h5" />
      </svg>
    `;
    if (settingsBtn) {
      iconRail.insertBefore(btn, settingsBtn);
    } else {
      iconRail.appendChild(btn);
    }
  }

  // 3. Panel skeleton (right-docked, full-height, like Notes)
  if (!document.getElementById('obsidian-panel')) {
    const panelHtml = `
      <div id="obsidian-panel" class="obsidian-panel">
        <div class="obsidian-panel-backdrop" id="obsidian-panel-backdrop"></div>
        <div id="obsidian-modal" class="obsidian-modal modal hidden">
        <div class="obsidian-panel-content modal-content">
          <div class="obsidian-panel-resize-handle" id="obsidian-panel-resize-handle" role="separator" aria-label="Panel Resize Handle" aria-orientation="vertical"></div>
          <!-- Header -->
          <div class="obsidian-panel-header modal-header">
            <div class="obsidian-panel-title">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="margin-right:8px;opacity:0.8;">
                <polygon points="6 2 18 2 18 6 6 6" />
                <rect x="3" y="6" width="18" height="16" rx="2" />
                <path d="M8 11h8M8 15h5" />
              </svg>
              <span>Obsidian Vault</span>
            </div>
            <div class="obsidian-panel-actions">
              <label class="obsidian-header-view-toggle" title="Switch document or graph view">
                <span>Editor</span>
                <input type="checkbox" id="obsidian-header-view-toggle">
                <span class="obsidian-toggle-track" aria-hidden="true"></span>
                <span>Graph</span>
              </label>
              <button class="obsidian-panel-btn" id="obsidian-settings-toggle" title="Vault settings">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <circle cx="12" cy="12" r="3"></circle>
                  <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1A2 2 0 1 1 4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1A2 2 0 1 1 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3h.1a1.7 1.7 0 0 0 .9-1.6V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1A2 2 0 1 1 19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9v.1a1.7 1.7 0 0 0 1.6.9h.1a2 2 0 1 1 0 4H21a1.7 1.7 0 0 0-1.6 1Z"></path>
                </svg>
              </button>
              <button class="obsidian-panel-btn minimize-btn" id="obsidian-panel-minimize" title="Minimize">─</button>
              <button class="obsidian-panel-btn" id="obsidian-panel-close" title="Close">✕</button>
              <div class="obsidian-settings-menu hidden" id="obsidian-settings-menu" role="menu">
                <div class="obsidian-surface-mode" role="radiogroup" aria-label="Window mode">
                  <div class="obsidian-settings-label">Window mode</div>
                  <div class="obsidian-surface-options">
                    <button type="button" class="obsidian-surface-option" data-obsidian-surface-mode="sidebar" role="radio" aria-checked="true">Sidebar</button>
                    <button type="button" class="obsidian-surface-option" data-obsidian-surface-mode="overlay" role="radio" aria-checked="false">Overlay</button>
                    <button type="button" class="obsidian-surface-option" data-obsidian-surface-mode="fullscreen" role="radio" aria-checked="false">Fullscreen</button>
                  </div>
                </div>
                <div class="obsidian-ai-status" role="group" aria-label="AI model status">
                  <div class="obsidian-settings-label">AI Model</div>
                  <div class="obsidian-ai-status-value" id="obsidian-ai-model-value">Loading...</div>
                  <div class="obsidian-ai-status-hint" id="obsidian-ai-model-hint"></div>
                </div>
                <button type="button" data-settings-action="import" role="menuitem">Import vault</button>
                <button type="button" data-settings-action="export" role="menuitem">Export vault</button>
                <button type="button" data-settings-action="set-password" role="menuitem">Set password</button>
                <button type="button" data-settings-action="remove-password" role="menuitem">Remove password</button>
                <button type="button" data-settings-action="reset-graph" role="menuitem">Reset graph view</button>
                <input type="file" id="obsidian-import-input" class="hidden" accept=".zip,application/zip">
              </div>
            </div>
          </div>

          <!-- Body -->
          <div class="obsidian-panel-body">
            <!-- Sidebar: Tree + Search -->
            <div class="obsidian-sidebar">
              <div class="obsidian-actions">
                <button id="obsidian-new-note" title="New Note">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                </button>
                <button id="obsidian-new-folder" title="New Folder">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
                </button>
                <button id="obsidian-project-plan" title="Plan Project">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6h11"/><path d="M9 12h11"/><path d="M9 18h11"/><path d="M4 6h1"/><path d="M4 12h1"/><path d="M4 18h1"/></svg>
                </button>
                <button id="obsidian-memory-read" title="Gedaechtnis Lesen">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/></svg>
                </button>
                <button id="obsidian-memory-review" title="Gedaechtnis Pflegen">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a7 7 0 0 0-7 7c0 2.2 1.02 4.16 2.61 5.44.53.43.89 1.05.89 1.73V18h7v-.83c0-.68.36-1.3.89-1.73A6.98 6.98 0 0 0 19 10a7 7 0 0 0-7-7Z"/><path d="M9 21h6"/><path d="M10 18v3"/><path d="M14 18v3"/></svg>
                </button>
                <button id="obsidian-memory-tree" title="Diagnostics">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v18"/><path d="M5 8h7"/><path d="M12 16h7"/><circle cx="5" cy="8" r="2"/><circle cx="19" cy="16" r="2"/><circle cx="12" cy="3" r="2"/><circle cx="12" cy="21" r="2"/></svg>
                </button>
                <button id="obsidian-spark" title="Insights">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.94 14.56 8.5 21l-1.44-6.44L.62 13.12l6.44-1.44L8.5 5.24l1.44 6.44 6.44 1.44-6.44 1.44Z"/><path d="M18 8V2"/><path d="M21 5h-6"/><path d="M19 22v-4"/><path d="M21 20h-4"/></svg>
                </button>
                <button id="obsidian-activity" title="Activity">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 8-4-16-3 8H2"/></svg>
                </button>
              </div>
              <div class="obsidian-search-box">
                <input type="text" id="obsidian-search-input" placeholder="Search notes..." autocomplete="off">
              </div>
              <div class="obsidian-file-tree" id="obsidian-file-tree"></div>
            </div>
            <div class="obsidian-split-resize-handle" id="obsidian-split-resize-handle" role="separator" aria-label="Split Resize Handle" aria-orientation="vertical"></div>

            <!-- Workspace: Editor / Graph -->
            <div class="obsidian-workspace">
              <div class="obsidian-project-planner hidden" id="obsidian-project-planner">
                <div class="obsidian-project-header">
                  <div>
                    <div class="obsidian-project-title">Project planning</div>
                    <div class="obsidian-project-subtitle" id="obsidian-project-target">No target folder selected</div>
                  </div>
                  <button type="button" class="obsidian-panel-btn" id="obsidian-project-close" title="Close project planner">x</button>
                </div>
                <div class="obsidian-project-form">
                  <div class="obsidian-project-select obsidian-project-folder-select" data-project-select="folder">
                    <select id="obsidian-project-folder" title="Target folder"></select>
                    <button type="button" class="obsidian-project-select-trigger" data-project-select-trigger="folder" aria-haspopup="listbox" aria-expanded="false"></button>
                    <div class="obsidian-project-select-menu hidden" data-project-select-menu="folder" role="listbox"></div>
                  </div>
                  <input id="obsidian-project-title" type="text" placeholder="Project title" autocomplete="off">
                  <div class="obsidian-project-select obsidian-project-kind-select" data-project-select="kind">
                    <select id="obsidian-project-kind" title="Project kind">
                      <option value="software">Software</option>
                    </select>
                    <button type="button" class="obsidian-project-select-trigger" data-project-select-trigger="kind" aria-haspopup="listbox" aria-expanded="false"></button>
                    <div class="obsidian-project-select-menu hidden" data-project-select-menu="kind" role="listbox"></div>
                  </div>
                  <div class="obsidian-project-description-wrap">
                    <textarea id="obsidian-project-description" placeholder="Project goal, scope, constraints, and useful context"></textarea>
                    <button type="button" id="obsidian-project-improve-description" class="obsidian-project-ai-btn" title="Improve prompt" aria-label="Improve project prompt">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.94 14.56 8.5 21l-1.44-6.44L.62 13.12l6.44-1.44L8.5 5.24l1.44 6.44 6.44 1.44-6.44 1.44Z"/><path d="M18 8V2"/><path d="M21 5h-6"/><path d="M19 22v-4"/><path d="M21 20h-4"/></svg>
                    </button>
                  </div>
                  <textarea id="obsidian-project-focus" placeholder="Custom prompts, priorities, sections to emphasize, tone, constraints, or quality checks"></textarea>
                  <div class="obsidian-project-actions">
                    <button type="button" id="obsidian-project-preview" class="btn btn-secondary">Create plan preview</button>
                    <button type="button" id="obsidian-project-apply" class="btn btn-primary" disabled>Create files</button>
                  </div>
                </div>
                <div class="obsidian-project-gamedev-draft hidden" id="obsidian-project-gamedev-draft"></div>
                <div class="obsidian-project-preview" id="obsidian-project-preview-panel"></div>
              </div>
              <div class="obsidian-memory-review-panel hidden" id="obsidian-memory-review-panel">
                <div class="obsidian-project-header">
                  <div>
                    <div class="obsidian-project-title" id="obsidian-memory-shell-title">Gedaechtnis Lesen</div>
                    <div class="obsidian-project-subtitle" id="obsidian-memory-target">Fragen, Quellen, Confidence und Graph-Jumps bleiben im Lesekontext gebuendelt.</div>
                  </div>
                  <button type="button" class="obsidian-panel-btn" id="obsidian-memory-close" title="Close memory workspace">x</button>
                </div>
                <div class="obsidian-memory-shell-toolbar">
                  <div class="obsidian-memory-shell-tabs" role="tablist">
                    <button type="button" data-memory-workspace-tab="read">Gedaechtnis Lesen</button>
                    <button type="button" data-memory-workspace-tab="maintain">Gedaechtnis Pflegen</button>
                  </div>
                </div>
                <div class="obsidian-memory-shell-view" id="obsidian-memory-read-view"></div>
                <div class="obsidian-memory-shell-view hidden" id="obsidian-memory-maintain-view">
                  <div class="obsidian-memory-form obsidian-project-form">
                    <label class="obsidian-memory-field obsidian-memory-title-field">
                      <span>Title (optional)</span>
                      <input id="obsidian-memory-title" type="text" placeholder="Title (optional)" autocomplete="off">
                      <small>Used for the new note title and filename. If empty, a title is generated from the insight.</small>
                    </label>
                    <label class="obsidian-memory-field obsidian-memory-action-field">
                      <span>Review action</span>
                      <select id="obsidian-memory-action" title="Review action">
                        <option value="save_to_obsidian">Save to vault</option>
                        <option value="append_to_note" hidden>Append to selected note</option>
                        <option value="review_queue">Queue for review</option>
                        <option value="memory_only">Memory only</option>
                        <option value="discard">Discard</option>
                      </select>
                      <small id="obsidian-memory-action-help">Choose whether this writes to Obsidian now, stages into the review queue, stays in memory, or is discarded.</small>
                    </label>
                    <div class="obsidian-memory-field obsidian-memory-destination-field" id="obsidian-memory-destination-field">
                      <span>Save to</span>
                      <button type="button" id="obsidian-memory-save-to" class="obsidian-memory-save-to" data-memory-destination-type="" data-memory-destination-path="" aria-label="Choose where to save this memory">
                        <span id="obsidian-memory-save-to-label"></span>
                      </button>
                      <small id="obsidian-memory-destination-help">Choose a folder to create a new note, or choose a note to append this insight.</small>
                      <input id="obsidian-memory-folder" type="hidden" value="">
                      <input id="obsidian-memory-note" type="hidden" value="">
                      <div class="obsidian-memory-destination-picker hidden" id="obsidian-memory-destination-picker">
                        <div class="obsidian-memory-picker-tabs">
                          <button type="button" data-memory-picker-tab="folders">Folders</button>
                          <button type="button" data-memory-picker-tab="notes">Notes</button>
                        </div>
                        <input id="obsidian-memory-picker-search" type="text" placeholder="Search vault..." autocomplete="off">
                        <div class="obsidian-memory-picker-hints" id="obsidian-memory-picker-hints"></div>
                        <div class="obsidian-memory-picker-list" id="obsidian-memory-picker-list"></div>
                      </div>
                    </div>
                    <div class="obsidian-memory-field obsidian-memory-tags-field">
                      <span>Tags</span>
                    <div class="obsidian-memory-tag-input" id="obsidian-memory-tags">
                      <div class="obsidian-memory-tag-chips" id="obsidian-memory-tag-chips"></div>
                      <input id="obsidian-memory-tag-entry" type="text" placeholder="Type a tag and press Enter" autocomplete="off">
                      <div class="obsidian-memory-tag-menu hidden" id="obsidian-memory-tag-menu"></div>
                    </div>
                    <small>Existing tags autocomplete from the vault. Press Enter to add the selected tag or create a new one.</small>
                    <small class="obsidian-memory-tag-feedback hidden" id="obsidian-memory-tag-feedback" data-state="idle"></small>
                  </div>
                    <label class="obsidian-memory-field obsidian-memory-content-field">
                      <span>Insight to save</span>
                      <textarea id="obsidian-memory-content" placeholder="Write the reviewed insight or decision that should become vault context."></textarea>
                      <small>This text becomes the saved note content or the appended memory section.</small>
                    </label>
                    <div class="obsidian-project-actions">
                      <button type="button" id="obsidian-memory-preview" class="btn btn-secondary">Preview changes</button>
                      <button type="button" id="obsidian-memory-apply" class="btn btn-primary" disabled>Apply to vault</button>
                    </div>
                  </div>
                  <div class="obsidian-project-preview" id="obsidian-memory-preview-panel"></div>
                  <div class="obsidian-memory-maintain-queue" id="obsidian-memory-maintain-queue"></div>
                </div>
              </div>
              <div class="obsidian-memory-tree-panel hidden" id="obsidian-memory-tree-panel">
                <div class="obsidian-project-header">
                  <div>
                    <div class="obsidian-project-title">Diagnostics</div>
                    <div class="obsidian-project-subtitle" id="obsidian-memory-tree-subtitle">Audit-, Struktur- und Health-Signale fuer Memory, Quarantine und RAPTOR.</div>
                  </div>
                  <button type="button" class="obsidian-panel-btn" id="obsidian-memory-tree-close" title="Close diagnostics">x</button>
                </div>
                <div class="obsidian-memory-tree-toolbar">
                  <div class="obsidian-memory-tree-tabs" role="tablist">
                    <button type="button" data-memory-tree-tab="tree">Tree</button>
                    <button type="button" data-memory-tree-tab="audit">Audit</button>
                    <button type="button" data-memory-tree-tab="quarantine">Quarantine</button>
                    <button type="button" data-memory-tree-tab="raptor">RAPTOR</button>
                  </div>
                  <button type="button" id="obsidian-memory-tree-refresh" class="btn btn-secondary">Refresh</button>
                </div>
                <div class="obsidian-memory-tree-content" id="obsidian-memory-tree-content"></div>
              </div>
              <div class="obsidian-spark-panel hidden" id="obsidian-spark-panel">
                <div class="obsidian-project-header">
                  <div>
                    <div class="obsidian-project-title">Insights</div>
                    <div class="obsidian-project-subtitle" id="obsidian-spark-subtitle">Lesende Hinweise, Muster, Quellenkontext und Canonicals fuer den aktuellen Scope.</div>
                  </div>
                  <button type="button" class="obsidian-panel-btn" id="obsidian-spark-close" title="Close insights">x</button>
                </div>
                <div class="obsidian-spark-toolbar">
                  <div class="obsidian-spark-tabs" role="tablist">
                    <button type="button" data-spark-tab="health">Insights</button>
                    <button type="button" data-spark-tab="plan">Follow-ups</button>
                    <button type="button" data-spark-tab="canonicals">Canonicals</button>
                  </div>
                  <div class="obsidian-project-actions" id="obsidian-spark-actions">
                    <button type="button" id="obsidian-spark-analyze" class="btn btn-primary">Insights aktualisieren</button>
                    <button type="button" id="obsidian-spark-plan" class="btn btn-secondary">Pflegeplan laden</button>
                    <button type="button" id="obsidian-spark-apply" class="btn btn-secondary" disabled>Ausgewaehlte Pflege pruefen</button>
                  </div>
                </div>
                <div class="obsidian-spark-content" id="obsidian-spark-content"></div>
              </div>
              <div class="obsidian-activity-panel hidden" id="obsidian-activity-panel">
                <div class="obsidian-project-header">
                  <div>
                    <div class="obsidian-project-title">Activity</div>
                    <div class="obsidian-project-subtitle" id="obsidian-activity-subtitle">Jobs, Automationen und Fehler bleiben operativ sichtbar.</div>
                  </div>
                  <button type="button" class="obsidian-panel-btn" id="obsidian-activity-close" title="Close activity">x</button>
                </div>
                <div class="obsidian-activity-content" id="obsidian-activity-content"></div>
              </div>
              <div class="obsidian-empty-state" id="obsidian-empty-state">
                <span>Select a note to start editing or create a new one</span>
              </div>
              <div class="obsidian-editor-container hidden" id="obsidian-editor-container">
                <div class="obsidian-editor-header">
                  <div class="obsidian-current-note-header">
                    <div class="obsidian-current-note-title" id="obsidian-current-note-title">Untitled.md</div>
                    <div class="obsidian-current-note-meta" id="obsidian-current-note-meta"></div>
                    <div class="obsidian-document-intelligence-mount" id="obsidian-document-intelligence-bar"></div>
                  </div>
                </div>
                <div class="obsidian-editor-toolbar" id="obsidian-editor-toolbar" aria-label="Markdown tools">
                  <button data-md-action="bold" title="Bold"><strong>B</strong></button>
                  <button data-md-action="italic" title="Italic"><em>I</em></button>
                  <button data-md-action="inline-code" title="Inline code"><code>&lt;/&gt;</code></button>
                  <button data-md-action="codeblock" title="Code block"><code>{ }</code></button>
                  <button data-md-action="heading" title="Heading">H</button>
                  <button data-md-action="list" title="Bullet list">-</button>
                  <button data-md-action="checkbox" title="Checkbox">[ ]</button>
                  <button data-md-action="quote" title="Quote">&gt;</button>
                  <button data-md-action="link" title="Markdown link">link</button>
                  <button data-md-action="wikilink" title="Wiki link">[[ ]]</button>
                  <button data-md-action="tag" title="Tag">#</button>
                  <button data-md-action="table" title="Table">tbl</button>
                </div>
                <div class="obsidian-editor-panes">
                  <div class="obsidian-pane obsidian-editor-pane">
                    <textarea id="obsidian-textarea" placeholder="Start writing markdown..."></textarea>
                    <div id="obsidian-autocomplete" class="obsidian-autocomplete hidden" role="listbox"></div>
                  </div>
                  <div class="obsidian-pane obsidian-preview-pane" id="obsidian-rendered-preview"></div>
                </div>
              </div>
              <div class="obsidian-graph-view hidden" id="obsidian-graph-view"></div>
            </div>
          </div>
        </div>
        </div>
      </div>
    `;
    const div = document.createElement('div');
    div.innerHTML = panelHtml;
    document.body.appendChild(div.firstElementChild);
  }
}

// ─── Panel Toggle ────────────────────────────────────────────────────────────

function togglePanel() {
  const mode = getStoredSurfaceMode();
  if (mode === 'overlay' && Modals.isRegistered(OBSIDIAN_MODAL_ID) && Modals.isMinimized(OBSIDIAN_MODAL_ID)) {
    Modals.restore(OBSIDIAN_MODAL_ID);
    return;
  }
  if (isPanelOpen) closePanel();
  else showObsidianSurface(mode);
}

function openPanel() {
  if (isPanelOpen) return;
  showObsidianSurface(getStoredSurfaceMode());
}

function closePanel(options = {}) {
  if (!isPanelOpen) return;
  if (isStandaloneMode()) {
    clearObsidianTransientShellState();
    document.body.classList.add('obsidian-open');
    return;
  }
  hideObsidianSurface({
    unregisterOverlay: options.unregisterOverlay !== false,
    resetWindow: true,
  });
}

function minimizePanel() {
  const mode = normalizeSurfaceMode(document.getElementById('obsidian-panel')?.dataset.surfaceMode || getStoredSurfaceMode());
  minimizedSurfaceMode = mode;
  ensureObsidianModalRegistered();
  Modals.minimize(OBSIDIAN_MODAL_ID);
  isPanelOpen = false;
  clearObsidianTransientShellState();
  clearObsidianSurfaceClasses();
  document.body.classList.remove('obsidian-open', 'obsidian-fullscreen');
  setLauncherActive(false);
  showToast('Obsidian minimized');
}

// ─── File Tree ───────────────────────────────────────────────────────────────

async function loadProjectPlanSessions() {
  try {
    const res = await fetch('/api/plugins/obsidian/project-plan/sessions');
    if (!res.ok) {
      projectPlanSessions = [];
      return;
    }
    const data = await res.json();
    projectPlanSessions = data.sessions || [];
  } catch (e) {
    console.error('Failed to load project planning sessions:', e);
    projectPlanSessions = [];
  }
}

async function loadVaultFiles() {
  try {
    const [res] = await Promise.all([
      fetch('/api/plugins/obsidian/files'),
      loadProjectPlanSessions(),
    ]);
    if (res.ok) {
      vaultFiles = await res.json();
      tagCache = null;
      renderFileTree();
      if (currentViewMode === 'graph') {
        renderGraphView();
      }
    }
  } catch (e) {
    console.error('Failed to load vault files:', e);
  }
}

function scheduleVaultRefresh(reason = 'vault_changed') {
  if (vaultRefreshTimeout) clearTimeout(vaultRefreshTimeout);
  vaultRefreshTimeout = setTimeout(async () => {
    vaultRefreshTimeout = null;
    await loadVaultFiles();
    if (currentNotePath && !autosaveTimeout) {
      await openNote(currentNotePath);
    }
    if (reason !== 'ready') {
      console.debug('[obsidian] vault refreshed from event:', reason);
    }
  }, 250);
}

function startVaultEventStream() {
  if (vaultEvents || typeof EventSource === 'undefined') return;
  vaultEvents = new EventSource('/api/plugins/obsidian/vault/events');
  vaultEvents.addEventListener('ready', () => {
    scheduleVaultRefresh('ready');
  });
  vaultEvents.addEventListener('vault_changed', () => {
    scheduleVaultRefresh('vault_changed');
  });
  vaultEvents.onerror = () => {
    if (!vaultEvents) return;
    console.warn('[obsidian] vault event stream interrupted; browser will retry');
  };
}

function stopVaultEventStream() {
  if (vaultRefreshTimeout) {
    clearTimeout(vaultRefreshTimeout);
    vaultRefreshTimeout = null;
  }
  if (vaultEvents) {
    vaultEvents.close();
    vaultEvents = null;
  }
}

function renderFileTree() {
  const container = document.getElementById('obsidian-file-tree');
  if (!container) return;
  buildTreeHTML(visibleTreeNodes(vaultFiles), container, 0);
  if (!container.dataset.dndBound) {
    container.dataset.dndBound = 'true';
    container.addEventListener('dragover', (e) => {
      if (e.dataTransfer?.types.includes('application/x-obsidian-path') || e.dataTransfer?.files?.length) {
        e.preventDefault();
        container.classList.add('drag-over-root');
      }
    });
    container.addEventListener('dragleave', (e) => {
      if (!container.contains(e.relatedTarget)) {
        container.classList.remove('drag-over-root');
      }
    });
    container.addEventListener('drop', async (e) => {
      e.preventDefault();
      container.classList.remove('drag-over-root');
      const oldPath = e.dataTransfer?.getData('application/x-obsidian-path');
      if (oldPath) {
        await moveVaultItem(oldPath, '');
        return;
      }
      if (e.dataTransfer?.files?.length) {
        await importDroppedMarkdownFiles(e.dataTransfer.files, '');
      }
    });
  }
}

function buildTreeHTML(nodes, container, level) {
  if (level === 0) container.innerHTML = '';

  nodes.forEach(node => {
    const item = document.createElement('div');
    item.className = `tree-item ${node.is_dir ? 'tree-folder' : 'tree-file'}`;
    if (node.is_virtual_root) item.classList.add('tree-root-node');
    if (node.is_virtual_project_session) item.classList.add('tree-project-session');
    if (node.is_virtual_project_session_file) item.classList.add('tree-project-session-file');
    item.dataset.path = node.path;
    const isCurrentNote = !node.is_dir && currentNotePath === node.path;
    const isSelected = selectedTreePath === node.path || (!selectedTreePath && isCurrentNote);
    const isFolderSelected = node.is_dir && selectedTreePath === node.path;
    const isFileSelected = !node.is_dir && isSelected;
    const isInlineRenaming = inlineRenamePath === node.path;
    if (isSelected) {
      item.classList.add('active');
    }

    const header = document.createElement('div');
    header.className = 'tree-item-header';
    header.style.paddingLeft = `${level * 12 + 6}px`;
    header.draggable = !isInlineRenaming && !node.is_virtual_root && !node.is_virtual_project_session && !node.is_virtual_project_session_file;

    const icon = document.createElement('span');
    icon.className = 'tree-item-icon';
    if (node.is_virtual_root) {
      icon.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 10.5 12 3l9 7.5"/><path d="M5 10v10h14V10"/><path d="M9 20v-6h6v6"/></svg>`;
    } else if (node.is_virtual_project_session) {
      icon.innerHTML = `<span class="tree-project-session-spinner" aria-hidden="true"></span>`;
    } else if (node.is_virtual_project_session_file) {
      icon.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`;
    } else if (node.is_dir) {
      const isExpanded = expandedFolders.has(node.path);
      icon.innerHTML = isExpanded
        ? `<svg class="chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
           <svg class="folder" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`
        : `<svg class="chevron" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" style="transform: rotate(-90deg)"><polyline points="6 9 12 15 18 9"/></svg>
           <svg class="folder" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>`;
    } else {
      icon.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`;
    }

    let name;
    if (isInlineRenaming && !node.is_virtual_project_session && !node.is_virtual_project_session_file) {
      name = document.createElement('input');
      name.className = 'tree-rename-input';
      name.dataset.path = node.path;
      name.value = node.name;
      name.addEventListener('click', (e) => e.stopPropagation());
      name.addEventListener('pointerdown', (e) => e.stopPropagation());
      name.addEventListener('keydown', async (e) => {
        e.stopPropagation();
        if (e.key === 'Escape') {
          e.preventDefault();
          cancelInlineRenameItem();
        }
        if (e.key === 'Enter') {
          e.preventDefault();
          await commitInlineRenameItem(node.path, name.value);
        }
      });
      name.addEventListener('blur', async () => {
        if (inlineRenamePath === node.path) {
          await commitInlineRenameItem(node.path, name.value);
        }
      });
    } else {
      name = document.createElement('span');
      name.className = 'tree-item-name';
      name.textContent = node.name;
    }

    header.appendChild(icon);
    header.appendChild(name);
    if (node.is_virtual_project_session) {
      const status = document.createElement('span');
      status.className = `tree-project-session-status tree-project-session-status-${String(node.wip_status || 'draft').toLowerCase()}`;
      status.textContent = sessionStatusLabel(node.wip_status);
      header.appendChild(status);
    }
    if (isFolderSelected && !isInlineRenaming && !node.is_virtual_project_session && !node.is_virtual_root) {
      const actions = document.createElement('span');
      actions.className = 'tree-item-actions';

      const renameButton = document.createElement('button');
      renameButton.type = 'button';
      renameButton.className = 'tree-rename-button';
      renameButton.title = 'Rename folder';
      renameButton.setAttribute('aria-label', 'Rename selected folder');
      renameButton.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>`;
      renameButton.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        startInlineRenameItem(node.path);
      });

      const deleteButton = document.createElement('button');
      deleteButton.type = 'button';
      deleteButton.className = 'tree-delete-button';
      deleteButton.title = 'Delete folder';
      deleteButton.setAttribute('aria-label', 'Delete selected folder');
      deleteButton.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>`;
      deleteButton.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        await deleteFolder(node.path);
      });

      actions.appendChild(renameButton);
      actions.appendChild(deleteButton);
      header.appendChild(actions);
    }
    if (isFileSelected && !isInlineRenaming && !node.is_virtual_project_session_file) {
      const actions = document.createElement('span');
      actions.className = 'tree-item-actions';

      const renameButton = document.createElement('button');
      renameButton.type = 'button';
      renameButton.className = 'tree-rename-button';
      renameButton.title = 'Rename note';
      renameButton.setAttribute('aria-label', 'Rename selected note');
      renameButton.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>`;
      renameButton.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        startInlineRenameItem(node.path);
      });

      const deleteButton = document.createElement('button');
      deleteButton.type = 'button';
      deleteButton.className = 'tree-delete-button';
      deleteButton.title = 'Delete note';
      deleteButton.setAttribute('aria-label', 'Delete selected note');
      deleteButton.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>`;
      deleteButton.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        await deleteNote(node.path);
      });

      actions.appendChild(renameButton);
      actions.appendChild(deleteButton);
      header.appendChild(actions);
    }
    item.appendChild(header);

    if (node.is_dir && node.children && node.children.length > 0) {
      const childrenContainer = document.createElement('div');
      childrenContainer.className = 'tree-item-children';
      if (!expandedFolders.has(node.path)) {
        childrenContainer.style.display = 'none';
      }
      buildTreeHTML(node.children, childrenContainer, level + 1);
      item.appendChild(childrenContainer);
    }

    header.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (node.is_virtual_root) {
        selectedTreePath = VAULT_ROOT_TREE_PATH;
        renderFileTree();
        if (currentViewMode === 'graph') {
          renderGraphView();
        }
        return;
      }
      if (node.is_virtual_project_session || node.is_virtual_project_session_file) {
        selectTreeItem(node.path);
        await openProjectPlanSession(node.session_id);
        return;
      }
      if (node.is_dir) {
        if (inlineRenamePath === node.path) {
          return;
        }
        selectedTreePath = node.path;
        if (expandedFolders.has(node.path)) {
          expandedFolders.delete(node.path);
        } else {
          expandedFolders.add(node.path);
        }
        renderFileTree();
        if (currentViewMode === 'graph') {
          renderGraphView();
        }
      } else {
        selectTreeItem(node.path);
        openNote(node.path);
      }
    });

    header.addEventListener('dragstart', (e) => {
      if (node.is_virtual_project_session || node.is_virtual_project_session_file) {
        e.preventDefault();
        return;
      }
      e.dataTransfer.setData('application/x-obsidian-path', node.path);
      e.dataTransfer.effectAllowed = 'move';
      item.classList.add('dragging');
    });

    header.addEventListener('dragend', () => {
      item.classList.remove('dragging');
      document.querySelectorAll('.tree-item.drop-target').forEach(el => el.classList.remove('drop-target'));
    });

    if (node.is_dir && !node.is_virtual_project_session) {
      header.addEventListener('dragover', (e) => {
        if (e.dataTransfer?.types.includes('application/x-obsidian-path') || e.dataTransfer?.files?.length) {
          e.preventDefault();
          item.classList.add('drop-target');
        }
      });
      header.addEventListener('dragleave', () => {
        item.classList.remove('drop-target');
      });
      header.addEventListener('drop', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        item.classList.remove('drop-target');
        const oldPath = e.dataTransfer?.getData('application/x-obsidian-path');
        const dropTarget = node.is_virtual_root ? '' : node.path;
        if (oldPath) {
          await moveVaultItem(oldPath, dropTarget);
          return;
        }
        if (e.dataTransfer?.files?.length) {
          await importDroppedMarkdownFiles(e.dataTransfer.files, dropTarget);
        }
      });
    }

    container.appendChild(item);
  });
}

// ─── Note Operations ─────────────────────────────────────────────────────────

async function openNote(path) {
  try {
    currentNoteDocumentIntelligence = { state: 'loading' };
    const res = await fetch(`/api/plugins/obsidian/file?path=${encodeURIComponent(path)}`);
    if (res.ok) {
      const data = await res.json();
      currentNotePath = path;

      // Update UI panels visibility
      document.getElementById('obsidian-project-planner')?.classList.add('hidden');
      document.getElementById('obsidian-memory-review-panel')?.classList.add('hidden');
      document.getElementById('obsidian-memory-tree-panel')?.classList.add('hidden');
      document.getElementById('obsidian-spark-panel')?.classList.add('hidden');
      document.getElementById('obsidian-empty-state').classList.add('hidden');
      document.getElementById('obsidian-editor-container').classList.remove('hidden');

      // Update active selection class in tree
      selectTreeItem(path);

      // Update header title and textarea value
      document.getElementById('obsidian-current-note-title').textContent = path;
      const textarea = document.getElementById('obsidian-textarea');
      textarea.value = data.content || '';
      currentNoteDocumentIntelligence = computeDocumentIntelligence(path, data.content || '');
      applyCurrentNoteLensState();
      renderEditorPreview(textarea.value);
      if (currentViewMode === 'graph') {
        renderGraphView();
      }
    } else {
      currentNoteDocumentIntelligence = { state: 'error' };
    }
  } catch (e) {
    currentNoteDocumentIntelligence = { state: 'error' };
    console.error('Failed to open note:', e);
    showToast('Failed to open note');
  }
}

function setViewMode(mode) {
  currentViewMode = mode === 'graph' ? 'graph' : 'document';
  const editor = document.getElementById('obsidian-editor-container');
  const empty = document.getElementById('obsidian-empty-state');
  const panes = document.querySelector('.obsidian-editor-panes');
  const graph = document.getElementById('obsidian-graph-view');
  const toolbar = document.getElementById('obsidian-editor-toolbar');
  const toggle = document.getElementById('obsidian-header-view-toggle');
  if (toggle) toggle.checked = currentViewMode === 'graph';
  const graphMode = currentViewMode === 'graph';
  toolbar?.classList.toggle('hidden', graphMode);
  panes?.classList.toggle('hidden', graphMode);
  graph?.classList.toggle('hidden', !graphMode);
  if (graphMode) {
    editor?.classList.add('hidden');
    empty?.classList.add('hidden');
  } else if (currentNotePath) {
    editor?.classList.remove('hidden');
    empty?.classList.add('hidden');
  } else {
    editor?.classList.add('hidden');
    empty?.classList.remove('hidden');
  }
  if (currentViewMode === 'graph') {
    renderGraphView();
  } else {
    closeGraphFilterPanel();
  }
}

function applyCurrentNoteLensState() {
  const meta = currentNoteLensMeta();
  const metaEl = document.getElementById('obsidian-current-note-meta');
  const intelligenceEl = document.getElementById('obsidian-document-intelligence-bar');
  const textarea = document.getElementById('obsidian-textarea');
  const editor = document.getElementById('obsidian-editor-container');
  const toolbarButtons = document.querySelectorAll('#obsidian-editor-toolbar button');
  if (metaEl) {
    if (!meta.label) {
      metaEl.innerHTML = '';
    } else {
      metaEl.innerHTML = `
        <span class="obsidian-note-badge obsidian-note-badge-${escapeHtml(meta.kind)}">${escapeHtml(meta.label)}</span>
        <span class="obsidian-note-description">${escapeHtml(meta.description)}</span>
      `;
    }
  }
  if (intelligenceEl) intelligenceEl.innerHTML = renderDocumentIntelligenceBar();
  if (textarea) {
    textarea.readOnly = Boolean(meta.readOnly);
    if (meta.readOnly) {
      textarea.setAttribute('aria-readonly', 'true');
    } else {
      textarea.removeAttribute('aria-readonly');
    }
  }
  editor?.classList.toggle('obsidian-note-readonly', Boolean(meta.readOnly));
  toolbarButtons.forEach(button => {
    button.disabled = Boolean(meta.readOnly);
  });
  intelligenceEl?.querySelector('#obsidian-document-graph-jump')?.addEventListener('click', () => {
    if (!currentNotePath) return;
    setViewMode('graph');
  });
}

async function activateGraphNode(path) {
  if (!path) return;
  if (path === currentNotePath && currentViewMode === 'graph') {
    setViewMode('document');
    return;
  }
  await openNote(path);
}

function remapExpandedFolders(oldPath, newPath) {
  const next = new Set();
  expandedFolders.forEach(path => {
    if (path === oldPath || path.startsWith(`${oldPath}/`)) {
      next.add(path.replace(oldPath, newPath));
    } else {
      next.add(path);
    }
  });
  expandedFolders.clear();
  next.forEach(path => expandedFolders.add(path));
}

async function promptRenameSelectedItem() {
  const oldPath = selectedTreePath || currentNotePath;
  if (!oldPath) return;

  startInlineRenameItem(oldPath);
}

async function renameVaultItem(oldPath, newPath, isFolder) {
  try {
    const res = await fetch('/api/plugins/obsidian/rename', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_path: oldPath, new_path: newPath })
    });
    if (res.ok) {
      showToast(isFolder ? 'Renamed folder' : 'Renamed note');
      if (selectedTreePath === oldPath || selectedTreePath?.startsWith(`${oldPath}/`)) {
        selectedTreePath = selectedTreePath.replace(oldPath, newPath);
      }
      if (currentNotePath === oldPath || currentNotePath?.startsWith(`${oldPath}/`)) {
        currentNotePath = currentNotePath.replace(oldPath, newPath);
      }
      if (isFolder) {
        remapExpandedFolders(oldPath, newPath);
      }
      await loadVaultFiles();
      if (!isFolder && currentNotePath === newPath) {
        await openNote(newPath);
      } else if (isFolder && currentNotePath?.startsWith(`${newPath}/`)) {
        await openNote(currentNotePath);
      } else if (selectedTreePath) {
        selectTreeItem(selectedTreePath);
      }
    } else {
      const err = await res.json();
      showToast(err.detail || 'Failed to rename');
    }
  } catch (e) {
    console.error(e);
    showToast(isFolder ? 'Error renaming folder' : 'Error renaming note');
  }
}

function startInlineRenameItem(path) {
  const selected = findTreeNode(path);
  if (!selected) return;
  selectedTreePath = path;
  inlineRenamePath = path;
  renderFileTree();
  requestAnimationFrame(() => {
    const input = document.querySelector(`.tree-rename-input[data-path="${CSS.escape(path)}"]`);
    input?.focus();
    input?.select();
  });
}

function startInlineRenameFolder(path) {
  const selected = findTreeNode(path);
  if (!selected?.is_dir) return;
  startInlineRenameItem(path);
}

function cancelInlineRenameItem() {
  inlineRenamePath = null;
  renderFileTree();
}

function cancelInlineRenameFolder() {
  cancelInlineRenameItem();
}

function inlineRenameTargetPath(oldPath, trimmedName, isFolder) {
  const nextName = !isFolder
    && oldPath.toLowerCase().endsWith('.md')
    && !trimmedName.toLowerCase().endsWith('.md')
    ? `${trimmedName}.md`
    : trimmedName;
  return joinPath(getParentDir(oldPath), nextName);
}

async function commitInlineRenameItem(oldPath, nextName) {
  const selected = findTreeNode(oldPath);
  if (!selected) {
    inlineRenamePath = null;
    renderFileTree();
    return;
  }
  const trimmedName = (nextName || '').trim();
  inlineRenamePath = null;
  if (!trimmedName) {
    renderFileTree();
    return;
  }
  const newPath = inlineRenameTargetPath(oldPath, trimmedName, Boolean(selected.is_dir));
  if (newPath === oldPath) {
    renderFileTree();
    return;
  }
  await renameVaultItem(oldPath, newPath, Boolean(selected.is_dir));
}

async function commitInlineRenameFolder(oldPath, nextName) {
  const selected = findTreeNode(oldPath);
  if (!selected?.is_dir) return;
  await commitInlineRenameItem(oldPath, nextName);
}

async function deleteNote(path) {
  if (!path) return;
  const confirm = await styledConfirm('Are you sure you want to delete this note?', { confirmText: 'Delete', danger: true });
  if (!confirm) return;

  try {
    const res = await fetch(`/api/plugins/obsidian/file?path=${encodeURIComponent(path)}`, {
      method: 'DELETE'
    });
    if (res.ok) {
      showToast('Note deleted');
      if (selectedTreePath === path) {
        selectedTreePath = null;
      }
      if (currentNotePath === path) {
        currentNotePath = null;
        document.getElementById('obsidian-editor-container')?.classList.add('hidden');
        document.getElementById('obsidian-empty-state')?.classList.remove('hidden');
      }
      await loadVaultFiles();
      await refreshSearchResults();
    } else {
      showToast('Failed to delete note');
    }
  } catch (e) {
    console.error(e);
    showToast('Error deleting note');
  }
}

async function deleteFolder(path) {
  if (!path) return;
  const confirm = await styledConfirm('Are you sure you want to delete this folder and all of its contents?', { confirmText: 'Delete', danger: true });
  if (!confirm) return;

  try {
    const res = await fetch(`/api/plugins/obsidian/folder?path=${encodeURIComponent(path)}`, {
      method: 'DELETE'
    });
    if (res.ok) {
      showToast('Folder deleted');
      if (selectedTreePath === path) {
        selectedTreePath = null;
      }
      if (currentNotePath && (currentNotePath === path || currentNotePath.startsWith(path + '/'))) {
        currentNotePath = null;
        document.getElementById('obsidian-editor-container')?.classList.add('hidden');
        document.getElementById('obsidian-empty-state')?.classList.remove('hidden');
      }
      await loadVaultFiles();
      await refreshSearchResults();
    } else {
      showToast('Failed to delete folder');
    }
  } catch (e) {
    console.error(e);
    showToast('Error deleting folder');
  }
}

function currentTargetFolder() {
  if (selectedTreePath === VAULT_ROOT_TREE_PATH) return '';
  const selectedFolder = selectedFolderPath();
  if (selectedFolder) return selectedFolder;
  if (currentNotePath) return getParentDir(currentNotePath);
  const firstFolder = flattenTree(vaultFiles).find(node => node.is_dir);
  return firstFolder?.path || 'Projects';
}

function slugifyProjectTitle(value) {
  return (value || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'untitled-project';
}

function projectFolderOptions() {
  const folders = flattenTree(vaultFiles)
    .filter(node => node.is_dir)
    .map(node => node.path)
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b));
  return ['', ...folders.filter((path, index) => folders.indexOf(path) === index)];
}

function projectSelectIcon(type, key = '') {
  const normalized = String(key || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_');
  if (type === 'folder') {
    if (key === NEW_PROJECT_FOLDER_SENTINEL) {
      return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 10v6"/><path d="M9 13h6"/><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
    }
    return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
  }
  if (['research', 'study', 'science'].includes(normalized)) {
    return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/><path d="M8 11h6"/></svg>';
  }
  if (['writing', 'creative_writing', 'book'].includes(normalized)) {
    return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>';
  }
  if (['game_dev', 'gamedev', 'game_development', 'game'].includes(normalized)) {
    return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="6" y1="11" x2="10" y2="11"/><line x1="8" y1="9" x2="8" y2="13"/><line x1="15" y1="12" x2="15.01" y2="12"/><line x1="18" y1="10" x2="18.01" y2="10"/><path d="M17.3 6H6.7A4.7 4.7 0 0 0 2 10.7v2.6A4.7 4.7 0 0 0 6.7 18h.6a2 2 0 0 0 1.6-.8l1-1.4h4.2l1 1.4a2 2 0 0 0 1.6.8h.6a4.7 4.7 0 0 0 4.7-4.7v-2.6A4.7 4.7 0 0 0 17.3 6Z"/></svg>';
  }
  if (['sec_ops', 'secops', 'security_operations'].includes(normalized)) {
    return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>';
  }
  if (['security', 'cybersecurity', 'audit'].includes(normalized)) {
    return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 13c0 5-3.5 7.5-7.7 8.9a1 1 0 0 1-.6 0C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.2-2.4a1.4 1.4 0 0 1 1.6 0C14.5 3.8 17 5 19 5a1 1 0 0 1 1 1z"/></svg>';
  }
  if (['teaching', 'education', 'course', 'lesson'].includes(normalized)) {
    return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 10 12 5 2 10l10 5 10-5Z"/><path d="M6 12v5c3 2 9 2 12 0v-5"/></svg>';
  }
  if (['software', 'code', 'development'].includes(normalized)) {
    return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>';
  }
  return '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6h11"/><path d="M9 12h11"/><path d="M9 18h11"/><path d="M4 6h1"/><path d="M4 12h1"/><path d="M4 18h1"/></svg>';
}

function closeProjectSelectMenus(except = '') {
  document.querySelectorAll('.obsidian-project-select-menu').forEach(menu => {
    if (except && menu.dataset.projectSelectMenu === except) return;
    menu.classList.add('hidden');
  });
  document.querySelectorAll('.obsidian-project-select-trigger').forEach(trigger => {
    if (except && trigger.dataset.projectSelectTrigger === except) return;
    trigger.setAttribute('aria-expanded', 'false');
  });
}

function setProjectSelectValue(type, value) {
  const select = document.getElementById(type === 'folder' ? 'obsidian-project-folder' : 'obsidian-project-kind');
  if (!select) return;
  select.value = value;
  renderProjectCustomSelect(type);
  select.dispatchEvent(new Event('change', { bubbles: true }));
  closeProjectSelectMenus();
}

function renderProjectCustomSelect(type, items = null) {
  const select = document.getElementById(type === 'folder' ? 'obsidian-project-folder' : 'obsidian-project-kind');
  const trigger = document.querySelector(`[data-project-select-trigger="${type}"]`);
  const menu = document.querySelector(`[data-project-select-menu="${type}"]`);
  if (!select || !trigger || !menu) return;
  const options = items || [...select.options].map(option => ({ value: option.value, label: option.textContent || option.value }));
  const selected = options.find(item => item.value === select.value) || options[0] || { value: '', label: '' };
  trigger.innerHTML = `
    <span class="obsidian-project-select-icon">${projectSelectIcon(type, selected.value)}</span>
    <span class="obsidian-project-select-label">${escapeHtml(selected.label)}</span>
    <svg class="obsidian-project-select-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>
  `;
  menu.innerHTML = options.map(item => `
    <button type="button" class="obsidian-project-select-option ${item.value === select.value ? 'active' : ''}" data-project-select-value="${escapeHtml(item.value)}" role="option" aria-selected="${item.value === select.value ? 'true' : 'false'}">
      <span class="obsidian-project-select-icon">${projectSelectIcon(type, item.value)}</span>
      <span>${escapeHtml(item.label)}</span>
    </button>
  `).join('');
}

function toggleProjectSelectMenu(type) {
  const trigger = document.querySelector(`[data-project-select-trigger="${type}"]`);
  const menu = document.querySelector(`[data-project-select-menu="${type}"]`);
  if (!trigger || !menu) return;
  const willOpen = menu.classList.contains('hidden');
  closeProjectSelectMenus(type);
  menu.classList.toggle('hidden', !willOpen);
  trigger.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
}

function renderProjectFolderOptions() {
  const select = document.getElementById('obsidian-project-folder');
  if (!select) return;
  const previous = select.value;
  const folders = projectFolderOptions();
  select.innerHTML = folders.map(path => {
    const label = path || 'Vault root';
    return `<option value="${escapeHtml(path)}">${escapeHtml(label)}</option>`;
  }).join('') + `<option value="${NEW_PROJECT_FOLDER_SENTINEL}">${escapeHtml('Plan new project folder')}</option>`;
  if (previous && [...select.options].some(option => option.value === previous)) {
    select.value = previous;
  } else if ([...select.options].some(option => option.value === NEW_PROJECT_FOLDER_SENTINEL)) {
    select.value = NEW_PROJECT_FOLDER_SENTINEL;
  } else {
    select.value = '';
  }
  renderProjectCustomSelect('folder');
}

function resolveProjectTargetFolder() {
  const folderSelect = document.getElementById('obsidian-project-folder');
  const title = document.getElementById('obsidian-project-title')?.value || '';
  const selected = folderSelect?.value || '';
  if (selected === NEW_PROJECT_FOLDER_SENTINEL) {
    return `${NEW_PROJECT_FOLDER_SENTINEL}::`;
  }
  return selected || '';
}

function updateProjectTargetLabel() {
  const selected = document.getElementById('obsidian-project-folder')?.value || '';
  const title = document.getElementById('obsidian-project-title')?.value || '';
  const label = document.getElementById('obsidian-project-target');
  if (!label) return;
  if (selected === NEW_PROJECT_FOLDER_SENTINEL) {
    const slug = slugifyProjectTitle(title);
    label.textContent = `Target: ${slug}`;
  } else {
    label.textContent = selected ? `Target: ${selected}` : 'Target: vault root';
  }
}

async function loadProjectTemplateOptions() {
  const kindSelect = document.getElementById('obsidian-project-kind');
  if (!kindSelect) return;
  if (!projectTemplateOptions) {
    const res = await fetch('/api/plugins/obsidian/project-plan/templates');
    if (!res.ok) throw new Error('Failed to load project templates');
    projectTemplateOptions = await res.json();
  }
  const previous = kindSelect.value || projectTemplateOptions.default_kind || 'software';
  const kinds = projectTemplateOptions.kinds || [];
  kindSelect.innerHTML = kinds.map(kind => {
    const key = typeof kind === 'string' ? kind : kind.key;
    const label = typeof kind === 'string' ? kind : kind.label;
    return `<option value="${escapeHtml(key)}">${escapeHtml(label)}</option>`;
  }).join('');
  kindSelect.value = kinds.some(kind => (typeof kind === 'string' ? kind : kind.key) === previous)
    ? previous
    : (projectTemplateOptions.default_kind || 'software');
  renderProjectCustomSelect('kind');
}

function isGameDevProjectKind(kind) {
  const normalized = String(kind || '').trim().toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '');
  return ['game_dev', 'gamedev', 'game_development'].includes(normalized);
}

function clearGameDevDraft() {
  gameDevConceptDraft = null;
  const panel = document.getElementById('obsidian-project-gamedev-draft');
  if (panel) {
    panel.classList.add('hidden');
    panel.innerHTML = '';
  }
}

function invalidateProjectPlanPreview({ clearDraft = true } = {}) {
  projectPlanPreview = null;
  const previewPanel = document.getElementById('obsidian-project-preview-panel');
  if (previewPanel) previewPanel.innerHTML = '';
  if (clearDraft) clearGameDevDraft();
}

function projectPlanRequestFromForm({ conceptApproved = false, approvedConcept = '' } = {}) {
  return {
    target_folder: resolveProjectTargetFolder(),
    title: document.getElementById('obsidian-project-title')?.value || '',
    kind: document.getElementById('obsidian-project-kind')?.value || 'software',
    description: document.getElementById('obsidian-project-description')?.value || '',
    custom_focus: document.getElementById('obsidian-project-focus')?.value || '',
    generate_content: true,
    approved_concept: approvedConcept,
    concept_approved: conceptApproved,
  };
}

function setProjectPlanFormFromRequest(requestPayload = {}) {
  const folder = document.getElementById('obsidian-project-folder');
  const title = document.getElementById('obsidian-project-title');
  const kind = document.getElementById('obsidian-project-kind');
  const description = document.getElementById('obsidian-project-description');
  const focus = document.getElementById('obsidian-project-focus');
  if (folder) {
    const requestedFolder = requestPayload.target_folder || '';
    folder.value = String(requestedFolder).startsWith(`${NEW_PROJECT_FOLDER_SENTINEL}::`)
      ? NEW_PROJECT_FOLDER_SENTINEL
      : requestedFolder;
  }
  if (title) title.value = requestPayload.title || '';
  if (kind) kind.value = requestPayload.kind || 'software';
  if (description) description.value = requestPayload.description || '';
  if (focus) focus.value = requestPayload.custom_focus || '';
  renderProjectCustomSelect('folder');
  renderProjectCustomSelect('kind');
  updateProjectTargetLabel();
}

function setActiveProjectPlanSession(session) {
  activeProjectPlanSession = session || null;
  activeProjectPlanSessionId = session?.id || null;
  if (session?.id) {
    const existing = projectPlanSessions.findIndex(item => item.id === session.id);
    if (existing >= 0) {
      projectPlanSessions[existing] = session;
    } else if (!['created', 'cancelled'].includes(String(session.status || '').toLowerCase())) {
      projectPlanSessions.push(session);
    }
  }
  renderFileTree();
}

function projectSessionProgressHtml() {
  if (!activeProjectPlanSession) return '';
  const progress = activeProjectPlanSession.progress || {};
  const status = activeProjectPlanSession.status || 'draft';
  const total = Number(progress.total_files || 0);
  const current = Math.min(Number(progress.current_index || 0), total || Number(progress.current_index || 0));
  const percent = total ? Math.max(0, Math.min(100, Math.round((current / total) * 100))) : 0;
  return `
    <div class="obsidian-project-session-progress" data-project-session-status="${escapeHtml(status)}">
      <div class="obsidian-project-session-progress-head">
        <strong>${escapeHtml(sessionStatusLabel(status))}</strong>
        <span>${escapeHtml(progress.message || 'Recoverable WIP plan')}</span>
        <button type="button" class="obsidian-project-session-cancel" data-project-session-cancel>Cancel planning</button>
      </div>
      <div class="obsidian-project-session-bar" aria-hidden="true"><span style="width:${percent}%"></span></div>
      ${total ? `<div class="obsidian-project-session-count">${escapeHtml(current)} / ${escapeHtml(total)} files</div>` : ''}
    </div>
  `;
}

function projectSessionDebugHtml() {
  if (!activeProjectPlanSession) return '';
  const events = activeProjectPlanSession.debug_events || [];
  const sessionId = activeProjectPlanSession.id || activeProjectPlanSessionId || '';
  const status = activeProjectPlanSession.status || 'draft';
  const progress = activeProjectPlanSession.progress || {};
  const rows = events.slice(-18).reverse().map(event => {
    const bits = [
      event.phase || 'event',
      event.file || '',
      event.message || '',
      event.error ? `Error: ${event.error}` : '',
    ].filter(Boolean).join(' - ');
    return `<li><time>${escapeHtml(event.ts || '')}</time><span>${escapeHtml(bits)}</span></li>`;
  }).join('');
  return `
    <details class="obsidian-project-debug" open>
      <summary>Debug</summary>
      <div class="obsidian-project-debug-meta">
        <span>Session: ${escapeHtml(sessionId || 'none')}</span>
        <span>Status: ${escapeHtml(status)}</span>
        <span>Phase: ${escapeHtml(progress.phase || '')}</span>
        <span>${escapeHtml(progress.message || '')}</span>
      </div>
      <ol>${rows || '<li><span>No debug events yet</span></li>'}</ol>
    </details>
  `;
}

function renderProjectSessionDraft() {
  const panel = document.getElementById('obsidian-project-preview-panel');
  if (!panel || !activeProjectPlanSession) return;
  panel.innerHTML = `
    ${projectSessionProgressHtml()}
    ${projectSessionDebugHtml()}
    <div class="obsidian-project-ok">Preview saves a recoverable WIP plan.</div>
  `;
  bindProjectSessionActions();
  updateProjectApplyState();
}

function bindProjectSessionActions() {
  document.querySelector('[data-project-session-cancel]')?.addEventListener('click', async (e) => {
    e.preventDefault();
    await cancelActiveProjectPlanSession();
  });
}

function updateProjectApplyState() {
  const applyBtn = document.getElementById('obsidian-project-apply');
  if (!applyBtn) return;
  const conflicts = projectPlanPreview?.conflicts || [];
  const files = projectPlanPreview?.files || [];
  const sessionStatus = String(activeProjectPlanSession?.status || '').toLowerCase();
  applyBtn.disabled = projectPlanPreviewStreaming
    || conflicts.length > 0
    || files.length === 0
    || ['draft', 'generating', 'applying', 'error'].includes(sessionStatus);
}

async function cancelActiveProjectPlanSession() {
  if (!activeProjectPlanSessionId) return;
  const confirmed = await styledConfirm('Cancel this recoverable project planning session?', { confirmText: 'Cancel planning', danger: true });
  if (!confirmed) return;
  try {
    const res = await fetch(`/api/plugins/obsidian/project-plan/sessions/${encodeURIComponent(activeProjectPlanSessionId)}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Failed to cancel planning session');
    activeProjectPlanSession = null;
    activeProjectPlanSessionId = null;
    projectPlanPreview = null;
    await loadProjectPlanSessions();
    renderFileTree();
    closeProjectPlanner();
    showToast('Planning session cancelled');
  } catch (e) {
    console.error('Project planning cancel failed:', e);
    showToast(e.message || 'Failed to cancel planning session');
  }
}

function handleProjectInputChanged() {
  updateProjectTargetLabel();
  invalidateProjectPlanPreview();
}

function splitProjectList(value) {
  return String(value || '')
    .split(/[\n,]+/)
    .map(item => item.trim())
    .filter(Boolean);
}

function invalidateMemoryReviewPreview({ clearPanel = false } = {}) {
  memoryReviewPreview = null;
  const applyBtn = document.getElementById('obsidian-memory-apply');
  if (applyBtn) applyBtn.disabled = true;
  if (clearPanel) {
    const panel = document.getElementById('obsidian-memory-preview-panel');
    if (panel) panel.innerHTML = '';
  }
}

function memoryReviewActionLabel(action) {
  switch (action) {
    case 'append_to_note':
      return 'Append to note';
    case 'review_queue':
      return 'Queue for review';
    case 'memory_only':
      return 'Memory only';
    case 'discard':
      return 'Discard';
    case 'save_to_obsidian':
    default:
      return 'Create note';
  }
}

function memoryReviewDestinationLabel() {
  if (memoryReviewDestination.type === 'folder') {
    return memoryReviewDestination.path || 'Vault root';
  }
  if (memoryReviewDestination.type === 'note') {
    return memoryReviewDestination.path;
  }
  return '';
}

function updateMemoryReviewCopy() {
  const action = document.getElementById('obsidian-memory-action')?.value || 'save_to_obsidian';
  const subtitle = document.getElementById('obsidian-memory-target');
  const actionHelp = document.getElementById('obsidian-memory-action-help');
  const destinationHelp = document.getElementById('obsidian-memory-destination-help');
  if (subtitle) {
    subtitle.textContent = (
      action === 'review_queue'
        ? 'Stage one reviewed insight into the review queue for later curation'
        : action === 'memory_only'
          ? 'Keep one reviewed insight in Odysseus memory without writing to the vault'
          : action === 'discard'
            ? 'Discard one reviewed insight without changing the vault'
            : action === 'append_to_note'
              ? 'Append one reviewed insight to a selected note'
              : 'Save one reviewed insight into your vault'
    );
  }
  if (actionHelp) {
    actionHelp.textContent = (
      action === 'review_queue'
        ? 'Queueing writes into AI Memory/Review Queue so unresolved or duplicate-prone items stay staged instead of becoming settled canon.'
        : 'Choose whether this writes to Obsidian now, stages into the review queue, stays in memory, or is discarded.'
    );
  }
  if (destinationHelp) {
    destinationHelp.textContent = (
      action === 'review_queue'
        ? 'Queue entries are stored automatically under AI Memory/Review Queue; no manual destination is needed.'
        : 'Choose a folder to create a new note, or choose a note to append this insight.'
    );
  }
}

function syncMemoryDestinationFields() {
  const folderInput = document.getElementById('obsidian-memory-folder');
  const noteInput = document.getElementById('obsidian-memory-note');
  const saveTo = document.getElementById('obsidian-memory-save-to');
  const label = document.getElementById('obsidian-memory-save-to-label');
  const actionSelect = document.getElementById('obsidian-memory-action');
  const isFolder = memoryReviewDestination.type === 'folder';
  const isNote = memoryReviewDestination.type === 'note';

  if (folderInput) folderInput.value = isFolder ? memoryReviewDestination.path : '';
  if (noteInput) noteInput.value = isNote ? memoryReviewDestination.path : '';
  if (saveTo) {
    saveTo.dataset.memoryDestinationType = memoryReviewDestination.type;
    saveTo.dataset.memoryDestinationPath = memoryReviewDestination.path;
  }
  if (label) label.textContent = memoryReviewDestinationLabel();
  if (actionSelect && (isFolder || isNote)) {
    actionSelect.value = isNote ? 'append_to_note' : 'save_to_obsidian';
  }
}

function setMemoryReviewDestination(type, path) {
  memoryReviewDestination = { type, path: String(path || '').replace(/\\/g, '/').replace(/^\/+/, '') };
  syncMemoryDestinationFields();
  updateMemoryReviewActionUi();
  closeMemoryDestinationPicker();
  invalidateMemoryReviewPreview({ clearPanel: true });
}

function clearMemoryReviewDestination() {
  memoryReviewDestination = { type: '', path: '' };
  syncMemoryDestinationFields();
}

function updateMemoryReviewActionUi() {
  const action = document.getElementById('obsidian-memory-action')?.value || 'save_to_obsidian';
  const destinationField = document.getElementById('obsidian-memory-destination-field');
  const saveTo = document.getElementById('obsidian-memory-save-to');
  const requiresDestination = !['memory_only', 'discard', 'review_queue'].includes(action);
  destinationField?.classList.toggle('hidden', !requiresDestination);
  if (saveTo) saveTo.disabled = !requiresDestination;
  if (!requiresDestination) closeMemoryDestinationPicker();
  updateMemoryReviewCopy();
}

function handleMemoryActionChanged() {
  const action = document.getElementById('obsidian-memory-action')?.value || 'save_to_obsidian';
  if (action === 'save_to_obsidian' && memoryReviewDestination.type === 'note') {
    clearMemoryReviewDestination();
  }
  if (action === 'append_to_note' && memoryReviewDestination.type === 'folder') {
    clearMemoryReviewDestination();
  }
  if (action === 'review_queue' && memoryReviewDestination.type) {
    clearMemoryReviewDestination();
  }
  updateMemoryReviewActionUi();
  invalidateMemoryReviewPreview({ clearPanel: true });
}

function uniqueMemoryFolders() {
  const folders = flattenTree(vaultFiles)
    .filter(node => node.is_dir)
    .map(node => node.path)
    .filter(Boolean)
    .sort((a, b) => a.localeCompare(b));
  return ['', ...folders.filter((path, index) => folders.indexOf(path) === index)];
}

function memoryPickerItems() {
  const search = (document.getElementById('obsidian-memory-picker-search')?.value || '').trim().toLowerCase();
  const paths = memoryDestinationPickerTab === 'notes' ? flattenNotes(vaultFiles) : uniqueMemoryFolders();
  return paths.filter(path => {
    const label = path || 'Vault root';
    return !search || label.toLowerCase().includes(search);
  });
}

function renderMemoryDestinationPicker() {
  const picker = document.getElementById('obsidian-memory-destination-picker');
  const hints = document.getElementById('obsidian-memory-picker-hints');
  const list = document.getElementById('obsidian-memory-picker-list');
  if (!picker || !hints || !list) return;

  picker.querySelectorAll('[data-memory-picker-tab]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.memoryPickerTab === memoryDestinationPickerTab);
  });

  const hintItems = [];
  const selectedFolder = selectedFolderPath();
  const currentFolder = currentNotePath ? getParentDir(currentNotePath) : '';
  if (memoryDestinationPickerTab === 'folders') {
    hintItems.push({ label: 'Vault root', type: 'folder', path: '' });
    if (selectedFolder) hintItems.push({ label: `Selected folder: ${selectedFolder}`, type: 'folder', path: selectedFolder });
    if (currentFolder && currentFolder !== selectedFolder) hintItems.push({ label: `Current folder: ${currentFolder}`, type: 'folder', path: currentFolder });
  } else if (currentNotePath) {
    hintItems.push({ label: `Current note: ${currentNotePath}`, type: 'note', path: currentNotePath });
  }

  hints.innerHTML = hintItems.map(item => `
    <button type="button" data-memory-pick-type="${escapeHtml(item.type)}" data-memory-pick-path="${escapeHtml(item.path)}">${escapeHtml(item.label)}</button>
  `).join('');

  const type = memoryDestinationPickerTab === 'notes' ? 'note' : 'folder';
  const items = memoryPickerItems();
  list.innerHTML = items.length ? items.map(path => `
    <button type="button" data-memory-pick-type="${type}" data-memory-pick-path="${escapeHtml(path)}">
      <span>${escapeHtml(path || 'Vault root')}</span>
      <small>${type === 'note' ? getParentDir(path) || 'Vault root' : 'Folder'}</small>
    </button>
  `).join('') : '<div class="obsidian-memory-picker-empty">No matches</div>';

  picker.querySelectorAll('[data-memory-pick-type]').forEach(btn => {
    btn.addEventListener('click', () => setMemoryReviewDestination(btn.dataset.memoryPickType, btn.dataset.memoryPickPath || ''));
  });
}

function openMemoryDestinationPicker() {
  const picker = document.getElementById('obsidian-memory-destination-picker');
  if (!picker) return;
  picker.classList.remove('hidden');
  renderMemoryDestinationPicker();
  const search = document.getElementById('obsidian-memory-picker-search');
  search?.focus();
  search?.select();
}

function closeMemoryDestinationPicker() {
  document.getElementById('obsidian-memory-destination-picker')?.classList.add('hidden');
}

function tagChipKey(value) {
  return String(value || '').trim().toLowerCase();
}

function normalizeTagChipValue(value) {
  const raw = String(value || '').trim().replace(/^#+/, '');
  if (!raw) {
    return { value: '', error: 'Tag darf nicht leer sein.' };
  }
  const collapsed = raw.replace(/\s+/g, '-');
  if (!/^[A-Za-z0-9_/-]+$/.test(collapsed)) {
    return { value: '', error: 'Nur Buchstaben, Zahlen, /, - und _ sind erlaubt.' };
  }
  return { value: `#${collapsed}`, error: '' };
}

function setMemoryTagFeedback(message = '') {
  const feedback = document.getElementById('obsidian-memory-tag-feedback');
  if (!feedback) return;
  const text = String(message || '').trim();
  feedback.textContent = text;
  feedback.classList.toggle('hidden', !text);
  feedback.dataset.state = text ? 'error' : 'idle';
}

function clearMemoryTagPendingRemoval() {
  memoryTagPendingRemoval = '';
  document.querySelectorAll('[data-memory-tag].pending-removal').forEach(btn => {
    btn.classList.remove('pending-removal');
    btn.dataset.pendingRemoval = 'false';
  });
}

function markMemoryTagPendingRemoval(tag) {
  clearMemoryTagPendingRemoval();
  const key = tagChipKey(tag);
  memoryTagPendingRemoval = key;
  const chip = document.querySelector(`[data-memory-tag-key="${escapeHtml(key)}"]`);
  if (!chip) return;
  chip.classList.add('pending-removal');
  chip.dataset.pendingRemoval = 'true';
  chip.focus();
}

function removeMemoryTag(value, { restoreFocus = true } = {}) {
  const key = tagChipKey(value);
  memoryReviewTags = memoryReviewTags.filter(tag => tagChipKey(tag) !== key);
  clearMemoryTagPendingRemoval();
  renderMemoryTagChips();
  updateMemoryTagSuggestions();
  invalidateMemoryReviewPreview({ clearPanel: true });
  setMemoryTagFeedback('');
  if (restoreFocus) document.getElementById('obsidian-memory-tag-entry')?.focus();
}

function renderMemoryTagChips() {
  const chips = document.getElementById('obsidian-memory-tag-chips');
  if (!chips) return;
  chips.innerHTML = memoryReviewTags.map(tag => `
    <button
      type="button"
      class="obsidian-memory-tag-chip obsidian-tag-chip"
      data-memory-tag="${escapeHtml(tag)}"
      data-memory-tag-key="${escapeHtml(tagChipKey(tag))}"
      data-pending-removal="false"
      aria-label="Tag ${escapeHtml(tag)} entfernen"
    >
      <span>${escapeHtml(tag)}</span>
      <span class="obsidian-tag-chip-remove" aria-hidden="true">x</span>
    </button>
  `).join('');
  chips.querySelectorAll('[data-memory-tag]').forEach(btn => {
    btn.addEventListener('click', () => {
      removeMemoryTag(btn.dataset.memoryTag);
    });
    btn.addEventListener('focus', () => {
      clearMemoryTagPendingRemoval();
      btn.dataset.pendingRemoval = 'false';
    });
    btn.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ' || e.key === 'Backspace' || e.key === 'Delete') {
        e.preventDefault();
        removeMemoryTag(btn.dataset.memoryTag);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        clearMemoryTagPendingRemoval();
        document.getElementById('obsidian-memory-tag-entry')?.focus();
      }
    });
  });
}

function addMemoryTag(value) {
  const normalized = normalizeTagChipValue(value);
  if (normalized.error) {
    setMemoryTagFeedback(normalized.error);
    return false;
  }
  const tag = normalized.value;
  if (memoryReviewTags.some(existing => tagChipKey(existing) === tagChipKey(tag))) {
    setMemoryTagFeedback('Tag bereits vorhanden.');
    return false;
  }
  memoryReviewTags.push(tag);
  clearMemoryTagPendingRemoval();
  renderMemoryTagChips();
  const input = document.getElementById('obsidian-memory-tag-entry');
  if (input) input.value = '';
  hideMemoryTagMenu();
  invalidateMemoryReviewPreview({ clearPanel: true });
  setMemoryTagFeedback('');
  return true;
}

function hideMemoryTagMenu() {
  memoryTagPickerState = { index: 0, items: [] };
  const menu = document.getElementById('obsidian-memory-tag-menu');
  if (menu) {
    menu.classList.add('hidden');
    menu.innerHTML = '';
  }
}

function renderMemoryTagMenu() {
  const menu = document.getElementById('obsidian-memory-tag-menu');
  if (!menu || !memoryTagPickerState.items.length) {
    hideMemoryTagMenu();
    return;
  }
  menu.innerHTML = memoryTagPickerState.items.map((item, index) => `
    <button type="button" class="obsidian-memory-tag-option ${index === memoryTagPickerState.index ? 'active' : ''}" data-memory-tag-option="${escapeHtml(item.value)}">
      <span>${escapeHtml(item.label)}</span>
      <small>${escapeHtml(item.meta || '')}</small>
    </button>
  `).join('');
  menu.querySelectorAll('[data-memory-tag-option]').forEach(btn => {
    btn.addEventListener('mousedown', (e) => {
      e.preventDefault();
      addMemoryTag(btn.dataset.memoryTagOption);
    });
  });
  menu.classList.remove('hidden');
}

async function updateMemoryTagSuggestions() {
  const input = document.getElementById('obsidian-memory-tag-entry');
  if (!input || document.activeElement !== input) {
    hideMemoryTagMenu();
    return;
  }
  const query = input.value.trim().replace(/^#+/, '').toLowerCase();
  if (!query) {
    hideMemoryTagMenu();
    return;
  }
  const tags = (await getVaultTags())
    .filter(tag => tag.name.toLowerCase().includes(query))
    .filter(tag => !memoryReviewTags.some(existing => tagChipKey(existing) === tagChipKey(`#${tag.name}`)))
    .slice(0, 8)
    .map(tag => ({ value: `#${tag.name}`, label: `#${tag.name}`, meta: `${tag.files.length} notes` }));
  memoryTagPickerState = { index: 0, items: tags };
  renderMemoryTagMenu();
}

function handleMemoryTagKey(e) {
  const input = document.getElementById('obsidian-memory-tag-entry');
  if (!input) return;
  const items = memoryTagPickerState.items;
  if (e.key === 'ArrowDown' && items.length) {
    e.preventDefault();
    memoryTagPickerState.index = (memoryTagPickerState.index + 1) % items.length;
    renderMemoryTagMenu();
    return;
  }
  if (e.key === 'ArrowUp' && items.length) {
    e.preventDefault();
    memoryTagPickerState.index = (memoryTagPickerState.index - 1 + items.length) % items.length;
    renderMemoryTagMenu();
    return;
  }
  if (e.key === 'Enter') {
    e.preventDefault();
    const selected = items[memoryTagPickerState.index]?.value;
    addMemoryTag(selected || input.value);
    return;
  }
  if (e.key === 'ArrowLeft' && !input.value && memoryReviewTags.length) {
    e.preventDefault();
    markMemoryTagPendingRemoval(memoryReviewTags[memoryReviewTags.length - 1]);
    return;
  }
  if (e.key === 'Backspace' && !input.value && memoryReviewTags.length) {
    e.preventDefault();
    const lastTag = memoryReviewTags[memoryReviewTags.length - 1];
    if (memoryTagPendingRemoval && tagChipKey(lastTag) === memoryTagPendingRemoval) {
      removeMemoryTag(lastTag, { restoreFocus: true });
      return;
    }
    markMemoryTagPendingRemoval(lastTag);
    return;
  }
  if (e.key === 'Escape') {
    hideMemoryTagMenu();
    clearMemoryTagPendingRemoval();
    return;
  }
  clearMemoryTagPendingRemoval();
}

async function showProjectPlanner({ preserveSession = false } = {}) {
  const planner = document.getElementById('obsidian-project-planner');
  if (!planner) return;
  if (!preserveSession) {
    projectPlanPreview = null;
    activeProjectPlanSession = null;
    activeProjectPlanSessionId = null;
    clearGameDevDraft();
  }
  document.getElementById('obsidian-editor-container')?.classList.add('hidden');
  document.getElementById('obsidian-empty-state')?.classList.add('hidden');
  document.getElementById('obsidian-spark-panel')?.classList.add('hidden');
  document.getElementById('obsidian-memory-tree-panel')?.classList.add('hidden');
  planner.classList.remove('hidden');
  renderProjectFolderOptions();
  await loadProjectTemplateOptions().catch((e) => {
    console.error('Failed to load project templates:', e);
    showToast(e.message || 'Failed to load project templates');
  });
  updateProjectTargetLabel();
  if (!preserveSession) {
    document.getElementById('obsidian-project-preview-panel').innerHTML = '';
  }
}

async function openProjectPlanSession(sessionId) {
  if (!sessionId) return;
  try {
    const res = await fetch(`/api/plugins/obsidian/project-plan/sessions/${encodeURIComponent(sessionId)}`);
    if (!res.ok) throw new Error((await res.json()).detail || 'Failed to load planning session');
    const session = await res.json();
    await showProjectPlanner({ preserveSession: true });
    setActiveProjectPlanSession(session);
    setProjectPlanFormFromRequest(session.request || {});
    projectPlanPreview = session.plan || null;
    if (projectPlanPreview) {
      renderProjectPlanPreview(projectPlanPreview);
    } else {
      renderProjectSessionDraft();
    }
  } catch (e) {
    console.error('Failed to open project planning session:', e);
    showToast(e.message || 'Failed to open planning session');
  }
}

function closeProjectPlanner() {
  document.getElementById('obsidian-project-planner')?.classList.add('hidden');
  if (currentNotePath) {
    document.getElementById('obsidian-editor-container')?.classList.remove('hidden');
  } else {
    document.getElementById('obsidian-empty-state')?.classList.remove('hidden');
  }
}

function showMemoryReview() {
  const panel = document.getElementById('obsidian-memory-review-panel');
  if (!panel) return;
  document.getElementById('obsidian-project-planner')?.classList.add('hidden');
  document.getElementById('obsidian-spark-panel')?.classList.add('hidden');
  document.getElementById('obsidian-memory-tree-panel')?.classList.add('hidden');
  document.getElementById('obsidian-editor-container')?.classList.add('hidden');
  document.getElementById('obsidian-empty-state')?.classList.add('hidden');
  const wasHidden = panel.classList.contains('hidden');
  panel.classList.remove('hidden');
  if (wasHidden) {
    memoryReviewPreview = null;
    clearMemoryReviewDestination();
    const actionSelect = document.getElementById('obsidian-memory-action');
    if (actionSelect) actionSelect.value = 'save_to_obsidian';
    renderMemoryTagChips();
    updateMemoryReviewActionUi();
    document.getElementById('obsidian-memory-preview-panel').innerHTML = '';
    document.getElementById('obsidian-memory-apply').disabled = true;
  }
  setMemoryWorkspaceTab('maintain');
}

function closeMemoryReview() {
  document.getElementById('obsidian-memory-review-panel')?.classList.add('hidden');
  if (currentNotePath) {
    document.getElementById('obsidian-editor-container')?.classList.remove('hidden');
  } else {
    document.getElementById('obsidian-empty-state')?.classList.remove('hidden');
  }
}

function showMemoryReadWorkspace() {
  const panel = document.getElementById('obsidian-memory-review-panel');
  if (!panel) return;
  document.getElementById('obsidian-project-planner')?.classList.add('hidden');
  document.getElementById('obsidian-spark-panel')?.classList.add('hidden');
  document.getElementById('obsidian-memory-tree-panel')?.classList.add('hidden');
  document.getElementById('obsidian-editor-container')?.classList.add('hidden');
  document.getElementById('obsidian-empty-state')?.classList.add('hidden');
  panel.classList.remove('hidden');
  setMemoryWorkspaceTab('read');
  if (!sparkQueryStatus && !sparkQueryLoading) loadSparkQueryStatus();
}

function setMemoryWorkspaceTab(tab) {
  memoryWorkspaceTab = tab === 'maintain' ? 'maintain' : 'read';
  renderMemoryWorkspacePanel();
}

function renderMemoryWorkspacePanel() {
  const panel = document.getElementById('obsidian-memory-review-panel');
  if (!panel || panel.classList.contains('hidden')) return;
  const title = document.getElementById('obsidian-memory-shell-title');
  if (title) title.textContent = memoryWorkspaceTitle();
  const subtitle = document.getElementById('obsidian-memory-target');
  if (subtitle) subtitle.textContent = memoryWorkspaceSubtitle();
  document.querySelectorAll('[data-memory-workspace-tab]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.memoryWorkspaceTab === memoryWorkspaceTab);
  });
  const readView = document.getElementById('obsidian-memory-read-view');
  const maintainView = document.getElementById('obsidian-memory-maintain-view');
  readView?.classList.toggle('hidden', memoryWorkspaceTab !== 'read');
  maintainView?.classList.toggle('hidden', memoryWorkspaceTab !== 'maintain');
  if (memoryWorkspaceTab === 'read') {
    renderMemoryReadWorkspace();
  } else {
    renderMemoryMaintainWorkspace();
  }
  bindSparkPanelActions();
}

function renderMemoryReadWorkspace() {
  const container = document.getElementById('obsidian-memory-read-view');
  if (!container) return;
  container.innerHTML = `
    <div class="obsidian-memory-read-shell" data-memory-shell-role="read">
      ${renderSparkQuery()}
    </div>
  `;
}

function renderMemoryMaintainWorkspace() {
  const queue = document.getElementById('obsidian-memory-maintain-queue');
  if (!queue) return;
  const queuePaths = flattenNotes(vaultFiles).filter(path => path.startsWith('AI Memory/Review Queue/'));
  const queueMarkup = queuePaths.length
    ? renderSparkQueue()
    : renderLensStateCard('maintain', 'empty', 'Aktuell gibt es nichts zu pflegen.', 'Dokument pruefen', 'obsidian-memory-maintain-primary');
  queue.innerHTML = `
    <div class="obsidian-memory-maintain-queue-card" data-memory-shell-role="maintain">
      <div class="obsidian-project-summary">
        <strong>Review Queue</strong>
        <span>Pflegefaelle und offene Uebernahmen bleiben im Pflegekontext.</span>
      </div>
      ${queueMarkup}
    </div>
  `;
}

function setMemoryTreeTab(tab) {
  memoryTreeActiveTab = ['tree', 'audit', 'quarantine', 'raptor'].includes(tab) ? tab : 'tree';
  renderMemoryTreePanel();
}

async function fetchMemoryDashboardJson(path) {
  const res = await fetch(path);
  if (!res.ok) {
    let detail = '';
    try {
      detail = (await res.json()).detail || '';
    } catch (_err) {
      detail = '';
    }
    throw new Error(detail || `Request failed: ${path}`);
  }
  return res.json();
}

async function loadMemoryTreeDashboard() {
  memoryTreeLoading = true;
  memoryTreeError = '';
  renderMemoryTreePanel();
  try {
    const [status, tree, audit, quarantine, raptor] = await Promise.all([
      fetchMemoryDashboardJson('/api/plugins/obsidian/memory/status'),
      fetchMemoryDashboardJson('/api/plugins/obsidian/memory-tree/analyze'),
      fetchMemoryDashboardJson('/api/plugins/obsidian/knowledge-audit'),
      fetchMemoryDashboardJson('/api/plugins/obsidian/quarantine'),
      fetchMemoryDashboardJson('/api/plugins/obsidian/raptor/status'),
    ]);
    memoryStatusReport = status;
    memoryTreeReport = tree;
    knowledgeAuditReport = audit;
    quarantineReport = quarantine;
    raptorReport = raptor;
  } catch (e) {
    console.error('Knowledge audit load failed:', e);
    memoryTreeError = e.message || 'Knowledge audit failed';
    showToast(memoryTreeError);
  } finally {
    memoryTreeLoading = false;
    renderMemoryTreePanel();
  }
}

function showMemoryTreePanel() {
  const panel = document.getElementById('obsidian-memory-tree-panel');
  if (!panel) return;
  document.getElementById('obsidian-project-planner')?.classList.add('hidden');
  document.getElementById('obsidian-memory-review-panel')?.classList.add('hidden');
  document.getElementById('obsidian-spark-panel')?.classList.add('hidden');
  document.getElementById('obsidian-editor-container')?.classList.add('hidden');
  document.getElementById('obsidian-empty-state')?.classList.add('hidden');
  panel.classList.remove('hidden');
  renderMemoryTreePanel();
  if (!memoryStatusReport && !memoryTreeReport && !memoryTreeLoading) loadMemoryTreeDashboard();
}

function closeMemoryTreePanel() {
  document.getElementById('obsidian-memory-tree-panel')?.classList.add('hidden');
  if (currentNotePath) {
    document.getElementById('obsidian-editor-container')?.classList.remove('hidden');
  } else {
    document.getElementById('obsidian-empty-state')?.classList.remove('hidden');
  }
}

function memoryMetricGrid(metrics) {
  return `<div class="obsidian-memory-tree-metrics">${metrics.map(item => sparkMetric(item.label, item.value)).join('')}</div>`;
}

function renderMemoryWarnings(...reports) {
  const warnings = Array.from(new Set(
    reports
      .flatMap(report => [...(report?.warnings || []), ...(report?.summary?.warnings || [])])
      .map(item => String(item))
      .map(item => item.trim())
      .filter(Boolean)
  ));
  if (!warnings.length) return '';
  return `<div class="obsidian-project-warnings">${warnings.slice(0, 8).map(item => `<div>${escapeHtml(item)}</div>`).join('')}</div>`;
}

function renderMemoryStatusCounts(title, counts = {}) {
  const entries = Object.entries(counts || {});
  return `
    <div class="obsidian-memory-tree-card">
      <strong>${escapeHtml(title)}</strong>
      ${entries.length ? `<ul>${entries.map(([key, value]) => `<li><span>${escapeHtml(key)}</span><b>${escapeHtml(value)}</b></li>`).join('')}</ul>` : '<p>None</p>'}
    </div>
  `;
}

function renderMemoryReadinessFamilies() {
  const byFamily = memoryStatusReport?.readiness_by_family || {};
  const entries = Object.entries(byFamily);
  if (!entries.length) return '';
  return `
    <div class="obsidian-memory-status-families">
      ${entries.map(([family, signal]) => `
        <span data-memory-family-ready="${signal.ready ? 'true' : 'false'}">
          <b>${escapeHtml(family)}</b>
          <small>${escapeHtml(signal.state || 'unknown')}</small>
        </span>
      `).join('')}
    </div>
  `;
}

function memoryReadinessGapNames() {
  const gateGaps = memoryStatusReport?.readiness_gate?.gaps || memoryStatusReport?.summary?.readiness_gate?.gaps;
  if (Array.isArray(gateGaps) && gateGaps.length) {
    return gateGaps;
  }
  const summaryGaps = memoryStatusReport?.summary?.readiness_gap_names;
  if (Array.isArray(summaryGaps) && summaryGaps.length) {
    return summaryGaps;
  }
  const signals = Array.isArray(memoryStatusReport?.readiness_signals)
    ? memoryStatusReport.readiness_signals
    : [];
  return signals.flatMap(signal => Array.isArray(signal?.gaps) ? signal.gaps : []);
}

function renderMemoryReadinessGaps() {
  const gaps = [...new Set(memoryReadinessGapNames().map(gap => String(gap || '').trim()).filter(Boolean))];
  if (!gaps.length) return '';
  return `
    <div class="obsidian-memory-status-gaps" aria-label="Memory readiness gaps">
      ${gaps.slice(0, 12).map(gap => `<span>${escapeHtml(gap.replace(/_/g, ' '))}</span>`).join('')}
    </div>
  `;
}

function renderUnifiedMemoryStatus() {
  if (!memoryStatusReport) return '';
  const summary = memoryStatusReport.summary || {};
  const gate = memoryStatusReport.readiness_gate || summary.readiness_gate || {};
  const state = summary.readiness_state || 'unknown';
  const retrievalPolicy = memoryStatusReport.retrieval_policy || summary.retrieval_policy || {};
  const freshnessIsolationFlags = memoryStatusReport.freshness_isolation_flags || summary.freshness_isolation_flags || {};
  const activeFreshnessIsolationFlags = Object.entries(freshnessIsolationFlags)
    .filter(([name, value]) => value === true && name !== 'filtering_active')
    .map(([name]) => name.replace(/_/g, ' '));
  const raptorLineageFlags = memoryStatusReport.raptor_lineage_flags || summary.raptor_lineage_flags || {};
  const activeRaptorLineageFlags = Object.entries(raptorLineageFlags)
    .filter(([, value]) => value === true)
    .map(([name]) => name.replace(/_/g, ' '));
  const raptorWriteGate = memoryStatusReport.raptor_write_gate || summary.raptor_write_gate || {};
  return `
    <div class="obsidian-memory-status-band" data-memory-readiness-state="${escapeHtml(state)}">
      ${memoryMetricGrid([
        { label: 'Readiness', value: state },
        { label: 'Gate', value: gate.state || 'unknown' },
        { label: 'Ready families', value: `${summary.ready_families ?? 0}/${summary.readiness_families ?? summary.families ?? 0}` },
        { label: 'Status families', value: summary.status_families ?? Object.keys(memoryStatusReport.families || {}).length },
        { label: 'Filtering', value: summary.filtering_state || memoryStatusReport.filtering_state || 'unknown' },
        { label: 'Default filtered', value: retrievalPolicy.default_retrieval_is_filtered ? 'yes' : 'no' },
        { label: 'Gaps', value: summary.readiness_gaps ?? 0 },
        { label: 'Default retrieval', value: summary.default_retrieval ?? 0 },
        { label: 'Isolated', value: summary.isolated ?? 0 },
        { label: 'Freshness isolation', value: activeFreshnessIsolationFlags.length ? activeFreshnessIsolationFlags.join(', ') : 'clear' },
        { label: 'Quarantine', value: summary.quarantine_items ?? 0 },
        { label: 'RAPTOR sources', value: summary.raptor_sources ?? 0 },
        { label: 'RAPTOR lineage', value: activeRaptorLineageFlags.length ? activeRaptorLineageFlags.join(', ') : 'clear' },
        { label: 'RAPTOR write gate', value: raptorWriteGate.state || 'blocked' },
        { label: 'Writes', value: summary.writes_supported ? 'yes' : 'no' },
      ])}
      ${renderMemoryReadinessFamilies()}
      ${renderMemoryReadinessGaps()}
      ${renderMemoryWarnings(memoryStatusReport)}
    </div>
  `;
}

function shortMemorySourceHash(hash = '') {
  const value = String(hash || '');
  const normalized = value.startsWith('sha256:') ? value.slice(7) : value;
  return normalized ? normalized.slice(0, 12) : '';
}

function renderMemoryRecordLineage(item = {}) {
  const meta = [];
  if (item.policy) meta.push(`policy ${item.policy}`);
  if (item.source_mtime) meta.push(`mtime ${item.source_mtime}`);
  const hash = shortMemorySourceHash(item.source_hash || '');
  if (hash) meta.push(`hash ${hash}`);
  if (!meta.length) return '';
  return `<small class="obsidian-memory-lineage" data-memory-lineage="true">${escapeHtml(meta.join(' | '))}</small>`;
}

function renderMemoryRecordIsolation(item = {}) {
  if (item.default_retrieval !== false && !item.isolation_reason) return '';
  const reason = item.isolation_reason || 'Excluded from default retrieval.';
  return `<small class="obsidian-memory-record-isolation" data-memory-record-isolation="true">${escapeHtml(reason)}</small>`;
}

function renderMemoryRecordList(title, items = [], empty = 'No items') {
  return `
    <div class="obsidian-memory-tree-card">
      <strong>${escapeHtml(title)}</strong>
      ${items.length ? `<div class="obsidian-memory-tree-records">
        ${items.slice(0, 14).map(item => `
          <div class="obsidian-memory-tree-record">
            <div>
              <span>${escapeHtml(item.path || item.title || item.id || 'Untitled')}</span>
              <small>${escapeHtml(item.reason || item.status || item.kind || '')}</small>
              ${renderMemoryRecordIsolation(item)}
              ${renderMemoryRecordLineage(item)}
            </div>
            <em>${escapeHtml(item.status || item.channel || item.kind || '')}</em>
          </div>
        `).join('')}
      </div>` : `<p>${escapeHtml(empty)}</p>`}
    </div>
  `;
}

function memoryFreshnessFilteringState(report = {}) {
  const flags = report.flags || {};
  const gateEnabled = report.enabled !== false && flags.obsidian_freshness_gate_enabled !== false;
  const hybridEnabled = flags.obsidian_hybrid_retrieval_enabled === true;
  const rawState = report.filtering_state || report.summary?.filtering_state || '';
  const state = rawState
    ? String(rawState).replace(/_/g, '-')
    : (gateEnabled && hybridEnabled ? 'active' : (gateEnabled ? 'audit-only' : 'disabled'));
  return {
    state,
    label: state === 'active' ? 'active filtering' : (state === 'audit-only' ? 'audit only' : 'disabled'),
    description: state === 'active'
      ? 'Conflict, review, stale, superseded, archived, and quarantined knowledge stays visible here but is excluded from default context.'
      : (state === 'audit-only'
        ? 'Conflict, review, stale, superseded, archived, and quarantined knowledge stays visible here. Default context is unchanged because hybrid Freshness filtering is off.'
        : 'Freshness Gate is disabled. Audit records are shown here for visibility, but default context is unchanged.'),
  };
}

function renderRetrievalIsolationNotice({ conflicts = [], quarantined = [], needsReview = [], total = null, report = {} } = {}) {
  const isolated = (conflicts.length || 0) + (quarantined.length || 0) + (needsReview.length || 0);
  const count = total === null ? isolated : total;
  const filtering = memoryFreshnessFilteringState(report);
  return `
    <div class="obsidian-memory-isolation" data-memory-isolation="true" data-memory-isolation-state="${escapeHtml(filtering.state)}">
      <strong>Default retrieval isolation</strong>
      <small>${escapeHtml(filtering.label)}</small>
      <p>${escapeHtml(filtering.description)}</p>
      <ul>
        <li><span>Conflicts</span><b>${escapeHtml(conflicts.length || 0)}</b></li>
        <li><span>Needs review</span><b>${escapeHtml(needsReview.length || 0)}</b></li>
        <li><span>Quarantined</span><b>${escapeHtml(quarantined.length || 0)}</b></li>
        <li><span>Isolated total</span><b>${escapeHtml(count || 0)}</b></li>
      </ul>
    </div>
  `;
}

function renderMemoryTreeOverview() {
  if (!memoryTreeReport) {
    return '<div class="obsidian-project-loading">Run refresh to analyze the memory tree.</div>';
  }
  const summary = memoryTreeReport.summary || {};
  const readiness = memoryTreeReport.readiness || {};
  const gate = memoryTreeReport.readiness_gate || summary.readiness_gate || {};
  return `
    ${memoryMetricGrid([
      { label: 'Notes', value: summary.total_notes || 0 },
      { label: 'Readiness', value: summary.readiness_state || readiness.state || 'unknown' },
      { label: 'Gate', value: gate.state || 'unknown' },
      { label: 'Branches', value: summary.branch_candidates || 0 },
      { label: 'Default retrieval', value: summary.default_retrieval || 0 },
      { label: 'Isolated', value: summary.isolated || 0 },
      { label: 'Issues', value: (memoryTreeReport.issues || []).length },
      { label: 'Gaps', value: summary.readiness_gaps ?? (readiness.gaps || []).length },
      { label: 'Writes', value: memoryTreeReport.storage?.writes_performed ? 'yes' : 'no' },
    ])}
    ${renderMemoryWarnings(memoryTreeReport)}
    <div class="obsidian-memory-tree-grid">
      ${renderMemoryStatusCounts('Readiness gaps', (readiness.gaps || []).reduce((acc, gap) => ({ ...acc, [gap]: 1 }), {}))}
      ${renderMemoryStatusCounts('Status', summary.status_counts || {})}
      ${renderMemoryStatusCounts('Isolated statuses', summary.isolation_counts || {})}
      ${renderMemoryStatusCounts('Truth levels', summary.truth_level_counts || {})}
      ${renderMemoryRecordList('Branch candidates', memoryTreeReport.branches || [], 'No branch candidates')}
      ${renderMemoryRecordList('Issues', memoryTreeReport.issues || [], 'No issues detected')}
    </div>
  `;
}

function renderKnowledgeAudit() {
  if (!knowledgeAuditReport) {
    return '<div class="obsidian-project-loading">Run refresh to audit vault freshness.</div>';
  }
  const summary = knowledgeAuditReport.summary || {};
  const channels = knowledgeAuditReport.channels || {};
  const readiness = knowledgeAuditReport.readiness || {};
  const gate = knowledgeAuditReport.readiness_gate || summary.readiness_gate || {};
  return `
    ${memoryMetricGrid([
      { label: 'Current', value: summary.current || 0 },
      { label: 'Readiness', value: summary.readiness_state || readiness.state || 'unknown' },
      { label: 'Gate', value: gate.state || 'unknown' },
      { label: 'Needs review', value: summary.needs_review || 0 },
      { label: 'Conflicts', value: summary.conflicts || 0 },
      { label: 'Quarantined', value: summary.quarantined || 0 },
      { label: 'Default retrieval', value: summary.default_retrieval || 0 },
      { label: 'Isolated', value: summary.isolated || 0 },
    ])}
    ${renderRetrievalIsolationNotice({
      conflicts: channels.conflicts || [],
      needsReview: channels.needs_review || [],
      quarantined: channels.quarantined || [],
      report: knowledgeAuditReport,
    })}
    ${renderMemoryWarnings(knowledgeAuditReport)}
    <div class="obsidian-memory-tree-grid">
      ${renderMemoryStatusCounts('Review gaps', (readiness.gaps || []).reduce((acc, gap) => ({ ...acc, [gap]: 1 }), {}))}
      ${renderMemoryStatusCounts('Statuses', summary.status_counts || {})}
      ${renderMemoryStatusCounts('Isolated statuses', summary.isolation_counts || {})}
      ${renderMemoryRecordList('Needs review', channels.needs_review || [], 'No review items')}
      ${renderMemoryRecordList('Conflicts', channels.conflicts || [], 'No conflicts')}
      ${renderMemoryRecordList('Quarantined', channels.quarantined || [], 'No quarantined items')}
    </div>
  `;
}

function renderQuarantineList() {
  if (!quarantineReport) {
    return '<div class="obsidian-project-loading">Run refresh to load quarantine records.</div>';
  }
  const readiness = quarantineReport.readiness || {};
  const gate = quarantineReport.readiness_gate || quarantineReport.summary?.readiness_gate || {};
  return `
    ${memoryMetricGrid([
      { label: 'Items', value: quarantineReport.summary?.total || 0 },
      { label: 'Gate', value: quarantineReport.enabled ? 'on' : 'off' },
      { label: 'Gate state', value: gate.state || 'unknown' },
      { label: 'Readiness', value: quarantineReport.summary?.readiness_state || readiness.state || 'unknown' },
      { label: 'Default retrieval', value: quarantineReport.summary?.default_retrieval || 0 },
      { label: 'Isolated', value: quarantineReport.summary?.isolated || 0 },
    ])}
    ${renderRetrievalIsolationNotice({
      conflicts: (quarantineReport.items || []).filter(item => item.status === 'conflict' || item.channel === 'conflicts'),
      quarantined: (quarantineReport.items || []).filter(item => item.channel === 'quarantined'),
      needsReview: (quarantineReport.items || []).filter(item => item.channel === 'needs_review'),
      total: quarantineReport.summary?.total || 0,
      report: quarantineReport,
    })}
    ${renderMemoryWarnings(quarantineReport)}
    <div class="obsidian-memory-tree-grid">
      ${renderMemoryStatusCounts('Review gaps', (readiness.gaps || []).reduce((acc, gap) => ({ ...acc, [gap]: 1 }), {}))}
      ${renderMemoryStatusCounts('By status', quarantineReport.summary?.by_status || {})}
      ${renderMemoryStatusCounts('By channel', quarantineReport.summary?.by_channel || {})}
      ${renderMemoryRecordList('Isolated from default retrieval', quarantineReport.items || [], 'No quarantined knowledge')}
    </div>
  `;
}

function raptorDirtySourceRecords(lineage = {}) {
  return (lineage.dirty_sources || []).map(item => ({
    path: item.path || 'Unknown source',
    status: 'dirty',
    reason: `Source hash changed: expected ${shortMemorySourceHash(item.expected || '') || 'unknown'}, actual ${shortMemorySourceHash(item.actual || '') || 'unknown'}.`,
    source_hash: item.actual || '',
  }));
}

function raptorMissingSourceRecords(lineage = {}) {
  return (lineage.missing_sources || []).map(path => ({
    path,
    status: 'missing',
    reason: 'Source listed in RAPTOR lineage is missing from the vault.',
  }));
}

function raptorTaintedSourceRecords(lineage = {}) {
  return (lineage.tainted_sources || []).map(item => ({
    ...item,
    status: item.status || 'tainted',
    reason: item.reason || 'Freshness Gate isolates this source from default retrieval.',
  }));
}

function renderRaptorStatus() {
  if (!raptorReport) {
    return '<div class="obsidian-project-loading">Run refresh to inspect RAPTOR status.</div>';
  }
  const lineage = raptorReport.lineage || {};
  const summary = raptorReport.summary || {};
  const readiness = raptorReport.readiness || {};
  const gate = raptorReport.readiness_gate || summary.readiness_gate || {};
  const writeGate = raptorReport.write_gate || summary.write_gate || {};
  const lineageFlags = raptorReport.lineage_flags || summary.lineage_flags || {};
  const activeLineageFlags = Object.entries(lineageFlags)
    .filter(([, value]) => value === true)
    .map(([name]) => name.replace(/_/g, ' '));
  return `
    ${memoryMetricGrid([
      { label: 'Enabled', value: raptorReport.enabled ? 'yes' : 'no' },
      { label: 'Configured', value: raptorReport.configured ? 'yes' : 'no' },
      { label: 'Readiness', value: summary.readiness_state || readiness.state || 'unknown' },
      { label: 'Gate', value: gate.state || 'unknown' },
      { label: 'Sources', value: summary.source_count ?? lineage.source_count ?? 0 },
      { label: 'Dirty', value: summary.dirty_sources ?? (lineage.dirty_sources || []).length },
      { label: 'Missing', value: summary.missing_sources ?? (lineage.missing_sources || []).length },
      { label: 'Tainted', value: summary.tainted_sources ?? (lineage.tainted_sources || []).length },
      { label: 'Invalid', value: summary.invalid_sources ?? ((raptorReport.invalid_index ? 1 : 0) + (raptorReport.invalid_summaries ? 1 : 0)) },
      { label: 'Gaps', value: summary.readiness_gaps ?? (readiness.gaps || []).length },
      { label: 'Lineage flags', value: activeLineageFlags.length ? activeLineageFlags.join(', ') : 'clear' },
      { label: 'Write gate', value: writeGate.state || 'blocked' },
      { label: 'Writes', value: (summary.writes_supported ?? raptorReport.writes_supported) ? 'yes' : 'no' },
    ])}
    <div class="obsidian-memory-tree-grid">
      <div class="obsidian-memory-tree-card">
        <strong>Lineage</strong>
        <ul>
          <li><span>Index</span><b>${escapeHtml(raptorReport.index_present ? 'present' : 'missing')}</b></li>
          <li><span>Summaries</span><b>${escapeHtml(raptorReport.summaries_present ? 'present' : 'missing')}</b></li>
          <li><span>Last built</span><b>${escapeHtml(raptorReport.last_built || 'never')}</b></li>
          <li><span>Tainted</span><b>${escapeHtml(raptorReport.tainted ? 'yes' : 'no')}</b></li>
        </ul>
      </div>
      <div class="obsidian-memory-tree-card">
        <strong>Policy</strong>
        <p>${escapeHtml(raptorReport.message || 'RAPTOR status is read-only.')}</p>
        <p>${escapeHtml((writeGate.gaps || []).length ? `Write gate: ${(writeGate.gaps || []).join(', ')}` : 'RAPTOR writes are not enabled.')}</p>
        <p>${escapeHtml((readiness.gaps || []).length ? `Review gaps: ${(readiness.gaps || []).join(', ')}` : 'No RAPTOR readiness gaps detected.')}</p>
        <code>${escapeHtml(raptorReport.index_path || '')}</code>
        <code>${escapeHtml(raptorReport.summaries_path || '')}</code>
      </div>
      ${renderMemoryWarnings(raptorReport)}
      ${renderMemoryRecordList('Dirty sources', raptorDirtySourceRecords(lineage), 'No dirty sources')}
      ${renderMemoryRecordList('Missing sources', raptorMissingSourceRecords(lineage), 'No missing sources')}
      ${renderMemoryRecordList('Tainted sources', raptorTaintedSourceRecords(lineage), 'No tainted sources')}
    </div>
  `;
}

function renderMemoryTreePanel() {
  const content = document.getElementById('obsidian-memory-tree-content');
  if (!content) return;
  const subtitle = document.getElementById('obsidian-memory-tree-subtitle');
  if (subtitle) {
    const summary = memoryStatusReport?.summary || {};
    subtitle.textContent = summary.readiness_state
      ? `Memory readiness: ${summary.readiness_state} | ${summary.ready_families ?? 0}/${summary.readiness_families ?? summary.families ?? 0} families ready | ${summary.readiness_gaps ?? 0} gaps`
      : 'Audit-, Struktur- und Health-Signale fuer Memory, Quarantine und RAPTOR.';
  }
  document.querySelectorAll('[data-memory-tree-tab]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.memoryTreeTab === memoryTreeActiveTab);
  });
  const refresh = document.getElementById('obsidian-memory-tree-refresh');
  if (refresh) refresh.disabled = memoryTreeLoading;
  if (memoryTreeLoading && !memoryTreeReport) {
    content.innerHTML = renderLensStateCard('diagnostics', 'loading', 'Diagnostik wird geladen.', 'Status pruefen', 'obsidian-diagnostics-loading-action', { disabled: true });
    return;
  }
  if (memoryTreeError) {
    content.innerHTML = renderLensStateCard('diagnostics', 'error', 'Die Diagnostik konnte gerade nicht geladen werden.', 'Erneut laden', 'obsidian-diagnostics-retry', { detail: memoryTreeError });
    return;
  }
  if (memoryTreeActiveTab === 'audit') {
    content.innerHTML = renderUnifiedMemoryStatus() + renderKnowledgeAudit();
  } else if (memoryTreeActiveTab === 'quarantine') {
    content.innerHTML = renderUnifiedMemoryStatus() + renderQuarantineList();
  } else if (memoryTreeActiveTab === 'raptor') {
    content.innerHTML = renderUnifiedMemoryStatus() + renderRaptorStatus();
  } else {
    content.innerHTML = renderUnifiedMemoryStatus() + renderMemoryTreeOverview();
  }
}

function showSparkPanel() {
  const panel = document.getElementById('obsidian-spark-panel');
  if (!panel) return;
  document.getElementById('obsidian-project-planner')?.classList.add('hidden');
  document.getElementById('obsidian-memory-review-panel')?.classList.add('hidden');
  document.getElementById('obsidian-memory-tree-panel')?.classList.add('hidden');
  document.getElementById('obsidian-editor-container')?.classList.add('hidden');
  document.getElementById('obsidian-empty-state')?.classList.add('hidden');
  panel.classList.remove('hidden');
  renderSparkPanel();
  if (!sparkHealth) analyzeSpark();
}

function closeSparkPanel() {
  document.getElementById('obsidian-spark-panel')?.classList.add('hidden');
  if (currentNotePath) {
    document.getElementById('obsidian-editor-container')?.classList.remove('hidden');
  } else {
    document.getElementById('obsidian-empty-state')?.classList.remove('hidden');
  }
}

function setSparkTab(tab) {
  sparkActiveTab = ['health', 'plan', 'query', 'queue', 'canonicals'].includes(tab) ? tab : 'health';
  renderSparkPanel();
  if (sparkActiveTab === 'query' && !sparkQueryStatus && !sparkQueryLoading) {
    loadSparkQueryStatus();
  }
}

function updateSparkApplyState() {
  const applyBtn = document.getElementById('obsidian-spark-apply');
  if (!applyBtn) return;
  applyBtn.disabled = !sparkPlan || sparkSelectedActions.size === 0;
}

function sparkMetric(label, value) {
  return `
    <div class="obsidian-spark-metric">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `;
}

function sparkQuerySubtitle() {
  return 'Lesende Hinweise, Muster, Quellenkontext und Canonicals fuer den aktuellen Scope.';
}

function updateSparkChrome() {
  const subtitle = document.getElementById('obsidian-spark-subtitle');
  if (subtitle) subtitle.textContent = sparkQuerySubtitle();
  const actions = document.getElementById('obsidian-spark-actions');
  if (actions) actions.classList.toggle('hidden', !['health', 'plan'].includes(sparkActiveTab));
}

function renderActivityEventList(session) {
  const events = Array.isArray(session?.debug_events) ? session.debug_events.slice(-10).reverse() : [];
  if (!events.length) return '';
  return `
    <div class="obsidian-spark-card">
      <strong>Letzte Aktivitaet</strong>
      <ol class="obsidian-activity-events">
        ${events.map(event => {
          const bits = [
            event.phase || 'event',
            event.file || '',
            event.message || '',
            event.error ? `Error: ${event.error}` : '',
          ].filter(Boolean).join(' - ');
          return `<li><time>${escapeHtml(event.ts || '')}</time><span>${escapeHtml(bits)}</span></li>`;
        }).join('')}
      </ol>
    </div>
  `;
}

function activityStatusCards() {
  const session = activeProjectPlanSession || projectPlanSessions[0] || null;
  const warnings = [];
  if (memoryTreeError) warnings.push(`Diagnostics: ${memoryTreeError}`);
  if (sparkQueryError) warnings.push(`Insights: ${sparkQueryError}`);
  if (!session && !warnings.length) {
    return renderLensStateCard('activity', 'empty', 'Aktuell gibt es keine neue Aktivitaet.', 'Aktivitaet aktualisieren', 'obsidian-activity-refresh');
  }
  return `
    ${session ? projectSessionProgressHtml() : ''}
    ${warnings.length ? `<div class="obsidian-project-warnings">${warnings.map(item => `<div>${escapeHtml(item)}</div>`).join('')}</div>` : ''}
    ${session ? renderActivityEventList(session) : ''}
    ${!session && warnings.length ? renderLensStateCard('activity', 'default', 'Es liegen nur Fehlerhinweise vor.', 'Aktivitaet aktualisieren', 'obsidian-activity-refresh') : ''}
  `;
}

function renderActivityPanel() {
  const content = document.getElementById('obsidian-activity-content');
  if (!content) return;
  if (activityLoading) {
    content.innerHTML = renderLensStateCard('activity', 'loading', 'Aktivitaet wird geladen.', 'Status pruefen', 'obsidian-activity-loading-action', { disabled: true });
    return;
  }
  if (activityError) {
    content.innerHTML = renderLensStateCard('activity', 'error', 'Die Aktivitaet konnte gerade nicht geladen werden.', 'Erneut laden', 'obsidian-activity-retry', { detail: activityError });
    return;
  }
  content.innerHTML = activityStatusCards();
}

async function refreshActivityPanel() {
  activityLoading = true;
  activityError = '';
  renderActivityPanel();
  try {
    await loadProjectPlanSessions();
  } catch (e) {
    activityError = e.message || 'Activity load failed';
  } finally {
    activityLoading = false;
    renderActivityPanel();
  }
}

function showActivityPanel() {
  const panel = document.getElementById('obsidian-activity-panel');
  if (!panel) return;
  document.getElementById('obsidian-project-planner')?.classList.add('hidden');
  document.getElementById('obsidian-memory-review-panel')?.classList.add('hidden');
  document.getElementById('obsidian-memory-tree-panel')?.classList.add('hidden');
  document.getElementById('obsidian-spark-panel')?.classList.add('hidden');
  document.getElementById('obsidian-editor-container')?.classList.add('hidden');
  document.getElementById('obsidian-empty-state')?.classList.add('hidden');
  panel.classList.remove('hidden');
  renderActivityPanel();
}

function closeActivityPanel() {
  document.getElementById('obsidian-activity-panel')?.classList.add('hidden');
  if (currentNotePath) {
    document.getElementById('obsidian-editor-container')?.classList.remove('hidden');
  } else {
    document.getElementById('obsidian-empty-state')?.classList.remove('hidden');
  }
}

function closeActiveMemorySurface() {
  const memoryPanel = document.getElementById('obsidian-memory-review-panel');
  if (memoryPanel && !memoryPanel.classList.contains('hidden')) {
    closeMemoryReview();
    return;
  }
  const sparkPanel = document.getElementById('obsidian-spark-panel');
  if (sparkPanel && !sparkPanel.classList.contains('hidden')) {
    closeSparkPanel();
    return;
  }
  const activityPanel = document.getElementById('obsidian-activity-panel');
  if (activityPanel && !activityPanel.classList.contains('hidden')) {
    closeActivityPanel();
  }
}

function renderMemoryQuerySurfaces() {
  if (sparkActiveTab === 'query') {
    renderSparkPanel();
  }
  if (memoryWorkspaceTab === 'read') {
    renderMemoryWorkspacePanel();
  }
}

function renderSparkPanel() {
  const content = document.getElementById('obsidian-spark-content');
  if (!content) return;
  updateSparkChrome();
  document.querySelectorAll('[data-spark-tab]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.sparkTab === sparkActiveTab);
  });
  if (sparkActiveTab === 'health') {
    content.innerHTML = renderSparkHealth();
  } else if (sparkActiveTab === 'plan') {
    content.innerHTML = renderSparkPlan();
  } else if (sparkActiveTab === 'query') {
    content.innerHTML = renderSparkQuery();
  } else if (sparkActiveTab === 'queue') {
    content.innerHTML = renderSparkQueue();
  } else {
    content.innerHTML = renderSparkCanonicals();
  }
  bindSparkPanelActions();
  updateSparkApplyState();
}

function renderSparkHealth() {
  if (!sparkHealth) {
    return renderLensStateCard('insights', 'empty', 'Fuer diesen Kontext sind noch keine Insights verfuegbar.', 'Insights aktualisieren', 'obsidian-insights-refresh');
  }
  const h = sparkHealth;
  return `
    <div class="obsidian-spark-metrics">
      ${sparkMetric('Notes', h.total_notes || 0)}
      ${sparkMetric('Tags', h.total_tags || 0)}
      ${sparkMetric('Review queue', h.review_queue_count || 0)}
      ${sparkMetric('Orphans', (h.orphan_notes || []).length)}
      ${sparkMetric('No frontmatter', (h.missing_frontmatter || []).length)}
      ${sparkMetric('Duplicate groups', (h.duplicate_candidates || []).length)}
    </div>
    ${h.truncated ? '<div class="obsidian-project-warnings"><div>Analysis was limited; scope Spark to a folder or tag for deeper cleanup.</div></div>' : ''}
    <div class="obsidian-spark-grid">
      ${sparkList('Largest tags', h.largest_tags || [], item => `#${item.name} (${item.count})`)}
      ${sparkList('Broad tags', h.broad_tags || [], item => `#${item.name} (${item.count})`)}
      ${sparkList('Orphan candidates', h.orphan_notes || [], item => item)}
      ${sparkList('Missing frontmatter', h.missing_frontmatter || [], item => item)}
    </div>
  `;
}

function renderSparkPlan() {
  if (!sparkPlan) {
    return renderLensStateCard('insights', 'empty', 'Fuer diesen Kontext sind noch keine Insights verfuegbar.', 'Insights aktualisieren', 'obsidian-insights-plan-refresh');
  }
  const warnings = sparkPlan.warnings || [];
  const actions = sparkPlan.actions || [];
  return `
    <div class="obsidian-project-summary">
      <strong>${escapeHtml(sparkPlan.summary || 'Spark plan')}</strong>
      <span>${escapeHtml(actions.length)} actions</span>
      <span>${escapeHtml(sparkPlan.scope || 'vault')}</span>
    </div>
    ${warnings.length ? `<div class="obsidian-project-warnings">${warnings.map(item => `<div>${escapeHtml(item)}</div>`).join('')}</div>` : ''}
    <div class="obsidian-spark-actions">
      ${actions.length ? actions.map(action => renderSparkAction(action)).join('') : '<div class="obsidian-project-ok">No cleanup actions needed</div>'}
    </div>
  `;
}

function renderSparkAction(action) {
  const selected = sparkSelectedActions.has(action.id);
  const highRisk = action.risk === 'high';
  const hasOps = action.operations && action.operations.length > 0;
  const disabled = highRisk && !hasOps;
  return `
    <label class="obsidian-spark-action obsidian-spark-risk-${escapeHtml(action.risk || 'low')}">
      <input type="checkbox" data-spark-action-id="${escapeHtml(action.id)}" ${selected ? 'checked' : ''} ${disabled ? 'disabled' : ''}>
      <div>
        <div class="obsidian-spark-action-head">
          <strong>${escapeHtml(action.type || 'action')}</strong>
          <span>${escapeHtml(action.risk || 'low')}</span>
        </div>
        <p>${escapeHtml(action.reason || '')}</p>
        ${action.target_path ? `<code>${escapeHtml(action.target_path)}</code>` : ''}
        ${(action.paths || []).length ? `<small>${escapeHtml(action.paths.slice(0, 5).join(', '))}</small>` : ''}
        ${disabled ? '<small>No automatic operations — review this group manually.</small>' : ''}
        ${highRisk && hasOps ? '<small>⚠ High-risk: content from secondaries will be merged and secondaries deleted.</small>' : ''}
        ${action.preview_markdown ? `<details><summary>📋 Merge preview</summary><pre>${escapeHtml(action.preview_markdown)}</pre></details>` : ''}
      </div>
    </label>
  `;
}

function renderSparkQueue() {
  const queue = flattenNotes(vaultFiles).filter(path => path.startsWith('AI Memory/Review Queue/'));
  return `
    <div class="obsidian-project-summary">
      <strong>Review Queue</strong>
      <span>${escapeHtml(queue.length)} notes</span>
    </div>
    <div class="obsidian-spark-list">
      ${queue.length ? queue.map(path => `<button type="button" data-spark-open-path="${escapeHtml(path)}">${escapeHtml(path)}</button>`).join('') : renderLensStateCard('maintain', 'empty', 'Aktuell gibt es nichts zu pflegen.', 'Dokument pruefen', 'obsidian-memory-maintain-empty')}
    </div>
  `;
}

function renderSparkQuery() {
  const status = sparkQueryStatus;
  const result = sparkQueryResult;
  const warnings = [
    ...((status && status.warnings) || []),
    ...((result && result.warnings) || []),
    ...(sparkQueryError ? [sparkQueryError] : []),
  ];
  const readiness = result?.readiness || status?.readiness || {};
  const summary = result?.summary || status?.summary || {};
  const citations = (result && result.citations) || [];
  const readinessState = readabilityLabel(readiness.state || 'not_loaded');
  const gate = readabilityLabel(result?.readiness_gate || status?.readiness_gate || 'unknown');
  const confidence = result
    ? `${readabilityLabel(result.confidence || 'unknown')} (${Math.round((Number(result.confidence_score) || 0) * 100)}%)`
    : 'Not asked yet';
  const answerMode = result ? sparkAnswerModeLabel(result.answer_mode || 'extractive') : 'Not asked yet';
  return `
    <form class="obsidian-spark-query-bar" data-spark-query-form>
      <input
        type="text"
        data-spark-query-input
        value="${escapeHtml(sparkQueryText)}"
        placeholder="Ask a memory question backed by indexed sources"
      >
      <button type="submit" class="btn btn-primary" ${sparkQueryLoading ? 'disabled' : ''}>Ask</button>
      <button type="button" class="btn btn-secondary" data-spark-query-refresh ${sparkQueryLoading ? 'disabled' : ''}>Refresh status</button>
    </form>
    <div class="obsidian-spark-metrics">
      ${sparkMetric('Mode', answerMode)}
      ${sparkMetric('Readiness', readinessState)}
      ${sparkMetric('Gate', gate)}
      ${sparkMetric('Confidence', confidence)}
      ${sparkMetric('Sources', result ? (summary.matched_sources || 0) : (summary.source_count || 0))}
      ${sparkMetric('Chunks', result ? (summary.matched_chunks || 0) : (summary.chunk_count || 0))}
    </div>
    ${warnings.length ? `<div class="obsidian-project-warnings">${warnings.map(item => `<div>${escapeHtml(item)}</div>`).join('')}</div>` : ''}
    ${renderSparkQueryStatusCard(readiness, summary)}
    ${sparkQueryLoading ? '<div class="obsidian-project-loading">Querying derived memory...</div>' : ''}
    ${result ? renderSparkQueryAnswer(result, citations) : '<div class="obsidian-spark-card"><strong>Answer card</strong><p>Ask a question to inspect grounded answer text, citations, confidence, and uncertainty.</p></div>'}
  `;
}

function renderSparkQueryStatusCard(readiness, summary) {
  const gaps = Array.isArray(readiness.gaps) ? readiness.gaps : [];
  return `
    <div class="obsidian-spark-card">
      <strong>Lens status</strong>
      <p>The answer lens only reflects derived memory. It does not rewrite source notes and may stay blocked or stale until rebuildable index work has completed.</p>
      ${gaps.length ? `<ul>${gaps.map(item => `<li>${escapeHtml(readabilityLabel(item))}</li>`).join('')}</ul>` : '<p>No readiness gaps reported.</p>'}
      ${(summary && Array.isArray(summary.warnings) && summary.warnings.length)
        ? `<small>${escapeHtml(summary.warnings[0])}</small>`
        : ''}
    </div>
  `;
}

function renderSparkQueryAnswer(result, citations) {
  const answer = result.answer || '';
  const uncertainty = [];
  const modelWarnings = Array.isArray(result.model_capability_warnings) ? result.model_capability_warnings : [];
  if ((result.confidence || 'low') === 'low') uncertainty.push('Confidence is low; treat this as a hint and inspect the cited source notes.');
  if (!citations.length) uncertainty.push('No citations were returned for this answer.');
  if (String(result.answer_mode || 'extractive') === 'extractive') {
    uncertainty.push('Running in safe grounded extractive mode without model synthesis.');
  }
  return `
    <div class="obsidian-spark-grid">
      <div class="obsidian-spark-card obsidian-spark-answer-card">
        <strong>Answer</strong>
        <div class="obsidian-spark-answer-copy">${escapeHtml(answer || 'No grounded answer available yet.')}</div>
        <div class="obsidian-spark-answer-meta">
          <span>${escapeHtml(readabilityLabel(result.confidence || 'unknown'))}</span>
          <span>${escapeHtml(`${Math.round((Number(result.confidence_score) || 0) * 100)}% confidence score`)}</span>
          <span>${escapeHtml(readabilityLabel(result.readiness_gate || 'unknown'))}</span>
        </div>
        ${uncertainty.length ? `<div class="obsidian-project-warnings">${uncertainty.map(item => `<div>${escapeHtml(item)}</div>`).join('')}</div>` : ''}
      </div>
      <div class="obsidian-spark-card">
        <strong>Answer mode</strong>
        <div class="obsidian-spark-answer-meta">
          <span>${escapeHtml(sparkAnswerModeLabel(result.answer_mode || 'extractive'))}</span>
          <span>${escapeHtml(`role ${result.selected_role || 'memory.answer'}`)}</span>
          <span>${escapeHtml(`model ${result.selected_model || 'extractive'}`)}</span>
          <span>${escapeHtml(`endpoint ${result.selected_endpoint_id || 'none'}`)}</span>
          <span>${escapeHtml(`context ${sparkContextTokensLabel(result.model_context_tokens)}`)}</span>
        </div>
        <small>${escapeHtml(sparkFallbackLabel(result.fallback_reason || ''))}</small>
        ${modelWarnings.length ? `<div class="obsidian-project-warnings" data-spark-model-warnings="true">${modelWarnings.map(item => `<div>${escapeHtml(readabilityLabel(item))}</div>`).join('')}</div>` : ''}
      </div>
      <div class="obsidian-spark-card">
        <strong>Provenance breadcrumb</strong>
        <p>answer -> citation snippet -> source note -> optional graph jump</p>
        <small>Use citations to verify the answer against source-facing Lens surfaces.</small>
      </div>
    </div>
    <div class="obsidian-spark-citations">
      ${citations.length ? citations.map(citation => renderSparkCitation(citation)).join('') : '<div class="obsidian-project-conflicts">No supporting citations were returned.</div>'}
    </div>
  `;
}

function renderSparkCitation(citation) {
  const snippets = Array.isArray(citation.snippets) ? citation.snippets : [];
  return `
    <div class="obsidian-spark-card obsidian-spark-citation-card">
      <div class="obsidian-spark-citation-head">
        <div>
          <strong>${escapeHtml(citation.title || citation.path || 'Source')}</strong>
          <small>${escapeHtml(citation.path || '')}</small>
        </div>
        <span>${escapeHtml(`score ${citation.score || 0}`)}</span>
      </div>
      ${snippets.length ? `<div class="obsidian-spark-citation-snippets">${snippets.map(snippet => `<blockquote>${escapeHtml(snippet)}</blockquote>`).join('')}</div>` : '<p>No snippet excerpt returned.</p>'}
      <div class="obsidian-spark-citation-actions">
        <button type="button" class="btn btn-secondary" data-spark-query-open-path="${escapeHtml(citation.path || '')}">Open source</button>
        <button type="button" class="btn btn-secondary" data-spark-query-graph-path="${escapeHtml(citation.path || '')}">Graph jump</button>
      </div>
    </div>
  `;
}

function renderSparkCanonicals() {
  const canonicals = [
    'AI Memory/Canonical/User Preferences.md',
    'AI Memory/Canonical/Odysseus Architecture.md',
    'AI Memory/Canonical/Obsidian MCP Rules.md',
    'AI Memory/Canonical/Open Decisions.md',
    'AI Memory/02 Entscheidungen.md',
    'AI Memory/03 Offene Fragen.md',
  ];
  const notes = flattenNotes(vaultFiles);
  return `
    <div class="obsidian-spark-list">
      ${canonicals.map(path => {
        const exists = notes.includes(path);
        return `<button type="button" data-spark-open-path="${escapeHtml(path)}" ${exists ? '' : 'disabled'}>
          <span>${escapeHtml(path)}</span>
          <small>${exists ? 'available' : 'missing'}</small>
        </button>`;
      }).join('')}
    </div>
  `;
}

function sparkList(title, items, renderItem) {
  return `
    <div class="obsidian-spark-card">
      <strong>${escapeHtml(title)}</strong>
      ${items.length ? `<ul>${items.slice(0, 12).map(item => `<li>${escapeHtml(renderItem(item))}</li>`).join('')}</ul>` : '<p>None</p>'}
    </div>
  `;
}

function bindSparkPanelActions() {
  document.querySelectorAll('[data-spark-action-id]').forEach(input => {
    input.addEventListener('change', () => {
      if (input.checked) sparkSelectedActions.add(input.dataset.sparkActionId);
      else sparkSelectedActions.delete(input.dataset.sparkActionId);
      updateSparkApplyState();
    });
  });
  document.querySelectorAll('[data-spark-open-path]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const path = btn.dataset.sparkOpenPath;
      if (!path || btn.disabled) return;
      closeActiveMemorySurface();
      await openNote(path);
    });
  });
  document.querySelectorAll('[data-spark-query-open-path]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const path = btn.dataset.sparkQueryOpenPath;
      if (!path || btn.disabled) return;
      closeActiveMemorySurface();
      await openNote(path);
    });
  });
  document.querySelectorAll('[data-spark-query-graph-path]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const path = btn.dataset.sparkQueryGraphPath;
      if (!path || btn.disabled) return;
      closeActiveMemorySurface();
      await openNote(path);
      setViewMode('graph');
    });
  });
  document.querySelectorAll('[data-spark-query-refresh]').forEach(btn => {
    btn.addEventListener('click', () => {
      loadSparkQueryStatus();
    });
  });
  document.querySelectorAll('[data-spark-query-form]').forEach(form => {
    form.addEventListener('submit', event => {
      event.preventDefault();
      runSparkQuery();
    });
  });
  document.querySelectorAll('[data-spark-query-input]').forEach(input => {
    input.addEventListener('input', () => {
      sparkQueryText = input.value || '';
    });
  });
}

function readabilityLabel(value) {
  return String(value || '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, match => match.toUpperCase());
}

function sparkAnswerModeLabel(value) {
  switch (String(value || '').trim().toLowerCase()) {
    case 'cloud':
      return 'Cloud-Antwort genutzt';
    case 'local':
      return 'Lokales Modell genutzt';
    case 'extractive':
      return 'Sicherer belegter Lesemodus aktiv';
    case 'auto':
      return 'Automatische Moduswahl';
    default:
      return readabilityLabel(value || 'unknown');
  }
}

function sparkFallbackLabel(value) {
  if (!value) return 'Kein Fallback noetig.';
  return `Fallback-Grund: ${readabilityLabel(value)}`;
}

function sparkContextTokensLabel(value) {
  const tokens = Number(value) || 0;
  return tokens > 0 ? `${tokens} tokens` : 'not reported';
}

async function loadSparkQueryStatus() {
  sparkQueryLoading = true;
  sparkQueryError = '';
  renderMemoryQuerySurfaces();
  try {
    sparkQueryStatus = await fetchMemoryDashboardJson('/api/plugins/obsidian/memory/query/status');
    if (!sparkQueryStatus?.readiness?.ready) sparkQueryResult = null;
  } catch (e) {
    console.error('Spark query status failed:', e);
    sparkQueryError = e.message || 'Query status failed';
    sparkQueryResult = null;
  } finally {
    sparkQueryLoading = false;
    renderMemoryQuerySurfaces();
  }
}

async function runSparkQuery() {
  const query = String(sparkQueryText || '').trim();
  if (!query) {
    showToast('Enter a memory question first');
    return;
  }
  sparkQueryLoading = true;
  sparkQueryError = '';
  renderMemoryQuerySurfaces();
  try {
    sparkQueryStatus = sparkQueryStatus || await fetchMemoryDashboardJson('/api/plugins/obsidian/memory/query/status');
    sparkQueryResult = await fetchMemoryDashboardJson(`/api/plugins/obsidian/memory/query?q=${encodeURIComponent(query)}&top_k=5`);
  } catch (e) {
    console.error('Spark query failed:', e);
    sparkQueryError = e.message || 'Query failed';
    sparkQueryResult = null;
  } finally {
    sparkQueryLoading = false;
    renderMemoryQuerySurfaces();
  }
}

async function analyzeSpark() {
  const content = document.getElementById('obsidian-spark-content');
  if (content) content.innerHTML = renderLensStateCard('insights', 'loading', 'Insights werden geladen.', 'Ladevorgang ansehen', 'obsidian-insights-loading-action', { disabled: true });
  try {
    const res = await fetch('/api/plugins/obsidian/spark/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: 'vault', limit: 5000 }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Spark analysis failed');
    sparkHealth = await res.json();
    sparkActiveTab = 'health';
    renderSparkPanel();
  } catch (e) {
    console.error('Spark analysis failed:', e);
    if (content) content.innerHTML = renderLensStateCard('insights', 'error', 'Die Insights konnten gerade nicht geladen werden.', 'Erneut laden', 'obsidian-insights-retry', { detail: e.message || 'Spark analysis failed' });
  }
}

async function createSparkPlan() {
  const content = document.getElementById('obsidian-spark-content');
  if (content) content.innerHTML = renderLensStateCard('insights', 'loading', 'Insights werden geladen.', 'Ladevorgang ansehen', 'obsidian-insights-plan-loading-action', { disabled: true });
  try {
    const res = await fetch('/api/plugins/obsidian/spark/plan', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scope: 'vault', limit: 5000 }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Spark plan failed');
    sparkPlan = await res.json();
    sparkHealth = sparkPlan.health || sparkHealth;
    sparkSelectedActions = new Set();
    sparkActiveTab = 'plan';
    renderSparkPanel();
  } catch (e) {
    console.error('Spark plan failed:', e);
    if (content) content.innerHTML = renderLensStateCard('insights', 'error', 'Die Insights konnten gerade nicht geladen werden.', 'Erneut laden', 'obsidian-insights-plan-retry', { detail: e.message || 'Spark plan failed' });
  }
}

async function applySparkPlan() {
  if (!sparkPlan || sparkSelectedActions.size === 0) return;
  const ok = await styledConfirm('Apply selected Spark actions to the vault?', { confirmText: 'Apply' });
  if (!ok) return;
  const applyBtn = document.getElementById('obsidian-spark-apply');
  if (applyBtn) applyBtn.disabled = true;
  try {
    const res = await fetch('/api/plugins/obsidian/spark/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        plan: sparkPlan,
        confirm: true,
        selected_action_ids: Array.from(sparkSelectedActions),
      }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Spark apply failed');
    const result = await res.json();
    showToast(`Applied ${result.applied_actions?.length || 0} Spark actions`);
    sparkSelectedActions = new Set();
    await loadVaultFiles();
    await createSparkPlan();
  } catch (e) {
    console.error('Spark apply failed:', e);
    showToast(e.message || 'Spark apply failed');
    updateSparkApplyState();
  }
}

function renderProjectPlanPreviewLegacy(plan) {
  const panel = document.getElementById('obsidian-project-preview-panel');
  if (!panel) return;
  const conflicts = plan.conflicts || [];
  const warnings = plan.warnings || [];
  const files = plan.files || [];
  const tags = plan.new_tags || [];
  const relationships = plan.relationships || [];
  panel.innerHTML = `
    ${projectSessionProgressHtml()}
    <div class="obsidian-project-summary">
      <strong>${escapeHtml(plan.project?.title || 'Project')}</strong>
      <span>${escapeHtml(files.length)} files</span>
      <span>${escapeHtml(relationships.length)} relationships</span>
    </div>
    ${conflicts.length ? `<div class="obsidian-project-conflicts" data-project-conflicts="true">
      <strong>Conflicts</strong>
      ${conflicts.map(item => `<div>${escapeHtml(item.path)} - ${escapeHtml(item.reason)}</div>`).join('')}
    </div>` : '<div class="obsidian-project-ok">No file conflicts</div>'}
    ${warnings.length ? `<div class="obsidian-project-warnings">
      ${warnings.map(item => `<div>${escapeHtml(item)}</div>`).join('')}
    </div>` : ''}
    <div class="obsidian-project-files">
      ${files.map(file => `
        <div class="obsidian-project-file" data-project-file="${escapeHtml(file.path)}">
          <div>
            <strong>${escapeHtml(file.path)}</strong>
            <span>${escapeHtml(file.type)} · ${escapeHtml(file.status)}</span>
          </div>
          <div class="obsidian-project-tags">${(file.tags || []).map(tag => `<code>${escapeHtml(tag)}</code>`).join('')}</div>
          <div class="obsidian-project-links">${(file.links || []).slice(0, 5).map(link => `<span>${escapeHtml(link)}</span>`).join('')}</div>
        </div>
      `).join('')}
    </div>
    ${tags.length ? `<div class="obsidian-project-new-tags">
      <strong>New tags</strong>
      ${tags.map(item => `<div><code>${escapeHtml(item.tag)}</code> ${escapeHtml(item.reason)}</div>`).join('')}
    </div>` : ''}
  `;
}

function projectFileGenerationState(file) {
  const explicit = String(file?.__generationState || '').toLowerCase();
  if (['done', 'wip', 'open'].includes(explicit)) return explicit;
  const status = String(file?.status || '').toLowerCase();
  if (['writing', 'in_progress', 'work_in_progress', 'work in progress', 'wip'].includes(status)) return 'wip';
  if (projectPlanPreviewStreaming) return 'open';
  if (file?.content || file?.content_preview) return 'done';
  return 'open';
}

function projectFileGenerationLabel(state) {
  if (state === 'done') return 'Done';
  if (state === 'wip') return 'Work in Progress';
  return 'Open';
}

function renderProjectPlanPreview(plan) {
  const panel = document.getElementById('obsidian-project-preview-panel');
  if (!panel) return;
  const conflicts = plan.conflicts || [];
  const warnings = plan.warnings || [];
  const files = plan.files || [];
  const tags = plan.new_tags || [];
  const relationships = plan.relationships || [];
  panel.innerHTML = `
    ${projectSessionProgressHtml()}
    ${projectSessionDebugHtml()}
    <div class="obsidian-project-summary">
      <strong>${escapeHtml(plan.project?.title || 'Project')}</strong>
      <span>${escapeHtml(files.length)} files</span>
      <span>${escapeHtml(relationships.length)} relationships</span>
    </div>
    ${conflicts.length ? `<div class="obsidian-project-conflicts" data-project-conflicts="true">
      <strong>Conflicts</strong>
      ${conflicts.map(item => `<div>${escapeHtml(item.path)} - ${escapeHtml(item.reason)}</div>`).join('')}
    </div>` : '<div class="obsidian-project-ok">No file conflicts</div>'}
    ${warnings.length ? `<div class="obsidian-project-warnings">
      ${warnings.map(item => `<div>${escapeHtml(item)}</div>`).join('')}
    </div>` : ''}
    ${relationships.length ? `<div class="obsidian-project-relationships">
      <strong>External link suggestions</strong>
      ${relationships.map((relationship, index) => `
        <label class="obsidian-project-relationship">
          <input type="checkbox" data-project-relationship-index="${index}" ${relationship.disabled ? '' : 'checked'}>
          <span>${escapeHtml(relationship.source)} -> ${escapeHtml(relationship.target)}</span>
          <small>${escapeHtml(relationship.reason || relationship.type || 'relates to')}</small>
        </label>
      `).join('')}
    </div>` : ''}
    <div class="obsidian-project-files">
      ${files.map((file, index) => {
        const generationState = projectFileGenerationState(file);
        return `
        <details class="obsidian-project-file obsidian-project-file-editor obsidian-project-file-${generationState}" data-project-file="${escapeHtml(file.path)}" data-project-index="${index}" data-project-generation-state="${generationState}">
          <summary>
            <strong>${escapeHtml(file.path)}</strong>
            <span class="obsidian-project-file-meta">
              <span>${escapeHtml(file.type)} - ${escapeHtml(file.status)}</span>
              <span class="obsidian-project-file-state obsidian-project-file-state-${generationState}">${projectFileGenerationLabel(generationState)}</span>
            </span>
          </summary>
          <div class="obsidian-project-file-grid">
            <label>Path<input data-project-field="path" value="${escapeHtml(file.path)}"></label>
            <label>Title<input data-project-field="title" value="${escapeHtml(file.title || '')}"></label>
            <label>Type<input data-project-field="type" value="${escapeHtml(file.type || '')}"></label>
            <label>Status<input data-project-field="status" value="${escapeHtml(file.status || 'draft')}"></label>
          </div>
          <label class="obsidian-project-wide-field">Outline<textarea data-project-field="outline">${escapeHtml((file.outline || []).join('\n'))}</textarea></label>
          <label class="obsidian-project-wide-field">Links<textarea data-project-field="links">${escapeHtml((file.links || []).join('\n'))}</textarea></label>
          <label class="obsidian-project-wide-field">Markdown<textarea data-project-field="content">${escapeHtml(file.content || file.content_preview || '')}</textarea></label>
          <div class="obsidian-project-tags">${(file.tags || []).map(tag => `<code>${escapeHtml(tag)}</code>`).join('')}</div>
        </details>
      `;
      }).join('')}
    </div>
    ${tags.length ? `<div class="obsidian-project-new-tags">
      <strong>New tags</strong>
      ${tags.map(item => `<div><code>${escapeHtml(item.tag)}</code> ${escapeHtml(item.reason)}</div>`).join('')}
    </div>` : ''}
  `;
  bindProjectSessionActions();
  updateProjectApplyState();
}

function syncProjectPlanPreviewEdits() {
  if (!projectPlanPreview) return;
  document.querySelectorAll('[data-project-index]').forEach(editor => {
    const index = Number(editor.dataset.projectIndex);
    const file = projectPlanPreview.files?.[index];
    if (!file) return;
    const previousPath = file.path;
    editor.querySelectorAll('[data-project-field]').forEach(field => {
      const key = field.dataset.projectField;
      const value = field.value || '';
      if (key === 'outline' || key === 'links') {
        file[key] = splitProjectList(value);
      } else {
        file[key] = value.trim();
      }
    });
    if (file.frontmatter) {
      file.frontmatter.type = file.type;
      file.frontmatter.status = file.status;
    }
    if (previousPath && file.path && previousPath !== file.path) {
      (projectPlanPreview.relationships || []).forEach(relationship => {
        if (relationship.source === previousPath) relationship.source = file.path;
        if (relationship.target === previousPath) relationship.target = file.path;
      });
    }
    file.content_preview = String(file.content || '').split('\n').filter(Boolean).slice(0, 8).join('\n');
  });
  const checkedRelationships = new Set(
    [...document.querySelectorAll('[data-project-relationship-index]:checked')]
      .map(input => Number(input.dataset.projectRelationshipIndex))
  );
  projectPlanPreview.relationships = (projectPlanPreview.relationships || [])
    .filter((relationship, index) => !relationship.suggested || checkedRelationships.has(index));
}

function renderMemoryReviewPreview(plan) {
  const panel = document.getElementById('obsidian-memory-preview-panel');
  const applyBtn = document.getElementById('obsidian-memory-apply');
  if (!panel || !applyBtn) return;
  const conflicts = plan.conflicts || [];
  const warnings = plan.warnings || [];
  const files = plan.files || [];
  const tags = plan.new_tags || [];
  const notes = plan.suggested_notes || [];
  const duplicates = plan.duplicate_candidates || [];
  const relationships = plan.relationships || [];
  const firstFile = files[0] || null;
  const titleWasProvided = Boolean((plan.candidate?.title || '').trim());
  const destination = firstFile?.path
    || (plan.action === 'review_queue' ? 'AI Memory/Review Queue' : '')
    || (plan.action === 'memory_only' ? 'Odysseus memory only' : '')
    || (plan.action === 'discard' ? 'No vault destination' : '')
    || plan.target_note
    || plan.target_folder
    || 'Vault root';
  panel.innerHTML = `
    <div class="obsidian-memory-preview-summary">
      <div>
        <span>Action</span>
        <strong>${escapeHtml(memoryReviewActionLabel(plan.action))}</strong>
      </div>
      <div>
        <span>Destination</span>
        <strong>${escapeHtml(destination)}</strong>
      </div>
      <div>
        <span>Title source</span>
        <strong>${titleWasProvided ? 'Typed title' : 'Generated from insight'}</strong>
      </div>
      <div>
        <span>Relationships</span>
        <strong>${escapeHtml(relationships.length)}</strong>
      </div>
      <div>
        <span>Duplicates</span>
        <strong>${escapeHtml(duplicates.length)}</strong>
      </div>
    </div>
    ${conflicts.length ? `<div class="obsidian-project-conflicts" data-memory-conflicts="true">
      <strong>Conflicts</strong>
      ${conflicts.map(item => `<div>${escapeHtml(item.path)} - ${escapeHtml(item.reason)}</div>`).join('')}
    </div>` : '<div class="obsidian-project-ok">No file conflicts</div>'}
    ${warnings.length ? `<div class="obsidian-project-warnings">
      ${warnings.map(item => `<div>${escapeHtml(item)}</div>`).join('')}
    </div>` : ''}
    <div class="obsidian-project-files">
      ${files.map(file => `
        <div class="obsidian-project-file" data-memory-file="${escapeHtml(file.path)}">
          <div>
            <strong>${escapeHtml(file.path)}</strong>
            <span>${escapeHtml(file.mode)} · ${escapeHtml(file.type)} · ${escapeHtml(file.status)}</span>
          </div>
          <div class="obsidian-project-tags">${(file.tags || []).map(tag => `<code>${escapeHtml(tag)}</code>`).join('')}</div>
          <div class="obsidian-project-links">${(file.links || []).slice(0, 5).map(link => `<span>${escapeHtml(link)}</span>`).join('')}</div>
          <details class="obsidian-memory-markdown-preview" open>
            <summary>Generated markdown</summary>
            <pre>${escapeHtml(file.content || file.content_preview || '')}</pre>
          </details>
        </div>
      `).join('')}
    </div>
    ${notes.length ? `<div class="obsidian-project-new-tags">
      <strong>Suggested notes</strong>
      ${notes.map(item => `<div><code>${escapeHtml(item.path)}</code> ${escapeHtml(item.reason)}</div>`).join('')}
    </div>` : ''}
    ${duplicates.length ? `<div class="obsidian-project-warnings" data-memory-duplicates="true">
      <strong>Possible duplicates</strong>
      ${duplicates.map(item => `<div><code>${escapeHtml(item.path)}</code> ${escapeHtml(item.reason)}</div>`).join('')}
    </div>` : ''}
    ${tags.length ? `<div class="obsidian-project-new-tags">
      <strong>New tags</strong>
      ${tags.map(item => `<div><code>${escapeHtml(item.tag)}</code> ${escapeHtml(item.reason)}</div>`).join('')}
    </div>` : ''}
  `;
  applyBtn.disabled = conflicts.length > 0;
}

function renderGameDevDraftPanel(draftPayload) {
  const panel = document.getElementById('obsidian-project-gamedev-draft');
  if (!panel) return;
  const warnings = draftPayload.warnings || [];
  panel.classList.remove('hidden');
  panel.innerHTML = `
    <div class="obsidian-project-summary">
      <strong>GameDev concept draft</strong>
      <span>Edit and approve this concept before creating the plan.</span>
    </div>
    ${warnings.length ? `<div class="obsidian-project-warnings">
      <strong>Draft warnings</strong>
      <ul>${warnings.map(warning => `<li>${escapeHtml(warning)}</li>`).join('')}</ul>
    </div>` : ''}
    <textarea id="obsidian-gamedev-draft-text" class="obsidian-gamedev-draft-text">${escapeHtml(draftPayload.draft || '')}</textarea>
    <div class="obsidian-project-actions">
      <button type="button" id="obsidian-gamedev-regenerate" class="btn btn-secondary">Regenerate draft</button>
      <button type="button" id="obsidian-gamedev-approve" class="btn btn-primary">Approve & create plan</button>
    </div>
  `;
  document.getElementById('obsidian-gamedev-regenerate')?.addEventListener('click', createGameDevDraft);
  document.getElementById('obsidian-gamedev-approve')?.addEventListener('click', async () => {
    const approvedConcept = document.getElementById('obsidian-gamedev-draft-text')?.value || '';
    if (!approvedConcept.trim()) {
      showToast('GameDev concept draft required');
      return;
    }
    await previewProjectPlan({ conceptApproved: true, approvedConcept });
  });
}

async function createGameDevDraft() {
  const title = document.getElementById('obsidian-project-title')?.value || '';
  const kind = document.getElementById('obsidian-project-kind')?.value || 'game_dev';
  const description = document.getElementById('obsidian-project-description')?.value || '';
  const custom_focus = document.getElementById('obsidian-project-focus')?.value || '';
  if (!title.trim()) {
    showToast('Project title required');
    return;
  }
  if (!description.trim()) {
    showToast('Game idea required');
    return;
  }
  const panel = document.getElementById('obsidian-project-gamedev-draft');
  const previewBtn = document.getElementById('obsidian-project-preview');
  invalidateProjectPlanPreview({ clearDraft: false });
  if (panel) {
    panel.classList.remove('hidden');
    panel.innerHTML = '<div class="obsidian-project-loading">Creating editable GameDev concept draft...</div>';
  }
  if (previewBtn) previewBtn.disabled = true;
  try {
    const res = await fetch('/api/plugins/obsidian/project-plan/gamedev-draft', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, kind, description, custom_focus }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'GameDev draft failed');
    gameDevConceptDraft = await res.json();
    renderGameDevDraftPanel(gameDevConceptDraft);
  } catch (e) {
    console.error('GameDev draft failed:', e);
    if (panel) panel.innerHTML = `<div class="obsidian-project-conflicts">${escapeHtml(e.message || 'GameDev draft failed')}</div>`;
  } finally {
    if (previewBtn) previewBtn.disabled = false;
  }
}

function startProjectPlanLoadingAnimation(panel) {
  if (!panel) return () => {};
  const baseText = 'Creating plan and writing AI content sequentially';
  let dots = 0;
  panel.innerHTML = '<div class="obsidian-project-loading"><span data-project-loading-text></span></div>';
  const render = () => {
    const target = panel.querySelector('[data-project-loading-text]');
    if (target) target.textContent = `${baseText}${'.'.repeat(dots)}`;
    dots = (dots + 1) % 4;
  };
  render();
  const timer = window.setInterval(render, 450);
  return () => window.clearInterval(timer);
}

function startProjectDescriptionImproveLoading(textarea) {
  const wrap = textarea?.closest('.obsidian-project-description-wrap');
  if (!wrap) return () => {};

  wrap.querySelector('.obsidian-project-description-loading')?.remove();
  const spinner = createWhirlpool(26);
  const overlay = document.createElement('div');
  overlay.className = 'obsidian-project-description-loading';
  overlay.setAttribute('aria-label', 'Improving project prompt');
  overlay.appendChild(spinner.element);
  wrap.appendChild(overlay);

  const wasReadOnly = textarea.readOnly;
  wrap.classList.add('is-ai-loading');
  textarea.readOnly = true;
  textarea.setAttribute('aria-busy', 'true');

  return () => {
    spinner.destroy();
    overlay.remove();
    wrap.classList.remove('is-ai-loading');
    textarea.readOnly = wasReadOnly;
    textarea.removeAttribute('aria-busy');
  };
}

function setProjectPlanFile(index, file) {
  if (!projectPlanPreview || !file) return;
  if (!Array.isArray(projectPlanPreview.files)) projectPlanPreview.files = [];
  if (Number.isInteger(index) && index >= 0 && index < projectPlanPreview.files.length) {
    projectPlanPreview.files[index] = file;
    return;
  }
  const existingIndex = projectPlanPreview.files.findIndex(item => item.path === file.path);
  if (existingIndex >= 0) projectPlanPreview.files[existingIndex] = file;
}

function handleProjectPlanStreamEvent(eventName, payload) {
  if (eventName === 'session_updated' && payload?.session) {
    setActiveProjectPlanSession(payload.session);
    if (payload.session.plan) {
      projectPlanPreview = payload.session.plan;
      renderProjectPlanPreview(projectPlanPreview);
    } else {
      renderProjectSessionDraft();
    }
    return;
  }
  if (eventName === 'plan_started' && payload?.plan) {
    projectPlanPreview = payload.plan;
    renderProjectPlanPreview(projectPlanPreview);
    return;
  }
  if (eventName === 'file_started') {
    const index = Number(payload?.index);
    const file = projectPlanPreview?.files?.[index];
    if (file) {
      file.status = 'writing';
      file.__generationState = 'wip';
      renderProjectPlanPreview(projectPlanPreview);
    }
    return;
  }
  if (eventName === 'file_done') {
    const file = payload?.file;
    if (file) file.__generationState = 'done';
    setProjectPlanFile(Number(payload?.index), file);
    renderProjectPlanPreview(projectPlanPreview);
    return;
  }
  if (eventName === 'warning' && payload?.message) {
    if (projectPlanPreview) {
      projectPlanPreview.warnings = Array.from(new Set([...(projectPlanPreview.warnings || []), payload.message]));
      renderProjectPlanPreview(projectPlanPreview);
    }
    return;
  }
  if (eventName === 'plan_done' && payload?.plan) {
    projectPlanPreview = payload.plan;
    projectPlanPreviewStreaming = false;
    renderProjectPlanPreview(projectPlanPreview);
    return;
  }
  if (eventName === 'error') {
    throw new Error(payload?.detail || 'Project preview failed');
  }
}

function parseProjectPlanSseBlock(block) {
  let eventName = 'message';
  const dataLines = [];
  block.split(/\r?\n/).forEach(line => {
    if (line.startsWith('event:')) eventName = line.slice(6).trim() || 'message';
    if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart());
  });
  if (!dataLines.length) return null;
  return { eventName, payload: JSON.parse(dataLines.join('\n')) };
}

async function previewProjectPlanFallback(payload) {
  const res = await fetch('/api/plugins/obsidian/project-plan/preview', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error((await res.json()).detail || 'Project preview failed');
  projectPlanPreview = await res.json();
  projectPlanPreviewStreaming = false;
  renderProjectPlanPreview(projectPlanPreview);
}

async function ensureProjectPlanSession(payload) {
  if (activeProjectPlanSessionId) return activeProjectPlanSessionId;
  const res = await fetch('/api/plugins/obsidian/project-plan/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ request: payload }),
  });
  if (!res.ok) throw new Error((await res.json()).detail || 'Failed to create planning session');
  const session = await res.json();
  setActiveProjectPlanSession(session);
  await loadProjectPlanSessions();
  renderFileTree();
  renderProjectSessionDraft();
  return session.id;
}

async function previewProjectPlanStream(payload, sessionId) {
  const url = sessionId
    ? `/api/plugins/obsidian/project-plan/sessions/${encodeURIComponent(sessionId)}/preview-stream`
    : '/api/plugins/obsidian/project-plan/preview-stream';
  const body = sessionId ? { request: payload } : payload;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error((await res.json()).detail || 'Project preview failed');
  if (!res.body || !res.body.getReader) {
    await previewProjectPlanFallback(payload);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() || '';
    for (const block of blocks) {
      if (!block.trim()) continue;
      const event = parseProjectPlanSseBlock(block);
      if (event) handleProjectPlanStreamEvent(event.eventName, event.payload);
    }
    if (done) break;
  }
  if (buffer.trim()) {
    const event = parseProjectPlanSseBlock(buffer);
    if (event) handleProjectPlanStreamEvent(event.eventName, event.payload);
  }
}

async function previewProjectPlan({ conceptApproved = false, approvedConcept = '' } = {}) {
  const payload = projectPlanRequestFromForm({ conceptApproved, approvedConcept });
  const title = payload.title;
  const kind = payload.kind;
  if (!title.trim()) {
    showToast('Project title required');
    return;
  }
  if (isGameDevProjectKind(kind) && !conceptApproved) {
    await createGameDevDraft();
    return;
  }
  const panel = document.getElementById('obsidian-project-preview-panel');
  const previewBtn = document.getElementById('obsidian-project-preview');
  const stopLoadingAnimation = startProjectPlanLoadingAnimation(panel);
  if (previewBtn) previewBtn.disabled = true;
  projectPlanPreviewStreaming = true;
  try {
    const sessionId = await ensureProjectPlanSession(payload);
    await previewProjectPlanStream(payload, sessionId);
  } catch (e) {
    console.error('Project preview failed:', e);
    projectPlanPreview = null;
    projectPlanPreviewStreaming = false;
    if (panel) panel.innerHTML = `<div class="obsidian-project-conflicts">${escapeHtml(e.message || 'Project preview failed')}</div>`;
  } finally {
    stopLoadingAnimation();
    projectPlanPreviewStreaming = false;
    if (projectPlanPreview) renderProjectPlanPreview(projectPlanPreview);
    if (previewBtn) previewBtn.disabled = false;
    updateProjectApplyState();
  }
}

async function improveProjectDescription() {
  const title = document.getElementById('obsidian-project-title')?.value || '';
  const kind = document.getElementById('obsidian-project-kind')?.value || 'software';
  const textarea = document.getElementById('obsidian-project-description');
  const description = textarea?.value || '';
  const custom_focus = document.getElementById('obsidian-project-focus')?.value || '';
  const btn = document.getElementById('obsidian-project-improve-description');
  if (!textarea) return;
  if (!description.trim()) {
    showToast('Project context required');
    return;
  }
  const stopLoadingAnimation = startProjectDescriptionImproveLoading(textarea);
  try {
    if (btn) btn.disabled = true;
    const res = await fetch('/api/plugins/obsidian/project-plan/improve-description', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, kind, description, custom_focus }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Prompt improvement failed');
    const data = await res.json();
    textarea.value = data.description || description;
    textarea.dispatchEvent(new Event('input', { bubbles: true }));
    showToast('Project prompt improved');
  } catch (e) {
    console.error('Project prompt improvement failed:', e);
    showToast(e.message || 'Prompt improvement failed');
  } finally {
    stopLoadingAnimation();
    if (btn) btn.disabled = false;
  }
}

async function previewMemoryReview() {
  const title = document.getElementById('obsidian-memory-title')?.value || '';
  const action = document.getElementById('obsidian-memory-action')?.value || 'save_to_obsidian';
  const target_folder = document.getElementById('obsidian-memory-folder')?.value ?? '';
  const target_note = document.getElementById('obsidian-memory-note')?.value || '';
  const content = document.getElementById('obsidian-memory-content')?.value || '';
  const tags = [...memoryReviewTags];
  const link_paths = target_note ? [target_note] : [];
  if (!content.trim()) {
    showToast('Insight to save required');
    return;
  }
  if (!['memory_only', 'discard', 'review_queue'].includes(action) && !memoryReviewDestination.type) {
    showToast('Choose where to save this memory');
    return;
  }
  const panel = document.getElementById('obsidian-memory-preview-panel');
  if (panel) panel.innerHTML = '<div class="obsidian-project-loading">Previewing memory changes...</div>';
  try {
    const res = await fetch('/api/plugins/obsidian/memory-review/preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        candidate: { title, content, source: 'manual', source_ref: currentNotePath || '', risk: 'normal' },
        action,
        target_folder,
        target_note,
        note_type: 'memory',
        status: 'review',
        tags,
        link_paths,
      }),
    });
    if (!res.ok) throw new Error((await res.json()).detail || 'Memory review preview failed');
    memoryReviewPreview = await res.json();
    renderMemoryReviewPreview(memoryReviewPreview);
  } catch (e) {
    console.error('Memory review preview failed:', e);
    if (panel) panel.innerHTML = `<div class="obsidian-project-conflicts">${escapeHtml(e.message || 'Memory review preview failed')}</div>`;
    document.getElementById('obsidian-memory-apply').disabled = true;
  }
}

async function applyMemoryReview() {
  if (!memoryReviewPreview) return;
  const needsConfirm = !['memory_only', 'discard'].includes(memoryReviewPreview.action);
  if (needsConfirm) {
    const confirmed = await styledConfirm(
      memoryReviewPreview.action === 'review_queue'
        ? 'Queue this memory for later review in Obsidian?'
        : 'Apply this memory review to the vault?',
      { confirmText: 'Apply' },
    );
    if (!confirmed) return;
  }
  try {
    const res = await fetch('/api/plugins/obsidian/memory-review/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan: memoryReviewPreview, confirm: needsConfirm }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(typeof err.detail === 'string' ? err.detail : err.detail?.message || 'Memory review apply failed');
    }
    const data = await res.json();
    tagCache = null;
    await loadVaultFiles();
    closeMemoryReview();
    const firstFile = data.created_files?.[0] || data.updated_files?.[0];
    if (firstFile) await openNote(firstFile);
    setViewMode('graph');
    showToast('Memory review applied');
  } catch (e) {
    console.error('Memory review apply failed:', e);
    showToast(e.message || 'Memory review apply failed');
  }
}

async function applyProjectPlan() {
  if (!projectPlanPreview) return;
  syncProjectPlanPreviewEdits();
  const confirmed = await styledConfirm('Create these project files in the vault?', { confirmText: 'Create files' });
  if (!confirmed) return;
  const applyBtn = document.getElementById('obsidian-project-apply');
  if (applyBtn) applyBtn.disabled = true;
  try {
    const url = activeProjectPlanSessionId
      ? `/api/plugins/obsidian/project-plan/sessions/${encodeURIComponent(activeProjectPlanSessionId)}/apply`
      : '/api/plugins/obsidian/project-plan/apply';
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan: projectPlanPreview, confirm: true }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(typeof err.detail === 'string' ? err.detail : err.detail?.message || 'Project apply failed');
    }
    const data = await res.json();
    tagCache = null;
    activeProjectPlanSession = null;
    activeProjectPlanSessionId = null;
    projectPlanPreview = null;
    await loadVaultFiles();
    closeProjectPlanner();
    const firstFile = data.created_files?.[0] || data.session?.plan?.files?.[0]?.path;
    if (firstFile) await openNote(firstFile);
    setViewMode('graph');
    showToast('Project files created');
  } catch (e) {
    console.error('Project apply failed:', e);
    showToast(e.message || 'Project apply failed');
    if (activeProjectPlanSessionId) {
      await openProjectPlanSession(activeProjectPlanSessionId).catch(() => {});
    }
  } finally {
    updateProjectApplyState();
  }
}

async function renderGraphView() {
  const graph = document.getElementById('obsidian-graph-view');
  if (!graph) return;

  destroyGraphCytoscape();
  const renderToken = graphViewState.renderToken;
  graph.innerHTML = '<div class="obsidian-graph-empty">Building graph...</div>';

  let prepared;
  try {
    prepared = prepareGraphData(await fetchGraphData());
  } catch (e) {
    console.error('Failed to build graph:', e);
    graph.innerHTML = '<div class="obsidian-graph-empty">Unable to build graph.</div>';
    return;
  }
  if (renderToken !== graphViewState.renderToken) return;

  const lensed = applyGraphLens(prepared);

  if (!lensed.prepared.markdownNodes.length) {
    graph.innerHTML = `<div class="obsidian-graph-empty">${escapeHtml(lensed.summary.emptyMessage || 'No markdown notes to graph yet.')}</div>`;
    return;
  }

  const preferredRenderer = preferredGraphRenderer();
  renderGraphShell(graph, lensed.prepared, preferredRenderer, lensed.summary);

  if (preferredRenderer === OBSIDIAN_GRAPH_RENDERER_SVG) {
    renderSvgGraphFallback(graph, lensed.prepared);
    return;
  }

  try {
    await renderCytoscapeGraph(graph, lensed.prepared, renderToken);
  } catch (e) {
    if (renderToken !== graphViewState.renderToken) return;
    console.warn('Cytoscape graph failed, falling back to SVG:', e);
    renderGraphShell(graph, lensed.prepared, OBSIDIAN_GRAPH_RENDERER_SVG, lensed.summary);
    renderSvgGraphFallback(graph, lensed.prepared);
  }
}

async function fetchGraphData() {
  const focusPath = graphFocusPath();
  const focus = focusPath ? `?focus=${encodeURIComponent(focusPath)}` : '';
  const res = await fetch(`/api/plugins/obsidian/graph${focus}`);
  if (!res.ok) throw new Error(`Graph request failed: ${res.status}`);
  return res.json();
}

function preferredGraphRenderer() {
  try {
    return localStorage.getItem(OBSIDIAN_GRAPH_RENDERER_KEY) === OBSIDIAN_GRAPH_RENDERER_SVG
      ? OBSIDIAN_GRAPH_RENDERER_SVG
      : OBSIDIAN_GRAPH_RENDERER_CYTOSCAPE;
  } catch (_) {
    return OBSIDIAN_GRAPH_RENDERER_CYTOSCAPE;
  }
}

function destroyGraphCytoscape() {
  graphViewState.renderToken += 1;
  if (graphViewState.cytoscapeInstance) {
    graphViewState.cytoscapeInstance.destroy();
    graphViewState.cytoscapeInstance = null;
  }
}

function directFolderForPath(path) {
  const clean = String(path || '').replace(/\\/g, '/');
  if (!clean.includes('/')) return null;
  return clean.split('/').slice(0, -1).join('/');
}

function parentFolderForFolder(path) {
  const clean = String(path || '').replace(/\\/g, '/');
  if (!clean.includes('/')) return null;
  return clean.split('/').slice(0, -1).join('/');
}

function prepareGraphData(graphData) {
  const rawNodes = graphData.graph?.nodes || [];
  const markdownNodes = rawNodes.filter(node => node.type === 'markdown');
  const markdownIds = new Set(markdownNodes.map(node => node.id));
  const folderIds = new Set(rawNodes.filter(node => node.type === 'folder').map(node => node.id));

  markdownNodes.forEach(node => {
    let folder = directFolderForPath(node.id);
    while (folder) {
      folderIds.add(folder);
      folder = parentFolderForFolder(folder);
    }
  });

  const folderNodes = [...folderIds]
    .sort((a, b) => a.localeCompare(b))
    .map(id => ({
      id,
      label: id.split('/').pop(),
      type: 'folder',
    }));

  const allEdges = (graphData.graph?.edges || [])
    .filter(edge => markdownIds.has(edge.source) && markdownIds.has(edge.target));
  const edgeTypes = [...new Set(allEdges.map(edge => edge.type || 'link'))].sort();
  const edges = allEdges;

  return {
    markdownNodes,
    folderNodes,
    edges,
    edgeTypes,
  };
}

function buildGraphFolderNodes(markdownNodes) {
  const folderIds = new Set();
  markdownNodes.forEach(node => {
    let folder = directFolderForPath(node.id);
    while (folder) {
      folderIds.add(folder);
      folder = parentFolderForFolder(folder);
    }
  });
  return [...folderIds]
    .sort((a, b) => a.localeCompare(b))
    .map(id => ({
      id,
      label: id.split('/').pop(),
      type: 'folder',
    }));
}

function graphLensSummary(mode, prepared, extra = {}) {
  const visibleNotes = prepared.markdownNodes.length;
  const visibleEdges = prepared.edges.length;
  const visibleFolders = prepared.folderNodes.length;
  const clusterState = extra.clusterState || 'Derived clusters will appear here when Memory Index data is available.';
  if (mode === 'current_source') {
    return {
      title: 'Current source',
      description: extra.description || 'Shows the selected source note together with directly connected notes and folders.',
      focusLabel: extra.focusLabel || (currentNotePath || 'No source selected'),
      visibleNotes,
      visibleEdges,
      visibleFolders,
      clusterState,
      emptyMessage: 'No current source is available for this graph lens yet.',
    };
  }
  if (mode === 'review_queue') {
    return {
      title: 'Review queue',
      description: extra.description || 'Shows staged queue notes and the notes they directly connect to in the current vault graph.',
      focusLabel: extra.focusLabel || 'AI Memory/Review Queue',
      visibleNotes,
      visibleEdges,
      visibleFolders,
      clusterState,
      emptyMessage: 'No review queue notes are visible in the graph yet.',
    };
  }
  return {
    title: 'Overview',
    description: 'Shows the current vault graph as the baseline Lens over sources and relationships.',
    focusLabel: currentNotePath || 'Whole vault',
    visibleNotes,
    visibleEdges,
    visibleFolders,
    clusterState,
    emptyMessage: 'No markdown notes to graph yet.',
  };
}

function applyGraphLens(prepared) {
  if (graphViewState.lensMode === 'current_source') {
    const currentNode = prepared.markdownNodes.find(node => node.id === currentNotePath);
    if (!currentNode) {
      return {
        prepared,
        summary: graphLensSummary('current_source', prepared, {
          description: 'No current note is selected, so the Lens falls back to the full overview.',
          focusLabel: 'No source selected',
        }),
      };
    }
    const visibleIds = new Set([currentNode.id]);
    prepared.edges.forEach(edge => {
      if (edge.source === currentNode.id) visibleIds.add(edge.target);
      if (edge.target === currentNode.id) visibleIds.add(edge.source);
    });
    const markdownNodes = prepared.markdownNodes.filter(node => visibleIds.has(node.id));
    const edges = prepared.edges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target));
    const lensed = {
      markdownNodes,
      folderNodes: buildGraphFolderNodes(markdownNodes),
      edges,
      edgeTypes: prepared.edgeTypes,
    };
    return {
      prepared: lensed,
      summary: graphLensSummary('current_source', lensed, {
        focusLabel: currentNode.id,
      }),
    };
  }
  if (graphViewState.lensMode === 'review_queue') {
    const queueSeedIds = new Set(
      prepared.markdownNodes
        .filter(node => node.id === 'AI Memory/Review Queue.md' || node.id.startsWith('AI Memory/Review Queue/'))
        .map(node => node.id),
    );
    if (queueSeedIds.size === 0) {
      const emptyPrepared = {
        markdownNodes: [],
        folderNodes: [],
        edges: [],
        edgeTypes: prepared.edgeTypes,
      };
      return {
        prepared: emptyPrepared,
        summary: graphLensSummary('review_queue', emptyPrepared),
      };
    }
    const visibleIds = new Set(queueSeedIds);
    prepared.edges.forEach(edge => {
      if (queueSeedIds.has(edge.source)) visibleIds.add(edge.target);
      if (queueSeedIds.has(edge.target)) visibleIds.add(edge.source);
    });
    const markdownNodes = prepared.markdownNodes.filter(node => visibleIds.has(node.id));
    const edges = prepared.edges.filter(edge => visibleIds.has(edge.source) && visibleIds.has(edge.target));
    const lensed = {
      markdownNodes,
      folderNodes: buildGraphFolderNodes(markdownNodes),
      edges,
      edgeTypes: prepared.edgeTypes,
    };
    return {
      prepared: lensed,
      summary: graphLensSummary('review_queue', lensed, {
        focusLabel: `${queueSeedIds.size} queued note${queueSeedIds.size === 1 ? '' : 's'}`,
      }),
    };
  }
  return {
    prepared,
    summary: graphLensSummary('overview', prepared),
  };
}

function renderGraphShell(graph, prepared, renderer, summary) {
  const graphFilterState = graphViewState.filters;
  const edgeCheckboxes = ['wiki_link', 'filename_mention', 'shared_tag', 'manual', 'relates_to', 'depends_on', 'blocks', 'supports']
    .map(type => {
      const checked = graphFilterState.edges[type] !== false ? 'checked' : '';
      const label = type.replace(/_/g, ' ');
      return `
        <label class="obsidian-filter-checkbox-label">
          <input type="checkbox" data-filter-edge="${type}" ${checked}>
          <span>${escapeHtml(label)}</span>
        </label>
      `;
    }).join('');

  const nodeMarkdownChecked = graphFilterState.nodes.markdown !== false ? 'checked' : '';
  const nodeFolderChecked = graphFilterState.nodes.folder !== false ? 'checked' : '';
  const panelOpenClass = graphViewState.filterPanelOpen ? '' : ' hidden';

  const modeOptions = [
    { value: 'highlight', label: 'Highlight Matches' },
    { value: 'show', label: 'Show Matches Only' },
    { value: 'hide', label: 'Hide Matches' }
  ].map(opt => `
    <option value="${opt.value}" ${graphFilterState.mode === opt.value ? 'selected' : ''}>${opt.label}</option>
  `).join('');
  const lensOptions = [
    { value: 'overview', label: 'Overview' },
    { value: 'current_source', label: 'Current source' },
    { value: 'review_queue', label: 'Review queue' },
  ].map(opt => `
    <option value="${opt.value}" ${graphViewState.lensMode === opt.value ? 'selected' : ''}>${opt.label}</option>
  `).join('');

  graph.innerHTML = `
    <div class="obsidian-graph-controls">
      <label class="obsidian-graph-lens-picker">
        <span>Lens</span>
        <select id="obsidian-graph-lens-mode">${lensOptions}</select>
      </label>
      <button class="obsidian-graph-filter-btn" id="obsidian-graph-filter-toggle" title="Graph Filters">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>
        <span>Filters</span>
      </button>
      
      <div class="obsidian-graph-filter-panel${panelOpenClass}" id="obsidian-graph-filter-panel">
        <div class="obsidian-filter-section">
          <label class="obsidian-filter-label">Filter Mode</label>
          <select id="obsidian-graph-filter-mode">${modeOptions}</select>
        </div>
        
        <div class="obsidian-filter-section">
          <label class="obsidian-filter-label">Search Nodes</label>
          <input type="text" id="obsidian-graph-filter-search" placeholder="Search title or content..." value="${escapeHtml(graphFilterState.search || '')}">
        </div>

        <div class="obsidian-filter-section">
          <label class="obsidian-filter-label">Node Types</label>
          <div class="obsidian-filter-group">
            <label class="obsidian-filter-checkbox-label">
              <input type="checkbox" id="obsidian-graph-filter-node-markdown" ${nodeMarkdownChecked}>
              <span>Notes</span>
            </label>
            <label class="obsidian-filter-checkbox-label">
              <input type="checkbox" id="obsidian-graph-filter-node-folder" ${nodeFolderChecked}>
              <span>Folders</span>
            </label>
          </div>
        </div>
        
        <div class="obsidian-filter-section">
          <label class="obsidian-filter-label">Edge Types</label>
          <div class="obsidian-filter-group grid-2">
            ${edgeCheckboxes}
          </div>
        </div>

        <div class="obsidian-filter-section">
          <label class="obsidian-filter-label">Filter by Tags</label>
          <input type="text" id="obsidian-graph-filter-tags" placeholder="e.g. project, type/concept" value="${escapeHtml((graphFilterState.tags || []).join(', '))}">
        </div>

        <button class="obsidian-graph-filter-reset-btn" id="obsidian-graph-filter-reset">Reset Filters</button>
      </div>
    </div>
    <div class="obsidian-graph-lens-summary">
      <div class="obsidian-graph-lens-card">
        <span>Lens</span>
        <strong>${escapeHtml(summary.title)}</strong>
        <small>${escapeHtml(summary.description)}</small>
      </div>
      <div class="obsidian-graph-lens-card">
        <span>Focus</span>
        <strong>${escapeHtml(summary.focusLabel)}</strong>
        <small>${escapeHtml(summary.visibleNotes)} notes, ${escapeHtml(summary.visibleEdges)} edges, ${escapeHtml(summary.visibleFolders)} folders</small>
      </div>
      <div class="obsidian-graph-lens-card">
        <span>Derived clusters</span>
        <strong>Waiting on index data</strong>
        <small>${escapeHtml(summary.clusterState)}</small>
      </div>
    </div>
    <div class="obsidian-graph-canvas" id="obsidian-graph-canvas" data-graph-renderer="${escapeHtml(renderer)}"></div>
  `;

  graph.querySelector('#obsidian-graph-lens-mode')?.addEventListener('change', (e) => {
    graphViewState.lensMode = e.target.value || 'overview';
    loadGraphFilterState();
    saveGraphLensMode();
    renderGraphView();
  });
  const toggleBtn = graph.querySelector('#obsidian-graph-filter-toggle');
  const panel = graph.querySelector('#obsidian-graph-filter-panel');
  toggleBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    graphViewState.filterPanelOpen = !graphViewState.filterPanelOpen;
    panel?.classList.toggle('hidden', !graphViewState.filterPanelOpen);
  });

  panel?.addEventListener('click', (e) => {
    e.stopPropagation();
  });

  if (graphViewState.outsideClickHandler) {
    document.removeEventListener('click', graphViewState.outsideClickHandler);
  }
  graphViewState.outsideClickHandler = (e) => {
    const currentPanel = document.getElementById('obsidian-graph-filter-panel');
    const currentToggle = document.getElementById('obsidian-graph-filter-toggle');
    if (graphViewState.filterPanelOpen && currentPanel && currentToggle && !currentPanel.contains(e.target) && e.target !== currentToggle && !currentToggle.contains(e.target)) {
      graphViewState.filterPanelOpen = false;
      currentPanel.classList.add('hidden');
    }
  };
  document.addEventListener('click', graphViewState.outsideClickHandler);

  graph.querySelector('#obsidian-graph-filter-mode')?.addEventListener('change', (e) => {
    graphFilterState.mode = e.target.value;
    saveGraphFilterState();
    renderGraphView();
  });

  const searchInput = graph.querySelector('#obsidian-graph-filter-search');
  searchInput?.addEventListener('input', () => {
    graphFilterState.search = searchInput.value;
    saveGraphFilterState();
    renderGraphView();
  });

  graph.querySelector('#obsidian-graph-filter-node-markdown')?.addEventListener('change', (e) => {
    graphFilterState.nodes.markdown = e.target.checked;
    saveGraphFilterState();
    renderGraphView();
  });

  graph.querySelector('#obsidian-graph-filter-node-folder')?.addEventListener('change', (e) => {
    graphFilterState.nodes.folder = e.target.checked;
    saveGraphFilterState();
    renderGraphView();
  });

  graph.querySelectorAll('input[data-filter-edge]').forEach(checkbox => {
    checkbox.addEventListener('change', (e) => {
      const edgeType = e.target.getAttribute('data-filter-edge');
      graphFilterState.edges[edgeType] = e.target.checked;
      saveGraphFilterState();
      renderGraphView();
    });
  });

  const tagsInput = graph.querySelector('#obsidian-graph-filter-tags');
  tagsInput?.addEventListener('input', () => {
    graphFilterState.tags = tagsInput.value.split(',').map(t => t.trim()).filter(Boolean);
    saveGraphFilterState();
    renderGraphView();
  });

  graph.querySelector('#obsidian-graph-filter-reset')?.addEventListener('click', () => {
    resetGraphFilterState();
    saveGraphFilterState();
    renderGraphView();
  });
}

function graphCssVar(element, name, fallback) {
  const value = getComputedStyle(element).getPropertyValue(name).trim();
  return value || fallback;
}

async function loadCytoscape() {
  if (window.cytoscape) return window.cytoscape;
  if (graphCytoscapeLoadPromise) return graphCytoscapeLoadPromise;

  graphCytoscapeLoadPromise = new Promise((resolve, reject) => {
    const existing = document.querySelector('script[data-obsidian-cytoscape-loader="true"]');
    if (existing) {
      existing.addEventListener('load', () => resolve(window.cytoscape), { once: true });
      existing.addEventListener('error', reject, { once: true });
      return;
    }

    const script = document.createElement('script');
    script.src = OBSIDIAN_CYTOSCAPE_ASSET;
    script.async = true;
    script.dataset.obsidianCytoscapeLoader = 'true';
    script.onload = () => window.cytoscape ? resolve(window.cytoscape) : reject(new Error('Cytoscape did not initialize'));
    script.onerror = () => reject(new Error('Failed to load Cytoscape'));
    document.head.appendChild(script);
  });

  return graphCytoscapeLoadPromise;
}

function focusedProjectFolder() {
  return focusedGraphFolderPath();
}

function projectFolderNodes(prepared) {
  const folder = focusedProjectFolder();
  if (!folder) return [];
  return prepared.markdownNodes
    .filter(node => directFolderForPath(node.id) === folder)
    .sort((a, b) => a.id.localeCompare(b.id));
}

function projectHubNode(nodes) {
  if (!nodes.length) return null;
  return nodes.find(node => /^00[\s_-]/.test(getBaseName(node.id).replace(/\.md$/i, ''))) || nodes[0];
}

function starPositions(prepared, width, height) {
  const nodes = projectFolderNodes(prepared);
  if (nodes.length < 3) return null;
  const hub = projectHubNode(nodes);
  if (!hub) return null;
  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.max(120, Math.min(width, height) * 0.32);
  const positions = new Map([[hub.id, { x: cx, y: cy }]]);
  const spokes = nodes.filter(node => node.id !== hub.id);
  spokes.forEach((node, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(spokes.length, 1) - Math.PI / 2;
    positions.set(node.id, {
      x: cx + Math.cos(angle) * radius,
      y: cy + Math.sin(angle) * radius,
    });
  });
  return { hub: hub.id, positions };
}

function cytoscapeElements(prepared, star = null) {
  const graphFilterState = graphViewState.filters;
  const focusedFolder = focusedGraphFolderPath();
  
  const folderElements = prepared.folderNodes.map(node => {
    const parent = parentFolderForFolder(node.id);
    const isFocusedFolder = focusedFolder && (node.id === focusedFolder || focusedFolder.startsWith(`${node.id}/`));
    const matches = isNodeMatchingFilter(node);
    
    let classes = ['obsidian-folder-node', isFocusedFolder ? 'obsidian-focused-project-folder' : ''];
    if (graphFilterState.mode === 'show' && !matches) {
      classes.push('hidden');
    } else if (graphFilterState.mode === 'highlight') {
      if (matches) classes.push('highlighted');
      else classes.push('dimmed');
    } else if (graphFilterState.mode === 'hide' && matches) {
      classes.push('hidden');
    }

    return {
      data: {
        id: node.id,
        label: node.label || node.id,
        type: 'folder',
        parent: parent || undefined,
      },
      classes: classes.filter(Boolean).join(' '),
    };
  });

  const markdownElements = prepared.markdownNodes.map(node => {
    const parent = directFolderForPath(node.id);
    const isFocusedProjectNode = focusedFolder && (parent === focusedFolder || parent?.startsWith(`${focusedFolder}/`));
    const matches = isNodeMatchingFilter(node);
    const isCurrentNode = node.id === currentNotePath;

    let classes = [
      isCurrentNode ? 'obsidian-current-node' : '',
      isFocusedProjectNode ? 'obsidian-focused-project-node' : '',
      star?.hub === node.id ? 'obsidian-project-hub-node' : '',
    ];
    if (graphFilterState.mode === 'show' && !matches && !isCurrentNode) {
      classes.push('hidden');
    } else if (graphFilterState.mode === 'highlight') {
      if (matches) classes.push('highlighted');
      else if (!isCurrentNode) classes.push('dimmed');
    } else if (graphFilterState.mode === 'hide' && matches && !isCurrentNode) {
      classes.push('hidden');
    }

    const element = {
      data: {
        id: node.id,
        label: node.label || node.id.replace(/\.md$/i, '').split('/').pop(),
        type: 'markdown',
        parent: parent || undefined,
        tags: (node.tags || []).join(', '),
      },
      classes: classes.filter(Boolean).join(' '),
    };
    const position = star?.positions?.get(node.id);
    if (position) element.position = position;
    return element;
  });

  const edgeElements = prepared.edges.map((edge, index) => {
    const sourceNode = prepared.markdownNodes.find(n => n.id === edge.source) || prepared.folderNodes.find(n => n.id === edge.source);
    const targetNode = prepared.markdownNodes.find(n => n.id === edge.target) || prepared.folderNodes.find(n => n.id === edge.target);

    const sourceMatches = sourceNode ? isNodeMatchingFilter(sourceNode) : true;
    const targetMatches = targetNode ? isNodeMatchingFilter(targetNode) : true;
    const edgeMatches = isEdgeMatchingFilter(edge) && sourceMatches && targetMatches;

    let classes = [`edge-${edge.type || 'link'}`];
    if (graphFilterState.mode === 'show' && !edgeMatches) {
      classes.push('hidden');
    } else if (graphFilterState.mode === 'highlight') {
      if (edgeMatches) classes.push('highlighted');
      else classes.push('dimmed');
    } else if (graphFilterState.mode === 'hide' && edgeMatches) {
      classes.push('hidden');
    }

    return {
      data: {
        id: `edge-${index}-${edge.source}-${edge.target}-${edge.type || 'link'}`,
        source: edge.source,
        target: edge.target,
        type: edge.type || 'link',
        reason: edge.reason || edge.type || 'link',
        weight: edge.weight || '',
      },
      classes: classes.join(' '),
    };
  });

  return [...folderElements, ...markdownElements, ...edgeElements];
}

function cytoscapeStyle(container) {
  const fg = graphCssVar(container, '--fg', '#e5e7eb');
  const bg = graphCssVar(container, '--bg', '#111827');
  const accent = graphCssVar(container, '--accent', '#ef4444');
  const border = graphCssVar(container, '--border', '#374151');

  return [
    {
      selector: 'node',
      style: {
        'background-color': bg,
        'border-color': fg,
        'border-width': 1.5,
        'color': fg,
        'font-size': 12,
        'label': 'data(label)',
        'text-background-color': bg,
        'text-background-opacity': 0.88,
        'text-background-padding': 2,
        'text-margin-y': 7,
        'text-valign': 'bottom',
        'text-halign': 'center',
        'width': 26,
        'height': 26,
      },
    },
    {
      selector: 'node[type = "folder"]',
      style: {
        'background-opacity': 0.08,
        'background-color': accent,
        'border-color': border,
        'border-style': 'dashed',
        'border-width': 1.4,
        'padding': 18,
        'shape': 'round-rectangle',
        'text-valign': 'top',
        'text-halign': 'left',
        'text-margin-x': 6,
        'text-margin-y': 6,
      },
    },
    {
      selector: 'node[type = "markdown"]',
      style: {
        'shape': 'ellipse',
      },
    },
    {
      selector: 'node.obsidian-current-node',
      style: {
        'background-color': accent,
        'border-color': accent,
        'border-width': 3,
        'width': 34,
        'height': 34,
      },
    },
    {
      selector: 'node.obsidian-focused-project-folder',
      style: {
        'background-opacity': 0.14,
        'border-color': accent,
        'border-width': 2,
      },
    },
    {
      selector: 'node.obsidian-focused-project-node',
      style: {
        'border-color': accent,
        'border-width': 2,
      },
    },
    {
      selector: 'node.obsidian-project-hub-node',
      style: {
        'background-color': accent,
        'border-color': fg,
        'border-width': 3,
        'width': 38,
        'height': 38,
      },
    },
    {
      selector: 'edge',
      style: {
        'curve-style': 'bezier',
        'line-color': fg,
        'opacity': 0.34,
        'target-arrow-color': fg,
        'target-arrow-shape': 'triangle',
        'width': 1.3,
      },
    },
    {
      selector: 'edge[type = "shared_tag"]',
      style: {
        'line-color': accent,
        'target-arrow-color': accent,
        'opacity': 0.46,
        'width': 1.1,
      },
    },
    {
      selector: 'edge[type = "filename_mention"]',
      style: {
        'line-style': 'dashed',
      },
    },
    {
      selector: 'edge[type = "manual"], edge[type = "relates_to"], edge[type = "depends_on"], edge[type = "blocks"], edge[type = "supports"]',
      style: {
        'line-color': accent,
        'target-arrow-color': accent,
        'opacity': 0.64,
        'width': 2,
      },
    },
    {
      selector: 'edge[type = "blocks"]',
      style: {
        'line-style': 'dashed',
      },
    },
    {
      selector: '.hidden',
      style: {
        'display': 'none',
      },
    },
    {
      selector: 'node.dimmed',
      style: {
        'opacity': 0.15,
      },
    },
    {
      selector: 'edge.dimmed',
      style: {
        'opacity': 0.08,
      },
    },
    {
      selector: 'node.highlighted',
      style: {
        'border-width': 3.5,
        'border-color': accent,
      },
    },
    {
      selector: 'edge.highlighted',
      style: {
        'opacity': 0.8,
        'width': 2.8,
        'line-color': accent,
        'target-arrow-color': accent,
      },
    },
  ];
}

async function renderCytoscapeGraph(graph, prepared, renderToken) {
  const canvas = graph.querySelector('#obsidian-graph-canvas');
  if (!canvas) return;
  const cytoscape = await loadCytoscape();
  if (renderToken !== graphViewState.renderToken) return;
  const rect = canvas.getBoundingClientRect();
  const star = starPositions(prepared, Math.max(760, rect.width || 900), Math.max(480, rect.height || 560));

  const cytoscapeInstance = cytoscape({
    container: canvas,
    elements: cytoscapeElements(prepared, star),
    layout: star ? {
      name: 'preset',
      animate: false,
      fit: true,
      padding: 58,
    } : {
      name: 'cose',
      animate: false,
      fit: true,
      padding: 48,
      nodeDimensionsIncludeLabels: true,
      avoidOverlap: true,
      avoidOverlapPadding: 18,
      nestingFactor: 1.25,
      nodeRepulsion: 14000,
      idealEdgeLength: 125,
      componentSpacing: 110,
      gravity: 0.55,
      numIter: 1400,
    },
    style: cytoscapeStyle(canvas),
    wheelSensitivity: OBSIDIAN_GRAPH_WHEEL_SENSITIVITY,
    minZoom: 0.28,
    maxZoom: 2.4,
  });
  graphViewState.cytoscapeInstance = cytoscapeInstance;

  cytoscapeInstance.on('tap', 'node[type = "markdown"]', (event) => {
    activateGraphNode(event.target.id());
  });
  cytoscapeInstance.on('mouseover', 'node, edge', (event) => {
    const data = event.target.data();
    canvas.title = data.reason || data.tags || data.id || '';
  });
  cytoscapeInstance.on('mouseout', 'node, edge', () => {
    canvas.title = '';
  });

  const viewportTargetPath = focusedGraphFolderPath() || currentNotePath || '';
  if (viewportTargetPath) {
    const activeNode = cytoscapeInstance.getElementById(viewportTargetPath);
    if (activeNode.length > 0) {
      setTimeout(() => {
        if (graphViewState.renderToken === renderToken && graphViewState.cytoscapeInstance === cytoscapeInstance) {
          cytoscapeInstance.animate({
            center: { eles: activeNode },
            zoom: activeNode.data('type') === 'folder' ? 0.92 : 1.15,
            duration: 350
          });
        }
      }, 50);
    }
  }
}

function renderSvgGraphFallback(graph, prepared) {
  const canvas = graph.querySelector('#obsidian-graph-canvas');
  if (!canvas) return;
  const graphFilterState = graphViewState.filters;

  const nodes = prepared.markdownNodes;
  const edges = prepared.edges;
  const width = 900;
  const height = 560;
  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.max(90, Math.min(width, height) * 0.34);
  const positions = new Map();
  const star = starPositions(prepared, width, height);

  if (star) {
    star.positions.forEach((position, path) => positions.set(path, position));
  }

  nodes.forEach((node, index) => {
    const path = node.id;
    if (!positions.has(path)) {
      const angle = (Math.PI * 2 * index) / Math.max(nodes.length, 1) - Math.PI / 2;
      const linkedCount = edges.filter(edge => edge.source === path || edge.target === path).length;
      const r = path === currentNotePath ? radius * 0.55 : radius + (linkedCount % 3) * 22;
      positions.set(path, {
        x: cx + Math.cos(angle) * r,
        y: cy + Math.sin(angle) * r
      });
    }
  });

  const edgeSvg = edges.map(edge => {
    const from = positions.get(edge.source);
    const to = positions.get(edge.target);
    if (!from || !to) return '';

    const sourceNode = nodes.find(n => n.id === edge.source);
    const targetNode = nodes.find(n => n.id === edge.target);
    const sourceMatches = sourceNode ? isNodeMatchingFilter(sourceNode) : true;
    const targetMatches = targetNode ? isNodeMatchingFilter(targetNode) : true;
    const edgeMatches = isEdgeMatchingFilter(edge) && sourceMatches && targetMatches;

    const type = escapeHtml(edge.type || 'link');
    const reason = escapeHtml(edge.reason || type);

    let edgeClass = `obsidian-graph-edge edge-${type}`;
    if (graphFilterState.mode === 'show' && !edgeMatches) edgeClass += ' hidden';
    else if (graphFilterState.mode === 'highlight') {
      edgeClass += edgeMatches ? ' highlighted' : ' dimmed';
    } else if (graphFilterState.mode === 'hide' && edgeMatches) edgeClass += ' hidden';

    return `<line class="${edgeClass}" x1="${from.x.toFixed(1)}" y1="${from.y.toFixed(1)}" x2="${to.x.toFixed(1)}" y2="${to.y.toFixed(1)}"><title>${reason}</title></line>`;
  }).join('');

  const nodeSvg = nodes.map(node => {
    const path = node.id;
    const pos = positions.get(path);
    const isCurrent = path === currentNotePath;
    const label = escapeHtml(path.replace(/\.md$/i, '').split('/').pop());
    const safePath = escapeHtml(path);
    const tags = escapeHtml((node.tags || []).slice(0, 4).join(', '));
    const matches = isNodeMatchingFilter(node);

    const classes = [
      'obsidian-graph-node',
      isCurrent ? 'current' : '',
      star?.hub === path ? 'project-hub' : '',
    ];
    if (graphFilterState.mode === 'show' && !matches && !isCurrent) classes.push('hidden');
    else if (graphFilterState.mode === 'highlight') {
      if (matches) classes.push('highlighted');
      else if (!isCurrent) classes.push('dimmed');
    } else if (graphFilterState.mode === 'hide' && matches && !isCurrent) classes.push('hidden');

    const classesStr = classes.filter(Boolean).join(' ');
    return `
      <g class="${classesStr}" data-path="${safePath}" tabindex="0" role="button">
        <title>${safePath}${tags ? `\nTags: ${tags}` : ''}</title>
        <circle cx="${pos.x.toFixed(1)}" cy="${pos.y.toFixed(1)}" r="${isCurrent ? 18 : 13}"></circle>
        <text x="${pos.x.toFixed(1)}" y="${(pos.y + 30).toFixed(1)}">${label}</text>
      </g>
    `;
  }).join('');

  canvas.innerHTML = `
    <svg class="obsidian-graph-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Obsidian note graph">
      <g>${edgeSvg}</g>
      <g>${nodeSvg}</g>
    </svg>
  `;

  canvas.querySelectorAll('.obsidian-graph-node:not(.missing)').forEach(node => {
    node.addEventListener('click', () => activateGraphNode(node.dataset.path));
    node.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        activateGraphNode(node.dataset.path);
      }
    });
  });
}

function findFileInTree(nodes, relativePath) {
  const cleanSearch = relativePath.toLowerCase();
  for (const node of nodes) {
    const nodePathLower = node.path.toLowerCase();
    if (!node.is_dir) {
      if (nodePathLower === cleanSearch || nodePathLower === cleanSearch + '.md') {
        return node.path;
      }
    } else if (node.children) {
      const found = findFileInTree(node.children, relativePath);
      if (found) return found;
    }
  }
  return null;
}

async function handleWikiLinkClick(targetPath) {
  let notePath = targetPath;
  if (!notePath.toLowerCase().endsWith('.md')) {
    notePath += '.md';
  }

  const existingPath = findFileInTree(vaultFiles, notePath);
  if (existingPath) {
    await openNote(existingPath);
    return;
  }

  let dir = '';
  if (currentNotePath && currentNotePath.includes('/')) {
    dir = currentNotePath.substring(0, currentNotePath.lastIndexOf('/') + 1);
  }
  const fullPath = dir + notePath;

  try {
    const res = await fetch('/api/plugins/obsidian/file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path: fullPath,
        content: `# ${targetPath}\n\n`
      })
    });
    if (res.ok) {
      showToast(`Created note: ${targetPath}`);
      await loadVaultFiles();
      await openNote(fullPath);
    }
  } catch (e) {
    console.error('Failed to create wiki note:', e);
  }
}

async function handleRenderedPreviewClick(e) {
  const tagBadge = e.target.closest('[data-obsidian-tag]');
  if (tagBadge) {
    e.preventDefault();
    await openTagDetails(tagBadge.dataset.obsidianTag, tagBadge);
    return;
  }

  const link = e.target.closest('a[href]');
  if (!link) return;
  const href = link.getAttribute('href') || '';
  if (href.startsWith('#obsidian-link-')) {
    e.preventDefault();
    await handleWikiLinkClick(decodeURIComponent(href.replace('#obsidian-link-', '')));
    return;
  }
  if (/^https?:\/\//i.test(href)) {
    link.setAttribute('target', '_blank');
    link.setAttribute('rel', 'noopener noreferrer');
    return;
  }
  const filePath = resolveMarkdownFileLink(href);
  if (filePath) {
    e.preventDefault();
    await openNote(filePath);
  }
}

function renderSearchResults(results) {
  const container = document.getElementById('obsidian-file-tree');
  if (!container) return;

  container.innerHTML = '';
  if (results.length === 0) {
    container.innerHTML = '<div class="obsidian-no-results">No matches found</div>';
    return;
  }

  results.forEach(result => {
    const item = document.createElement('div');
    item.className = 'search-result-item';

    const pathHeader = document.createElement('div');
    pathHeader.className = 'search-result-path';
    const pathLabel = document.createElement('span');
    pathLabel.textContent = result.path;
    pathHeader.appendChild(pathLabel);

    if (selectedTreePath === result.path || currentNotePath === result.path) {
      const actions = document.createElement('span');
      actions.className = 'search-result-actions';

      const renameButton = document.createElement('button');
      renameButton.type = 'button';
      renameButton.className = 'tree-rename-button';
      renameButton.title = 'Rename note';
      renameButton.setAttribute('aria-label', 'Rename selected search result');
      renameButton.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>`;
      renameButton.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        startInlineRenameItem(result.path);
      });

      const deleteButton = document.createElement('button');
      deleteButton.type = 'button';
      deleteButton.className = 'tree-delete-button';
      deleteButton.title = 'Delete note';
      deleteButton.setAttribute('aria-label', 'Delete selected search result');
      deleteButton.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>`;
      deleteButton.addEventListener('click', async (e) => {
        e.preventDefault();
        e.stopPropagation();
        await deleteNote(result.path);
      });

      actions.appendChild(renameButton);
      actions.appendChild(deleteButton);
      pathHeader.appendChild(actions);
    }
    item.appendChild(pathHeader);

    const matchesDiv = document.createElement('div');
    matchesDiv.className = 'search-result-matches';
    result.matches.slice(0, 3).forEach(match => {
      const matchLine = document.createElement('div');
      matchLine.className = 'search-result-match';
      matchLine.innerHTML = `<strong>L${match.line}:</strong> ${escapeHtml(match.text)}`;
      matchesDiv.appendChild(matchLine);
    });
    item.appendChild(matchesDiv);

    item.addEventListener('click', async () => {
      await openNote(result.path);
      renderSearchResults(results);
    });
    container.appendChild(item);
  });
}

// ─── Event Listeners ─────────────────────────────────────────────────────────

async function refreshSearchResults() {
  const searchInput = document.getElementById('obsidian-search-input');
  const q = searchInput?.value.trim() || '';
  if (!q) return;
  try {
    const res = await fetch(`/api/plugins/obsidian/search?q=${encodeURIComponent(q)}`);
    if (res.ok) {
      renderSearchResults(await res.json());
    }
  } catch (e) {
    console.error('Search refresh failed:', e);
  }
}

function setupEventListeners() {
  setupObsidianResizers();

  // Toggle via sidebar or rail button. Delegation keeps this working if the
  // Odysseus shell rebuilds either launcher after this module initializes.
  if (!window.__obsidianPanelClickBound) {
    window.__obsidianPanelClickBound = true;
    document.addEventListener('click', (e) => {
      const launcher = e.target.closest('#tool-obsidian-btn, #rail-obsidian');
      if (!launcher) return;
      e.preventDefault();
      togglePanel();
    });
  }

  // Close / Minimize
  document.getElementById('obsidian-panel-close')?.addEventListener('click', closePanel);
  document.getElementById('obsidian-panel-minimize')?.addEventListener('click', minimizePanel);

  // Backdrop click closes panel
  document.getElementById('obsidian-panel-backdrop')?.addEventListener('click', closePanel);

  // Keyboard: Escape closes panel
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeSettingsMenu();
    }
    if (e.key === 'Escape' && isPanelOpen) {
      closePanel();
    }
    if (e.key === 'F2' && (isPanelOpen || isStandaloneMode()) && selectedTreePath) {
      const target = e.target instanceof Element ? e.target : null;
      if (target?.closest('input, textarea, select, [contenteditable="true"]')) return;
      e.preventDefault();
      promptRenameSelectedItem();
    }
  });

  // New Note
  document.getElementById('obsidian-new-note')?.addEventListener('click', async () => {
    const name = await styledPrompt('Enter note title:', { defaultValue: 'Untitled', confirmText: 'Create' });
    if (!name) return;

    let path = name;
    if (!path.toLowerCase().endsWith('.md')) {
      path += '.md';
    }

    const dir = currentTargetFolder();
    const fullPath = joinPath(dir, path);

    try {
      const res = await fetch('/api/plugins/obsidian/file', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: fullPath,
          content: `# ${name}\n\n`
        })
      });
      if (res.ok) {
        showToast('Note created');
        if (dir) {
          expandedFolders.add(dir);
        }
        await loadVaultFiles();
        await openNote(fullPath);
      } else {
        const err = await res.json();
        showToast(err.detail || 'Failed to create note');
      }
    } catch (e) {
      console.error(e);
      showToast('Error creating note');
    }
  });

  // New Folder
  document.getElementById('obsidian-new-folder')?.addEventListener('click', async () => {
    const name = await styledPrompt('Enter folder name:', { defaultValue: 'New Folder', confirmText: 'Create' });
    if (!name) return;

    const dir = currentTargetFolder();
    const fullPath = joinPath(dir, name);

    try {
      const res = await fetch('/api/plugins/obsidian/folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: fullPath })
      });
      if (res.ok) {
        showToast('Folder created');
        await loadVaultFiles();
      } else {
        const err = await res.json();
        showToast(err.detail || 'Failed to create folder');
      }
    } catch (e) {
      console.error(e);
      showToast('Error creating folder');
    }
  });

  document.getElementById('obsidian-project-plan')?.addEventListener('click', showProjectPlanner);
  document.getElementById('obsidian-project-close')?.addEventListener('click', closeProjectPlanner);
  document.getElementById('obsidian-project-preview')?.addEventListener('click', previewProjectPlan);
  document.getElementById('obsidian-project-apply')?.addEventListener('click', applyProjectPlan);
  document.getElementById('obsidian-project-improve-description')?.addEventListener('click', improveProjectDescription);
  document.getElementById('obsidian-project-folder')?.addEventListener('change', handleProjectInputChanged);
  document.getElementById('obsidian-project-title')?.addEventListener('input', handleProjectInputChanged);
  document.getElementById('obsidian-project-kind')?.addEventListener('change', handleProjectInputChanged);
  document.getElementById('obsidian-project-description')?.addEventListener('input', handleProjectInputChanged);
  document.getElementById('obsidian-project-focus')?.addEventListener('input', handleProjectInputChanged);
  document.querySelectorAll('[data-project-select-trigger]').forEach(trigger => {
    trigger.addEventListener('click', (e) => {
      e.preventDefault();
      toggleProjectSelectMenu(trigger.dataset.projectSelectTrigger);
    });
    trigger.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        toggleProjectSelectMenu(trigger.dataset.projectSelectTrigger);
      }
      if (e.key === 'Escape') {
        closeProjectSelectMenus();
      }
    });
  });
  document.querySelectorAll('.obsidian-project-select-menu').forEach(menu => {
    menu.addEventListener('click', (e) => {
      const option = e.target.closest('[data-project-select-value]');
      if (!option) return;
      e.preventDefault();
      setProjectSelectValue(menu.dataset.projectSelectMenu, option.dataset.projectSelectValue || '');
    });
  });
  document.getElementById('obsidian-memory-read')?.addEventListener('click', showMemoryReadWorkspace);
  document.getElementById('obsidian-memory-review')?.addEventListener('click', showMemoryReview);
  document.getElementById('obsidian-memory-close')?.addEventListener('click', closeMemoryReview);
  document.querySelectorAll('[data-memory-workspace-tab]').forEach(btn => {
    btn.addEventListener('click', () => setMemoryWorkspaceTab(btn.dataset.memoryWorkspaceTab));
  });
  document.getElementById('obsidian-memory-preview')?.addEventListener('click', previewMemoryReview);
  document.getElementById('obsidian-memory-apply')?.addEventListener('click', applyMemoryReview);
  document.getElementById('obsidian-memory-tree')?.addEventListener('click', showMemoryTreePanel);
  document.getElementById('obsidian-memory-tree-close')?.addEventListener('click', closeMemoryTreePanel);
  document.getElementById('obsidian-memory-tree-refresh')?.addEventListener('click', loadMemoryTreeDashboard);
  document.querySelectorAll('[data-memory-tree-tab]').forEach(btn => {
    btn.addEventListener('click', () => setMemoryTreeTab(btn.dataset.memoryTreeTab));
  });
  document.getElementById('obsidian-spark')?.addEventListener('click', showSparkPanel);
  document.getElementById('obsidian-spark-close')?.addEventListener('click', closeSparkPanel);
  document.getElementById('obsidian-spark-analyze')?.addEventListener('click', analyzeSpark);
  document.getElementById('obsidian-spark-plan')?.addEventListener('click', createSparkPlan);
  document.getElementById('obsidian-spark-apply')?.addEventListener('click', applySparkPlan);
  document.getElementById('obsidian-activity')?.addEventListener('click', showActivityPanel);
  document.getElementById('obsidian-activity-close')?.addEventListener('click', closeActivityPanel);
  document.querySelectorAll('[data-spark-tab]').forEach(btn => {
    btn.addEventListener('click', () => setSparkTab(btn.dataset.sparkTab));
  });
  document.addEventListener('click', (e) => {
    if (e.target.closest('#obsidian-insights-refresh, #obsidian-insights-plan-refresh, #obsidian-insights-retry, #obsidian-insights-plan-retry')) {
      analyzeSpark();
      return;
    }
    if (e.target.closest('#obsidian-memory-maintain-primary, #obsidian-memory-maintain-empty')) {
      closeMemoryReview();
      return;
    }
    if (e.target.closest('#obsidian-diagnostics-retry')) {
      loadMemoryTreeDashboard();
      return;
    }
    if (e.target.closest('#obsidian-activity-refresh, #obsidian-activity-retry')) {
      refreshActivityPanel();
    }
  });
  document.getElementById('obsidian-memory-action')?.addEventListener('change', handleMemoryActionChanged);
  document.getElementById('obsidian-memory-title')?.addEventListener('input', () => invalidateMemoryReviewPreview({ clearPanel: true }));
  document.getElementById('obsidian-memory-content')?.addEventListener('input', () => invalidateMemoryReviewPreview({ clearPanel: true }));
  document.getElementById('obsidian-memory-save-to')?.addEventListener('click', openMemoryDestinationPicker);
  document.getElementById('obsidian-memory-picker-search')?.addEventListener('input', renderMemoryDestinationPicker);
  document.querySelectorAll('[data-memory-picker-tab]').forEach(btn => {
    btn.addEventListener('click', () => {
      memoryDestinationPickerTab = btn.dataset.memoryPickerTab || 'folders';
      renderMemoryDestinationPicker();
      document.getElementById('obsidian-memory-picker-search')?.focus();
    });
  });
  const memoryTagEntry = document.getElementById('obsidian-memory-tag-entry');
  memoryTagEntry?.addEventListener('input', updateMemoryTagSuggestions);
  memoryTagEntry?.addEventListener('focus', () => {
    clearMemoryTagPendingRemoval();
    updateMemoryTagSuggestions();
  });
  memoryTagEntry?.addEventListener('keydown', handleMemoryTagKey);
  memoryTagEntry?.addEventListener('blur', () => {
    clearMemoryTagPendingRemoval();
    setTimeout(hideMemoryTagMenu, 120);
  });
  document.getElementById('obsidian-memory-tags')?.addEventListener('click', () => {
    memoryTagEntry?.focus();
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.obsidian-project-select')) {
      closeProjectSelectMenus();
    }
    if (!e.target.closest('#obsidian-memory-destination-field')) {
      closeMemoryDestinationPicker();
    }
  });

  document.getElementById('obsidian-header-view-toggle')?.addEventListener('change', (e) => {
    setViewMode(e.target.checked ? 'graph' : 'document');
  });

  document.getElementById('obsidian-settings-toggle')?.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    toggleSettingsMenu();
  });
  document.getElementById('obsidian-settings-menu')?.addEventListener('click', (e) => {
    const modeBtn = e.target.closest('[data-obsidian-surface-mode]');
    if (modeBtn) {
      e.preventDefault();
      e.stopPropagation();
      changeObsidianSurfaceMode(modeBtn.dataset.obsidianSurfaceMode);
      return;
    }
    const btn = e.target.closest('[data-settings-action]');
    if (!btn) return;
    e.preventDefault();
    handleVaultSettingsAction(btn.dataset.settingsAction);
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('#obsidian-settings-menu, #obsidian-settings-toggle')) {
      closeSettingsMenu();
    }
  });
  document.getElementById('obsidian-import-input')?.addEventListener('change', async (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    const confirmed = await styledConfirm(`Import ${file.name} into this vault?`, { confirmText: 'Import' });
    if (!confirmed) return;
    const password = await styledPrompt('Archive password, if needed:', { defaultValue: '', confirmText: 'Import' });
    try {
      const archive_base64 = await fileToBase64(file);
      const res = await fetch('/api/plugins/obsidian/vault/import', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ archive_base64, password: password || null }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || 'Import failed');
      tagCache = null;
      await loadVaultFiles();
      showToast('Vault imported');
    } catch (err) {
      console.error('Vault import failed:', err);
      showToast(err.message || 'Vault import failed');
    }
  });

  document.getElementById('obsidian-editor-toolbar')?.addEventListener('click', (e) => {
    const btn = e.target.closest('[data-md-action]');
    if (!btn) return;
    e.preventDefault();
    applyMarkdownAction(btn.dataset.mdAction);
  });

  // Autosave + Preview
  const textarea = document.getElementById('obsidian-textarea');
  textarea?.addEventListener('input', () => {
    if (textarea.readOnly) return;
    clearTimeout(autosaveTimeout);
    const original = textarea.value;
    const normalized = normalizeMarkdownTags(original);
    if (normalized !== original) {
      const start = textarea.selectionStart;
      const end = textarea.selectionEnd;
      const delta = original.length - normalized.length;
      textarea.value = normalized;
      textarea.selectionStart = Math.max(0, start - delta);
      textarea.selectionEnd = Math.max(0, end - delta);
    }
    const content = textarea.value;
    currentNoteDocumentIntelligence = currentNotePath
      ? computeDocumentIntelligence(currentNotePath, content)
      : { state: 'empty' };
    applyCurrentNoteLensState();
    renderEditorPreview(content);
    updateAutocomplete();

    autosaveTimeout = setTimeout(async () => {
      if (!currentNotePath) return;
      try {
        await fetch('/api/plugins/obsidian/file', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: currentNotePath, content })
        });
      } catch (e) {
        console.error('Autosave failed:', e);
      }
    }, 800);
  });
  textarea?.addEventListener('keydown', (e) => {
    if (handleAutocompleteKey(e)) {
      e.stopPropagation();
    }
  });
  textarea?.addEventListener('click', updateAutocomplete);
  textarea?.addEventListener('blur', () => {
    setTimeout(hideAutocomplete, 120);
  });
  document.getElementById('obsidian-rendered-preview')?.addEventListener('click', handleRenderedPreviewClick);
  document.addEventListener('click', (e) => {
    if (!e.target.closest('.obsidian-tag-detail-popover, .obsidian-tag-badge')) {
      closeTagDetails();
    }
  });

  // Search with debounce
  const searchInput = document.getElementById('obsidian-search-input');
  searchInput?.addEventListener('input', () => {
    clearTimeout(searchTimeout);
    const q = searchInput.value.trim();
    if (q.length === 0) {
      renderFileTree();
      return;
    }
    searchTimeout = setTimeout(async () => {
      try {
        const res = await fetch(`/api/plugins/obsidian/search?q=${encodeURIComponent(q)}`);
        if (res.ok) {
          const results = await res.json();
          renderSearchResults(results);
        }
      } catch (e) {
        console.error('Search failed:', e);
      }
    }, 300);
  });
}

// ─── Init ────────────────────────────────────────────────────────────────────

function init() {
  loadGraphFilterState();
  loadGraphLensMode();
  const standalone = isStandaloneMode();
  document.body.classList.toggle('obsidian-standalone', standalone);
  injectUIElements();
  if (standalone) {
    applyObsidianSurfaceMode('fullscreen');
  } else {
    initializeClosedObsidianSurface();
  }
  setupEventListeners();
  window.OdysseusObsidian = { openPanel, closePanel, togglePanel };
  if (standalone) {
    openPanel();
  }
  console.log('[Obsidian Plugin] Panel-based UI initialized (Option B)');
}

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
