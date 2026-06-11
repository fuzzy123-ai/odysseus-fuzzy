import os
import re
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Set

from pydantic import BaseModel, Field

from .vault_model import add_manual_relationship, build_vault_index, normalize_tag_name


NEW_PROJECT_FOLDER_SENTINEL = "__new_project_folder__"
PROJECT_KIND_ALIASES = {"ops": "sec_ops", "unterricht": "teaching"}
DOCUMENT_TYPES = {
    "project",
    "requirements",
    "architecture",
    "module",
    "api",
    "data_model",
    "ui_flow",
    "risk",
    "decision",
    "implementation_plan",
    "test_plan",
    "glossary",
    "operations",
    "research",
    "research_question",
    "methodology",
    "findings",
    "audience",
    "outline",
    "draft",
    "revision",
    "security",
    "infrastructure",
    "monitoring",
    "runbook",
    "incident_response",
    "framework",
    "competencies",
    "didactics",
    "lesson_sequence",
    "materials",
    "solutions",
}
RELATIONSHIP_TYPES = {"manual", "relates_to", "depends_on", "blocks", "supports"}


PROJECT_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "software": {
        "label": "Software",
        "documents": [
            {"filename": "00 Projektuebersicht.md", "title": "Projektuebersicht", "type": "project", "outline": ["Ziel", "Umfang", "Dokumente", "Offene Fragen"]},
            {"filename": "01 Anforderungen.md", "title": "Anforderungen", "type": "requirements", "outline": ["Muss-Anforderungen", "Soll-Anforderungen", "Nicht-Ziele"]},
            {"filename": "02 Architektur.md", "title": "Architektur", "type": "architecture", "outline": ["Bausteine", "Datenfluss", "Abhaengigkeiten"]},
            {"filename": "03 Implementierungsplan.md", "title": "Implementierungsplan", "type": "implementation_plan", "outline": ["Schnitt 1", "Schnitt 2", "Risiken"]},
            {"filename": "04 Testplan.md", "title": "Testplan", "type": "test_plan", "outline": ["Unit-Tests", "Integrationstests", "UI-Smokes"]},
            {"filename": "05 Risiken und offene Fragen.md", "title": "Risiken und offene Fragen", "type": "risk", "outline": ["Risiken", "Offene Fragen", "Entscheidungsbedarf"]},
            {"filename": "APIs und Schnittstellen.md", "title": "APIs und Schnittstellen", "type": "api", "outline": ["Eingaenge", "Ausgaenge", "Fehlerfaelle"]},
            {"filename": "Datenmodell.md", "title": "Datenmodell", "type": "data_model", "outline": ["Entitaeten", "Validierung", "Migration"]},
            {"filename": "Entscheidungen/ADR-0001-Grundarchitektur.md", "title": "ADR-0001-Grundarchitektur", "type": "decision", "outline": ["Kontext", "Entscheidung", "Konsequenzen"]},
        ],
        "relationships": [
            ("implementation_plan", "architecture", "depends_on", "Implementation depends on architecture"),
            ("implementation_plan", "requirements", "depends_on", "Implementation tracks requirements"),
            ("test_plan", "requirements", "depends_on", "Tests verify requirements"),
            ("risk", "architecture", "relates_to", "Risks affect architecture choices"),
            ("decision", "architecture", "supports", "Decision records architecture rationale"),
            ("api", "architecture", "depends_on", "APIs are derived from architecture"),
            ("data_model", "architecture", "depends_on", "Data model supports architecture"),
        ],
    },
    "research": {
        "label": "Research",
        "documents": [
            {"filename": "00 Forschungsuebersicht.md", "title": "Forschungsuebersicht", "type": "project", "outline": ["Thema", "Ziel", "Dokumente", "Offene Fragen"]},
            {"filename": "01 Forschungsfrage.md", "title": "Forschungsfrage", "type": "research_question", "outline": ["Leitfrage", "Teilfragen", "Abgrenzung"]},
            {"filename": "02 Quellenlage.md", "title": "Quellenlage", "type": "research", "outline": ["Primaerquellen", "Sekundaerquellen", "Bewertung"]},
            {"filename": "03 Methodik.md", "title": "Methodik", "type": "methodology", "outline": ["Vorgehen", "Kriterien", "Grenzen"]},
            {"filename": "04 Erkenntnisse.md", "title": "Erkenntnisse", "type": "findings", "outline": ["Befunde", "Muster", "Unsicherheiten"]},
            {"filename": "05 Offene Fragen.md", "title": "Offene Fragen", "type": "risk", "outline": ["Ungeklaertes", "Risiken", "Naechste Recherche"]},
        ],
        "relationships": [
            ("methodology", "research_question", "depends_on", "Methodology follows the research question"),
            ("findings", "research", "depends_on", "Findings are derived from sources"),
            ("findings", "methodology", "depends_on", "Findings follow methodology"),
            ("risk", "research_question", "relates_to", "Open questions refine the research question"),
        ],
    },
    "writing": {
        "label": "Writing",
        "documents": [
            {"filename": "00 Schreibprojekt.md", "title": "Schreibprojekt", "type": "project", "outline": ["Ziel", "Format", "Dokumente", "Offene Fragen"]},
            {"filename": "01 Zielgruppe und Aussage.md", "title": "Zielgruppe und Aussage", "type": "audience", "outline": ["Leser", "Kernaussage", "Ton"]},
            {"filename": "02 Gliederung.md", "title": "Gliederung", "type": "outline", "outline": ["Struktur", "Kapitel", "Argumentationsbogen"]},
            {"filename": "03 Recherche.md", "title": "Recherche", "type": "research", "outline": ["Quellen", "Notizen", "Luecken"]},
            {"filename": "04 Entwurf.md", "title": "Entwurf", "type": "draft", "outline": ["Rohfassung", "Szenen oder Abschnitte", "Arbeitsnotizen"]},
            {"filename": "05 Revision.md", "title": "Revision", "type": "revision", "outline": ["Pruefpunkte", "Feedback", "Naechste Fassung"]},
        ],
        "relationships": [
            ("outline", "audience", "depends_on", "Outline follows audience and message"),
            ("draft", "outline", "depends_on", "Draft follows outline"),
            ("draft", "research", "depends_on", "Draft uses research"),
            ("revision", "draft", "depends_on", "Revision improves the draft"),
        ],
    },
    "sec_ops": {
        "label": "Sec-Ops",
        "documents": [
            {"filename": "00 Sicherheitsuebersicht.md", "title": "Sicherheitsuebersicht", "type": "project", "outline": ["Ziel", "Scope", "Assets", "Offene Fragen"]},
            {"filename": "01 Infrastruktur.md", "title": "Infrastruktur", "type": "infrastructure", "outline": ["Systeme", "Zugaenge", "Datenfluesse"]},
            {"filename": "02 Monitoring.md", "title": "Monitoring", "type": "monitoring", "outline": ["Signale", "Alarme", "Dashboards"]},
            {"filename": "03 Runbook.md", "title": "Runbook", "type": "runbook", "outline": ["Routineablaeufe", "Checks", "Eskalation"]},
            {"filename": "04 Incident Response.md", "title": "Incident Response", "type": "incident_response", "outline": ["Erkennung", "Eindaemmung", "Kommunikation", "Nachbereitung"]},
            {"filename": "05 Risiken und Kontrollen.md", "title": "Risiken und Kontrollen", "type": "security", "outline": ["Risiken", "Kontrollen", "Restunsicherheit"]},
        ],
        "relationships": [
            ("monitoring", "infrastructure", "depends_on", "Monitoring observes infrastructure"),
            ("runbook", "monitoring", "depends_on", "Runbook reacts to monitoring signals"),
            ("incident_response", "runbook", "depends_on", "Incident response builds on runbooks"),
            ("security", "infrastructure", "relates_to", "Security controls protect infrastructure"),
        ],
    },
    "generic": {
        "label": "Generic",
        "documents": [
            {"filename": "00 Projektuebersicht.md", "title": "Projektuebersicht", "type": "project", "outline": ["Ziel", "Umfang", "Dokumente", "Offene Fragen"]},
            {"filename": "01 Ziele.md", "title": "Ziele", "type": "requirements", "outline": ["Ergebnis", "Nicht-Ziele", "Erfolgskriterien"]},
            {"filename": "02 Arbeitspakete.md", "title": "Arbeitspakete", "type": "implementation_plan", "outline": ["Paket 1", "Paket 2", "Abhaengigkeiten"]},
            {"filename": "03 Entscheidungen.md", "title": "Entscheidungen", "type": "decision", "outline": ["Entscheidungen", "Begruendung", "Konsequenzen"]},
            {"filename": "04 Risiken.md", "title": "Risiken", "type": "risk", "outline": ["Risiken", "Gegenmassnahmen", "Offene Punkte"]},
        ],
        "relationships": [
            ("implementation_plan", "requirements", "depends_on", "Work packages follow goals"),
            ("decision", "requirements", "supports", "Decisions support goals"),
            ("risk", "implementation_plan", "relates_to", "Risks affect work packages"),
        ],
    },
    "teaching": {
        "label": "Unterricht",
        "documents": [
            {"filename": "00 Unterrichtsuebersicht.md", "title": "Unterrichtsuebersicht", "type": "project", "outline": ["Thema", "Zielgruppe", "Umfang", "Dokumente", "Offene Entscheidungen"]},
            {"filename": "01 Rahmenkriterien.md", "title": "Rahmenkriterien", "type": "framework", "outline": ["Bundesland", "Schulart", "Klasse", "Paedagogische Besonderheiten", "Vorwissen"]},
            {"filename": "02 Kompetenzen und Bildungsplan.md", "title": "Kompetenzen und Bildungsplan", "type": "competencies", "outline": ["Bildungsplanbezug", "G-Niveau", "M-Niveau", "E-Niveau", "Sozialkompetenzen", "Metakompetenzen"]},
            {"filename": "03 Wissenschaftliche Recherche.md", "title": "Wissenschaftliche Recherche", "type": "research", "outline": ["Sachstand", "Zentrale Begriffe", "Quellen", "Fehlvorstellungen"]},
            {"filename": "04 Didaktische Reduktion.md", "title": "Didaktische Reduktion", "type": "didactics", "outline": ["Zielgruppenbezug", "Reduktionen", "Modelle", "Differenzierung", "Paedagogische Begruendung"]},
            {"filename": "05 Verlaufsplan.md", "title": "Verlaufsplan", "type": "lesson_sequence", "outline": ["Stundenuebersicht", "Phasen", "Lehrerhandlung", "Schuelerhandlung", "Sozialform", "Sicherung"]},
            {"filename": "06 Materialien.md", "title": "Materialien", "type": "materials", "outline": ["Praesentationen", "Arbeitsblaetter", "Videos", "Tafelbilder", "Digitale Tools"]},
            {"filename": "07 Loesungen und Erwartungshorizont.md", "title": "Loesungen und Erwartungshorizont", "type": "solutions", "outline": ["Musterloesungen", "Erwartungshorizont", "Hilfen", "Niveaudifferenzierung"]},
            {"filename": "08 Kritische Review.md", "title": "Kritische Review", "type": "revision", "outline": ["Fachliche Stimmigkeit", "Bildungsplan-Abgleich", "Zielgruppenpassung", "Zeitrealismus", "Ueberarbeitungen"]},
        ],
        "relationships": [
            ("competencies", "framework", "depends_on", "Competencies depend on teaching context"),
            ("research", "competencies", "supports", "Research supports competency planning"),
            ("didactics", "research", "depends_on", "Didactic reduction follows research"),
            ("didactics", "framework", "depends_on", "Didactic reduction follows learner context"),
            ("lesson_sequence", "didactics", "depends_on", "Lesson sequence follows didactic reduction"),
            ("materials", "lesson_sequence", "depends_on", "Materials support lesson phases"),
            ("solutions", "materials", "depends_on", "Solutions correspond to planned materials"),
            ("revision", "lesson_sequence", "relates_to", "Review checks the full lesson plan"),
        ],
    },
}
PROJECT_KINDS = set(PROJECT_TEMPLATES)


