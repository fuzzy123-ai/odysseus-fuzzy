import json
import os
import re
import sys
import tempfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backend.routes as obsidian_routes
from backend.project_planning import (
    GameDevConceptDraftRequest,
    NEW_PROJECT_FOLDER_SENTINEL,
    ProjectDescriptionImproveRequest,
    ProjectPlan,
    ProjectPlanRequest,
    ProjectPlanValidationError,
    build_gamedev_concept_draft_with_ai,
    build_project_plan,
    generate_project_plan_content,
    improve_project_description_with_ai,
    normalize_project_kind,
    normalize_project_target_folder,
    template_options,
    validate_gamedev_concept_gate,
    validate_project_plan,
)
from plugin import (
    handle_graph,
    handle_history,
    handle_project_plan_apply,
    handle_project_plan_gamedev_draft,
    handle_project_plan_improve_description,
    handle_project_plan_preview,
    handle_project_plan_templates,
)


def test_project_plan_preview_validates_schema_paths_tags_and_conflicts():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects", "Demo"), exist_ok=True)
        with open(os.path.join(tmpdir, "Projects", "Demo", "00 Projektuebersicht.md"), "w", encoding="utf-8") as f:
            f.write("# Existing")

        plan = build_project_plan(tmpdir, ProjectPlanRequest(
            target_folder="Projects/Demo",
            title="Demo App",
            description="A small planning target.",
            custom_focus="Emphasize offline-first decisions.",
            kind="software",
        ))

        assert plan.project.slug == "demo-app"
        assert "Nutzerdefinierte Schwerpunkte" in plan.project.summary
        assert "offline-first" in plan.files[0].content
        assert plan.conflicts == [{"path": "Projects/Demo/00 Projektuebersicht.md", "reason": "file_exists"}]
        first = plan.files[0]
        assert first.path == "Projects/Demo/00 Projektuebersicht.md"
        assert "#project/demo-app" in first.tags
        assert "#type/project" in first.tags
        assert "#status/draft" in first.tags
        assert first.links == [
            "[[Projects/Demo/01 Anforderungen]]",
            "[[Projects/Demo/02 Architektur]]",
            "[[Projects/Demo/03 Implementierungsplan]]",
            "[[Projects/Demo/04 Testplan]]",
            "[[Projects/Demo/05 Risiken und offene Fragen]]",
            "[[Projects/Demo/APIs und Schnittstellen]]",
            "[[Projects/Demo/Datenmodell]]",
            "[[Projects/Demo/Entscheidungen/ADR-0001-Grundarchitektur]]",
        ]
        assert all(file.links == ["[[Projects/Demo/00 Projektuebersicht]]"] for file in plan.files[1:])
        assert plan.relationships == []

        plan_payload = plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
        bad = ProjectPlan(**plan_payload)
        bad.files[0].path = "../escape.md"
        with pytest.raises(ProjectPlanValidationError):
            validate_project_plan(tmpdir, bad)

        bad = ProjectPlan(**plan_payload)
        bad.files[0].tags = ["#project/demo-app", "#status/draft"]
        with pytest.raises(ProjectPlanValidationError):
            validate_project_plan(tmpdir, bad)


