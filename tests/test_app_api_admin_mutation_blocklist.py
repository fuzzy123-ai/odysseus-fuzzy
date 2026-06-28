import json

import pytest

from src.tool_implementations import do_app_api


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "tool_name"),
    [
        ("POST", "/api/model-endpoints", "manage_endpoints"),
        ("PATCH", "/api/model-endpoints/ep1", "manage_endpoints"),
        ("DELETE", "/api/model-endpoints/ep1", "manage_endpoints"),
        ("POST", "/api/webhooks", "manage_webhooks"),
        ("PATCH", "/api/webhooks/wh1", "manage_webhooks"),
        ("DELETE", "/api/webhooks/wh1", "manage_webhooks"),
        ("POST", "/api/mcp/servers", "manage_mcp"),
        ("PUT", "/api/mcp/servers/srv1", "manage_mcp"),
        ("PATCH", "/api/mcp/servers/srv1", "manage_mcp"),
        ("DELETE", "/api/mcp/servers/srv1", "manage_mcp"),
        ("POST", "/api/gallery/upload", "Gallery UI"),
        ("POST", "/api/gallery/img1/replace", "Gallery UI"),
        ("POST", "/api/gallery/img1/rename", "Gallery UI"),
        ("PATCH", "/api/gallery/img1", "Gallery UI"),
        ("PUT", "/api/gallery/albums/album1", "Gallery UI"),
        ("DELETE", "/api/gallery/img1", "Gallery UI"),
        ("POST", "/api/document", "manage_documents"),
        ("POST", "/api/document/doc1/archive", "manage_documents"),
        ("PUT", "/api/document/doc1", "manage_documents"),
        ("PATCH", "/api/document/doc1", "manage_documents"),
        ("DELETE", "/api/document/doc1", "manage_documents"),
        ("POST", "/api/documents/import-pdf", "manage_documents"),
        ("POST", "/api/documents/export-zip", "manage_documents"),
        ("POST", "/api/documents/tidy", "manage_documents"),
        ("POST", "/api/documents/ai-tidy", "manage_documents"),
        ("DELETE", "/api/research/report1", "manage_research"),
        ("POST", "/api/tasks", "manage_tasks"),
        ("PUT", "/api/tasks/task1", "manage_tasks"),
        ("PATCH", "/api/tasks/task1", "manage_tasks"),
        ("DELETE", "/api/tasks/task1", "manage_tasks"),
        ("POST", "/api/session", "manage_session"),
        ("PATCH", "/api/session/s1", "manage_session"),
        ("DELETE", "/api/session/s1", "manage_session"),
        ("POST", "/api/session/s1/archive", "manage_session"),
        ("POST", "/api/session/s1/compact", "manage_session"),
        ("POST", "/api/sessions/bulk-delete", "manage_session"),
        ("DELETE", "/api/sessions/all", "manage_session"),
        ("POST", "/api/sessions/auto-sort", "manage_session"),
        ("POST", "/api/notes", "manage_notes"),
        ("PUT", "/api/notes/note1", "manage_notes"),
        ("DELETE", "/api/notes/note1", "manage_notes"),
        ("POST", "/api/notes/note1/pin", "manage_notes"),
        ("POST", "/api/calendar/events", "manage_calendar"),
        ("PUT", "/api/calendar/events/evt1", "manage_calendar"),
        ("DELETE", "/api/calendar/events/evt1", "manage_calendar"),
        ("POST", "/api/calendar/config/accounts", "Calendar UI"),
        ("PUT", "/api/calendar/calendars/cal1", "Calendar UI"),
        ("PUT", "/api/prefs/theme", "manage_settings"),
        ("POST", "/api/prefs/custom-themes", "manage_settings"),
        ("PATCH", "/api/prefs/custom-themes", "manage_settings"),
        ("DELETE", "/api/prefs/custom-themes", "manage_settings"),
        ("POST", "/api/memory/add", "manage_memory"),
        ("POST", "/api/memory/search", "manage_memory"),
        ("POST", "/api/memory/import", "Memory UI"),
        ("POST", "/api/memory/mem1/pin", "Memory UI"),
        ("PUT", "/api/memory/mem1", "manage_memory"),
        ("DELETE", "/api/memory/mem1", "manage_memory"),
        ("POST", "/api/contacts/add", "manage_contact"),
        ("PUT", "/api/contacts/contact1", "manage_contact"),
        ("DELETE", "/api/contacts/contact1", "manage_contact"),
        ("POST", "/api/contacts/import", "Contacts UI"),
        ("PUT", "/api/contacts/config", "Contacts UI"),
        ("DELETE", "/api/contacts/clear", "Contacts UI"),
        ("POST", "/api/email/send", "send_email"),
        ("POST", "/api/email/ai-reply", "reply_to_email"),
        ("POST", "/api/email/archive/123", "archive_email"),
        ("POST", "/api/email/mark-read/123", "mark_email_read"),
        ("DELETE", "/api/email/delete/123", "delete_email"),
        ("POST", "/api/email/schedule", "named email tools"),
        ("DELETE", "/api/email/scheduled/s1", "named email tools"),
        ("POST", "/api/email/pending/s1/approve", "staged-send"),
        ("PUT", "/api/email/config", "named email tools"),
        ("POST", "/api/email/accounts", "Email Settings UI"),
        ("PUT", "/api/email/accounts/acct1", "Email Settings UI"),
        ("DELETE", "/api/email/accounts/acct1", "Email Settings UI"),
        ("POST", "/api/skills/add", "manage_skills"),
        ("POST", "/api/skills/import-from-url", "manage_skills"),
        ("POST", "/api/skills/search", "manage_skills"),
        ("POST", "/api/skills/skill1/test", "Skills UI"),
        ("POST", "/api/skills/audit-all", "Skills UI"),
        ("POST", "/api/skills/skill1/markdown", "manage_skills"),
        ("PUT", "/api/skills/skill1", "manage_skills"),
        ("DELETE", "/api/skills/skill1", "manage_skills"),
        ("PATCH", "/api/assistant/settings", "manage_assistant"),
        ("POST", "/api/assistant/run/task1", "manage_assistant"),
        ("POST", "/api/chat", "normal chat UI"),
        ("POST", "/api/chat_stream", "normal chat UI"),
        ("POST", "/api/chat/stop/s1", "normal chat UI"),
        ("POST", "/api/inject_context/s1", "normal chat UI"),
        ("POST", "/api/rewrite", "normal chat UI"),
        ("POST", "/api/codex/todos", "native named tools"),
        ("POST", "/api/codex/emails/send", "native named tools"),
        ("POST", "/api/codex/memory", "native named tools"),
        ("POST", "/api/codex/calendar/events", "native named tools"),
        ("POST", "/api/codex/documents", "native named tools"),
        ("DELETE", "/api/codex/documents/doc1", "native named tools"),
        ("POST", "/api/codex/cookbook/serve", "native named tools"),
        ("POST", "/api/codex/cookbook/preset/foo", "native named tools"),
        ("POST", "/api/embeddings/models/BAAI/bge-small-en-v1.5/download", "Embedding Settings UI"),
        ("DELETE", "/api/embeddings/models/BAAI/bge-small-en-v1.5", "Embedding Settings UI"),
        ("POST", "/api/embeddings/endpoint", "Embedding Settings UI"),
        ("DELETE", "/api/embeddings/endpoint", "Embedding Settings UI"),
        ("GET", "/api/upload/stats", "attachment UI"),
        ("GET", "/api/upload/file1", "attachment UI"),
        ("GET", "/api/upload/file1/vision", "attachment UI"),
        ("POST", "/api/upload", "attachment UI"),
        ("POST", "/api/upload/cleanup", "attachment UI"),
        ("PUT", "/api/upload/file1/vision", "attachment UI"),
        ("GET", "/api/signatures", "Signature/Documents UI"),
        ("POST", "/api/signatures", "Signature/Documents UI"),
        ("DELETE", "/api/signatures/sig1", "Signature/Documents UI"),
        ("POST", "/api/presets/custom", "Presets UI"),
        ("POST", "/api/presets/templates", "Presets UI"),
        ("DELETE", "/api/presets/templates/t1", "Presets UI"),
        ("POST", "/api/presets/groups", "Presets UI"),
        ("GET", "/api/editor-drafts", "Gallery Editor UI"),
        ("GET", "/api/editor-drafts/d1", "Gallery Editor UI"),
        ("POST", "/api/editor-drafts", "Gallery Editor UI"),
        ("PUT", "/api/editor-drafts/d1", "Gallery Editor UI"),
        ("DELETE", "/api/editor-drafts/d1", "Gallery Editor UI"),
        ("POST", "/api/cleanup", "manage_session"),
        ("POST", "/api/plugins/telegram/reply", "Plugins UI"),
        ("POST", "/api/plugins/rescan", "Plugins UI"),
        ("POST", "/api/plugins/install", "Plugins UI"),
        ("POST", "/api/plugins/telegram/enable", "Plugins UI"),
        ("DELETE", "/api/plugins/registries", "Plugins UI"),
        ("POST", "/api/personal/reload", "Personal Docs UI"),
        ("POST", "/api/personal/add_directory", "Personal Docs UI"),
        ("POST", "/api/personal/upload", "Personal Docs UI"),
        ("DELETE", "/api/personal/remove_directory", "Personal Docs UI"),
        ("DELETE", "/api/personal/file", "Personal Docs UI"),
    ],
)
async def test_app_api_blocks_admin_mutations_before_loopback(method, path, tool_name, monkeypatch):
    import httpx

    class ForbiddenClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("app_api should block this route before loopback")

    monkeypatch.setattr(httpx, "AsyncClient", ForbiddenClient)

    result = await do_app_api(
        json.dumps({"method": method, "path": path, "body": {"unsafe": True}}),
        owner="admin",
    )

    assert result["exit_code"] == 1
    assert tool_name in result["error"]


