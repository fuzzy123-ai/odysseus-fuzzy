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

// Dynamic stylesheet insertion
const link = document.createElement('link');
link.rel = 'stylesheet';
link.href = '/api/plugins/obsidian/web/style.css';
document.head.appendChild(link);

// ─── State ───────────────────────────────────────────────────────────────────
let currentNotePath = null;
let selectedTreePath = null;
let inlineRenamePath = null;
let vaultFiles = [];
const expandedFolders = new Set();
let autosaveTimeout = null;
let searchTimeout = null;
let isPanelOpen = false;
let currentViewMode = 'document';
let tagCache = null;
let autocompleteState = null;
let graphEdgeTypeFilter = 'all';
let graphCytoscapeInstance = null;
let graphCytoscapeLoadPromise = null;
let projectPlanPreview = null;
let memoryReviewPreview = null;
let memoryReviewDestination = { type: '', path: '' };
let memoryReviewTags = [];
let memoryTagPickerState = { index: 0, items: [] };
let memoryDestinationPickerTab = 'folders';
let projectTemplateOptions = null;
let gameDevConceptDraft = null;
let projectPlanPreviewStreaming = false;
const OBSIDIAN_GRAPH_RENDERER_KEY = 'odysseus.obsidian.graphRenderer';
const OBSIDIAN_GRAPH_RENDERER_CYTOSCAPE = 'cytoscape';
const OBSIDIAN_GRAPH_RENDERER_SVG = 'svg';
const OBSIDIAN_CYTOSCAPE_ASSET = '/api/plugins/obsidian/web/cytoscape.min.js';
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