def test_project_plan_templates_drive_distinct_project_kinds_and_aliases():
    options = template_options()
    kind_labels = {item["key"]: item["label"] for item in options["kinds"]}
    assert kind_labels["sec_ops"] == "Sec-Ops"
    assert kind_labels["teaching"] == "Teaching"
    assert kind_labels["game_dev"] == "GameDev"
    assert "ops" not in kind_labels
    assert normalize_project_kind("ops") == "sec_ops"
    assert normalize_project_kind("Unterricht") == "teaching"
    assert normalize_project_kind("Education") == "teaching"
    assert normalize_project_kind("GameDev") == "game_dev"
    assert normalize_project_kind("game-dev") == "game_dev"

    with tempfile.TemporaryDirectory() as tmpdir:
        plans = {
            kind: build_project_plan(tmpdir, ProjectPlanRequest(
                target_folder=f"Projects/{kind}",
                title=f"{kind} Demo",
                description="Template coverage.",
                kind=kind,
            ))
            for kind in ["software", "research", "writing", "sec_ops", "generic", "teaching", "game_dev"]
        }

        software_paths = {file.path for file in plans["software"].files}
        assert "Projects/software/APIs und Schnittstellen.md" in software_paths
        assert "Projects/software/Datenmodell.md" in software_paths

        assert {file.path for file in plans["research"].files} != software_paths
        assert any(file.path.endswith("01 Forschungsfrage.md") for file in plans["research"].files)
        assert any(file.path.endswith("02 Gliederung.md") for file in plans["writing"].files)
        assert any(file.path.endswith("04 Incident Response.md") for file in plans["sec_ops"].files)
        assert any(file.path.endswith("02 Arbeitspakete.md") for file in plans["generic"].files)
        assert any(file.path.endswith("03 Engine and Architecture.md") for file in plans["game_dev"].files)
        assert any(file.path.endswith("09 Risks and Open Questions.md") for file in plans["game_dev"].files)

        teaching_paths = [file.path for file in plans["teaching"].files]
        assert len(teaching_paths) == 9
        assert teaching_paths == [
            "Projects/teaching/00 Unterrichtsuebersicht.md",
            "Projects/teaching/01 Rahmenkriterien.md",
            "Projects/teaching/02 Kompetenzen und Bildungsplan.md",
            "Projects/teaching/03 Wissenschaftliche Recherche.md",
            "Projects/teaching/04 Didaktische Reduktion.md",
            "Projects/teaching/05 Verlaufsplan.md",
            "Projects/teaching/06 Materialien.md",
            "Projects/teaching/07 Loesungen und Erwartungshorizont.md",
            "Projects/teaching/08 Kritische Review.md",
        ]

        game_paths = [file.path for file in plans["game_dev"].files]
        assert len(game_paths) == 10
        assert game_paths == [
            "Projects/game_dev/00 Game Overview.md",
            "Projects/game_dev/01 Scope and MVP.md",
            "Projects/game_dev/02 Core Gameplay Loop.md",
            "Projects/game_dev/03 Engine and Architecture.md",
            "Projects/game_dev/04 Gameplay Systems.md",
            "Projects/game_dev/05 Content and Level Design.md",
            "Projects/game_dev/06 Art Audio UI Pipeline.md",
            "Projects/game_dev/07 Production Plan.md",
            "Projects/game_dev/08 Testing and Balancing.md",
            "Projects/game_dev/09 Risks and Open Questions.md",
        ]
        assert "[[Projects/game_dev/00 Game Overview]]" not in plans["game_dev"].files[0].links
        assert all(file.links == ["[[Projects/game_dev/00 Game Overview]]"] for file in plans["game_dev"].files[1:])


def test_project_plan_new_folder_sentinel_is_resolved_without_preview_writes():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects"), exist_ok=True)
        assert normalize_project_target_folder(f"{NEW_PROJECT_FOLDER_SENTINEL}::Projects", "demo-app") == "Projects/demo-app"
        assert normalize_project_target_folder(f"{NEW_PROJECT_FOLDER_SENTINEL}::", "demo-app") == "demo-app"

        plan = build_project_plan(tmpdir, ProjectPlanRequest(
            target_folder=f"{NEW_PROJECT_FOLDER_SENTINEL}::",
            title="Demo App",
            description="Preview only.",
            kind="generic",
        ))

        assert plan.target_folder == "demo-app"
        assert all(file.path.startswith("demo-app/") for file in plan.files)
        assert not os.path.exists(os.path.join(tmpdir, "demo-app"))


