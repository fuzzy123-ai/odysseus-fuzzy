// Shared renderer for compact agent tool-run summaries.

function directToolNodes(threadWrap) {
  return Array.from(threadWrap?.children || []).filter(node => node.classList?.contains('agent-thread-node'));
}

function cleanToolName(name) {
  return String(name || 'tool')
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function formatToolList(names) {
  const counts = new Map();
  names.forEach(name => {
    const key = cleanToolName(name);
    counts.set(key, (counts.get(key) || 0) + 1);
  });
  const parts = Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 3)
    .map(([name, count]) => count > 1 ? `${name} x${count}` : name);
  const hidden = counts.size - parts.length;
  if (hidden > 0) parts.push(`+${hidden} more`);
  return parts.join(', ');
}

function formatDuration(ms) {
  if (!Number.isFinite(ms) || ms <= 0) return '';
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const mins = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60).toString().padStart(2, '0');
  return `${mins}m ${rest}s`;
}

function collectDiffStats(nodes) {
  let added = 0;
  let removed = 0;
  let files = 0;
  nodes.forEach(node => {
    const diff = node.querySelector('.agent-tool-diff');
    if (!diff) return;
    files += 1;
    const addText = diff.querySelector('.diff-stat-add')?.textContent || '';
    const delText = diff.querySelector('.diff-stat-del')?.textContent || '';
    const add = Number((addText.match(/\d+/) || [0])[0]);
    const del = Number((delText.match(/\d+/) || [0])[0]);
    added += Number.isFinite(add) ? add : 0;
    removed += Number.isFinite(del) ? del : 0;
  });
  return { files, added, removed };
}

function collectDuration(nodes) {
  const finishedDurations = nodes
    .map(node => Number(node.dataset.durationMs || 0))
    .filter(ms => Number.isFinite(ms) && ms > 0);
  if (finishedDurations.length) {
    return finishedDurations.reduce((sum, ms) => sum + ms, 0);
  }

  const starts = nodes
    .map(node => Number(node.dataset.startedAt || 0))
    .filter(ms => Number.isFinite(ms) && ms > 0);
  if (!starts.length) return 0;
  return Date.now() - Math.min(...starts);
}

function getNodeToolName(node) {
  return node.dataset.toolName || node.querySelector('.agent-thread-tool')?.textContent || 'tool';
}

function ensureSummary(threadWrap) {
  let summary = threadWrap.querySelector(':scope > .agent-thread-summary');
  if (summary) return summary;

  summary = document.createElement('button');
  summary.type = 'button';
  summary.className = 'agent-thread-summary';
  summary.dataset.agentToolSummary = 'true';
  summary.setAttribute('aria-label', 'Toggle agent tool details');
  summary.innerHTML = [
    '<span class="agent-thread-summary-dot" aria-hidden="true"></span>',
    '<span class="agent-thread-summary-main">',
    '<strong data-agent-tool-summary-title></strong>',
    '<span data-agent-tool-summary-detail></span>',
    '</span>',
    '<span class="agent-thread-summary-meta" data-agent-tool-summary-meta></span>',
  ].join('');
  summary.addEventListener('click', () => {
    const nodes = directToolNodes(threadWrap);
    const shouldOpen = nodes.some(node => !node.classList.contains('open'));
    nodes.forEach(node => node.classList.toggle('open', shouldOpen));
  });
  threadWrap.insertBefore(summary, threadWrap.firstChild);
  return summary;
}

export function updateAgentToolSummary(threadWrap) {
  const nodes = directToolNodes(threadWrap);
  if (!threadWrap || !nodes.length) return null;

  const summary = ensureSummary(threadWrap);
  const running = nodes.some(node => node.classList.contains('running'));
  const failed = nodes.some(node => node.classList.contains('error'));
  const count = nodes.length;
  const toolNames = nodes.map(getNodeToolName);
  const diff = collectDiffStats(nodes);
  const duration = collectDuration(nodes);
  const durationText = formatDuration(duration);
  const status = running ? 'running' : failed ? 'failed' : 'done';
  const title = running
    ? `Using ${count} tool${count === 1 ? '' : 's'}`
    : `${count} tool${count === 1 ? '' : 's'} ${status}`;
  const detailParts = [formatToolList(toolNames)].filter(Boolean);
  if (diff.files) {
    detailParts.push(`${diff.files} edit${diff.files === 1 ? '' : 's'} +${diff.added} -${diff.removed}`);
  }
  const metaParts = [];
  if (durationText) metaParts.push(durationText);
  if (failed) metaParts.push('needs attention');
  else if (!running) metaParts.push('ok');
  else metaParts.push('live');

  summary.dataset.state = status;
  summary.querySelector('[data-agent-tool-summary-title]').textContent = title;
  summary.querySelector('[data-agent-tool-summary-detail]').textContent = detailParts.join(' | ');
  const meta = summary.querySelector('[data-agent-tool-summary-meta]');
  meta.replaceChildren(...metaParts.map(part => {
    const span = document.createElement('span');
    span.textContent = part;
    return span;
  }));
  return summary;
}

export function stampToolNodeStart(node, toolName) {
  if (!node) return;
  node.dataset.toolName = cleanToolName(toolName);
  node.dataset.startedAt = String(Date.now());
  node.dataset.toolState = 'running';
}

export function stampToolNodeFinish(node, toolName, ok) {
  if (!node) return;
  const now = Date.now();
  const started = Number(node.dataset.startedAt || 0);
  node.dataset.toolName = cleanToolName(toolName || node.dataset.toolName);
  node.dataset.finishedAt = String(now);
  if (Number.isFinite(started) && started > 0) {
    node.dataset.durationMs = String(Math.max(0, now - started));
  }
  node.dataset.toolState = ok ? 'done' : 'failed';
}
