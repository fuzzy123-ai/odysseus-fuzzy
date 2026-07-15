(function () {
  'use strict';

  const HASH = 'sha256:' + 'a'.repeat(64);
  const RUN_ID = 'arun-' + '7'.repeat(32);
  const STATES = Object.freeze({
    running: { state: 'running', activity: 'running', heartbeat: 'healthy', commands: ['pause', 'cancel', 'steer_run'] },
    waiting_gate: { state: 'waiting_gate', activity: 'scheduled', heartbeat: 'late', commands: ['pause', 'cancel', 'decide_gate'] },
    paused: { state: 'paused', activity: 'scheduled', heartbeat: 'not_expected', commands: ['resume', 'cancel', 'steer_run'] },
    retry_wait: { state: 'running', activity: 'retry_wait', heartbeat: 'stale', commands: ['pause', 'cancel', 'retry_activity', 'steer_run'] },
    waiting_signal: { state: 'waiting_signal', activity: 'scheduled', heartbeat: 'not_expected', commands: ['pause', 'cancel', 'steer_run'] },
    completed: { state: 'completed', activity: 'succeeded', heartbeat: 'not_expected', commands: [] },
    failed: { state: 'failed', activity: 'failed', heartbeat: 'not_expected', commands: [] },
    timed_out: { state: 'timed_out', activity: 'timed_out', heartbeat: 'not_expected', commands: [] },
    cancelled: { state: 'cancelled', activity: 'cancelled', heartbeat: 'not_expected', commands: [] }
  });

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function makeProjection(name) {
    const selected = STATES[name] || STATES.running;
    const terminal = ['completed', 'failed', 'timed_out', 'cancelled'].includes(selected.state);
    const gateWaiting = selected.state === 'waiting_gate';
    const retrying = selected.activity === 'retry_wait';
    return {
      schema_id: 'odysseus.agent.operation_projection.v1',
      observed_at: '2026-07-15T14:28:00Z',
      run: {
        agent_run_id: RUN_ID,
        workflow_id: `odysseus-abc/demo/${RUN_ID}`,
        workflow_run_id: 'temporal-demo-run-1',
        history_segment: 1,
        plan_ref: {
          project_id: 'harbor-one',
          roadmap_id: 'temporal-light-agent-execution',
          revision: 12,
          content_hash: HASH
        },
        state: selected.state,
        version: 18,
        started_at: '2026-07-15T08:00:00Z',
        updated_at: '2026-07-15T14:27:58Z',
        completed_at: terminal ? '2026-07-15T14:27:58Z' : null,
        deadline_at: '2026-07-16T08:00:00Z',
        current_node_ids: terminal ? [] : ['TLR-07-agent-screen-runtime-ui'],
        waiting_reason: gateWaiting ? 'Operator decision required for HPA-AGENT-UX-ACCEPTANCE.' : retrying ? 'Retry backoff after a bounded browser check.' : null,
        allowed_commands: selected.commands
      },
      activities: [
        {
          activity_id: 'activity-tlr07-preview',
          node_id: 'TLR-07-agent-screen-runtime-ui',
          type: 'execute_slice',
          state: selected.activity,
          attempt: retrying ? 2 : 1,
          max_attempts: 3,
          retryable: !terminal,
          next_retry_at: retrying ? '2026-07-15T14:29:00Z' : null,
          started_at: '2026-07-15T14:20:00Z',
          updated_at: '2026-07-15T14:27:58Z',
          completed_at: terminal ? '2026-07-15T14:27:58Z' : null,
          last_heartbeat_at: selected.heartbeat === 'not_expected' ? null : selected.heartbeat === 'stale' ? '2026-07-15T14:22:00Z' : '2026-07-15T14:27:55Z',
          heartbeat_timeout_seconds: selected.heartbeat === 'not_expected' ? null : 30,
          heartbeat_health: selected.heartbeat,
          error_code: selected.activity === 'failed' ? 'activity_failed' : null
        }
      ],
      claims: terminal ? [] : [{ claim_id: 'claim-tlr07', state: 'active', owner: 'Alice', lease_expires_at: '2026-07-15T14:35:00Z', node_id: 'TLR-07-agent-screen-runtime-ui' }],
      gates: [{ gate_id: 'HPA-AGENT-UX-ACCEPTANCE', state: gateWaiting ? 'pending' : 'approved', safe_default: 'Preview only; no root cutover.' }],
      evidence: [{ evidence_id: 'evidence-agent-preview', state: terminal ? 'verified' : 'collecting', summary: 'Responsive Agent projection and command contract.' }]
    };
  }

  function makeHistory() {
    return [
      { event_id: 'h0:1', event_type: 'run_started', occurred_at: '2026-07-15T08:00:00Z', node_id: null, activity_id: null, summary: 'Run started from pinned Planning revision.', ref_ids: [] },
      { event_id: 'h0:2', event_type: 'claim_acquired', occurred_at: '2026-07-15T14:19:58Z', node_id: 'TLR-07-agent-screen-runtime-ui', activity_id: null, summary: 'Serialized path claim acquired.', ref_ids: ['claim-tlr07'] },
      { event_id: 'h1:1', event_type: 'activity_started', occurred_at: '2026-07-15T14:20:00Z', node_id: 'TLR-07-agent-screen-runtime-ui', activity_id: 'activity-tlr07-preview', summary: 'Agent preview implementation started.', ref_ids: [] },
      { event_id: 'h1:2', event_type: 'heartbeat_recorded', occurred_at: '2026-07-15T14:27:55Z', node_id: 'TLR-07-agent-screen-runtime-ui', activity_id: 'activity-tlr07-preview', summary: 'Activity heartbeat recorded.', ref_ids: [] }
    ];
  }

  class FixtureAgentOperationsApi {
    constructor(options) {
      const settings = options || {};
      this.scenario = STATES[settings.scenario] ? settings.scenario : 'running';
      this.projection = makeProjection(this.scenario);
      this.history = makeHistory();
      this.streamTimer = null;
    }

    listRuns() {
      return Promise.resolve({ items: [{ agent_run_id: RUN_ID, state: this.projection.run.state, plan_ref: clone(this.projection.run.plan_ref) }], next_cursor: '' });
    }

    getRun() {
      return Promise.resolve(clone(this.projection));
    }

    getHistory(agentRunId, options) {
      const after = String(options && options.after || '');
      const start = after ? this.history.findIndex(event => event.event_id === after) + 1 : 0;
      const events = this.history.slice(Math.max(0, start));
      return Promise.resolve({ cursor: after, next_cursor: events.length ? events[events.length - 1].event_id : after, has_more: false, events: clone(events) });
    }

    executeCommand(agentRunId, body) {
      const next = { pause: 'paused', resume: 'running', cancel: 'cancelled' }[body.command];
      if (next) {
        this.scenario = next;
        this.projection = makeProjection(next);
        this.projection.run.version = body.expected_run_version + 1;
      }
      return Promise.resolve({
        schema_id: 'odysseus.temporal_light.command_receipt.v1',
        command_id: body.command_id,
        idempotency_key: body.idempotency_key,
        command: body.command,
        binding_digest: HASH,
        accepted_run_version: body.expected_run_version,
        result_run_version: body.expected_run_version + 1,
        result_code: 'applied',
        state: this.projection.run.state
      });
    }

    streamHistory(agentRunId, options) {
      this.streamTimer = window.setTimeout(() => {
        options.onEvent?.({
          event_id: 'h1:3',
          event_type: 'projection_refreshed',
          occurred_at: '2026-07-15T14:28:01Z',
          node_id: 'TLR-07-agent-screen-runtime-ui',
          activity_id: 'activity-tlr07-preview',
          summary: 'Backend projection refreshed after reconnect.',
          ref_ids: []
        });
      }, 120);
      return () => window.clearTimeout(this.streamTimer);
    }
  }

  window.HarborAgentOperationFixtures = Object.freeze({
    FixtureAgentOperationsApi,
    RUN_ID,
    STATES,
    makeHistory,
    makeProjection
  });
})();