class ProjectSpec(BaseModel):
    title: str
    slug: str
    kind: str = "generic"
    summary: str = ""


class PlannedFile(BaseModel):
    path: str
    title: str
    type: str
    status: str = "draft"
    tags: List[str] = Field(default_factory=list)
    frontmatter: Dict[str, Any] = Field(default_factory=dict)
    links: List[str] = Field(default_factory=list)
    outline: List[str] = Field(default_factory=list)
    content_preview: str = ""
    content: Optional[str] = None


class PlannedRelationship(BaseModel):
    source: str
    target: str
    type: str = "relates_to"
    reason: str = ""


class NewTag(BaseModel):
    tag: str
    reason: str = ""


class ProjectPlan(BaseModel):
    target_folder: str
    project: ProjectSpec
    files: List[PlannedFile]
    relationships: List[PlannedRelationship] = Field(default_factory=list)
    new_tags: List[NewTag] = Field(default_factory=list)
    conflicts: List[Dict[str, str]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    questions: List[str] = Field(default_factory=list)


class ProjectPlanRequest(BaseModel):
    target_folder: str = ""
    title: str
    description: str = ""
    kind: str = "software"


class ProjectPlanApplyRequest(BaseModel):
    plan: ProjectPlan
    confirm: bool = False
    confirm_conflicts: bool = False


class ProjectPlanValidationError(ValueError):
    pass


def slugify_project(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "untitled-project"


def normalize_project_kind(value: str) -> str:
    kind = re.sub(r"[^a-z0-9]+", "_", str(value or "generic").strip().lower()).strip("_")
    kind = PROJECT_KIND_ALIASES.get(kind, kind)
    return kind if kind in PROJECT_TEMPLATES else "generic"


def normalize_relative_path(path: str) -> str:
    raw = str(path or "").replace("\\", "/").strip()
    if not raw or raw in {".", "/"}:
        return ""
    if re.match(r"^[A-Za-z]:", raw) or raw.startswith("/"):
        raise ProjectPlanValidationError("Absolute paths are not allowed")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ProjectPlanValidationError("Path traversal is not allowed")
    return "/".join(parts)


def normalize_project_target_folder(target_folder: str, project_slug: str) -> str:
    raw = str(target_folder or "").strip()
    if raw == NEW_PROJECT_FOLDER_SENTINEL:
        return normalize_relative_path(project_slug)
    prefix = f"{NEW_PROJECT_FOLDER_SENTINEL}::"
    if raw.startswith(prefix):
        parent = normalize_relative_path(raw[len(prefix):])
        return "/".join(part for part in [parent, project_slug] if part)
    return normalize_relative_path(raw)


def resolve_inside(base_dir: str, relative_path: str) -> str:
    clean = normalize_relative_path(relative_path)
    abs_base = os.path.abspath(base_dir)
    abs_target = os.path.abspath(os.path.join(abs_base, clean))
    if os.path.commonpath([abs_base, abs_target]) != abs_base:
        raise ProjectPlanValidationError("Path escapes the vault")
    return abs_target


def ensure_under_folder(target_folder: str, file_path: str) -> None:
    folder = normalize_relative_path(target_folder)
    path = normalize_relative_path(file_path)
    if not folder:
        return
    if path != folder and not path.startswith(f"{folder}/"):
        raise ProjectPlanValidationError(f"File path must stay under target folder: {path}")


def template_options() -> Dict[str, Any]:
    kinds = [
        {"key": key, "label": template["label"]}
        for key, template in PROJECT_TEMPLATES.items()
    ]
    return {
        "kinds": kinds,
        "document_types": sorted(DOCUMENT_TYPES),
        "default_kind": "software",
        "new_folder_sentinel": NEW_PROJECT_FOLDER_SENTINEL,
    }


def build_project_plan(
    vault_dir: str,
    request: ProjectPlanRequest,
    *,
    today: Optional[date] = None,
) -> ProjectPlan:
    title = request.title.strip()
    if not title:
        raise ProjectPlanValidationError("Project title is required")
    kind = normalize_project_kind(request.kind)
    slug = slugify_project(title)
    target_folder = normalize_project_target_folder(request.target_folder, slug)
    created = (today or date.today()).isoformat()
    existing_tags = {tag["name"] for tag in build_vault_index(vault_dir).get("tags", [])}
    specs = _document_specs(kind)
    files: List[PlannedFile] = []
    for spec in specs:
        path = _document_path(target_folder, spec["filename"])
        tags = [
            f"#project/{slug}",
            f"#type/{spec['type']}",
            "#status/draft",
        ]
        frontmatter = {
            "type": spec["type"],
            "project": slug,
            "status": "draft",
            "source": "ai_project_planning",
            "created": created,
        }
        links = _links_for(spec["type"], specs, target_folder)
        content = render_project_markdown(
            title=spec["title"],
            project_title=title,
            project_slug=slug,
            description=request.description,
            doc_type=spec["type"],
            created=created,
            tags=tags,
            links=links,
            outline=spec["outline"],
        )
        files.append(PlannedFile(
            path=path,
            title=spec["title"],
            type=spec["type"],
            status="draft",
            tags=tags,
            frontmatter=frontmatter,
            links=links,
            outline=spec["outline"],
            content_preview=_content_preview(content),
            content=content,
        ))
    relationships = _relationships_for(files, kind)
    new_tags = _new_tags_for(files, existing_tags)
    plan = ProjectPlan(
        target_folder=target_folder,
        project=ProjectSpec(title=title, slug=slug, kind=kind, summary=request.description.strip()),
        files=files,
        relationships=relationships,
        new_tags=new_tags,
        conflicts=[],
        warnings=[],
        questions=[],
    )
    return validate_project_plan(vault_dir, plan, collect_conflicts=True)


def validate_project_plan(
    vault_dir: str,
    plan: ProjectPlan,
    *,
    collect_conflicts: bool = False,
) -> ProjectPlan:
    target_folder = normalize_relative_path(plan.target_folder)
    seen: Set[str] = set()
    planned_paths: Set[str] = set()
    conflicts: List[Dict[str, str]] = []
    warnings: List[str] = []
    existing_notes = set(_markdown_notes(vault_dir))

    plan.project.kind = normalize_project_kind(plan.project.kind)
    if plan.project.kind not in PROJECT_KINDS:
        warnings.append(f"Unknown project kind normalized by caller: {plan.project.kind}")
    if not plan.project.slug:
        raise ProjectPlanValidationError("Project slug is required")

    for planned in plan.files:
        planned.path = normalize_relative_path(planned.path)
        ensure_under_folder(target_folder, planned.path)
        if not planned.path.lower().endswith(".md"):
            raise ProjectPlanValidationError(f"Planned file must be markdown: {planned.path}")
        lower = planned.path.lower()
        if lower in seen:
            raise ProjectPlanValidationError(f"Duplicate planned file path: {planned.path}")
        seen.add(lower)
        planned_paths.add(planned.path)
        resolve_inside(vault_dir, planned.path)
        if os.path.exists(os.path.join(vault_dir, planned.path)):
            conflicts.append({"path": planned.path, "reason": "file_exists"})
        if planned.type not in DOCUMENT_TYPES:
            raise ProjectPlanValidationError(f"Unsupported document type: {planned.type}")
        _validate_tags(planned.tags, plan.project.slug)
        if not planned.frontmatter.get("type"):
            raise ProjectPlanValidationError(f"Missing frontmatter type for {planned.path}")
        if not planned.frontmatter.get("project"):
            raise ProjectPlanValidationError(f"Missing frontmatter project for {planned.path}")
        if planned.frontmatter.get("source") != "ai_project_planning":
            raise ProjectPlanValidationError(f"Invalid source for {planned.path}")

    allowed_link_targets = planned_paths | existing_notes
    for planned in plan.files:
        for link in planned.links:
            target = _link_to_path(link, planned.path)
            if target and target not in allowed_link_targets:
                warnings.append(f"Link target does not exist in plan or vault: {link}")

    for relationship in plan.relationships:
        relationship.source = normalize_relative_path(relationship.source)
        relationship.target = normalize_relative_path(relationship.target)
        ensure_under_folder(target_folder, relationship.source)
        ensure_under_folder(target_folder, relationship.target)
        if relationship.source not in planned_paths and relationship.source not in existing_notes:
            raise ProjectPlanValidationError(f"Relationship source does not exist: {relationship.source}")
        if relationship.target not in planned_paths and relationship.target not in existing_notes:
            raise ProjectPlanValidationError(f"Relationship target does not exist: {relationship.target}")
        if relationship.source == relationship.target:
            raise ProjectPlanValidationError("Relationship source and target must differ")
        if relationship.type not in RELATIONSHIP_TYPES:
            raise ProjectPlanValidationError(f"Unsupported relationship type: {relationship.type}")

    for new_tag in plan.new_tags:
        normalized = normalize_hash_tag(new_tag.tag)
        if new_tag.tag != normalized:
            new_tag.tag = normalized
        if not new_tag.reason:
            raise ProjectPlanValidationError(f"New tag needs a reason: {new_tag.tag}")

    plan.target_folder = target_folder
    plan.conflicts = conflicts if collect_conflicts else plan.conflicts
    plan.warnings = sorted(set([*plan.warnings, *warnings]))
    return plan


def apply_project_plan(vault_dir: str, plan: ProjectPlan) -> Dict[str, Any]:
    plan = validate_project_plan(vault_dir, plan, collect_conflicts=True)
    if plan.conflicts:
        raise ProjectPlanValidationError("Plan has file conflicts")

    written: List[str] = []
    relationships: List[Dict[str, Any]] = []
    for planned in plan.files:
        abs_path = resolve_inside(vault_dir, planned.path)
        if os.path.exists(abs_path):
            raise ProjectPlanValidationError(f"File already exists: {planned.path}")
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        content = planned.content or render_project_markdown(
            title=planned.title,
            project_title=plan.project.title,
            project_slug=plan.project.slug,
            description=plan.project.summary,
            doc_type=planned.type,
            created=str(planned.frontmatter.get("created") or date.today().isoformat()),
            tags=planned.tags,
            links=planned.links,
            outline=planned.outline,
        )
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        written.append(planned.path)

    for relationship in plan.relationships:
        relationship_payload = relationship.model_dump() if hasattr(relationship, "model_dump") else relationship.dict()
        relationships.append(add_manual_relationship(vault_dir, relationship_payload))

    graph = build_vault_index(vault_dir)["graph"]
    return {
        "success": True,
        "created_files": written,
        "relationships": relationships,
        "graph": {
            "nodes": len(graph["nodes"]),
            "edges": len(graph["edges"]),
        },
    }


def render_project_markdown(
    *,
    title: str,
    project_title: str,
    project_slug: str,
    description: str,
    doc_type: str,
    created: str,
    tags: Iterable[str],
    links: Iterable[str],
    outline: Iterable[str],
) -> str:
    overview_link = next((link for link in links if re.match(r"\[\[[^]]*00 ", str(link))), "")
    project_line = f"Projekt: {overview_link}" if overview_link else f"Projekt: {project_title}"
    frontmatter = [
        "---",
        f"type: {doc_type}",
        f"project: {project_slug}",
        "status: draft",
        "source: ai_project_planning",
        f"created: {created}",
        "---",
        "",
    ]
    body = [
        f"# {title}",
        "",
        project_line,
        "",
        "## Kontext",
        "",
        description.strip() or "Projektkontext wird im naechsten Review ausgearbeitet.",
        "",
        "## Verknuepfte Notizen",
        "",
    ]
    link_lines = [f"- {link}" for link in links] or ["- [[00 Projektuebersicht]]"]
    outline_lines = ["", "## Arbeitsnotizen", ""]
    for item in outline:
        outline_lines.extend([f"### {item}", "", "- [ ] Klaeren und ausarbeiten.", ""])
    tag_line = ["Tags: " + " ".join(tags), ""]
    return "\n".join(frontmatter + body + link_lines + outline_lines + tag_line)


def normalize_hash_tag(tag: str) -> str:
    raw = str(tag or "").strip()
    raw = raw[1:] if raw.startswith("#") else raw
    return f"#{normalize_tag_name(raw)}"


def _validate_tags(tags: List[str], project_slug: str) -> None:
    normalized = {normalize_hash_tag(tag) for tag in tags}
    required = {
        f"#project/{project_slug}",
        "#status/draft",
    }
    if not required.issubset(normalized):
        raise ProjectPlanValidationError("Project and draft status tags are required")
    if not any(tag.startswith("#type/") for tag in normalized):
        raise ProjectPlanValidationError("A type tag is required")


def _document_specs(kind: str) -> List[Dict[str, Any]]:
    template = PROJECT_TEMPLATES[normalize_project_kind(kind)]
    return [dict(spec) for spec in template["documents"]]


def _document_path(target_folder: str, filename: str) -> str:
    return "/".join(part for part in [target_folder, filename] if part)


def _links_for(doc_type: str, specs: List[Dict[str, Any]], target_folder: str) -> List[str]:
    def note_link(filename: str) -> str:
        stem = os.path.splitext(filename)[0]
        return f"[[{_document_path(target_folder, stem)}]]"

    overview = note_link(specs[0]["filename"])
    by_type = {spec["type"]: spec["filename"] for spec in specs}
    if doc_type == "project":
        return [note_link(spec["filename"]) for spec in specs if spec["type"] != "project"]
    links = [overview]
    if doc_type == "implementation_plan":
        for linked_type in ("requirements", "architecture", "test_plan"):
            if linked_type in by_type:
                links.append(note_link(by_type[linked_type]))
    if doc_type == "test_plan":
        for linked_type in ("requirements", "implementation_plan"):
            if linked_type in by_type:
                links.append(note_link(by_type[linked_type]))
    if doc_type == "risk":
        for linked_type in ("requirements", "architecture", "implementation_plan"):
            if linked_type in by_type:
                links.append(note_link(by_type[linked_type]))
    if doc_type in {"api", "data_model", "decision"} and "architecture" in by_type:
        links.append(note_link(by_type["architecture"]))
    return sorted(set(links), key=links.index)


def _relationships_for(files: List[PlannedFile], kind: str) -> List[PlannedRelationship]:
    by_type = {planned.type: planned.path for planned in files}
    specs = PROJECT_TEMPLATES[normalize_project_kind(kind)].get("relationships", [])
    relationships = []
    for source_type, target_type, relation_type, reason in specs:
        source = by_type.get(source_type)
        target = by_type.get(target_type)
        if source and target:
            relationships.append(PlannedRelationship(source=source, target=target, type=relation_type, reason=reason))
    return relationships


def _new_tags_for(files: List[PlannedFile], existing_tags: Set[str]) -> List[NewTag]:
    tags = sorted({normalize_hash_tag(tag) for planned in files for tag in planned.tags})
    new_tags = []
    for tag in tags:
        name = tag[1:]
        if name not in existing_tags:
            new_tags.append(NewTag(tag=tag, reason="Required by Phase 4 project note schema"))
    return new_tags


def _markdown_notes(vault_dir: str) -> List[str]:
    notes: List[str] = []
    for root, dirs, files in os.walk(vault_dir):
        dirs[:] = [d for d in dirs if d != ".obsidian"]
        for file in files:
            if file.lower().endswith(".md"):
                notes.append(os.path.relpath(os.path.join(root, file), vault_dir).replace("\\", "/"))
    notes.sort(key=str.lower)
    return notes


def _link_to_path(link: str, source_path: str) -> Optional[str]:
    match = re.match(r"\[\[([^\]|#]+)", str(link or "").strip())
    if not match:
        return None
    target = match.group(1).strip()
    if not target.lower().endswith(".md"):
        target = f"{target}.md"
    if "/" not in target and "/" in source_path:
        local = f"{source_path.rsplit('/', 1)[0]}/{target}"
        return local
    return target


def _content_preview(content: str) -> str:
    lines = [line for line in content.splitlines() if line.strip()]
    return "\n".join(lines[:8])
