(function () {
  'use strict';

  const MAX_RENDERED_EVENTS = 300;
  const TERMINAL_STATES = new Set(['cancelled', 'completed', 'failed', 'timed_out', 'terminated']);
  const state = {
    api: null,
    fixtureScenario: '',
    runId: '',
    projection: null,
    historyCursor: '',
    historyEvents: new Map(),
    stopStream: null,
    busy: false
  };

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }

  function formatTime(value) {
    if (!value) return 'not set';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return 'unknown';
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
  }

  function shortId(value) {
    const text = String(value || '');
    return text.length > 24 ? `${text.slice(0, 12)}…${text.slice(-7)}` : text;
  }

  function announce(message, tone) {
    const target = document.querySelector('[data-agent-operations-notice]');
    if (!target) return;
    target.textContent = message;
    target.dataset.tone = tone || 'quiet';
  }

  function createShell(agentScreen) {
    const launcher = element('button', 'agent-operations-launcher');
    launcher.type = 'button';
    launcher.dataset.agentOperationsToggle = '';
    launcher.setAttribute('aria-expanded', 'true');
    launcher.setAttribute('aria-controls', 'agent-operations-panel');
    launcher.innerHTML = '<span class="agent-operations-pulse" aria-hidden="true"></span><span><strong>Run control</strong><small data-agent-launcher-state>connecting</small></span>';

    const panel = element('aside', 'agent-operations-panel');
    panel.id = 'agent-operations-panel';
    panel.dataset.agentOperationsPanel = '';
    panel.dataset.open = 'true';
    panel.setAttribute('aria-label', 'Current agent operation');
    panel.innerHTML = `
      <header class="agent-operations-head">
        <div><span class="agent-operations-eyebrow">Agent · current operation</span><strong data-agent-operation-title>Run control</strong></div>
        <div class="agent-operations-head-actions">
          <button type="button" data-agent-operation-refresh aria-label="Refresh run projection">Refresh</button>
          <button type="button" data-agent-operation-close aria-label="Close run control">×</button>
        </div>
      </header>
      <div class="agent-operations-notice" data-agent-operations-notice data-tone="quiet" aria-live="polite">Loading backend projection…</div>
      <label class="agent-fixture-picker" data-agent-fixture-picker hidden>Preview state
        <select data-agent-fixture-select></select>
      </label>
      <div class="agent-operations-scroll">
        <section class="agent-run-hero" data-agent-run-hero hidden>
          <div class="agent-run-hero-line"><span class="agent-state-badge" data-agent-run-state>unknown</span><span data-agent-run-id></span></div>
          <a class="agent-plan-link" data-agent-plan-link href="#"><span>Pinned plan</span><strong data-agent-plan-title></strong><small data-agent-plan-hash></small></a>
          <dl class="agent-run-facts">
            <div><dt>Started</dt><dd data-agent-started></dd></div>
            <div><dt>Deadline</dt><dd data-agent-deadline></dd></div>
            <div><dt>History</dt><dd data-agent-segment></dd></div>
          </dl>
          <div class="agent-frontier"><span>Current frontier</span><strong data-agent-frontier></strong><small data-agent-waiting></small></div>
        </section>
        <section class="agent-operation-section" data-agent-activities-section hidden>
          <div class="agent-operation-section-title"><span>Activities</span><small data-agent-activity-count></small></div>
          <div class="agent-activity-list" data-agent-activity-list></div>
        </section>
        <section class="agent-operation-section agent-command-section" data-agent-command-section hidden>
          <div class="agent-operation-section-title"><span>Allowed controls</span><small>server projected</small></div>
          <label class="agent-steering-ref" data-agent-steering-wrap hidden><span>Steering reference</span><input data-agent-steering-input value="operator-steering" maxlength="128"></label>
          <div class="agent-command-bar" data-agent-command-bar></div>
          <div class="agent-command-receipt" data-agent-command-receipt hidden></div>
        </section>
        <div class="agent-operation-detail-grid">
          <details class="agent-operation-detail" open><summary>Gates <span data-agent-gate-count>0</span></summary><div data-agent-gate-list></div></details>
          <details class="agent-operation-detail"><summary>Claims <span data-agent-claim-count>0</span></summary><div data-agent-claim-list></div></details>
          <details class="agent-operation-detail"><summary>Evidence <span data-agent-evidence-count>0</span></summary><div data-agent-evidence-list></div></details>
        </div>
        <section class="agent-operation-section agent-history-section" data-agent-history-section hidden>
          <div class="agent-operation-section-title"><span>History</span><small data-agent-stream-state>connecting</small></div>
          <ol class="agent-history-list" data-agent-history-list></ol>
          <button class="agent-history-more" type="button" data-agent-history-more hidden>Load next page</button>
        </section>
        <section class="agent-operation-empty" data-agent-operation-empty hidden><strong>No active operation</strong><span>Start a roadmap through /abc. Runtime state will appear here, never in Planning.</span></section>
      </div>`;

    agentScreen.append(launcher, panel);
    return { launcher, panel };
  }

  function setPanelOpen(open) {
    const panel = document.querySelector('[data-agent-operations-panel]');
    const button = document.querySelector('[data-agent-operations-toggle]');
    if (!panel || !button) return;
    panel.dataset.open = String(open);
    button.setAttribute('aria-expanded', String(open));
  }

  function renderCollection(selector, items, labelKeys) {
    const target = document.querySelector(selector);
    if (!target) return;
    target.replaceChildren();
    items.forEach(item => {
      const row = element('div', 'agent-detail-row');
      const title = labelKeys.map(key => item[key]).find(Boolean) || 'record';
      row.append(element('strong', '', shortId(title)));
      const stateLabel = item.state || item.status || item.decision || 'recorded';
      const badge = element('span', '', stateLabel);
      badge.dataset.state = stateLabel;
      row.append(badge);
      target.append(row);
    });
    if (!items.length) target.append(element('span', 'agent-detail-empty', 'None for this run.'));
  }

  function renderActivities(activities) {
    const list = document.querySelector('[data-agent-activity-list]');
    if (!list) return;
    list.replaceChildren();
    activities.forEach(activity => {
      const card = element('article', 'agent-activity-card');
      card.dataset.state = activity.state || 'unknown';
      const top = element('div', 'agent-activity-top');
      top.append(element('strong', '', activity.node_id || activity.type || 'Activity'));
      const stateBadge = element('span', '', activity.state || 'unknown');
      stateBadge.dataset.state = activity.state || 'unknown';
      top.append(stateBadge);
      const meta = element('div', 'agent-activity-meta');
      meta.append(element('span', '', `${activity.type || 'activity'} · attempt ${activity.attempt || 0}/${activity.max_attempts || 0}`));
      const heartbeat = element('span', 'agent-heartbeat', `heartbeat ${activity.heartbeat_health || 'not_expected'}`);
      heartbeat.dataset.health = activity.heartbeat_health || 'not_expected';
      meta.append(heartbeat);
      card.append(top, meta);
      if (activity.next_retry_at) card.append(element('small', 'agent-activity-next', `Next retry ${formatTime(activity.next_retry_at)}`));
      list.append(card);
    });
  }

  function commandLabel(command) {
    return ({ pause: 'Pause', resume: 'Resume', cancel: 'Cancel run', retry_activity: 'Retry activity', decide_gate: 'Approve gate', steer_run: 'Send steering' })[command] || command;
  }

  function commandPayload(command, projection) {
    if (command === 'retry_activity') return { node_id: projection.run.current_node_ids[0] };
    if (command === 'decide_gate') {
      const gate = projection.gates.find(item => item.state === 'pending') || projection.gates[0];
      return { gate_id: gate && gate.gate_id, decision: 'approved' };
    }
    if (command === 'steer_run') {
      const input = document.querySelector('[data-agent-steering-input]');
      return { steering_ref: String(input && input.value || 'operator-steering').trim() };
    }
    return {};
  }

  function renderCommands(projection) {
    const commands = projection.run.allowed_commands || [];
    const bar = document.querySelector('[data-agent-command-bar]');
    const section = document.querySelector('[data-agent-command-section]');
    const steering = document.querySelector('[data-agent-steering-wrap]');
    if (!bar || !section || !steering) return;
    bar.replaceChildren();
    section.hidden = TERMINAL_STATES.has(projection.run.state) && !commands.length;
    steering.hidden = !commands.includes('steer_run');
    commands.forEach(command => {
      const button = element('button', 'agent-command', commandLabel(command));
      button.type = 'button';
      button.dataset.command = command;
      button.disabled = state.busy;
      if (command === 'cancel') button.dataset.danger = 'true';
      button.addEventListener('click', () => executeCommand(command));
      bar.append(button);
    });
  }

  function renderProjection(projection) {
    state.projection = projection;
    const run = projection.run;
    document.querySelector('[data-agent-run-hero]').hidden = false;
    document.querySelector('[data-agent-activities-section]').hidden = !projection.activities.length;
    document.querySelector('[data-agent-history-section]').hidden = false;
    document.querySelector('[data-agent-operation-empty]').hidden = true;
    const stateBadge = document.querySelector('[data-agent-run-state]');
    stateBadge.textContent = run.state;
    stateBadge.dataset.state = run.state;
    document.querySelector('[data-agent-run-id]').textContent = shortId(run.agent_run_id);
    document.querySelector('[data-agent-launcher-state]').textContent = run.state.replaceAll('_', ' ');
    document.querySelector('[data-agent-operation-title]').textContent = run.plan_ref.roadmap_id;
    document.querySelector('[data-agent-plan-title]').textContent = `${run.plan_ref.roadmap_id} · r${run.plan_ref.revision}`;
    document.querySelector('[data-agent-plan-hash]').textContent = shortId(run.plan_ref.content_hash);
    const planLink = document.querySelector('[data-agent-plan-link]');
    const url = new URL(window.location.href);
    url.searchParams.set('workspace', 'planning');
    url.searchParams.set('project_id', run.plan_ref.project_id);
    url.searchParams.set('roadmap_id', run.plan_ref.roadmap_id);
    url.searchParams.set('revision', run.plan_ref.revision);
    planLink.href = url.toString();
    document.querySelector('[data-agent-started]').textContent = formatTime(run.started_at);
    document.querySelector('[data-agent-deadline]').textContent = formatTime(run.deadline_at);
    document.querySelector('[data-agent-segment]').textContent = `segment ${run.history_segment} · v${run.version}`;
    document.querySelector('[data-agent-frontier]').textContent = run.current_node_ids.length ? run.current_node_ids.join(', ') : 'No runnable node';
    const waiting = document.querySelector('[data-agent-waiting]');
    waiting.textContent = run.waiting_reason || (TERMINAL_STATES.has(run.state) ? 'Run is terminal.' : 'Dispatch is active.');
    document.querySelector('[data-agent-activity-count]').textContent = `${projection.activities.length} visible`;
    renderActivities(projection.activities);
    renderCommands(projection);
    const collections = [
      ['[data-agent-gate-list]', projection.gates, ['gate_id'], '[data-agent-gate-count]'],
      ['[data-agent-claim-list]', projection.claims, ['claim_id'], '[data-agent-claim-count]'],
      ['[data-agent-evidence-list]', projection.evidence, ['evidence_id', 'summary'], '[data-agent-evidence-count]']
    ];
    collections.forEach(([list, items, keys, count]) => {
      renderCollection(list, items, keys);
      document.querySelector(count).textContent = items.length;
    });
  }

  function renderHistory() {
    const list = document.querySelector('[data-agent-history-list]');
    if (!list) return;
    list.replaceChildren();
    Array.from(state.historyEvents.values()).slice(-MAX_RENDERED_EVENTS).forEach(event => {
      const row = element('li', 'agent-history-event');
      row.dataset.eventId = event.event_id;
      row.append(element('time', '', formatTime(event.occurred_at)));
      const copy = element('div');
      copy.append(element('strong', '', event.summary || event.event_type));
      copy.append(element('small', '', [event.event_type, event.node_id].filter(Boolean).join(' · ')));
      row.append(copy);
      list.append(row);
    });
  }

  function appendHistory(events) {
    events.forEach(event => {
      if (!event || !event.event_id || state.historyEvents.has(event.event_id)) return;
      state.historyEvents.set(event.event_id, event);
      state.historyCursor = event.event_id;
    });
    while (state.historyEvents.size > MAX_RENDERED_EVENTS) {
      state.historyEvents.delete(state.historyEvents.keys().next().value);
    }
    renderHistory();
  }

  async function loadHistory(reset) {
    if (reset) {
      state.historyCursor = '';
      state.historyEvents.clear();
    }
    const page = await state.api.getHistory(state.runId, { after: state.historyCursor, limit: 50 });
    appendHistory(Array.isArray(page.events) ? page.events : []);
    state.historyCursor = page.next_cursor || state.historyCursor;
    document.querySelector('[data-agent-history-more]').hidden = !page.has_more;
  }

  function connectStream() {
    state.stopStream?.();
    const status = document.querySelector('[data-agent-stream-state]');
    status.textContent = 'live · reconnect safe';
    state.stopStream = state.api.streamHistory(state.runId, {
      after: state.historyCursor,
      onEvent: event => appendHistory([event]),
      onError: () => { status.textContent = 'reconnecting'; }
    });
  }

  async function loadRun() {
    announce('Refreshing backend projection…');
    const projection = await state.api.getRun(state.runId);
    renderProjection(projection);
    await loadHistory(true);
    connectStream();
    announce(state.fixtureScenario ? `Preview fixture · ${state.fixtureScenario.replaceAll('_', ' ')}` : 'Backend projection connected.', 'success');
  }

  async function loadCatalog() {
    state.stopStream?.();
    state.stopStream = null;
    try {
      const catalog = await state.api.listRuns({ limit: 50 });
      const runs = Array.isArray(catalog.items) ? catalog.items : [];
      const requested = new URLSearchParams(window.location.search).get('agent_run_id');
      const selected = runs.find(run => run.agent_run_id === requested) || runs[0];
      if (!selected) {
        document.querySelector('[data-agent-operation-empty]').hidden = false;
        document.querySelector('[data-agent-run-hero]').hidden = true;
        document.querySelector('[data-agent-activities-section]').hidden = true;
        document.querySelector('[data-agent-command-section]').hidden = true;
        document.querySelector('[data-agent-history-section]').hidden = true;
        document.querySelector('[data-agent-launcher-state]').textContent = 'idle';
        announce('No active operation. Runtime remains in Agent.', 'quiet');
        return;
      }
      state.runId = selected.agent_run_id;
      await loadRun();
    } catch (error) {
      document.querySelector('[data-agent-launcher-state]').textContent = 'unavailable';
      announce(error && error.message || 'Agent operation projection is unavailable.', 'error');
    }
  }

  function uniqueId(prefix) {
    const value = window.crypto && typeof window.crypto.randomUUID === 'function'
      ? window.crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    return `${prefix}-${value}`;
  }

  async function executeCommand(command) {
    if (state.busy || !state.projection || !state.projection.run.allowed_commands.includes(command)) return;
    if (command === 'cancel' && !window.confirm('Cancel this run? No new activities will be dispatched.')) return;
    const payload = commandPayload(command, state.projection);
    if (Object.values(payload).some(value => !value)) {
      announce('The projected command is missing its required target.', 'error');
      return;
    }
    state.busy = true;
    renderCommands(state.projection);
    announce(`Sending ${command.replaceAll('_', ' ')}…`);
    try {
      const receipt = await state.api.executeCommand(state.runId, {
        command_id: uniqueId('ui-command'),
        command,
        expected_run_version: state.projection.run.version,
        idempotency_key: uniqueId('ui-idempotency'),
        payload
      });
      const target = document.querySelector('[data-agent-command-receipt]');
      target.hidden = false;
      target.textContent = `${receipt.result_code} · v${receipt.result_run_version} · ${shortId(receipt.command_id)}`;
      await loadRun();
    } catch (error) {
      announce(error && error.message || 'Command was rejected.', 'error');
    } finally {
      state.busy = false;
      if (state.projection) renderCommands(state.projection);
    }
  }

  function configureApi() {
    const query = new URLSearchParams(window.location.search);
    const requestedFixture = query.get('agent-fixture');
    const fixtureRegistry = window.HarborAgentOperationFixtures;
    if (requestedFixture && fixtureRegistry && fixtureRegistry.STATES[requestedFixture]) {
      state.fixtureScenario = requestedFixture;
      state.api = new fixtureRegistry.FixtureAgentOperationsApi({ scenario: requestedFixture });
      const picker = document.querySelector('[data-agent-fixture-picker]');
      const select = document.querySelector('[data-agent-fixture-select]');
      picker.hidden = false;
      Object.keys(fixtureRegistry.STATES).forEach(name => {
        const option = element('option', '', name.replaceAll('_', ' '));
        option.value = name;
        option.selected = name === requestedFixture;
        select.append(option);
      });
      select.addEventListener('change', () => {
        state.fixtureScenario = select.value;
        state.api = new fixtureRegistry.FixtureAgentOperationsApi({ scenario: select.value });
        loadCatalog();
      });
      return;
    }
    if (!window.HarborAgentApi) throw new Error('Agent operations API is unavailable.');
    state.api = new window.HarborAgentApi.AgentOperationsApi();
  }

  function bindEvents() {
    document.querySelector('[data-agent-operations-toggle]')?.addEventListener('click', event => {
      setPanelOpen(event.currentTarget.getAttribute('aria-expanded') !== 'true');
    });
    document.querySelector('[data-agent-operation-close]')?.addEventListener('click', () => setPanelOpen(false));
    document.querySelector('[data-agent-operation-refresh]')?.addEventListener('click', loadCatalog);
    document.querySelector('[data-agent-history-more]')?.addEventListener('click', () => loadHistory(false));
    window.addEventListener('harbor:agent-composer-drafted', () => window.setTimeout(loadCatalog, 250));
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && window.matchMedia('(max-width: 1280px)').matches) setPanelOpen(false);
    });
  }

  function init() {
    const agentScreen = document.querySelector('[data-workspace-screen="agent"]');
    if (!agentScreen || agentScreen.querySelector('[data-agent-operations-panel]')) return;
    createShell(agentScreen);
    bindEvents();
    try {
      configureApi();
      loadCatalog();
    } catch (error) {
      announce(error && error.message || 'Agent operation surface could not start.', 'error');
    }
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();

  window.HarborAgentOperations = Object.freeze({ init, MAX_RENDERED_EVENTS });
})();