@pytest.mark.asyncio
async def test_project_plan_ai_improves_description():
    async def fake_llm(messages, **kwargs):
        assert "kein Denkprotokoll" in messages[0]["content"]
        assert "keine Meta-Kommentare" in messages[0]["content"]
        assert "Der Nutzer will" in messages[0]["content"]
        assert "Sprache der eigentlichen Projekteingabe" in messages[0]["content"]
        assert "Korrigiere offensichtliche Tippfehler still" in messages[0]["content"]
        assert "Verbessere diesen Projektkontext" in messages[-1]["content"]
        assert "Nutzerdefinierte Schwerpunkte" in messages[-1]["content"]
        assert "Beginne direkt mit den lokalisierten Entsprechungen" in messages[-1]["content"]
        assert "Geplantes Ergebnis" in messages[-1]["content"]
        assert "Schreibe keine Saetze ueber die Eingabe" in messages[-1]["content"]
        assert "Nenne keine internen Ueberlegungen" in messages[-1]["content"]
        assert "Differenzierung" in messages[-1]["content"]
        return "Ziel: klarer Unterrichtsplan.\nOffene Fragen: Bundesland klaeren."

    improved = await improve_project_description_with_ai(
        ProjectDescriptionImproveRequest(
            title="Hasen",
            description="mach unterricht",
            custom_focus="Differenzierung und Zeitrealismus beachten.",
            kind="teaching",
        ),
        llm_call=fake_llm,
    )

    assert "klarer Unterrichtsplan" in improved
    assert "Offene Fragen" in improved


@pytest.mark.asyncio
async def test_project_plan_ai_strips_prompt_improvement_metatext():
    async def fake_llm(messages, **kwargs):
        return (
            "Wir haben die Eingabe: Projektart Research, Projekttitel Beer.\n"
            "Der Nutzer will eine verbesserte Projektbeschreibung.\n\n"
            "Project type: Research\n"
            "Project title: Beer\n"
            "Goal: Research the history of beer with verified sources.\n"
            "Open questions: Define geography and depth."
        )

    improved = await improve_project_description_with_ai(
        ProjectDescriptionImproveRequest(
            title="Beer",
            description="Research the history of beer.",
            custom_focus="make sure every link is true to its proposed content. No fake news!",
            kind="research",
        ),
        llm_call=fake_llm,
    )

    assert improved.startswith("Project type: Research")
    assert "Wir haben" not in improved
    assert "Der Nutzer will" not in improved
    assert "Research the history of beer" in improved


@pytest.mark.asyncio
async def test_project_plan_gamedev_draft_and_approval_gate():
    async def fake_llm(messages, **kwargs):
        assert "editable GameDev concept draft" in messages[0]["content"]
        assert "worker/unit complexity" in messages[-1]["content"]
        assert "pathfinding risk first" in messages[-1]["content"]
        return (
            "# GameDev Concept Draft\n\n"
            "## MVP Scope\nA tiny 2D strategy prototype.\n\n"
            "## Engine and Tech Assumptions\nGodot 2D.\n\n"
            "## Production Risks\nWorker units need pathfinding and task queues.\n\n"
            "## Open Questions\nMap size and win condition."
        )

    draft = await build_gamedev_concept_draft_with_ai(
        GameDevConceptDraftRequest(
            title="Worker Fields",
            description="2D strategy game in Godot with workers.",
            custom_focus="pathfinding risk first",
            kind="GameDev",
        ),
        llm_call=fake_llm,
    )
    assert "Worker units" in draft["draft"]
    assert draft["warnings"] == []

    blocked = ProjectPlanRequest(
        target_folder="Games/Worker Fields",
        title="Worker Fields",
        description="2D strategy game in Godot with workers.",
        kind="GameDev",
        generate_content=True,
    )
    with pytest.raises(ProjectPlanValidationError):
        validate_gamedev_concept_gate(blocked)

    approved = ProjectPlanRequest(
        target_folder="Games/Worker Fields",
        title="Worker Fields",
        description="Original prompt.",
        kind="GameDev",
        generate_content=True,
        concept_approved=True,
        approved_concept=draft["draft"],
    )
    validate_gamedev_concept_gate(approved)
    with tempfile.TemporaryDirectory() as tmpdir:
        plan = build_project_plan(tmpdir, approved)
        assert plan.project.kind == "game_dev"
        assert "Worker units need pathfinding" in plan.project.summary
        assert "Worker units need pathfinding" in plan.files[0].content