@pytest.mark.asyncio
async def test_app_api_discovery_hides_destructive_data_mutations(monkeypatch):
    import httpx

    class FakeResponse:
        def json(self):
            return {
                "paths": {
                    "/api/gallery/library": {"get": {"summary": "Gallery Library"}},
                    "/api/gallery/upload": {"post": {"summary": "Upload Image"}},
                    "/api/gallery/{image_id}": {
                        "get": {"summary": "Read Image"},
                        "patch": {"summary": "Patch Image"},
                        "delete": {"summary": "Delete Image"},
                    },
                    "/api/gallery/{image_id}/replace": {"post": {"summary": "Replace Image"}},
                    "/api/gallery/albums/{album_id}": {"put": {"summary": "Update Album"}},
                    "/api/document/{doc_id}": {
                        "get": {"summary": "Read Document"},
                        "put": {"summary": "Update Document"},
                        "patch": {"summary": "Patch Document"},
                        "delete": {"summary": "Delete Document"},
                    },
                    "/api/document": {"post": {"summary": "Create Document"}},
                    "/api/document/{doc_id}/archive": {"post": {"summary": "Archive Document"}},
                    "/api/documents/import-pdf": {"post": {"summary": "Import PDF"}},
                    "/api/documents/export-zip": {"post": {"summary": "Export Zip"}},
                    "/api/documents/tidy": {"post": {"summary": "Tidy Documents"}},
                    "/api/research/{session_id}": {"delete": {"summary": "Delete Research"}},
                    "/api/tasks/notifications": {"get": {"summary": "Task Notifications"}},
                    "/api/tasks": {"post": {"summary": "Create Task"}},
                    "/api/tasks/{task_id}": {
                        "put": {"summary": "Update Task"},
                        "delete": {"summary": "Delete Task"},
                    },
                    "/api/sessions": {
                        "get": {"summary": "List Sessions"},
                    },
                    "/api/session/{sid}": {
                        "get": {"summary": "Read Session"},
                        "patch": {"summary": "Patch Session"},
                        "delete": {"summary": "Delete Session"},
                    },
                    "/api/session/{sid}/archive": {"post": {"summary": "Archive Session"}},
                    "/api/session/{sid}/compact": {"post": {"summary": "Compact Session"}},
                    "/api/sessions/bulk-delete": {"post": {"summary": "Bulk Delete Sessions"}},
                    "/api/sessions/all": {"delete": {"summary": "Delete All Sessions"}},
                    "/api/notes": {
                        "get": {"summary": "List Notes"},
                        "post": {"summary": "Create Note"},
                    },
                    "/api/notes/{note_id}": {
                        "get": {"summary": "Read Note"},
                        "put": {"summary": "Update Note"},
                        "delete": {"summary": "Delete Note"},
                    },
                    "/api/notes/{note_id}/pin": {"post": {"summary": "Pin Note"}},
                    "/api/calendar/events": {
                        "get": {"summary": "List Events"},
                        "post": {"summary": "Create Event"},
                    },
                    "/api/calendar/events/{uid}": {
                        "get": {"summary": "Read Event"},
                        "put": {"summary": "Update Event"},
                        "delete": {"summary": "Delete Event"},
                    },
                    "/api/calendar/calendars": {
                        "get": {"summary": "List Calendars"},
                        "post": {"summary": "Create Calendar"},
                    },
                    "/api/calendar/config/accounts": {
                        "get": {"summary": "List Calendar Accounts"},
                        "post": {"summary": "Add Calendar Account"},
                    },
                    "/api/prefs": {
                        "get": {"summary": "List Preferences"},
                    },
                    "/api/prefs/{key}": {
                        "get": {"summary": "Read Preference"},
                        "put": {"summary": "Set Preference"},
                    },
                    "/api/prefs/custom-themes": {
                        "get": {"summary": "Read Custom Themes"},
                        "post": {"summary": "Create Custom Theme"},
                        "patch": {"summary": "Patch Custom Themes"},
                        "delete": {"summary": "Delete Custom Themes"},
                    },
                    "/api/memory": {
                        "get": {"summary": "List Memories"},
                    },
                    "/api/memory/add": {"post": {"summary": "Add Memory"}},
                    "/api/memory/search": {"post": {"summary": "Search Memories"}},
                    "/api/memory/import": {"post": {"summary": "Import Memories"}},
                    "/api/memory/{memory_id}": {
                        "get": {"summary": "Read Memory"},
                        "put": {"summary": "Update Memory"},
                        "delete": {"summary": "Delete Memory"},
                    },
                    "/api/memory/{memory_id}/pin": {"post": {"summary": "Pin Memory"}},
                    "/api/contacts/list": {"get": {"summary": "List Contacts"}},
                    "/api/contacts/search": {"get": {"summary": "Search Contacts"}},
                    "/api/contacts/export": {"get": {"summary": "Export Contacts"}},
                    "/api/contacts/add": {"post": {"summary": "Add Contact"}},
                    "/api/contacts/import": {"post": {"summary": "Import Contacts"}},
                    "/api/contacts/config": {
                        "get": {"summary": "Get Contacts Config"},
                        "put": {"summary": "Update Contacts Config"},
                    },
                    "/api/contacts/{uid}": {
                        "put": {"summary": "Update Contact"},
                        "delete": {"summary": "Delete Contact"},
                    },
                    "/api/contacts/clear": {"delete": {"summary": "Clear Contacts"}},
                    "/api/email/list": {"get": {"summary": "List Email"}},
                    "/api/email/read/{uid}": {"get": {"summary": "Read Email"}},
                    "/api/email/attachments/{uid}": {"get": {"summary": "List Attachments"}},
                    "/api/email/accounts": {
                        "get": {"summary": "List Email Accounts"},
                        "post": {"summary": "Add Email Account"},
                    },
                    "/api/email/accounts/{account_id}": {
                        "put": {"summary": "Update Email Account"},
                        "delete": {"summary": "Delete Email Account"},
                    },
                    "/api/email/send": {"post": {"summary": "Send Email"}},
                    "/api/email/ai-reply": {"post": {"summary": "AI Reply"}},
                    "/api/email/archive/{uid}": {"post": {"summary": "Archive Email"}},
                    "/api/email/mark-read/{uid}": {"post": {"summary": "Mark Read"}},
                    "/api/email/delete/{uid}": {"delete": {"summary": "Delete Email"}},
                    "/api/email/schedule": {"post": {"summary": "Schedule Email"}},
                    "/api/email/scheduled/{sid}": {"delete": {"summary": "Cancel Scheduled Email"}},
                    "/api/email/pending/{sid}/approve": {"post": {"summary": "Approve Pending Email"}},
                    "/api/email/config": {
                        "get": {"summary": "Read Email Config"},
                        "put": {"summary": "Update Email Config"},
                    },
                    "/api/skills": {"get": {"summary": "List Skills"}},
                    "/api/skills/index": {"get": {"summary": "Skills Index"}},
                    "/api/skills/{skill_id}": {
                        "get": {"summary": "Read Skill"},
                        "put": {"summary": "Update Skill"},
                        "delete": {"summary": "Delete Skill"},
                    },
                    "/api/skills/{skill_id}/markdown": {
                        "get": {"summary": "Read Skill Markdown"},
                        "post": {"summary": "Save Skill Markdown"},
                    },
                    "/api/skills/add": {"post": {"summary": "Add Skill"}},
                    "/api/skills/import-from-url": {"post": {"summary": "Import Skill"}},
                    "/api/skills/search": {"post": {"summary": "Search Skills"}},
                    "/api/skills/{skill_id}/test": {"post": {"summary": "Test Skill"}},
                    "/api/skills/audit-all": {"post": {"summary": "Audit All Skills"}},
                    "/api/assistant/session": {"get": {"summary": "Assistant Session"}},
                    "/api/assistant/settings": {
                        "get": {"summary": "Read Assistant Settings"},
                        "patch": {"summary": "Update Assistant Settings"},
                    },
                    "/api/assistant/run/{task_id}": {"post": {"summary": "Run Assistant Task"}},
                    "/api/assistant/run-status/{task_id}": {"get": {"summary": "Assistant Run Status"}},
                    "/api/chat": {"post": {"summary": "Chat"}},
                    "/api/chat_stream": {"post": {"summary": "Chat Stream"}},
                    "/api/chat/stop/{session_id}": {"post": {"summary": "Stop Chat"}},
                    "/api/chat/stream_status/{session_id}": {"get": {"summary": "Chat Stream Status"}},
                    "/api/chat/run_ledger/{session_id}": {"get": {"summary": "Chat Run Ledger"}},
                    "/api/chat/mission/{session_id}": {"get": {"summary": "Chat Mission"}},
                    "/api/inject_context/{session_id}": {"post": {"summary": "Inject Context"}},
                    "/api/rewrite": {"post": {"summary": "Rewrite Message"}},
                    "/api/codex/todos": {
                        "get": {"summary": "List Codex Todos"},
                        "post": {"summary": "Mutate Codex Todos"},
                    },
                    "/api/codex/emails": {"get": {"summary": "List Codex Emails"}},
                    "/api/codex/emails/{uid}": {"get": {"summary": "Read Codex Email"}},
                    "/api/codex/emails/send": {"post": {"summary": "Send Codex Email"}},
                    "/api/codex/memory": {
                        "get": {"summary": "List Codex Memory"},
                        "post": {"summary": "Add Codex Memory"},
                    },
                    "/api/codex/memory/{memory_id}": {"delete": {"summary": "Delete Codex Memory"}},
                    "/api/codex/calendar/events": {
                        "get": {"summary": "List Codex Calendar Events"},
                        "post": {"summary": "Create Codex Calendar Event"},
                    },
                    "/api/codex/calendar/events/{uid}": {"delete": {"summary": "Delete Codex Calendar Event"}},
                    "/api/codex/documents": {
                        "get": {"summary": "List Codex Documents"},
                        "post": {"summary": "Create Codex Document"},
                    },
                    "/api/codex/documents/{doc_id}": {
                        "get": {"summary": "Read Codex Document"},
                        "delete": {"summary": "Delete Codex Document"},
                    },
                    "/api/codex/cookbook/tasks": {"get": {"summary": "List Codex Cookbook Tasks"}},
                    "/api/codex/cookbook/serve": {"post": {"summary": "Serve Codex Cookbook Model"}},
                    "/api/codex/cookbook/preset/{name}": {"post": {"summary": "Serve Codex Cookbook Preset"}},
                    "/api/embeddings/models": {"get": {"summary": "List Embedding Models"}},
                    "/api/embeddings/models/{model_name}/download": {"post": {"summary": "Download Embedding Model"}},
                    "/api/embeddings/models/{model_name}/status": {"get": {"summary": "Embedding Model Status"}},
                    "/api/embeddings/models/{model_name}": {"delete": {"summary": "Delete Embedding Model"}},
                    "/api/embeddings/endpoint": {
                        "get": {"summary": "Get Embedding Endpoint"},
                        "post": {"summary": "Set Embedding Endpoint"},
                        "delete": {"summary": "Clear Embedding Endpoint"},
                    },
                    "/api/upload": {"post": {"summary": "Upload Attachment"}},
                    "/api/upload/cleanup": {"post": {"summary": "Cleanup Uploads"}},
                    "/api/upload/stats": {"get": {"summary": "Upload Stats"}},
                    "/api/upload/{file_id}": {"get": {"summary": "Download Upload"}},
                    "/api/upload/{file_id}/vision": {
                        "get": {"summary": "Get Vision Text"},
                        "put": {"summary": "Set Vision Text"},
                    },
                    "/api/signatures": {
                        "get": {"summary": "List Signatures"},
                        "post": {"summary": "Create Signature"},
                    },
                    "/api/signatures/{sig_id}": {"delete": {"summary": "Delete Signature"}},
                    "/api/presets": {"get": {"summary": "List Presets"}},
                    "/api/presets/custom": {"post": {"summary": "Update Custom Preset"}},
                    "/api/presets/templates": {
                        "get": {"summary": "List Preset Templates"},
                        "post": {"summary": "Save Preset Template"},
                    },
                    "/api/presets/templates/{template_id}": {"delete": {"summary": "Delete Preset Template"}},
                    "/api/presets/expand": {"post": {"summary": "Expand Preset Prompt"}},
                    "/api/presets/groups": {
                        "get": {"summary": "List Preset Groups"},
                        "post": {"summary": "Save Preset Groups"},
                    },
                    "/api/editor-drafts": {
                        "get": {"summary": "List Editor Drafts"},
                        "post": {"summary": "Create Editor Draft"},
                    },
                    "/api/editor-drafts/{draft_id}": {
                        "get": {"summary": "Get Editor Draft"},
                        "put": {"summary": "Update Editor Draft"},
                        "delete": {"summary": "Delete Editor Draft"},
                    },
                    "/api/cleanup/preview": {"get": {"summary": "Cleanup Preview"}},
                    "/api/cleanup": {"post": {"summary": "Run Cleanup"}},
                    "/api/plugins": {"get": {"summary": "List Plugins"}},
                    "/api/plugins/registry": {"get": {"summary": "Plugin Registry"}},
                    "/api/plugins/registries": {
                        "get": {"summary": "List Plugin Registries"},
                        "post": {"summary": "Add Plugin Registry"},
                        "delete": {"summary": "Delete Plugin Registry"},
                    },
                    "/api/plugins/rescan": {"post": {"summary": "Rescan Plugins"}},
                    "/api/plugins/install": {"post": {"summary": "Install Plugin"}},
                    "/api/plugins/{plugin_id}/enable": {"post": {"summary": "Enable Plugin"}},
                    "/api/plugins/{plugin_id}/status": {"get": {"summary": "Plugin Status"}},
                    "/api/plugins/{plugin_id}/reply": {"post": {"summary": "Plugin Reply"}},
                    "/api/personal": {"get": {"summary": "List Personal Documents"}},
                    "/api/personal/reload": {"post": {"summary": "Reload Personal Documents"}},
                    "/api/personal/add_directory": {"post": {"summary": "Add Personal Directory"}},
                    "/api/personal/upload": {"post": {"summary": "Upload Personal Document"}},
                    "/api/personal/remove_directory": {"delete": {"summary": "Remove Personal Directory"}},
                    "/api/personal/file": {"delete": {"summary": "Delete Personal File"}},
                }
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    result = await do_app_api(json.dumps({"action": "endpoints"}), owner="admin")

    assert result["exit_code"] == 0
    paths = {(endpoint["method"], endpoint["path"]) for endpoint in result["endpoints"]}
    assert ("GET", "/api/gallery/library") in paths
    assert ("GET", "/api/gallery/{image_id}") in paths
    assert ("GET", "/api/document/{doc_id}") in paths
    assert ("GET", "/api/tasks/notifications") in paths
    assert ("GET", "/api/sessions") in paths
    assert ("GET", "/api/session/{sid}") in paths
    assert ("GET", "/api/notes") in paths
    assert ("GET", "/api/notes/{note_id}") in paths
    assert ("GET", "/api/calendar/events") in paths
    assert ("GET", "/api/calendar/events/{uid}") in paths
    assert ("GET", "/api/calendar/calendars") in paths
    assert ("GET", "/api/calendar/config/accounts") in paths
    assert ("GET", "/api/prefs") in paths
    assert ("GET", "/api/prefs/{key}") in paths
    assert ("GET", "/api/prefs/custom-themes") in paths
    assert ("GET", "/api/memory") in paths
    assert ("GET", "/api/memory/{memory_id}") in paths
    assert ("GET", "/api/contacts/list") in paths
    assert ("GET", "/api/contacts/search") in paths
    assert ("GET", "/api/contacts/export") in paths
    assert ("GET", "/api/contacts/config") in paths
    assert ("GET", "/api/email/list") in paths
    assert ("GET", "/api/email/read/{uid}") in paths
    assert ("GET", "/api/email/attachments/{uid}") in paths
    assert ("GET", "/api/email/config") in paths
    assert ("GET", "/api/skills") in paths
    assert ("GET", "/api/skills/index") in paths
    assert ("GET", "/api/skills/{skill_id}") in paths
    assert ("GET", "/api/skills/{skill_id}/markdown") in paths
    assert ("GET", "/api/assistant/session") in paths
    assert ("GET", "/api/assistant/settings") in paths
    assert ("GET", "/api/assistant/run-status/{task_id}") in paths
    assert ("GET", "/api/chat/stream_status/{session_id}") in paths
    assert ("GET", "/api/chat/run_ledger/{session_id}") in paths
    assert ("GET", "/api/chat/mission/{session_id}") in paths
    assert ("GET", "/api/codex/todos") in paths
    assert ("GET", "/api/codex/emails") in paths
    assert ("GET", "/api/codex/emails/{uid}") in paths
    assert ("GET", "/api/codex/memory") in paths
    assert ("GET", "/api/codex/calendar/events") in paths
    assert ("GET", "/api/codex/documents") in paths
    assert ("GET", "/api/codex/documents/{doc_id}") in paths
    assert ("GET", "/api/codex/cookbook/tasks") in paths
    assert ("GET", "/api/embeddings/models") in paths
    assert ("GET", "/api/embeddings/models/{model_name}/status") in paths
    assert ("GET", "/api/embeddings/endpoint") in paths
    assert ("GET", "/api/plugins") in paths
    assert ("GET", "/api/plugins/registry") in paths
    assert ("GET", "/api/plugins/registries") in paths
    assert ("GET", "/api/plugins/{plugin_id}/status") in paths
    assert ("GET", "/api/presets") in paths
    assert ("GET", "/api/presets/templates") in paths
    assert ("POST", "/api/presets/expand") in paths
    assert ("GET", "/api/presets/groups") in paths
    assert ("GET", "/api/cleanup/preview") in paths
    assert ("GET", "/api/personal") in paths
    assert ("POST", "/api/gallery/upload") not in paths
    assert ("PATCH", "/api/gallery/{image_id}") not in paths
    assert ("DELETE", "/api/gallery/{image_id}") not in paths
    assert ("POST", "/api/gallery/{image_id}/replace") not in paths
    assert ("PUT", "/api/gallery/albums/{album_id}") not in paths
    assert ("POST", "/api/document") not in paths
    assert ("POST", "/api/document/{doc_id}/archive") not in paths
    assert ("PUT", "/api/document/{doc_id}") not in paths
    assert ("PATCH", "/api/document/{doc_id}") not in paths
    assert ("DELETE", "/api/document/{doc_id}") not in paths
    assert ("POST", "/api/documents/import-pdf") not in paths
    assert ("POST", "/api/documents/export-zip") not in paths
    assert ("POST", "/api/documents/tidy") not in paths
    assert ("DELETE", "/api/research/{session_id}") not in paths
    assert ("POST", "/api/tasks") not in paths
    assert ("PUT", "/api/tasks/{task_id}") not in paths
    assert ("DELETE", "/api/tasks/{task_id}") not in paths
    assert ("PATCH", "/api/session/{sid}") not in paths
    assert ("DELETE", "/api/session/{sid}") not in paths
    assert ("POST", "/api/session/{sid}/archive") not in paths
    assert ("POST", "/api/session/{sid}/compact") not in paths
    assert ("POST", "/api/sessions/bulk-delete") not in paths
    assert ("DELETE", "/api/sessions/all") not in paths
    assert ("POST", "/api/notes") not in paths
    assert ("PUT", "/api/notes/{note_id}") not in paths
    assert ("DELETE", "/api/notes/{note_id}") not in paths
    assert ("POST", "/api/notes/{note_id}/pin") not in paths
    assert ("POST", "/api/calendar/events") not in paths
    assert ("PUT", "/api/calendar/events/{uid}") not in paths
    assert ("DELETE", "/api/calendar/events/{uid}") not in paths
    assert ("POST", "/api/calendar/calendars") not in paths
    assert ("POST", "/api/calendar/config/accounts") not in paths
    assert ("PUT", "/api/prefs/{key}") not in paths
    assert ("POST", "/api/prefs/custom-themes") not in paths
    assert ("PATCH", "/api/prefs/custom-themes") not in paths
    assert ("DELETE", "/api/prefs/custom-themes") not in paths
    assert ("POST", "/api/memory/add") not in paths
    assert ("POST", "/api/memory/search") not in paths
    assert ("POST", "/api/memory/import") not in paths
    assert ("PUT", "/api/memory/{memory_id}") not in paths
    assert ("DELETE", "/api/memory/{memory_id}") not in paths
    assert ("POST", "/api/memory/{memory_id}/pin") not in paths
    assert ("POST", "/api/contacts/add") not in paths
    assert ("POST", "/api/contacts/import") not in paths
    assert ("PUT", "/api/contacts/config") not in paths
    assert ("PUT", "/api/contacts/{uid}") not in paths
    assert ("DELETE", "/api/contacts/{uid}") not in paths
    assert ("DELETE", "/api/contacts/clear") not in paths
    assert ("GET", "/api/email/accounts") not in paths
    assert ("POST", "/api/email/accounts") not in paths
    assert ("PUT", "/api/email/accounts/{account_id}") not in paths
    assert ("DELETE", "/api/email/accounts/{account_id}") not in paths
    assert ("POST", "/api/email/send") not in paths
    assert ("POST", "/api/email/ai-reply") not in paths
    assert ("POST", "/api/email/archive/{uid}") not in paths
    assert ("POST", "/api/email/mark-read/{uid}") not in paths
    assert ("DELETE", "/api/email/delete/{uid}") not in paths
    assert ("POST", "/api/email/schedule") not in paths
    assert ("DELETE", "/api/email/scheduled/{sid}") not in paths
    assert ("POST", "/api/email/pending/{sid}/approve") not in paths
    assert ("PUT", "/api/email/config") not in paths
    assert ("POST", "/api/skills/add") not in paths
    assert ("POST", "/api/skills/import-from-url") not in paths
    assert ("POST", "/api/skills/search") not in paths
    assert ("POST", "/api/skills/{skill_id}/test") not in paths
    assert ("POST", "/api/skills/audit-all") not in paths
    assert ("POST", "/api/skills/{skill_id}/markdown") not in paths
    assert ("PUT", "/api/skills/{skill_id}") not in paths
    assert ("DELETE", "/api/skills/{skill_id}") not in paths
    assert ("PATCH", "/api/assistant/settings") not in paths
    assert ("POST", "/api/assistant/run/{task_id}") not in paths
    assert ("POST", "/api/chat") not in paths
    assert ("POST", "/api/chat_stream") not in paths
    assert ("POST", "/api/chat/stop/{session_id}") not in paths
    assert ("POST", "/api/inject_context/{session_id}") not in paths
    assert ("POST", "/api/rewrite") not in paths
    assert ("POST", "/api/codex/todos") not in paths
    assert ("POST", "/api/codex/emails/send") not in paths
    assert ("POST", "/api/codex/memory") not in paths
    assert ("DELETE", "/api/codex/memory/{memory_id}") not in paths
    assert ("POST", "/api/codex/calendar/events") not in paths
    assert ("DELETE", "/api/codex/calendar/events/{uid}") not in paths
    assert ("POST", "/api/codex/documents") not in paths
    assert ("DELETE", "/api/codex/documents/{doc_id}") not in paths
    assert ("POST", "/api/codex/cookbook/serve") not in paths
    assert ("POST", "/api/codex/cookbook/preset/{name}") not in paths
    assert ("POST", "/api/embeddings/models/{model_name}/download") not in paths
    assert ("DELETE", "/api/embeddings/models/{model_name}") not in paths
    assert ("POST", "/api/embeddings/endpoint") not in paths
    assert ("DELETE", "/api/embeddings/endpoint") not in paths
    assert ("POST", "/api/upload") not in paths
    assert ("POST", "/api/upload/cleanup") not in paths
    assert ("GET", "/api/upload/stats") not in paths
    assert ("GET", "/api/upload/{file_id}") not in paths
    assert ("GET", "/api/upload/{file_id}/vision") not in paths
    assert ("PUT", "/api/upload/{file_id}/vision") not in paths
    assert ("GET", "/api/signatures") not in paths
    assert ("POST", "/api/signatures") not in paths
    assert ("DELETE", "/api/signatures/{sig_id}") not in paths
    assert ("POST", "/api/presets/custom") not in paths
    assert ("POST", "/api/presets/templates") not in paths
    assert ("DELETE", "/api/presets/templates/{template_id}") not in paths
    assert ("POST", "/api/presets/groups") not in paths
    assert ("GET", "/api/editor-drafts") not in paths
    assert ("GET", "/api/editor-drafts/{draft_id}") not in paths
    assert ("POST", "/api/editor-drafts") not in paths
    assert ("PUT", "/api/editor-drafts/{draft_id}") not in paths
    assert ("DELETE", "/api/editor-drafts/{draft_id}") not in paths
    assert ("POST", "/api/cleanup") not in paths
    assert ("POST", "/api/plugins/registries") not in paths
    assert ("DELETE", "/api/plugins/registries") not in paths
    assert ("POST", "/api/plugins/rescan") not in paths
    assert ("POST", "/api/plugins/install") not in paths
    assert ("POST", "/api/plugins/{plugin_id}/enable") not in paths
    assert ("POST", "/api/plugins/{plugin_id}/reply") not in paths
    assert ("POST", "/api/personal/reload") not in paths
    assert ("POST", "/api/personal/add_directory") not in paths
    assert ("POST", "/api/personal/upload") not in paths
    assert ("DELETE", "/api/personal/remove_directory") not in paths
    assert ("DELETE", "/api/personal/file") not in paths
