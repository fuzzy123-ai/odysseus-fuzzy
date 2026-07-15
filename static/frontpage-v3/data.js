(function () {
const modelProfiles = {
  'deepseek-v4-flash': {
    route: 'API',
    state: 'ready',
    context: '21% used, 79% free',
    tokens: '426 in / 48 out',
    load: 'normal',
    note: 'fast default'
  },
  'gemma-local': {
    route: 'local',
    state: 'busy',
    context: '12% used, 88% free',
    tokens: 'local budget',
    load: 'busy',
    note: 'database upkeep'
  },
  'old-vision': {
    route: 'API',
    state: 'offline',
    context: 'not available',
    tokens: 'none',
    load: 'problem',
    note: 'needs reconnect'
  }
};

const workspaceOrder = ['agent', 'knowledge', 'planning', 'inbox'];

const workspaceLabels = {
  agent: 'Agent',
  knowledge: 'Knowledge',
  planning: 'Planning',
  inbox: 'Inbox'
};

const documentSamples = {
  'src/services/search_memory.py': {
    title: 'search_memory.py',
    path: 'src/services/search_memory.py',
    type: 'code',
    language: 'python',
    summary: 'Memory search entry point. Collects candidates, caps recent items, and reranks query results.',
    content: `def search_memory(query, session):
  # dynamic highlighting follows the selected language preset
  candidates = collect_candidates(query, session)
  recent = candidates[:240]

  return rerank(query, recent)`
  },
  'static/frontpage-v2/app.js': {
    title: 'app.js',
    path: 'static/frontpage-v2/app.js',
    type: 'code',
    language: 'javascript',
    summary: 'Frontend v2 behavior file. Owns floating windows, chat interactions, toolwheel actions, and document opening.',
    content: `const mode = "code";
function openDocument(file) {
return viewer.render(file, mode);
}`
  },
  'docs/plans/memory-budget.md': {
    title: 'memory-budget.md',
    path: 'docs/plans/memory-budget.md',
    type: 'text',
    summary: 'Planning note for long-session retrieval limits, old-context caps, and hidden work visibility.',
    content: `# Memory Budget

Long sessions should keep old context available without letting retrieval become noisy.

## Rules

- Cap old candidate pools before reranking.
- Keep recent user intent close to the prompt.
- Show hidden work only when the user asks for it.`
  },
  'docs/plans/planning-mcp-roadmap.json': {
    title: 'planning-mcp-roadmap.json',
    path: 'docs/plans/planning-mcp-roadmap.json',
    type: 'code',
    language: 'json',
    summary: 'JSON-first roadmap for exposing Planning through internal and external MCP tools.',
    content: `{
"kind": "odysseus.planning_mcp_roadmap",
"status": "planned",
"goal": "Expose Planning as reliable MCP tools for roadmaps and context packs.",
"recommended_next_step": "Implement PMCP-1 service contract first.",
"tools": [
  "planning_list_roadmaps",
  "planning_read_roadmap",
  "planning_get_context_pack",
  "planning_create_roadmap_draft"
]
}`
  },
  'docs/release-notes.pdf': {
    title: 'release-notes.pdf',
    path: 'docs/release-notes.pdf',
    type: 'pdf',
    page: '1 / 8',
    zoom: '92%',
    summary: 'Release notes preview document used to validate PDF paging, zoom controls, and quiet reading mode.',
    content: `Release Notes

This page represents PDF and office-style documents. Page state and zoom tools stay visible, but out of the reading path.

A document can be read, zoomed, paged, and later connected to the rest of Odysseus without turning this surface into a dashboard.`
  },
  'repo://code-changes': {
    title: 'Code changes since last commit',
    path: 'repo://odysseus/dev',
    type: 'code',
    language: 'diff',
    summary: 'Current workspace diff preview. 2405 new code lines are waiting for review and later clean commits.',
    content: `diff --git a/static/frontpage-v2/index.html b/static/frontpage-v2/index.html
@@ chat header
+ repo chip split into branch action and code delta action
+ +2405 now opens this change preview in Document Viewer
+ branch action remains reserved for clean commit requests

diff --git a/static/frontpage-v2/app.js b/static/frontpage-v2/app.js
@@ document viewer
+ add repository change preview as a code document
+ support diff language preset
+ route code delta clicks into the viewer without closing open documents

diff --git a/static/frontpage-v2/styles.css b/static/frontpage-v2/styles.css
@@ repo chip
+ make branch and delta independent targets inside one compact chip
+ keep the tooltip attached to the whole repository control
+ add hover glow only on the actionable +2405 section

Summary
+2405 code lines since the last commit
8 modified files
Next action: review changes, then ask ABC to prepare clean commits.`
  }
};

const codeLanguageSamples = {
  python: {
    title: 'search_memory.py',
    path: 'src/services/search_memory.py',
    content: documentSamples['src/services/search_memory.py'].content
  },
  javascript: {
    title: 'app.js',
    path: 'static/frontpage-v2/app.js',
    content: documentSamples['static/frontpage-v2/app.js'].content
  },
  html: {
    title: 'document-view.html',
    path: 'static/mockups/document-view.html',
    content: `<article class="document-viewer">
<section>Current document</section>
</article>`
  },
  json: {
    title: 'document-meta.json',
    path: 'data/document-meta.json',
    content: `{
"type": "code",
"language": "python",
"mode": "editor"
}`
  },
  diff: {
    title: 'Code changes since last commit',
    path: 'repo://odysseus/dev',
    content: documentSamples['repo://code-changes'].content
  }
};

const historicalChats = [
  { title: 'Project Runner', subtitle: 'Overview graph layout', age: '4m', state: 'working' },
  { title: 'Settings Scope', subtitle: 'Needs one decision', age: '1h', state: 'question' },
  { title: 'Knowledge Search', subtitle: 'Unread answer', age: '1d', state: 'unread' },
  { title: 'Universal Inbox', subtitle: 'Drop zone rules', age: '2d', state: '' },
  { title: 'Frontend V2', subtitle: 'Toolwheel polish', age: '1W', state: '' }
];

const projectSamples = [
  {
    title: 'Agent Autonomy',
    subtitle: 'completed JSON master',
    progress: '6/6',
    statusColor: 'var(--green)',
    status: 'done',
    next: 'History',
    branch: 'master',
    decision: 'clear',
    chips: ['master roadmap', 'json ready', 'gates passed'],
    todos: [
      {
        type: 'Done',
        title: 'Telegram task lifecycle',
        detail: 'Remote operator can start, pause, query and receive status for bounded agent tasks.',
        source: 'AAE-1 - Jul 02',
        color: 'var(--green)',
        done: true
      },
      {
        type: 'Gate',
        title: 'Live web target approval',
        detail: 'Target domain, crawl depth, page cap, rate limit and login handling were bounded.',
        source: 'gate - Jul 02',
        color: 'var(--green)',
        done: true
      },
      {
        type: 'Evidence',
        title: 'ASV BW live research smoke',
        detail: 'Browser, Telegram, sandbox and Memory/RaptorGraph evidence are linked in JSON.',
        source: 'live smoke - 2h',
        color: 'var(--green)',
        done: true
      }
    ]
  },
  {
    title: 'Frontend V2',
    subtitle: 'active - needs review',
    progress: '8/14',
    statusColor: 'var(--blue)',
    status: 'active',
    next: 'Overview',
    branch: 'dev +84',
    decision: '1 waiting',
    chips: ['repo ready', 'push held'],
    todos: [
      {
        type: 'Review',
        title: 'Check project overview layout',
        detail: 'AI noticed the overview should stay readable next to chat.',
        source: 'from chat - 4m ago',
        color: 'var(--blue)',
        done: false
      },
      {
        type: 'Decision',
        title: 'Choose default roadmap view',
        detail: 'Graph, list, or mixed view needs one default.',
        source: 'from roadmap node',
        color: 'var(--amber)',
        done: false
      },
      {
        type: 'Commit',
        title: 'Prepare clean frontend commit',
        detail: 'Repo has new UI lines since the last checkpoint.',
        source: 'from repo chip',
        color: 'var(--cyan)',
        done: false
      }
    ]
  },
  {
    title: 'Memory Debug',
    subtitle: 'ready - checks green',
    progress: '5/5',
    statusColor: 'var(--green)',
    status: 'ready',
    next: 'Trace cap',
    branch: 'debug +12',
    decision: 'none',
    chips: ['checks green', 'ready'],
    todos: [
      {
        type: 'Task',
        title: 'Confirm old-context cutoff',
        detail: 'AI traced the likely slowdown point; user should approve the cap.',
        source: 'from AI trace',
        color: 'var(--green)',
        done: false
      }
    ]
  },
  {
    title: 'Local Models',
    subtitle: 'waiting - choose setup',
    progress: '2/7',
    statusColor: 'var(--amber)',
    status: 'waiting',
    next: 'Choose setup',
    branch: 'local +3',
    decision: '1 waiting',
    chips: ['waiting', 'local'],
    todos: [
      {
        type: 'Decision',
        title: 'Pick local fallback model',
        detail: 'Gemma is busy; choose fallback behavior for low memory.',
        source: 'from model status',
        color: 'var(--amber)',
        done: false
      }
    ]
  },
  {
    title: 'Project Atlas',
    subtitle: 'blocked - repo missing',
    progress: '1/9',
    statusColor: 'var(--red)',
    status: 'blocked',
    next: 'Create repo',
    branch: 'none',
    decision: 'blocked',
    chips: ['blocked', 'repo missing'],
    todos: [
      {
        type: 'Blocked',
        title: 'Connect repository',
        detail: 'Project cannot run until a repo is mounted.',
        source: 'from project setup',
        color: 'var(--red)',
        done: false
      }
    ]
  }
];

const planningRoadmapDemo = {
  title: 'Agent Autonomy Extensions Master Roadmap',
  subtitle: 'Selected JSON master roadmap. The spine reads left to right, branches show dependent capability tracks, and gates release the next roadmap.',
  source: 'agent-autonomy-extensions-master-roadmap.json',
  schema: 'schema_version 1 - kind odysseus.agent_autonomy_extensions_master_roadmap - status done - updated 2026-07-02',
  fromVersion: 'v0.1',
  toVersion: 'v0.2',
  canvas: { width: 1360, height: 560 },
  nodes: [
    { id: 'v0.1', title: 'Version 0.1', state: 'done', kind: 'version', x: 70, y: 292 },
    { id: 'AAE-1', title: 'Telegram Task Orchestrator', state: 'done', kind: 'roadmap', x: 220, y: 292 },
    { id: 'G-WEB', title: 'Live web approved', state: 'done', kind: 'gate', x: 350, y: 292, affects: ['AAE-1', 'AAE-2', 'AAE-3', 'AAE-4'], label: 'Live web target approved. Domain, crawl depth, page cap, rate limit and login handling are bounded.' },
    { id: 'AAE-2', title: 'Website Research Pipeline', state: 'done', kind: 'roadmap', x: 480, y: 292 },
    { id: 'G-MEM', title: 'Memory policy passed', state: 'done', kind: 'gate', x: 610, y: 176, affects: ['AAE-2', 'AAE-6'], label: 'Memory write policy passed. Reviewed research abstractions may become source-linked Memory and RaptorGraph entries.' },
    { id: 'AAE-6', title: 'Memory And RaptorGraph Knowledge Commit', state: 'done', kind: 'roadmap', x: 760, y: 176 },
    { id: 'AAE-3', title: 'Browser DevTools Understanding', state: 'done', kind: 'roadmap', x: 740, y: 292 },
    { id: 'AAE-4', title: 'No-GPU Visual Observer', state: 'done', kind: 'roadmap', x: 980, y: 292 },
    { id: 'G-SANDBOX', title: 'Sandbox live go', state: 'done', kind: 'gate', x: 350, y: 426, affects: ['AAE-1', 'AAE-5'], label: 'Sandbox execution live go passed. Disposable Podman jobs are allowed with resource limits, scoped mounts and redacted artifacts.' },
    { id: 'AAE-5', title: 'Sandbox Code Execution', state: 'done', kind: 'roadmap', x: 610, y: 426 },
    { id: 'G-EVIDENCE', title: 'Evidence verified', state: 'done', kind: 'gate', x: 1125, y: 292, affects: ['AAE-4', 'AAE-5', 'AAE-6', 'v0.2'], label: 'Evidence verified. Telegram, browser, sandbox and Memory/RaptorGraph smoke evidence is recorded without raw private content.' },
    { id: 'v0.2', title: 'Version 0.2', state: 'done', kind: 'version', x: 1260, y: 292 }
  ],
  edges: [
    { from: 'v0.1', to: 'AAE-1', dashed: true },
    { from: 'AAE-1', to: 'G-WEB' },
    { from: 'G-WEB', to: 'AAE-2' },
    { from: 'AAE-2', to: 'AAE-3' },
    { from: 'AAE-3', to: 'AAE-4' },
    { from: 'AAE-2', to: 'G-MEM' },
    { from: 'G-MEM', to: 'AAE-6' },
    { from: 'AAE-1', to: 'G-SANDBOX' },
    { from: 'G-SANDBOX', to: 'AAE-5' },
    { from: 'AAE-4', to: 'G-EVIDENCE' },
    { from: 'AAE-6', to: 'G-EVIDENCE' },
    { from: 'AAE-5', to: 'G-EVIDENCE' },
    { from: 'G-EVIDENCE', to: 'v0.2', arrow: true }
  ],
  gates: [
    { id: 'live-web-target-approval', state: 'done', x: 350, y: 292, label: 'done. Target, crawl caps and live web bounds were approved.' },
    { id: 'memory-write-policy', state: 'done', x: 610, y: 176, label: 'done. Reviewed knowledge writes are allowed by policy.' },
    { id: 'sandbox-execution-live-go', state: 'done', x: 350, y: 426, label: 'done. Disposable sandbox jobs are allowed with resource limits.' },
    { id: 'live-evidence-verified', state: 'done', x: 1125, y: 292, label: 'done. Live smoke evidence is recorded and redacted.' }
  ],
  rows: [
    ['AAE-1', 'Telegram Task Orchestrator', 'Starts, pauses, queries and reports long-running bounded agent tasks through Telegram.', 'done'],
    ['AAE-2', 'Website Research Pipeline', 'Approved site scopes, crawl caps, source inventory, gaps and redacted synthesis.', 'done'],
    ['AAE-3', 'Browser DevTools Understanding', 'Console, network, DOM, accessibility tree, storage metadata and failed request evidence.', 'done'],
    ['AAE-4', 'No-GPU Visual Observer', 'Software-rendered screenshots, frame sampling and diff evidence without GPU dependency.', 'done'],
    ['AAE-5', 'Sandbox Code Execution', 'Disposable Podman sandboxes with scoped mounts, resource limits and redacted artifacts.', 'done'],
    ['AAE-6', 'Memory And RaptorGraph Knowledge Commit', 'Source-linked candidates become policy-approved knowledge entries with provenance.', 'done']
  ]
};

const planningMcpRoadmap = {
  title: 'Planning MCP Roadmap',
  path: 'docs/plans/planning-mcp-roadmap.json',
  status: 'planned',
  next: 'PMCP-1 service contract',
  summary: 'Shared planning core for internal agents, Codex, and trusted external MCP clients.',
  tools: [
    { name: 'List roadmaps', tool: 'planning_list_roadmaps', state: 'read-only' },
    { name: 'Read roadmap', tool: 'planning_read_roadmap', state: 'read-only' },
    { name: 'Search plans', tool: 'planning_search_roadmaps', state: 'read-only' },
    { name: 'Context pack', tool: 'planning_get_context_pack', state: 'read-only' },
    { name: 'Create draft', tool: 'planning_create_roadmap_draft', state: 'dry-run' },
    { name: 'Apply patch', tool: 'planning_apply_patch', state: 'gated' }
  ],
  gates: [
    { id: 'PLANNING-MCP-READONLY-GO', state: 'ready' },
    { id: 'PLANNING-WRITE-GO', state: 'gated' },
    { id: 'EXTERNAL-MCP-CLIENT-GO', state: 'live-go' }
  ],
  slices: [
    { id: 'PMCP-1', label: 'Service contract', state: 'next', detail: 'List, read, search, validate, context packs.' },
    { id: 'PMCP-2', label: 'Internal MCP', state: 'planned', detail: 'Built-in stdio server for Odysseus agents.' },
    { id: 'PMCP-3', label: 'External policy', state: 'planned', detail: 'Expose read-only tools through MCP server policy.' },
    { id: 'PMCP-5', label: 'Context bridge', state: 'planned', detail: 'Roadmap + memory capsules + source previews.' }
  ]
};

const globalTodoExtras = [
  {
    id: 'global-secret-handoff',
    project: 'Self Control',
    type: 'Blocked',
    title: 'Confirm secret handoff UI',
    detail: 'Backend can request pending secret input; the UI still needs the human-only completion window.',
    source: 'from backend roadmap',
    color: 'var(--red)',
    priority: 'P0',
    updated: '1h',
    done: false
  },
  {
    id: 'global-mcp-parity',
    project: 'Self Control',
    type: 'Question',
    title: 'Pick MCP parity slice',
    detail: 'Choose the smallest safe backend step before broad route parity work expands.',
    source: 'from MASTER thread',
    color: 'var(--teal)',
    priority: 'P2',
    updated: '1d',
    done: false
  },
  {
    id: 'global-snap-group',
    project: 'Frontend V2',
    type: 'Polish',
    title: 'Decide grouped snapping behavior',
    detail: 'Shift-selected windows move together; decide whether grouped snap should stay disabled.',
    source: 'from UI session',
    color: 'var(--blue)',
    priority: 'P3',
    updated: 'now',
    done: false
  }
];

const skillSamples = [
  {
    id: 'project-archivist',
    name: 'Project Archivist',
    category: 'cloud',
    summary: 'Keeps project material organized and connected.',
    purpose: 'Keeps project material organized, connects related files, and creates concise summaries without changing source documents.',
    used: 'When new notes, meeting files, research exports, or loose documents appear near an active project.',
    allowed: ['Move loose files into project folders', 'Add tags', 'Create summaries', 'Link related files'],
    never: ['Delete originals', 'Share files externally', 'Rewrite source documents without versioning'],
    activity: 'Last run: organized Project Atlas 12 min ago.',
    rules: 'May organize project folders automatically when every move is logged and reversible.',
    health: 'ready',
    healthLabel: 'Ready',
    trust: 'High',
    checked: '12 min ago',
    healthReason: 'Recent run completed cleanly and all file moves were reversible.',
    trustReason: '42 logged actions, no conflicts, no protected folder touched.',
    reviewAction: 'No action needed.'
  },
  {
    id: 'invoice-sorter',
    name: 'Invoice Sorter',
    category: 'cloud',
    summary: 'Recognizes invoices and makes finance files searchable.',
    purpose: 'Reads invoice metadata, adds useful tags, and creates summaries for search and review.',
    used: 'When PDFs or scans look like invoices, receipts, licenses, or recurring service bills.',
    allowed: ['Read metadata', 'Tag files', 'Create sidecar summaries', 'Flag missing information'],
    never: ['Edit originals', 'Move finance structure', 'Create shares', 'Infer payments as completed'],
    activity: 'Last run: tagged 18 finance PDFs 31 min ago.',
    rules: 'May tag finance documents automatically. Original files and folder structure are protected.',
    health: 'ready',
    healthLabel: 'Ready',
    trust: 'High',
    checked: '31 min ago',
    healthReason: 'Only metadata and tags changed; originals stayed untouched.',
    trustReason: 'Invoice detection matched known vendors and stayed inside finance rules.',
    reviewAction: 'Next review after 50 more runs or one user correction.'
  },
  {
    id: 'duplicate-cleaner',
    name: 'Duplicate Cleaner',
    category: 'cloud',
    summary: 'Finds duplicates and moves likely copies into quarantine.',
    purpose: 'Finds likely duplicate files and makes cleanup reversible instead of destructive.',
    used: 'When files have matching hashes, repeated names, old exports, or duplicated downloads.',
    allowed: ['Compare files', 'Tag duplicates', 'Move likely copies to quarantine', 'Explain confidence'],
    never: ['Delete files directly', 'Merge files', 'Touch originals without backup', 'Hide conflicts'],
    activity: 'Last run: quarantined 6 likely duplicates 1h ago.',
    rules: 'May quarantine duplicates automatically. Direct deletion remains disabled.',
    health: 'review',
    healthLabel: 'Needs review',
    trust: 'Medium',
    checked: '1h ago',
    healthReason: 'One duplicate group had similar names but only partial hash overlap.',
    trustReason: 'The action is reversible, but confidence dropped below the clean-auto threshold.',
    reviewAction: 'Review the flagged group or tighten duplicate matching rules.'
  },
  {
    id: 'meeting-notes-assistant',
    name: 'Meeting Notes Assistant',
    category: 'cloud',
    summary: 'Creates summaries and action lists next to raw notes.',
    purpose: 'Turns raw meeting notes into summaries, decisions, and action lists while preserving the source.',
    used: 'When meeting notes, transcripts, or planning documents are added or updated.',
    allowed: ['Create summaries', 'Extract todos', 'Link project files', 'Tag topics'],
    never: ['Move raw notes', 'Overwrite original wording', 'Mark todos done without evidence'],
    activity: 'Last run: created 9 meeting summaries 2h ago.',
    rules: 'May create sidecar files. Raw notes are read-only by default.',
    health: 'ready',
    healthLabel: 'Ready',
    trust: 'High',
    checked: '2h ago',
    healthReason: 'Generated sidecar summaries only; no source note was changed.',
    trustReason: 'User accepted the last two summaries without correction.',
    reviewAction: 'No action needed.'
  },
  {
    id: 'memory-curator',
    name: 'Memory Curator',
    category: 'memory',
    summary: 'Keeps durable memory useful and conflict-aware.',
    purpose: 'Reviews candidate memories, merges duplicates, and flags conflicts only when confidence is low.',
    used: 'When repeated facts, preferences, project decisions, or contradictions appear.',
    allowed: ['Suggest durable memories', 'Merge duplicate facts', 'Flag contradictions'],
    never: ['Silently overwrite user intent', 'Store sensitive content without rule permission'],
    activity: 'Last run: merged project UI preferences yesterday.',
    rules: 'Learns from manual corrections and shows review only when uncertain.',
    health: 'review',
    healthLabel: 'Needs review',
    trust: 'Medium',
    checked: 'Yesterday',
    healthReason: 'Two memories describe similar UI preferences with different wording.',
    trustReason: 'The skill should learn from your last correction before merging again.',
    reviewAction: 'Approve which preference wins; the correction becomes training signal.'
  },
  {
    id: 'project-runner',
    name: 'Project Runner',
    category: 'projects',
    summary: 'Turns project roadmaps into visible execution state.',
    purpose: 'Maintains project overview, roadmaps, milestones, and open decisions.',
    used: 'When a project has active plans, blockers, or roadmap updates.',
    allowed: ['Update progress', 'Create todos', 'Surface blockers'],
    never: ['Mark major milestones complete without evidence', 'Delete roadmap history'],
    activity: 'Last run: updated Overview mockup tasks today.',
    rules: 'Can update planning state, but execution changes need logged evidence.',
    health: 'ready',
    healthLabel: 'Ready',
    trust: 'High',
    checked: 'Today',
    healthReason: 'Planning updates were linked to visible roadmap items.',
    trustReason: 'No milestone was marked complete without evidence.',
    reviewAction: 'No action needed.'
  },
  {
    id: 'code-reviewer',
    name: 'Code Reviewer',
    category: 'code',
    summary: 'Reviews code changes for bugs, risks, and missing tests.',
    purpose: 'Finds concrete risks in code changes and keeps comments tied to files and lines.',
    used: 'When code is changed, committed, or prepared for review.',
    allowed: ['Inspect diffs', 'Run local checks', 'Suggest targeted fixes'],
    never: ['Rewrite unrelated files', 'Hide test failures', 'Approve unknown behavior'],
    activity: 'Last run: checked V2 mockup JavaScript today.',
    rules: 'Prioritizes correctness and minimal blast radius over broad refactors.',
    health: 'ready',
    healthLabel: 'Ready',
    trust: 'High',
    checked: 'Today',
    healthReason: 'Last review produced file-specific findings and did not rewrite code.',
    trustReason: 'Uses tests and diffs as evidence before recommending approval.',
    reviewAction: 'No action needed.'
  },
  {
    id: 'automation-monitor',
    name: 'Automation Monitor',
    category: 'automation',
    summary: 'Watches recurring tasks and reports only useful state changes.',
    purpose: 'Tracks background automations, failed jobs, and pending decisions.',
    used: 'When scheduled or unattended work is running.',
    allowed: ['Monitor status', 'Summarize results', 'Escalate failures'],
    never: ['Retry destructive tasks without permission', 'Suppress repeated failures'],
    activity: 'Idle.',
    rules: 'Escalates only when action is needed or confidence drops.',
    health: 'draft',
    healthLabel: 'Draft',
    trust: 'Low',
    checked: 'Not yet checked',
    healthReason: 'This skill is configured but has not passed a review run yet.',
    trustReason: 'It should stay quiet until one test run proves the escalation logic.',
    reviewAction: 'Run skill review before enabling automatic actions.'
  }
];

const knowledgeGraphPalette = {
  Raptor: '#22d3b6',
  Memory: '#4ade80',
  Files: '#16d9f5',
  Projects: '#4b8cff',
  Chats: '#f7b955',
  Code: '#ff5c73'
};

const knowledgeGraphTypes = ['Cluster', 'Summary', 'Source', 'Memory', 'Decision', 'Task'];

const memoryTypeColors = {
  Preference: 'var(--green)',
  Workflow: 'var(--blue)',
  Fact: 'var(--cyan)',
  Project: 'var(--amber)',
  Privacy: 'var(--teal)',
  Decision: 'var(--red)'
};

const memorySamples = [
  {
    id: 'm1',
    title: 'Human labels first',
    text: 'Primary UI labels should be precise, short, and understandable without developer vocabulary.',
    type: 'Preference',
    source: 'Chat',
    project: 'Frontend V2',
    status: 'confirmed',
    confidence: 96,
    lastUsed: '2m',
    evidence: ['User asked for user-first wording.', 'Repeated requests to remove technical clutter.'],
    why: 'ABC is confident enough to apply this automatically.',
    ask: 'No action needed.',
    learn: 'Edits here teach ABC how strict your wording preference should be.'
  },
  {
    id: 'm2',
    title: 'Toolwheel stays minimal',
    text: 'Primary toolwheel categories stay short; deeper actions appear on hover, More, or customization.',
    type: 'Workflow',
    source: 'UI Session',
    project: 'Frontend V2',
    status: 'pinned',
    confidence: 92,
    lastUsed: '5m',
    evidence: ['Toolwheel reduced to four visible actions plus More.', 'Customize will later hide or restore commands.'],
    why: 'Pinned because it protects the main UI direction.',
    ask: 'No action needed.',
    learn: 'Unpinning teaches ABC that this is preference, not a hard rule.'
  },
  {
    id: 'm3',
    title: 'Memory search may slow in long sessions',
    text: 'Long sessions can grow candidate pools and duplicate ranking paths unless old context is capped.',
    type: 'Fact',
    source: 'AI Trace',
    project: 'Memory Debug',
    status: 'attention',
    confidence: 74,
    lastUsed: '18m',
    evidence: ['AI response identified a growing candidate pool.', 'No source trace has been confirmed yet.'],
    why: 'ABC is asking because this came from reasoning, not verified trace evidence.',
    ask: 'Save this as durable memory, or keep it temporary until a trace confirms it?',
    learn: 'Confirm teaches ABC when diagnostic hypotheses are worth saving. Forget teaches ABC to keep unverified traces temporary.'
  },
  {
    id: 'm4',
    title: 'Network background is the default',
    text: 'The network background feels better than the grid and should remain active in V2.',
    type: 'Preference',
    source: 'Design Review',
    project: 'Frontend V2',
    status: 'confirmed',
    confidence: 88,
    lastUsed: '1h',
    evidence: ['User preferred the network variant.', 'User asked for a denser net with fewer blank spots.'],
    why: 'Repeated preference signal is strong enough for automatic use.',
    ask: 'No action needed.',
    learn: 'Changing this teaches ABC how to distinguish stable taste from temporary exploration.'
  },
  {
    id: 'm5',
    title: 'Project Atlas state is probably mock data',
    text: 'Project Atlas looked blocked in mock data, but ABC is not sure this reflects real project state.',
    type: 'Project',
    source: 'Project Setup',
    project: 'Project Atlas',
    status: 'attention',
    confidence: 66,
    lastUsed: '1d',
    evidence: ['Project overview mock data marks it blocked.', 'No live repo link exists in this preview.'],
    why: 'ABC is asking because placeholder facts should not silently become real memory.',
    ask: 'Keep this mock-only, scope it to Project Atlas, or forget it?',
    learn: 'Your choice teaches ABC how to handle demo and placeholder facts.'
  },
  {
    id: 'm6',
    title: 'Sensitive sources stay local',
    text: 'Private source workflows should default to local-only handling unless explicitly approved.',
    type: 'Privacy',
    source: 'Policy',
    project: 'Global',
    status: 'private',
    confidence: 91,
    lastUsed: '3h',
    evidence: ['Secure mode exists in the V2 header.', 'Secure-source planning favors local handling.'],
    why: 'ABC can enforce this automatically, but privacy rules stay visible.',
    ask: 'No action needed.',
    learn: 'Manual changes teach ABC when to tighten or relax privacy routing.'
  },
  {
    id: 'm7',
    title: 'Old sidebar commands are gone',
    text: 'Former sidebar items should move into header, footer, chat, or toolwheel surfaces.',
    type: 'Decision',
    source: 'UI Session',
    project: 'Frontend V2',
    status: 'forgotten',
    confidence: 81,
    lastUsed: '2d',
    evidence: ['Zero-sidebar direction was chosen.', 'This is kept only as undo/history context.'],
    why: 'Recently muted memories remain recoverable for a while.',
    ask: 'No action needed.',
    learn: 'Restoring teaches ABC to keep removed UI-direction memories recoverable longer.'
  },
  {
    id: 'm8',
    title: 'Weak brainstorm ideas stay local',
    text: 'When visual variants are explored, ABC should keep weak preferences local until repeated or confirmed.',
    type: 'Workflow',
    source: 'User Choices',
    project: 'Global',
    status: 'learned',
    confidence: 89,
    lastUsed: 'now',
    evidence: ['User wants fewer manual checks.', 'Manual memory clicks should teach future behavior.'],
    why: 'This is a behavioral rule learned from memory-control decisions.',
    ask: 'No action needed.',
    learn: 'ABC uses this to avoid asking about every small design experiment.'
  }
];

const settingsCatalog = [
  {
    name: 'Models and providers',
    color: 'var(--cyan)',
    legacy: 'Add Models, Added Models, AI Defaults',
    rows: [
      ['Add local model source', 'Connect local Ollama or OpenAI-compatible endpoints, test them, and register them for model selection.', ['local', 'endpoint', 'ollama', 'test'], 'normal', 'button', 'Add source'],
      ['Add API model source', 'Connect cloud/API providers with secure key handoff, provider auth, and status tests.', ['API', 'provider', 'key', 'secret'], 'normal', 'secret', 'Secure setup'],
      ['Connected model sources', 'Review endpoints, online/offline state, enabled state, model list, probe all, clear offline, delete, and copy URLs.', ['added models', 'probe', 'online', 'offline'], 'normal', 'stack', 'Ollama local|ready|green;OpenAI-compatible|online|teal;Old LM Studio|offline|red'],
      ['Visible models per source', 'Hide, pin, or refresh individual models exposed by each endpoint so normal users see a clean list.', ['hidden models', 'pinned', 'refresh'], 'advanced', 'button', 'Manage models'],
      ['Default chat model', 'Choose the model used for new chats and define fallback models if the primary fails.', ['default model', 'chat', 'fallback'], 'normal', 'select', 'deepseek-v4-flash'],
      ['Utility model', 'Pick the small/background model for cleanup, compaction, auto naming, memory retrieval, and low-stakes jobs.', ['utility', 'background', 'compaction'], 'normal', 'select', 'gemma-local'],
      ['Research model', 'Set the model used for long research runs, independent from the normal chat model.', ['research model', 'deep research'], 'normal', 'select', 'same as chat'],
      ['Vision model', 'Enable image understanding, choose a vision-capable model, and set a fallback chain.', ['vision', 'screenshots', 'images'], 'normal', 'switch', 'on'],
      ['Image generation', 'Enable image creation, choose the model, and pick quality presets.', ['image generation', 'quality'], 'normal', 'select', 'medium'],
      ['Speech input', 'Configure speech-to-text for the composer microphone mode, including local/API routing.', ['STT', 'voice input', 'microphone'], 'normal', 'select', 'local first'],
      ['Speech output', 'Configure text-to-speech provider, model, voice, speed, and preview.', ['TTS', 'read aloud', 'voice'], 'advanced', 'button', 'Preview voice'],
      ['Model source ownership', 'Control whether model settings are user-specific, global defaults, inherited from env, or overridden by runtime policy.', ['user scope', 'global', 'env'], 'advanced', 'button', 'Explain source']
    ]
  },
  {
    name: 'Knowledge and search',
    color: 'var(--teal)',
    legacy: 'Search, Deep Research, Memory, Documents',
    rows: [
      ['Web search provider', 'Choose SearXNG, DuckDuckGo, Brave, Google PSE, Tavily, Serper, or disabled.', ['web search', 'provider', 'searxng', 'brave'], 'normal', 'select', 'SearXNG'],
      ['Search provider credentials', 'Store provider URL, API key, Google CX ID, and run a test query without exposing secrets.', ['search key', 'CX', 'URL', 'secret'], 'advanced', 'secret', 'Secure field'],
      ['Search result count', 'Set how many web results are fetched per query, including a custom count.', ['search results', 'count'], 'normal', 'select', '5 results'],
      ['Search fallback chain', 'Try backup providers in order when the primary search provider fails or rate limits.', ['fallback', 'rate limit'], 'advanced', 'button', 'Edit chain'],
      ['Deep research behavior', 'Control source use, token budget, extraction timeout, parallel extraction, and global timeout.', ['deep research', 'tokens', 'timeout'], 'normal', 'button', 'Research rules'],
      ['Knowledge sources', 'Choose where ABC may look: chats, memory, files, mounted folders, documents, Nextcloud, web, notes, and projects.', ['sources', 'documents', 'nextcloud'], 'normal', 'stack', 'Chats and memory|enabled|green;Mounted folders|2 active|teal;Web search|ask first|amber'],
      ['Memory learning', 'Set when ABC learns automatically, asks for review, or keeps information temporary.', ['memory', 'learning', 'review'], 'normal', 'switch', 'on'],
      ['Memory review exceptions', 'Only show conflicts, review items, and uncertain memories when the responsible AI is unsure.', ['memory review', 'conflicts', 'uncertainty'], 'normal', 'button', 'Review rules'],
      ['Raptor memory', 'Control RAPTOR cache, summaries, graph writes, stale-source checks, and large-session retrieval budgets.', ['raptor', 'cache', 'large sessions'], 'advanced', 'button', 'Raptor status'],
      ['Knowledge graph viewer', 'Configure filters, source types, layout density, LOD, minimap, and inspector defaults for large graphs.', ['graph', 'nodes', 'filters'], 'normal', 'button', 'Open graph'],
      ['Universal inbox routing', 'Define how dropped files are extracted, classified, routed, placed, and written into memory/graph state.', ['inbox', 'routing', 'drop files'], 'normal', 'button', 'Inbox rules'],
      ['Knowledge rebuild', 'Rebuild or refresh indexes and derived graph state. Advanced because it can be expensive.', ['rebuild', 'index', 'maintenance'], 'advanced', 'button', 'Start rebuild']
    ]
  },
  {
    name: 'Interface and workflow',
    color: 'var(--blue)',
    legacy: 'Appearance, Shortcuts, theme customizer',
    rows: [
      ['Appearance', 'Choose network or grid background, accent colors, density, opacity preview, and motion level.', ['theme', 'background', 'network', 'grid'], 'normal', 'select', 'Network'],
      ['Theme library', 'Use default themes, personal themes, save/share theme exports, and color harmony helpers.', ['themes', 'save', 'share', 'harmony'], 'normal', 'button', 'Theme library'],
      ['Font and layout', 'Set readable chat typography, terminal/working typography, spacing, chat width, and responsive density.', ['font', 'layout', 'chat width'], 'normal', 'button', 'Typography'],
      ['Toolwheel customization', 'Move, hide, restore, and later add commands back into the wheel without crowding the default UI.', ['toolwheel', 'customize', 'commands'], 'normal', 'button', 'Customize wheel'],
      ['Toolwheel keyboard control', 'Review Alt+Space, number selection, Enter confirm, Esc close, and future arrow navigation.', ['alt space', 'keyboard', 'numbers'], 'normal', 'button', 'Shortcuts'],
      ['Keyboard shortcuts', 'Edit chat switching, Ctrl+Tab, Ctrl+1..9, composer, windows, and accessibility shortcuts.', ['shortcuts', 'ctrl tab', 'ctrl numbers'], 'normal', 'button', 'Edit shortcuts'],
      ['Window behavior', 'Set defaults for floating windows, resize handles, minimize bubbles, focus behavior, and future snap assist.', ['windows', 'snap', 'minimize'], 'normal', 'button', 'Window rules'],
      ['Chat history and title', 'Configure title rename behavior, header history button, recency labels, unread states, and history sidebar defaults.', ['history', 'title', 'rename'], 'normal', 'button', 'History rules'],
      ['Model chip tooltip', 'Choose what the header model tooltip shows: tokens, context size, available context, local/API, load, and cost hints.', ['model chip', 'tooltip', 'tokens'], 'normal', 'button', 'Tooltip fields'],
      ['Reduced motion', 'Turn off nonessential animation and replace motion with quieter state changes.', ['motion', 'accessibility'], 'normal', 'switch', 'off'],
      ['Legacy visibility controls', 'Map old sidebar, chat area, and chat bar visibility controls into the new zero-sidebar shell.', ['sidebar', 'chat area', 'chat bar'], 'advanced', 'button', 'Migration map']
    ]
  },
  {
    name: 'Communication and apps',
    color: 'var(--green)',
    legacy: 'Integrations, Email, Reminders',
    rows: [
      ['Connected apps', 'Manage external service connections in one place: mail, calendar, contacts, vaults, webhooks, tokens, MCP, and plugins.', ['integrations', 'apps', 'accounts'], 'normal', 'button', 'Add app'],
      ['Email accounts', 'Add, edit, test, OAuth-connect, set default, or remove mail accounts.', ['email', 'accounts', 'OAuth'], 'normal', 'button', 'Email accounts'],
      ['Email safety', 'Require AI-written email to be staged as drafts for approval instead of sending immediately.', ['email safety', 'drafts', 'send'], 'normal', 'switch', 'on'],
      ['Email tasks', 'Choose how pending mail tasks, scheduled sends, urgent messages, and inbox follow-ups appear.', ['email tasks', 'scheduled', 'urgent'], 'normal', 'button', 'Mail rules'],
      ['Writing style', 'Extract, edit, and reuse personal writing style for suggested replies.', ['writing style', 'reply'], 'normal', 'button', 'Writing style'],
      ['Reminder delivery', 'Choose reminder channel: in-app, email, ntfy, webhook, or integration-backed delivery.', ['reminders', 'notifications', 'ntfy'], 'normal', 'select', 'In app'],
      ['Reminder message AI', 'Let the utility model write reminder messages and choose an optional persona.', ['reminder synthesis', 'persona'], 'normal', 'switch', 'off'],
      ['Public app URL', 'Set the externally reachable app URL used by reminders, callbacks, and links.', ['public URL', 'callbacks'], 'advanced', 'input', 'https://abc.local'],
      ['Calendar accounts', 'Connect CalDAV calendars, test accounts, and choose which calendars ABC may read or write.', ['calendar', 'caldav'], 'normal', 'button', 'Calendar setup'],
      ['Contacts', 'Connect CardDAV, import/export contacts, add contacts, and use contacts for email compose.', ['contacts', 'carddav'], 'normal', 'button', 'Contacts'],
      ['Telegram intake', 'Configure Telegram plugin intake, voice transcription, image intake, gated replies, dry-run, and local-only behavior.', ['telegram', 'voice', 'plugin'], 'advanced', 'button', 'Telegram']
    ]
  },
  {
    name: 'Tools and automation',
    color: 'var(--blue)',
    legacy: 'Agent Tools, Mounts, Plugins, MCP, Tokens, Webhooks',
    rows: [
      ['Agent limits', 'Set tool call limit, max steps per message, stream timeout, and input token budget.', ['agent', 'tool limit', 'steps'], 'normal', 'input', '20 steps'],
      ['Built-in tools', 'Enable or disable tools available to Agent mode, with plain-language descriptions.', ['tools', 'enable', 'disable'], 'normal', 'button', 'Tool access'],
      ['MCP servers', 'Add MCP servers, choose transport, reconnect, authorize OAuth, and toggle individual MCP tools.', ['MCP', 'servers', 'OAuth'], 'advanced', 'button', 'MCP servers'],
      ['Planning MCP', 'Internal planning bridge for AI clients: read project roadmaps, fetch context packs, and keep write actions gated. Disable it here if Planning should stay local-only.', ['planning MCP', 'roadmaps', 'context packs', 'Codex'], 'normal', 'planning-mcp', 'on'],
      ['Folder mounts', 'Manage permanent mounts: virtual path, host path, owner, allowed tools, read-only, write rules, validate, reload.', ['mounts', 'folders', 'read write'], 'normal', 'button', 'Mounts'],
      ['Temporary chat mount defaults', 'Set defaults for composer-created Mount Folder context nodges.', ['temporary mount', 'composer'], 'normal', 'button', 'Mount defaults'],
      ['Skills and hooks', 'Manage reusable skills, hooks, action recipes, and which are visible in composer/tool menus.', ['skills', 'hooks'], 'normal', 'button', 'Skills'],
      ['Plugins', 'Enable, disable, reload, uninstall, open plugin UI, rescan local plugins, and inspect load errors.', ['plugins', 'extensions'], 'advanced', 'button', 'Installed plugins'],
      ['Plugin depot', 'Install curated plugins from registries with digest verification.', ['plugin depot', 'registry'], 'advanced', 'button', 'Depot'],
      ['API tokens', 'Create, rename, scope, copy once, disable, or revoke tokens for external clients.', ['tokens', 'scopes', 'secret'], 'advanced', 'secret', 'Token vault'],
      ['Webhooks', 'Add, test, enable, disable, or delete webhook targets and event subscriptions.', ['webhooks', 'events'], 'advanced', 'button', 'Webhooks'],
      ['ABC self-control', 'Registry-backed settings that ABC may change itself, including scope, confirmation, secret handoff, and human-only rules.', ['self control', 'settings registry'], 'advanced', 'button', 'Policy registry']
    ]
  },
  {
    name: 'Account and access',
    color: 'var(--amber)',
    legacy: 'Account, Users',
    rows: [
      ['Account', 'Show current account, login state, logout, and personal account details.', ['account', 'login'], 'normal', 'button', 'Account'],
      ['Change password', 'Change the current password with policy-aware validation.', ['password', 'security'], 'normal', 'secret', 'Change password'],
      ['Two-factor authentication', 'Set up, confirm, or disable 2FA.', ['2FA', 'security'], 'normal', 'button', '2FA'],
      ['Registration', 'Allow or block new account signups.', ['registration', 'signup'], 'advanced', 'switch', 'off'],
      ['Users', 'Create, rename, remove, promote, or demote users.', ['users', 'admin'], 'advanced', 'button', 'Manage users'],
      ['User privileges', 'Control who may use Agent mode, browser, shell/files, documents, research, image generation, memory, and daily limits.', ['privileges', 'limits'], 'advanced', 'button', 'Privileges'],
      ['Allowed models per user', 'Restrict individual users to all models, no models, or selected models only.', ['allowed models', 'users'], 'advanced', 'button', 'Model access']
    ]
  },
  {
    name: 'Privacy and data',
    color: 'var(--red)',
    legacy: 'Account safety, Backup, Danger Zone plus V2 secure mode',
    rows: [
      ['Secure mode', 'Toggle local-first behavior, API restrictions, and visual privacy feedback.', ['secure mode', 'privacy', 'local only'], 'normal', 'switch', 'off'],
      ['Data classification', 'Define sensitive data categories, source rules, and which content must stay local.', ['classification', 'sensitive'], 'advanced', 'button', 'Rules'],
      ['Secret handoff', 'Open secure UI fields for API keys and passwords so secrets never pass through chat or tool output.', ['secrets', 'handoff', 'API keys'], 'normal', 'secret', 'Secure input'],
      ['Memory privacy', 'Decide what can become durable memory, what stays temporary, and when ABC must ask first.', ['memory privacy', 'durable'], 'normal', 'button', 'Memory rules'],
      ['Import/export data', 'Export or import user data: memories, presets, settings, skills, preferences, and relevant state.', ['backup', 'export', 'import'], 'normal', 'button', 'Export data'],
      ['Backup policy', 'Configure snapshot behavior, include/exclude research runs and attachments, and backup visibility.', ['backup', 'snapshots'], 'advanced', 'button', 'Backup policy'],
      ['Audit and logs privacy', 'Choose what settings, tool output, provider payloads, and diagnostics may appear in logs.', ['audit', 'logs', 'redaction'], 'advanced', 'button', 'Log policy'],
      ['Danger zone', 'Delete chats, memory, documents, notes, tasks, settings, or other categories with explicit confirmation.', ['wipe', 'delete', 'danger'], 'advanced', 'button', 'Open danger zone']
    ]
  },
  {
    name: 'System and diagnostics',
    color: 'var(--cyan)',
    legacy: 'System, feature flags, diagnostics',
    rows: [
      ['Updates and backups', 'Show version, latest commit, scheduled updater, host service state, backup snapshots, and recent changes.', ['updates', 'version', 'snapshots'], 'normal', 'stack', 'Version|current|green;Schedule|active|green;Backups|snapshots visible|teal'],
      ['Run system actions', 'Trigger update check, backup now, or update now when host gates allow it.', ['update now', 'backup now'], 'advanced', 'button', 'System actions'],
      ['Terminal logs', 'View live diagnostic logs with search, level filter, line limit, refresh, and auto-poll.', ['logs', 'terminal', 'diagnostics'], 'normal', 'button', 'Open logs'],
      ['Feature flags', 'Turn major app features on or off from a safe admin surface.', ['features', 'flags'], 'advanced', 'button', 'Feature flags'],
      ['System health', 'View local health checks, collectors, alerts, runtime status, Docker/Podman/restic availability, and blockers.', ['system health', 'runtime'], 'normal', 'button', 'Health'],
      ['Runtime readiness', 'Inspect API readiness, model source readiness, memory/index readiness, and background worker state.', ['runtime', 'ready'], 'advanced', 'button', 'Readiness'],
      ['Developer diagnostics', 'Advanced troubleshooting for route parity, settings registry coverage, provider tests, and UI/backend mismatch.', ['developer', 'diagnostics'], 'advanced', 'button', 'Diagnostics']
    ]
  }
];

window.HarborV2Data = {
  modelProfiles,
  workspaceOrder,
  workspaceLabels,
  documentSamples,
  codeLanguageSamples,
  historicalChats,
  projectSamples,
  planningRoadmapDemo,
  planningMcpRoadmap,
  globalTodoExtras,
  skillSamples,
  knowledgeGraphPalette,
  knowledgeGraphTypes,
  memoryTypeColors,
  memorySamples,
  settingsCatalog
};
})();
