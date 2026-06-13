# Odysseus als plattformübergreifendes Langzeitgedächtnis — Gesamtplan

> **Stand:** 2025-01-20  
> **Status:** Approved, P0 in Progress  
> **Ziel:** Obsidian-Vault über MCP für KI-Assistenten (Codex, Claude, Antigravity) als  
> persistentes, semantisch durchsuchbares Langzeitgedächtnis verfügbar machen —  
> mit intelligentem Context-Provider, Link-Tracking und mehrstufigem Sicherheitsnetz.

---

## Architektur-Übersicht

```
Antigravity / Codex / Claude (MCP-Client)
    │ Streamable HTTP (SSE)
    ▼
http://localhost:7000/mcp/vault
    │ AuthMiddleware (Bearer ody_...)
    │ Scope-Gate: vault:read / vault:write / vault:delete
    ▼
src/mcp_servers/vault_server.py  ←── ruft direkt ──→ plugins/obsidian/backend/*
    │                                                  (erweiterte Endpunkte)
    ▼
Vault (data/obsidian_vaults/{owner}/)
```

---

## Phase 0 — Obsidian-Endpunkte vervollständigen

### P0: Semantische Suche

- [ ] `GET /api/plugins/obsidian/search-semantic?q=...&top_k=10`  
  Neuer Endpunkt, nutzt `src/embeddings.py` (FastEmbed), alle `.md`-Notizen mit  
  Embeddings indizieren, Cosine-Similarity-Suche. Cache in `.obsidian/embeddings.json`,  
  invalidiert bei `mtime`-Änderung.

- [ ] `_build_vault_embedding_index(vault_dir)` in `vault_model.py`  
  Nutzt `get_embedding_client()`, speichert `{path: vector}` als JSON oder ChromaDB-Collection  
  `odysseus_vault_{owner}`. Inkrementelles Update bei `write_file`/`delete_file`.

- [ ] Semantischer Fallback in `context_provider.py`  
  Wenn Keyword-Suche <5 Treffer → Embedding-Suche nachschalten, Ergebnisse mergen.

### P0: Frontmatter CRUD

- [ ] `GET /api/plugins/obsidian/file/frontmatter?path=...`  
  Isoliertes Lesen des YAML-Headers ohne Body. Nutzt existierendes `parse_frontmatter()`.

- [ ] `PUT /api/plugins/obsidian/file/frontmatter`  
  Frontmatter mergen (Body unverändert). `vault_history.record_action()` für Undo.

### P1: Batch-Operationen

- [ ] `POST /api/plugins/obsidian/batch`  
  Atomare Multi-File-Operation: `{operations: [{action, path, content?, frontmatter?}, ...]}`.  
  Transaktion via Tempfiles + `os.replace()`. Supportet `"dry_run": true` für Vorschau  
  ohne Änderung.

### P1: Zeitraum-Queries

- [ ] `GET /api/plugins/obsidian/files/recent?since=...&until=...`  
  Dateien nach `mtime` filtern. Nutzt `os.stat().st_mtime`.

- [ ] `GET /api/plugins/obsidian/files/changed?since=...`  
  Nur geänderte (nicht neu erstellte) Dateien.

### P2: Link-Vorschläge

- [ ] `GET /api/plugins/obsidian/suggest-links?path=...&top_k=5`  
  Kombiniert Shared-Tags + Link-Distanz + Embedding-Ähnlichkeit. Nutzt existierenden  
  `build_vault_index()`-Output und `vault_graph.py`.

### P3: Tag-Vorschläge

- [ ] `GET /api/plugins/obsidian/tags/suggest?prefix=...`  
  Existierende Tags aus `build_vault_index()` filtern. Trivial, da Tags bereits indiziert.

---

## Phase 1 — Context-Provider: Zusammenhänge & Link-Tracking

> **Problem:** Aktueller `context_provider.py` ist eine flache Keyword-Suche —  
> null Link-Tracking, keine Backlink-Auflösung, kein Folder-Context.

- [ ] `_enrich_with_backlinks(note, vault_dir)`  
  Nutzt `vault_graph.py` Graph-Daten, findet alle Notizen die per `[[Wiki-Link]]`  
  oder Markdown-Link auf den Treffer zeigen. Fügt sie mit reduziertem Score als  
  Kontext-Snippets hinzu.

- [ ] `_enrich_with_shared_tags(note, all_notes)`  
  Notizen mit ≥2 gemeinsamen Tags (exkl. `project/*`, `status/*`, `type/*`).  
  Max 3 pro Treffer.

- [ ] `_enrich_with_folder_context(note, all_notes)`  
  Andere Notizen im selben Ordner mit Score-Bonus (Ordner sind implizite Cluster).

- [ ] Token-genaue Budget-Füllung  
  `budget * 4`-Heuristik durch `estimate_tokens()` aus `src/model_context.py` ersetzen.  
  Snippets token-genau füllen statt char-basiert.

- [ ] Zusammenfassung bei vielen Treffern  
  `{total_hits, top_tags, folder_distribution}` ans Kontext-Ende, damit die KI weiß  
  was noch ungesehen ist und gezielt nachfragen kann.

