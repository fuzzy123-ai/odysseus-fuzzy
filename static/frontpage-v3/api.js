(function () {
  'use strict';

  const RUNTIME_KEYS = new Set([
    'activity_attempt', 'activity_id', 'agent_run_id', 'allowed_commands', 'attempt',
    'changed_files', 'claim', 'claim_id', 'command', 'command_id', 'commit', 'commit_id',
    'completed_at', 'evidence', 'evidence_receipt', 'fencing_token', 'heartbeat',
    'heartbeat_age_seconds', 'heartbeat_at', 'heartbeat_health', 'heartbeat_timeout_seconds',
    'history_event_id', 'history_segment', 'last_heartbeat_at', 'lease_expires_at',
    'lease_id', 'lease_revision', 'max_attempts', 'next_retry_at', 'retry_count', 'run_id',
    'run_progress', 'runtime_status', 'signal', 'signal_id', 'started_at', 'temporal_run_id',
    'update_id', 'waiting_reason', 'worker_id', 'workflow_id', 'workflow_run_id'
  ]);
  const GATE_RUNTIME_KEYS = new Set(['actor', 'decided_at', 'decision', 'evidence_receipt', 'expires_at', 'state']);

  class PlanningApiError extends Error {
    constructor(code, message, status) {
      super(message);
      this.name = 'PlanningApiError';
      this.code = code;
      this.status = status || 0;
    }
  }

  function assertDefinitionPayload(value, path, gateDefinition) {
    const currentPath = path || '$';
    if (Array.isArray(value)) {
      value.forEach((item, index) => assertDefinitionPayload(item, `${currentPath}[${index}]`, gateDefinition));
      return value;
    }
    if (!value || typeof value !== 'object') return value;
    Object.entries(value).forEach(([key, child]) => {
      if (RUNTIME_KEYS.has(key) || (gateDefinition && GATE_RUNTIME_KEYS.has(key))) {
        throw new PlanningApiError('runtime_payload_rejected', `Forbidden field at ${currentPath}.${key}`);
      }
      const nestedGate = gateDefinition || key === 'gates' || key === 'gate_definitions';
      assertDefinitionPayload(child, `${currentPath}.${key}`, nestedGate);
    });
    return value;
  }

  function identifier(value, label) {
    const text = String(value || '').trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(text)) {
      throw new PlanningApiError('invalid_identifier', `${label} is invalid`);
    }
    return encodeURIComponent(text);
  }

  class PlanningDefinitionApi {
    constructor(options) {
      const settings = options || {};
      this.baseUrl = String(settings.baseUrl || '/api/planning').replace(/\/$/, '');
      this.fetchImpl = settings.fetchImpl || window.fetch.bind(window);
    }

    async request(path, options) {
      const settings = options || {};
      const { headers: requestedHeaders, ...requestOptions } = settings;
      const response = await this.fetchImpl(this.baseUrl + path, {
        credentials: 'same-origin',
        ...requestOptions,
        headers: { Accept: 'application/json', ...(requestedHeaders || {}) }
      });
      let payload = null;
      try {
        payload = await response.json();
      } catch {
        throw new PlanningApiError('invalid_response', 'Planning returned a non-JSON response.', response.status);
      }
      if (!response.ok) {
        const detail = payload && (payload.detail || payload.error);
        throw new PlanningApiError('request_failed', String(detail || `Planning request failed (${response.status}).`), response.status);
      }
      return assertDefinitionPayload(payload);
    }

    listProjects() {
      return this.request('/projects?limit=100');
    }

    getProject(projectId) {
      return this.request(`/projects/${identifier(projectId, 'project_id')}`);
    }

    listRoadmaps(projectId) {
      return this.request(`/projects/${identifier(projectId, 'project_id')}/roadmaps?limit=100`);
    }

    getRoadmap(projectId, roadmapId, revision) {
      const selected = revision == null ? 'latest_approved' : String(revision);
      return this.request(`/projects/${identifier(projectId, 'project_id')}/roadmaps/${identifier(roadmapId, 'roadmap_id')}?revision=${encodeURIComponent(selected)}`);
    }

    listRevisions(projectId, roadmapId) {
      return this.request(`/projects/${identifier(projectId, 'project_id')}/roadmaps/${identifier(roadmapId, 'roadmap_id')}/revisions?limit=100`);
    }

    createAgentHandoff(projectId, roadmapId, revision, contentHash) {
      return this.request(`/projects/${identifier(projectId, 'project_id')}/roadmaps/${identifier(roadmapId, 'roadmap_id')}/agent-handoff`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ revision, content_hash: contentHash })
      });
    }

    createDraft(projectId, roadmapId, body) {
      return this.request(`/projects/${identifier(projectId, 'project_id')}/roadmaps/${identifier(roadmapId, 'roadmap_id')}/drafts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
    }

    validateDraft(projectId, roadmapId, draftId, expectedDraftVersion) {
      return this.request(`/projects/${identifier(projectId, 'project_id')}/roadmaps/${identifier(roadmapId, 'roadmap_id')}/drafts/${identifier(draftId, 'draft_id')}/validate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ expected_draft_version: expectedDraftVersion })
      });
    }

    actOnDraft(projectId, roadmapId, draftId, body) {
      return this.request(`/projects/${identifier(projectId, 'project_id')}/roadmaps/${identifier(roadmapId, 'roadmap_id')}/drafts/${identifier(draftId, 'draft_id')}/actions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
    }

    async loadCatalog() {
      const projectPage = await this.listProjects();
      const projects = Array.isArray(projectPage.items) ? projectPage.items : [];
      if (!projects.length) {
        return { source: 'live', scenario: 'empty', projects: [], project: null, roadmaps: [], readModel: null };
      }
      const projectId = projects[0].project_id;
      const [projectModel, roadmapPage] = await Promise.all([
        this.getProject(projectId),
        this.listRoadmaps(projectId)
      ]);
      const roadmaps = Array.isArray(roadmapPage.items) ? roadmapPage.items : [];
      if (!roadmaps.length) {
        return { source: 'live', scenario: 'empty', projects, project: projectModel.project, roadmaps: [], readModel: null };
      }
      const selected = roadmaps[0];
      const revision = selected.latest_approved_revision || selected.newest_revision;
      const readModel = await this.getRoadmap(projectId, selected.roadmap_id, revision);
      return {
        source: 'live',
        scenario: readModel.origin && readModel.origin.state || 'live',
        projects,
        project: projectModel.project,
        roadmaps,
        readModel
      };
    }
  }

  class AgentOperationsApiError extends Error {
    constructor(code, message, status) {
      super(message);
      this.name = 'AgentOperationsApiError';
      this.code = code;
      this.status = status || 0;
    }
  }

  function assertAgentProjection(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
      throw new AgentOperationsApiError('invalid_projection', 'Agent returned an invalid projection.');
    }
    if (value.schema_id !== 'odysseus.agent.operation_projection.v1' || !value.run || typeof value.run !== 'object') {
      throw new AgentOperationsApiError('invalid_projection', 'Agent projection schema is unsupported.');
    }
    const collections = ['activities', 'claims', 'gates', 'evidence'];
    if (collections.some(key => !Array.isArray(value[key]))) {
      throw new AgentOperationsApiError('invalid_projection', 'Agent projection collections are invalid.');
    }
    if (!Array.isArray(value.run.allowed_commands) || !Array.isArray(value.run.current_node_ids)) {
      throw new AgentOperationsApiError('invalid_projection', 'Agent run controls are invalid.');
    }
    return value;
  }

  class AgentOperationsApi {
    constructor(options) {
      const settings = options || {};
      this.baseUrl = String(settings.baseUrl || '/api/agent/runs').replace(/\/$/, '');
      this.fetchImpl = settings.fetchImpl || window.fetch.bind(window);
      this.eventSourceFactory = settings.eventSourceFactory || ((url) => new window.EventSource(url, { withCredentials: true }));
    }

    async request(path, options) {
      const settings = options || {};
      const { headers: requestedHeaders, ...requestOptions } = settings;
      const response = await this.fetchImpl(this.baseUrl + path, {
        credentials: 'same-origin',
        ...requestOptions,
        headers: { Accept: 'application/json', ...(requestedHeaders || {}) }
      });
      let payload;
      try {
        payload = await response.json();
      } catch {
        throw new AgentOperationsApiError('invalid_response', 'Agent returned a non-JSON response.', response.status);
      }
      if (!response.ok) {
        const detail = payload && (payload.detail || payload.error);
        throw new AgentOperationsApiError('request_failed', String(detail || `Agent request failed (${response.status}).`), response.status);
      }
      return payload;
    }

    listRuns(options) {
      const settings = options || {};
      const query = new URLSearchParams();
      if (settings.projectId) query.set('project_id', String(settings.projectId));
      if (settings.state) query.set('state', String(settings.state));
      if (settings.cursor) query.set('cursor', String(settings.cursor));
      query.set('limit', String(Math.min(100, Math.max(1, Number(settings.limit) || 50))));
      return this.request(`?${query}`);
    }

    async getRun(agentRunId) {
      return assertAgentProjection(await this.request(`/${identifier(agentRunId, 'agent_run_id')}`));
    }

    getHistory(agentRunId, options) {
      const settings = options || {};
      const query = new URLSearchParams();
      if (settings.after) query.set('after', String(settings.after));
      query.set('limit', String(Math.min(200, Math.max(1, Number(settings.limit) || 50))));
      return this.request(`/${identifier(agentRunId, 'agent_run_id')}/history?${query}`);
    }

    executeCommand(agentRunId, body) {
      return this.request(`/${identifier(agentRunId, 'agent_run_id')}/commands`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
    }

    streamHistory(agentRunId, options) {
      const settings = options || {};
      const query = new URLSearchParams();
      if (settings.after) query.set('after', String(settings.after));
      const source = this.eventSourceFactory(`${this.baseUrl}/${identifier(agentRunId, 'agent_run_id')}/stream?${query}`);
      const onEvent = event => {
        try {
          const payload = JSON.parse(event.data);
          if (payload && typeof payload.event_id === 'string') settings.onEvent?.(payload);
        } catch {
          settings.onError?.(new AgentOperationsApiError('invalid_stream_event', 'Agent stream emitted invalid JSON.'));
        }
      };
      source.addEventListener('agent_operation', onEvent);
      source.addEventListener('error', () => settings.onError?.(new AgentOperationsApiError('stream_reconnecting', 'Agent history is reconnecting.')));
      return () => source.close();
    }
  }

  class AiLensApiError extends Error {
    constructor(code, message, status) {
      super(message);
      this.name = 'AiLensApiError';
      this.code = code;
      this.status = status || 0;
    }
  }

  function aiLensIdentifier(value) {
    const text = String(value || '').trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,119}$/.test(text)) {
      throw new AiLensApiError('invalid_session_id', 'AI Lens session identifier is invalid.');
    }
    return encodeURIComponent(text);
  }

  function assertAiLensEnvelope(value, schema) {
    if (!value || typeof value !== 'object' || Array.isArray(value) || value.schema !== schema) {
      throw new AiLensApiError('invalid_schema', `AI Lens returned an unsupported ${schema} payload.`);
    }
    if (value.raw_content_visible !== false) {
      throw new AiLensApiError('unsafe_payload', 'AI Lens payload did not confirm that raw content is hidden.');
    }
    return value;
  }

  function assertAiLensEvent(value) {
    const event = assertAiLensEnvelope(value, 'odysseus.ai_lens.event.v1');
    const requiredStrings = ['event_id', 'session_id', 'turn_id', 'created_at', 'event_type', 'phase', 'status', 'truth_level', 'observation_origin', 'privacy_level', 'redaction_level'];
    if (requiredStrings.some(key => typeof event[key] !== 'string' || !event[key])) {
      throw new AiLensApiError('invalid_event', 'AI Lens event fields are incomplete.');
    }
    if (!Number.isInteger(event.sequence) || event.sequence < 1 || !Array.isArray(event.source_refs)) {
      throw new AiLensApiError('invalid_event', 'AI Lens event sequence or source references are invalid.');
    }
    if (!event.payload || typeof event.payload !== 'object' || Array.isArray(event.payload)) {
      throw new AiLensApiError('invalid_event', 'AI Lens event payload is invalid.');
    }
    return event;
  }

  function assertAiLensService(value) {
    const service = assertAiLensEnvelope(value, 'odysseus.ai_lens.service.v1');
    if (!service.limits || typeof service.limits !== 'object' || !Number.isInteger(service.session_count)) {
      throw new AiLensApiError('invalid_service', 'AI Lens service metadata is invalid.');
    }
    return service;
  }

  function assertAiLensSessions(value) {
    const page = assertAiLensEnvelope(value, 'odysseus.ai_lens.sessions.v1');
    if (!Array.isArray(page.sessions) || !Number.isInteger(page.session_count)) {
      throw new AiLensApiError('invalid_sessions', 'AI Lens session page is invalid.');
    }
    page.sessions.forEach(session => {
      if (!session || session.schema !== 'odysseus.ai_lens.session_summary.v1' || typeof session.session_id !== 'string' || session.raw_content_visible !== false) {
        throw new AiLensApiError('invalid_session', 'AI Lens session summary is invalid.');
      }
    });
    return page;
  }

  function assertAiLensSnapshot(value) {
    const snapshot = assertAiLensEnvelope(value, 'odysseus.ai_lens.snapshot.v1');
    if (!Array.isArray(snapshot.events) || typeof snapshot.session_id !== 'string') {
      throw new AiLensApiError('invalid_snapshot', 'AI Lens snapshot is invalid.');
    }
    snapshot.events = snapshot.events.map(assertAiLensEvent);
    return snapshot;
  }

  class AiLensApi {
    constructor(options) {
      const settings = options || {};
      this.baseUrl = String(settings.baseUrl || '/api/ai-lens').replace(/\/$/, '');
      this.fetchImpl = settings.fetchImpl || window.fetch.bind(window);
      this.eventSourceFactory = settings.eventSourceFactory || (url => new window.EventSource(url, { withCredentials: true }));
    }

    async request(path) {
      const response = await this.fetchImpl(this.baseUrl + path, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' }
      });
      let payload;
      try {
        payload = await response.json();
      } catch {
        throw new AiLensApiError('invalid_response', 'AI Lens returned a non-JSON response.', response.status);
      }
      if (!response.ok) {
        const detail = payload && (payload.detail || payload.error);
        throw new AiLensApiError('request_failed', String(detail || `AI Lens request failed (${response.status}).`), response.status);
      }
      return payload;
    }

    async getService() {
      return assertAiLensService(await this.request('/service'));
    }

    async listSessions() {
      return assertAiLensSessions(await this.request('/sessions'));
    }

    async getSnapshot(sessionId, limit) {
      const query = new URLSearchParams();
      if (limit != null) query.set('limit', String(Math.min(128, Math.max(1, Number(limit) || 1))));
      const suffix = query.size ? `?${query}` : '';
      return assertAiLensSnapshot(await this.request(`/sessions/${aiLensIdentifier(sessionId)}/snapshot${suffix}`));
    }

    streamSession(sessionId, options) {
      const settings = options || {};
      const query = new URLSearchParams({
        event_limit: String(Math.min(128, Math.max(1, Number(settings.eventLimit) || 64))),
        heartbeat_every: String(Math.min(64, Math.max(1, Number(settings.heartbeatEvery) || 8)))
      });
      const source = this.eventSourceFactory(`${this.baseUrl}/sessions/${aiLensIdentifier(sessionId)}/stream?${query}`);
      let closed = false;
      const close = () => {
        if (closed) return;
        closed = true;
        source.close();
      };
      source.addEventListener('ai_lens_event', event => {
        try {
          settings.onEvent?.(assertAiLensEvent(JSON.parse(event.data)));
        } catch (error) {
          close();
          settings.onError?.(error instanceof AiLensApiError ? error : new AiLensApiError('invalid_stream_event', 'AI Lens stream emitted invalid JSON.'));
        }
      });
      source.addEventListener('stream_end', event => {
        try {
          const payload = assertAiLensEnvelope(JSON.parse(event.data), 'odysseus.ai_lens.stream_end.v1');
          settings.onEnd?.(payload);
        } catch (error) {
          settings.onError?.(error instanceof AiLensApiError ? error : new AiLensApiError('invalid_stream_end', 'AI Lens stream ended with invalid metadata.'));
        } finally {
          close();
        }
      });
      source.addEventListener('error', () => {
        close();
        settings.onError?.(new AiLensApiError('stream_unavailable', 'AI Lens stream is unavailable; the bounded snapshot remains visible.'));
      });
      return close;
    }
  }

  window.HarborPlanningApi = Object.freeze({
    GATE_RUNTIME_KEYS,
    PlanningApiError,
    PlanningDefinitionApi,
    RUNTIME_KEYS,
    assertDefinitionPayload
  });
  window.HarborAgentApi = Object.freeze({
    AgentOperationsApi,
    AgentOperationsApiError,
    assertAgentProjection
  });
  window.HarborAiLensApi = Object.freeze({
    AiLensApi,
    AiLensApiError,
    assertAiLensEvent,
    assertAiLensService,
    assertAiLensSessions,
    assertAiLensSnapshot
  });
})();
