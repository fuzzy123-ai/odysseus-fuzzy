(function () {
  const stage = document.getElementById('stage');
  const brushCanvas = document.getElementById('grid-brush');
  const brushCtx = brushCanvas.getContext('2d');
  const toolwheel = document.getElementById('toolwheel');
  const workspace = stage.querySelector('.workspace');
  const topline = stage.querySelector('.topline');
  const aurora = stage.querySelector('.aurora');
  const wheelArrow = document.getElementById('wheel-arrow');
  const wheelCore = document.getElementById('wheel-core');
  const coreNewTree = toolwheel.querySelector('.core-new-tree');
  const readout = document.getElementById('wheel-readout');
  const buildState = document.getElementById('build-state');
  const coreWindow = document.querySelector('[data-core]');
  const chatTitle = coreWindow.querySelector('.chat-title');
  const chatTitleEditor = coreWindow.querySelector('.chat-title-editor');
  const headerModelChip = coreWindow.querySelector('[data-header-model-chip]');
  const currentModelLabel = coreWindow.querySelector('[data-current-model-label]');
  const messages = coreWindow.querySelector('.messages');
  const promptInput = coreWindow.querySelector('.prompt-input');
  const composerModelChooser = coreWindow.querySelector('[data-composer-model-chooser]');
  const composerModelButton = coreWindow.querySelector('[data-model-chooser-button]');
  const composerModelMenu = coreWindow.querySelector('[data-model-chooser-menu]');
  const composerModelLabel = coreWindow.querySelector('[data-composer-model-label]');
  const chatHistoryButton = coreWindow.querySelector('[data-chat-history-toggle]');
  const chatHistoryPanel = coreWindow.querySelector('[data-chat-history-panel]');
  const chatHistoryList = coreWindow.querySelector('[data-chat-history-list]');
  const chatHistoryCount = coreWindow.querySelector('[data-chat-history-count]');
  const chatHistorySearch = coreWindow.querySelector('.chat-history-search');
  const contextNodges = coreWindow.querySelector('[data-context-nodges]');
  const leftNodge = document.getElementById('chat-nodge-left');
  const rightNodge = document.getElementById('chat-nodge-right');
  const chatCarousel = document.getElementById('chat-carousel');
  const privacyToggle = document.getElementById('privacy-toggle');
  const universalInbox = document.getElementById('universal-inbox');
  const universalInboxTrigger = document.getElementById('universal-inbox-trigger');
  const urlParams = new URLSearchParams(window.location.search);
  const backgroundMode = urlParams.get('bg') === 'grid' ? 'grid' : 'network';
  const wheelOverlayMode = urlParams.get('wheel') === 'dim' ? 'dim' : 'depth';

  let brushDpr = 1;
  let gridSignals = [];
  let networkNodes = [];
  let networkEdges = [];
  let networkSignals = [];
  let brushFrame = 0;
  let lastWheelPointer = null;
  let wheelOpenPointer = null;
  let focusedNode = null;
  let coreMenuTimer = null;
  let preparedChats = 0;
  let zTop = 20;
  let activeChatIndex = 0;
  let lastSelectedModel = 'deepseek-v4-flash';
  let inboxHover = false;
  let inboxTriggerHover = false;
  let inboxDragDepth = 0;
  let inboxCloseTimer = null;
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
    }
  };
  const chatSpaces = [{
    id: 'chat-1',
    title: chatTitle.textContent.trim(),
    messages: messages.innerHTML,
    prompt: promptInput.value,
    model: lastSelectedModel,
    modelState: 'ready',
    isNew: false,
    lastAnswer: 'now',
    unread: false,
    needsQuestion: false,
    contexts: []
  }];
  const historicalChats = [
    { title: 'Project Runner', subtitle: 'Overview graph layout', age: '4m', state: 'working' },
    { title: 'Settings Scope', subtitle: 'Needs one decision', age: '1h', state: 'question' },
    { title: 'Knowledge Search', subtitle: 'Unread answer', age: '1d', state: 'unread' },
    { title: 'Universal Inbox', subtitle: 'Drop zone rules', age: '2d', state: '' },
    { title: 'Frontend V2', subtitle: 'Toolwheel polish', age: '1W', state: '' }
  ];
  const projectSamples = [
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
  const todosViewState = {
    filter: 'All',
    sort: 'Project',
    selectedId: null,
    query: ''
  };
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
  const skillsViewState = {
    filter: 'all',
    selectedId: 'project-archivist',
    query: ''
  };
  const knowledgeGraphPalette = {
    Raptor: '#22d3b6',
    Memory: '#4ade80',
    Files: '#16d9f5',
    Projects: '#4b8cff',
    Chats: '#f7b955',
    Code: '#ff5c73'
  };
  const knowledgeGraphSources = Object.keys(knowledgeGraphPalette);
  const knowledgeGraphTypes = ['Cluster', 'Summary', 'Source', 'Memory', 'Decision', 'Task'];
  const knowledgeGraphState = {
    source: 'All',
    type: 'All',
    query: '',
    density: 72,
    trust: 38,
    cluster: true,
    labels: 'Focus',
    layout: 'Semantic',
    selectedId: 'kg-14'
  };
  let knowledgeGraphNodes = [];
  let knowledgeGraphEdges = [];
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
      evidence: ['GDPR mode exists in the V2 header.', 'Secure-source planning favors local handling.'],
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
  const memoryState = {
    tab: 'All',
    type: 'All',
    source: 'All',
    query: '',
    selectedId: null,
    sortAttentionFirst: true,
    filtersOpen: true,
    detailOpen: false
  };
  const settingsViewState = {
    query: '',
    advanced: false
  };
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
      legacy: 'Account safety, Backup, Danger Zone plus V2 GDPR',
      rows: [
        ['Global privacy mode', 'Toggle GDPR/local-first behavior, API restrictions, and visual privacy feedback.', ['GDPR', 'privacy', 'local only'], 'normal', 'switch', 'off'],
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

  function resizeBrushCanvas() {
    brushDpr = Math.min(window.devicePixelRatio || 1, 2);
    brushCanvas.width = Math.floor(window.innerWidth * brushDpr);
    brushCanvas.height = Math.floor(window.innerHeight * brushDpr);
    brushCanvas.style.width = window.innerWidth + 'px';
    brushCanvas.style.height = window.innerHeight + 'px';
    brushCtx.setTransform(brushDpr, 0, 0, brushDpr, 0, 0);
    if (backgroundMode === 'network') {
      setupNetwork();
    }
  }

  function drawExactGrid() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    const grid = 44;
    const fade = brushCtx.createRadialGradient(w * 0.5, h * 0.52, 0, w * 0.5, h * 0.52, Math.max(w, h) * 0.58);
    fade.addColorStop(0, 'rgba(255,255,255,1)');
    fade.addColorStop(0.68, 'rgba(255,255,255,0.72)');
    fade.addColorStop(1, 'rgba(255,255,255,0)');

    brushCtx.save();
    brushCtx.globalCompositeOperation = 'source-over';
    brushCtx.strokeStyle = 'rgba(69, 215, 255, 0.048)';
    brushCtx.lineWidth = 1;

    for (let x = 0.5; x <= w; x += grid) {
      brushCtx.beginPath();
      brushCtx.moveTo(x, 0);
      brushCtx.lineTo(x, h);
      brushCtx.stroke();
    }

    for (let y = 0.5; y <= h; y += grid) {
      brushCtx.beginPath();
      brushCtx.moveTo(0, y);
      brushCtx.lineTo(w, y);
      brushCtx.stroke();
    }

    brushCtx.globalCompositeOperation = 'destination-in';
    brushCtx.fillStyle = fade;
    brushCtx.fillRect(0, 0, w, h);
    brushCtx.restore();
  }

  function addGridSignal() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    const grid = 44;
    const horizontal = Math.random() > 0.38;
    const line = Math.round((horizontal ? h : w) * (0.18 + Math.random() * 0.66) / grid) * grid;
    const dir = Math.random() > 0.5 ? 1 : -1;
    const span = horizontal ? w : h;
    const start = dir > 0 ? -grid : span + grid;
    const speed = 36 + Math.random() * 48;
    const life = (span + grid * 2) / speed * 1000;

    gridSignals.push({
      horizontal,
      line,
      pos: start,
      dir,
      speed,
      age: 0,
      life,
      strength: 0.28 + Math.random() * 0.18,
      tail: 38 + Math.random() * 30
    });
  }

  function setupNetwork() {
    const w = window.innerWidth;
    const h = window.innerHeight;
    const detailCount = Math.max(12, Math.min(22, Math.round((w * h) / 78000)));
    const spreadX = w * 1.48;
    const spreadY = h * 1.44;
    const offsetX = (spreadX - w) / 2;
    const offsetY = (spreadY - h) / 2;
    const columns = Math.max(7, Math.ceil(spreadX / 210));
    const rows = Math.max(5, Math.ceil(spreadY / 185));
    const anchors = [
      [-0.16, 0.18],
      [0.16, -0.14],
      [0.62, -0.18],
      [1.13, 0.14],
      [1.18, 0.58],
      [1.04, 1.12],
      [0.45, 1.18],
      [-0.14, 0.84],
      [-0.2, 0.48]
    ];
    networkSignals = [];
    networkNodes = anchors.map(([x, y], index) => ({
      id: index,
      x: w * x,
      y: h * y,
      r: 1.8 + Math.random() * 2.2,
      phase: Math.random() * Math.PI * 2,
      drift: 0.18 + Math.random() * 0.34
    }));

    const coverageNodes = [];
    for (let row = 0; row < rows; row++) {
      for (let column = 0; column < columns; column++) {
        const cellX = columns <= 1 ? 0.5 : column / (columns - 1);
        const cellY = rows <= 1 ? 0.5 : row / (rows - 1);
        const jitterX = (Math.random() - 0.5) * (spreadX / columns) * 0.68;
        const jitterY = (Math.random() - 0.5) * (spreadY / rows) * 0.68;
        coverageNodes.push({
          id: anchors.length + coverageNodes.length,
          x: cellX * spreadX - offsetX + jitterX,
          y: cellY * spreadY - offsetY + jitterY,
          r: 1.15 + Math.random() * 2.1,
          phase: Math.random() * Math.PI * 2,
          drift: 0.18 + Math.random() * 0.34
        });
      }
    }

    const detailNodes = Array.from({ length: detailCount }, (_, index) => ({
      id: anchors.length + coverageNodes.length + index,
      x: Math.random() * spreadX - offsetX,
      y: Math.random() * spreadY - offsetY,
      r: 1.4 + Math.random() * 2.4,
      phase: Math.random() * Math.PI * 2,
      drift: 0.18 + Math.random() * 0.34
    }));
    networkNodes.push(...coverageNodes, ...detailNodes);

    const edgeMap = new Set();
    networkEdges = [];
    networkNodes.forEach(node => {
      const nearest = networkNodes
        .filter(other => other !== node)
        .map(other => {
          const dx = other.x - node.x;
          const dy = other.y - node.y;
          return { other, distance: Math.hypot(dx, dy) };
        })
        .sort((a, b) => a.distance - b.distance)
        .slice(0, 8);

      nearest.forEach(({ other, distance }) => {
        if (distance > Math.min(w, h) * 0.95) return;
        const key = [node.id, other.id].sort((a, b) => a - b).join(':');
        if (edgeMap.has(key)) return;
        edgeMap.add(key);
        networkEdges.push({ a: node, b: other, distance });
      });
    });
  }

  function addNetworkSignal() {
    if (!networkEdges.length) return;
    const edge = networkEdges[Math.floor(Math.random() * networkEdges.length)];
    networkSignals.push({
      edge,
      age: 0,
      life: 1500 + Math.random() * 1200,
      dir: Math.random() > 0.5 ? 1 : -1,
      strength: 0.28 + Math.random() * 0.22
    });
  }

  function drawNetwork(now) {
    const w = window.innerWidth;
    const h = window.innerHeight;
    const fade = brushCtx.createRadialGradient(w * 0.5, h * 0.52, 0, w * 0.5, h * 0.52, Math.max(w, h) * 0.88);
    fade.addColorStop(0, 'rgba(255,255,255,1)');
    fade.addColorStop(0.78, 'rgba(255,255,255,0.82)');
    fade.addColorStop(1, 'rgba(255,255,255,0)');

    brushCtx.save();
    brushCtx.globalCompositeOperation = 'source-over';
    brushCtx.lineWidth = 1;

    networkEdges.forEach(edge => {
      const alpha = Math.max(0.01, 0.038 - edge.distance / Math.max(w, h) * 0.026);
      brushCtx.strokeStyle = `rgba(69, 215, 255, ${alpha})`;
      brushCtx.beginPath();
      brushCtx.moveTo(edge.a.x, edge.a.y);
      brushCtx.lineTo(edge.b.x, edge.b.y);
      brushCtx.stroke();
    });

    networkNodes.forEach(node => {
      const pulse = 0.55 + Math.sin((now || 0) * 0.0012 + node.phase) * 0.25;
      brushCtx.fillStyle = `rgba(130, 232, 255, ${0.18 + pulse * 0.16})`;
      brushCtx.beginPath();
      brushCtx.arc(node.x, node.y, node.r, 0, Math.PI * 2);
      brushCtx.fill();

      brushCtx.fillStyle = `rgba(22, 217, 245, ${0.035 + pulse * 0.035})`;
      brushCtx.beginPath();
      brushCtx.arc(node.x, node.y, node.r * 3.4, 0, Math.PI * 2);
      brushCtx.fill();
    });

    if (brushFrame % 14 === 0 || networkSignals.length < 9) addNetworkSignal();

    brushCtx.globalCompositeOperation = 'lighter';
    networkSignals = networkSignals.filter(signal => {
      signal.age += 16;
      const p = signal.age / signal.life;
      if (p >= 1) return false;
      const t = signal.dir > 0 ? p : 1 - p;
      const x = signal.edge.a.x + (signal.edge.b.x - signal.edge.a.x) * t;
      const y = signal.edge.a.y + (signal.edge.b.y - signal.edge.a.y) * t;
      const alpha = signal.strength * Math.min(1, p * 5, (1 - p) * 5);

      brushCtx.strokeStyle = `rgba(73, 201, 235, ${alpha * 0.18})`;
      brushCtx.lineWidth = 1.2;
      brushCtx.beginPath();
      brushCtx.moveTo(signal.edge.a.x, signal.edge.a.y);
      brushCtx.lineTo(signal.edge.b.x, signal.edge.b.y);
      brushCtx.stroke();

      brushCtx.fillStyle = `rgba(164, 241, 255, ${alpha})`;
      brushCtx.beginPath();
      brushCtx.arc(x, y, 1.55, 0, Math.PI * 2);
      brushCtx.fill();

      brushCtx.fillStyle = `rgba(35, 214, 246, ${alpha * 0.08})`;
      brushCtx.beginPath();
      brushCtx.arc(x, y, 5.8, 0, Math.PI * 2);
      brushCtx.fill();

      return true;
    });

    brushCtx.globalCompositeOperation = 'destination-in';
    brushCtx.fillStyle = fade;
    brushCtx.fillRect(0, 0, w, h);
    brushCtx.restore();
  }

  function animateGridBrush() {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      if (backgroundMode === 'network') {
        drawNetwork(performance.now());
      } else {
        drawExactGrid();
      }
      return;
    }

    if (!brushCanvas.width) resizeBrushCanvas();
    brushCtx.clearRect(0, 0, window.innerWidth, window.innerHeight);
    if (backgroundMode === 'network') {
      drawNetwork(performance.now());
      brushFrame++;
      requestAnimationFrame(animateGridBrush);
      return;
    }

    drawExactGrid();
    if (brushFrame % 28 === 0 || gridSignals.length < 4) addGridSignal();
    brushFrame++;

    brushCtx.save();
    brushCtx.globalCompositeOperation = 'lighter';
    gridSignals = gridSignals.filter(signal => {
      signal.age += 16;
      signal.pos += signal.dir * signal.speed * 0.016;
      const p = signal.age / signal.life;
      if (p >= 1) return false;
      const edgeFade = Math.min(1, p * 5, (1 - p) * 5);
      const alpha = signal.strength * edgeFade;
      const x = signal.horizontal ? signal.pos : signal.line;
      const y = signal.horizontal ? signal.line : signal.pos;
      const tx = signal.horizontal ? signal.tail * -signal.dir : 0;
      const ty = signal.horizontal ? 0 : signal.tail * -signal.dir;

      brushCtx.strokeStyle = `rgba(73, 201, 235, ${alpha * 0.34})`;
      brushCtx.lineWidth = 1;
      brushCtx.beginPath();
      brushCtx.moveTo(x, y);
      brushCtx.lineTo(x + tx, y + ty);
      brushCtx.stroke();

      brushCtx.fillStyle = `rgba(164, 241, 255, ${alpha})`;
      brushCtx.beginPath();
      brushCtx.arc(x, y, 1.35, 0, Math.PI * 2);
      brushCtx.fill();

      brushCtx.fillStyle = `rgba(35, 214, 246, ${alpha * 0.1})`;
      brushCtx.beginPath();
      brushCtx.arc(x, y, 3.2, 0, Math.PI * 2);
      brushCtx.fill();

      return true;
    });
    brushCtx.restore();
    requestAnimationFrame(animateGridBrush);
  }

  function updateWheelArrow(sourceEvent) {
    if (sourceEvent && typeof sourceEvent.clientX === 'number') {
      lastWheelPointer = { x: sourceEvent.clientX, y: sourceEvent.clientY };
    }
    const panel = toolwheel.querySelector('.toolwheel-panel');
    const rect = panel.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const target = lastWheelPointer || { x: cx, y: cy - 180 };
    const angle = Math.atan2(target.y - cy, target.x - cx) * 180 / Math.PI;
    wheelArrow.style.setProperty('--angle', angle + 'deg');
  }

  function focusWheelNode(index) {
    const nodes = Array.from(toolwheel.querySelectorAll('.wheel-node'));
    nodes.forEach(node => node.classList.remove('focused'));
    const node = nodes[index];
    if (!node) return;
    focusedNode = node;
    node.classList.add('focused');
    readout.textContent = node.dataset.node + ' selected. Press Enter to confirm, or use numbers to jump.';
  }

  function clearWheelFocus() {
    toolwheel.querySelectorAll('.wheel-node').forEach(node => node.classList.remove('focused'));
    focusedNode = null;
    readout.textContent = 'Choose a section. Use hover, numbers, or arrow keys.';
  }

  function openToolwheel(sourceEvent) {
    stage.classList.add('toolwheel-active');
    setToolwheelDepthActive(true);
    toolwheel.classList.add('open');
    toolwheel.classList.add('suppress-core-menu');
    toolwheel.setAttribute('aria-hidden', 'false');
    wheelOpenPointer = sourceEvent && typeof sourceEvent.clientX === 'number'
      ? { x: sourceEvent.clientX, y: sourceEvent.clientY }
      : null;
    clearWheelFocus();
    requestAnimationFrame(() => updateWheelArrow(sourceEvent));
  }

  function closeToolwheel() {
    stage.classList.remove('toolwheel-active');
    setToolwheelDepthActive(false);
    toolwheel.classList.remove('open');
    toolwheel.classList.remove('core-new-open');
    toolwheel.classList.remove('suppress-core-menu');
    coreNewTree.classList.remove('is-open');
    wheelOpenPointer = null;
    clearWheelFocus();
    toolwheel.setAttribute('aria-hidden', 'true');
  }

  function setToolwheelDepthActive(active) {
    if (wheelOverlayMode !== 'depth') return;
    const layers = [
      {
        element: workspace,
        active: {
          filter: 'blur(1.4px) saturate(0.58) contrast(0.82) brightness(0.58)',
          opacity: '0.68',
          transform: 'perspective(1200px) translateY(10px) scale(0.965) rotateX(1.2deg)'
        },
        inactive: {
          filter: 'none',
          opacity: '',
          transform: 'none'
        }
      },
      {
        element: topline,
        active: {
          filter: 'blur(1.2px) saturate(0.62)',
          opacity: '0.34',
          transform: 'translateY(-3px) scale(0.985)'
        },
        inactive: {
          filter: 'none',
          opacity: '',
          transform: 'none'
        }
      },
      {
        element: brushCanvas,
        active: {
          filter: 'blur(2px) saturate(0.88) brightness(0.74)',
          opacity: '0.46'
        },
        inactive: {
          filter: 'none',
          opacity: ''
        }
      },
      {
        element: aurora,
        active: {
          filter: 'blur(4px) saturate(0.92)',
          opacity: '0.48'
        },
        inactive: {
          filter: 'none',
          opacity: ''
        }
      }
    ];

    layers.forEach(layer => {
      if (!layer.element) return;
      const styles = active ? layer.active : layer.inactive;
      layer.element.style.transition = active ? '' : 'none';
      Object.keys(styles).forEach(property => {
        layer.element.style[property] = styles[property];
      });
      if (!active) {
        void layer.element.offsetHeight;
        requestAnimationFrame(() => {
          layer.element.style.transition = '';
        });
      }
    });
  }

  function announceAction(action) {
    readout.textContent = action + ' prepared.';
    buildState.textContent = 'V2 - ' + action;
  }

  function serializeContextNodges() {
    return Array.from(contextNodges.querySelectorAll('.context-nodge:not([data-context-suggestion="true"])')).map(nodge => ({
      kind: nodge.dataset.contextKind || 'Context',
      label: nodge.dataset.contextLabel || nodge.textContent.trim(),
      path: nodge.dataset.contextPath || '',
      summary: nodge.dataset.contextSummary || '',
      pinned: nodge.dataset.contextPinned === 'true'
    }));
  }

  function contextKindMeta(kind) {
    const normalized = String(kind || 'Context').toLowerCase();
    const map = {
      file: { icon: '[]', hint: 'File attached to this prompt' },
      mount: { icon: '#', hint: 'Folder mount available to this chat' },
      source: { icon: '@', hint: 'Knowledge source added as context' },
      project: { icon: '<>', hint: 'Project context is active' },
      chat: { icon: '//', hint: 'Chat context is linked' },
      memory: { icon: '*', hint: 'Memory context is active' },
      document: { icon: 'pin', hint: 'Document summary is pinned to this chat' },
      selection: { icon: '+', hint: 'Selection added as context' }
    };
    return {
      key: map[normalized] ? normalized : 'context',
      icon: map[normalized]?.icon || '+',
      hint: map[normalized]?.hint || 'Context attached to this chat'
    };
  }

  function renderContextNodges(contexts) {
    contextNodges.innerHTML = '';
    (contexts || []).forEach(context => {
      const meta = contextKindMeta(context.kind);
      const nodge = document.createElement('span');
      nodge.className = 'context-nodge';
      nodge.dataset.contextKind = context.kind;
      nodge.dataset.contextType = meta.key;
      nodge.dataset.contextLabel = context.label;
      nodge.dataset.contextPinned = context.pinned === false ? 'false' : 'true';
      if (context.path) nodge.dataset.contextPath = context.path;
      if (context.summary) nodge.dataset.contextSummary = context.summary;
      nodge.title = context.kind + ': ' + context.label + ' - ' + (context.summary || meta.hint);

      const icon = document.createElement('span');
      icon.className = 'context-nodge-icon';
      icon.textContent = meta.icon;

      const kind = document.createElement('span');
      kind.className = 'context-nodge-kind';
      kind.textContent = context.kind;

      const label = document.createElement('span');
      label.className = 'context-nodge-label';
      label.textContent = context.label;

      const remove = document.createElement('button');
      remove.className = 'context-nodge-remove';
      remove.type = 'button';
      remove.setAttribute('aria-label', 'Remove ' + context.kind + ' context');
      remove.textContent = 'x';

      const tooltip = document.createElement('span');
      tooltip.className = 'context-nodge-tooltip';
      tooltip.innerHTML = context.summary
        ? '<strong>' + esc(context.label) + '</strong><span>' + esc(context.summary) + '</span>'
        : context.kind + ': ' + meta.hint;

      nodge.append(icon, label, remove, tooltip);
      contextNodges.appendChild(nodge);
    });
    renderOpenDocumentContextSuggestions();
    contextNodges.hidden = !contextNodges.children.length;
  }

  function addContextNodge(kind, label, details = {}) {
    const contexts = serializeContextNodges();
    const exists = contexts.some(context => context.kind === kind && context.label === label && (details.path ? context.path === details.path : true));
    if (!exists) contexts.push({ kind, label, ...details, pinned: true });
    renderContextNodges(contexts);
    const current = chatSpaces[activeChatIndex];
    if (current) current.contexts = contexts;
  }

  function openDocumentContexts() {
    return Array.from(document.querySelectorAll('.document-viewer-window')).map(win => ({
      label: win.dataset.documentTitle || win.querySelector('[data-document-title]')?.textContent?.trim() || basename(win.dataset.documentPath),
      path: win.dataset.documentPath || '',
      summary: win.dataset.documentSummary || 'Imported document summary will appear here after Universal Inbox analysis.',
      type: win.dataset.documentType || 'text'
    })).filter(context => context.path);
  }

  function renderOpenDocumentContextSuggestions() {
    if (!contextNodges) return;
    contextNodges.querySelectorAll('[data-context-suggestion="true"]').forEach(item => item.remove());
    const tray = documentContextTray();
    tray.querySelectorAll('[data-context-suggestion="true"]').forEach(item => item.remove());
    const pinnedPaths = new Set(serializeContextNodges()
      .filter(context => context.kind === 'Document')
      .map(context => context.path));
    openDocumentContexts().forEach(context => {
      if (pinnedPaths.has(context.path)) return;
      contextNodges.appendChild(documentSuggestionNodge(context));
      tray.appendChild(documentSuggestionNodge(context));
    });
    contextNodges.hidden = !contextNodges.children.length;
    tray.hidden = !tray.children.length;
  }

  function documentContextTray() {
    let tray = document.getElementById('document-context-tray');
    if (!tray) {
      tray = document.createElement('div');
      tray.id = 'document-context-tray';
      tray.className = 'document-context-tray';
      tray.setAttribute('aria-label', 'Open document context suggestions');
      stage.appendChild(tray);
    }
    return tray;
  }

  function documentSuggestionNodge(context) {
    const nodge = document.createElement('button');
    nodge.type = 'button';
    nodge.className = 'context-nodge context-nodge-suggestion';
    nodge.dataset.contextSuggestion = 'true';
    nodge.dataset.contextKind = 'Document';
    nodge.dataset.contextType = 'document';
    nodge.dataset.contextLabel = context.label;
    nodge.dataset.contextPath = context.path;
    nodge.dataset.contextSummary = context.summary;
    nodge.setAttribute('aria-label', 'Pin document context ' + context.label);
    nodge.title = 'Pin document context: ' + context.summary;
    nodge.innerHTML = [
      '<span class="context-nodge-icon">+</span>',
      '<span class="context-nodge-label">' + esc(context.label) + '</span>',
      '<span class="context-nodge-tooltip"><strong>' + esc(context.label) + '</strong><span>' + esc(context.summary) + '</span></span>'
    ].join('');
    return nodge;
  }

  function pinDocumentContextFromSuggestion(nodge) {
    addContextNodge('Document', nodge.dataset.contextLabel || 'Document', {
      path: nodge.dataset.contextPath || '',
      summary: nodge.dataset.contextSummary || '',
      pinned: true
    });
    buildState.textContent = 'V2 - Document context pinned';
  }

  function currentShortTime(offsetMinutes = 0) {
    const date = new Date(Date.now() + offsetMinutes * 60000);
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  function aiMetaRows(message, index) {
    const defaults = [
      { label: 'Time', value: message.dataset.aiTime || currentShortTime(index * 2) },
      { label: 'Model', value: message.dataset.aiModel || 'deepseek-v4-flash' },
      { label: 'Tokens', value: message.dataset.aiTokens || (260 + index * 84) + ' in / ' + (42 + index * 18) + ' out' },
      { label: 'Context', value: message.dataset.aiContext || (18 + index * 3) + '% used' },
      { label: 'Latency', value: message.dataset.aiLatency || (message.classList.contains('working') ? 'running' : (1.1 + index * 0.4).toFixed(1) + 's') }
    ];
    return defaults;
  }

  function ensureAiMetaHotspots() {
    messages.querySelectorAll('.message.ai').forEach((message, index) => {
      if (message.querySelector('.ai-meta-hotspot')) return;
      const rows = aiMetaRows(message, index);
      const summary = rows.map(row => row.label + ': ' + row.value).join(', ');
      const hotspot = document.createElement('span');
      hotspot.className = 'ai-meta-hotspot';
      hotspot.tabIndex = 0;
      hotspot.setAttribute('role', 'button');
      hotspot.setAttribute('aria-label', 'AI response metadata. ' + summary);

      const tooltip = document.createElement('span');
      tooltip.className = 'ai-meta-tooltip';
      tooltip.setAttribute('role', 'tooltip');
      rows.forEach(row => {
        const item = document.createElement('span');
        item.className = 'ai-meta-tooltip-row';
        const label = document.createElement('span');
        label.className = 'ai-meta-tooltip-label';
        label.textContent = row.label;
        const value = document.createElement('span');
        value.className = 'ai-meta-tooltip-value';
        value.textContent = row.value;
        item.append(label, value);
        tooltip.appendChild(item);
      });

      message.prepend(tooltip);
      message.prepend(hotspot);
    });
    syncWorkRuns();
  }

  function workRunSteps(mode) {
    if (mode === 'plan') {
      return [
        { type: 'Read', title: 'Goal clarified', detail: 'Separated the desired outcome from implementation details.' },
        { type: 'Checked', title: 'Relevant context found', detail: 'Looked at matching project state and prior UI decisions.' },
        { type: 'Decided', title: 'Small next step chosen', detail: 'Prepared a plan without changing files yet.' }
      ];
    }
    return [
      { type: 'Read', title: 'Request understood', detail: 'Mapped the prompt to the current chat and workspace state.' },
      { type: 'Checked', title: 'Context inspected', detail: 'Checked the useful UI structure before answering.' },
      { type: 'Prepared', title: 'Safe next action ready', detail: 'Chose the first reversible implementation path.' }
    ];
  }

  function workRunMarkup(status, steps) {
    const isRunning = status === 'running';
    const isAttention = status === 'attention';
    const collapsed = !isRunning;
    const count = steps.length;
    const summary = isRunning
      ? 'ABC is doing ' + count + ' background steps'
      : 'ABC did ' + count + ' background steps';
    const hint = collapsed ? 'Show work log' : 'Hide work log';
    return [
      '<section class="work-run ' + esc(status) + (collapsed ? ' collapsed' : '') + '" data-work-run>',
      '  <button class="work-run-summary" type="button" data-work-run-toggle aria-expanded="' + String(!collapsed) + '">',
      '    <span class="work-run-dot" aria-hidden="true"></span>',
      '    <span class="work-run-copy"><strong>' + esc(summary) + '</strong><span>' + esc(hint) + '</span></span>',
      '  </button>',
      '  <div class="work-run-details">',
      steps.map((step, index) => (
        '    <div class="work-step" data-work-step-type="' + esc(step.type.toLowerCase()) + '">' +
        '<span class="work-step-type">' + esc(step.type) + '</span>' +
        '<strong>' + esc(step.title) + '</strong>' +
        '<small>' + esc(step.detail || 'Finished') + '</small>' +
        '<span class="work-step-index">' + esc(String(index + 1).padStart(2, '0')) + '</span>' +
        '</div>'
      )).join(''),
      '  </div>',
      '</section>'
    ].join('');
  }

  function syncWorkRuns() {
    messages.querySelectorAll('[data-work-run]').forEach(run => {
      const toggle = run.querySelector('[data-work-run-toggle]');
      const copyHint = run.querySelector('.work-run-copy span');
      const isExpanded = !run.classList.contains('collapsed');
      toggle?.setAttribute('aria-expanded', String(isExpanded));
      if (copyHint) copyHint.textContent = isExpanded ? 'Hide work log' : 'Show work log';
    });
  }

  function documentModeForPath(path) {
    const extension = String(path || '').split('.').pop().toLowerCase();
    if (['py', 'js', 'jsx', 'ts', 'tsx', 'html', 'css', 'json', 'yaml', 'yml', 'toml', 'rs', 'go', 'java', 'cs', 'cpp', 'c', 'h', 'sql', 'sh', 'ps1'].includes(extension)) return 'code';
    if (['pdf', 'doc', 'docx'].includes(extension)) return 'pdf';
    return 'text';
  }

  function languageForPath(path) {
    const extension = String(path || '').split('.').pop().toLowerCase();
    const map = {
      py: 'python',
      js: 'javascript',
      jsx: 'javascript',
      ts: 'javascript',
      tsx: 'javascript',
      html: 'html',
      css: 'css',
      json: 'json',
      md: 'markdown',
      txt: 'text'
    };
    return map[extension] || 'text';
  }

  function basename(path) {
    return String(path || 'Untitled').split(/[\\/]/).pop() || 'Untitled';
  }

  function normalizeDocument(pathOrDocument) {
    if (typeof pathOrDocument === 'object' && pathOrDocument) {
      const path = pathOrDocument.path || pathOrDocument.title || 'Untitled';
      return {
        title: pathOrDocument.title || basename(path),
        path,
        type: pathOrDocument.type || documentModeForPath(path),
        language: pathOrDocument.language || languageForPath(path),
        page: pathOrDocument.page || '1 / 1',
        zoom: pathOrDocument.zoom || '100%',
        summary: pathOrDocument.summary || '',
        content: pathOrDocument.content || ''
      };
    }

    const path = String(pathOrDocument || '').trim();
    const sample = documentSamples[path] || documentSamples[basename(path)];
    if (sample) return normalizeDocument(sample);
    return {
      title: basename(path),
      path,
      type: documentModeForPath(path),
      language: languageForPath(path),
      page: '1 / 1',
      zoom: '100%',
      summary: 'Imported document summary will appear here after Universal Inbox analysis.',
      content: sampleDocumentContent(path)
    };
  }

  function sampleDocumentContent(path) {
    const type = documentModeForPath(path);
    if (type === 'code') {
      return codeLanguageSamples[languageForPath(path)]?.content || '// File preview will load here.';
    }
    if (type === 'pdf') {
      return 'Document Preview\n\nThis PDF or office document opens with page and zoom tools only.';
    }
    return '# Document Preview\n\nThe selected text document opens here as a quiet writing surface.';
  }

  function renderDocumentHeaderControl(doc) {
    if (doc.type !== 'code') return '';
    const languages = ['python', 'javascript', 'html', 'json'];
    const selected = languages.includes(doc.language) ? doc.language : 'python';
    return [
      '<select class="document-language-select" data-doc-language aria-label="Programming language preset">',
      languages.map(language => '<option value="' + esc(language) + '"' + (language === selected ? ' selected' : '') + '>' + esc(languageLabel(language)) + '</option>').join(''),
      '</select>'
    ].join('');
  }

  function languageLabel(language) {
    const labels = {
      python: 'Python',
      javascript: 'JavaScript',
      html: 'HTML',
      json: 'JSON'
    };
    return labels[language] || language;
  }

  function renderDocumentBody(doc) {
    if (doc.type === 'code') return renderCodeDocument(doc);
    if (doc.type === 'pdf') return renderPdfDocument(doc);
    return renderTextDocument(doc);
  }

  function renderTextDocument(doc) {
    const lines = String(doc.content || '').split('\n');
    const body = lines.map(line => {
      if (line.startsWith('# ')) return '<h1>' + esc(line.slice(2)) + '</h1>';
      if (line.startsWith('## ')) return '<h2>' + esc(line.slice(3)) + '</h2>';
      if (line.startsWith('- ')) return '<li>' + esc(line.slice(2)) + '</li>';
      if (!line.trim()) return '';
      return '<p>' + esc(line) + '</p>';
    }).join('');

    return [
      '<section class="document-mode document-text-mode" aria-label="Text document">',
      '  <div class="document-format-bar" aria-label="Text formatting">',
      '    <button class="document-format-button active" type="button" title="Bold">B</button>',
      '    <button class="document-format-button" type="button" title="Italic">I</button>',
      '    <button class="document-format-button" type="button" title="Heading">H</button>',
      '    <button class="document-format-button" type="button" title="List">List</button>',
      '    <button class="document-format-button" type="button" title="Code">`</button>',
      '  </div>',
      '  <article class="document-text-surface">' + body + '</article>',
      '</section>'
    ].join('');
  }

  function renderCodeDocument(doc) {
    const code = String(doc.content || '');
    const lines = code.split('\n').map((_, index) => index + 1).join('\n');
    return [
      '<section class="document-mode document-code-mode" aria-label="Code document">',
      '  <div class="document-code-subhead">',
      '    <span>' + esc(doc.path) + '</span>',
      '  </div>',
      '  <section class="document-code-editor">',
      '    <pre class="document-line-numbers">' + esc(lines) + '</pre>',
      '    <pre class="document-code-content" data-document-code>' + highlightCode(code, doc.language) + '</pre>',
      '  </section>',
      '</section>'
    ].join('');
  }

  function renderPdfDocument(doc) {
    const paragraphs = String(doc.content || '').split('\n\n').filter(Boolean);
    const title = paragraphs.shift() || doc.title;
    return [
      '<section class="document-mode document-pdf-mode" aria-label="PDF or office document">',
      '  <div class="document-pdf-tools" aria-label="PDF tools">',
      '    <div class="document-page-state">Page ' + esc(doc.page || '1 / 1') + '</div>',
      '    <div class="document-zoom-tools">',
      '      <button type="button" title="Zoom out">-</button>',
      '      <button type="button" title="Fit page">Fit</button>',
      '      <button type="button" title="Zoom in">+</button>',
      '    </div>',
      '    <div class="document-zoom-label">' + esc(doc.zoom || '100%') + '</div>',
      '  </div>',
      '  <section class="document-pdf-canvas">',
      '    <article class="document-pdf-page">',
      '      <h1>' + esc(title) + '</h1>',
      paragraphs.map((paragraph, index) => (index === 1 ? '<div class="document-pdf-rule"></div>' : '') + '<p>' + esc(paragraph) + '</p>').join(''),
      '    </article>',
      '  </section>',
      '</section>'
    ].join('');
  }

  function highlightCode(source, language) {
    const escaped = esc(source);
    if (language === 'html') {
      return escaped
        .replace(/(&lt;\/?)([a-z0-9-]+)/gi, '$1<span class="token-tag">$2</span>')
        .replace(/([a-z-]+)=(&quot;[^&]+&quot;)/gi, '<span class="token-attr">$1</span>=<span class="token-str">$2</span>');
    }
    if (language === 'json') {
      return escaped
        .replace(/(&quot;[^&]+&quot;)(?=:)/g, '<span class="token-key">$1</span>')
        .replace(/: (&quot;[^&]+&quot;)/g, ': <span class="token-str">$1</span>')
        .replace(/\b(\d+)\b/g, '<span class="token-num">$1</span>');
    }
    if (language === 'javascript') {
      return escaped
        .replace(/\b(const|let|function|return|if|else|await|async)\b/g, '<span class="token-key">$1</span>')
        .replace(/(&quot;[^&]+&quot;|'[^']*')/g, '<span class="token-str">$1</span>')
        .replace(/\b(\d+)\b/g, '<span class="token-num">$1</span>');
    }
    return escaped
      .replace(/(#.*)$/gm, '<span class="token-comment">$1</span>')
      .replace(/\b(def|return|if|else|for|in|class|import|from)\b/g, '<span class="token-key">$1</span>')
      .replace(/\b(\d+)\b/g, '<span class="token-num">$1</span>');
  }

  function updateDocumentViewer(win, input) {
    const doc = normalizeDocument(input);
    win.dataset.documentPath = doc.path;
    win.dataset.documentType = doc.type;
    win.dataset.documentTitle = doc.title;
    win.dataset.documentSummary = doc.summary || 'Imported document summary will appear here after Universal Inbox analysis.';
    const title = win.querySelector('[data-document-title]');
    const left = win.querySelector('[data-document-header-left]');
    const body = win.querySelector('.window-body');
    if (title) title.textContent = doc.title;
    if (left) left.innerHTML = renderDocumentHeaderControl(doc);
    if (body) body.innerHTML = renderDocumentBody(doc);
    wireDocumentViewer(win);
    renderOpenDocumentContextSuggestions();
  }

  function openDocumentViewer(input) {
    const doc = normalizeDocument(input);
    let win = Array.from(document.querySelectorAll('.document-viewer-window'))
      .find(item => item.dataset.documentPath === doc.path);
    if (!win) {
      const existingCount = document.querySelectorAll('.document-viewer-window').length;
      win = document.createElement('article');
      win.className = 'floating-window document-viewer-window active';
      win.id = 'document-viewer-window-' + (existingCount + 1);
      win.dataset.window = '';
      win.dataset.windowId = 'document-viewer-' + (existingCount + 1);
      win.dataset.windowCloseMode = 'remove';
      win.setAttribute('aria-label', 'Document Viewer');
      win.innerHTML = [
        '<header class="window-head document-window-head" data-drag-handle>',
        '  <div class="document-header-left" data-document-header-left></div>',
        '  <div class="window-title document-window-title" data-document-title></div>',
        '  <div class="window-actions" aria-label="Window controls">',
        '    <button class="window-control" data-window-min title="Minimize" aria-label="Minimize">-</button>',
        '    <button class="window-control" data-window-max title="Maximize" aria-label="Maximize">&#9633;</button>',
        '    <button class="window-control" data-window-close title="Close" aria-label="Close">x</button>',
        '  </div>',
        '</header>',
        '<div class="window-body"></div>'
      ].join('');
      workspace.appendChild(win);
      offsetDocumentWindow(win, existingCount);
      prepareFloatingWindow(win);
    } else {
      setWindowMinimized(win, false);
    }
    updateDocumentViewer(win, doc);
    activateWindow(win);
    buildState.textContent = 'V2 - Opened ' + doc.title;
    return win;
  }

  function offsetDocumentWindow(win, index) {
    if (!index) return;
    const width = Math.min(1080, Math.max(620, window.innerWidth - 110));
    const height = Math.min(780, Math.max(420, window.innerHeight - 96));
    const margin = 24;
    const left = Math.min(window.innerWidth - width - margin, Math.max(margin, ((window.innerWidth - width) / 2) + (index * 30)));
    const top = Math.min(window.innerHeight - height - margin, Math.max(margin, ((window.innerHeight - height) / 2) + (index * 24)));
    win.style.left = left + 'px';
    win.style.top = top + 'px';
    win.style.transform = 'none';
  }

  function wireDocumentViewer(win) {
    const select = win.querySelector('[data-doc-language]');
    if (!select || select.dataset.documentPrepared) return;
    select.dataset.documentPrepared = 'true';
    select.addEventListener('change', event => {
      const sample = codeLanguageSamples[event.target.value] || codeLanguageSamples.python;
      updateDocumentViewer(win, {
        title: sample.title,
        path: sample.path,
        type: 'code',
        language: event.target.value,
        content: sample.content
      });
    });
  }

  function documentPathFromTarget(target) {
    const link = target.closest('[data-document-link]');
    if (!link) return null;
    return link.dataset.documentLink || link.textContent.trim();
  }

  function isDocumentLinkableText(text) {
    return /\.(md|txt|pdf|doc|docx|js|jsx|ts|tsx|py|html|css|json|yaml|yml|toml|rs|go|java|cs|cpp|c|h|sql|sh|ps1)\b/i.test(text);
  }

  function linkifyDocumentReferences(root) {
    const scope = root || messages;
    const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || !node.nodeValue || !isDocumentLinkableText(node.nodeValue)) return NodeFilter.FILTER_REJECT;
        if (parent.closest('button, a, select, textarea, input, [data-document-link]')) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    const pattern = /((?:[A-Za-z0-9_.-]+\/)+(?:[A-Za-z0-9_.-]+)\.(?:md|txt|pdf|docx?|js|jsx|ts|tsx|py|html|css|json|yaml|yml|toml|rs|go|java|cs|cpp|c|h|sql|sh|ps1)|[A-Za-z0-9_.-]+\.(?:md|txt|pdf|docx?|js|jsx|ts|tsx|py|html|css|json|yaml|yml|toml|rs|go|java|cs|cpp|c|h|sql|sh|ps1))/gi;
    nodes.forEach(node => {
      const text = node.nodeValue;
      const fragment = document.createDocumentFragment();
      let last = 0;
      text.replace(pattern, (match, _unused, offset) => {
        fragment.append(document.createTextNode(text.slice(last, offset)));
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'document-inline-link';
        button.dataset.documentLink = match;
        button.textContent = match;
        fragment.append(button);
        last = offset + match.length;
        return match;
      });
      fragment.append(document.createTextNode(text.slice(last)));
      node.replaceWith(fragment);
    });
  }

  function chatIsNew(space) {
    if (!space) return false;
    return Boolean(space.isNew) || !String(space.messages || '').trim();
  }

  function modelStateClass(state) {
    if (state === 'busy') return 'busy';
    if (state === 'offline') return 'offline';
    return 'ready';
  }

  function setModelDotClass(dot, state) {
    if (!dot) return;
    dot.classList.remove('ready', 'busy', 'offline');
    dot.classList.add(modelStateClass(state));
  }

  function updateModelUi() {
    const current = chatSpaces[activeChatIndex];
    const isNew = chatIsNew(current);
    const model = current?.model || lastSelectedModel;
    const profile = modelProfiles[model] || modelProfiles[lastSelectedModel] || modelProfiles['deepseek-v4-flash'];
    const state = current?.modelState || profile.state || 'ready';
    if (currentModelLabel) currentModelLabel.textContent = model;
    if (composerModelLabel) composerModelLabel.textContent = model;
    setModelDotClass(headerModelChip?.querySelector('.model-status-dot'), state);
    setModelDotClass(composerModelButton?.querySelector('.model-status-dot'), state);
    const tooltip = headerModelChip?.querySelector('.chat-model-tooltip');
    if (tooltip) {
      tooltip.innerHTML = [
        ['Route', profile.route],
        ['Context', profile.context],
        ['Tokens', profile.tokens],
        ['Load', state === 'offline' ? 'problem' : profile.load],
        ['Note', profile.note]
      ].map(([label, value]) => `<span class="model-tooltip-row"><span>${esc(label)}</span><span>${esc(value)}</span></span>`).join('');
    }
    if (headerModelChip) headerModelChip.hidden = isNew;
    if (composerModelChooser) {
      composerModelChooser.hidden = !isNew;
      if (!isNew) composerModelChooser.classList.remove('open');
      composerModelButton?.setAttribute('aria-expanded', String(composerModelChooser.classList.contains('open')));
    }
    composerModelMenu?.querySelectorAll('[data-model-name]').forEach(row => {
      row.classList.toggle('selected', row.dataset.modelName === model);
    });
  }

  function renderChatHistory() {
    if (!chatHistoryList) return;
    const query = (chatHistorySearch?.value || '').trim().toLowerCase();
    const activeRows = chatSpaces.map((space, index) => ({
      title: space.title || 'New Chat',
      subtitle: chatIsNew(space) ? 'New chat' : (space.needsQuestion ? 'Needs your decision' : (space.unread ? 'Unread answer' : 'Active chat')),
      age: index === activeChatIndex ? 'now' : (space.lastAnswer || 'open'),
      state: space.needsQuestion ? 'question' : (chatIsWorking(space) ? 'working' : (space.unread ? 'unread' : '')),
      active: index === activeChatIndex,
      index
    }));
    const rows = [...activeRows, ...historicalChats.map(item => ({ ...item, index: -1 }))].filter(item => {
      if (!query) return true;
      return [item.title, item.subtitle, item.age].join(' ').toLowerCase().includes(query);
    });
    if (chatHistoryCount) chatHistoryCount.textContent = (chatSpaces.length + historicalChats.length) + ' chats';
    chatHistoryList.innerHTML = rows.map(item => `
      <button class="chat-history-item${item.active ? ' active' : ''}${item.state ? ' ' + esc(item.state) : ''}" type="button" data-chat-history-index="${item.index}" aria-label="${esc(item.title)} ${esc(item.subtitle)} ${esc(item.age)}">
        <span class="chat-history-text"><strong>${esc(item.title)}</strong><span>${esc(item.subtitle)}</span></span>
        <span class="chat-history-age"><span class="chat-history-state">${esc(item.state || 'open')}</span>${esc(item.age)}</span>
      </button>
    `).join('');
  }

  function setChatHistoryOpen(open) {
    if (!chatHistoryPanel) return;
    chatHistoryPanel.hidden = !open;
    coreWindow.classList.toggle('history-open', open);
    chatHistoryButton?.setAttribute('aria-expanded', String(open));
    if (open) renderChatHistory();
  }

  function startTitleRename() {
    if (!chatTitleEditor) return;
    chatTitleEditor.value = chatTitle.textContent.trim();
    chatTitle.hidden = true;
    chatTitleEditor.hidden = false;
    chatTitleEditor.focus();
    chatTitleEditor.select();
  }

  function commitTitleRename() {
    if (!chatTitleEditor || chatTitleEditor.hidden) return;
    const nextTitle = chatTitleEditor.value.trim() || 'Untitled chat';
    chatTitle.textContent = nextTitle;
    chatTitle.hidden = false;
    chatTitleEditor.hidden = true;
    const current = chatSpaces[activeChatIndex];
    if (current) current.title = nextTitle;
    updateChatCarousel();
    renderChatHistory();
  }

  function cancelTitleRename() {
    if (!chatTitleEditor) return;
    chatTitle.hidden = false;
    chatTitleEditor.hidden = true;
  }

  function selectChatModel(row) {
    const current = chatSpaces[activeChatIndex];
    if (!current || !row) return;
    current.model = row.dataset.modelName || lastSelectedModel;
    current.modelState = row.dataset.modelState || 'ready';
    lastSelectedModel = current.model;
    composerModelChooser?.classList.remove('open');
    composerModelButton?.setAttribute('aria-expanded', 'false');
    updateModelUi();
  }

  function demoAssistantMessage(promptText, mode, model, state) {
    const isPlan = mode === 'plan';
    const modelNote = state === 'busy'
      ? 'Local model is busy, so this would run with a smaller budget.'
      : (state === 'offline' ? 'Selected model has a problem; fallback would be requested.' : 'Model route is ready.');
    const body = isPlan
      ? [
        '<strong>Plan</strong>',
        '<span>1. Clarify the goal from your prompt.</span>',
        '<span>2. Pick the smallest safe next step.</span>',
        '<span>3. Ask before execution if the step changes files or state. Draft: docs/plans/memory-budget.md</span>',
        '<span class="busy-signal">Plan prepared <span class="pixel-wave" data-wave>&#9601;&#9602;&#9603;</span></span>'
      ].join('<br>')
      : [
        '<span>I would start with a focused pass on: ' + esc(promptText) + '</span>',
        '<span>' + esc(modelNote) + '</span>',
        '<span>Likely file to inspect: src/services/search_memory.py</span>',
        '<span class="busy-signal">Running first safe step <span class="pixel-wave" data-wave>&#9601;&#9602;&#9603;</span></span>'
      ].join('<br>');
    return '<div class="message ai" data-ai-time="' + esc(currentShortTime()) + '" data-ai-model="' + esc(model) + '" data-ai-tokens="demo" data-ai-context="' + (isPlan ? 'plan draft' : 'agent demo') + '" data-ai-latency="0.8s">' + body + '</div>';
  }

  function sendPromptDemo() {
    const current = chatSpaces[activeChatIndex];
    const text = promptInput.value.trim();
    if (!current || !text) return false;
    const mode = coreWindow.dataset.mode || 'agent';
    const model = current.model || lastSelectedModel;
    const state = current.modelState || modelProfiles[model]?.state || 'ready';
    const userMessage = '<div class="message user">' + esc(text) + '</div>';
    const separator = String(messages.innerHTML || '').trim() ? '' : '';
    const completedRun = workRunMarkup('complete', workRunSteps(mode));
    messages.innerHTML = String(messages.innerHTML || '').trim() + separator + userMessage + completedRun + demoAssistantMessage(text, mode, model, state);
    linkifyDocumentReferences(messages);
    promptInput.value = '';
    current.isNew = false;
    current.unread = false;
    current.needsQuestion = mode === 'plan';
    current.title = current.title === 'New Chat' ? text.slice(0, 36) : current.title;
    current.messages = messages.innerHTML;
    current.prompt = '';
    current.lastAnswer = 'now';
    autosizePrompt(promptInput);
    renderChatSpace(activeChatIndex);
    requestAnimationFrame(() => {
      messages.scrollTop = messages.scrollHeight;
    });
    return true;
  }

  function saveActiveChatSpace() {
    const current = chatSpaces[activeChatIndex];
    if (!current) return;
    ensureAiMetaHotspots();
    current.title = chatTitle.textContent.trim();
    current.messages = messages.innerHTML;
    current.prompt = promptInput.value;
    current.model = current.model || lastSelectedModel;
    current.contexts = serializeContextNodges();
  }

  function updateChatNodges() {
    const hasMultipleChats = chatSpaces.length > 1;
    const hasPrevious = hasMultipleChats && activeChatIndex > 0;
    const hasNext = hasMultipleChats && activeChatIndex < chatSpaces.length - 1;
    leftNodge.hidden = !hasPrevious;
    rightNodge.hidden = !hasNext;
    leftNodge.setAttribute('aria-label', hasPrevious ? 'Previous chat: ' + chatSpaces[activeChatIndex - 1].title : 'Previous chat');
    rightNodge.setAttribute('aria-label', hasNext ? 'Next chat: ' + chatSpaces[activeChatIndex + 1].title : 'Next chat');
  }

  function chatIsWorking(space) {
    return /\bworking\b/.test(space.messages);
  }

  function updateChatCarousel() {
    chatCarousel.hidden = chatSpaces.length <= 1;
    chatCarousel.innerHTML = '';
    if (chatCarousel.hidden) return;

    chatSpaces.forEach((space, index) => {
      const relative = index - activeChatIndex;
      const distance = Math.min(Math.abs(relative), 4);
      const tile = document.createElement('button');
      tile.type = 'button';
      tile.className = 'carousel-tile' + (index === activeChatIndex ? ' active' : '');
      tile.dataset.chatIndex = String(index);
      tile.style.left = 'calc(50% + ' + (relative * 24) + 'px)';
      tile.style.setProperty('--tile-x', relative * 24 + 'px');
      tile.style.setProperty('--tile-z', -distance * 18 + 'px');
      tile.style.setProperty('--tile-rotate', relative * -18 + 'deg');
      tile.style.setProperty('--tile-scale', String(Math.max(0.62, 1 - distance * 0.12)));
      tile.style.setProperty('--tile-opacity', String(Math.max(0.22, 1 - distance * 0.18)));
      tile.setAttribute('aria-label', 'Open ' + space.title);
      tile.innerHTML = [
        '<span class="carousel-card">',
        '<span class="carousel-number">' + (index + 1) + '</span>',
        '<span class="carousel-glyph" aria-hidden="true">' + (index === 0 ? '&#9678;' : '&#9635;') + '</span>',
        chatIsWorking(space) ? '<span class="carousel-work" aria-hidden="true"><span></span><span></span><span></span></span>' : '',
        '</span>'
      ].join('');
      tile.addEventListener('click', event => {
        event.preventDefault();
        openChatSpace(index);
      });
      chatCarousel.appendChild(tile);
    });
  }

  function renderChatSpace(index) {
    const next = chatSpaces[index];
    if (!next) return;
    next.unread = false;
    activeChatIndex = index;
    chatTitle.textContent = next.title;
    chatTitle.hidden = false;
    if (chatTitleEditor) chatTitleEditor.hidden = true;
    messages.innerHTML = next.messages;
    linkifyDocumentReferences(messages);
    ensureAiMetaHotspots();
    promptInput.value = next.prompt;
    renderContextNodges(next.contexts);
    autosizePrompt(promptInput);
    updateModelUi();
    updateChatNodges();
    updateChatCarousel();
    renderChatHistory();
    buildState.textContent = 'V2 - Chat ' + (activeChatIndex + 1) + ' / ' + chatSpaces.length;
  }

  function createChatSpace() {
    saveActiveChatSpace();
    const number = chatSpaces.length + 1;
    chatSpaces.push({
      id: 'chat-' + number,
      title: 'New Chat',
      messages: '',
      prompt: '',
      model: lastSelectedModel,
      modelState: 'ready',
      isNew: true,
      lastAnswer: 'new',
      contexts: []
    });
    setWindowMinimized(coreWindow, false);
    renderChatSpace(chatSpaces.length - 1);
    closeToolwheel();
  }

  function moveChatSpace(direction) {
    saveActiveChatSpace();
    const nextIndex = activeChatIndex + direction;
    if (nextIndex < 0 || nextIndex >= chatSpaces.length) return;
    renderChatSpace(nextIndex);
  }

  function openChatSpace(index) {
    if (index === activeChatIndex) return;
    saveActiveChatSpace();
    renderChatSpace(index);
  }

  function activateWindow(win) {
    document.querySelectorAll('[data-window]').forEach(item => item.classList.remove('active'));
    win.classList.add('active');
    win.style.zIndex = String(++zTop);
  }

  function selectedWindows() {
    return Array.from(document.querySelectorAll('[data-window].selected'))
      .filter(win => !win.classList.contains('minimized') && !win.classList.contains('maximized'));
  }

  function clearWindowSelection() {
    document.querySelectorAll('[data-window].selected').forEach(win => win.classList.remove('selected'));
  }

  function selectOnlyWindow(win) {
    clearWindowSelection();
    win.classList.add('selected');
  }

  function toggleWindowSelection(win) {
    win.classList.toggle('selected');
    buildState.textContent = 'V2 - ' + selectedWindows().length + ' window' + (selectedWindows().length === 1 ? '' : 's') + ' selected';
  }

  function prepareWindowSelection(win, event) {
    if (event.target.closest('button, textarea, .resize-handle')) return;
    if (event.shiftKey) {
      toggleWindowSelection(win);
    } else if (!win.classList.contains('selected')) {
      selectOnlyWindow(win);
    }
    activateWindow(win);
  }

  function activeDragGroup(win) {
    const selection = selectedWindows();
    if (win.classList.contains('selected') && selection.length > 1) return selection;
    return [win];
  }

  function prepareGroupDrag(group) {
    return group.map(win => {
      const rect = win.getBoundingClientRect();
      win.style.transform = 'none';
      win.style.left = rect.left + 'px';
      win.style.top = rect.top + 'px';
      win.style.width = rect.width + 'px';
      win.style.height = rect.height + 'px';
      win.style.zIndex = String(++zTop);
      return {
        win,
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height
      };
    });
  }

  function windowTitle(win) {
    return win.querySelector('.chat-title')?.textContent?.trim()
      || win.querySelector('.window-title')?.textContent?.trim()
      || win.getAttribute('aria-label')
      || 'Window';
  }

  function ensureWindowId(win, index) {
    if (!win.dataset.windowId) {
      win.dataset.windowId = 'window-' + (index + 1);
    }
    return win.dataset.windowId;
  }

  function dockBubbleFor(win) {
    const dock = document.getElementById('window-dock');
    if (!dock) return null;
    const id = win.dataset.windowId;
    let bubble = dock.querySelector('[data-restore-window="' + id + '"]');
    if (!bubble) {
      bubble = document.createElement('button');
      bubble.type = 'button';
      bubble.className = 'dock-bubble';
      bubble.dataset.restoreWindow = id;
      bubble.innerHTML = '<span class="dock-bubble-label"></span>';
      dock.appendChild(bubble);
    }
    const label = bubble.querySelector('.dock-bubble-label');
    if (label) label.textContent = windowTitle(win);
    bubble.title = 'Restore ' + windowTitle(win);
    bubble.setAttribute('aria-label', 'Restore ' + windowTitle(win));
    return bubble;
  }

  function removeDockBubble(win) {
    const dock = document.getElementById('window-dock');
    const id = win.dataset.windowId;
    const bubble = dock?.querySelector('[data-restore-window="' + id + '"]');
    if (bubble) bubble.remove();
  }

  function setWindowMinimized(win, minimized) {
    win.classList.toggle('minimized', minimized);
    if (minimized) {
      dockBubbleFor(win);
      win.classList.remove('active');
      win.classList.remove('selected');
    } else {
      removeDockBubble(win);
      selectOnlyWindow(win);
      activateWindow(win);
    }
  }

  function setComposerMenuOpen(shell, open) {
    if (!shell) return;
    const button = shell.querySelector('.composer-menu-button');
    const menu = shell.querySelector('.composer-tools-menu');
    shell.classList.toggle('menu-open', open);
    button?.setAttribute('aria-expanded', String(open));
    menu?.setAttribute('aria-hidden', String(!open));
  }

  function setWorkspaceMode(mode) {
    coreWindow.dataset.mode = mode;
    coreWindow.querySelectorAll('[data-mode-option]').forEach(button => {
      const active = button.dataset.modeOption === mode;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    buildState.textContent = mode === 'plan' ? 'V2 - Planning mode mockup' : 'V2 - Agent mode';
  }

  function setVoiceInputMode(active) {
    const composer = coreWindow.querySelector('.composer');
    const button = coreWindow.querySelector('[data-voice-toggle]');
    if (!button || !composer) return;
    composer.classList.toggle('voice-active', active);
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
    button.setAttribute('aria-label', active ? 'Leave voice input mode' : 'Switch to voice input mode');
    buildState.textContent = active ? 'V2 - Voice input mode' : 'V2 - Agent mode';
  }

  function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;'
    }[char]));
  }

  function slug(value) {
    return String(value || '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/(^-|-$)/g, '');
  }

  function renderSettingsControl(row) {
    const [title, , , level, type, value] = row;
    if (type === 'switch') {
      return `<button class="settings-switch ${value === 'on' ? 'on' : ''}" type="button" aria-label="${esc(title)} toggle"></button>`;
    }
    if (type === 'select') {
      return `<select class="settings-select-shell" aria-label="${esc(title)}"><option>${esc(value)}</option><option>Use recommended</option><option>Ask first</option></select>`;
    }
    if (type === 'input') {
      return `<input class="settings-input-shell" value="${esc(value)}" aria-label="${esc(title)}">`;
    }
    if (type === 'secret') {
      return `<button class="settings-tiny-button" type="button">${esc(value)}</button><span class="settings-inline-tags"><span class="secret">secret-safe</span></span>`;
    }
    if (type === 'stack') {
      return `<div class="settings-status-stack">${String(value).split(';').map(item => {
        const [name, status, tone] = item.split('|');
        const color = tone === 'green' ? 'var(--green)' : tone === 'red' ? 'var(--red)' : tone === 'amber' ? 'var(--amber)' : 'var(--teal)';
        return `<div class="settings-status-row" style="--state:${color}"><strong>${esc(name)}</strong><span>${esc(status)}</span></div>`;
      }).join('')}</div>`;
    }
    return `<button class="settings-tiny-button" type="button">${esc(value || 'Open')}</button>${level === 'advanced' ? '<button class="settings-tiny-button" type="button">Explain</button>' : ''}`;
  }

  function renderSettingsCatalog() {
    return settingsCatalog.map(section => `
      <section class="settings-section-v2" style="--section:${section.color}" data-settings-section="${esc(section.name)}">
        <header class="settings-section-head">
          <h2>${esc(section.name)}</h2>
          <span>legacy: ${esc(section.legacy)}</span>
        </header>
        ${section.rows.map(row => {
          const [title, desc, tags, level] = row;
          const id = `setting-${slug(section.name)}-${slug(title)}`;
          const allTags = [...tags, section.name, section.legacy, level].join(' ');
          return `
            <article class="settings-row-v2" id="${id}" data-settings-row data-title="${esc(title)}" data-group="${esc(section.name)}" data-tags="${esc(allTags)}" data-level="${esc(level)}">
              <div class="settings-copy-v2">
                <strong>${esc(title)}</strong>
                <p>${esc(desc)}</p>
                <div class="settings-tags-v2">
                  ${tags.slice(0, 4).map(tag => `<span>${esc(tag)}</span>`).join('')}
                  ${level === 'advanced' ? '<span class="risk">advanced</span>' : ''}
                </div>
              </div>
              <div class="settings-control-v2">${renderSettingsControl(row)}</div>
            </article>
          `;
        }).join('')}
      </section>
    `).join('');
  }

  function settingsRows(win) {
    return Array.from(win.querySelectorAll('[data-settings-row]'));
  }

  function settingsRowText(row) {
    return [row.dataset.title, row.dataset.group, row.dataset.tags, row.textContent]
      .join(' ')
      .toLowerCase();
  }

  function visibleSettingsRows(win) {
    const query = settingsViewState.query.trim().toLowerCase();
    return settingsRows(win).filter(row => {
      const levelOk = settingsViewState.advanced || row.dataset.level !== 'advanced';
      const queryOk = !query || settingsRowText(row).includes(query);
      return levelOk && queryOk;
    });
  }

  function focusSettingsRow(win, row) {
    settingsRows(win).forEach(item => item.classList.remove('highlight'));
    row.hidden = false;
    row.closest('.settings-section-v2').hidden = false;
    row.classList.add('highlight');
    row.scrollIntoView({ block: 'center', behavior: 'smooth' });
    const title = win.querySelector('[data-settings-title]');
    const subtitle = win.querySelector('[data-settings-subtitle]');
    if (title) title.textContent = row.dataset.title;
    if (subtitle) subtitle.textContent = row.querySelector('p')?.textContent || 'Selected setting';
    setTimeout(() => row.classList.remove('highlight'), 1400);
  }

  function refreshSettingsWindow(win) {
    const queryInput = win.querySelector('[data-settings-search]');
    const resultList = win.querySelector('[data-settings-results]');
    const scroll = win.querySelector('[data-settings-scroll]');
    const title = win.querySelector('[data-settings-title]');
    const subtitle = win.querySelector('[data-settings-subtitle]');
    const normalButton = win.querySelector('[data-settings-mode="normal"]');
    const advancedButton = win.querySelector('[data-settings-mode="advanced"]');
    const rows = settingsRows(win);
    const found = visibleSettingsRows(win);
    const query = settingsViewState.query.trim();
    const colors = Object.fromEntries(settingsCatalog.map(section => [section.name, section.color]));

    if (queryInput && queryInput.value !== settingsViewState.query) {
      queryInput.value = settingsViewState.query;
    }
    normalButton?.classList.toggle('active', !settingsViewState.advanced);
    advancedButton?.classList.toggle('active', settingsViewState.advanced);

    rows.forEach(row => {
      row.hidden = !found.includes(row);
    });
    win.querySelectorAll('.settings-section-v2').forEach(section => {
      const hasVisible = Array.from(section.querySelectorAll('[data-settings-row]')).some(row => !row.hidden);
      section.hidden = !hasVisible;
    });
    scroll?.classList.toggle('no-results', found.length === 0);

    if (title) title.textContent = query ? `Search: ${query}` : (settingsViewState.advanced ? 'All settings, advanced' : 'All settings');
    if (subtitle) {
      subtitle.textContent = query
        ? `${found.length} matching settings. Search includes legacy names, new V2 names, admin areas, and plain-language aliases.`
        : (settingsViewState.advanced
          ? 'Advanced mode includes admin, host, secret handoff, plugins, tokens, MCP, feature flags, logs, and destructive actions.'
          : 'Normal mode keeps user-facing controls visible and hides admin-grade controls until needed.');
    }

    if (resultList) {
      const grouped = found.reduce((acc, row) => {
        const group = row.dataset.group || 'Settings';
        acc[group] = acc[group] || [];
        acc[group].push(row);
        return acc;
      }, {});
      resultList.innerHTML = Object.keys(grouped).map(group => {
        const items = grouped[group].map(row => `
          <button class="settings-result-item" style="--item:${colors[group] || 'var(--cyan)'}" type="button" data-settings-target="${row.id}">
            <strong>${esc(row.dataset.title)}</strong>
            <span>${esc(row.dataset.level)} - ${esc(row.dataset.tags.split(' ').slice(0, 2).join(' '))}</span>
          </button>
        `).join('');
        return `<div class="settings-result-group">${esc(group)}</div>${items}`;
      }).join('');
    }
  }

  function renderSettingsWindow() {
    return `
      <div class="settings-window-layout-v2">
        <aside class="settings-rail-v2" aria-label="Settings search and results">
          <div class="settings-search-wrap">
            <label class="settings-search-v2">
              <input data-settings-search type="search" placeholder="Search settings, e.g. model, mount, voice, backup" autocomplete="off">
            </label>
          </div>
          <div class="settings-quick-status" aria-label="System status">
            <div class="settings-status-tile" style="--tile:var(--green)"><strong>Local</strong>ready</div>
            <div class="settings-status-tile" style="--tile:var(--amber)"><strong>GPU</strong>busy</div>
            <div class="settings-status-tile" style="--tile:var(--teal)"><strong>API</strong>online</div>
          </div>
          <div class="settings-result-list" data-settings-results aria-label="Search results"></div>
          <div class="settings-rail-footer">Normal shows user-facing settings. Advanced reveals admin, secret handoff, host, and developer controls.</div>
        </aside>
        <section class="settings-content-v2">
          <header class="settings-content-head">
            <div class="settings-content-title">
              <h1 data-settings-title>All settings</h1>
              <p data-settings-subtitle>A complete searchable map of the legacy settings menu plus the new V2 shell.</p>
            </div>
            <div class="settings-mode-v2" aria-label="Settings detail mode">
              <button class="active" type="button" data-settings-mode="normal">Normal</button>
              <button type="button" data-settings-mode="advanced">Advanced</button>
            </div>
          </header>
          <div class="settings-scroll-v2" data-settings-scroll>
            <div class="settings-empty-v2">No matching setting. Try "model", "endpoint", "email", "mount", "MCP", "shortcut", "backup", or "privacy".</div>
            ${renderSettingsCatalog()}
          </div>
        </section>
      </div>
    `;
  }

  function wireSettingsWindow(win) {
    win.querySelector('[data-settings-search]')?.addEventListener('input', event => {
      settingsViewState.query = event.target.value.trim();
      refreshSettingsWindow(win);
      win.querySelector('[data-settings-search]')?.focus();
    });
    win.querySelector('[data-settings-mode="normal"]')?.addEventListener('click', () => {
      settingsViewState.advanced = false;
      refreshSettingsWindow(win);
    });
    win.querySelector('[data-settings-mode="advanced"]')?.addEventListener('click', () => {
      settingsViewState.advanced = true;
      refreshSettingsWindow(win);
    });
    win.querySelector('[data-settings-results]')?.addEventListener('click', event => {
      const button = event.target.closest('[data-settings-target]');
      if (!button) return;
      const row = win.querySelector('#' + CSS.escape(button.dataset.settingsTarget));
      if (row) focusSettingsRow(win, row);
    });
    win.addEventListener('click', event => {
      const toggle = event.target.closest('.settings-switch');
      if (toggle) toggle.classList.toggle('on');
    });
  }

  function openSettingsWindow(options = {}) {
    const query = options.query ?? settingsViewState.query;
    settingsViewState.query = query || '';
    settingsViewState.advanced = options.advanced ?? settingsViewState.advanced;
    let win = document.getElementById('settings-v2-window');
    if (!win) {
      win = document.createElement('article');
      win.className = 'floating-window settings-v2-window active';
      win.id = 'settings-v2-window';
      win.dataset.window = '';
      win.dataset.windowId = 'settings';
      win.dataset.windowCloseMode = 'remove';
      win.setAttribute('aria-label', 'Settings');
      win.innerHTML = `
        <header class="window-head" data-drag-handle>
          <div class="window-subtitle">system</div>
          <div class="window-title">Settings</div>
          <div class="window-actions" aria-label="Window controls">
            <button class="window-control" data-window-min title="Minimize" aria-label="Minimize">-</button>
            <button class="window-control" data-window-max title="Maximize" aria-label="Maximize">&#9633;</button>
            <button class="window-control" data-window-close title="Close" aria-label="Close">x</button>
          </div>
        </header>
        <div class="window-body">${renderSettingsWindow()}</div>
      `;
      workspace.appendChild(win);
      prepareFloatingWindow(win);
      wireSettingsWindow(win);
    } else {
      setWindowMinimized(win, false);
    }
    refreshSettingsWindow(win);
    activateWindow(win);
    buildState.textContent = 'V2 - Settings';
    closeToolwheel();
  }

  function statusChipClass(label) {
    const normalized = String(label || '').toLowerCase();
    if (normalized.includes('ready') || normalized.includes('green')) return ' ready';
    if (normalized.includes('wait') || normalized.includes('held')) return ' waiting';
    if (normalized.includes('block') || normalized.includes('missing')) return ' blocked';
    return '';
  }

  function renderProjectRows(activeIndex) {
    return projectSamples.map((project, index) => `
      <button class="project-row${index === activeIndex ? ' active' : ''}" style="--status:${project.statusColor}" type="button" data-project-index="${index}">
        <span class="project-dot"></span>
        <span class="project-name"><strong>${esc(project.title)}</strong><span>${esc(project.subtitle)}</span></span>
        <span class="progress-mini">${esc(project.progress)}</span>
      </button>
    `).join('');
  }

  function renderUserTodos(project) {
    const todos = project.todos || [];
    if (!todos.length) {
      return '<div class="todo-empty">Nothing needs you right now.</div>';
    }
    return todos.map((todo, index) => `
      <article class="todo-row${todo.done ? ' done' : ''}" style="--todo:${todo.color || 'var(--cyan)'}" data-todo-index="${index}">
        <button class="todo-check" type="button" data-todo-toggle aria-label="${todo.done ? 'Mark open' : 'Mark done'}"></button>
        <div class="todo-copy">
          <span class="todo-type">${esc(todo.type)}</span>
          <strong class="todo-title" tabindex="0" data-todo-title title="Double click to rename">${esc(todo.title)}</strong>
          <span class="todo-detail">${esc(todo.detail)}</span>
          <span class="todo-source">${esc(todo.source)}</span>
        </div>
        <button class="todo-delete" type="button" data-todo-delete aria-label="Delete task">x</button>
      </article>
    `).join('');
  }

  function todoPriority(todo) {
    if (todo.priority) return todo.priority;
    const type = String(todo.type || '').toLowerCase();
    if (type.includes('blocked')) return 'P0';
    if (type.includes('decision') || type.includes('review')) return 'P1';
    if (type.includes('commit') || type.includes('question')) return 'P2';
    return 'P3';
  }

  function todoStatus(todo) {
    if (todo.done) return 'Done';
    const type = String(todo.type || '').toLowerCase();
    if (type.includes('blocked')) return 'Blocked';
    if (type.includes('decision') || type.includes('question')) return 'Waiting';
    return 'Active';
  }

  function todoUpdatedValue(todo) {
    const value = String(todo.updated || todo.source || '').toLowerCase();
    if (value.includes('now')) return 0;
    const match = value.match(/(\d+)/);
    const number = match ? Number(match[1]) : 99;
    if (value.includes('m')) return number;
    if (value.includes('h')) return number * 60;
    if (value.includes('d')) return number * 1440;
    return number + 1000;
  }

  function allTodos() {
    const projectTodos = projectSamples.flatMap((project, projectIndex) => (project.todos || []).map((todo, todoIndex) => ({
      ...todo,
      id: 'project-' + projectIndex + '-' + todoIndex,
      project: project.title,
      projectIndex,
      todoIndex,
      priority: todoPriority(todo),
      updated: todo.source?.match(/\d+[mhdw]|now/)?.[0] || 'now',
      isProjectTodo: true
    })));
    return [...projectTodos, ...globalTodoExtras].map(todo => ({
      ...todo,
      status: todoStatus(todo),
      priority: todoPriority(todo)
    }));
  }

  function visibleTodos() {
    const query = todosViewState.query.toLowerCase();
    const statusRank = { Blocked: 0, Waiting: 1, Active: 2, Done: 3 };
    const priorityRank = { P0: 0, P1: 1, P2: 2, P3: 3 };
    const items = allTodos().filter(todo => {
      const matchesFilter = todosViewState.filter === 'All' || todo.status === todosViewState.filter;
      const haystack = [todo.title, todo.project, todo.type, todo.detail, todo.source].join(' ').toLowerCase();
      return matchesFilter && (!query || haystack.includes(query));
    });

    return items.sort((a, b) => {
      if (todosViewState.sort === 'Project') {
        return a.project.localeCompare(b.project)
          || (priorityRank[a.priority] ?? 9) - (priorityRank[b.priority] ?? 9)
          || a.title.localeCompare(b.title);
      }
      if (todosViewState.sort === 'Updated') return todoUpdatedValue(a) - todoUpdatedValue(b);
      if (todosViewState.sort === 'Status') {
        return (statusRank[a.status] ?? 9) - (statusRank[b.status] ?? 9)
          || (priorityRank[a.priority] ?? 9) - (priorityRank[b.priority] ?? 9);
      }
      return (priorityRank[a.priority] ?? 9) - (priorityRank[b.priority] ?? 9)
        || a.project.localeCompare(b.project);
    });
  }

  function renderTodoFilters() {
    const filters = ['All', 'Active', 'Waiting', 'Blocked', 'Done'];
    const todos = allTodos();
    return filters.map(filter => {
      const count = filter === 'All' ? todos.length : todos.filter(todo => todo.status === filter).length;
      return `<button class="todos-filter${todosViewState.filter === filter ? ' active' : ''}" type="button" data-todos-filter="${filter}"><span>${filter}</span><span>${count}</span></button>`;
    }).join('');
  }

  function renderTodosList(items) {
    if (!items.length) {
      return '<div class="todos-empty">No matching todos.</div>';
    }

    let lastProject = '';
    return items.map((todo, index) => {
      const group = todosViewState.sort === 'Project' && todo.project !== lastProject
        ? `<div class="todos-project-group">${esc(todo.project)}</div>`
        : '';
      lastProject = todo.project;
      return group + `
        <button class="global-todo-row${todo.id === todosViewState.selectedId ? ' active' : ''}" style="--todo:${todo.color || 'var(--cyan)'}" type="button" data-global-todo-id="${esc(todo.id)}">
          <span class="global-todo-dot"></span>
          <span class="global-todo-copy">
            <span class="global-todo-title-line"><strong>${esc(todo.title)}</strong><span>${esc(todo.status)}</span></span>
            <span class="global-todo-meta"><span>${esc(todo.project)}</span><span>${esc(todo.source)}</span><span>${esc(todo.priority)}</span></span>
          </span>
        </button>
      `;
    }).join('');
  }

  function renderTodoDetail(todo) {
    if (!todo) {
      return '<div class="todos-detail-empty">Select a todo.</div>';
    }
    return `
      <div class="todos-detail-head">
        <div class="todos-detail-kicker"><span>${esc(todo.project)}</span><span>${esc(todo.status)}</span></div>
        <h2>${esc(todo.title)}</h2>
        <p>${esc(todo.detail)}</p>
      </div>
      <div class="todos-detail-body">
        <div class="todos-props">
          <div><span>Priority</span><strong>${esc(todo.priority)}</strong></div>
          <div><span>Source</span><strong>${esc(todo.source)}</strong></div>
          <div><span>Project</span><strong>${esc(todo.project)}</strong></div>
          <div><span>Updated</span><strong>${esc(todo.updated || 'now')}</strong></div>
        </div>
      </div>
      <div class="todos-detail-actions">
        <button type="button" data-todos-focus-source>Open Source</button>
        <button class="primary" type="button" data-todos-toggle-done>${todo.done ? 'Reopen' : 'Mark Done'}</button>
      </div>
    `;
  }

  function renderTodosWindow() {
    const items = visibleTodos();
    if (!todosViewState.selectedId || !items.some(todo => todo.id === todosViewState.selectedId)) {
      todosViewState.selectedId = items[0]?.id || null;
    }
    const selected = allTodos().find(todo => todo.id === todosViewState.selectedId);
    return `
      <div class="todos-window-layout">
        <section class="todos-main" aria-label="Todos">
          <div class="todos-toolbar">
            <div class="todos-toolbar-top">
              <label class="todos-search">
                <input type="search" value="${esc(todosViewState.query)}" placeholder="find todos, projects, blockers" data-todos-search>
              </label>
              <button class="todos-sort" type="button" data-todos-sort>Sort: ${esc(todosViewState.sort)}</button>
              <button class="todos-new" type="button" data-todos-new>New Todo</button>
            </div>
            <div class="todos-filters">${renderTodoFilters()}</div>
          </div>
          <div class="global-todo-list">${renderTodosList(items)}</div>
        </section>
        <aside class="todos-detail" aria-label="Todo details">${renderTodoDetail(selected)}</aside>
      </div>
    `;
  }

  function refreshTodosWindow(win) {
    const body = win.querySelector('.window-body');
    body.innerHTML = renderTodosWindow();
    wireTodosWindow(win);
  }

  function toggleTodoDone(todo) {
    if (!todo) return;
    if (todo.isProjectTodo) {
      const source = projectSamples[todo.projectIndex]?.todos?.[todo.todoIndex];
      if (source) source.done = !source.done;
      return;
    }
    const extra = globalTodoExtras.find(item => item.id === todo.id);
    if (extra) extra.done = !extra.done;
  }

  function wireTodosWindow(win) {
    win.querySelector('[data-todos-search]')?.addEventListener('input', event => {
      todosViewState.query = event.target.value.trim();
      refreshTodosWindow(win);
      win.querySelector('[data-todos-search]')?.focus();
    });
    win.querySelector('[data-todos-sort]')?.addEventListener('click', () => {
      const modes = ['Project', 'Priority', 'Updated', 'Status'];
      const next = (modes.indexOf(todosViewState.sort) + 1) % modes.length;
      todosViewState.sort = modes[next];
      refreshTodosWindow(win);
    });
    win.querySelectorAll('[data-todos-filter]').forEach(button => {
      button.addEventListener('click', () => {
        todosViewState.filter = button.dataset.todosFilter || 'All';
        todosViewState.selectedId = null;
        refreshTodosWindow(win);
      });
    });
    win.querySelectorAll('[data-global-todo-id]').forEach(button => {
      button.addEventListener('click', () => {
        todosViewState.selectedId = button.dataset.globalTodoId;
        refreshTodosWindow(win);
      });
    });
    win.querySelector('[data-todos-toggle-done]')?.addEventListener('click', () => {
      toggleTodoDone(allTodos().find(todo => todo.id === todosViewState.selectedId));
      refreshTodosWindow(win);
    });
    win.querySelector('[data-todos-focus-source]')?.addEventListener('click', () => {
      const selected = allTodos().find(todo => todo.id === todosViewState.selectedId);
      if (selected?.isProjectTodo) {
        openProjectsOverview(selected.projectIndex);
      }
      buildState.textContent = 'V2 - Todo source focused';
    });
    win.querySelector('[data-todos-new]')?.addEventListener('click', () => {
      globalTodoExtras.unshift({
        id: 'global-manual-' + Date.now(),
        project: 'Inbox',
        type: 'Manual',
        title: 'Untitled todo',
        detail: 'New manual todo placeholder.',
        source: 'manual',
        color: 'var(--cyan)',
        priority: 'P3',
        updated: 'now',
        done: false
      });
      todosViewState.filter = 'All';
      todosViewState.sort = 'Project';
      todosViewState.selectedId = globalTodoExtras[0].id;
      refreshTodosWindow(win);
    });
  }

  function skillHealthClass(skill) {
    if (skill.health === 'review') return 'review';
    if (skill.health === 'draft') return 'draft';
    if (skill.health === 'blocked') return 'blocked';
    return 'ready';
  }

  function visibleSkills() {
    const query = skillsViewState.query.toLowerCase();
    return skillSamples.filter(skill => {
      const matchesFilter = skillsViewState.filter === 'all'
        || skill.category === skillsViewState.filter
        || (skillsViewState.filter === 'review' && skill.health !== 'ready');
      const haystack = [
        skill.name,
        skill.category,
        skill.summary,
        skill.purpose,
        skill.used,
        skill.rules,
        skill.healthLabel,
        skill.trust,
        skill.healthReason,
        skill.trustReason,
        skill.reviewAction
      ].join(' ').toLowerCase();
      return matchesFilter && (!query || haystack.includes(query));
    });
  }

  function renderSkillFilters() {
    const filters = [
      ['all', 'All'],
      ['cloud', 'Cloud'],
      ['memory', 'Memory'],
      ['projects', 'Projects'],
      ['code', 'Code'],
      ['automation', 'Automation'],
      ['review', 'Needs review']
    ];
    return filters.map(([id, label]) => `
      <button class="skills-filter${skillsViewState.filter === id ? ' active' : ''}${id === 'review' ? ' review' : ''}" type="button" data-skills-filter="${esc(id)}">${esc(label)}</button>
    `).join('');
  }

  function renderSkillsList(items) {
    if (!items.length) {
      return '<div class="skills-empty">No matching skills.</div>';
    }
    return items.map(skill => `
      <button class="skills-row${skill.id === skillsViewState.selectedId ? ' active' : ''}" type="button" data-skill-id="${esc(skill.id)}">
        <span class="skills-health-dot ${esc(skillHealthClass(skill))}" aria-hidden="true"></span>
        <span class="skills-row-copy">
          <strong>${esc(skill.name)}</strong>
          <span>${esc(skill.summary)}</span>
        </span>
        <span class="skills-state-pill">${esc(skill.healthLabel)}</span>
      </button>
    `).join('');
  }

  function renderSkillDetail(skill) {
    if (!skill) {
      return '<div class="skills-detail-empty">Select a skill.</div>';
    }
    return `
      <div class="skills-detail-head">
        <span>${esc(skill.category)}</span>
        <h2>${esc(skill.name)}</h2>
      </div>
      <section class="skills-health-panel" aria-label="Skill health">
        <article class="skills-health-card ${esc(skillHealthClass(skill))}">
          <span>Skill Health</span>
          <strong>${esc(skill.healthLabel)}</strong>
          <p>${esc(skill.healthReason)}</p>
        </article>
        <article class="skills-health-card ${esc(skillHealthClass(skill))}">
          <span>Trust</span>
          <strong>${esc(skill.trust)}</strong>
          <p>${esc(skill.trustReason)}</p>
        </article>
        <article class="skills-health-card">
          <span>Last checked</span>
          <strong>${esc(skill.checked)}</strong>
          <p>${esc(skill.reviewAction)}</p>
        </article>
      </section>
      <section class="skills-detail-grid">
        <article class="skills-section wide">
          <h3>Purpose</h3>
          <p>${esc(skill.purpose)}</p>
        </article>
        <article class="skills-section">
          <h3>Used when</h3>
          <p>${esc(skill.used)}</p>
        </article>
        <article class="skills-section">
          <h3>Allowed</h3>
          <ul>${skill.allowed.map(item => `<li>${esc(item)}</li>`).join('')}</ul>
        </article>
        <article class="skills-section">
          <h3>Never</h3>
          <ul class="never">${skill.never.map(item => `<li>${esc(item)}</li>`).join('')}</ul>
        </article>
        <article class="skills-section">
          <h3>Recent activity</h3>
          <p>${esc(skill.activity)}</p>
        </article>
        <article class="skills-section wide">
          <h3>Rules</h3>
          <p>${esc(skill.rules)}</p>
        </article>
      </section>
      <div class="skills-actions">
        <button class="primary" type="button" data-skill-review>Run skill review</button>
        <button type="button" data-skill-edit-rules>Edit skill rules</button>
        <button type="button" data-skill-open-activity>Open activity</button>
        <button type="button" data-skill-disable>Disable skill</button>
      </div>
    `;
  }

  function renderSkillsWindow() {
    const items = visibleSkills();
    if (!skillsViewState.selectedId || !skillSamples.some(skill => skill.id === skillsViewState.selectedId)) {
      skillsViewState.selectedId = items[0]?.id || skillSamples[0]?.id || null;
    }
    if (items.length && !items.some(skill => skill.id === skillsViewState.selectedId)) {
      skillsViewState.selectedId = items[0].id;
    }
    const selected = skillSamples.find(skill => skill.id === skillsViewState.selectedId);
    const needsReview = skillSamples.filter(skill => skill.health !== 'ready').length;
    return `
      <div class="skills-window-layout">
        <div class="skills-toolbar">
          <div class="skills-toolbar-summary"><span class="model-status-dot ready" aria-hidden="true"></span>${skillSamples.length} skills · ${needsReview} need review</div>
          <nav class="skills-filters" aria-label="Skill filters">${renderSkillFilters()}</nav>
          <label class="skills-search">
            <input type="search" value="${esc(skillsViewState.query)}" placeholder="Search skills, actions, rules, tools" data-skills-search>
          </label>
          <span class="skills-rules-chip">Rules editable here</span>
        </div>
        <div class="skills-content">
          <aside class="skills-list" aria-label="Skills">${renderSkillsList(items)}</aside>
          <section class="skills-detail" aria-label="Skill details">${renderSkillDetail(selected)}</section>
        </div>
      </div>
    `;
  }

  function refreshSkillsWindow(win) {
    win.querySelector('.window-body').innerHTML = renderSkillsWindow();
    wireSkillsWindow(win);
  }

  function wireSkillsWindow(win) {
    win.querySelector('[data-skills-search]')?.addEventListener('input', event => {
      skillsViewState.query = event.target.value.trim();
      refreshSkillsWindow(win);
      win.querySelector('[data-skills-search]')?.focus();
    });
    win.querySelectorAll('[data-skills-filter]').forEach(button => {
      button.addEventListener('click', () => {
        skillsViewState.filter = button.dataset.skillsFilter || 'all';
        refreshSkillsWindow(win);
      });
    });
    win.querySelectorAll('[data-skill-id]').forEach(button => {
      button.addEventListener('click', () => {
        skillsViewState.selectedId = button.dataset.skillId;
        refreshSkillsWindow(win);
      });
    });
    win.querySelector('[data-skill-review]')?.addEventListener('click', () => {
      const skill = skillSamples.find(item => item.id === skillsViewState.selectedId);
      buildState.textContent = 'V2 - Skill review queued for ' + (skill?.name || 'selected skill');
    });
    win.querySelector('[data-skill-edit-rules]')?.addEventListener('click', () => {
      const skill = skillSamples.find(item => item.id === skillsViewState.selectedId);
      buildState.textContent = 'V2 - Editing rules for ' + (skill?.name || 'selected skill');
    });
    win.querySelector('[data-skill-open-activity]')?.addEventListener('click', () => {
      const skill = skillSamples.find(item => item.id === skillsViewState.selectedId);
      buildState.textContent = 'V2 - Activity opened for ' + (skill?.name || 'selected skill');
    });
    win.querySelector('[data-skill-disable]')?.addEventListener('click', () => {
      const skill = skillSamples.find(item => item.id === skillsViewState.selectedId);
      buildState.textContent = 'V2 - Disable skill placeholder: ' + (skill?.name || 'selected skill');
    });
  }

  function openSkillsWindow(options = {}) {
    if (options.skillId && skillSamples.some(skill => skill.id === options.skillId)) {
      skillsViewState.selectedId = options.skillId;
    }
    if (options.filter) skillsViewState.filter = options.filter;
    let win = document.getElementById('skills-window');
    if (!win) {
      win = document.createElement('article');
      win.className = 'floating-window skills-window active';
      win.id = 'skills-window';
      win.dataset.window = '';
      win.dataset.windowId = 'skills';
      win.dataset.windowCloseMode = 'remove';
      win.setAttribute('aria-label', 'Skills');
      win.innerHTML = `
        <header class="window-head" data-drag-handle>
          <div class="window-subtitle">automation</div>
          <div class="window-title">Skills</div>
          <div class="window-actions" aria-label="Window controls">
            <button class="window-control" data-window-min title="Minimize" aria-label="Minimize">-</button>
            <button class="window-control" data-window-max title="Maximize" aria-label="Maximize">&#9633;</button>
            <button class="window-control" data-window-close title="Close" aria-label="Close">x</button>
          </div>
        </header>
        <div class="window-body">${renderSkillsWindow()}</div>
      `;
      workspace.appendChild(win);
      prepareFloatingWindow(win);
      installResizeHandles(win);
      wireSkillsWindow(win);
    } else {
      setWindowMinimized(win, false);
      win.querySelector('.window-body').innerHTML = renderSkillsWindow();
      wireSkillsWindow(win);
    }
    activateWindow(win);
    buildState.textContent = 'V2 - Skills';
    closeToolwheel();
  }

  function renderProjectsOverview(activeIndex = 0) {
    const project = projectSamples[activeIndex] || projectSamples[0];
    return `
      <div class="projects-layout">
        <aside class="project-rail" aria-label="Project list">
          <div class="rail-title">
            <span>Projects</span>
            <span class="rail-count">${projectSamples.length}</span>
          </div>
          <div class="project-list">
            ${renderProjectRows(activeIndex)}
          </div>
          <div class="metric-strip" aria-label="Project metrics">
            <div class="metric"><span class="metric-label">Progress</span><span class="metric-value">${esc(project.progress.replace('/', ' / '))}</span></div>
            <div class="metric"><span class="metric-label">Next</span><span class="metric-value">${esc(project.next)}</span></div>
            <div class="metric"><span class="metric-label">Branch</span><span class="metric-value">${esc(project.branch)}</span></div>
            <div class="metric"><span class="metric-label">Decision</span><span class="metric-value">${esc(project.decision)}</span></div>
          </div>
        </aside>
        <section class="project-overview" aria-label="Selected project overview">
          <div class="overview-head">
            <div>
              <h1 class="project-title">${esc(project.title)}</h1>
              <div class="project-sub">Zero-sidebar project context with roadmap visibility, floating windows, and a minimal command surface.</div>
            </div>
            <div class="status-stack">
              ${project.chips.map(chip => `<span class="status-chip${statusChipClass(chip)}">${esc(chip)}</span>`).join('')}
              <span class="status-chip privacy">privacy mode</span>
            </div>
          </div>
          <div class="overview-main">
            <section class="graph-panel" aria-label="Roadmap graph placeholder">
              <div class="panel-head">
                <span>Roadmap</span>
                <div class="view-tabs" aria-label="Overview views">
                  <button class="active" type="button">Graph</button>
                  <button type="button">List</button>
                  <button type="button">Changes</button>
                </div>
              </div>
              <div class="roadmap-graph">
                <span class="graph-line" style="left: 90px; top: 90px; width: 76px; rotate: 28deg;"></span>
                <span class="graph-line" style="left: 132px; top: 154px; width: 78px; rotate: 134deg;"></span>
                <span class="graph-line" style="left: 88px; top: 214px; width: 76px; rotate: 24deg;"></span>
                <span class="graph-line" style="left: 136px; top: 254px; width: 62px; rotate: 130deg;"></span>
                <div class="graph-node" style="--node: var(--green); left: 30px; top: 54px;"><strong>Shell</strong><span>done</span></div>
                <div class="graph-node" style="--node: var(--blue); left: 132px; top: 112px;"><strong>Projects</strong><span>${esc(project.status)}</span></div>
                <div class="graph-node" style="--node: var(--amber); left: 34px; top: 182px;"><strong>Decisions</strong><span>${project.decision === 'none' ? 'clear' : 'waiting'}</span></div>
                <div class="graph-node" style="--node: var(--cyan); left: 132px; top: 238px;"><strong>Composer</strong><span>ready</span></div>
                <div class="graph-node" style="--node: var(--red); left: 34px; top: 292px;"><strong>Deploy</strong><span>${project.status === 'blocked' ? 'blocked' : 'held'}</span></div>
              </div>
            </section>
            <section class="todo-panel" aria-label="User To-Do">
              <div class="panel-head">
                <span>User To-Do</span>
                <span>${(project.todos || []).filter(todo => !todo.done).length} open</span>
              </div>
              <div class="todo-list" data-active-project="${activeIndex}">
                ${renderUserTodos(project)}
              </div>
            </section>
          </div>
          <div class="placeholder-note">Placeholder window - UI only</div>
        </section>
      </div>
    `;
  }

  function wireProjectsOverview(win) {
    win.querySelectorAll('[data-project-index]').forEach(button => {
      button.addEventListener('click', event => {
        event.preventDefault();
        const index = Number(button.dataset.projectIndex || 0);
        const body = win.querySelector('.window-body');
        body.innerHTML = renderProjectsOverview(index);
        wireProjectsOverview(win);
      });
    });

    win.querySelectorAll('[data-todo-toggle]').forEach(button => {
      button.addEventListener('click', event => {
        event.preventDefault();
        const row = button.closest('[data-todo-index]');
        const list = button.closest('[data-active-project]');
        const project = projectSamples[Number(list?.dataset.activeProject || 0)];
        const todo = project?.todos?.[Number(row?.dataset.todoIndex || 0)];
        if (!todo) return;
        todo.done = !todo.done;
        const body = win.querySelector('.window-body');
        body.innerHTML = renderProjectsOverview(Number(list.dataset.activeProject || 0));
        wireProjectsOverview(win);
      });
    });

    win.querySelectorAll('[data-todo-delete]').forEach(button => {
      button.addEventListener('click', event => {
        event.preventDefault();
        const row = button.closest('[data-todo-index]');
        const list = button.closest('[data-active-project]');
        const projectIndex = Number(list?.dataset.activeProject || 0);
        projectSamples[projectIndex]?.todos?.splice(Number(row?.dataset.todoIndex || 0), 1);
        const body = win.querySelector('.window-body');
        body.innerHTML = renderProjectsOverview(projectIndex);
        wireProjectsOverview(win);
      });
    });

    win.querySelectorAll('[data-todo-title]').forEach(title => {
      title.addEventListener('dblclick', () => {
        title.contentEditable = 'true';
        title.focus();
        document.getSelection()?.selectAllChildren(title);
      });
      title.addEventListener('keydown', event => {
        if (event.key === 'Enter') {
          event.preventDefault();
          title.blur();
        }
        if (event.key === 'Escape') {
          event.preventDefault();
          title.blur();
        }
      });
      title.addEventListener('blur', () => {
        if (title.contentEditable !== 'true') return;
        const row = title.closest('[data-todo-index]');
        const list = title.closest('[data-active-project]');
        const project = projectSamples[Number(list?.dataset.activeProject || 0)];
        const todo = project?.todos?.[Number(row?.dataset.todoIndex || 0)];
        if (todo) todo.title = title.textContent.trim() || todo.title;
        title.contentEditable = 'false';
      });
    });
  }

  function openProjectsOverview(activeIndex = 0) {
    let win = document.getElementById('projects-overview-window');
    if (!win) {
      win = document.createElement('article');
      win.className = 'floating-window projects-overview-window active';
      win.id = 'projects-overview-window';
      win.dataset.window = '';
      win.dataset.windowId = 'projects-overview';
      win.dataset.windowCloseMode = 'remove';
      win.setAttribute('aria-label', 'Projects and overview');
      win.innerHTML = `
        <header class="window-head" data-drag-handle>
          <div class="window-subtitle">projects</div>
          <div class="window-title">Projects / Overview</div>
          <div class="window-actions" aria-label="Window controls">
            <button class="window-control" data-window-min title="Minimize" aria-label="Minimize">-</button>
            <button class="window-control" data-window-max title="Maximize" aria-label="Maximize">&#9633;</button>
            <button class="window-control" data-window-close title="Close" aria-label="Close">x</button>
          </div>
        </header>
        <div class="window-body">${renderProjectsOverview(activeIndex)}</div>
      `;
      workspace.appendChild(win);
      prepareFloatingWindow(win);
      wireProjectsOverview(win);
    } else {
      setWindowMinimized(win, false);
      win.querySelector('.window-body').innerHTML = renderProjectsOverview(activeIndex);
      wireProjectsOverview(win);
    }
    activateWindow(win);
    buildState.textContent = 'V2 - Projects / Overview';
    closeToolwheel();
  }

  function openTodosWindow() {
    let win = document.getElementById('todos-window');
    if (!win) {
      win = document.createElement('article');
      win.className = 'floating-window todos-window active';
      win.id = 'todos-window';
      win.dataset.window = '';
      win.dataset.windowId = 'todos';
      win.dataset.windowCloseMode = 'remove';
      win.setAttribute('aria-label', 'Todos');
      win.innerHTML = `
        <header class="window-head" data-drag-handle>
          <div class="window-subtitle">global</div>
          <div class="window-title">Todos</div>
          <div class="window-actions" aria-label="Window controls">
            <button class="window-control" data-window-min title="Minimize" aria-label="Minimize">-</button>
            <button class="window-control" data-window-max title="Maximize" aria-label="Maximize">&#9633;</button>
            <button class="window-control" data-window-close title="Close" aria-label="Close">x</button>
          </div>
        </header>
        <div class="window-body">${renderTodosWindow()}</div>
      `;
      workspace.appendChild(win);
      prepareFloatingWindow(win);
      wireTodosWindow(win);
    } else {
      setWindowMinimized(win, false);
      refreshTodosWindow(win);
    }
    activateWindow(win);
    buildState.textContent = 'V2 - Todos';
    closeToolwheel();
  }

  function knowledgeGraphRandom(seed) {
    let value = seed % 2147483647;
    return () => {
      value = value * 16807 % 2147483647;
      return (value - 1) / 2147483646;
    };
  }

  function ensureKnowledgeGraphData() {
    if (knowledgeGraphNodes.length) return;
    const random = knowledgeGraphRandom(428);
    knowledgeGraphNodes = Array.from({ length: 260 }, (_, index) => {
      const source = knowledgeGraphSources[index % knowledgeGraphSources.length];
      const type = knowledgeGraphTypes[(index * 7) % knowledgeGraphTypes.length];
      const ring = Math.floor(index / 38);
      const angle = random() * Math.PI * 2;
      const radius = 82 + ring * 54 + random() * 118;
      const topic = index % 11 === 0 ? 'Memory retrieval' : source;
      return {
        id: 'kg-' + index,
        title: topic + ' ' + type + ' ' + (index + 1),
        source,
        type,
        cluster: source + ' / ' + (type === 'Cluster' ? 'Core' : type),
        trust: Math.round(26 + random() * 74),
        age: Math.round(random() * 96) + 'h',
        x: Math.cos(angle) * radius + random() * 72,
        y: Math.sin(angle) * radius + random() * 72,
        size: type === 'Cluster' ? 8 + random() * 8 : 3.5 + random() * 5,
        summary: 'Derived knowledge node with linked sources, summaries, memory candidates and project context.'
      };
    });

    knowledgeGraphEdges = [];
    for (let index = 0; index < knowledgeGraphNodes.length; index += 1) {
      const links = 1 + (index % 4);
      for (let step = 1; step <= links; step += 1) {
        knowledgeGraphEdges.push({
          from: knowledgeGraphNodes[index].id,
          to: knowledgeGraphNodes[(index + step * (7 + index % 9)) % knowledgeGraphNodes.length].id,
          weight: 0.2 + step / 5
        });
      }
    }
  }

  function visibleKnowledgeGraphNodes() {
    ensureKnowledgeGraphData();
    const query = knowledgeGraphState.query.toLowerCase();
    return knowledgeGraphNodes.filter(node => {
      const sourceOk = knowledgeGraphState.source === 'All' || node.source === knowledgeGraphState.source;
      const typeOk = knowledgeGraphState.type === 'All' || node.type === knowledgeGraphState.type;
      const trustOk = node.trust >= knowledgeGraphState.trust;
      const queryOk = !query || [node.title, node.source, node.type, node.cluster].join(' ').toLowerCase().includes(query);
      return sourceOk && typeOk && trustOk && queryOk;
    }).slice(0, Math.max(20, Math.round(knowledgeGraphNodes.length * knowledgeGraphState.density / 100)));
  }

  function visibleKnowledgeGraphEdges(nodes) {
    const ids = new Set(nodes.map(node => node.id));
    return knowledgeGraphEdges.filter(edge => ids.has(edge.from) && ids.has(edge.to));
  }

  function knowledgeGraphColor(node) {
    return knowledgeGraphPalette[node.source] || '#16d9f5';
  }

  function projectKnowledgeGraphNode(node, rect) {
    const scale = Math.min(rect.width, rect.height) / 760;
    let x = node.x;
    let y = node.y;

    if (knowledgeGraphState.layout === 'Source') {
      const sourceIndex = knowledgeGraphSources.indexOf(node.source);
      const angle = (sourceIndex / knowledgeGraphSources.length) * Math.PI * 2 - Math.PI / 2;
      x = Math.cos(angle) * 240 + node.x * 0.38;
      y = Math.sin(angle) * 190 + node.y * 0.38;
    } else if (knowledgeGraphState.layout === 'Time') {
      const typeIndex = knowledgeGraphTypes.indexOf(node.type);
      x = (node.trust - 58) * 7 + node.x * 0.34;
      y = (typeIndex - 2.5) * 76 + node.y * 0.28;
    }

    return {
      x: rect.width / 2 + x * scale,
      y: rect.height / 2 + y * scale
    };
  }

  function renderKnowledgeGraphFacets(win) {
    ensureKnowledgeGraphData();
    const sourceTarget = win.querySelector('[data-kg-source-facets]');
    const typeTarget = win.querySelector('[data-kg-type-facets]');
    if (!sourceTarget || !typeTarget) return;

    sourceTarget.innerHTML = [['All', '#16d9f5'], ...knowledgeGraphSources.map(source => [source, knowledgeGraphPalette[source]])].map(([label, color]) => `
      <button class="kg-facet${knowledgeGraphState.source === label ? ' active' : ''}" style="--facet:${color}" type="button" data-kg-source="${esc(label)}">
        <strong>${esc(label)}</strong><span>${label === 'All' ? knowledgeGraphNodes.length : knowledgeGraphNodes.filter(node => node.source === label).length}</span>
      </button>
    `).join('');

    typeTarget.innerHTML = [['All', '#16d9f5'], ...knowledgeGraphTypes.map(type => [type, '#22d3b6'])].map(([label, color]) => `
      <button class="kg-facet${knowledgeGraphState.type === label ? ' active' : ''}" style="--facet:${color}" type="button" data-kg-type="${esc(label)}">
        <strong>${esc(label)}</strong><span>${label === 'All' ? knowledgeGraphNodes.length : knowledgeGraphNodes.filter(node => node.type === label).length}</span>
      </button>
    `).join('');

    win.querySelector('[data-kg-source-label]').textContent = knowledgeGraphState.source;
    win.querySelector('[data-kg-type-label]').textContent = knowledgeGraphState.type;
  }

  function renderKnowledgeGraphInspector(win, node) {
    const inspector = win.querySelector('[data-kg-inspector]');
    if (!inspector) return;
    if (!node) {
      inspector.innerHTML = '<div class="kg-inspector-empty">No node selected.</div>';
      return;
    }
    const links = knowledgeGraphEdges.filter(edge => edge.from === node.id || edge.to === node.id).length;
    inspector.innerHTML = `
      <div class="kg-inspector-head">
        <div class="kg-inspector-kicker"><span>${esc(node.source)}</span><span>${node.trust}% trust</span></div>
        <h2>${esc(node.title)}</h2>
        <p>${esc(node.summary)}</p>
      </div>
      <div class="kg-inspector-body">
        <div class="kg-props">
          <div><span>Type</span><strong>${esc(node.type)}</strong></div>
          <div><span>Cluster</span><strong>${esc(node.cluster)}</strong></div>
          <div><span>Age</span><strong>${esc(node.age)}</strong></div>
          <div><span>Links</span><strong>${links}</strong></div>
          <div><span>Render</span><strong>Canvas node</strong></div>
        </div>
      </div>
      <div class="kg-inspector-actions">
        <button type="button" data-kg-action="Open Source">Open Source</button>
        <button type="button" data-kg-action="Attach">Attach</button>
        <button class="primary" type="button" data-kg-action="Use Cluster">Use Cluster</button>
      </div>
    `;
  }

  function drawKnowledgeGraphClusters(ctx, nodes, rect) {
    const groups = new Map();
    nodes.forEach(node => {
      if (!groups.has(node.source)) groups.set(node.source, []);
      groups.get(node.source).push(node);
    });

    groups.forEach((group, source) => {
      const center = group.reduce((acc, node) => {
        const point = projectKnowledgeGraphNode(node, rect);
        acc.x += point.x;
        acc.y += point.y;
        return acc;
      }, { x: 0, y: 0 });
      center.x /= group.length;
      center.y /= group.length;
      const radius = Math.min(92, 24 + group.length * 0.82);
      ctx.beginPath();
      ctx.strokeStyle = knowledgeGraphPalette[source] + '36';
      ctx.fillStyle = knowledgeGraphPalette[source] + '0f';
      ctx.arc(center.x, center.y, radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    });
  }

  function drawKnowledgeGraphMinimap(win, nodes) {
    const canvas = win.querySelector('[data-kg-minimap]');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const width = 132;
    const height = 92;
    canvas.width = Math.floor(width * dpr);
    canvas.height = Math.floor(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = 'rgba(2, 9, 16, 0.72)';
    ctx.fillRect(0, 0, width, height);
    nodes.forEach(node => {
      ctx.fillStyle = knowledgeGraphColor(node) + '99';
      ctx.beginPath();
      ctx.arc(66 + node.x / 12, 46 + node.y / 12, 1.45, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.strokeStyle = 'rgba(22, 217, 245, 0.28)';
    ctx.strokeRect(26, 18, 80, 56);
  }

  function drawKnowledgeGraph(win) {
    const canvas = win.querySelector('[data-kg-canvas]');
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const sizeKey = rect.width + 'x' + rect.height + '@' + dpr;
    if (canvas.dataset.sizeKey !== sizeKey) {
      canvas.width = Math.floor(rect.width * dpr);
      canvas.height = Math.floor(rect.height * dpr);
      canvas.dataset.sizeKey = sizeKey;
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, rect.width, rect.height);
    ctx.fillStyle = 'rgba(2, 9, 16, 0.24)';
    ctx.fillRect(0, 0, rect.width, rect.height);

    const nodes = visibleKnowledgeGraphNodes();
    const edges = visibleKnowledgeGraphEdges(nodes);
    const byId = new Map(nodes.map(node => [node.id, node]));
    edges.forEach(edge => {
      const a = byId.get(edge.from);
      const b = byId.get(edge.to);
      if (!a || !b) return;
      const pa = projectKnowledgeGraphNode(a, rect);
      const pb = projectKnowledgeGraphNode(b, rect);
      ctx.strokeStyle = 'rgba(22, 217, 245, 0.09)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pa.x, pa.y);
      ctx.lineTo(pb.x, pb.y);
      ctx.stroke();
    });

    if (knowledgeGraphState.cluster && nodes.length > 80) {
      drawKnowledgeGraphClusters(ctx, nodes, rect);
    }

    nodes.forEach(node => {
      const p = projectKnowledgeGraphNode(node, rect);
      const color = knowledgeGraphColor(node);
      const selected = node.id === knowledgeGraphState.selectedId;
      ctx.beginPath();
      ctx.fillStyle = selected ? color : color + '64';
      ctx.strokeStyle = color;
      ctx.lineWidth = selected ? 2 : 1;
      ctx.shadowColor = selected ? color : 'transparent';
      ctx.shadowBlur = selected ? 15 : 0;
      ctx.arc(p.x, p.y, selected ? node.size + 4 : node.size, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.shadowBlur = 0;

      const shouldLabel = selected || knowledgeGraphState.labels === 'All' || (knowledgeGraphState.labels === 'Focus' && node.type === 'Cluster' && nodes.length < 150);
      if (shouldLabel) {
        ctx.fillStyle = 'rgba(215, 247, 255, 0.82)';
        ctx.font = '10px Fira Code, SFMono-Regular, Consolas, monospace';
        ctx.fillText(node.title, p.x + 10, p.y - 8);
      }
    });

    drawKnowledgeGraphMinimap(win, nodes);
    win.querySelector('[data-kg-visible]').textContent = nodes.length + ' visible';
    win.querySelector('[data-kg-links]').textContent = edges.length + ' links';
    win.querySelector('[data-kg-clusters]').textContent = new Set(nodes.map(node => node.cluster)).size + ' clusters';
    renderKnowledgeGraphInspector(win, nodes.find(node => node.id === knowledgeGraphState.selectedId) || nodes[0]);
  }

  function renderKnowledgeGraphWindow() {
    return `
      <div class="knowledge-graph-layout">
        <div class="kg-toolbar">
          <label class="kg-search">
            <input type="search" data-kg-search placeholder="find memories, sources, projects" value="${esc(knowledgeGraphState.query)}">
          </label>
          <button class="kg-toolbar-button${knowledgeGraphState.cluster ? ' active' : ''}" type="button" data-kg-cluster>Cluster mode</button>
          <button class="kg-toolbar-button" type="button" data-kg-labels>Labels: ${esc(knowledgeGraphState.labels)}</button>
          <button class="kg-toolbar-button" type="button" data-kg-layout>Layout: ${esc(knowledgeGraphState.layout)}</button>
        </div>
        <div class="kg-body">
          <aside class="kg-filter-rail">
            <section class="kg-filter-section">
              <div class="kg-filter-title"><span>Source</span><span data-kg-source-label>All</span></div>
              <div class="kg-facet-stack" data-kg-source-facets></div>
            </section>
            <section class="kg-filter-section">
              <div class="kg-filter-title"><span>Type</span><span data-kg-type-label>All</span></div>
              <div class="kg-facet-stack" data-kg-type-facets></div>
            </section>
            <section class="kg-filter-section">
              <div class="kg-filter-title"><span>Scale</span><span>LOD</span></div>
              <div class="kg-range-block">
                <label><span>Density</span><span data-kg-density-value>${knowledgeGraphState.density}%</span></label>
                <input type="range" min="18" max="100" value="${knowledgeGraphState.density}" data-kg-density>
                <label><span>Min trust</span><span data-kg-trust-value>${knowledgeGraphState.trust}%</span></label>
                <input type="range" min="0" max="95" value="${knowledgeGraphState.trust}" data-kg-trust>
              </div>
            </section>
          </aside>
          <section class="kg-canvas-area" aria-label="Knowledge graph canvas">
            <canvas class="kg-canvas" data-kg-canvas></canvas>
            <div class="kg-hud">
              <span class="kg-hud-chip" data-kg-visible>0 visible</span>
              <span class="kg-hud-chip" data-kg-links>0 links</span>
              <span class="kg-hud-chip" data-kg-clusters>0 clusters</span>
              <span class="kg-hud-chip">Canvas render</span>
            </div>
            <div class="kg-minimap"><canvas data-kg-minimap></canvas></div>
            <div class="kg-lod-note">Large graph mode: labels reduce first, distant nodes become clusters, and filters define the render budget.</div>
          </section>
          <aside class="kg-inspector" data-kg-inspector></aside>
        </div>
      </div>
    `;
  }

  function wireKnowledgeGraphWindow(win) {
    ensureKnowledgeGraphData();
    renderKnowledgeGraphFacets(win);

    win.querySelector('[data-kg-search]')?.addEventListener('input', event => {
      knowledgeGraphState.query = event.target.value.trim();
      requestAnimationFrame(() => drawKnowledgeGraph(win));
    });

    win.querySelector('[data-kg-density]')?.addEventListener('input', event => {
      knowledgeGraphState.density = Number(event.target.value);
      win.querySelector('[data-kg-density-value]').textContent = knowledgeGraphState.density + '%';
      drawKnowledgeGraph(win);
    });

    win.querySelector('[data-kg-trust]')?.addEventListener('input', event => {
      knowledgeGraphState.trust = Number(event.target.value);
      win.querySelector('[data-kg-trust-value]').textContent = knowledgeGraphState.trust + '%';
      drawKnowledgeGraph(win);
    });

    win.querySelector('.kg-filter-rail')?.addEventListener('click', event => {
      const source = event.target.closest('[data-kg-source]');
      const type = event.target.closest('[data-kg-type]');
      if (!source && !type) return;
      if (source) knowledgeGraphState.source = source.dataset.kgSource;
      if (type) knowledgeGraphState.type = type.dataset.kgType;
      renderKnowledgeGraphFacets(win);
      drawKnowledgeGraph(win);
    });

    win.querySelector('[data-kg-cluster]')?.addEventListener('click', event => {
      knowledgeGraphState.cluster = !knowledgeGraphState.cluster;
      event.currentTarget.classList.toggle('active', knowledgeGraphState.cluster);
      drawKnowledgeGraph(win);
    });

    win.querySelector('[data-kg-labels]')?.addEventListener('click', event => {
      knowledgeGraphState.labels = knowledgeGraphState.labels === 'Focus' ? 'All' : 'Focus';
      event.currentTarget.textContent = 'Labels: ' + knowledgeGraphState.labels;
      drawKnowledgeGraph(win);
    });

    win.querySelector('[data-kg-layout]')?.addEventListener('click', event => {
      const modes = ['Semantic', 'Source', 'Time'];
      knowledgeGraphState.layout = modes[(modes.indexOf(knowledgeGraphState.layout) + 1) % modes.length];
      event.currentTarget.textContent = 'Layout: ' + knowledgeGraphState.layout;
      drawKnowledgeGraph(win);
    });

    win.querySelector('[data-kg-canvas]')?.addEventListener('click', event => {
      const canvas = event.currentTarget;
      const rect = canvas.getBoundingClientRect();
      const nodes = visibleKnowledgeGraphNodes();
      let best = null;
      let bestDistance = Infinity;
      nodes.forEach(node => {
        const point = projectKnowledgeGraphNode(node, rect);
        const distance = Math.hypot(event.clientX - rect.left - point.x, event.clientY - rect.top - point.y);
        if (distance < bestDistance) {
          bestDistance = distance;
          best = node;
        }
      });
      if (best && bestDistance < 28) {
        knowledgeGraphState.selectedId = best.id;
        drawKnowledgeGraph(win);
      }
    });

    win.querySelector('[data-kg-inspector]')?.addEventListener('click', event => {
      const button = event.target.closest('[data-kg-action]');
      if (!button) return;
      if (button.dataset.kgAction === 'Attach') {
        const selected = knowledgeGraphNodes.find(node => node.id === knowledgeGraphState.selectedId);
        if (selected) addContextNodge('Memory', selected.title);
      }
      announceAction(button.dataset.kgAction);
    });

    if (window.ResizeObserver && !win._knowledgeGraphResizeObserver) {
      win._knowledgeGraphResizeObserver = new ResizeObserver(() => {
        requestAnimationFrame(() => drawKnowledgeGraph(win));
      });
      win._knowledgeGraphResizeObserver.observe(win.querySelector('.kg-canvas-area'));
    }
    requestAnimationFrame(() => drawKnowledgeGraph(win));
  }

  function memoryStatusColor(memory) {
    const colors = {
      attention: 'var(--red)',
      learned: 'var(--blue)',
      pinned: 'var(--amber)',
      confirmed: 'var(--green)',
      private: 'var(--teal)',
      forgotten: 'var(--muted)'
    };
    return colors[memory?.status] || 'var(--cyan)';
  }

  function memoryMatchesTab(memory) {
    if (memoryState.tab === 'All') return true;
    if (memoryState.tab === 'Attention') return memory.status === 'attention';
    if (memoryState.tab === 'Pinned') return memory.status === 'pinned';
    if (memoryState.tab === 'Learned') return memory.status === 'learned';
    if (memoryState.tab === 'History') return memory.status === 'forgotten';
    return true;
  }

  function visibleMemories() {
    const query = memoryState.query.toLowerCase();
    const weight = { attention: 0, private: 1, learned: 2, pinned: 3, confirmed: 4, forgotten: 5 };
    return memorySamples
      .filter(memory => {
        const typeOk = memoryState.type === 'All' || memory.type === memoryState.type;
        const sourceOk = memoryState.source === 'All' || memory.source === memoryState.source;
        const queryOk = !query || [memory.title, memory.text, memory.type, memory.source, memory.project].join(' ').toLowerCase().includes(query);
        return typeOk && sourceOk && memoryMatchesTab(memory) && queryOk;
      })
      .sort((a, b) => {
        if (memoryState.sortAttentionFirst) return (weight[a.status] ?? 9) - (weight[b.status] ?? 9);
        return b.confidence - a.confidence;
      });
  }

  function renderMemoryTabs() {
    const tabs = [
      ['All', memorySamples.length],
      ['Attention', memorySamples.filter(memory => memory.status === 'attention').length],
      ['Pinned', memorySamples.filter(memory => memory.status === 'pinned').length],
      ['Learned', memorySamples.filter(memory => memory.status === 'learned').length],
      ['History', memorySamples.filter(memory => memory.status === 'forgotten').length]
    ];
    return tabs.map(([tab, count]) => `
      <button class="memory-tab${memoryState.tab === tab ? ' active' : ''}" type="button" data-memory-tab="${esc(tab)}">
        <span>${tab === 'Attention' ? 'Needs attention' : esc(tab)}</span><span>${count}</span>
      </button>
    `).join('');
  }

  function renderMemoryFacet(items, active, key, colors = {}) {
    return ['All', ...items].map(item => {
      const count = item === 'All' ? memorySamples.length : memorySamples.filter(memory => memory[key] === item).length;
      const color = item === 'All' ? 'var(--cyan)' : (colors[item] || 'var(--teal)');
      return `
        <button class="memory-facet${active === item ? ' active' : ''}" style="--facet:${color}" type="button" data-memory-${key}="${esc(item)}">
          <strong>${esc(item)}</strong><span>${count}</span>
        </button>
      `;
    }).join('');
  }

  function renderMemoryRows(items) {
    if (!items.length) return '<div class="memory-empty">Nothing needs attention here.</div>';
    return items.map(memory => `
      <button class="memory-row${memoryState.detailOpen && memory.id === memoryState.selectedId ? ' active' : ''}" style="--memory:${memoryStatusColor(memory)}" type="button" data-memory-id="${esc(memory.id)}">
        <span class="memory-dot"></span>
        <span class="memory-copy">
          <span class="memory-title-line"><strong>${esc(memory.title)}</strong><span>${memory.status === 'attention' ? 'asks' : esc(memory.status)}</span></span>
          <span class="memory-text">${esc(memory.text)}</span>
          <span class="memory-meta"><span>${esc(memory.type)}</span><span>${esc(memory.project)}</span><span>${memory.confidence}%</span><span>${esc(memory.lastUsed)}</span></span>
        </span>
      </button>
    `).join('');
  }

  function renderMemoryDetail(memory) {
    if (!memory) {
      return '<div class="memory-detail-empty">Select a memory.</div>';
    }
    const attention = memory.status === 'attention';
    return `
      <div class="memory-detail-head">
        <div class="memory-detail-kicker"><span>${esc(memory.type)}</span><span>${memory.confidence}% confidence</span></div>
        <h2>${esc(memory.title)}</h2>
        <p>${esc(memory.text)}</p>
      </div>
      <div class="memory-detail-body">
        <div class="memory-props">
          <div><span>Status</span><strong>${attention ? 'Needs attention' : esc(memory.status)}</strong></div>
          <div><span>Project</span><strong>${esc(memory.project)}</strong></div>
          <div><span>Source</span><strong>${esc(memory.source)}</strong></div>
          <div><span>Last used</span><strong>${esc(memory.lastUsed)}</strong></div>
        </div>
        <section class="memory-evidence">
          <div class="memory-evidence-title">${attention ? 'Why ABC is asking' : 'Why ABC believes this'}</div>
          <p>${esc(memory.ask)}</p>
          ${memory.evidence.map(item => `<span>${esc(item)}</span>`).join('')}
        </section>
        <section class="memory-learning">
          <div class="memory-evidence-title">What ABC learns</div>
          <p>${esc(memory.learn)}</p>
        </section>
      </div>
      <div class="memory-detail-actions">
        <button type="button" data-memory-action="open">Open source</button>
        <button type="button" data-memory-action="scope">Scope</button>
        <button type="button" data-memory-action="pin">${memory.status === 'pinned' ? 'Unpin' : 'Pin'}</button>
        <button class="primary" type="button" data-memory-action="confirm">Confirm & teach</button>
        <button class="danger" type="button" data-memory-action="forget">Forget & learn</button>
      </div>
    `;
  }

  function renderMemoryWindow() {
    const items = visibleMemories();
    let selected = null;
    if (memoryState.detailOpen) {
      selected = items.find(memory => memory.id === memoryState.selectedId) || null;
      if (!selected) {
        memoryState.selectedId = null;
        memoryState.detailOpen = false;
      }
    }
    const types = [...new Set(memorySamples.map(memory => memory.type))];
    const sources = [...new Set(memorySamples.map(memory => memory.source))];
    const autopilotCount = memorySamples.filter(memory => ['confirmed', 'pinned', 'private'].includes(memory.status)).length;
    const learnedCount = memorySamples.filter(memory => memory.status === 'learned').length;
    const attentionCount = memorySamples.filter(memory => memory.status === 'attention').length;

    return `
      <div class="memory-window-layout">
        <aside class="memory-rail" aria-label="Memory filters">
          <section class="memory-autopilot">
            <div class="memory-autopilot-head"><span>Memory autopilot</span><span>local</span></div>
            <div class="memory-autopilot-ring" aria-label="${autopilotCount} memories handled automatically">${autopilotCount}</div>
            <div class="memory-autopilot-copy">ABC only asks when a memory is uncertain, risky, or needs your intent.</div>
            <div class="memory-autopilot-stats">
              <span><strong>${attentionCount}</strong> needs attention</span>
              <span><strong>${learnedCount}</strong> learned rules</span>
            </div>
          </section>
          <div class="memory-facet-section">
            <div class="memory-section-title"><span>Type</span><span>${esc(memoryState.type)}</span></div>
            <div class="memory-facet-stack">${renderMemoryFacet(types, memoryState.type, 'type', memoryTypeColors)}</div>
          </div>
          <div class="memory-facet-section">
            <div class="memory-section-title"><span>Source</span><span>${esc(memoryState.source)}</span></div>
            <div class="memory-facet-stack">${renderMemoryFacet(sources, memoryState.source, 'source')}</div>
          </div>
        </aside>
        <section class="memory-main" aria-label="Memory list">
          <div class="memory-toolbar">
            <label class="memory-search">
              <input type="search" value="${esc(memoryState.query)}" placeholder="find memory, source, project" data-memory-search>
            </label>
            <button class="memory-sort" type="button" data-memory-sort>${memoryState.sortAttentionFirst ? 'Attention first' : 'Confidence'}</button>
          </div>
          <div class="memory-tabs" aria-label="Memory views">${renderMemoryTabs()}</div>
          <div class="memory-list">${renderMemoryRows(items)}</div>
        </section>
        <aside class="memory-detail" aria-label="Memory detail">${renderMemoryDetail(selected)}</aside>
      </div>
    `;
  }

  function refreshMemoryWindow(win) {
    win.querySelector('.window-body').innerHTML = renderMemoryWindow();
    wireMemoryWindow(win);
  }

  function updateMemoryPanelState(win) {
    if (!win) return;
    win.classList.toggle('filters-collapsed', !memoryState.filtersOpen);
    win.classList.toggle('detail-collapsed', !memoryState.detailOpen);
    win.querySelectorAll('[data-memory-panel]').forEach(button => {
      const panel = button.dataset.memoryPanel;
      const active = panel === 'filters' ? memoryState.filtersOpen : memoryState.detailOpen;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
      button.title = (active ? 'Hide ' : 'Show ') + (panel === 'filters' ? 'filters' : 'details');
    });
  }

  function keepMemoryWindowInViewport(win) {
    if (!win || !memoryState.detailOpen || win.classList.contains('maximized')) return;
    requestAnimationFrame(() => {
      const rect = win.getBoundingClientRect();
      const margin = 18;
      if (rect.right <= window.innerWidth - margin) return;
      win.style.transform = 'none';
      win.style.left = Math.max(margin, window.innerWidth - margin - rect.width) + 'px';
      win.style.top = rect.top + 'px';
      win.style.width = rect.width + 'px';
      win.style.height = rect.height + 'px';
    });
  }

  function applyMemoryAction(memory, action) {
    if (!memory) return;
    if (action === 'confirm') {
      memory.status = 'learned';
      memory.confidence = Math.max(memory.confidence, 90);
      memory.lastUsed = 'now';
      buildState.textContent = 'V2 - Memory learned from confirmation';
      return;
    }
    if (action === 'forget') {
      memory.status = 'forgotten';
      memory.confidence = Math.max(42, memory.confidence - 22);
      memory.lastUsed = 'now';
      buildState.textContent = 'V2 - Memory learned from forget';
      return;
    }
    if (action === 'pin') {
      memory.status = memory.status === 'pinned' ? 'confirmed' : 'pinned';
      memory.lastUsed = 'now';
      buildState.textContent = 'V2 - Memory pin preference learned';
      return;
    }
    if (action === 'scope') {
      addContextNodge('Memory', memory.title);
      buildState.textContent = 'V2 - Memory scoped to chat';
      return;
    }
    buildState.textContent = 'V2 - Memory source focused';
  }

  function wireMemoryWindow(win) {
    updateMemoryPanelState(win);
    win.querySelectorAll('[data-memory-panel]').forEach(button => {
      if (button.dataset.memoryPanelReady) return;
      button.dataset.memoryPanelReady = 'true';
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        const panel = button.dataset.memoryPanel;
        if (panel === 'filters') memoryState.filtersOpen = !memoryState.filtersOpen;
        if (panel === 'detail') memoryState.detailOpen = !memoryState.detailOpen;
        updateMemoryPanelState(win);
        buildState.textContent = 'V2 - Memory ' + (panel === 'filters' ? 'filters' : 'details') + (button.classList.contains('active') ? ' shown' : ' hidden');
      });
    });
    win.querySelector('[data-memory-search]')?.addEventListener('input', event => {
      memoryState.query = event.target.value.trim();
      refreshMemoryWindow(win);
      win.querySelector('[data-memory-search]')?.focus();
    });
    win.querySelector('[data-memory-sort]')?.addEventListener('click', () => {
      memoryState.sortAttentionFirst = !memoryState.sortAttentionFirst;
      refreshMemoryWindow(win);
    });
    win.querySelectorAll('[data-memory-tab]').forEach(button => {
      button.addEventListener('click', () => {
        memoryState.tab = button.dataset.memoryTab || 'All';
        memoryState.selectedId = null;
        refreshMemoryWindow(win);
      });
    });
    win.querySelectorAll('[data-memory-type]').forEach(button => {
      button.addEventListener('click', () => {
        memoryState.type = button.dataset.memoryType || 'All';
        memoryState.selectedId = null;
        refreshMemoryWindow(win);
      });
    });
    win.querySelectorAll('[data-memory-source]').forEach(button => {
      button.addEventListener('click', () => {
        memoryState.source = button.dataset.memorySource || 'All';
        memoryState.selectedId = null;
        refreshMemoryWindow(win);
      });
    });
    win.querySelectorAll('[data-memory-id]').forEach(button => {
      button.addEventListener('click', () => {
        const selectedAgain = memoryState.detailOpen && memoryState.selectedId === button.dataset.memoryId;
        memoryState.selectedId = selectedAgain ? null : button.dataset.memoryId;
        memoryState.detailOpen = !selectedAgain;
        refreshMemoryWindow(win);
        keepMemoryWindowInViewport(win);
      });
    });
    win.querySelectorAll('[data-memory-action]').forEach(button => {
      button.addEventListener('click', () => {
        const memory = memorySamples.find(item => item.id === memoryState.selectedId);
        applyMemoryAction(memory, button.dataset.memoryAction);
        refreshMemoryWindow(win);
      });
    });
  }

  function openMemoryWindow() {
    let win = document.getElementById('memory-window');
    if (!win) {
      win = document.createElement('article');
      win.className = 'floating-window memory-window active';
      win.id = 'memory-window';
      win.dataset.window = '';
      win.dataset.windowId = 'memory';
      win.dataset.windowCloseMode = 'remove';
      win.setAttribute('aria-label', 'Memory');
      win.innerHTML = `
        <header class="window-head" data-drag-handle>
          <div class="window-subtitle">knowledge</div>
          <div class="window-title">Memory</div>
          <div class="window-actions" aria-label="Window controls">
            <button class="window-control" data-window-min title="Minimize" aria-label="Minimize">-</button>
            <button class="window-control" data-window-max title="Maximize" aria-label="Maximize">&#9633;</button>
            <button class="window-control" data-window-close title="Close" aria-label="Close">x</button>
          </div>
        </header>
        <button class="memory-filter-handle active" type="button" data-memory-panel="filters" aria-pressed="true" aria-label="Toggle filters" title="Hide filters">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M4 6h16"></path>
            <path d="M7 12h10"></path>
            <path d="M10 18h4"></path>
          </svg>
        </button>
        <div class="window-body">${renderMemoryWindow()}</div>
      `;
      workspace.appendChild(win);
      prepareFloatingWindow(win);
      wireMemoryWindow(win);
    } else {
      setWindowMinimized(win, false);
      refreshMemoryWindow(win);
    }
    activateWindow(win);
    buildState.textContent = 'V2 - Memory autopilot';
    closeToolwheel();
  }

  function openKnowledgeGraphWindow() {
    let win = document.getElementById('knowledge-graph-window');
    if (!win) {
      win = document.createElement('article');
      win.className = 'floating-window knowledge-graph-window active';
      win.id = 'knowledge-graph-window';
      win.dataset.window = '';
      win.dataset.windowId = 'knowledge-graph';
      win.dataset.windowCloseMode = 'remove';
      win.setAttribute('aria-label', 'Knowledge Graph');
      win.innerHTML = `
        <header class="window-head" data-drag-handle>
          <div class="window-subtitle">knowledge</div>
          <div class="window-title">Knowledge Graph</div>
          <div class="window-actions" aria-label="Window controls">
            <button class="window-control" data-window-min title="Minimize" aria-label="Minimize">-</button>
            <button class="window-control" data-window-max title="Maximize" aria-label="Maximize">&#9633;</button>
            <button class="window-control" data-window-close title="Close" aria-label="Close">x</button>
          </div>
        </header>
        <div class="window-body">${renderKnowledgeGraphWindow()}</div>
      `;
      workspace.appendChild(win);
      prepareFloatingWindow(win);
      wireKnowledgeGraphWindow(win);
    } else {
      setWindowMinimized(win, false);
      requestAnimationFrame(() => drawKnowledgeGraph(win));
    }
    activateWindow(win);
    buildState.textContent = 'V2 - Knowledge Graph';
    closeToolwheel();
  }

  function handleToolwheelAction(item) {
    const action = item.dataset.action;
    const type = item.dataset.actionType || 'Instant';
    if (!action) return;

    if (action === 'Todos' || action === 'New Task') {
      openTodosWindow();
      return;
    }

    if (action === 'Overview' || action === 'Open Project' || action === 'Project Atlas' || action === 'Memory Debug' || action === 'Frontend V2' || action === 'Local Models') {
      openProjectsOverview();
      return;
    }

    if (action === 'Graph View') {
      openKnowledgeGraphWindow();
      return;
    }

    if (action === 'Memory') {
      openMemoryWindow();
      return;
    }

    if (action === 'Skills') {
      openSkillsWindow();
      return;
    }

    if (['Model', 'Appearance', 'Shortcuts', 'Voice', 'Settings More'].includes(action)) {
      const queries = {
        Model: 'model',
        Appearance: 'appearance theme background',
        Shortcuts: 'shortcuts keyboard',
        Voice: 'voice speech STT TTS',
        'Settings More': ''
      };
      openSettingsWindow({ query: queries[action], advanced: action === 'Settings More' });
      return;
    }

    if (action === 'New Chat') {
      preparedChats++;
      createChatSpace();
      return;
    }

    if (type === 'Attach') {
      const kind = item.dataset.nodgeKind || 'Context';
      addContextNodge(kind, action);
      announceAction(kind + ' attached');
      closeToolwheel();
      return;
    }

    if (type === 'Open') {
      announceAction(action + ' window');
      closeToolwheel();
      return;
    }

    if (type === 'More') {
      announceAction(action);
      return;
    }

    announceAction(action);
    closeToolwheel();
  }

  function installResizeHandles(root = document) {
    const dirs = ['n', 'ne', 'e', 'se', 's', 'sw', 'w', 'nw'];
    root.querySelectorAll('[data-window]').forEach(win => {
      if (win.querySelector('.resize-handle')) return;
      dirs.forEach(dir => {
        const handle = document.createElement('span');
        handle.className = 'resize-handle';
        handle.dataset.resizeDir = dir;
        win.appendChild(handle);
      });
    });
  }

  function storeWindowBounds(win) {
    if (win._restoreBounds) return;
    const rect = win.getBoundingClientRect();
    win._restoreBounds = {
      left: rect.left + 'px',
      top: rect.top + 'px',
      width: rect.width + 'px',
      height: rect.height + 'px',
      transform: 'none'
    };
  }

  function restoreWindowBounds(win) {
    if (!win._restoreBounds) return;
    win.style.left = win._restoreBounds.left;
    win.style.top = win._restoreBounds.top;
    win.style.width = win._restoreBounds.width;
    win.style.height = win._restoreBounds.height;
    win.style.transform = win._restoreBounds.transform;
    win._restoreBounds = null;
  }

  function autosizePrompt(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.max(96, textarea.scrollHeight) + 'px';
    const composer = textarea.closest('.composer');
    if (composer) {
      composer.style.minHeight = textarea.style.height;
    }
  }

  function rectsOverlap(startA, endA, startB, endB) {
    return Math.max(0, Math.min(endA, endB) - Math.max(startA, startB));
  }

  function clampAxisPosition(value, size, viewportSize) {
    const margin = 14;
    return Math.max(margin, Math.min(viewportSize - size - margin, value));
  }

  function buildSnapTarget(target, lockedAxis) {
    const next = { ...target };
    if (lockedAxis === 'x') {
      next.top = clampAxisPosition(next.top, next.height, window.innerHeight);
    } else {
      next.left = clampAxisPosition(next.left, next.width, window.innerWidth);
    }
    return next;
  }

  function snapTargetFits(target) {
    const margin = 14;
    return target.left >= margin
      && target.top >= margin
      && target.left + target.width <= window.innerWidth - margin
      && target.top + target.height <= window.innerHeight - margin;
  }

  function snapPreviewElement() {
    let preview = document.getElementById('window-snap-preview');
    if (!preview) {
      preview = document.createElement('div');
      preview.id = 'window-snap-preview';
      preview.className = 'window-snap-preview';
      preview.innerHTML = '<span>SNAP</span>';
      stage.appendChild(preview);
    }
    return preview;
  }

  function hideSnapPreview() {
    const preview = document.getElementById('window-snap-preview');
    preview?.classList.remove('visible', 'vertical', 'horizontal');
  }

  function showSnapPreview(candidate) {
    const preview = snapPreviewElement();
    preview.classList.toggle('vertical', candidate.orientation === 'vertical');
    preview.classList.toggle('horizontal', candidate.orientation === 'horizontal');
    preview.style.left = candidate.preview.left + 'px';
    preview.style.top = candidate.preview.top + 'px';
    preview.style.width = candidate.preview.width + 'px';
    preview.style.height = candidate.preview.height + 'px';
    preview.classList.add('visible');
  }

  function findSnapCandidate(dragWin, rect) {
    const threshold = 44;
    const gap = 12;
    const minOverlap = 48;
    const alignThreshold = 72;
    const current = {
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height,
      right: rect.left + rect.width,
      bottom: rect.top + rect.height
    };
    let best = null;

    document.querySelectorAll('[data-window]').forEach(other => {
      if (other === dragWin || other.classList.contains('minimized') || other.classList.contains('maximized')) return;
      const otherRect = other.getBoundingClientRect();
      const verticalOverlap = rectsOverlap(current.top, current.bottom, otherRect.top, otherRect.bottom);
      const horizontalOverlap = rectsOverlap(current.left, current.right, otherRect.left, otherRect.right);

      const rightTarget = buildSnapTarget({
        left: otherRect.right + gap,
        top: Math.abs(current.top - otherRect.top) <= alignThreshold ? otherRect.top : current.top,
        width: current.width,
        height: current.height
      }, 'x');
      const leftTarget = buildSnapTarget({
        left: otherRect.left - gap - current.width,
        top: Math.abs(current.top - otherRect.top) <= alignThreshold ? otherRect.top : current.top,
        width: current.width,
        height: current.height
      }, 'x');
      const bottomTarget = buildSnapTarget({
        left: Math.abs(current.left - otherRect.left) <= alignThreshold ? otherRect.left : current.left,
        top: otherRect.bottom + gap,
        width: current.width,
        height: current.height
      }, 'y');
      const topTarget = buildSnapTarget({
        left: Math.abs(current.left - otherRect.left) <= alignThreshold ? otherRect.left : current.left,
        top: otherRect.top - gap - current.height,
        width: current.width,
        height: current.height
      }, 'y');

      const candidates = [
        {
          side: 'right',
          distance: Math.abs(current.left - (otherRect.right + gap)),
          valid: verticalOverlap >= minOverlap && snapTargetFits(rightTarget),
          target: rightTarget,
          preview: {
            left: otherRect.right + Math.floor(gap / 2),
            top: Math.max(current.top, otherRect.top),
            width: 2,
            height: Math.max(86, Math.min(current.bottom, otherRect.bottom) - Math.max(current.top, otherRect.top))
          },
          orientation: 'vertical'
        },
        {
          side: 'left',
          distance: Math.abs(current.right - (otherRect.left - gap)),
          valid: verticalOverlap >= minOverlap && snapTargetFits(leftTarget),
          target: leftTarget,
          preview: {
            left: otherRect.left - Math.floor(gap / 2),
            top: Math.max(current.top, otherRect.top),
            width: 2,
            height: Math.max(86, Math.min(current.bottom, otherRect.bottom) - Math.max(current.top, otherRect.top))
          },
          orientation: 'vertical'
        },
        {
          side: 'bottom',
          distance: Math.abs(current.top - (otherRect.bottom + gap)),
          valid: horizontalOverlap >= minOverlap && snapTargetFits(bottomTarget),
          target: bottomTarget,
          preview: {
            left: Math.max(current.left, otherRect.left),
            top: otherRect.bottom + Math.floor(gap / 2),
            width: Math.max(86, Math.min(current.right, otherRect.right) - Math.max(current.left, otherRect.left)),
            height: 2
          },
          orientation: 'horizontal'
        },
        {
          side: 'top',
          distance: Math.abs(current.bottom - (otherRect.top - gap)),
          valid: horizontalOverlap >= minOverlap && snapTargetFits(topTarget),
          target: topTarget,
          preview: {
            left: Math.max(current.left, otherRect.left),
            top: otherRect.top - Math.floor(gap / 2),
            width: Math.max(86, Math.min(current.right, otherRect.right) - Math.max(current.left, otherRect.left)),
            height: 2
          },
          orientation: 'horizontal'
        }
      ];

      candidates.forEach(candidate => {
        if (!candidate.valid || candidate.distance > threshold) return;
        if (!best || candidate.distance < best.distance) {
          best = candidate;
        }
      });
    });

    return best;
  }

  function applySnapCandidate(win, candidate) {
    if (!candidate) return;
    win.style.left = candidate.target.left + 'px';
    win.style.top = candidate.target.top + 'px';
    win.style.width = candidate.target.width + 'px';
    win.style.height = candidate.target.height + 'px';
    win.style.transform = 'none';
    buildState.textContent = 'V2 - Window snapped';
  }

  function setInboxOpen(open, reason = '') {
    if (!universalInbox) return;
    stage.classList.toggle('inbox-open', open);
    universalInbox.setAttribute('aria-hidden', String(!open));
    if (open && reason) buildState.textContent = 'V2 - Inbox: ' + reason;
  }

  function scheduleInboxClose() {
    clearTimeout(inboxCloseTimer);
    inboxCloseTimer = setTimeout(() => {
      if (!inboxHover && !inboxTriggerHover && inboxDragDepth <= 0) {
        setInboxOpen(false);
      }
    }, 420);
  }

  function installUniversalInbox() {
    if (!universalInbox || !universalInboxTrigger) return;

    universalInboxTrigger.addEventListener('mouseenter', () => {
      inboxTriggerHover = true;
      setInboxOpen(true, 'ready');
    });

    universalInboxTrigger.addEventListener('mouseleave', () => {
      inboxTriggerHover = false;
      scheduleInboxClose();
    });

    universalInbox.addEventListener('mouseenter', () => {
      inboxHover = true;
      setInboxOpen(true);
    });

    universalInbox.addEventListener('mouseleave', () => {
      inboxHover = false;
      scheduleInboxClose();
    });

    document.addEventListener('dragenter', event => {
      inboxDragDepth += 1;
      stage.classList.add('inbox-dragging');
      setInboxOpen(true, 'drop target');
      event.preventDefault();
    });

    document.addEventListener('dragover', event => {
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
    });

    document.addEventListener('dragleave', () => {
      inboxDragDepth = Math.max(0, inboxDragDepth - 1);
      if (inboxDragDepth === 0) {
        stage.classList.remove('inbox-dragging');
        scheduleInboxClose();
      }
    });

    document.addEventListener('drop', event => {
      event.preventDefault();
      const files = event.dataTransfer?.files?.length || 0;
      const itemCount = files || event.dataTransfer?.items?.length || 1;
      inboxDragDepth = 0;
      stage.classList.remove('inbox-dragging');
      setInboxOpen(true, itemCount + ' item' + (itemCount === 1 ? '' : 's') + ' added');
      setTimeout(scheduleInboxClose, 900);
    });
  }

  function prepareFloatingWindow(win, index = document.querySelectorAll('[data-window]').length) {
    ensureWindowId(win, index);
    installResizeHandles(win.parentElement || document);

    if (!win.dataset.windowPrepared) {
      win.addEventListener('pointerdown', event => {
        if (event.windowSelectionHandled) return;
        prepareWindowSelection(win, event);
      });
      win.dataset.windowPrepared = 'true';
    }

    win.querySelectorAll('[data-drag-handle]').forEach(handle => {
      if (handle.dataset.dragPrepared) return;
      handle.dataset.dragPrepared = 'true';
      handle.addEventListener('pointerdown', event => {
        if (event.target.closest('button, textarea, input, .chat-title, .chat-title-editor, .resize-handle')) return;
        const dragWin = handle.closest('[data-window]');
        if (!dragWin || dragWin.classList.contains('maximized')) return;
        event.windowSelectionHandled = true;
        prepareWindowSelection(dragWin, event);
        const rect = dragWin.getBoundingClientRect();
        const startX = event.clientX;
        const startY = event.clientY;
        const offsetX = startX - rect.left;
        const offsetY = startY - rect.top;
        const dragGroup = activeDragGroup(dragWin);
        const groupBounds = prepareGroupDrag(dragGroup);
        const isGroupDrag = dragGroup.length > 1;
        handle.setPointerCapture(event.pointerId);
        let snapCandidate = null;

        function move(e) {
          const dx = e.clientX - startX;
          const dy = e.clientY - startY;
          const nextRect = {
            left: e.clientX - offsetX,
            top: e.clientY - offsetY,
            width: rect.width,
            height: rect.height
          };
          groupBounds.forEach(item => {
            item.win.style.left = item.left + dx + 'px';
            item.win.style.top = item.top + dy + 'px';
          });
          snapCandidate = isGroupDrag ? null : findSnapCandidate(dragWin, nextRect);
          if (!isGroupDrag && snapCandidate) {
            showSnapPreview(snapCandidate);
          } else {
            hideSnapPreview();
          }
        }

        function up() {
          handle.releasePointerCapture(event.pointerId);
          document.removeEventListener('pointermove', move);
          document.removeEventListener('pointerup', up);
          if (isGroupDrag) {
            buildState.textContent = 'V2 - ' + dragGroup.length + ' windows moved';
          } else {
            applySnapCandidate(dragWin, snapCandidate);
          }
          hideSnapPreview();
        }

        document.addEventListener('pointermove', move);
        document.addEventListener('pointerup', up);
      });
    });
  }

  function installWindowInteractions() {
    document.querySelectorAll('[data-window]').forEach((win, index) => {
      prepareFloatingWindow(win, index);
    });

    document.addEventListener('pointerdown', event => {
      const handle = event.target.closest('.resize-handle');
      if (!handle) return;
      const win = handle.closest('[data-window]');
      if (!win || win.classList.contains('maximized')) return;
      event.preventDefault();
      activateWindow(win);
      const dir = handle.dataset.resizeDir;
      const rect = win.getBoundingClientRect();
      const startX = event.clientX;
      const startY = event.clientY;
      const minW = 340;
      const minH = 260;
      win.style.transform = 'none';
      win.style.left = rect.left + 'px';
      win.style.top = rect.top + 'px';
      win.style.width = rect.width + 'px';
      win.style.height = rect.height + 'px';
      handle.setPointerCapture(event.pointerId);

      function move(e) {
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        let left = rect.left;
        let top = rect.top;
        let width = rect.width;
        let height = rect.height;

        if (dir.includes('e')) width = Math.max(minW, rect.width + dx);
        if (dir.includes('s')) height = Math.max(minH, rect.height + dy);
        if (dir.includes('w')) {
          width = Math.max(minW, rect.width - dx);
          left = rect.right - width;
        }
        if (dir.includes('n')) {
          height = Math.max(minH, rect.height - dy);
          top = rect.bottom - height;
        }

        win.style.left = left + 'px';
        win.style.top = top + 'px';
        win.style.width = width + 'px';
        win.style.height = height + 'px';
      }

      function up() {
        handle.releasePointerCapture(event.pointerId);
        document.removeEventListener('pointermove', move);
        document.removeEventListener('pointerup', up);
      }

      document.addEventListener('pointermove', move);
      document.addEventListener('pointerup', up);
    });

    document.addEventListener('click', event => {
      const control = event.target.closest('[data-window-min], [data-window-max], [data-window-close]');
      if (!control) return;
      event.preventDefault();
      event.stopPropagation();
      const win = control.closest('[data-window]');
      if (!win) return;
      activateWindow(win);

      if (control.matches('[data-window-min]')) {
        setWindowMinimized(win, !win.classList.contains('minimized'));
        return;
      }

      if (control.matches('[data-window-close]')) {
        if (win.dataset.windowCloseMode === 'remove') {
          removeDockBubble(win);
          win.remove();
          renderOpenDocumentContextSuggestions();
          buildState.textContent = 'V2 - Window closed';
          return;
        }
        setWindowMinimized(win, true);
        return;
      }

      if (control.matches('[data-window-max]')) {
        const shouldMaximize = !win.classList.contains('maximized');
        if (shouldMaximize) {
          storeWindowBounds(win);
          win.classList.add('maximized');
          control.textContent = '\u25A2';
          control.title = 'Restore';
          control.setAttribute('aria-label', 'Restore');
        } else {
          win.classList.remove('maximized');
          restoreWindowBounds(win);
          control.textContent = '\u25A1';
          control.title = 'Maximize';
          control.setAttribute('aria-label', 'Maximize');
        }
      }
    });

    document.querySelectorAll('.prompt-input').forEach(textarea => {
      autosizePrompt(textarea);
      textarea.addEventListener('input', () => {
        autosizePrompt(textarea);
        saveActiveChatSpace();
      });
    });

    chatTitle.addEventListener('dblclick', event => {
      event.preventDefault();
      event.stopPropagation();
      startTitleRename();
    });

    chatTitleEditor?.addEventListener('keydown', event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        commitTitleRename();
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        cancelTitleRename();
      }
    });

    chatTitleEditor?.addEventListener('blur', commitTitleRename);

    chatHistoryButton?.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      setChatHistoryOpen(chatHistoryPanel?.hidden);
    });

    chatHistorySearch?.addEventListener('input', renderChatHistory);

    chatHistoryList?.addEventListener('click', event => {
      const item = event.target.closest('[data-chat-history-index]');
      if (!item) return;
      const index = Number(item.dataset.chatHistoryIndex);
      if (index >= 0) openChatSpace(index);
    });

    composerModelButton?.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      if (composerModelChooser?.hidden) return;
      composerModelChooser.classList.toggle('open');
      composerModelButton.setAttribute('aria-expanded', String(composerModelChooser.classList.contains('open')));
    });

    composerModelMenu?.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      const row = event.target.closest('[data-model-name]');
      if (row) selectChatModel(row);
    });

    coreWindow.querySelector('.send-btn')?.addEventListener('click', event => {
      if (sendPromptDemo()) event.preventDefault();
    });

    document.addEventListener('click', event => {
      const suggestion = event.target.closest('[data-context-suggestion="true"]');
      if (suggestion) {
        event.preventDefault();
        event.stopPropagation();
        pinDocumentContextFromSuggestion(suggestion);
        return;
      }

      const path = documentPathFromTarget(event.target);
      if (!path) return;
      event.preventDefault();
      event.stopPropagation();
      openDocumentViewer(path);
    });

    document.addEventListener('keydown', event => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      const suggestion = event.target.closest('[data-context-suggestion="true"]');
      if (suggestion) {
        event.preventDefault();
        event.stopPropagation();
        pinDocumentContextFromSuggestion(suggestion);
        return;
      }

      const path = documentPathFromTarget(event.target);
      if (!path) return;
      event.preventDefault();
      openDocumentViewer(path);
    });

    messages.addEventListener('click', event => {
      const toggle = event.target.closest('[data-work-run-toggle]');
      if (!toggle) return;
      const run = toggle.closest('[data-work-run]');
      run?.classList.toggle('collapsed');
      syncWorkRuns();
      saveActiveChatSpace();
    });

    messages.addEventListener('keydown', event => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      const toggle = event.target.closest('[data-work-run-toggle]');
      if (!toggle) return;
      event.preventDefault();
      const run = toggle.closest('[data-work-run]');
      run?.classList.toggle('collapsed');
      syncWorkRuns();
      saveActiveChatSpace();
    });

    document.getElementById('window-dock')?.addEventListener('click', event => {
      const bubble = event.target.closest('[data-restore-window]');
      if (!bubble) return;
      const win = document.querySelector('[data-window-id="' + bubble.dataset.restoreWindow + '"]');
      if (!win) return;
      setWindowMinimized(win, false);
    });

    document.querySelectorAll('.composer-menu-button').forEach(button => {
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        const shell = button.closest('.composer-shell');
        setComposerMenuOpen(shell, !shell.classList.contains('menu-open'));
      });
    });

    document.querySelectorAll('.composer-tool').forEach(tool => {
      tool.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        const action = tool.dataset.composerTool || tool.textContent.trim();
        if (tool.dataset.composerType === 'Attach') {
          const kind = tool.dataset.nodgeKind || 'Context';
          addContextNodge(kind, action);
          announceAction(kind + ' attached');
          setComposerMenuOpen(tool.closest('.composer-shell'), false);
          return;
        }
        if (action === 'Skills') {
          openSkillsWindow();
          setComposerMenuOpen(tool.closest('.composer-shell'), false);
          return;
        }
        announceAction(action);
        setComposerMenuOpen(tool.closest('.composer-shell'), false);
      });
    });

    document.querySelectorAll('[data-mode-option]').forEach(button => {
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        setWorkspaceMode(button.dataset.modeOption);
      });
    });

    document.querySelectorAll('[data-voice-toggle]').forEach(button => {
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        setVoiceInputMode(button.getAttribute('aria-pressed') !== 'true');
      });
    });

    document.querySelectorAll('[data-repo-commit-request]').forEach(button => {
      button.addEventListener('click', event => {
        event.preventDefault();
        event.stopPropagation();
        buildState.textContent = 'V2 - Clean commit request prepared';
      });
    });

    privacyToggle?.addEventListener('change', () => {
      const active = privacyToggle.checked;
      stage.classList.toggle('privacy-on', active);
      buildState.textContent = active ? 'V2 - GDPR mode active' : 'V2 - Main chat window';
    });

    contextNodges.addEventListener('click', event => {
      const suggestion = event.target.closest('[data-context-suggestion="true"]');
      if (suggestion) {
        event.preventDefault();
        pinDocumentContextFromSuggestion(suggestion);
        return;
      }

      const remove = event.target.closest('.context-nodge-remove');
      if (!remove) return;
      event.preventDefault();
      const nodge = remove.closest('.context-nodge');
      nodge?.remove();
      const current = chatSpaces[activeChatIndex];
      if (current) current.contexts = serializeContextNodges();
      renderOpenDocumentContextSuggestions();
      contextNodges.hidden = !contextNodges.children.length;
    });

    document.addEventListener('click', event => {
      document.querySelectorAll('.composer-shell.menu-open').forEach(shell => {
        if (!shell.contains(event.target)) {
          setComposerMenuOpen(shell, false);
        }
      });
      if (composerModelChooser && !composerModelChooser.contains(event.target)) {
        composerModelChooser.classList.remove('open');
        composerModelButton?.setAttribute('aria-expanded', 'false');
      }
    });

    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      document.querySelectorAll('.composer-shell.menu-open').forEach(shell => {
        setComposerMenuOpen(shell, false);
      });
    });

    leftNodge.addEventListener('click', () => moveChatSpace(-1));
    rightNodge.addEventListener('click', () => moveChatSpace(1));

    chatCarousel.addEventListener('click', event => {
      const tile = event.target.closest('[data-chat-index]');
      if (!tile) return;
      openChatSpace(Number(tile.dataset.chatIndex));
    });

    chatCarousel.addEventListener('wheel', event => {
      if (chatSpaces.length <= 1) return;
      event.preventDefault();
      moveChatSpace(event.deltaY > 0 ? 1 : -1);
    }, { passive: false });
  }

  stage.addEventListener('contextmenu', event => {
    event.preventDefault();
    toolwheel.classList.contains('open') ? closeToolwheel() : openToolwheel(event);
  });

  toolwheel.addEventListener('mousemove', event => {
    if (!toolwheel.classList.contains('open')) return;
    if (toolwheel.classList.contains('suppress-core-menu')) {
      if (!wheelOpenPointer) {
        toolwheel.classList.remove('suppress-core-menu');
      } else {
        const distance = Math.hypot(event.clientX - wheelOpenPointer.x, event.clientY - wheelOpenPointer.y);
        if (distance > 14) toolwheel.classList.remove('suppress-core-menu');
      }
    }
    updateWheelArrow(event);
  });

  wheelCore.addEventListener('mouseenter', () => {
    if (toolwheel.classList.contains('suppress-core-menu')) return;
    clearTimeout(coreMenuTimer);
    toolwheel.classList.add('core-new-open');
    coreNewTree.classList.add('is-open');
  });

  wheelCore.addEventListener('mouseleave', () => {
    coreMenuTimer = setTimeout(() => {
      if (!coreNewTree.matches(':hover')) {
        toolwheel.classList.remove('core-new-open');
        coreNewTree.classList.remove('is-open');
      }
    }, 220);
  });

  coreNewTree.addEventListener('mouseenter', () => {
    clearTimeout(coreMenuTimer);
    coreNewTree.classList.add('is-open');
  });

  coreNewTree.addEventListener('mouseleave', () => {
    toolwheel.classList.remove('core-new-open');
    coreNewTree.classList.remove('is-open');
  });

  wheelCore.addEventListener('click', event => {
    event.preventDefault();
    preparedChats++;
    createChatSpace();
  });

  toolwheel.querySelectorAll('.wheel-node').forEach((node, index) => {
    node.addEventListener('mouseenter', () => {
      toolwheel.classList.remove('suppress-core-menu');
      toolwheel.classList.remove('core-new-open');
      coreNewTree.classList.remove('is-open');
      focusWheelNode(index);
    });

    node.addEventListener('focus', () => {
      toolwheel.classList.remove('core-new-open');
      coreNewTree.classList.remove('is-open');
      focusWheelNode(index);
    });

    node.addEventListener('click', () => {
      if (node.dataset.node === 'Projects') {
        openProjectsOverview();
        return;
      }
      if (node.dataset.node === 'Knowledge') {
        openKnowledgeGraphWindow();
        return;
      }
      if (node.dataset.node === 'Settings') {
        openSettingsWindow({ query: '', advanced: false });
        return;
      }
      announceAction(node.dataset.node);
    });

    node.addEventListener('keydown', event => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      if (node.dataset.node === 'Projects') {
        openProjectsOverview();
        return;
      }
      if (node.dataset.node === 'Knowledge') {
        openKnowledgeGraphWindow();
        return;
      }
      if (node.dataset.node === 'Settings') {
        openSettingsWindow({ query: '', advanced: false });
        return;
      }
      announceAction(node.dataset.node);
    });
  });

  toolwheel.querySelectorAll('[data-action]').forEach(item => {
    item.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      handleToolwheelAction(item);
    });
  });

  document.addEventListener('keydown', event => {
    if (event.ctrlKey && event.key === 'Tab') {
      event.preventDefault();
      moveChatSpace(event.shiftKey ? -1 : 1);
      return;
    }

    if (event.altKey && event.code === 'Space') {
      event.preventDefault();
      toolwheel.classList.contains('open') ? closeToolwheel() : openToolwheel();
      return;
    }

    if (!toolwheel.classList.contains('open')) return;

    if (event.key === 'Escape') {
      closeToolwheel();
      return;
    }

    if (/^[1-4]$/.test(event.key)) {
      focusWheelNode(Number(event.key) - 1);
      return;
    }

    if (event.key === 'Enter' && focusedNode) {
      if (focusedNode.dataset.node === 'Projects') {
        openProjectsOverview();
        return;
      }
      if (focusedNode.dataset.node === 'Knowledge') {
        openKnowledgeGraphWindow();
        return;
      }
      if (focusedNode.dataset.node === 'Settings') {
        openSettingsWindow({ query: '', advanced: false });
        return;
      }
      announceAction(focusedNode.dataset.node);
      return;
    }

    if (event.key === 'ArrowRight' || event.key === 'Tab') {
      event.preventDefault();
      const nodes = Array.from(toolwheel.querySelectorAll('.wheel-node'));
      focusWheelNode((nodes.indexOf(focusedNode) + 1) % nodes.length);
    }

    if (event.key === 'ArrowLeft') {
      const nodes = Array.from(toolwheel.querySelectorAll('.wheel-node'));
      focusWheelNode((nodes.indexOf(focusedNode) - 1 + nodes.length) % nodes.length);
    }
  });

  resizeBrushCanvas();
  stage.classList.add('wheel-' + wheelOverlayMode + '-mode');
  buildState.textContent = 'V2 - Background: ' + (backgroundMode === 'network' ? 'Network' : 'Grid');
  installWindowInteractions();
  installUniversalInbox();
  linkifyDocumentReferences(messages);
  ensureAiMetaHotspots();
  setWorkspaceMode('agent');
  updateModelUi();
  renderChatHistory();
  updateChatNodges();
  updateChatCarousel();
  window.addEventListener('resize', resizeBrushCanvas);
  requestAnimationFrame(animateGridBrush);
})();