- [ ] `vault_search` mit `max_results` + `tag_filter`-Parametern  
  KI-gesteuerte Eingrenzung statt blinder Pagination. Die KI entscheidet aktiv,  
  was sie vertiefen will.

---

## Phase 2 — MCP-Vault-Server

- [ ] `src/mcp_servers/vault_server.py` — Neuer MCP-Server  
  Streamable-HTTP-Transport nach dem Muster von `memory_server.py`. 14 Tools:

  | Tool | Hint | Nutzt Endpunkt |
  |---|---|---|
  | `vault_tree` | read | `GET /files` |
  | `vault_read` | read | `GET /file` + `GET /file/frontmatter` |
  | `vault_search` | read | `GET /search` + `GET /search-semantic` |
  | `vault_semantic` | read | `GET /search-semantic` |
  | `vault_tags` | read | `GET /tags` + `GET /tags/suggest` |
  | `vault_graph` | read | `GET /graph` |
  | `vault_related` | read | `GET /suggest-links` |
  | `vault_recent` | read | `GET /files/recent` |
  | `vault_history` | read | `GET /history` |
  | `vault_status` | read | `GET /status` |
  | `vault_write` | destructive | `POST /file` + `PUT /file/frontmatter` |
  | `vault_batch` | destructive | `POST /batch` |
  | `vault_delete` | destructive | `DELETE /file` |
  | `vault_undo` | destructive | `POST /undo` |

- [ ] Mount in `app.py`  
  `vault_mcp_app` als ASGI-App unter `/mcp/vault` mounten. Auth-Middleware greift  
  automatisch über existierendes Scope-System.

- [ ] Scope-Gating  
  `vault:read`, `vault:write`, `vault:delete` in `routes/api_token_routes.py` registrieren.  
  `vault:write` impliziert `vault:read`. Bei gesperrtem Vault: `423 Locked`.

- [ ] Audit-Trail  
  Nach jedem schreibenden MCP-Call: `vault_history.record_action(..., tool="mcp_vault_...")`.

- [ ] Registrierung in `builtin_mcp.py`  
  `"vault"` zu `_BUILTIN_SERVERS` hinzufügen. Startet als Subprozess wie `memory_server.py`.

- [ ] Rate-Limiter für MCP-Write-Calls  
  Pro API-Token max 10 destructive Calls/Minute. In-Memory-Dict  
  `{token_id: deque([timestamp, ...])}`. `429 + Retry-After` bei Überschreitung.

---

## Phase 3 — Sechsstufiges Sicherheitsnetz für Destructive-Ops

> **Prinzip:** Die KI soll praktisch unfähig sein, echten Schaden anzurichten —  
> selbst bei kompromittiertem Token.

| Ebene | Mechanismus | Implementierung |
|---|---|---|
| **1** | Scope-Gate | Token muss explizit `vault:write` oder `vault:delete` haben |
| **2** | Soft-Delete | `delete_file` → `.trash/{iso_date}/{rel_path}` statt `os.unlink()` |
| **3** | Auto-Snapshot | `write_file` kopiert alte Version nach `.obsidian/snapshots/` |
| **4** | Rate-Limiting | 10 destructive ops/Minute/Token |
| **5** | Batch-Dry-Run | `vault_batch` mit `"dry_run": true` → Diff-Report ohne Änderung |
| **6** | Undo-Garantie | ≥50 Versionen/Datei, 30 Tage Retention |

- [ ] **Ebene 1: Scope-Gate**  
  Token muss explizit `vault:write` oder `vault:delete` Scope haben.

- [ ] **Ebene 2: Soft-Delete**  
  `delete_file` in `vault_model.py`: `shutil.move()` nach `.trash/{iso_date}/{rel_path}`  
  statt `os.unlink()`. Alte Logik als `_hard_delete_file` nur für Purge-Job.

- [ ] **Ebene 3: Auto-Snapshot**  
  `write_file` in `vault_model.py`: Alte Version nach  
  `.obsidian/snapshots/{file_hash}/{iso_timestamp}.md` kopieren VOR dem Überschreiben.  
  Max 50 Snapshots pro Datei, älteste rotieren raus.

- [ ] **Ebene 4: Rate-Limiting**  
  Wie oben unter Phase 2 beschrieben.

- [ ] **Ebene 5: Batch-Dry-Run**  
  `POST /api/plugins/obsidian/batch` mit `"dry_run": true` returned  
  `{would_create: [...], would_modify: [...], would_delete: [...]}` ohne Änderungen.

- [ ] **Ebene 6: Undo-Garantie**  
  `vault_history` behält ≥50 Einträge pro Datei, Snapshots + Trash 30 Tage Retention.

- [ ] `.trash/`-Purge-Job  
  Alle 24h per APScheduler: löscht `.trash/`-Einträge >30 Tage per `_hard_delete_file`.  
  Konfigurierbar via `TRASH_RETENTION_DAYS` in Settings.

---

