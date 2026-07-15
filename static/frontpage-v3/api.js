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

  window.HarborPlanningApi = Object.freeze({
    GATE_RUNTIME_KEYS,
    PlanningApiError,
    PlanningDefinitionApi,
    RUNTIME_KEYS,
    assertDefinitionPayload
  });
})();
