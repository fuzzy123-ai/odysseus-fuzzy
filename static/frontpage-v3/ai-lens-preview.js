(function () {
  'use strict';

  const limits = Object.freeze({
    max_sessions: 32,
    max_events_per_session: 256,
    max_bytes_per_session: 2097152,
    max_snapshot_events: 128,
    max_snapshot_bytes: 1048576
  });
  const sessionId = 'fixture-session-001';
  const turnId = 'fixture-turn-001';
  const baseTime = Date.parse('2026-07-10T08:00:00.000Z');

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function source(sourceId, kind, redactionLevel, redactedPreview) {
    return {
      source_id: sourceId,
      kind,
      redaction_level: redactionLevel,
      redacted_preview: redactedPreview || ''
    };
  }

  function event(sequence, eventType, phase, status, summary, payload, options) {
    const settings = options || {};
    const sourceRefs = settings.source_refs || [];
    return {
      schema: 'odysseus.ai_lens.event.v1',
      event_id: `fixture-event-${String(sequence).padStart(3, '0')}`,
      session_id: sessionId,
      turn_id: turnId,
      sequence,
      created_at: new Date(baseTime + (sequence - 1) * 25).toISOString(),
      event_type: eventType,
      phase,
      status,
      truth_level: 'runtime_trace',
      observation_origin: 'synthetic_fixture',
      privacy_level: 'metadata',
      redaction_level: 'redacted',
      source_ref: sourceRefs[0] || null,
      source_refs: sourceRefs,
      summary,
      payload,
      model_id: settings.model_id || '',
      latency_ms: settings.latency_ms || 0,
      raw_content_visible: false
    };
  }

  const events = Object.freeze([
    event(1, 'lens_session_started', 'session', 'started', 'Deterministic AI Lens fixture session started.', {
      fixture: true,
      schema_version: 1
    }),
    event(2, 'query_received', 'input', 'received', 'A bounded fixture query was received.', {
      fixture: true,
      input_chars: 37
    }, {
      source_refs: [source('fixture-query-001', 'fixture', 'redacted', 'Explain the selected project context.')]
    }),
    event(3, 'memory_search_started', 'retrieval', 'started', 'Fixture memory search started.', {
      fixture: true,
      candidate_budget: 8
    }),
    event(4, 'memory_hit', 'retrieval', 'completed', 'One redacted fixture memory matched.', {
      fixture: true,
      rank: 1,
      score: 0.91
    }, {
      source_refs: [source('fixture-memory-001', 'memory', 'redacted', '[redacted fixture memory summary]')],
      latency_ms: 12
    }),
    event(5, 'context_pack_composed', 'context', 'completed', 'Fixture context pack was composed within budget.', {
      fixture: true,
      included_count: 1,
      excluded_count: 2,
      used_tokens: 144
    }),
    event(6, 'model_route_selected', 'model', 'completed', 'Fixture model route selected.', {
      fixture: true,
      route_kind: 'fixture_only',
      local_internals_available: false
    }, {
      model_id: 'fixture-model'
    }),
    event(7, 'tool_call_started', 'tool', 'started', 'Fixture read-only tool call started.', {
      fixture: true,
      tool_kind: 'read_only'
    }, {
      source_refs: [source('fixture-tool-001', 'tool', 'metadata_only')]
    }),
    event(8, 'tool_call_result', 'tool', 'succeeded', 'Fixture tool call completed without raw output.', {
      fixture: true,
      result_count: 1,
      result_redacted: true
    }, {
      source_refs: [source('fixture-tool-001', 'tool', 'metadata_only')],
      latency_ms: 9
    }),
    event(9, 'answer_completed', 'response', 'succeeded', 'Fixture answer completed with bounded provenance.', {
      fixture: true,
      supporting_source_count: 1,
      unsupported_segment_count: 0
    }, {
      source_refs: [source('fixture-answer-001', 'answer', 'metadata_only')],
      latency_ms: 84
    })
  ]);

  const service = Object.freeze({
    schema: 'odysseus.ai_lens.service.v1',
    mode: 'fixture',
    fixture_mode: true,
    preview_mode: true,
    session_count: 1,
    evicted_session_count: 0,
    limits,
    raw_content_visible: false,
    fixture_access_enabled: true,
    write_endpoint_available: false,
    stream_event_limit: 128,
    stream_byte_budget: 524288
  });

  const session = Object.freeze({
    schema: 'odysseus.ai_lens.session_summary.v1',
    session_id: sessionId,
    mode: 'fixture',
    observation_origin: 'synthetic_fixture',
    retained_event_count: 9,
    event_count: 9,
    accepted_event_count: 9,
    evicted_event_count: 0,
    retained_bytes: 6479,
    incomplete: false,
    last_retained_at: '2026-07-10T08:00:00.200Z',
    raw_content_visible: false
  });

  const snapshot = Object.freeze({
    schema: 'odysseus.ai_lens.snapshot.v1',
    session_id: sessionId,
    mode: 'fixture',
    observation_origin: 'synthetic_fixture',
    fixture_mode: true,
    preview_mode: true,
    accepted_event_count: 9,
    event_count: 9,
    retained_event_count: 9,
    returned_event_count: 9,
    evicted_event_count: 0,
    retained_bytes: 6479,
    snapshot_bytes: 7586,
    turn_count: 1,
    first_retained_at: '2026-07-10T08:00:00.000Z',
    last_retained_at: '2026-07-10T08:00:00.200Z',
    incomplete: false,
    truncated: false,
    truncation_reasons: [],
    summary_scope: 'retained_events',
    phase_counts: { context: 1, input: 1, model: 1, response: 1, retrieval: 2, session: 1, tool: 2 },
    event_type_counts: {
      answer_completed: 1,
      context_pack_composed: 1,
      lens_session_started: 1,
      memory_hit: 1,
      memory_search_started: 1,
      model_route_selected: 1,
      query_received: 1,
      tool_call_result: 1,
      tool_call_started: 1
    },
    truth_level_counts: { runtime_trace: 9 },
    privacy_level_counts: { metadata: 9 },
    events,
    limits,
    raw_content_visible: false
  });

  function graphEdge(edgeId, sourceId, targetId, edgeType, score) {
    return { edge_id: edgeId, source_id: sourceId, target_id: targetId, edge_type: edgeType, score };
  }

  const chunkDefinitions = Object.freeze([
    ['chunk-ui-shell-001', 'ui_shell_001', 'V3 workspace shell', 'ui', 'active', 0.82, 90, 78],
    ['chunk-ui-knowledge-002', 'ui_knowledge_002', 'Knowledge workspace contract', 'ui', 'active', 0.98, 86, 300],
    ['chunk-ui-graph-003', 'ui_graph_003', 'Complete graph view', 'ui', 'active', 0.99, 235, 315],
    ['chunk-ui-lens-004', 'ui_lens_004', 'AI Lens overlay', 'ui', 'active', 0.98, 990, 300],
    ['chunk-ui-trace-005', 'ui_trace_005', 'Event trace presentation', 'ui', 'active', 0.88, 220, 82],
    ['chunk-ui-inspector-006', 'ui_inspector_006', 'Selected event inspector', 'ui', 'active', 0.78, 355, 102],
    ['chunk-ui-tokens-007', 'ui_tokens_007', 'V3 design tokens', 'ui', 'active', 0.72, 145, 168],
    ['chunk-ui-mobile-008', 'ui_mobile_008', 'Responsive graph layout', 'ui', 'active', 0.7, 300, 180],
    ['chunk-ui-decision-009', 'ui_decision_009', 'Complete chunk graph decision', 'ui', 'active', 0.96, 1128, 332],

    ['chunk-memory-query-010', 'memory_query_010', 'Query term expansion', 'memory', 'active', 0.74, 418, 430],
    ['chunk-memory-retrieval-011', 'memory_retrieval_011', 'Bounded chunk retrieval', 'memory', 'active', 0.95, 540, 320],
    ['chunk-memory-hit-012', 'memory_hit_012', 'Ranked memory hit', 'memory', 'active', 0.91, 690, 300],
    ['chunk-memory-context-013', 'memory_context_013', 'Context pack composition', 'memory', 'active', 0.86, 545, 478],
    ['chunk-memory-budget-014', 'memory_budget_014', 'Retrieval token budget', 'memory', 'active', 0.8, 675, 445],
    ['chunk-memory-ranking-015', 'memory_ranking_015', 'Chunk ranking policy', 'memory', 'active', 0.82, 770, 505],
    ['chunk-memory-graph-016', 'memory_graph_016', 'Linked chunk expansion', 'memory', 'active', 0.97, 390, 300],
    ['chunk-memory-local-017', 'memory_local_017', 'Local durable memory', 'memory', 'active', 0.76, 490, 565],
    ['chunk-memory-replay-018', 'memory_replay_018', 'Session replay evidence', 'memory', 'active', 0.68, 625, 580],
    ['chunk-memory-legacy-019', 'memory_legacy_019', 'Superseded retrieval note', 'memory', 'superseded', 0.3, 770, 585],

    ['chunk-privacy-redaction-020', 'privacy_redaction_020', 'Redaction before display', 'privacy', 'active', 0.97, 840, 320],
    ['chunk-privacy-metadata-021', 'privacy_metadata_021', 'Metadata-only observation', 'privacy', 'active', 0.86, 838, 88],
    ['chunk-privacy-owner-022', 'privacy_owner_022', 'Owner-scoped memory', 'privacy', 'active', 0.83, 965, 82],
    ['chunk-privacy-refs-023', 'privacy_refs_023', 'Bounded source references', 'privacy', 'active', 0.9, 1088, 135],
    ['chunk-privacy-retention-024', 'privacy_retention_024', 'Retention limits', 'privacy', 'active', 0.76, 855, 188],
    ['chunk-privacy-raw-025', 'privacy_raw_025', 'Raw content remains hidden', 'privacy', 'active', 0.94, 1005, 202],
    ['chunk-privacy-review-026', 'privacy_review_026', 'Sensitive write review', 'privacy', 'active', 0.72, 1125, 238],

    ['chunk-source-nextcloud-027', 'source_nextcloud_027', 'Nextcloud project import', 'sources', 'active', 0.71, 840, 420],
    ['chunk-source-obsidian-028', 'source_obsidian_028', 'Obsidian note import', 'sources', 'active', 0.75, 975, 408],
    ['chunk-source-web-029', 'source_web_029', 'Web research packet', 'sources', 'active', 0.69, 1100, 425],
    ['chunk-source-repo-030', 'source_repo_030', 'Repository context', 'sources', 'active', 0.84, 820, 510],
    ['chunk-source-release-031', 'source_release_031', 'Release notes chunk', 'sources', 'active', 0.78, 945, 505],
    ['chunk-source-roadmap-032', 'source_roadmap_032', 'Roadmap decision chunk', 'sources', 'active', 0.8, 1070, 510],
    ['chunk-source-inbox-033', 'source_inbox_033', 'Universal inbox item', 'sources', 'active', 0.62, 845, 585],
    ['chunk-source-split-034', 'source_split_034', 'Document chunk boundary', 'sources', 'active', 0.77, 985, 590],
    ['chunk-source-provenance-035', 'source_provenance_035', 'Source provenance record', 'sources', 'active', 0.88, 1120, 580],

    ['chunk-ops-runtime-036', 'ops_runtime_036', 'Runtime deployment note', 'operations', 'active', 0.66, 82, 405],
    ['chunk-ops-podman-037', 'ops_podman_037', 'Rootless Podman operation', 'operations', 'active', 0.64, 205, 425],
    ['chunk-ops-model-038', 'ops_model_038', 'Local model routing', 'operations', 'active', 0.73, 320, 410],
    ['chunk-ops-maintenance-039', 'ops_maintenance_039', 'Graph maintenance policy', 'operations', 'active', 0.79, 110, 515],
    ['chunk-ops-api-040', 'ops_api_040', 'Progressive graph contract', 'operations', 'active', 0.89, 240, 505],
    ['chunk-ops-snapshot-041', 'ops_snapshot_041', 'Bounded snapshot envelope', 'operations', 'active', 0.87, 350, 535],
    ['chunk-ops-stream-042', 'ops_stream_042', 'Read-only event stream', 'operations', 'active', 0.74, 150, 605],
    ['chunk-ops-health-043', 'ops_health_043', 'Runtime health status', 'operations', 'active', 0.58, 300, 610]
  ]);

  const graphNodes = Object.freeze(chunkDefinitions.map(([node_id, chunk_ref, title, collection, status, score]) => ({
    node_id,
    label: chunk_ref,
    node_type: 'chunk',
    score
  })));

  const chunkMetadata = Object.freeze(Object.fromEntries(chunkDefinitions.map(([nodeId, chunkRef, title, collection, status]) => [nodeId, {
    chunk_ref: chunkRef,
    title,
    collection,
    status,
    raw_content_visible: false
  }])));

  const graphCoordinates = Object.freeze(chunkDefinitions.map(([node_id, , , , , , x, y]) => ({ node_id, x, y })));

  const graphEdges = Object.freeze([
    graphEdge('edge-ui-shell-tokens', 'chunk-ui-shell-001', 'chunk-ui-tokens-007', 'references', 0.82),
    graphEdge('edge-ui-shell-mobile', 'chunk-ui-shell-001', 'chunk-ui-mobile-008', 'supports', 0.75),
    graphEdge('edge-ui-trace-lens', 'chunk-ui-trace-005', 'chunk-ui-lens-004', 'explains', 0.9),
    graphEdge('edge-ui-inspector-trace', 'chunk-ui-inspector-006', 'chunk-ui-trace-005', 'inspects', 0.83),
    graphEdge('edge-ui-tokens-graph', 'chunk-ui-tokens-007', 'chunk-ui-graph-003', 'styles', 0.74),
    graphEdge('edge-ui-mobile-graph', 'chunk-ui-mobile-008', 'chunk-ui-graph-003', 'adapts', 0.78),
    graphEdge('edge-ui-decision-release', 'chunk-ui-decision-009', 'chunk-source-release-031', 'recorded_in', 0.81),

    graphEdge('edge-memory-query-retrieval', 'chunk-memory-query-010', 'chunk-memory-retrieval-011', 'seeds', 0.88),
    graphEdge('edge-memory-retrieval-ranking', 'chunk-memory-retrieval-011', 'chunk-memory-ranking-015', 'uses', 0.87),
    graphEdge('edge-memory-budget-retrieval', 'chunk-memory-budget-014', 'chunk-memory-retrieval-011', 'constrains', 0.91),
    graphEdge('edge-memory-hit-context', 'chunk-memory-hit-012', 'chunk-memory-context-013', 'selected_for', 0.9),
    graphEdge('edge-memory-context-budget', 'chunk-memory-context-013', 'chunk-memory-budget-014', 'bounded_by', 0.88),
    graphEdge('edge-memory-ranking-hit', 'chunk-memory-ranking-015', 'chunk-memory-hit-012', 'scores', 0.92),
    graphEdge('edge-memory-local-graph', 'chunk-memory-local-017', 'chunk-memory-graph-016', 'indexed_by', 0.84),
    graphEdge('edge-memory-replay-context', 'chunk-memory-replay-018', 'chunk-memory-context-013', 'replays', 0.72),
    graphEdge('edge-memory-legacy-local', 'chunk-memory-legacy-019', 'chunk-memory-local-017', 'superseded_by', 0.63),

    graphEdge('edge-privacy-redaction-metadata', 'chunk-privacy-redaction-020', 'chunk-privacy-metadata-021', 'produces', 0.88),
    graphEdge('edge-privacy-metadata-raw', 'chunk-privacy-metadata-021', 'chunk-privacy-raw-025', 'keeps_hidden', 0.94),
    graphEdge('edge-privacy-owner-refs', 'chunk-privacy-owner-022', 'chunk-privacy-refs-023', 'scopes', 0.9),
    graphEdge('edge-privacy-retention-metadata', 'chunk-privacy-retention-024', 'chunk-privacy-metadata-021', 'limits', 0.83),
    graphEdge('edge-privacy-review-redaction', 'chunk-privacy-review-026', 'chunk-privacy-redaction-020', 'requires', 0.84),
    graphEdge('edge-privacy-refs-raw', 'chunk-privacy-refs-023', 'chunk-privacy-raw-025', 'redacts', 0.88),

    graphEdge('edge-source-nextcloud-split', 'chunk-source-nextcloud-027', 'chunk-source-split-034', 'split_into', 0.84),
    graphEdge('edge-source-obsidian-split', 'chunk-source-obsidian-028', 'chunk-source-split-034', 'split_into', 0.86),
    graphEdge('edge-source-web-provenance', 'chunk-source-web-029', 'chunk-source-provenance-035', 'traced_by', 0.89),
    graphEdge('edge-source-repo-provenance', 'chunk-source-repo-030', 'chunk-source-provenance-035', 'traced_by', 0.91),
    graphEdge('edge-source-release-repo', 'chunk-source-release-031', 'chunk-source-repo-030', 'derived_from', 0.82),
    graphEdge('edge-source-roadmap-nextcloud', 'chunk-source-roadmap-032', 'chunk-source-nextcloud-027', 'imported_with', 0.72),
    graphEdge('edge-source-inbox-split', 'chunk-source-inbox-033', 'chunk-source-split-034', 'split_into', 0.79),
    graphEdge('edge-source-split-provenance', 'chunk-source-split-034', 'chunk-source-provenance-035', 'retains', 0.93),
    graphEdge('edge-source-provenance-refs', 'chunk-source-provenance-035', 'chunk-privacy-refs-023', 'exposes_as', 0.9),

    graphEdge('edge-ops-runtime-podman', 'chunk-ops-runtime-036', 'chunk-ops-podman-037', 'deployed_with', 0.8),
    graphEdge('edge-ops-runtime-health', 'chunk-ops-runtime-036', 'chunk-ops-health-043', 'observed_by', 0.72),
    graphEdge('edge-ops-model-runtime', 'chunk-ops-model-038', 'chunk-ops-runtime-036', 'runs_on', 0.7),
    graphEdge('edge-ops-maintenance-model', 'chunk-ops-maintenance-039', 'chunk-ops-model-038', 'assigned_to', 0.76),
    graphEdge('edge-ops-api-snapshot', 'chunk-ops-api-040', 'chunk-ops-snapshot-041', 'defines', 0.93),
    graphEdge('edge-ops-snapshot-stream', 'chunk-ops-snapshot-041', 'chunk-ops-stream-042', 'feeds', 0.91),
    graphEdge('edge-ops-stream-trace', 'chunk-ops-stream-042', 'chunk-ui-trace-005', 'renders_as', 0.86),
    graphEdge('edge-ops-api-ui-graph', 'chunk-ops-api-040', 'chunk-ui-graph-003', 'serves', 0.92),
    graphEdge('edge-ops-maintenance-memory-graph', 'chunk-ops-maintenance-039', 'chunk-memory-graph-016', 'maintains', 0.84),

    graphEdge('edge-cross-split-query', 'chunk-source-split-034', 'chunk-memory-query-010', 'searchable_by', 0.8),
    graphEdge('edge-cross-nextcloud-memory', 'chunk-source-nextcloud-027', 'chunk-memory-local-017', 'stored_as', 0.76),
    graphEdge('edge-cross-repo-ui-shell', 'chunk-source-repo-030', 'chunk-ui-shell-001', 'documents', 0.77),
    graphEdge('edge-cross-roadmap-decision', 'chunk-source-roadmap-032', 'chunk-ui-decision-009', 'supports', 0.79),
    graphEdge('edge-cross-snapshot-privacy', 'chunk-ops-snapshot-041', 'chunk-privacy-metadata-021', 'bounded_by', 0.87),
    graphEdge('edge-cross-context-redaction', 'chunk-memory-context-013', 'chunk-privacy-redaction-020', 'filtered_by', 0.93),

    graphEdge('trace-knowledge-graph', 'chunk-ui-knowledge-002', 'chunk-ui-graph-003', 'requests', 1),
    graphEdge('trace-graph-expansion', 'chunk-ui-graph-003', 'chunk-memory-graph-016', 'links_to', 0.99),
    graphEdge('trace-expansion-retrieval', 'chunk-memory-graph-016', 'chunk-memory-retrieval-011', 'expands_to', 1),
    graphEdge('trace-retrieval-hit', 'chunk-memory-retrieval-011', 'chunk-memory-hit-012', 'ranks', 1),
    graphEdge('trace-hit-redaction', 'chunk-memory-hit-012', 'chunk-privacy-redaction-020', 'filtered_by', 0.99),
    graphEdge('trace-redaction-lens', 'chunk-privacy-redaction-020', 'chunk-ui-lens-004', 'presented_by', 1),
    graphEdge('trace-lens-decision', 'chunk-ui-lens-004', 'chunk-ui-decision-009', 'supports', 0.98)
  ]);

  const traceNodeIds = Object.freeze([
    'chunk-ui-knowledge-002', 'chunk-ui-graph-003', 'chunk-memory-graph-016',
    'chunk-memory-retrieval-011', 'chunk-memory-hit-012', 'chunk-privacy-redaction-020',
    'chunk-ui-lens-004', 'chunk-ui-decision-009'
  ]);
  const traceEdgeIds = Object.freeze([
    'trace-knowledge-graph', 'trace-graph-expansion', 'trace-expansion-retrieval',
    'trace-retrieval-hit', 'trace-hit-redaction', 'trace-redaction-lens', 'trace-lens-decision'
  ]);

  const knowledgeGraph = Object.freeze({
    schema: 'odysseus.knowledge_chunk_graph.lens_view.v1',
    mode: 'fixture',
    preview_mode: true,
    graph: {
      graph_query_id: 'fixture-chunk-overview-001',
      graph_ref: 'knowledge-chunk-graph',
      viewport: { viewport_ref: 'all-chunks-overview', node_ref: '' },
      query_kind: 'overview',
      budget: { limit: 64, max_nodes: 64, max_edges: 128, depth: 0, max_hops: 0, time_budget_ms: 120, payload_budget_bytes: 131072 },
      nodes: graphNodes,
      edges: graphEdges,
      aggregates: [
        { aggregate_id: 'chunk-total', label: 'Chunks', count: graphNodes.length },
        { aggregate_id: 'relation-total', label: 'Relations', count: graphEdges.length },
        { aggregate_id: 'relevant-chunks', label: 'Relevant chunks', count: traceNodeIds.length }
      ],
      node_count: graphNodes.length,
      edge_count: graphEdges.length,
      status: 'complete',
      partial: false,
      clipped: false,
      next_cursor: '',
      reason: '',
      next_action: '',
      evidence_ref: 'fixture:knowledge-chunk-graph'
    },
    chunks: chunkMetadata,
    layout: {
      schema: 'odysseus.graph.layout.v1',
      coordinate_system: 'viewport_1200x660',
      width: 1200,
      height: 660,
      clusters: [
        { cluster_id: 'ui', label: 'UI chunks', x: 225, y: 128, rx: 190, ry: 92 },
        { cluster_id: 'operations', label: 'Operations chunks', x: 220, y: 520, rx: 190, ry: 135 },
        { cluster_id: 'memory', label: 'Memory + retrieval chunks', x: 610, y: 510, rx: 225, ry: 140 },
        { cluster_id: 'privacy', label: 'Privacy + policy chunks', x: 985, y: 150, rx: 190, ry: 108 },
        { cluster_id: 'sources', label: 'Source chunks', x: 980, y: 515, rx: 205, ry: 138 }
      ],
      coordinates: graphCoordinates
    },
    trace: {
      schema: 'odysseus.ai_lens.chunk_trace.v1',
      session_id: sessionId,
      node_ids: traceNodeIds,
      edge_ids: traceEdgeIds,
      event_node_map: {
        'fixture-event-003': 'chunk-memory-retrieval-011',
        'fixture-event-004': 'chunk-memory-hit-012',
        'fixture-event-009': 'chunk-ui-decision-009'
      }
    },
    raw_content_visible: false
  });

  class AiLensPreviewApi {
    async getService() {
      return clone(service);
    }

    async listSessions() {
      return {
        schema: 'odysseus.ai_lens.sessions.v1',
        mode: 'fixture',
        fixture_mode: true,
        preview_mode: true,
        session_count: 1,
        sessions: [clone(session)],
        raw_content_visible: false
      };
    }

    async getSnapshot(requestedSessionId, limit) {
      if (requestedSessionId !== sessionId) {
        const error = new Error('AI Lens simulation session not found.');
        error.status = 404;
        throw error;
      }
      const result = clone(snapshot);
      const boundedLimit = Math.max(1, Math.min(128, Number(limit) || 128));
      if (boundedLimit < result.events.length) {
        result.events = result.events.slice(-boundedLimit);
        result.returned_event_count = result.events.length;
        result.truncated = true;
        result.truncation_reasons = ['snapshot_event_budget'];
      }
      return result;
    }

    async getKnowledgeGraph(requestedSessionId) {
      if (requestedSessionId !== sessionId) {
        const error = new Error('AI Lens simulation session not found.');
        error.status = 404;
        throw error;
      }
      return clone(knowledgeGraph);
    }

    streamSession(requestedSessionId, options) {
      const settings = options || {};
      let closed = false;
      const timers = [];
      if (requestedSessionId !== sessionId) {
        timers.push(window.setTimeout(() => settings.onError?.(new Error('AI Lens simulation session not found.')), 0));
      } else {
        events.forEach((item, index) => {
          timers.push(window.setTimeout(() => {
            if (!closed) settings.onEvent?.(clone(item));
          }, 18 * (index + 1)));
        });
        timers.push(window.setTimeout(() => {
          if (closed) return;
          settings.onEnd?.({
            schema: 'odysseus.ai_lens.stream_end.v1',
            session_id: sessionId,
            available_event_count: events.length,
            emitted_event_count: events.length,
            byte_limited: false,
            snapshot_incomplete: false,
            snapshot_truncated: false,
            raw_content_visible: false
          });
        }, 18 * (events.length + 1)));
      }
      return () => {
        closed = true;
        timers.forEach(timer => window.clearTimeout(timer));
      };
    }
  }

  window.HarborAiLensPreview = Object.freeze({ AiLensPreviewApi });
})();