@pytest.mark.asyncio
async def test_project_plan_ai_generation_is_sequential_context_chain():
    with tempfile.TemporaryDirectory() as tmpdir:
        plan = build_project_plan(tmpdir, ProjectPlanRequest(
            target_folder="Projects/Demo",
            title="Demo App",
            description="Create a useful project folder.",
            custom_focus="Prioritize API boundaries.",
            kind="generic",
        ))
        calls = []

        async def fake_llm(messages, **kwargs):
            system = messages[0]["content"]
            user = messages[-1]["content"]
            calls.append({"system": system, "user": user})
            if "einzelne Markdown-Datei" in system:
                assert "Prioritize API boundaries" in user
                match = re.search(r"Zieldatei \d+ von \d+: (.+)", user)
                path = match.group(1)
                return f"# Generated {path}\n\nContent built from sequential context."
            if "Kontextzusammenfassung" in system:
                match = re.search(r"Neu generierte Datei: (.+)", user)
                path = match.group(1)
                previous = user.split("Bisherige Kontextzusammenfassung:\n", 1)[1].split("\n\nNeu generierte Datei:", 1)[0]
                return f"{previous}\nCTX after {path}"
            raise AssertionError("unexpected prompt")

        enriched = await generate_project_plan_content(plan, llm_call=fake_llm)

        generation_prompts = [call["user"] for call in calls if "Zieldatei" in call["user"]]
        assert len(generation_prompts) == len(enriched.files)
        assert "Bisher generierte Dateien: noch keine" in generation_prompts[0]
        assert "CTX after Projects/Demo/00 Projektuebersicht.md" in generation_prompts[1]
        assert "CTX after Projects/Demo/03 Entscheidungen.md" in generation_prompts[-1]
        assert "Generated Projects/Demo/00 Projektuebersicht.md" in enriched.files[0].content


@pytest.mark.asyncio
async def test_project_plan_ai_generation_emits_progress_in_file_order():
    with tempfile.TemporaryDirectory() as tmpdir:
        plan = build_project_plan(tmpdir, ProjectPlanRequest(
            target_folder="Projects/Demo",
            title="Demo App",
            description="Create a useful project folder.",
            kind="generic",
        ))
        events = []

        async def fake_llm(messages, **kwargs):
            system = messages[0]["content"]
            user = messages[-1]["content"]
            if "einzelne Markdown-Datei" in system:
                path = re.search(r"Zieldatei \d+ von \d+: (.+)", user).group(1)
                return f"# Generated {path}\n\nSequential content."
            if "Kontextzusammenfassung" in system:
                path = re.search(r"Neu generierte Datei: (.+)", user).group(1)
                return f"CTX after {path}"
            raise AssertionError("unexpected prompt")

        async def progress(event):
            events.append(dict(event))

        await generate_project_plan_content(plan, llm_call=fake_llm, progress_callback=progress)

        started = [event for event in events if event["type"] == "file_started"]
        done = [event for event in events if event["type"] == "file_done"]
        assert [event["index"] for event in started] == list(range(len(plan.files)))
        assert [event["index"] for event in done] == list(range(len(plan.files)))
        assert done[0]["file"]["path"] == "Projects/Demo/00 Projektuebersicht.md"
        assert "Sequential content" in done[0]["file"]["content"]