function preprocessWikiLinks(text) {
  if (!text) return '';
  return text.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (match, notePath, label) => {
    const cleanPath = notePath.trim();
    const displayLabel = (label || notePath).trim();
    const encodedPath = encodeURIComponent(cleanPath);
    return `[${displayLabel}](#obsidian-link-${encodedPath})`;
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

function findTreeNode(path) {
  return flattenTree(vaultFiles).find(node => node.path === path) || null;
}

function selectedFolderPath() {
  const selected = selectedTreePath ? findTreeNode(selectedTreePath) : null;
  if (selected?.is_dir) return selected.path;
  return '';
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

function toggleSettingsMenu() {
  document.getElementById('obsidian-settings-menu')?.classList.toggle('hidden');
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
      const confirmed = await styledConfirm('Set or replace password protection for this vault?', { confirmText: 'Set password' });
      if (!confirmed) return;
      const password = await styledPrompt('Vault password:', { confirmText: 'Save' });
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
      graphEdgeTypeFilter = 'all';
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
    },
  });
  bindResizeHandle(document.getElementById('obsidian-split-resize-handle'), {
    start: (e) => ({
      startX: e.clientX,
      startWidth: document.querySelector('.obsidian-sidebar')?.getBoundingClientRect().width || DEFAULT_SIDEBAR_WIDTH,
    }),
    move: (e, start) => applySidebarWidth(start.startWidth + (e.clientX - start.startX)),
    end: (e, start) => applySidebarWidth(start.startWidth + (e.clientX - start.startX), { persist: true }),
  });
  if (!window.__obsidianResizeViewportBound) {
    window.__obsidianResizeViewportBound = true;
    window.addEventListener('resize', () => {
      restoreObsidianResizeState();
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
        <div class="obsidian-panel-content">
          <div class="obsidian-panel-resize-handle" id="obsidian-panel-resize-handle" role="separator" aria-label="Panel Resize Handle" aria-orientation="vertical"></div>
          <!-- Header -->
          <div class="obsidian-panel-header">
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
              <button class="obsidian-panel-btn" id="obsidian-panel-minimize" title="Minimize">─</button>
              <button class="obsidian-panel-btn" id="obsidian-panel-close" title="Close">✕</button>
              <div class="obsidian-settings-menu hidden" id="obsidian-settings-menu" role="menu">
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
                <button id="obsidian-memory-review" title="Memory Review">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a7 7 0 0 0-7 7c0 2.2 1.02 4.16 2.61 5.44.53.43.89 1.05.89 1.73V18h7v-.83c0-.68.36-1.3.89-1.73A6.98 6.98 0 0 0 19 10a7 7 0 0 0-7-7Z"/><path d="M9 21h6"/><path d="M10 18v3"/><path d="M14 18v3"/></svg>
                </button>
                <button id="obsidian-refresh" title="Refresh Vault">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
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
                  <select id="obsidian-project-folder" title="Target folder"></select>
                  <input id="obsidian-project-title" type="text" placeholder="Project title" autocomplete="off">
                  <select id="obsidian-project-kind" title="Project kind">
                    <option value="software">Software</option>
                  </select>
                  <div class="obsidian-project-description-wrap">
                    <textarea id="obsidian-project-description" placeholder="Project goal, scope, constraints, and useful context"></textarea>
                    <button type="button" id="obsidian-project-improve-description" class="obsidian-project-ai-btn" title="Improve prompt" aria-label="Improve project prompt">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9.94 14.56 8.5 21l-1.44-6.44L.62 13.12l6.44-1.44L8.5 5.24l1.44 6.44 6.44 1.44-6.44 1.44Z"/><path d="M18 8V2"/><path d="M21 5h-6"/><path d="M19 22v-4"/><path d="M21 20h-4"/></svg>
                    </button>
                  </div>
                  <textarea id="obsidian-project-focus" placeholder="Custom prompts, priorities, sections to emphasize, tone, constraints, or quality checks"></textarea>
                  <div class="obsidian-project-actions">
                    <button type="button" id="obsidian-project-preview" class="btn btn-secondary">Create plan</button>
                    <button type="button" id="obsidian-project-apply" class="btn btn-primary" disabled>Create structure</button>
                  </div>
                </div>
                <div class="obsidian-project-gamedev-draft hidden" id="obsidian-project-gamedev-draft"></div>
                <div class="obsidian-project-preview" id="obsidian-project-preview-panel"></div>
              </div>
              <div class="obsidian-memory-review-panel hidden" id="obsidian-memory-review-panel">
                <div class="obsidian-project-header">
                  <div>
                    <div class="obsidian-project-title">Memory review</div>
                    <div class="obsidian-project-subtitle" id="obsidian-memory-target">Save one reviewed insight into your vault</div>
                  </div>
                  <button type="button" class="obsidian-panel-btn" id="obsidian-memory-close" title="Close memory review">x</button>
                </div>
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
                      <option value="memory_only">Memory only</option>
                      <option value="discard">Discard</option>
                    </select>
                    <small>Choose whether this writes to Obsidian, stays in memory, or is discarded.</small>
                  </label>
                  <div class="obsidian-memory-field obsidian-memory-destination-field" id="obsidian-memory-destination-field">
                    <span>Save to</span>
                    <button type="button" id="obsidian-memory-save-to" class="obsidian-memory-save-to" data-memory-destination-type="" data-memory-destination-path="" aria-label="Choose where to save this memory">
                      <span id="obsidian-memory-save-to-label"></span>
                    </button>
                    <small>Choose a folder to create a new note, or choose a note to append this insight.</small>
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
              </div>
              <div class="obsidian-empty-state" id="obsidian-empty-state">
                <span>Select a note to start editing or create a new one</span>
              </div>
              <div class="obsidian-editor-container hidden" id="obsidian-editor-container">
                <div class="obsidian-editor-header">
                  <div class="obsidian-current-note-title" id="obsidian-current-note-title">Untitled.md</div>
                  <div class="obsidian-editor-actions">
                    <button id="obsidian-rename-note" class="btn btn-secondary">Rename</button>
                    <button id="obsidian-delete-note" class="btn btn-danger">Delete</button>
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
  isPanelOpen = !isPanelOpen;
  document.body.classList.toggle('obsidian-open', isPanelOpen);
  
  if (isPanelOpen) {
    loadVaultFiles();
  }
}

function openPanel() {
  if (isPanelOpen) return;
  isPanelOpen = true;
  document.body.classList.add('obsidian-open');
  loadVaultFiles();
}

function closePanel() {
  if (!isPanelOpen) return;
  if (isStandaloneMode()) {
    document.body.classList.add('obsidian-open');
    return;
  }
  isPanelOpen = false;
  document.body.classList.remove('obsidian-open');
}

// ─── File Tree ───────────────────────────────────────────────────────────────

async function loadVaultFiles() {
  try {
    const res = await fetch('/api/plugins/obsidian/files');
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

function renderFileTree() {
  const container = document.getElementById('obsidian-file-tree');
  if (!container) return;
  buildTreeHTML(vaultFiles, container, 0);
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
    item.dataset.path = node.path;
    const isSelected = selectedTreePath === node.path || (!selectedTreePath && currentNotePath === node.path);
    const isInlineRenaming = node.is_dir && inlineRenamePath === node.path;
    if (isSelected) {
      item.classList.add('active');
    }

    const header = document.createElement('div');
    header.className = 'tree-item-header';
    header.style.paddingLeft = `${level * 12 + 6}px`;
    header.draggable = !isInlineRenaming;

    const icon = document.createElement('span');
    icon.className = 'tree-item-icon';
    if (node.is_dir) {
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
    if (isInlineRenaming) {
      name = document.createElement('input');
      name.className = 'tree-rename-input';
      name.dataset.path = node.path;
      name.value = node.name;
      name.addEventListener('click', (e) => e.stopPropagation());
      name.addEventListener('pointerdown', (e) => e.stopPropagation());
      name.addEventListener('keydown', async (e) => {
        if (e.key === 'Escape') {
          e.preventDefault();
          cancelInlineRenameFolder();
        }
        if (e.key === 'Enter') {
          e.preventDefault();
          await commitInlineRenameFolder(node.path, name.value);
        }
      });
      name.addEventListener('blur', async () => {
        if (inlineRenamePath === node.path) {
          await commitInlineRenameFolder(node.path, name.value);
        }
      });
    } else {
      name = document.createElement('span');
      name.className = 'tree-item-name';
      name.textContent = node.name;
    }

    header.appendChild(icon);
    header.appendChild(name);
    if (node.is_dir && isSelected && !isInlineRenaming) {
      const renameButton = document.createElement('button');
      renameButton.type = 'button';
      renameButton.className = 'tree-rename-button';
      renameButton.title = 'Rename folder';
      renameButton.setAttribute('aria-label', 'Rename selected folder');
      renameButton.innerHTML = `<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>`;
      renameButton.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        startInlineRenameFolder(node.path);
      });
      header.appendChild(renameButton);
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
      } else {
        selectTreeItem(node.path);
        openNote(node.path);
      }
    });

    header.addEventListener('dragstart', (e) => {
      e.dataTransfer.setData('application/x-obsidian-path', node.path);
      e.dataTransfer.effectAllowed = 'move';
      item.classList.add('dragging');
    });

    header.addEventListener('dragend', () => {
      item.classList.remove('dragging');
      document.querySelectorAll('.tree-item.drop-target').forEach(el => el.classList.remove('drop-target'));
    });

    if (node.is_dir) {
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
        if (oldPath) {
          await moveVaultItem(oldPath, node.path);
          return;
        }
        if (e.dataTransfer?.files?.length) {
          await importDroppedMarkdownFiles(e.dataTransfer.files, node.path);
        }
      });
    }

    container.appendChild(item);
  });
}