## Phase 4 — UI & Integration

- [ ] Scope-Toggles in Settings-UI  
  `static/js/settings.js`: Checkboxen `vault:read`, `vault:write`, `vault:delete`  
  im API-Token-Dialog.

- [ ] Codex/Claude-Skills erweitern  
  `integrations/codex/skills/odysseus/SKILL.md` +  
  `integrations/claude/skills/odysseus/SKILL.md`:  
  Sektion "Obsidian Vault" mit Heuristiken, Beispiel-Commands, Scope-Voraussetzung.

- [ ] Antigravity-Setup-Doku  
  `integrations/antigravity/README.md`: Token erstellen, `mcp.json` konfigurieren,  
  Test-Call.

- [ ] `odysseus_api.py` Helper erweitern  
  `vault-semantic`, `vault-recent`, `vault-related`, `vault-batch` Subcommands.

---

## Risiko-Analyse

| Risiko | Wkt. | Schaden | Mitigation |
|---|---|---|---|
| Embedding-Modell nicht verfügbar | Mittel | Mittel | Fallback auf Keyword-Suche; FastEmbed als lokaler Default |
| Embedding-Cache invalidiert nicht | Mittel | Mittel | `mtime`-Check vor jeder Suche; `write_file` triggert Neu-Indizierung |
| Token-Leak über MCP | Niedrig | Hoch | Scope-Granularität (read-only Token möglich); Audit-Trail pro Token |
| Path-Traversal via MCP-Args | Niedrig | Kritisch | `secure_path()` wird in jedem Tool direkt aufgerufen |
| Batch-Transaktion bricht ab | Mittel | Mittel | Tempfiles + `os.replace()` pro Operation; partielle Rollbacks |
| Rate-Limit zu restriktiv | Niedrig | Mittel | Konfigurierbar via Settings |
| Streamable HTTP vs. Client-Kompatibilität | Niedrig | Hoch | SSE-Endpoint parallel anbieten als Fallback |

---

## In Scope / Out of Scope

| In Scope | Out of Scope |
|---|---|
| Semantische Suche mit Embedding-Cache | Vault-Import/Export über MCP (Base64 zu groß) |
| Frontmatter CRUD | Projekt-Planung über MCP (SSE-Streaming unpassend) |
| Batch-Operationen mit Dry-Run | Memory Review über MCP |
| Link-Vorschläge (Shared Tags + Graph + Embeddings) | Vault-Passwort-Änderung über MCP |
| Zeitraum-Queries | Multi-Vault-Support |
| Context-Provider mit Backlink-Enrichment | MCP Resources (`vault://notes/*`) — nur Tools |
| MCP-Server mit 14 Tools | |
| 6-stufiges Sicherheitsnetz | |
| Scope-Gating + Audit-Trail | |
| Codex/Claude/Antigravity-Integration | |

---

## Abhängigkeiten zwischen den Phasen

```
Phase 0 (Obsidian-Endpunkte)
    │
    ├── P0 Semantische Suche ──── Voraussetzung für ──► Phase 2 vault_semantic Tool
    ├── P0 Frontmatter CRUD ───── Voraussetzung für ──► Phase 2 vault_read/vault_write
    ├── P1 Batch-Ops ──────────── Voraussetzung für ──► Phase 2 vault_batch
    ├── P1 Zeitraum-Queries ───── Voraussetzung für ──► Phase 2 vault_recent
    ├── P2 Link-Vorschläge ────── Voraussetzung für ──► Phase 1 _enrich_with_*
    └── P3 Tag-Vorschläge ─────── Voraussetzung für ──► Phase 2 vault_tags
         │
         ▼
Phase 1 (Context-Provider Intelligenz)
    │  Nutzt Phase 0 P2 (Link-Vorschläge) + vault_graph.py
    │
    ▼
Phase 2 (MCP-Server)
    │  Nutzt ALLE Phase 0 Endpunkte
    │  Nutzt Phase 3 Sicherheitsnetz
    │
    ▼
Phase 3 (Sicherheitsnetz)
    │  Parallel zu Phase 2 implementierbar
    │
    ▼
Phase 4 (UI & Integration)
       Kann parallel zu allen anderen Phasen laufen
```

---

## Nächste Schritte (Reihenfolge)

1. **P0 Semantische Suche** — `_build_vault_embedding_index()` + `search-semantic` Endpunkt
2. **P0 Frontmatter CRUD** — `GET/PUT /file/frontmatter`
3. **Context-Provider Enrichment** — `_enrich_with_backlinks/shared_tags/folder_context`
4. **P1 Batch-Ops** — `POST /batch` mit Dry-Run
5. **P1 Zeitraum-Queries** — `files/recent` + `files/changed`
6. **Sicherheitsnetz Ebenen 2+3** — Soft-Delete + Auto-Snapshot
7. **MCP-Server** — `vault_server.py` mit allen 14 Tools
8. **Sicherheitsnetz Ebene 4** — Rate-Limiter im MCP-Server
9. **UI/Integration** — Scopes, Skills, Doku