@pytest.mark.asyncio
async def test_project_plan_ai_generation_retries_and_keeps_partial_preview():
    with tempfile.TemporaryDirectory() as tmpdir:
        plan = build_project_plan(tmpdir, ProjectPlanRequest(
            target_folder="Projects/Demo",
            title="Demo App",
            description="Create a useful project folder.",
            kind="generic",
        ))
        attempts = {}

        async def fake_llm(messages, **kwargs):
            system = messages[0]["content"]
            user = messages[-1]["content"]
            if "einzelne Markdown-Datei" in system:
                match = re.search(r"Zieldatei \d+ von \d+: (.+)", user)
                path = match.group(1)
                attempts[path] = attempts.get(path, 0) + 1
                if path.endswith("00 Projektuebersicht.md"):
                    raise RuntimeError("temporary model error")
                assert "Generierungswarnung fuer Projects/Demo/00 Projektuebersicht.md" in user
                return f"# Generated {path}\n\nContinued after warning."
            if "Kontextzusammenfassung" in system:
                match = re.search(r"Neu generierte Datei: (.+)", user)
                return f"CTX after {match.group(1)}"
            raise AssertionError("unexpected prompt")

        enriched = await generate_project_plan_content(plan, llm_call=fake_llm, max_attempts=3)

        assert attempts["Projects/Demo/00 Projektuebersicht.md"] == 3
        assert any("AI generation failed for Projects/Demo/00 Projektuebersicht.md after 3 attempts" in warning for warning in enriched.warnings)
        assert "Klaeren und ausarbeiten" in enriched.files[0].content
        assert "Generated Projects/Demo/01 Ziele.md" in enriched.files[1].content


@pytest.mark.asyncio
async def test_project_plan_preview_stream_emits_sse_events(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)
        monkeypatch.setattr(obsidian_routes, "current_owner", lambda request: "alice")

        async def fake_llm(messages, **kwargs):
            system = messages[0]["content"]
            user = messages[-1]["content"]
            if "einzelne Markdown-Datei" in system:
                path = re.search(r"Zieldatei \d+ von \d+: (.+)", user).group(1)
                return f"# Generated {path}\n\nStreamed content."
            if "Kontextzusammenfassung" in system:
                path = re.search(r"Neu generierte Datei: (.+)", user).group(1)
                return f"CTX after {path}"
            raise AssertionError("unexpected prompt")

        monkeypatch.setattr(obsidian_routes, "project_planning_llm_call", lambda owner: fake_llm)
        response = await obsidian_routes.project_plan_preview_stream(ProjectPlanRequest(
            target_folder="Projects/Demo",
            title="Demo App",
            description="Create a streamed project folder.",
            kind="generic",
            generate_content=True,
        ), SimpleNamespace())

        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
        stream = "".join(chunks)

        assert "event: plan_started" in stream
        assert "event: file_started" in stream
        assert "event: file_done" in stream
        assert "event: plan_done" in stream
        assert stream.index("event: plan_started") < stream.index("event: file_started")
        assert "Streamed content" in stream


@pytest.mark.asyncio
async def test_project_plan_sessions_are_recoverable_and_non_destructive(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)

        created = await obsidian_routes.project_plan_session_create(
            obsidian_routes.ProjectPlanSessionCreateRequest(
                request=ProjectPlanRequest(
                    target_folder=f"{NEW_PROJECT_FOLDER_SENTINEL}::Projects",
                    title="Recoverable Demo",
                    description="Create a recoverable planning session.",
                    kind="software",
                    generate_content=True,
                )
            ),
            SimpleNamespace(),
        )

        assert created["status"] == "draft"
        assert created["target_folder"] == "Projects/recoverable-demo"
        assert created["debug_events"][0]["message"] == "Session created"
        assert not os.path.exists(os.path.join(tmpdir, "Projects", "recoverable-demo"))

        listed = await obsidian_routes.project_plan_sessions(SimpleNamespace())
        assert [session["id"] for session in listed["sessions"]] == [created["id"]]

        loaded = await obsidian_routes.project_plan_session_get(created["id"], SimpleNamespace())
        assert loaded["request"]["title"] == "Recoverable Demo"

        deleted = await obsidian_routes.project_plan_session_delete(created["id"], SimpleNamespace())
        assert deleted == {"success": True, "session_id": created["id"]}
        listed = await obsidian_routes.project_plan_sessions(SimpleNamespace())
        assert listed["sessions"] == []


