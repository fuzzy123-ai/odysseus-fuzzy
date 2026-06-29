import json

from src.universal_export import (
    build_universal_export_plan,
    build_universal_export_plan_from_intent,
    parse_universal_export_intent,
)


def test_parses_recent_inbox_file_to_pdf_intent():
    intent = parse_universal_export_intent("Mach daraus bitte ein PDF.")
    payload = intent.to_dict()

    assert payload["status"] == "ready"
    assert payload["target_format"] == "pdf"
    assert payload["input_ref"] == "last_inbox_file"
    assert payload["action"] == "document_export"
    assert payload["raw_content_visible"] is False


def test_intent_blocks_when_no_recent_file_exists():
    intent = parse_universal_export_intent(
        "Schick mir das als PNG zurueck",
        recent_input_available=False,
    ).to_dict()

    assert intent["status"] == "blocked"
    assert intent["target_format"] == "png"
    assert intent["reason"] == "recent_input_missing"


def test_builds_docx_to_pdf_export_plan_without_executing_tool():
    plan = build_universal_export_plan("letter.docx", "pdf").to_dict()

    assert plan["status"] == "planned"
    assert plan["source_family"] == "document"
    assert plan["source_suffix"] == ".docx"
    assert plan["target_format"] == "pdf"
    assert plan["required_tool"] == "libreoffice_or_pandoc"
    assert plan["executable_now"] is False
    assert plan["raw_content_visible"] is False


def test_builds_text_to_pdf_export_plan_as_builtin_ready():
    plan = build_universal_export_plan("notes.md", "pdf").to_dict()

    assert plan["status"] == "ready"
    assert plan["source_family"] == "text"
    assert plan["source_suffix"] == ".md"
    assert plan["target_format"] == "pdf"
    assert plan["required_tool"] == "builtin_text_pdf"
    assert plan["executable_now"] is True
    assert plan["raw_content_visible"] is False


def test_builds_pdf_to_png_page_render_plan():
    plan = build_universal_export_plan("scan.pdf", "png").to_dict()

    assert plan["status"] == "planned"
    assert plan["action"] == "render_pages"
    assert plan["required_tool"] == "poppler_or_mupdf"
    assert "page_render_review_required" in plan["reason_codes"]


def test_builds_image_and_audio_conversion_plans():
    image = build_universal_export_plan("photo.heic", "jpg").to_dict()
    audio = build_universal_export_plan("voice.ogg", "wav").to_dict()

    assert image["action"] == "image_convert"
    assert image["required_tool"] == "pillow"
    assert audio["action"] == "audio_transcode"
    assert audio["required_tool"] == "ffmpeg"


def test_builds_gamedev_asset_conversion_and_preview_plans():
    glb = build_universal_export_plan("character.blend", "glb").to_dict()
    preview = build_universal_export_plan("character.fbx", "png").to_dict()

    assert glb["source_family"] == "asset"
    assert glb["action"] == "asset_convert"
    assert glb["required_tool"] == "blender_or_assimp"
    assert preview["action"] == "render_preview"
    assert preview["required_tool"] == "blender_or_godot"


def test_dsgvo_forces_local_only_in_export_plan():
    plan = build_universal_export_plan("letter.docx", "pdf", dsgvo_mode=True).to_dict()

    assert plan["local_only"] is True
    assert plan["review_required"] is True


def test_builds_plan_from_ready_intent_and_keeps_report_redacted():
    intent = parse_universal_export_intent("wandle es in webp um")
    plan = build_universal_export_plan_from_intent("photo.png", intent).to_dict()
    encoded = json.dumps(plan, sort_keys=True)

    assert plan["target_format"] == "webp"
    assert plan["action"] == "image_convert"
    assert "raw_text" not in encoded
    assert "PRIVATE RAW TEXT" not in encoded


def test_unsupported_conversion_is_blocked():
    plan = build_universal_export_plan("voice.mp3", "fbx").to_dict()

    assert plan["status"] == "blocked"
    assert plan["reason"] == "conversion_not_supported"
    assert plan["required_tool"] == ""
