import json

import pytest

from src.universal_file_io import (
    UniversalFileIOError,
    build_export_plan,
    build_file_capability_registry,
    get_file_capability,
    parse_export_intent,
    summarize_file_capabilities,
)


def test_file_capability_registry_covers_common_families():
    registry = build_file_capability_registry()
    summary = summarize_file_capabilities([".docx", ".pdf", ".png", ".mp3", ".mp4", ".glb"])

    assert registry[".docx"].family == "document"
    assert registry[".pdf"].export_targets == ("txt", "png", "jpg", "searchable_pdf")
    assert registry[".glb"].family == "asset_3d"
    assert summary["families"] == {
        "asset_3d": 1,
        "audio": 1,
        "document": 1,
        "image": 1,
        "pdf": 1,
        "video": 1,
    }
    assert summary["raw_content_visible"] is False


def test_export_intent_references_recent_inbox_file_without_raw_request():
    intent = parse_export_intent(
        "Mach daraus bitte ein PDF und schick es spaeter zurueck",
        recent_source_ref="inbox:abc123",
    )

    payload = intent.to_dict()

    assert intent.target_format == "pdf"
    assert payload["source_ref"] == "inbox:abc123"
    assert payload["request_hash"].startswith("sha256:")
    assert payload["raw_request_visible"] is False
    assert "Mach daraus" not in json.dumps(payload)


def test_export_intent_hashes_unsafe_source_refs():
    intent = parse_export_intent(
        "als png",
        recent_source_ref="C:/Users/name/private/source.docx",
    )

    assert intent.source_ref.startswith("sha256:")
    assert "C:/Users" not in json.dumps(intent.to_dict())


def test_document_to_pdf_plan_is_redacted_and_non_executing():
    intent = parse_export_intent("mach daraus pdf", recent_source_ref="inbox:file1")
    plan = build_export_plan(intent, source_name_or_extension="brief.docx")
    payload = plan.to_dict()

    assert plan.status == "needs_review"
    assert plan.source_family == "document"
    assert plan.target_format == "pdf"
    assert plan.required_tool == "libreoffice_or_markitdown"
    assert plan.live_execution_allowed is False
    assert plan.delivery_allowed is False
    assert payload["raw_content_visible"] is False


def test_pdf_to_page_image_plan_uses_pdf_renderer():
    intent = parse_export_intent("erstelle ein seitenbild als png", recent_source_ref="inbox:pdf1")
    plan = build_export_plan(intent, source_name_or_extension=".pdf")

    assert plan.status == "needs_review"
    assert plan.source_family == "pdf"
    assert plan.target_format == "png"
    assert plan.required_tool == "pdf_page_renderer"


def test_image_and_audio_conversion_plans_remain_gated():
    image_intent = parse_export_intent("convert to jpg", recent_source_ref="inbox:image1")
    audio_intent = parse_export_intent("mach wav", recent_source_ref="inbox:audio1")

    image_plan = build_export_plan(image_intent, source_name_or_extension="photo.png")
    audio_plan = build_export_plan(audio_intent, source_name_or_extension="voice.mp3")

    assert image_plan.status == "plan_ready"
    assert image_plan.required_tool == "pillow"
    assert audio_plan.status == "needs_review"
    assert audio_plan.required_tool == "ffmpeg"
    assert audio_plan.live_execution_allowed is False
    assert audio_plan.delivery_allowed is False


def test_3d_asset_plan_is_supported_but_review_required():
    intent = parse_export_intent("exportiere das als fbx", recent_source_ref="inbox:model1")
    plan = build_export_plan(intent, source_name_or_extension="ship.glb")

    assert plan.status == "needs_review"
    assert plan.source_family == "asset_3d"
    assert plan.target_format == "fbx"
    assert plan.required_tool == "blender_or_assimp"


def test_unsupported_target_and_source_are_explicit_blockers():
    unsupported_target = parse_export_intent("mach daraus exe", recent_source_ref="inbox:file1")
    unsupported_source = parse_export_intent("mach daraus pdf", recent_source_ref="inbox:file2")

    target_plan = build_export_plan(unsupported_target, source_name_or_extension="notes.txt")
    source_plan = build_export_plan(unsupported_source, source_name_or_extension="bundle.unknown")

    assert target_plan.status == "unsupported"
    assert "export target could not be detected from the follow-up request" in target_plan.blockers
    assert source_plan.status == "unsupported"
    assert "source file type is not supported by Universal File IO" in source_plan.blockers


def test_dsgvo_mode_forces_local_only_plan_and_warning():
    intent = parse_export_intent("mach daraus pdf", recent_source_ref="inbox:private1", dsgvo_mode=True)
    plan = build_export_plan(intent, source_name_or_extension="vertrag.docx")

    assert intent.local_only is True
    assert plan.local_only is True
    assert "DSGVO mode forces local-only converter selection" in plan.warnings
    assert "enforce local-only processing before tool selection" in plan.steps


def test_source_ref_is_required():
    with pytest.raises(UniversalFileIOError):
        parse_export_intent("mach pdf", recent_source_ref="")


def test_get_file_capability_accepts_names_or_extensions():
    assert get_file_capability("report.PDF").family == "pdf"
    assert get_file_capability(".OBJ").family == "asset_3d"