@pytest.mark.asyncio
async def test_project_plan_session_preview_stream_persists_progress(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)
        monkeypatch.setattr(obsidian_routes, "current_owner", lambda request: "alice")

        async def fake_llm(messages, **kwargs):
            system = messages[0]["content"]
            user = messages[-1]["content"]
            if "einzelne Markdown-Datei" in system:
                path = re.search(r"Zieldatei \d+ von \d+: (.+)", user).group(1)
                return f"# Generated {path}\n\nStreamed content."
            if "Kontextzusammenfassung" in system:
                path = re.search(r"Neu generierte Datei: (.+)", user).group(1)
                return f"CTX after {path}"
            raise AssertionError("unexpected prompt")

        monkeypatch.setattr(obsidian_routes, "project_planning_llm_call", lambda owner: fake_llm)
        created = await obsidian_routes.project_plan_session_create(
            obsidian_routes.ProjectPlanSessionCreateRequest(
                request=ProjectPlanRequest(
                    target_folder="Projects/Demo",
                    title="Demo",
                    description="Create a streamed project folder.",
                    kind="software",
                    generate_content=True,
                )
            ),
            SimpleNamespace(),
        )

        response = await obsidian_routes.project_plan_session_preview_stream(
            created["id"],
            obsidian_routes.ProjectPlanSessionPreviewRequest(),
            SimpleNamespace(),
        )
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
        stream = "".join(chunks)

        assert "event: session_updated" in stream
        assert "event: plan_done" in stream
        loaded = await obsidian_routes.project_plan_session_get(created["id"], SimpleNamespace())
        assert loaded["status"] == "ready"
        assert loaded["progress"]["phase"] == "ready"
        assert loaded["plan"]["target_folder"] == "Projects/Demo"
        assert any(event["phase"] == "file_started" for event in loaded["debug_events"])
        assert any(event["phase"] == "ready" for event in loaded["debug_events"])
        assert not os.path.exists(os.path.join(tmpdir, "Projects", "Demo", "00 Projektuebersicht.md"))


@pytest.mark.asyncio
async def test_project_plan_session_apply_marks_created_and_hides_from_active(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)
        monkeypatch.setattr(obsidian_routes, "current_owner", lambda request: "alice")

        request_payload = ProjectPlanRequest(
            target_folder="Projects/Demo",
            title="Demo",
            description="Build a graphable project plan.",
            kind="software",
            generate_content=False,
        )
        created = await obsidian_routes.project_plan_session_create(
            obsidian_routes.ProjectPlanSessionCreateRequest(request=request_payload),
            SimpleNamespace(),
        )
        plan = build_project_plan(tmpdir, request_payload)
        obsidian_routes._update_project_plan_session(
            tmpdir,
            created["id"],
            plan=plan.model_dump() if hasattr(plan, "model_dump") else plan.dict(),
            status="ready",
        )

        result = await obsidian_routes.project_plan_session_apply(
            created["id"],
            obsidian_routes.ProjectPlanSessionApplyRequest(confirm=True),
            SimpleNamespace(),
        )

        assert result["success"] is True
        assert result["session"]["status"] == "created"
        assert any(event["phase"] == "created" for event in result["session"]["debug_events"])
        assert os.path.exists(os.path.join(tmpdir, "Projects", "Demo", "00 Projektuebersicht.md"))
        listed = await obsidian_routes.project_plan_sessions(SimpleNamespace())
        assert listed["sessions"] == []


