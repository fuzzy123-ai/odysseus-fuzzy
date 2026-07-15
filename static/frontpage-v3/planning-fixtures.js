(function () {
  'use strict';

  const HASH_A = 'sha256:' + '7a'.repeat(32);
  const HASH_B = 'sha256:' + '4c'.repeat(32);

  const project = {
    project_id: 'harbor-one',
    title: 'Harbor One',
    objective: 'Keep project intent, scope, dependencies and acceptance readable before execution begins.',
    scope: {
      in: ['Versioned Planning definitions', 'Exact Agent handoff drafts'],
      out: ['Running operation', 'Mutable execution telemetry']
    },
    constraints: [
      'Planning remains definition-only.',
      'Every Agent handoff pins one approved revision and content hash.'
    ],
    roadmap_refs: ['planning-definition-editor', 'temporal-light-agent-execution'],
    latest_approved_revision: {
      'planning-definition-editor': { revision: 5, content_hash: HASH_A },
      'temporal-light-agent-execution': { revision: 6, content_hash: HASH_B }
    },
    draft_refs: []
  };

  function node(nodeId, kind, title, objective, dependsOn, extras) {
    const options = extras || {};
    return {
      node_id: nodeId,
      kind,
      title,
      objective,
      depends_on: dependsOn,
      gate_ids: options.gateIds || [],
      deliverables: options.deliverables || [],
      allowed_paths: options.allowedPaths || [],
      blocked_paths: options.blockedPaths || [],
      capability_requirements: options.capabilities || [],
      verification_rule_ids: options.rules || ['definition-contract']
    };
  }

  const editorNodes = [
    node('definition-contract', 'group', 'Definition contract', 'Freeze the boundary between Planning definitions and Agent operation.', [], {
      deliverables: ['Canonical Definition v2 schema', 'Recursive runtime-field denylist']
    }),
    node('read-model', 'work', 'Read model', 'Read one immutable project and roadmap revision.', ['definition-contract'], {
      deliverables: ['Project index', 'Roadmap revision reader'],
      allowedPaths: ['src/planning_revision_store.py']
    }),
    node('revision-editor', 'work', 'Revision editor', 'Propose and validate definition changes without mutating the approved source.', ['read-model'], {
      deliverables: ['Draft diff', 'Validation result', 'Discard and undo affordances'],
      allowedPaths: ['static/frontpage-v3/planning.js']
    }),
    node('ux-acceptance', 'gate', 'Planning UX acceptance', 'Keep the accepted Calm Control Room direction definition-only.', ['read-model'], {
      gateIds: ['HPA-PLANNING-UX-ACCEPTANCE'],
      deliverables: ['Desktop, mobile and 200 percent zoom evidence']
    }),
    node('agent-handoff', 'work', 'Agent handoff', 'Prepare one exact composer draft without starting execution.', ['revision-editor', 'ux-acceptance'], {
      deliverables: ['Pinned revision and hash', 'Non-launching /abc composer draft']
    }),
    node('browser-boundary', 'work', 'Boundary acceptance', 'Prove that rendered Planning contains no running-operation controls or data.', ['agent-handoff'], {
      deliverables: ['DOM denylist assertions', 'Positive edit and handoff path']
    }),
    node('definition-ready', 'milestone', 'Definition ready', 'Mark the definition package ready for an operator-controlled handoff.', ['browser-boundary'], {
      deliverables: ['Definition acceptance evidence']
    })
  ];

  const editorRoadmap = {
    roadmap_id: 'planning-definition-editor',
    project_id: 'harbor-one',
    revision: 5,
    content_hash: HASH_A,
    revision_state: 'approved',
    title: 'Planning Definition Editor',
    objective: 'Make Planning the authoritative editor for versioned project and roadmap definitions.',
    assumptions: ['Approved revisions are immutable.', 'Agent owns all running operation.'],
    constraints: ['No direct workflow client.', 'No automatic composer submission.'],
    nodes: editorNodes,
    edges: editorNodes.flatMap(item => item.depends_on.map(dependency => ({
      from: dependency,
      to: item.node_id,
      kind: 'depends_on'
    }))),
    gates: [
      {
        gate_id: 'HPA-PLANNING-UX-ACCEPTANCE',
        kind: 'design',
        title: 'Planning UX acceptance',
        blocks: ['agent-handoff', 'browser-boundary'],
        decision_needed: 'Accept the V3 Calm Control Room direction for a definition-only Planning surface.',
        safe_default: 'Keep the preview definition-only and do not cut over the root UI.',
        approval_scope_schema: { type: 'object', additionalProperties: false },
        required_verification_rule_ids: ['viewport-contract']
      }
    ],
    done_contract: {
      required_node_ids: editorNodes.map(item => item.node_id),
      required_gate_ids: ['HPA-PLANNING-UX-ACCEPTANCE'],
      verification_rules: [
        { rule_id: 'definition-contract', kind: 'static', description: 'Definition v2 validates without runtime fields.' },
        { rule_id: 'viewport-contract', kind: 'visual', description: 'Desktop, mobile and 200 percent zoom remain readable.' }
      ],
      completion_rule: 'all_required_nodes_and_gates'
    },
    source_refs: ['docs/plans/planning-definition-editor-roadmap.json'],
    created_at: '2026-07-13T20:21:18Z',
    updated_at: '2026-07-15T11:55:12Z'
  };

  const temporalNodes = [
    node('workflow-contract', 'group', 'Deterministic contract', 'Define stable workflow inputs and outputs.', [], {
      deliverables: ['Workflow manifest', 'State transition table']
    }),
    node('history', 'work', 'Durable history', 'Persist replay-safe decisions and sanitized events.', ['workflow-contract'], {
      deliverables: ['History reader', 'Replay tests']
    }),
    node('activities', 'work', 'Activities and heartbeats', 'Bound effectful work behind idempotent activities.', ['history'], {
      deliverables: ['Activity contracts', 'Heartbeat protocol']
    }),
    node('signals', 'work', 'Signals and idempotency', 'Accept durable steering with duplicate suppression.', ['history'], {
      deliverables: ['Signal receipts', 'Idempotency keys']
    }),
    node('agent-projection', 'milestone', 'Agent projection', 'Expose operation only in the Agent surface.', ['activities', 'signals'], {
      deliverables: ['Agent operation API']
    })
  ];

  const temporalRoadmap = {
    roadmap_id: 'temporal-light-agent-execution',
    project_id: 'harbor-one',
    revision: 6,
    content_hash: HASH_B,
    revision_state: 'approved',
    title: 'Temporal Light Agent Execution',
    objective: 'Provide deterministic orchestration, durable history, activities, heartbeats, signals and idempotency.',
    assumptions: ['Execution projection is owned by Agent.'],
    constraints: ['Planning renders definitions only.'],
    nodes: temporalNodes,
    edges: temporalNodes.flatMap(item => item.depends_on.map(dependency => ({ from: dependency, to: item.node_id, kind: 'depends_on' }))),
    gates: [],
    done_contract: {
      required_node_ids: temporalNodes.map(item => item.node_id),
      required_gate_ids: [],
      verification_rules: [
        { rule_id: 'definition-contract', kind: 'static', description: 'The definition remains replay-independent.' }
      ],
      completion_rule: 'all_required_nodes_and_gates'
    },
    source_refs: ['docs/plans/temporal-light-agent-execution-roadmap.json'],
    created_at: '2026-07-13T20:21:18Z',
    updated_at: '2026-07-15T11:47:40Z'
  };

  const roadmaps = [editorRoadmap, temporalRoadmap];

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function readModelFor(roadmapId, originState) {
    const roadmap = roadmaps.find(item => item.roadmap_id === roadmapId) || roadmaps[0];
    const selectedProject = clone(project);
    selectedProject.roadmap_refs = [roadmap.roadmap_id];
    selectedProject.latest_approved_revision = {
      [roadmap.roadmap_id]: {
        revision: roadmap.revision,
        content_hash: roadmap.content_hash
      }
    };
    return {
      schema: 'odysseus.planning.definition_read_model.v2',
      project: selectedProject,
      roadmap: clone(roadmap),
      graph: {
        nodes: clone(roadmap.nodes),
        edges: clone(roadmap.edges),
        gate_definitions: clone(roadmap.gates)
      },
      origin: {
        state: originState || 'live',
        source: 'planning_revision_store',
        reason: originState === 'stale' ? 'definition_snapshot_older_than_catalog' : 'definition_snapshot_loaded',
        as_of: roadmap.updated_at
      },
      read_only: true,
      launch_authorized: false
    };
  }

  function catalog(originState) {
    return {
      source: 'fixture',
      scenario: originState || 'fixture',
      projects: [{
        project_id: project.project_id,
        title: project.title,
        roadmap_count: roadmaps.length,
        revision_count: roadmaps.reduce((total, item) => total + item.revision, 0),
        latest_updated_at: editorRoadmap.updated_at
      }],
      project: clone(project),
      roadmaps: roadmaps.map(item => ({
        project_id: item.project_id,
        roadmap_id: item.roadmap_id,
        title: item.title,
        revision_count: item.revision,
        newest_revision: item.revision,
        newest_revision_state: item.revision_state,
        latest_approved_revision: item.revision,
        latest_approved_hash: item.content_hash,
        updated_at: item.updated_at
      })),
      readModel: readModelFor(editorRoadmap.roadmap_id, originState === 'stale' ? 'stale' : 'live')
    };
  }

  function scenario(name) {
    const normalized = String(name || 'fixture').toLowerCase();
    if (normalized === 'unavailable' || normalized === 'error' || normalized === 'empty') {
      return {
        source: 'fixture',
        scenario: normalized,
        projects: [],
        project: null,
        roadmaps: [],
        readModel: null,
        message: normalized === 'unavailable'
          ? 'The definition source is unavailable.'
          : normalized === 'error'
            ? 'The definition response could not be validated.'
            : 'No Planning definitions are available yet.'
      };
    }
    const value = catalog(normalized);
    if (normalized === 'conflict') value.conflict = {
      title: 'Revision conflict',
      detail: 'The preview base no longer matches the selected approved revision.'
    };
    return value;
  }

  window.HarborPlanningFixtures = Object.freeze({
    catalog: () => catalog('fixture'),
    clone,
    readModelFor,
    scenario
  });
})();
