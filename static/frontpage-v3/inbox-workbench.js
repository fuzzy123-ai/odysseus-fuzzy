/* Harbor One V3 Universal Inbox focused workbench shell. */
(function () {
  'use strict';

  const root = document.querySelector('[data-inbox-workbench-root]');
  const stateModule = window.HarborInboxState;
  const apiModule = window.HarborInboxApi;
  const previewModule = window.HarborInboxPreview;
  const bridgeModule = window.HarborInboxDocumentBridge;
  if (!root || !stateModule || !apiModule) return;

  const params = new URLSearchParams(window.location.search);
  const requestedSource = String(params.get('inboxSource') || 'live').toLowerCase();
  const requestedScenario = String(params.get('inboxScenario') || 'fixture').toLowerCase();
  const visualState = requestedSource === 'fixture'
    ? String(params.get('inboxWorkbenchState') || 'selected').toLowerCase()
    : 'selected';
  const api = new apiModule.InboxApiClient();
  const model = new stateModule.InboxReadModel({ apiClient: api, onChange: render });
  const view = {
    selectedRef: '',
    search: '',
    documentMode: 'original',
    mobilePanel: 'document',
    bridgeState: null,
    documentLink: '',
    route: { sequence: 0, controller: null, sourceRef: '', state: 'idle', result: null, error: '' }
  };
  let previewController = null;
  let bridge = null;
  let bridgeSourceRef = '';
  const documentModes = ['original', 'extraction', 'working-copy', 'difference'];
  const mobilePanels = ['source', 'document', 'details'];

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function list(value) {
    return Array.isArray(value) ? value : [];
  }

  function itemsFor(state) {
    return list(state && state.list && state.list.items);
  }

  function selectedItem(state) {
    const items = itemsFor(state);
    return items.find(item => item.source_ref === view.selectedRef) || items[0] || null;
  }

  function modeCopy(mode) {
    const copies = {
      fixture: ['Preview fixture', 'Sample owner-scoped list · not live evidence'],
      live: ['Live owner scope', 'Authoritative Universal Inbox read'],
      stale: ['Refresh required', 'Showing the last bounded read'],
      empty: ['Inbox ready', 'No owner-scoped documents yet'],
      unauthorized: ['Authentication required', 'Owner-scoped content remains closed'],
      unavailable: ['Inbox unavailable', 'No preview fixture was substituted'],
      error: ['Response rejected', 'Unsafe or invalid data was not rendered'],
      loading: ['Connecting', 'Reading owner-scoped Inbox metadata']
    };
    return copies[mode] || copies.error;
  }

  function statusTone(state, item) {
    if (visualState === 'review' || (item && (item.metadata.review_required || item.metadata.blocked))) {
      return 'review';
    }
    if (state.mode === 'stale') return 'stale';
    if (['unauthorized', 'unavailable', 'error'].includes(state.mode)) return 'blocked';
    return state.mode === 'live' ? 'live' : 'fixture';
  }

  function render(state) {
    const nextItem = selectedItem(state);
    if (state.mode !== 'live' || (bridge && (!nextItem || bridgeSourceRef !== nextItem.source_ref))) {
      destroyPreview();
      destroyBridge();
      view.documentMode = 'original';
    }
    root.dataset.inboxMode = state.mode;
    root.dataset.mobilePanel = view.mobilePanel;
    root.dataset.visualState = visualState;
    const windowState = document.querySelector('[data-inbox-window-state]');
    if (windowState) windowState.textContent = modeCopy(state.mode)[0].toLowerCase();

    if (state.mode === 'loading') {
      root.innerHTML = renderLoading();
      return;
    }
    if (['empty', 'unauthorized', 'unavailable', 'error'].includes(state.mode)) {
      destroyPreview();
      destroyBridge();
      root.innerHTML = renderBoundaryState(state);
      bindEvents(state);
      return;
    }

    const item = nextItem;
    if (item && !view.selectedRef) view.selectedRef = item.source_ref;
    root.innerHTML = renderWorkbench(state, item);
    bindEvents(state);
    mountPreview(state, item);
    mountBridge(state, item);
  }

  function renderLoading() {
    return `
      <section class="uix-shell uix-loading" aria-label="Loading Universal Inbox">
        <div class="uix-source-bar">
          <span class="uix-source-signal" aria-hidden="true"></span>
          <span><strong>Connecting</strong><small>Reading owner-scoped Inbox metadata</small></span>
        </div>
        <div class="uix-loading-layout" role="status" aria-live="polite">
          <span class="uix-skeleton rail"></span>
          <span class="uix-skeleton document"></span>
          <span class="uix-skeleton details"></span>
          <strong>Opening the document workbench…</strong>
        </div>
      </section>
    `;
  }

  function renderBoundaryState(state) {
    const messages = {
      empty: ['No documents in this Inbox', 'Drop or upload a document, then return here to inspect it.'],
      unauthorized: ['Sign in to open your Inbox', 'Names, metadata, and content stay hidden until ownership is verified.'],
      unavailable: ['Universal Inbox is unavailable', 'The live source could not be reached. No sample data replaced it.'],
      error: ['Inbox response was rejected', 'The workbench stopped before rendering unsafe or malformed data.']
    };
    const [title, detail] = messages[state.mode] || messages.error;
    const [label, sourceDetail] = modeCopy(state.mode);
    return `
      <section class="uix-shell" aria-label="Universal Inbox document workbench">
        <header class="uix-source-bar" data-tone="${escapeHtml(statusTone(state))}">
          <span class="uix-source-signal" aria-hidden="true"></span>
          <span><strong>${escapeHtml(label)}</strong><small>${escapeHtml(sourceDetail)}</small></span>
          <span class="uix-boundary-label">Original protected</span>
        </header>
        <div class="uix-boundary-state" role="${state.mode === 'error' ? 'alert' : 'status'}">
          <span class="uix-boundary-mark" aria-hidden="true">${state.mode === 'empty' ? '0' : '!'}</span>
          <div>
            <h2>${escapeHtml(title)}</h2>
            <p>${escapeHtml(detail)}</p>
          </div>
          <div class="uix-boundary-actions">
            <button class="uix-button primary" type="button" data-inbox-refresh>Try again</button>
            ${requestedSource === 'fixture' ? '<button class="uix-button" type="button" data-inbox-load-preview>Load labeled preview</button>' : ''}
          </div>
        </div>
        <p class="uix-live-region" role="status" aria-live="polite" aria-atomic="true" data-inbox-live-region>${escapeHtml(title)}</p>
      </section>
    `;
  }

  function renderWorkbench(state, item) {
    const [label, sourceDetail] = modeCopy(state.mode);
    const tone = statusTone(state, item);
    const filteredItems = itemsFor(state).filter(candidate => {
      const query = view.search.trim().toLowerCase();
      return !query || String(candidate.display_name || '').toLowerCase().includes(query);
    });
    const snapshot = state.snapshot || {};
    return `
      <section class="uix-shell" aria-label="Universal Inbox document workbench">
        <header class="uix-source-bar" data-tone="${escapeHtml(tone)}">
          <span class="uix-source-signal" aria-hidden="true"></span>
          <span><strong>${escapeHtml(label)}</strong><small>${escapeHtml(sourceDetail)}</small></span>
          <span class="uix-source-count">${escapeHtml(snapshot.total_count || filteredItems.length)} document${Number(snapshot.total_count || filteredItems.length) === 1 ? '' : 's'}</span>
          <span class="uix-boundary-label">Original protected</span>
          <button class="uix-icon-button" type="button" data-inbox-refresh aria-label="Refresh Inbox" title="Refresh Inbox">↻</button>
        </header>
        ${renderActionSequence(tone)}
        ${renderMobileTabs()}
        <div class="uix-layout">
          ${renderSourcePanel(filteredItems, item)}
          ${renderDocumentPanel(item, tone, state)}
          ${renderDetailsPanel(item, tone, state)}
        </div>
        <p class="uix-live-region" role="status" aria-live="polite" aria-atomic="true" data-inbox-live-region>${escapeHtml(liveMessage(item, tone))}</p>
      </section>
    `;
  }

  function renderActionSequence(tone) {
    const states = visualState === 'export-success'
      ? ['complete', 'complete', 'complete', 'success']
      : visualState === 'dirty' || visualState === 'saving'
        ? ['complete', 'available', visualState, 'locked']
        : tone === 'review'
          ? ['review', 'locked', 'locked', 'locked']
          : ['current', 'available', 'available', 'locked'];
    const labels = ['Inspect', 'Suggest route', 'Working copy', 'Export'];
    const details = ['Selected source', 'Dry run only', 'Versioned document', 'Browser download'];
    return `
      <nav class="uix-action-sequence" aria-label="Document workflow">
        <ol>
          ${labels.map((label, index) => `
            <li data-step-state="${escapeHtml(states[index])}">
              <span class="uix-step-mark" aria-hidden="true">${index + 1}</span>
              <span><strong>${escapeHtml(label)}</strong><small>${escapeHtml(details[index])}</small></span>
            </li>
          `).join('')}
        </ol>
      </nav>
    `;
  }

  function renderMobileTabs() {
    const labels = { source: 'Inbox', document: 'Document', details: 'Details' };
    return `
      <div class="uix-mobile-tabs" role="tablist" aria-label="Workbench panels">
        ${mobilePanels.map(panel => `
          <button type="button" role="tab" data-mobile-panel-target="${panel}"
            aria-controls="uix-panel-${panel}" aria-selected="${view.mobilePanel === panel}" tabindex="${view.mobilePanel === panel ? '0' : '-1'}">
            ${labels[panel]}
          </button>
        `).join('')}
      </div>
    `;
  }

  function renderSourcePanel(items, selected) {
    return `
      <aside class="uix-panel uix-source-panel" id="uix-panel-source" data-workbench-panel="source" aria-label="Owner-scoped Inbox documents">
        <header class="uix-panel-heading">
          <span><strong>Inbox documents</strong><small>Owner-scoped source list</small></span>
          <button class="uix-mobile-return" type="button" data-return-document>Back to document</button>
        </header>
        <label class="uix-search">
          <span aria-hidden="true">⌕</span>
          <input type="search" value="${escapeHtml(view.search)}" placeholder="Find a document" aria-label="Find a document" data-inbox-search>
        </label>
        <div class="uix-item-list" role="listbox" aria-label="Inbox documents">
          ${items.map(item => renderItem(item, selected)).join('') || '<p class="uix-no-results">No documents match this search.</p>'}
        </div>
        <div class="uix-source-note">
          <strong>Source boundary</strong>
          <p>Names appear only after owner authorization. Paths and raw content never enter the list model.</p>
        </div>
      </aside>
    `;
  }

  function renderItem(item, selected) {
    const active = Boolean(selected && item.source_ref === selected.source_ref);
    const suffix = String(item.metadata && item.metadata.suffix || 'doc').replace('.', '').toUpperCase();
    const state = visualState === 'review'
      ? 'Needs review'
      : item.metadata && item.metadata.blocked
        ? 'Blocked'
        : item.metadata && item.metadata.review_required
          ? 'Needs review'
          : 'Ready to inspect';
    return `
      <button class="uix-item-row" type="button" role="option" data-inbox-item="${escapeHtml(item.source_ref)}"
        aria-selected="${active}" aria-current="${active ? 'true' : 'false'}" data-item-tone="${state === 'Ready to inspect' ? 'ready' : 'review'}">
        <span class="uix-file-kind" aria-hidden="true">${escapeHtml(suffix || 'DOC')}</span>
        <span><strong>${escapeHtml(item.display_name)}</strong><small>${escapeHtml(state)}</small></span>
        <span class="uix-item-state" aria-hidden="true">${state === 'Ready to inspect' ? '✓' : '!'}</span>
      </button>
    `;
  }

  function renderDocumentPanel(item, tone, state) {
    const status = visualState === 'dirty' ? 'Dirty'
      : visualState === 'saving' ? 'Saving…'
        : visualState === 'export-success' ? 'Export ready'
          : tone === 'review' ? 'Review required'
            : 'Source selected';
    const suffix = String(item && item.metadata && item.metadata.suffix || 'document').replace('.', '').toUpperCase();
    return `
      <main class="uix-panel uix-document-panel" id="uix-panel-document" data-workbench-panel="document" aria-label="Selected document">
        <header class="uix-document-heading" tabindex="-1" data-document-heading>
          <span class="uix-document-kind">${escapeHtml(suffix)}</span>
          <span><strong>${escapeHtml(item && item.display_name || 'No document selected')}</strong><small>${escapeHtml(status)} · original remains unchanged</small></span>
          <span class="uix-document-status" data-document-status="${escapeHtml(visualState)}">${escapeHtml(status)}</span>
        </header>
        <div class="uix-document-tabs" role="tablist" aria-label="Document views">
          ${documentModes.map(mode => {
            const enabled = enabledDocumentModes().includes(mode);
            const active = enabled && view.documentMode === mode;
            const label = { original: 'Original', extraction: 'Extraction', 'working-copy': 'Working copy', difference: 'Difference' }[mode];
            return `<button type="button" role="tab" data-document-mode="${mode}" aria-selected="${active}"
              aria-controls="uix-document-stage" tabindex="${active ? '0' : '-1'}" ${enabled ? '' : 'disabled aria-disabled="true" title="Create an authorized working copy first"'}>${label}</button>`;
          }).join('')}
        </div>
        <section class="uix-document-stage" id="uix-document-stage" role="tabpanel" aria-label="Document preview status">
          <div data-preview-root></div>
        </section>
      </main>
    `;
  }

  function renderDetailsPanel(item, tone, state) {
    const metadata = item && item.metadata || {};
    const review = tone === 'review';
    const routeCapability = routeCapabilityFor(item, stateForRouteCapability());
    return `
      <aside class="uix-panel uix-details-panel" id="uix-panel-details" data-workbench-panel="details" aria-label="Flow, provenance and actions">
        <header class="uix-panel-heading">
          <span><strong>Flow & provenance</strong><small>Server-authoritative boundaries</small></span>
          <button class="uix-mobile-return" type="button" data-return-document>Back to document</button>
        </header>
        ${review ? '<div class="uix-risk-banner" role="alert"><strong>Review required</strong><span>Inspection stops before content or write actions.</span></div>' : ''}
        <section class="uix-detail-section" aria-labelledby="uix-flow-title">
          <h2 id="uix-flow-title">Current flow</h2>
          <ol class="uix-flow-list">
            <li data-flow-state="done"><span>✓</span><span><strong>Received</strong><small>Owner scope verified</small></span></li>
            <li data-flow-state="${review ? 'review' : 'current'}"><span>${review ? '!' : '2'}</span><span><strong>Inspect</strong><small>${review ? 'Risk decision needed' : 'Ready for bounded preview'}</small></span></li>
            <li data-flow-state="locked"><span>3</span><span><strong>Route</strong><small>Dry run only</small></span></li>
            <li data-flow-state="locked"><span>4</span><span><strong>Write</strong><small>Live gate closed</small></span></li>
          </ol>
        </section>
        <section class="uix-detail-section" aria-labelledby="uix-provenance-title">
          <h2 id="uix-provenance-title">Provenance</h2>
          <dl class="uix-provenance">
            <div><dt>Source</dt><dd>${escapeHtml(item && item.source_kind || 'upload')}</dd></div>
            <div><dt>Family</dt><dd>${escapeHtml(metadata.family || 'document')}</dd></div>
            <div><dt>Policy</dt><dd>${item && item.capability && item.capability.server_authoritative ? 'Server authoritative' : 'Unavailable'}</dd></div>
            <div><dt>Original</dt><dd>Immutable</dd></div>
          </dl>
        </section>
        <section class="uix-detail-section uix-actions" aria-labelledby="uix-actions-title">
          <h2 id="uix-actions-title">Available now</h2>
          <button class="uix-button primary" type="button" data-focus-document ${review ? 'disabled' : ''}>Review selection</button>
          <button class="uix-button" type="button" data-route-dry-run ${routeCapability.enabled ? '' : 'disabled aria-disabled="true"'} title="${escapeHtml(routeCapability.note)}">Suggest route · dry run</button>
          ${renderRouteDryRunStatus(routeCapability)}
          <div data-working-copy-actions>${renderWorkingCopyActions(item, state)}</div>
          <button class="uix-button" type="button" disabled aria-disabled="true" title="UIX-NEXTCLOUD-LIVE-WRITE">Apply route live</button>
          <p>Live apply is disabled by UIX-NEXTCLOUD-LIVE-WRITE.</p>
          <p>No copy, move, delete, overwrite, provider, or memory write is available here.</p>
        </section>
      </aside>
    `;
  }

  function allowedAction(item, name) {
    const capability = item && item.capability;
    if (!capability || typeof capability !== 'object' || Array.isArray(capability)
      || capability.schema !== 'odysseus.universal_inbox.workbench_capability.v1'
      || capability.server_authoritative !== true || capability.owner_authorized !== true
      || capability.original_immutable !== true || capability.working_copy_versioned !== true
      || capability.raw_content_visible !== false || capability.absolute_path_visible !== false
      || capability.live_write_authorized !== false || !Array.isArray(capability.actions)) return false;
    const action = capability.actions.find(candidate => candidate && candidate.action === name);
    return Boolean(action && action.state === 'allowed' && action.mutates_original === false
      && action.performs_live_write === false && Array.isArray(action.reason_codes));
  }

  function workingCopyReady() {
    return Boolean(view.bridgeState && view.bridgeState.workingCopyId
      && (view.bridgeState.saveState === 'ready' || view.bridgeState.saveState === 'saved')
      && !view.bridgeState.dirty && !view.bridgeState.conflict && view.documentLink);
  }

  function enabledDocumentModes() {
    return workingCopyReady() ? ['original', 'working-copy', 'difference'] : ['original'];
  }

  function renderWorkingCopyActions(item, state) {
    if (state.mode !== 'live') {
      return '<p class="uix-route-status">Working-copy actions require a live owner-scoped selection; fixtures never create documents.</p>';
    }
    if (!allowedAction(item, 'create_working_copy')) {
      return '<p class="uix-route-status">Server policy has not authorized a working copy for this selection.</p>';
    }
    const creating = view.bridgeState && view.bridgeState.saveState === 'creating';
    const ready = workingCopyReady();
    const downloading = view.bridgeState && view.bridgeState.exportState === 'downloading';
    const link = ready && view.documentLink
      ? `<a class="uix-button primary" href="${escapeHtml(view.documentLink)}" data-document-link>Open existing Document UI</a>`
      : '';
    return `
      <button class="uix-button" type="button" data-create-working-copy ${creating || ready ? 'disabled aria-disabled="true"' : ''}>Open working copy</button>
      ${ready ? `
        ${link}
        <button class="uix-button" type="button" data-download-original ${downloading ? 'disabled aria-disabled="true"' : ''}>Download original</button>
        <button class="uix-button" type="button" data-export-working-copy ${downloading ? 'disabled aria-disabled="true"' : ''}>Export working copy</button>
        <a class="uix-button" href="${escapeHtml(view.documentLink)}" data-document-history-link>Open document history</a>
      ` : ''}
      <p class="uix-route-status" data-working-copy-status>${escapeHtml(workingCopyStatus())}</p>
    `;
  }

  function workingCopyStatus() {
    if (!view.bridgeState) return 'Create a versioned working copy before local browser actions are available.';
    if (view.bridgeState.saveState === 'creating') return 'Creating an owner-scoped versioned working copy.';
    if (view.bridgeState.saveState === 'conflict') return 'Working copy changed remotely; local export remains closed.';
    if (view.bridgeState.saveState === 'error') return 'Working-copy action was closed safely.';
    if (view.bridgeState.exportState === 'downloading') return 'Local browser download is in progress.';
    if (view.bridgeState.exportState === 'downloaded') return 'Local browser download completed without changing the original.';
    if (view.bridgeState.exportState === 'blocked') return 'Local browser action is blocked until the working copy is ready.';
    if (view.bridgeState.exportState === 'error') return 'Local browser download was closed safely.';
    if (workingCopyReady()) return 'Working copy is ready. Local browser actions do not change the original.';
    return 'Working-copy action is not ready for export.';
  }

  function stateForRouteCapability() {
    return model.getState();
  }

  function routeCapabilityFor(item, state) {
    if (!item) return { enabled: false, note: 'Select an owner-scoped source first.' };
    if (state && state.mode === 'fixture') {
      return { enabled: true, note: 'Synthetic fixture result only; never live evidence.' };
    }
    const actions = list(item.capability && item.capability.actions);
    const action = actions.find(candidate => candidate && candidate.action === 'route_dry_run');
    if (!action || !['allowed', 'review'].includes(action.state)) {
      return { enabled: false, note: 'Server policy does not currently allow a route dry run.' };
    }
    if (view.route.state === 'loading') {
      return { enabled: false, note: 'Server-authoritative route suggestion is loading.' };
    }
    return { enabled: true, note: 'Server-authoritative route suggestion; no write will run.' };
  }

  function renderRouteDryRunStatus(capability) {
    const route = view.route;
    if (route.state === 'loading') {
      return '<p class="uix-route-status" role="status" aria-live="polite" data-route-status>Checking server policy and dry-run route…</p>';
    }
    if (route.state === 'error') {
      return `<div class="uix-risk-banner" role="alert" data-route-status><strong>Route unavailable</strong><span>${escapeHtml(route.error || 'The route dry run could not be verified. No action ran.')}</span></div>`;
    }
    if (route.state === 'result' && route.result) {
      const result = route.result;
      const label = result.status === 'go' ? 'Route suggestion ready'
        : result.status === 'no_go' ? 'Route suggestion blocked' : 'Route needs review';
      const reasons = result.reason_codes.length
        ? result.reason_codes.map(reasonLabel).join(', ')
        : 'none';
      return `
        <div class="uix-risk-banner" role="status" aria-live="polite" data-route-status data-route-policy="${escapeHtml(result.status)}">
          <strong>${escapeHtml(label)}</strong>
          <span>Policy: ${escapeHtml(result.policy_status)} · Confidence: ${escapeHtml(result.confidence.toFixed(2))} · Reasons: ${reasons}</span>
          <span>${result.fixture ? 'Synthetic fixture evidence only; no live request ran.' : 'Server-authoritative dry run; no write ran.'}</span>
        </div>
      `;
    }
    return `<p class="uix-route-status" data-route-status>${escapeHtml(capability.note)}</p>`;
  }

  function reasonLabel(code) {
    const labels = {
      low_confidence: 'Low confidence',
      unknown_domain: 'Domain needs review',
      unknown_document_type: 'Document type needs review',
      synthetic_fixture_review: 'Synthetic preview only',
      route_capability_review: 'Route capability needs review',
      route_capability_blocked: 'Route capability blocked'
    };
    return `${escapeHtml(labels[code] || 'Policy review')} (${escapeHtml(code)})`;
  }

  function liveMessage(item, tone) {
    if (visualState === 'saving') return 'Saving working-copy preview fixture.';
    if (visualState === 'dirty') return 'Working-copy preview fixture has unsaved changes.';
    if (visualState === 'export-success') return 'Browser export preview fixture is ready.';
    if (tone === 'review') return 'Review required. Content and write actions remain closed.';
    return item ? `${item.display_name} selected. Original remains unchanged.` : 'No document selected.';
  }

  function destroyPreview() {
    if (previewController) previewController.destroy();
    previewController = null;
  }

  function destroyBridge() {
    if (bridge) bridge.destroy();
    bridge = null;
    bridgeSourceRef = '';
    view.bridgeState = null;
    view.documentLink = '';
  }

  function mountPreview(state, item) {
    destroyPreview();
    const previewRoot = root.querySelector('[data-preview-root]');
    if (!previewRoot) return;
    if (state.mode !== 'live') {
      previewRoot.innerHTML = '<div class="uix-preview-frame" data-preview-tone="fixture"><span class="uix-preview-glyph" aria-hidden="true">DOC</span><div><h2>Preview fixture remains synthetic</h2><p>No content route is requested for a fixture.</p></div></div>';
      return;
    }
    if (view.documentMode !== 'original') {
      const link = view.documentLink
        ? `<a href="${escapeHtml(view.documentLink)}" data-document-history-link>Open document history</a>`
        : '';
      const copy = view.documentMode === 'difference'
        ? 'Difference history remains in the existing Document UI.'
        : view.documentMode === 'working-copy'
          ? 'The versioned working copy remains in the existing Document UI.'
          : 'Extraction is not available for this selection.';
      previewRoot.innerHTML = `<div class="uix-preview-frame" data-preview-tone="live"><span class="uix-preview-glyph" aria-hidden="true">DOC</span><div><h2>Document handoff</h2><p>${copy}</p>${link}</div></div>`;
      return;
    }
    if (!previewModule || typeof previewModule.InboxPreviewController !== 'function') {
      previewRoot.textContent = 'Preview adapter is unavailable. Content remains closed.';
      return;
    }
    previewController = new previewModule.InboxPreviewController({ root: previewRoot, fetchImpl: api.fetchImpl });
    const previewItem = Object.assign({}, item, {
      capability: Object.assign({}, item && item.capability, {
        // The preview adapter's established P0 label is uppercase; the
        // server workbench contract emits the equivalent lowercase tier.
        mvp_tier: item && item.capability && item.capability.mvp_tier === 'p0' ? 'P0' : item && item.capability && item.capability.mvp_tier
      })
    });
    previewController.load(previewItem);
  }

  function projectBridgeState(next) {
    if (!next || typeof next !== 'object') return null;
    return {
      saveState: typeof next.saveState === 'string' ? next.saveState : 'error',
      workingCopyId: typeof next.workingCopyId === 'string' ? next.workingCopyId : null,
      version_count: Number.isInteger(next.version_count) ? next.version_count : 0,
      dirty: next.dirty === true,
      conflict: typeof next.conflict === 'string' ? next.conflict : null,
      exportState: typeof next.exportState === 'string' ? next.exportState : 'idle',
      exportTarget: typeof next.exportTarget === 'string' ? next.exportTarget : null,
      exportError: typeof next.exportError === 'string' ? next.exportError : null
    };
  }

  function documentHandoff(payload) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)
      || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(payload.id)
      || typeof payload.title !== 'string' || payload.title.length > 512
      || typeof payload.language !== 'string' || payload.language.length > 64
      || !Number.isInteger(payload.version_count) || payload.version_count < 1) {
      throw new Error('invalid_document_handoff');
    }
    view.documentLink = '/#document-' + encodeURIComponent(payload.id);
    updateWorkingCopySurface();
  }

  function mountBridge(state, item) {
    if (state.mode !== 'live' || !item || !allowedAction(item, 'create_working_copy')) {
      destroyBridge();
      return;
    }
    if (bridge && bridgeSourceRef === item.source_ref) return;
    destroyBridge();
    if (!bridgeModule || typeof bridgeModule.create !== 'function') return;
    bridgeSourceRef = item.source_ref;
    bridge = bridgeModule.create({
      fetch: api.fetchImpl,
      documentHandoff,
      onChange: next => {
        view.bridgeState = projectBridgeState(next);
        updateWorkingCopySurface();
      }
    });
  }

  function updateWorkingCopySurface() {
    const slot = root.querySelector('[data-working-copy-actions]');
    const state = model.getState();
    const item = selectedItem(state);
    if (!slot || !item) return;
    if (!syncDocumentModeControls(state)) return;
    slot.innerHTML = renderWorkingCopyActions(item, state);
    bindWorkingCopyEvents(state);
  }

  function syncDocumentModeControls(state) {
    const enabled = enabledDocumentModes();
    if (!enabled.includes(view.documentMode)) {
      view.documentMode = 'original';
      render(state);
      return false;
    }
    root.querySelectorAll('[data-document-mode]').forEach(button => {
      const allowed = enabled.includes(button.dataset.documentMode);
      const active = allowed && view.documentMode === button.dataset.documentMode;
      button.disabled = !allowed;
      button.setAttribute('aria-selected', String(active));
      button.tabIndex = active ? 0 : -1;
      if (allowed) {
        button.removeAttribute('aria-disabled');
        button.removeAttribute('title');
      } else {
        button.setAttribute('aria-disabled', 'true');
        button.setAttribute('title', 'Create an authorized working copy first');
      }
    });
    return true;
  }

  function bindWorkingCopyEvents(state) {
    root.querySelector('[data-create-working-copy]')?.addEventListener('click', () => {
      const item = selectedItem(state);
      if (bridge && item && allowedAction(item, 'create_working_copy')) bridge.createWorkingCopy(item.source_ref).catch(() => {});
    });
    root.querySelector('[data-download-original]')?.addEventListener('click', () => {
      if (bridge && workingCopyReady()) bridge.downloadOriginal().catch(() => {});
    });
    root.querySelector('[data-export-working-copy]')?.addEventListener('click', () => {
      if (bridge && workingCopyReady()) bridge.exportWorkingCopy().catch(() => {});
    });
  }

  function bindEvents(state) {
    root.querySelectorAll('[data-inbox-refresh]').forEach(button => {
      button.addEventListener('click', () => load());
    });
    root.querySelector('[data-inbox-load-preview]')?.addEventListener('click', () => {
      model.load({ source: 'fixture', scenario: 'fixture' });
    });
    root.querySelector('[data-inbox-search]')?.addEventListener('input', event => {
      view.search = event.currentTarget.value;
      render(state);
      const search = root.querySelector('[data-inbox-search]');
      search?.focus();
      if (search) search.setSelectionRange(search.value.length, search.value.length);
    });
    root.querySelectorAll('[data-inbox-item]').forEach(button => {
      button.addEventListener('click', () => selectItem(state, button.dataset.inboxItem));
      button.addEventListener('keydown', event => handleItemKeys(event, state));
    });
    root.querySelectorAll('[data-mobile-panel-target]').forEach(button => {
      button.addEventListener('click', () => setMobilePanel(state, button.dataset.mobilePanelTarget));
      button.addEventListener('keydown', event => handleTabKeys(event, state, mobilePanels, 'mobilePanel', '[data-mobile-panel-target]'));
    });
    root.querySelectorAll('[data-return-document]').forEach(button => {
      button.addEventListener('click', () => setMobilePanel(state, 'document', true));
    });
    root.querySelector('[data-focus-document]')?.addEventListener('click', () => {
      view.mobilePanel = 'document';
      render(state);
      root.querySelector('[data-document-heading]')?.focus();
      announce('Document selection focused. Original preview remains read-only.');
    });
    root.querySelector('[data-route-dry-run]')?.addEventListener('click', () => {
      const item = selectedItem(state);
      if (item) requestRouteDryRun(state, item);
    });
    root.querySelectorAll('[data-document-mode]').forEach(button => {
      button.addEventListener('click', () => {
        if (button.disabled || !enabledDocumentModes().includes(button.dataset.documentMode)) return;
        setDocumentMode(state, button.dataset.documentMode);
      });
      button.addEventListener('keydown', event => {
        if (button.disabled || !enabledDocumentModes().includes(button.dataset.documentMode)) return;
        handleTabKeys(event, state, enabledDocumentModes(), 'documentMode', '[data-document-mode]:not(:disabled)');
      });
    });
    bindWorkingCopyEvents(state);
  }

  function selectItem(state, sourceRef) {
    cancelRouteDryRun();
    destroyPreview();
    destroyBridge();
    view.documentMode = 'original';
    view.selectedRef = sourceRef;
    render(state);
    root.querySelector('[data-inbox-item][aria-selected="true"]')?.focus();
    announce(liveMessage(selectedItem(state), statusTone(state, selectedItem(state))));
  }

  function setMobilePanel(state, panel, focusTab = false) {
    if (!mobilePanels.includes(panel)) return;
    view.mobilePanel = panel;
    render(state);
    const tab = root.querySelector(`[data-mobile-panel-target="${panel}"]`);
    if (focusTab) tab?.focus();
    else tab?.focus();
  }

  function setDocumentMode(state, mode) {
    if (!enabledDocumentModes().includes(mode)) return;
    view.documentMode = mode;
    render(state);
    root.querySelector(`[data-document-mode="${mode}"]`)?.focus();
  }

  function handleItemKeys(event, state) {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const rows = Array.from(root.querySelectorAll('[data-inbox-item]'));
    const current = rows.indexOf(event.currentTarget);
    const target = event.key === 'Home' ? 0
      : event.key === 'End' ? rows.length - 1
        : event.key === 'ArrowDown' ? Math.min(rows.length - 1, current + 1)
          : Math.max(0, current - 1);
    rows[target]?.focus();
  }

  function handleTabKeys(event, state, values, field, selector) {
    if (!['ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const current = values.indexOf(view[field]);
    const target = event.key === 'Home' ? 0
      : event.key === 'End' ? values.length - 1
        : event.key === 'ArrowRight' ? (current + 1) % values.length
          : (current - 1 + values.length) % values.length;
    view[field] = values[target];
    render(state);
    root.querySelectorAll(selector)[target]?.focus();
  }

  function announce(message) {
    const live = root.querySelector('[data-inbox-live-region]');
    if (live) live.textContent = message;
  }

  function cancelRouteDryRun() {
    if (view.route.controller) view.route.controller.abort();
    view.route = {
      sequence: view.route.sequence + 1,
      controller: null,
      sourceRef: '',
      state: 'idle',
      result: null,
      error: ''
    };
  }

  function advisoryRouteInput() {
    // These are a bounded candidate only. The server owns policy, capability and effects.
    return {
      domain: 'unknown',
      document_type: 'unknown',
      confidence: 0,
      risk_signals: {}
    };
  }

  function fixtureRouteProjection() {
    return {
      schema: 'odysseus.universal_inbox.route_dry_run.v1',
      status: 'review',
      policy_status: 'review',
      input_authority: 'advisory',
      confidence: 0,
      reason_codes: ['synthetic_fixture_review', 'unknown_domain', 'unknown_document_type', 'low_confidence'],
      review_reasons: ['synthetic_fixture_review', 'unknown_domain', 'unknown_document_type', 'low_confidence'],
      no_go_reasons: [],
      dry_run: true,
      writes_performed: false,
      fixture: true
    };
  }

  async function requestRouteDryRun(state, item) {
    const capability = routeCapabilityFor(item, state);
    if (!capability.enabled) return;
    cancelRouteDryRun();
    const sequence = view.route.sequence + 1;
    const sourceRef = String(item.source_ref || '');
    const controller = new AbortController();
    view.route = { sequence, controller, sourceRef, state: 'loading', result: null, error: '' };
    render(state);
    announce('Checking server policy for a dry-run route. No write will run.');

    try {
      let result;
      if (state.mode === 'fixture') {
        result = fixtureRouteProjection();
      } else {
        const response = await api.fetchImpl(
          `${api.baseUrl}/items/${encodeURIComponent(sourceRef)}/route-dry-run`,
          {
            method: 'POST',
            mode: 'same-origin',
            redirect: 'error',
            credentials: 'same-origin',
            cache: 'no-store',
            headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
            body: JSON.stringify(advisoryRouteInput()),
            signal: controller.signal
          }
        );
        if (!response || !response.ok || !hasSafeRouteHeaders(response)) {
          throw new Error('route_dry_run_unavailable');
        }
        result = assertRouteDryRunProjection(await response.json());
      }
      if (view.route.sequence !== sequence || view.route.sourceRef !== sourceRef) return;
      view.route = { sequence, controller: null, sourceRef, state: 'result', result, error: '' };
      render(state);
      announce(`Route ${result.status}. Server policy result is available; no write ran.`);
    } catch (error) {
      if (error && error.name === 'AbortError') return;
      if (view.route.sequence !== sequence || view.route.sourceRef !== sourceRef) return;
      view.route = {
        sequence,
        controller: null,
        sourceRef,
        state: 'error',
        result: null,
        error: 'The server could not verify this dry-run route. No action ran.'
      };
      render(state);
      announce('Route dry run unavailable. No action ran.');
    }
  }

  function assertRouteDryRunProjection(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)
      || value.schema !== 'odysseus.universal_inbox.route_dry_run.v1'
      || !['go', 'review', 'no_go'].includes(value.status)
      || value.policy_status !== value.status
      || value.input_authority !== 'advisory'
      || typeof value.confidence !== 'number' || !Number.isFinite(value.confidence)
      || value.confidence < 0 || value.confidence > 1
      || !Array.isArray(value.reason_codes)
      || !value.reason_codes.every(code => typeof code === 'string' && /^[a-z][a-z0-9_]{0,63}$/.test(code))
      || !Array.isArray(value.review_reasons) || !Array.isArray(value.no_go_reasons)
      || !value.review_reasons.every(code => typeof code === 'string' && /^[a-z][a-z0-9_]{0,63}$/.test(code))
      || !value.no_go_reasons.every(code => typeof code === 'string' && /^[a-z][a-z0-9_]{0,63}$/.test(code))
      || value.dry_run !== true || value.writes_performed !== false
      || value.copy_performed !== false || value.move_performed !== false
      || value.delete_performed !== false || value.overwrite_performed !== false
      || value.memory_writes_performed !== false || value.live_writes_performed !== false
      || value.path_redacted !== true || value.content_redacted !== true
      || !value.live_apply || value.live_apply.enabled !== false
      || value.live_apply.gate !== 'UIX-NEXTCLOUD-LIVE-WRITE') {
      throw new Error('invalid_route_dry_run_projection');
    }
    const expectedReasons = value.no_go_reasons.concat(value.review_reasons);
    if (value.reason_codes.length !== expectedReasons.length
      || value.reason_codes.some((code, index) => code !== expectedReasons[index])
      || (value.status === 'go' && expectedReasons.length !== 0)
      || (value.status === 'review' && (!value.review_reasons.length || value.no_go_reasons.length))
      || (value.status === 'no_go' && !value.no_go_reasons.length)) {
      throw new Error('inconsistent_route_dry_run_projection');
    }
    assertNoUnsafeRouteFields(value);
    return {
      status: value.status,
      policy_status: value.policy_status,
      confidence: value.confidence,
      reason_codes: value.reason_codes.slice(),
      fixture: false
    };
  }

  function hasSafeRouteHeaders(response) {
    const headers = response.headers;
    const contentType = String(headers && headers.get('content-type') || '').toLowerCase();
    const cacheControl = String(headers && headers.get('cache-control') || '').toLowerCase();
    const nosniff = String(headers && headers.get('x-content-type-options') || '').toLowerCase();
    return contentType.startsWith('application/json')
      && cacheControl.includes('no-store') && nosniff === 'nosniff';
  }

  function assertNoUnsafeRouteFields(value) {
    const forbidden = new Set([
      'absolute_path', 'body', 'bytes', 'chat_id', 'content', 'display_name',
      'file_hash', 'file_path', 'filename', 'hash', 'original_name', 'original_path',
      'owner', 'owner_id', 'path', 'raw_bytes', 'raw_content', 'raw_text',
      'raptorgraph_event', 'raptorgraph_payload', 'review_path', 'sidecar_path',
      'source_path', 'storage_path', 'target_path', 'text_content'
    ]);
    const queue = [value];
    while (queue.length) {
      const current = queue.shift();
      if (!current || typeof current !== 'object') continue;
      for (const [key, nested] of Object.entries(current)) {
        if (forbidden.has(key)) throw new Error('unsafe_route_dry_run_projection');
        if (nested && typeof nested === 'object') queue.push(nested);
      }
    }
  }

  function load() {
    cancelRouteDryRun();
    const source = requestedSource === 'fixture' ? 'fixture' : 'live';
    return model.load({ source, scenario: requestedScenario });
  }

  window.addEventListener('harbor:workspace-changed', event => {
    if (event.detail && event.detail.workspace === 'inbox') {
      root.querySelector('[data-document-heading]')?.focus();
    }
  });
  root.addEventListener('keydown', event => {
    if (event.key === 'Escape' && view.mobilePanel !== 'document') {
      event.stopPropagation();
      setMobilePanel(model.getState(), 'document', true);
    }
  });

  window.HarborInboxWorkbench = Object.freeze({
    refresh: load,
    getState: () => model.getState(),
    getView: () => ({
      hasSelection: Boolean(view.selectedRef),
      hasSearch: Boolean(view.search.trim()),
      documentMode: view.documentMode,
      mobilePanel: view.mobilePanel,
      workingCopy: projectBridgeState(view.bridgeState),
      route: {
        sequence: view.route.sequence,
        state: view.route.state,
        result: view.route.result ? { ...view.route.result } : null,
        error: view.route.error
      }
    })
  });
  load();
})();
