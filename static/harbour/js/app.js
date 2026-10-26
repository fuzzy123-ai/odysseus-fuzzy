/**
 * Harbour One | Application Controller & View Router
 * High-craft, modular, human-first.
 */

(function() {
  'use strict';

  // Application State
  const state = {
    currentTab: 'kanban', // 'kanban' | 'devblog' | 'decisions'
    selectedProject: 'all',
    searchQuery: '',
    activeModal: null
  };

  // DOM Elements
  const elements = {
    tabButtons: document.querySelectorAll('.nav-tab-btn'),
    viewSections: document.querySelectorAll('.view-section'),
    projectSelect: document.getElementById('project-select'),
    kanbanGrid: document.getElementById('kanban-grid'),
    devblogFeed: document.getElementById('devblog-feed'),
    devblogTimeline: document.getElementById('devblog-timeline'),
    decisionsGrid: document.getElementById('decisions-grid'),
    modalOverlay: document.getElementById('modal-overlay'),
    modalTitle: document.getElementById('modal-title'),
    modalBody: document.getElementById('modal-body'),
    modalCloseBtn: document.getElementById('modal-close-btn')
  };

  // Initialize
  function init() {
    bindEvents();
    renderAll();
  }

  // Event Listeners
  function bindEvents() {
    // Navigation Tabs
    elements.tabButtons.forEach(btn => {
      btn.addEventListener('click', () => {
        const tab = btn.dataset.tab;
        switchTab(tab);
      });
    });

    // Project Select Filter
    if (elements.projectSelect) {
      elements.projectSelect.addEventListener('change', (e) => {
        state.selectedProject = e.target.value;
        renderAll();
      });
    }

    // Modal Close
    if (elements.modalCloseBtn) {
      elements.modalCloseBtn.addEventListener('click', closeModal);
    }
    if (elements.modalOverlay) {
      elements.modalOverlay.addEventListener('click', (e) => {
        if (e.target === elements.modalOverlay) closeModal();
      });
    }

    // Keyboard Shortcuts
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeModal();
      } else if (!e.metaKey && !e.ctrlKey && document.activeElement.tagName !== 'INPUT') {
        if (e.key === '1') switchTab('kanban');
        if (e.key === '2') switchTab('devblog');
        if (e.key === '3') switchTab('decisions');
      }
    });
  }

  // Switch View Tab
  function switchTab(tabId) {
    state.currentTab = tabId;

    // Update Tab Buttons
    elements.tabButtons.forEach(btn => {
      btn.classList.toggle('active', btn.dataset.tab === tabId);
    });

    // Update View Sections
    elements.viewSections.forEach(sec => {
      sec.classList.toggle('active', sec.id === `view-${tabId}`);
    });
  }

  // Render Everything
  function renderAll() {
    renderKanban();
    renderDevblog();
    renderDecisions();
  }

  // Render Kanban Board
  function renderKanban() {
    if (!elements.kanbanGrid) return;

    const data = window.HARBOUR_DATA;
    const filteredTasks = data.tasks.filter(t => {
      return state.selectedProject === 'all' || t.projectId === state.selectedProject;
    });

    const columns = [
      { id: 'planned', title: 'Geplant', tagClass: 'tag-planned', badge: 'Geplant' },
      { id: 'in_progress', title: 'In Arbeit', tagClass: 'tag-progress', badge: 'Aktiv' },
      { id: 'verified', title: 'Verifiziert', tagClass: 'tag-verified', badge: 'Geprüft' },
      { id: 'accepted', title: 'Abgenommen', tagClass: 'tag-accepted', badge: 'Done' }
    ];

    let html = '';

    columns.forEach(col => {
      const colTasks = filteredTasks.filter(t => t.status === col.id);
      
      html += `
        <div class="kanban-column">
          <div class="column-header">
            <div class="column-title">
              <span>${col.title}</span>
            </div>
            <span class="column-badge">${colTasks.length}</span>
          </div>
          <div class="card-list">
      `;

      if (colTasks.length === 0) {
        html += `<div style="padding: 24px 0; text-align: center; color: var(--text-dim); font-size: 12px;">Keine Aufgaben</div>`;
      } else {
        colTasks.forEach(task => {
          html += `
            <div class="kanban-card" data-task-id="${task.id}">
              <span class="card-tag ${col.tagClass}">${task.projectLabel}</span>
              <div class="card-title">${escapeHtml(task.title)}</div>
              <div class="card-summary">${escapeHtml(task.summary)}</div>
              <div class="card-footer">
                <span>${escapeHtml(task.milestone)}</span>
                <span>${escapeHtml(task.owner)}</span>
              </div>
            </div>
          `;
        });
      }

      html += `
          </div>
        </div>
      `;
    });

    elements.kanbanGrid.innerHTML = html;

    // Attach card click handlers
    elements.kanbanGrid.querySelectorAll('.kanban-card').forEach(card => {
      card.addEventListener('click', () => {
        const taskId = card.dataset.taskId;
        const task = data.tasks.find(t => t.id === taskId);
        if (task) openTaskModal(task);
      });
    });
  }

  // Render Devblog
  function renderDevblog() {
    if (!elements.devblogFeed || !elements.devblogTimeline) return;

    const data = window.HARBOUR_DATA;
    const filteredArticles = data.devblog.filter(a => {
      return state.selectedProject === 'all' || a.projectId === state.selectedProject;
    });

    // Render Timeline Sidebar
    let timelineHtml = '';
    filteredArticles.forEach((art, idx) => {
      timelineHtml += `
        <button class="timeline-item-btn ${idx === 0 ? 'active' : ''}" data-article-id="${art.id}">
          <div>${escapeHtml(art.title)}</div>
          <div style="font-size: 11px; color: var(--text-dim);">${art.date}</div>
        </button>
      `;
    });
    elements.devblogTimeline.innerHTML = timelineHtml;

    // Render Articles Feed
    let feedHtml = '';
    filteredArticles.forEach(art => {
      feedHtml += `
        <article class="article-card" id="${art.id}">
          <header class="article-header">
            <div class="article-meta">
              <span class="article-badge">${art.badge}</span>
              <span>•</span>
              <span>${art.projectLabel}</span>
              <span>•</span>
              <span>${art.date}</span>
              <span>•</span>
              <span>${art.readTime}</span>
            </div>
            <h2 class="article-title">${escapeHtml(art.title)}</h2>
            <p style="color: var(--text-muted); font-size: 14px;">${escapeHtml(art.summary)}</p>
          </header>
          <div class="article-body">
            ${art.content}
          </div>
        </article>
      `;
    });

    elements.devblogFeed.innerHTML = feedHtml;

    // Attach timeline jump click
    elements.devblogTimeline.querySelectorAll('.timeline-item-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        const targetId = btn.dataset.articleId;
        const el = document.getElementById(targetId);
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'start' });
          elements.devblogTimeline.querySelectorAll('.timeline-item-btn').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
        }
      });
    });
  }

  // Render Decisions
  function renderDecisions() {
    if (!elements.decisionsGrid) return;

    const data = window.HARBOUR_DATA;
    let html = '';

    data.decisions.forEach(dec => {
      html += `
        <div class="decision-card">
          <div class="decision-header">
            <span class="decision-id">${dec.id}</span>
            <span class="decision-status ${dec.status}">${dec.statusLabel}</span>
          </div>
          <div class="decision-title">${escapeHtml(dec.title)}</div>
          <div class="decision-desc">${escapeHtml(dec.description)}</div>
          <div class="decision-footer">
            <strong>Auswirkung:</strong> ${escapeHtml(dec.impact)}
          </div>
        </div>
      `;
    });

    elements.decisionsGrid.innerHTML = html;
  }

  // Task Modal Drawer
  function openTaskModal(task) {
    if (!elements.modalOverlay) return;

    elements.modalTitle.textContent = `${task.id}: ${task.title}`;
    elements.modalBody.innerHTML = `
      <div style="margin-bottom: 16px;">
        <span class="card-tag tag-${task.status}">${task.projectLabel}</span>
        <span style="font-size: 12px; color: var(--text-muted); margin-left: 8px;">Meilenstein: <strong>${escapeHtml(task.milestone)}</strong></span>
      </div>
      <p style="font-size: 14px; color: var(--text-main); margin-bottom: 20px;">${escapeHtml(task.summary)}</p>
      
      <div class="article-visual-block">
        <div class="visual-block-header">Prüfnachweis & Referenz</div>
        <div style="font-family: var(--font-mono); font-size: 12px; color: var(--accent-primary);">${escapeHtml(task.evidence)}</div>
      </div>

      <div style="margin-top: 24px; padding-top: 16px; border-top: 1px solid var(--border-subtle); display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: var(--text-muted);">
        <span>Zuständigkeit: <strong>${escapeHtml(task.owner)}</strong></span>
        <span>Status-Dimension: <strong>${task.status.toUpperCase()}</strong></span>
      </div>
    `;

    elements.modalOverlay.classList.add('active');
  }

  function closeModal() {
    if (elements.modalOverlay) {
      elements.modalOverlay.classList.remove('active');
    }
  }

  // Helper
  function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/[&<>'"]/g, 
      tag => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag] || tag)
    );
  }

  // Bootstrap
  document.addEventListener('DOMContentLoaded', init);
})();
