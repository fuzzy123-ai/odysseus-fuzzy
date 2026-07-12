from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]


def test_assistant_attachments_render_in_standard_and_agent_history_paths():
    renderer = (_ROOT / "static/js/chatRenderer.js").read_text(encoding="utf-8")

    assert "if (attachments?.length) {\n      b.appendChild(buildAttachCards(attachments));\n    }" in renderer
    assert "if (metadata?.attachments?.length)" in renderer
    assert "artifactBody.appendChild(buildAttachCards(metadata.attachments))" in renderer


def test_download_url_is_derived_only_from_valid_upload_id():
    renderer = (_ROOT / "static/js/chatRenderer.js").read_text(encoding="utf-8")

    assert "function _safeAttachmentId(value)" in renderer
    assert "link.href = `/api/upload/${id}`" in renderer
    assert "att.download_url" not in renderer
    assert "button.textContent = 'Download'" in renderer


def test_assistant_image_caption_editor_cannot_regenerate_assistant_message():
    renderer = (_ROOT / "static/js/chatRenderer.js").read_text(encoding="utf-8")

    assert "message?.classList.contains('msg-user') ? message : null" in renderer
    assert "regenBtn.disabled = !userMsgEl" in renderer


def test_live_metrics_attach_generated_files_to_final_assistant_bubble():
    chat = (_ROOT / "static/js/chat.js").read_text(encoding="utf-8")

    assert "if (metrics?.attachments?.length)" in chat
    assert "chatRenderer.updateMessageAttachments(footerTarget, metrics.attachments)" in chat
