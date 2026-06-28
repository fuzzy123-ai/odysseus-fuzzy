import pytest

pytest.importorskip("mcp")

import mcp_servers.email_server as es


@pytest.fixture(autouse=True)
def no_configured_accounts(monkeypatch):
    monkeypatch.setattr(es, "_read_accounts_from_db", lambda: [])


@pytest.mark.asyncio
async def test_permanent_delete_requires_confirmation(monkeypatch):
    called = {"delete": False}

    def fake_delete(*args, **kwargs):
        called["delete"] = True
        return True

    monkeypatch.setattr(es, "_delete_email", fake_delete)

    out = await es.call_tool(
        "delete_email",
        {"uid": "42", "folder": "INBOX", "permanent": True},
    )

    assert "Confirmation required" in out[0].text
    assert "permanent email deletion" in out[0].text
    assert called["delete"] is False


@pytest.mark.asyncio
async def test_confirmed_permanent_delete_runs(monkeypatch):
    calls = []

    def fake_delete(uid, folder="INBOX", permanent=False, account=None):
        calls.append({
            "uid": uid,
            "folder": folder,
            "permanent": permanent,
            "account": account,
        })
        return True

    monkeypatch.setattr(es, "_delete_email", fake_delete)

    out = await es.call_tool(
        "delete_email",
        {
            "uid": "42",
            "folder": "INBOX",
            "permanent": True,
            "confirmed": True,
            "account": "Gmail",
        },
    )

    assert out[0].text == "Deleted UID 42"
    assert calls == [{
        "uid": "42",
        "folder": "INBOX",
        "permanent": True,
        "account": "Gmail",
    }]


@pytest.mark.asyncio
async def test_move_to_trash_single_delete_does_not_require_confirmation(monkeypatch):
    calls = []

    def fake_delete(uid, folder="INBOX", permanent=False, account=None):
        calls.append({
            "uid": uid,
            "folder": folder,
            "permanent": permanent,
            "account": account,
        })
        return True

    monkeypatch.setattr(es, "_delete_email", fake_delete)

    out = await es.call_tool(
        "delete_email",
        {"uid": "42", "folder": "INBOX", "account": "Gmail"},
    )

    assert out[0].text == "Deleted UID 42"
    assert calls == [{
        "uid": "42",
        "folder": "INBOX",
        "permanent": False,
        "account": "Gmail",
    }]


@pytest.mark.asyncio
async def test_bulk_delete_requires_confirmation(monkeypatch):
    called = {"move": False, "flag": False}

    def fake_move(*args, **kwargs):
        called["move"] = True
        return 2

    def fake_flag(*args, **kwargs):
        called["flag"] = True
        return 2

    monkeypatch.setattr(es, "_bulk_move", fake_move)
    monkeypatch.setattr(es, "_bulk_set_flag", fake_flag)

    out = await es.call_tool(
        "bulk_email",
        {"action": "delete", "uids": ["1", "2"], "folder": "INBOX"},
    )

    assert "Confirmation required" in out[0].text
    assert "bulk email deletion" in out[0].text
    assert called == {"move": False, "flag": False}


@pytest.mark.asyncio
async def test_bulk_delete_all_unread_requires_confirmation_before_search(monkeypatch):
    called = {"search": False}

    def fake_search(*args, **kwargs):
        called["search"] = True
        return ["1", "2"]

    monkeypatch.setattr(es, "_search_uids", fake_search)

    out = await es.call_tool(
        "bulk_email",
        {"action": "delete", "all_unread": True, "folder": "INBOX"},
    )

    assert "Confirmation required" in out[0].text
    assert called["search"] is False


@pytest.mark.asyncio
async def test_confirmed_bulk_delete_moves_to_trash(monkeypatch):
    calls = []

    monkeypatch.setattr(
        es,
        "_load_config",
        lambda account=None: {"trash_folder": "Trash", "archive_folder": "Archive"},
    )

    def fake_move(uids, source_folder, dest_folder, account=None, role=""):
        calls.append({
            "uids": uids,
            "source_folder": source_folder,
            "dest_folder": dest_folder,
            "account": account,
            "role": role,
        })
        return len(uids)

    monkeypatch.setattr(es, "_bulk_move", fake_move)

    out = await es.call_tool(
        "bulk_email",
        {
            "action": "delete",
            "uids": ["1", "2"],
            "folder": "INBOX",
            "confirmed": True,
            "account": "Gmail",
        },
    )

    assert "Done" in out[0].text
    assert "2 email(s) moved to Trash" in out[0].text
    assert calls == [{
        "uids": ["1", "2"],
        "source_folder": "INBOX",
        "dest_folder": "Trash",
        "account": "Gmail",
        "role": "trash",
    }]