@pytest.mark.asyncio
async def test_project_plan_tools_preview_apply_and_graph(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)

        templates = await handle_project_plan_templates("", owner="alice")
        assert templates["exit_code"] == 0
        assert "software" in templates["output"]

        preview = await handle_project_plan_preview(json.dumps({
            "target_folder": "Projects/Demo",
            "title": "Demo App",
            "description": "Build a graphable project plan.",
            "kind": "software",
        }), owner="alice")
        assert preview["exit_code"] == 0
        plan = json.loads(preview["output"])
        assert plan["conflicts"] == []
        assert len(plan["files"]) >= 6
        assert "Projects/Demo/00 Projektuebersicht.md" in {item["path"] for item in plan["files"]}

        blocked = await handle_project_plan_apply(json.dumps({"plan": plan}), owner="alice")
        assert blocked["exit_code"] == 1
        assert "Confirmation required" in blocked["error"]

        applied = await handle_project_plan_apply(json.dumps({"plan": plan, "confirm": True}), owner="alice")
        assert applied["exit_code"] == 0
        result = json.loads(applied["output"])
        assert "Projects/Demo/00 Projektuebersicht.md" in result["created_files"]
        assert os.path.exists(os.path.join(tmpdir, "Projects", "Demo", "00 Projektuebersicht.md"))

        graph_res = await handle_graph("{}", owner="alice")
        graph = json.loads(graph_res["output"])["graph"]
        edge_types = {edge["type"] for edge in graph["edges"]}
        assert "wiki_link" in edge_types
        assert "shared_tag" not in edge_types
        assert "depends_on" not in edge_types
        assert "supports" not in edge_types

        history_res = await handle_history('{"limit": 20}', owner="alice")
        assert "obsidian_project_plan_apply" in history_res["output"]

        conflict = await handle_project_plan_preview(json.dumps({
            "target_folder": "Projects/Demo",
            "title": "Demo App",
            "description": "Build again.",
            "kind": "software",
        }), owner="alice")
        conflict_plan = json.loads(conflict["output"])
        assert conflict_plan["conflicts"]
        refused = await handle_project_plan_apply(json.dumps({"plan": conflict_plan, "confirm": True}), owner="alice")
        assert refused["exit_code"] == 1
        assert "conflicts" in refused["output"]

        new_folder_preview = await handle_project_plan_preview(json.dumps({
            "target_folder": f"{NEW_PROJECT_FOLDER_SENTINEL}::",
            "title": "Fresh Project",
            "description": "Create under the vault root only when applied.",
            "kind": "generic",
        }), owner="alice")
        assert new_folder_preview["exit_code"] == 0
        new_folder_plan = json.loads(new_folder_preview["output"])
        assert new_folder_plan["target_folder"] == "fresh-project"
        assert not os.path.exists(os.path.join(tmpdir, "fresh-project"))

        new_folder_apply = await handle_project_plan_apply(json.dumps({"plan": new_folder_plan, "confirm": True}), owner="alice")
        assert new_folder_apply["exit_code"] == 0
        assert os.path.exists(os.path.join(tmpdir, "fresh-project", "00 Projektuebersicht.md"))


@pytest.mark.asyncio
async def test_project_plan_apply_route_conflicts_return_409_before_writes(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects", "Demo"), exist_ok=True)
        existing_path = os.path.join(tmpdir, "Projects", "Demo", "00 Projektuebersicht.md")
        with open(existing_path, "w", encoding="utf-8") as f:
            f.write("# Existing\n")

        request_payload = ProjectPlanRequest(
            target_folder="Projects/Demo",
            title="Demo App",
            description="Build again on top of an existing project.",
            kind="software",
        )
        plan = build_project_plan(tmpdir, request_payload)
        plan_payload = plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
        request = SimpleNamespace(state=SimpleNamespace(api_token=False))

        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)

        def fail_apply(*args, **kwargs):
            raise AssertionError("apply_project_plan should not run when conflicts are present")

        monkeypatch.setattr(obsidian_routes, "apply_project_plan", fail_apply)

        with pytest.raises(HTTPException) as exc:
            await obsidian_routes.project_plan_apply(
                obsidian_routes.ProjectPlanApplyRequest(plan=plan_payload, confirm=True),
                request,
            )

        assert exc.value.status_code == 409
        assert exc.value.detail["message"] == "Plan has file conflicts"
        assert exc.value.detail["conflicts"]
        with open(existing_path, "r", encoding="utf-8") as f:
            assert f.read() == "# Existing\n"
        assert not os.path.exists(os.path.join(tmpdir, ".obsidian", "history.json"))
