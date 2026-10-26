
(function() {
  'use strict';

  // Global Tab Switcher (Direct DOM manipulation)
  window.switchHarbourTab = function(tabId) {
    // 1. Update Buttons
    const buttons = document.querySelectorAll('.nav-tab-btn');
    buttons.forEach(btn => {
      const match = btn.getAttribute('data-tab') === tabId;
      if (match) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });

    // 2. Update View Sections
    const views = ['kanban', 'devblog', 'decisions'];
    views.forEach(v => {
      const el = document.getElementById('view-' + v);
      if (el) {
        if (v === tabId) {
          el.style.display = 'block';
          el.classList.add('active');
        } else {
          el.style.display = 'none';
          el.classList.remove('active');
        }
      }
    });
  };

  // Global Modal Handlers
  window.openHarbourModal = function(taskId) {
    const data = window.HARBOUR_DATA;
    if (!data || !data.tasks) return;
    const task = data.tasks.find(t => t.id === taskId);
    if (!task) return;

    const overlay = document.getElementById('modal-overlay');
    const title = document.getElementById('modal-title');
    const body = document.getElementById('modal-body');
    if (!overlay || !title || !body) return;

    title.textContent = task.id + ': ' + task.title;
    body.innerHTML = `
      <div style="margin-bottom: 16px;">
        <span class="card-tag tag-${task.status}">${escapeHtml(task.projectLabel)}</span>
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
    overlay.classList.add('active');
  };

  window.closeHarbourModal = function() {
    const overlay = document.getElementById('modal-overlay');
    if (overlay) overlay.classList.remove('active');
  };

  // Keyboard Shortcuts (1, 2, 3, Esc)
  window.addEventListener('keydown', function(e) {
    const tag = (document.activeElement && document.activeElement.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;

    if (e.key === '1' || e.code === 'Digit1' || e.code === 'Numpad1') {
      window.switchHarbourTab('kanban');
    } else if (e.key === '2' || e.code === 'Digit2' || e.code === 'Numpad2') {
      window.switchHarbourTab('devblog');
    } else if (e.key === '3' || e.code === 'Digit3' || e.code === 'Numpad3') {
      window.switchHarbourTab('decisions');
    } else if (e.key === 'Escape') {
      window.closeHarbourModal();
    }
  });

  // Project Filtering
  window.filterHarbourProject = function(projectId) {
    const cards = document.querySelectorAll('.kanban-card');
    cards.forEach(c => {
      const p = c.getAttribute('data-project');
      if (projectId === 'all' || p === projectId) {
        c.style.display = 'block';
      } else {
        c.style.display = 'none';
      }
    });

    const articles = document.querySelectorAll('.article-card');
    articles.forEach(a => {
      const p = a.getAttribute('data-project');
      if (projectId === 'all' || p === projectId) {
        a.style.display = 'block';
      } else {
        a.style.display = 'none';
      }
    });
  };

  // Helper
  function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/[&<>'"]/g, 
      tag => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag] || tag)
    );
  }

  // Setup click listeners on load
  function setup() {
    // Project select change
    const sel = document.getElementById('project-select');
    if (sel) {
      sel.addEventListener('change', function(e) {
        window.filterHarbourProject(e.target.value);
      });
    }

    // Modal close button
    const closeBtn = document.getElementById('modal-close-btn');
    if (closeBtn) {
      closeBtn.addEventListener('click', window.closeHarbourModal);
    }
    const overlay = document.getElementById('modal-overlay');
    if (overlay) {
      overlay.addEventListener('click', function(e) {
        if (e.target === overlay) window.closeHarbourModal();
      });
    }

    // Timeline buttons in devblog
    document.querySelectorAll('.timeline-item-btn').forEach(btn => {
      btn.addEventListener('click', function() {
        const id = btn.getAttribute('data-article-id');
        const el = document.getElementById(id);
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'start' });
          document.querySelectorAll('.timeline-item-btn').forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
        }
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', setup);
  } else {
    setup();
  }
})();
