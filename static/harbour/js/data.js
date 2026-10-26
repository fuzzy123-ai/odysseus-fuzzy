/**
 * Harbour One | Domain Data Models (Projects, Tasks, Devblog & Decisions)
 * Sourced from RedQueen and Harbour Architecture Roadmaps.
 */

window.HARBOUR_DATA = {
  projects: [
    { id: "all", name: "Alle Projekte" },
    { id: "scrapper", name: "Scrapper" },
    { id: "queen-system1", name: "Queen System 1" },
    { id: "harbour-core", name: "Harbour One" },
    { id: "infra", name: "Infrastruktur & Server" }
  ],

  tasks: [
    // Planned
    {
      id: "TSK-01",
      projectId: "queen-system1",
      projectLabel: "Queen System 1",
      status: "planned",
      title: "LLM-Entscheidungskopplung & System1-Kern",
      summary: "Entkoppelte Schnittstelle für System1-Reaktionsmuster ohne blockierenden Chat-Monolith.",
      owner: "KI-Architektur",
      milestone: "M1: Konzept",
      evidence: "07_Produktion/97_PlanSystem1KI.md"
    },
    {
      id: "TSK-02",
      projectId: "harbour-core",
      projectLabel: "Harbour One",
      status: "planned",
      title: "Universal Transfer-API Spezifikation",
      summary: "Idempotente REST-Übergabe (POST /api/transfers) für 1-Klick-Übernahme aus Odysseus.",
      owner: "Platform Lead",
      milestone: "HPS-08",
      evidence: "docs/plans/harbour-product-separation-roadmap.json"
    },

    // In Progress
    {
      id: "TSK-03",
      projectId: "harbour-core",
      projectLabel: "Harbour One",
      status: "in_progress",
      title: "Autarkes Frontend & Magazin-Chronik",
      summary: "100% autarke CSS-Architektur ohne CDN-Abhängigkeit; Kanban- und Devblog-Viewports.",
      owner: "Frontend Craft",
      milestone: "Phase 1",
      evidence: "static/harbour/css/style.css"
    },
    {
      id: "TSK-04",
      projectId: "scrapper",
      projectLabel: "Scrapper",
      status: "in_progress",
      title: "Stufe 4: Verfeinerung der Gameplay-Loop",
      summary: "Balancing der Interaktionen und Optimierung der State-Maschinen-Übergänge.",
      owner: "Game Dev",
      milestone: "Stufe 4",
      evidence: "docs/scrapper/gameplay-loop.md"
    },

    // Verified
    {
      id: "TSK-05",
      projectId: "infra",
      projectLabel: "Infrastruktur & Server",
      status: "verified",
      title: "Cloudflare Ingress & Subdomain-Routing",
      summary: "CNAME harbour.katzarow.de auf Port 7050 im Debian-Tunnel aktiv und per HTTPS geprüft.",
      owner: "Ops Lead",
      milestone: "Deploy M1",
      evidence: "systemd: cloudflared-tunnel.service (ACTIVE)"
    },
    {
      id: "TSK-06",
      projectId: "scrapper",
      projectLabel: "Scrapper",
      status: "verified",
      title: "Stufen 1–3: Asset- & Modell-Chronik",
      summary: "Vollständige Erfassung der ersten drei Modellierungsstufen mit belegter Historie.",
      owner: "Asset Lead",
      milestone: "Stufe 3",
      evidence: "07_Produktion/Devblog/content/scrapper-history.json"
    },

    // Accepted
    {
      id: "TSK-07",
      projectId: "harbour-core",
      projectLabel: "Harbour One",
      status: "accepted",
      title: "Produkt-Trennung: 2-Welten-Architektur",
      summary: "Vollständige Nutzerfreigabe für zwei getrennte Repositories (Odysseus vs. Harbour One).",
      owner: "User / Nikola",
      milestone: "Architektur-Gate",
      evidence: "docs/plans/harbour-product-separation-inventory.md"
    },
    {
      id: "TSK-08",
      projectId: "infra",
      projectLabel: "Infrastruktur & Server",
      status: "accepted",
      title: "Rootless Podman Containerharbour",
      summary: "Isolierter User-Service harbour-podman.service läuft stabil auf Debian 192.168.178.122:7050.",
      owner: "User / Nikola",
      milestone: "Server Live",
      evidence: "harbour_one_1 (Up 2 hours, 0 restarts)"
    }
  ],

  devblog: [
    {
      id: "art-scrapper",
      projectId: "scrapper",
      projectLabel: "Scrapper",
      date: "26. Oktober 2026",
      readTime: "4 Min. Lesezeit",
      title: "Scrapper: Die 5 Evolutionsstufen der Modellarchitektur",
      badge: "Projektchronik",
      summary: "Ein detaillierter Rückblick auf die Entwicklung des Scrappers: Von der ersten Konzeptzeichnung über Rigging und Physik-Kollisionen bis zur aktuellen Stufe.",
      content: `
        <p>Das Projekt <strong>Scrapper</strong> durchläuft fünf klar definierte Reifegrade, um Gameplay-Tiefe mit robuster technischer Umsetzbarkeit zu verbinden. Hier ist der aktuelle Status der einzelnen Stufen:</p>
        
        <div class="article-visual-block">
          <div class="visual-block-header">Übersicht der 5 Stufen</div>
          <table class="evidence-table">
            <thead>
              <tr>
                <th>Stufe</th>
                <th>Bezeichnung</th>
                <th>Status</th>
                <th>Prüfbeleg</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>Stufe 1</td>
                <td>Geometrischer Prototyp & Bounding</td>
                <td><span class="card-tag tag-accepted">Abgenommen</span></td>
                <td>SRC-SCRAP-01</td>
              </tr>
              <tr>
                <td>Stufe 2</td>
                <td>Rigging & Gelenkkinematik</td>
                <td><span class="card-tag tag-accepted">Abgenommen</span></td>
                <td>SRC-SCRAP-02</td>
              </tr>
              <tr>
                <td>Stufe 3</td>
                <td>Materialien, Textur-PBR & Shader</td>
                <td><span class="card-tag tag-verified">Verifiziert</span></td>
                <td>SRC-SCRAP-03</td>
              </tr>
              <tr>
                <td>Stufe 4</td>
                <td>Gameplay-Loop & Interaktionsfeedback</td>
                <td><span class="card-tag tag-progress">In Arbeit</span></td>
                <td>ACT-SCRAP-04</td>
              </tr>
              <tr>
                <td>Stufe 5</td>
                <td>Audio-Integration & Polish</td>
                <td><span class="card-tag tag-planned">Geplant</span></td>
                <td>PLN-SCRAP-05</td>
              </tr>
            </tbody>
          </table>
        </div>

        <p>Besonderes Augenmerk lag in Stufe 3 auf der Performance unter Last: Alle Shader-Instanzen laufen mit minimalem Drawcall-Overhead und sind für stabile Frameraten auf Zielhardware optimiert.</p>
      `
    },
    {
      id: "art-system1",
      projectId: "queen-system1",
      projectLabel: "Queen System 1",
      date: "26. Oktober 2026",
      readTime: "3 Min. Lesezeit",
      title: "Queen System 1 KI: Entkopplung der Reaktionsmuster",
      badge: "Architektur",
      summary: "Warum reflexive KI-Entscheidungen getrennt von tiefen Sprachmodellen laufen müssen und wie das System-1-Muster aufgebaut ist.",
      content: `
        <p>In <code>07_Produktion/97_PlanSystem1KI.md</code> wurde der Grundstein für ein reaktionsschnelles System 1 gelegt. Anstatt für jede Interaktion ein schwerfälliges LLM im vollen Kontextfenster abzufragen, trennt die Architektur strikt zwischen zwei Ebenen:</p>
        
        <ul>
          <li><strong>System 1 (Reflexiv & Determinisch):</strong> Schnelle Vorhersagen, Cache-Lookup, Zustandsautomaten und lokale Regelsätze mit Antwortzeiten &lt; 50ms.</li>
          <li><strong>System 2 (Reflektiv & Generativ):</strong> Tiefgreifende Kontextanalysen, Planung und Dokumentationserstellung über dedizierte Modelle.</li>
        </ul>

        <div class="article-visual-block">
          <div class="visual-block-header">Architektur-Kernelement</div>
          <p style="font-size: 13px; color: var(--text-muted);">Das System 1 bleibt bis zur endgültigen Schnittstellendefinition entkoppelt und wird nicht vorzeitig in den Chat-Monolithen gezwungen.</p>
        </div>
      `
    },
    {
      id: "art-harbour-split",
      projectId: "harbour-core",
      projectLabel: "Harbour One",
      date: "26. Oktober 2026",
      readTime: "5 Min. Lesezeit",
      title: "Die 2-Welten-Architektur: Odysseus und Harbour One trennen",
      badge: "Meilenstein",
      summary: "Vollständige Entkopplung: Warum Alltag/Inbox und Planung/Dokumentation als getrennte Repositories ohne Altschulden neu aufgesetzt werden.",
      content: `
        <p>Am 26. Oktober 2026 wurde die fundamentale Weichenstellung für das gesamte Ökosystem getroffen:</p>
        
        <p>Der bisherige Monolith wird nicht als schwerer Altschuldenträger fortgeführt. Stattdessen entstehen zwei eigenständige, saubere Repositories:</p>
        
        <ul>
          <li><strong>Odysseus:</strong> Der persönliche Lebens- und Kommunikations-Hub (Universal Inbox für Telegram-Voice/Text, Nextcloud, Kalender und Assistenten-Chat).</li>
          <li><strong>Harbour One:</strong> Das Planungs- und Dokumentations-Cockpit (Kanban, Meilensteine, Devblog-Chronik und Entscheidungs-Boards).</li>
        </ul>

        <p>Die beiden Welten kommunizieren später über eine klare, asynchrone REST-Transfer-API (<code>POST /api/transfers</code>), wodurch beide Systeme völlig unabhängig voneinander gewartet und betrieben werden können.</p>
      `
    }
  ],

  
  roadmaps: [
    {
      id: "RMP-HPS",
      title: "Harbour One: Produkt-Trennung von Odysseus",
      projectId: "harbour-core",
      projectLabel: "Harbour One",
      progress: 50,
      totalSlices: 8,
      completedSlices: 4,
      slices: [
        { id: "HPS-01", name: "Produktgrenzen & Verträge", status: "accepted", dependsOn: [], desc: "Zwei Produkte und genau ein Writer je Datenbestand.", owner: "Architektur" },
        { id: "HPS-02", name: "Getrennte Startprofile", status: "verified", dependsOn: ["HPS-01"], desc: "Autarke Kompositionen für Plan/Dok und Inbox.", owner: "Platform Lead" },
        { id: "HPS-03", name: "Plan/Dok-Kern & MCP", status: "in_progress", dependsOn: ["HPS-02"], desc: "Dauerhafte Speicherung von Roadmaps & Slices.", owner: "Backend Lead" },
        { id: "HPS-04", name: "Devblog & Kanban-UI", status: "in_progress", dependsOn: ["HPS-03"], desc: "Bebilderte Chronik und Human-First Frontend.", owner: "Frontend Craft" },
        { id: "HPS-05", name: "Dokumentations-Agent", status: "planned", dependsOn: ["HPS-04"], desc: "Automatisierte Erstellung von Journalvorschlägen.", owner: "KI-Agent" },
        { id: "HPS-06", name: "Universal Inbox Kern", status: "planned", dependsOn: ["HPS-02"], desc: "Telegram- und Nextcloud-Connectors entkoppelt.", owner: "Inbox Lead" },
        { id: "HPS-07", name: "Austauschbare Prüfmodelle", status: "planned", dependsOn: ["HPS-06"], desc: "LLM-Vorprüfung für Dokumente und Memos.", owner: "KI-Architektur" },
        { id: "HPS-08", name: "1-Klick Transfer-API", status: "planned", dependsOn: ["HPS-05", "HPS-07"], desc: "Idempotente Übergabe von Odysseus an Harbour.", owner: "Platform Lead" }
      ]
    },
    {
      id: "RMP-SCRAP",
      title: "Scrapper: Die 5 Evolutionsstufen",
      projectId: "scrapper",
      projectLabel: "Scrapper",
      progress: 60,
      totalSlices: 5,
      completedSlices: 3,
      slices: [
        { id: "SCRAP-01", name: "Stufe 1: Geometrischer Prototyp", status: "accepted", dependsOn: [], desc: "Prototyp & Bounding Boxes.", owner: "Asset Lead" },
        { id: "SCRAP-02", name: "Stufe 2: Rigging & Kinematik", status: "accepted", dependsOn: ["SCRAP-01"], desc: "Gelenke und Bewegungshierarchien.", owner: "Animation Lead" },
        { id: "SCRAP-03", name: "Stufe 3: PBR-Materialien & Shader", status: "verified", dependsOn: ["SCRAP-02"], desc: "Performance-optimierte Shader.", owner: "Shader Dev" },
        { id: "SCRAP-04", name: "Stufe 4: Gameplay-Loop", status: "in_progress", dependsOn: ["SCRAP-03"], desc: "Interaktionsfeedback und Balancing.", owner: "Game Dev" },
        { id: "SCRAP-05", name: "Stufe 5: Audio & Polish", status: "planned", dependsOn: ["SCRAP-04"], desc: "Sounddesign und finale Abnahme.", owner: "Audio Lead" }
      ]
    },
    {
      id: "RMP-SYS1",
      title: "Queen System 1 KI: Entkopplung",
      projectId: "queen-system1",
      projectLabel: "Queen System 1",
      progress: 33,
      totalSlices: 3,
      completedSlices: 1,
      slices: [
        { id: "SYS1-01", name: "Schnittstellendefinition (<50ms)", status: "accepted", dependsOn: [], desc: "Entkoppelte Inferenz ohne Chat-Monolith.", owner: "KI-Architektur" },
        { id: "SYS1-02", name: "Zustandsautomat & Cache-Lookup", status: "in_progress", dependsOn: ["SYS1-01"], desc: "Reflexive Regelsätze und Vorhersagen.", owner: "Core Dev" },
        { id: "SYS1-03", name: "Live-Kopplung mit System 2", status: "planned", dependsOn: ["SYS1-02"], desc: "Asynchroner Handoff an generative Modelle.", owner: "KI-Architektur" }
      ]
    }
  ],

  decisions: [
    {
      id: "ADR-001",
      title: "Strikte Trennung in 2 GitHub-Repositories",
      status: "accepted",
      statusLabel: "Angenommen",
      date: "2026-10-26",
      description: "Odysseus (Alltag & Inbox) und Harbour One (Planung & Doku) werden als getrennte Repositories geführt. Keine gemeinsamen Datenbank-Schreibzugriffe.",
      impact: "Maximale Stabilität, saubere Codebases, kein Altschulden-Transfer."
    },
    {
      id: "ADR-002",
      title: "100% Autarkes Frontend ohne externe CDNs",
      status: "accepted",
      statusLabel: "Angenommen",
      date: "2026-10-26",
      description: "Alle Styles und Skripte werden lokal ausgeliefert. Ausfallschutz gegen fehlschlagende CDN-Requests und strikte Browser-Blocker.",
      impact: "Verlässliches Rendern auf allen Geräten und Verbindungen."
    },
    {
      id: "ADR-003",
      title: "Asynchrone Transfer-API für Inbox-Triage",
      status: "open",
      statusLabel: "In Vorbereitung",
      date: "2026-10-26",
      description: "Spezifikation des Transfer-Payloads (ID, Herkunft, Audio-/Dateireferenz, Quelltranskript) für die 1-Klick-Übergabe von Odysseus an Harbour.",
      impact: "Ermöglicht nahtlose Übergabe von Sprachnotizen direkt in die Projektplanung."
    }
  ]
};