// ─── Note Operations ─────────────────────────────────────────────────────────

async function openNote(path) {
  try {
    const res = await fetch(`/api/plugins/obsidian/file?path=${encodeURIComponent(path)}`);
    if (res.ok) {
      const data = await res.json();
      currentNotePath = path;

      // Update UI panels visibility
      document.getElementById('obsidian-project-planner')?.classList.add('hidden');
      document.getElementById('obsidian-memory-review-panel')?.classList.add('hidden');
      document.getElementById('obsidian-empty-state').classList.add('hidden');
      document.getElementById('obsidian-editor-container').classList.remove('hidden');

      // Update active selection class in tree
      selectTreeItem(path);

      // Update header title and textarea value
      document.getElementById('obsidian-current-note-title').textContent = path;
      const textarea = document.getElementById('obsidian-textarea');
      textarea.value = data.content || '';
      if (currentViewMode === 'graph') {
        renderGraphView();
      }
    }
  } catch (e) {
    console.error('Failed to open note:', e);
    showToast('Failed to open note');
  }
}

function setViewMode(mode) {
  currentViewMode = mode === 'graph' ? 'graph' : 'document';
  const panes = document.querySelector('.obsidian-editor-panes');
  const graph = document.getElementById('obsidian-graph-view');
  const toolbar = document.getElementById('obsidian-editor-toolbar');
  const toggle = document.getElementById('obsidian-header-view-toggle');
  if (toggle) toggle.checked = currentViewMode === 'graph';
  toolbar?.classList.toggle('hidden', currentViewMode === 'graph');
  panes?.classList.toggle('hidden', currentViewMode === 'graph');
  graph?.classList.toggle('hidden', currentViewMode !== 'graph');
  if (currentViewMode === 'graph') {
    renderGraphView();
  }
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

  const selected = findTreeNode(oldPath);
  const isFolder = Boolean(selected?.is_dir);
  if (isFolder) {
    startInlineRenameFolder(oldPath);
    return;
  }
  const newPath = await styledPrompt('Rename note to:', {
    defaultValue: oldPath,
    confirmText: 'Rename'
  });
  if (!newPath || newPath === oldPath) return;

  await renameVaultItem(oldPath, newPath, isFolder);
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

function startInlineRenameFolder(path) {
  const selected = findTreeNode(path);
  if (!selected?.is_dir) return;
  selectedTreePath = path;
  inlineRenamePath = path;
  renderFileTree();
  requestAnimationFrame(() => {
    const input = document.querySelector(`.tree-rename-input[data-path="${CSS.escape(path)}"]`);
    input?.focus();
    input?.select();
  });
}

function cancelInlineRenameFolder() {
  inlineRenamePath = null;
  renderFileTree();
}

async function commitInlineRenameFolder(oldPath, nextName) {
  const trimmedName = (nextName || '').trim();
  inlineRenamePath = null;
  if (!trimmedName) {
    renderFileTree();
    return;
  }
  const newPath = joinPath(getParentDir(oldPath), trimmedName);
  if (newPath === oldPath) {
    renderFileTree();
    return;
  }
  await renameVaultItem(oldPath, newPath, true);
}

function currentTargetFolder() {
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

function renderProjectFolderOptions() {
  const select = document.getElementById('obsidian-project-folder');
  if (!select) return;
  const previous = select.value;
  const target = currentTargetFolder();
  const folders = projectFolderOptions();
  select.innerHTML = folders.map(path => {
    const label = path || 'Vault root';
    return `<option value="${escapeHtml(path)}">${escapeHtml(label)}</option>`;
  }).join('') + `<option value="${NEW_PROJECT_FOLDER_SENTINEL}">${escapeHtml('Create new project folder')}</option>`;
  if (previous && [...select.options].some(option => option.value === previous)) {
    select.value = previous;
  } else if ([...select.options].some(option => option.value === target)) {
    select.value = target;
  } else {
    select.value = '';
  }
}

function resolveProjectTargetFolder() {
  const folderSelect = document.getElementById('obsidian-project-folder');
  const title = document.getElementById('obsidian-project-title')?.value || '';
  const selected = folderSelect?.value || '';
  if (selected === NEW_PROJECT_FOLDER_SENTINEL) {
    const parent = currentTargetFolder();
    return `${NEW_PROJECT_FOLDER_SENTINEL}::${parent || ''}`;
  }
  return selected || '';
}

function updateProjectTargetLabel() {
  const selected = document.getElementById('obsidian-project-folder')?.value || '';
  const title = document.getElementById('obsidian-project-title')?.value || '';
  const label = document.getElementById('obsidian-project-target');
  if (!label) return;
  if (selected === NEW_PROJECT_FOLDER_SENTINEL) {
    const parent = currentTargetFolder();
    const slug = slugifyProjectTitle(title);
    label.textContent = parent ? `Target: ${parent}/${slug}` : `Target: ${slug}`;
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
  const applyBtn = document.getElementById('obsidian-project-apply');
  if (applyBtn) applyBtn.disabled = true;
  const previewPanel = document.getElementById('obsidian-project-preview-panel');
  if (previewPanel) previewPanel.innerHTML = '';
  if (clearDraft) clearGameDevDraft();
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
  const requiresDestination = !['memory_only', 'discard'].includes(action);
  destinationField?.classList.toggle('hidden', !requiresDestination);
  if (saveTo) saveTo.disabled = !requiresDestination;
  if (!requiresDestination) closeMemoryDestinationPicker();
}

function handleMemoryActionChanged() {
  const action = document.getElementById('obsidian-memory-action')?.value || 'save_to_obsidian';
  if (action === 'save_to_obsidian' && memoryReviewDestination.type === 'note') {
    clearMemoryReviewDestination();
  }
  if (action === 'append_to_note' && memoryReviewDestination.type === 'folder') {
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

function normalizeMemoryTag(value) {
  const clean = String(value || '').trim().replace(/^#+/, '').replace(/\s+/g, '-');
  return clean ? `#${clean}` : '';
}

function renderMemoryTagChips() {
  const chips = document.getElementById('obsidian-memory-tag-chips');
  if (!chips) return;
  chips.innerHTML = memoryReviewTags.map(tag => `
    <button type="button" class="obsidian-memory-tag-chip" data-memory-tag="${escapeHtml(tag)}">
      <span>${escapeHtml(tag)}</span>
      <span aria-hidden="true">x</span>
    </button>
  `).join('');
  chips.querySelectorAll('[data-memory-tag]').forEach(btn => {
    btn.addEventListener('click', () => {
      memoryReviewTags = memoryReviewTags.filter(tag => tag !== btn.dataset.memoryTag);
      renderMemoryTagChips();
      updateMemoryTagSuggestions();
      invalidateMemoryReviewPreview({ clearPanel: true });
    });
  });
}

function addMemoryTag(value) {
  const tag = normalizeMemoryTag(value);
  if (!tag || memoryReviewTags.includes(tag)) return;
  memoryReviewTags.push(tag);
  renderMemoryTagChips();
  const input = document.getElementById('obsidian-memory-tag-entry');
  if (input) input.value = '';
  hideMemoryTagMenu();
  invalidateMemoryReviewPreview({ clearPanel: true });
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
    .filter(tag => !memoryReviewTags.includes(`#${tag.name}`))
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
  if (e.key === 'Backspace' && !input.value && memoryReviewTags.length) {
    memoryReviewTags.pop();
    renderMemoryTagChips();
    invalidateMemoryReviewPreview({ clearPanel: true });
    return;
  }
  if (e.key === 'Escape') {
    hideMemoryTagMenu();
  }
}

async function showProjectPlanner() {
  const planner = document.getElementById('obsidian-project-planner');
  if (!planner) return;
  projectPlanPreview = null;
  clearGameDevDraft();
  document.getElementById('obsidian-editor-container')?.classList.add('hidden');
  document.getElementById('obsidian-empty-state')?.classList.add('hidden');
  planner.classList.remove('hidden');
  renderProjectFolderOptions();
  await loadProjectTemplateOptions().catch((e) => {
    console.error('Failed to load project templates:', e);
    showToast(e.message || 'Failed to load project templates');
  });
  updateProjectTargetLabel();
  document.getElementById('obsidian-project-preview-panel').innerHTML = '';
  document.getElementById('obsidian-project-apply').disabled = true;
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
  memoryReviewPreview = null;
  clearMemoryReviewDestination();
  document.getElementById('obsidian-project-planner')?.classList.add('hidden');
  document.getElementById('obsidian-editor-container')?.classList.add('hidden');
  document.getElementById('obsidian-empty-state')?.classList.add('hidden');
  panel.classList.remove('hidden');
  const actionSelect = document.getElementById('obsidian-memory-action');
  if (actionSelect) actionSelect.value = 'save_to_obsidian';
  renderMemoryTagChips();
  updateMemoryReviewActionUi();
  document.getElementById('obsidian-memory-preview-panel').innerHTML = '';
  document.getElementById('obsidian-memory-apply').disabled = true;
}

function closeMemoryReview() {
  document.getElementById('obsidian-memory-review-panel')?.classList.add('hidden');
  if (currentNotePath) {
    document.getElementById('obsidian-editor-container')?.classList.remove('hidden');
  } else {
    document.getElementById('obsidian-empty-state')?.classList.remove('hidden');
  }
}

function renderProjectPlanPreviewLegacy(plan) {
  const panel = document.getElementById('obsidian-project-preview-panel');
  const applyBtn = document.getElementById('obsidian-project-apply');
  if (!panel || !applyBtn) return;
  const conflicts = plan.conflicts || [];
  const warnings = plan.warnings || [];
  const files = plan.files || [];
  const tags = plan.new_tags || [];
  const relationships = plan.relationships || [];
  panel.innerHTML = `
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
  applyBtn.disabled = projectPlanPreviewStreaming || conflicts.length > 0 || files.length === 0;
}

function renderProjectPlanPreview(plan) {
  const panel = document.getElementById('obsidian-project-preview-panel');
  const applyBtn = document.getElementById('obsidian-project-apply');
  if (!panel || !applyBtn) return;
  const conflicts = plan.conflicts || [];
  const warnings = plan.warnings || [];
  const files = plan.files || [];
  const tags = plan.new_tags || [];
  const relationships = plan.relationships || [];
  panel.innerHTML = `
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
      ${files.map((file, index) => `
        <details class="obsidian-project-file obsidian-project-file-editor" data-project-file="${escapeHtml(file.path)}" data-project-index="${index}" open>
          <summary>
            <strong>${escapeHtml(file.path)}</strong>
            <span>${escapeHtml(file.type)} - ${escapeHtml(file.status)}</span>
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
      `).join('')}
    </div>
    ${tags.length ? `<div class="obsidian-project-new-tags">
      <strong>New tags</strong>
      ${tags.map(item => `<div><code>${escapeHtml(item.tag)}</code> ${escapeHtml(item.reason)}</div>`).join('')}
    </div>` : ''}
  `;
  applyBtn.disabled = conflicts.length > 0 || files.length === 0;
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
  const relationships = plan.relationships || [];
  const firstFile = files[0] || null;
  const titleWasProvided = Boolean((plan.candidate?.title || '').trim());
  const destination = firstFile?.path
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
      renderProjectPlanPreview(projectPlanPreview);
    }
    return;
  }
  if (eventName === 'file_done') {
    setProjectPlanFile(Number(payload?.index), payload?.file);
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

async function previewProjectPlanStream(payload) {
  const res = await fetch('/api/plugins/obsidian/project-plan/preview-stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(payload),
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
  const target_folder = resolveProjectTargetFolder();
  const title = document.getElementById('obsidian-project-title')?.value || '';
  const kind = document.getElementById('obsidian-project-kind')?.value || 'software';
  const description = document.getElementById('obsidian-project-description')?.value || '';
  const custom_focus = document.getElementById('obsidian-project-focus')?.value || '';
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
  document.getElementById('obsidian-project-apply').disabled = true;
  projectPlanPreviewStreaming = true;
  const payload = {
    target_folder,
    title,
    kind,
    description,
    custom_focus,
    generate_content: true,
    approved_concept: approvedConcept,
    concept_approved: conceptApproved,
  };
  try {
    await previewProjectPlanStream(payload);
  } catch (e) {
    console.error('Project preview failed:', e);
    projectPlanPreview = null;
    projectPlanPreviewStreaming = false;
    if (panel) panel.innerHTML = `<div class="obsidian-project-conflicts">${escapeHtml(e.message || 'Project preview failed')}</div>`;
    document.getElementById('obsidian-project-apply').disabled = true;
  } finally {
    stopLoadingAnimation();
    projectPlanPreviewStreaming = false;
    if (projectPlanPreview) renderProjectPlanPreview(projectPlanPreview);
    if (previewBtn) previewBtn.disabled = false;
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
  if (!['memory_only', 'discard'].includes(action) && !memoryReviewDestination.type) {
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
    const confirmed = await styledConfirm('Apply this memory review to the vault?', { confirmText: 'Apply' });
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
  const confirmed = await styledConfirm('Create this project structure in the vault?', { confirmText: 'Create' });
  if (!confirmed) return;
  try {
    const res = await fetch('/api/plugins/obsidian/project-plan/apply', {
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
    await loadVaultFiles();
    closeProjectPlanner();
    const firstFile = data.created_files?.[0] || projectPlanPreview.files?.[0]?.path;
    if (firstFile) await openNote(firstFile);
    setViewMode('graph');
    showToast('Project structure created');
  } catch (e) {
    console.error('Project apply failed:', e);
    showToast(e.message || 'Project apply failed');
  }
}

async function renderGraphView() {
  const graph = document.getElementById('obsidian-graph-view');
  if (!graph) return;

  destroyGraphCytoscape();
  graph.innerHTML = '<div class="obsidian-graph-empty">Building graph...</div>';

  let prepared;
  try {
    prepared = prepareGraphData(await fetchGraphData());
  } catch (e) {
    console.error('Failed to build graph:', e);
    graph.innerHTML = '<div class="obsidian-graph-empty">Unable to build graph.</div>';
    return;
  }

  if (!prepared.markdownNodes.length) {
    graph.innerHTML = '<div class="obsidian-graph-empty">No markdown notes to graph yet.</div>';
    return;
  }

  const preferredRenderer = preferredGraphRenderer();
  renderGraphShell(graph, prepared, preferredRenderer);

  if (preferredRenderer === OBSIDIAN_GRAPH_RENDERER_SVG) {
    renderSvgGraphFallback(graph, prepared);
    return;
  }

  try {
    await renderCytoscapeGraph(graph, prepared);
  } catch (e) {
    console.warn('Cytoscape graph failed, falling back to SVG:', e);
    renderGraphShell(graph, prepared, OBSIDIAN_GRAPH_RENDERER_SVG, 'SVG fallback');
    renderSvgGraphFallback(graph, prepared);
  }
}

async function fetchGraphData() {
  const focus = currentNotePath ? `?focus=${encodeURIComponent(currentNotePath)}` : '';
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
  if (graphCytoscapeInstance) {
    graphCytoscapeInstance.destroy();
    graphCytoscapeInstance = null;
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
  const edges = graphEdgeTypeFilter === 'all'
    ? allEdges
    : allEdges.filter(edge => (edge.type || 'link') === graphEdgeTypeFilter);

  return {
    markdownNodes,
    folderNodes,
    edges,
    edgeTypes,
  };
}

function renderGraphShell(graph, prepared, renderer) {
  const filterOptions = ['all', ...prepared.edgeTypes].map(type => (
    `<option value="${escapeHtml(type)}" ${type === graphEdgeTypeFilter ? 'selected' : ''}>${escapeHtml(type.replace(/_/g, ' '))}</option>`
  )).join('');

  graph.innerHTML = `
    <div class="obsidian-graph-controls">
      <select id="obsidian-graph-filter" title="Filter graph edges">${filterOptions}</select>
    </div>
    <div class="obsidian-graph-canvas" id="obsidian-graph-canvas" data-graph-renderer="${escapeHtml(renderer)}"></div>
  `;

  graph.querySelector('#obsidian-graph-filter')?.addEventListener('change', (e) => {
    graphEdgeTypeFilter = e.target.value || 'all';
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

function cytoscapeElements(prepared) {
  const folderElements = prepared.folderNodes.map(node => {
    const parent = parentFolderForFolder(node.id);
    return {
      data: {
        id: node.id,
        label: node.label || node.id,
        type: 'folder',
        parent: parent || undefined,
      },
      classes: 'obsidian-folder-node',
    };
  });

  const markdownElements = prepared.markdownNodes.map(node => {
    const parent = directFolderForPath(node.id);
    return {
      data: {
        id: node.id,
        label: node.label || node.id.replace(/\.md$/i, '').split('/').pop(),
        type: 'markdown',
        parent: parent || undefined,
        tags: (node.tags || []).join(', '),
      },
      classes: node.id === currentNotePath ? 'obsidian-current-node' : '',
    };
  });

  const edgeElements = prepared.edges.map((edge, index) => ({
    data: {
      id: `edge-${index}-${edge.source}-${edge.target}-${edge.type || 'link'}`,
      source: edge.source,
      target: edge.target,
      type: edge.type || 'link',
      reason: edge.reason || edge.type || 'link',
      weight: edge.weight || '',
    },
    classes: `edge-${edge.type || 'link'}`,
  }));

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
  ];
}

async function renderCytoscapeGraph(graph, prepared) {
  const canvas = graph.querySelector('#obsidian-graph-canvas');
  if (!canvas) return;
  const cytoscape = await loadCytoscape();

  graphCytoscapeInstance = cytoscape({
    container: canvas,
    elements: cytoscapeElements(prepared),
    layout: {
      name: 'cose',
      animate: false,
      fit: true,
      padding: 48,
      nodeRepulsion: 9000,
      idealEdgeLength: 95,
      componentSpacing: 72,
    },
    style: cytoscapeStyle(canvas),
    wheelSensitivity: 0.22,
    minZoom: 0.28,
    maxZoom: 2.4,
  });

  graphCytoscapeInstance.on('tap', 'node[type = "markdown"]', (event) => {
    activateGraphNode(event.target.id());
  });
  graphCytoscapeInstance.on('mouseover', 'node, edge', (event) => {
    const data = event.target.data();
    canvas.title = data.reason || data.tags || data.id || '';
  });
  graphCytoscapeInstance.on('mouseout', 'node, edge', () => {
    canvas.title = '';
  });
}

function renderSvgGraphFallback(graph, prepared) {
  const canvas = graph.querySelector('#obsidian-graph-canvas');
  if (!canvas) return;

  const nodes = prepared.markdownNodes;
  const edges = prepared.edges;
  const width = 900;
  const height = 560;
  const cx = width / 2;
  const cy = height / 2;
  const radius = Math.max(90, Math.min(width, height) * 0.34);
  const positions = new Map();

  nodes.forEach((node, index) => {
    const path = node.id;
    const angle = (Math.PI * 2 * index) / Math.max(nodes.length, 1) - Math.PI / 2;
    const linkedCount = edges.filter(edge => edge.source === path || edge.target === path).length;
    const r = path === currentNotePath ? radius * 0.55 : radius + (linkedCount % 3) * 22;
    positions.set(path, {
      x: cx + Math.cos(angle) * r,
      y: cy + Math.sin(angle) * r
    });
  });

  const edgeSvg = edges.map(edge => {
    const from = positions.get(edge.source);
    const to = positions.get(edge.target);
    if (!from || !to) return '';
    const type = escapeHtml(edge.type || 'link');
    const reason = escapeHtml(edge.reason || type);
    return `<line class="obsidian-graph-edge edge-${type}" x1="${from.x.toFixed(1)}" y1="${from.y.toFixed(1)}" x2="${to.x.toFixed(1)}" y2="${to.y.toFixed(1)}"><title>${reason}</title></line>`;
  }).join('');

  const nodeSvg = nodes.map(node => {
    const path = node.id;
    const pos = positions.get(path);
    const isCurrent = path === currentNotePath;
    const label = escapeHtml(path.replace(/\.md$/i, '').split('/').pop());
    const safePath = escapeHtml(path);
    const tags = escapeHtml((node.tags || []).slice(0, 4).join(', '));
    const classes = [
      'obsidian-graph-node',
      isCurrent ? 'current' : '',
    ].filter(Boolean).join(' ');
    return `
      <g class="${classes}" data-path="${safePath}" tabindex="0" role="button">
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
    pathHeader.textContent = result.path;
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

    item.addEventListener('click', () => {
      openNote(result.path);
    });
    container.appendChild(item);
  });
}

// ─── Event Listeners ─────────────────────────────────────────────────────────

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
  document.getElementById('obsidian-panel-minimize')?.addEventListener('click', () => {
    closePanel();
    showToast('Obsidian panel minimized');
  });

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
  document.getElementById('obsidian-memory-review')?.addEventListener('click', showMemoryReview);
  document.getElementById('obsidian-memory-close')?.addEventListener('click', closeMemoryReview);
  document.getElementById('obsidian-memory-preview')?.addEventListener('click', previewMemoryReview);
  document.getElementById('obsidian-memory-apply')?.addEventListener('click', applyMemoryReview);
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
  memoryTagEntry?.addEventListener('focus', updateMemoryTagSuggestions);
  memoryTagEntry?.addEventListener('keydown', handleMemoryTagKey);
  memoryTagEntry?.addEventListener('blur', () => {
    setTimeout(hideMemoryTagMenu, 120);
  });
  document.getElementById('obsidian-memory-tags')?.addEventListener('click', () => {
    memoryTagEntry?.focus();
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('#obsidian-memory-destination-field')) {
      closeMemoryDestinationPicker();
    }
  });

  // Refresh
  document.getElementById('obsidian-refresh')?.addEventListener('click', async () => {
    await loadVaultFiles();
    if (currentViewMode === 'graph') {
      renderGraphView();
    }
    showToast('Vault refreshed');
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

  // Rename
  document.getElementById('obsidian-rename-note')?.addEventListener('click', promptRenameSelectedItem);

  // Delete
  document.getElementById('obsidian-delete-note')?.addEventListener('click', async () => {
    if (!currentNotePath) return;
    const confirm = await styledConfirm('Are you sure you want to delete this note?', { confirmText: 'Delete', danger: true });
    if (!confirm) return;

    try {
      const res = await fetch(`/api/plugins/obsidian/file?path=${encodeURIComponent(currentNotePath)}`, {
        method: 'DELETE'
      });
      if (res.ok) {
        showToast('Note deleted');
        currentNotePath = null;
        document.getElementById('obsidian-editor-container').classList.add('hidden');
        document.getElementById('obsidian-empty-state').classList.remove('hidden');
        await loadVaultFiles();
      } else {
        showToast('Failed to delete note');
      }
    } catch (e) {
      console.error(e);
      showToast('Error deleting note');
    }
  });

  // Autosave + Preview
  const textarea = document.getElementById('obsidian-textarea');
  textarea?.addEventListener('input', () => {
    clearTimeout(autosaveTimeout);
    const content = textarea.value;
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
  const standalone = isStandaloneMode();
  document.body.classList.toggle('obsidian-standalone', standalone);
  injectUIElements();
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
