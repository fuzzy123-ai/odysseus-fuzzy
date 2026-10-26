
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
    const views = ['kanban', 'roadmaps', 'devblog', 'decisions'];
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
    if (!data) return;
    
    // Check in tasks
    let task = (data.tasks || []).find(t => t.id === taskId);
    
    // Check in roadmaps slices
    if (!task && data.roadmaps) {
      for (const r of data.roadmaps) {
        const s = (r.slices || []).find(sl => sl.id === taskId);
        if (s) {
          task = {
            id: s.id,
            title: s.name,
            projectLabel: r.projectLabel,
            status: s.status,
            milestone: r.title,
            summary: s.desc,
            evidence: s.dependsOn && s.dependsOn.length ? 'Abhängig von: ' + s.dependsOn.join(', ') : 'Keine Vorbedingungen (Start-Slice)',
            owner: s.owner
          };
          break;
        }
      }
    }

    if (!task) return;

    const overlay = document.getElementById('modal-overlay');
    const title = document.getElementById('modal-title');
    const body = document.getElementById('modal-body');
    if (!overlay || !title || !body) return;

    title.textContent = task.id + ': ' + task.title;
    body.innerHTML = `
      <div style="margin-bottom: 16px;">
        <span class="card-tag tag-${task.status}">${escapeHtml(task.projectLabel)}</span>
        <span style="font-size: 12px; color: var(--text-muted); margin-left: 8px;">Meilenstein / Roadmap: <strong>${escapeHtml(task.milestone)}</strong></span>
      </div>
      <p style="font-size: 14px; color: var(--text-main); margin-bottom: 20px;">${escapeHtml(task.summary)}</p>
      
      <div class="article-visual-block">
        <div class="visual-block-header">Prüfnachweis / Abhängigkeit</div>
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

  // Keyboard Shortcuts (1=Kanban, 2=Roadmaps, 3=Devblog, 4=Decisions, Esc=Modal)
  window.addEventListener('keydown', function(e) {
    const tag = (document.activeElement && document.activeElement.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;

    if (e.key === '1' || e.code === 'Digit1' || e.code === 'Numpad1') {
      window.switchHarbourTab('kanban');
    } else if (e.key === '2' || e.code === 'Digit2' || e.code === 'Numpad2') {
      window.switchHarbourTab('roadmaps');
    } else if (e.key === '3' || e.code === 'Digit3' || e.code === 'Numpad3') {
      window.switchHarbourTab('devblog');
    } else if (e.key === '4' || e.code === 'Digit4' || e.code === 'Numpad4') {
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
      c.style.display = (projectId === 'all' || p === projectId) ? 'block' : 'none';
    });

    const articles = document.querySelectorAll('.article-card');
    articles.forEach(a => {
      const p = a.getAttribute('data-project');
      a.style.display = (projectId === 'all' || p === projectId) ? 'block' : 'none';
    });

    const summaries = document.querySelectorAll('.project-summary-card');
    summaries.forEach(s => {
      const p = s.getAttribute('data-project');
      s.style.display = (projectId === 'all' || p === projectId) ? 'block' : 'none';
    });

    const rows = document.querySelectorAll('.slice-row');
    rows.forEach(r => {
      const p = r.getAttribute('data-project');
      r.style.display = (projectId === 'all' || p === projectId) ? 'table-row' : 'none';
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
    const sel = document.getElementById('project-select');
    if (sel) {
      sel.addEventListener('change', function(e) {
        window.filterHarbourProject(e.target.value);
      });
    }

    const closeBtn = document.getElementById('modal-close-btn');
    if (closeBtn) closeBtn.addEventListener('click', window.closeHarbourModal);
    
    const overlay = document.getElementById('modal-overlay');
    if (overlay) {
      overlay.addEventListener('click', function(e) {
        if (e.target === overlay) window.closeHarbourModal();
      });
    }

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
