from routes import email_imap_helpers
from routes import email_routes


class FakeImap:
    def __init__(self, folders=None, uid_exists=True, move_status="OK", copy_status="OK", store_status="OK"):
        self.folders = folders or []
        self.uid_exists = uid_exists
        self.move_status = move_status
        self.copy_status = copy_status
        self.store_status = store_status
        self.calls = []
        self.expunged = False

    def list(self):
        return "OK", self.folders

    def uid(self, command, *args):
        self.calls.append(("uid", command, *args))
        if command == "FETCH":
            return ("OK", [b"1 (UID 42)"]) if self.uid_exists else ("NO", [])
        if command == "MOVE":
            return self.move_status, []
        if command == "COPY":
            return self.copy_status, []
        if command == "STORE":
            return self.store_status, []
        return "NO", []

    def copy(self, *args):
        self.calls.append(("copy", *args))
        return self.copy_status, []

    def store(self, *args):
        self.calls.append(("store", *args))
        return self.store_status, []

    def expunge(self):
        self.expunged = True


def test_email_routes_keep_legacy_imap_aliases():
    assert email_routes._group_uid_fetch_records is email_imap_helpers.group_uid_fetch_records
    assert email_routes._uid_from_fetch_meta is email_imap_helpers.uid_from_fetch_meta
    assert email_routes._imap_uid_fetch is email_imap_helpers.imap_uid_fetch
    assert email_routes._move_email_message is email_imap_helpers.move_email_message


def test_resolve_mail_folder_prefers_provider_special_use_flag():
    conn = FakeImap([
        b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Bin"',
        b'(\\HasNoChildren) "/" "Archive"',
    ])

    assert email_imap_helpers.resolve_mail_folder(conn, "Trash", role="trash") == "[Gmail]/Bin"


def test_resolve_mail_folder_falls_back_to_candidate_name():
    conn = FakeImap([
        b'(\\HasNoChildren) "/" "Spam"',
        b'(\\HasNoChildren) "/" "Deleted Items"',
    ])

    assert email_imap_helpers.resolve_mail_folder(conn, "Trash", role="trash") == "Deleted Items"


def test_move_email_message_uses_uid_move_when_supported():
    conn = FakeImap(folders=[b'() "/" "Archive"'], uid_exists=True, move_status="OK")

    assert email_imap_helpers.move_email_message(conn, "42", "Archive", role="archive") is True
    assert ("uid", "MOVE", b"42", '"Archive"') in conn.calls
    assert conn.expunged is False


def test_move_email_message_falls_back_to_copy_store_expunge():
    conn = FakeImap(folders=[b'() "/" "Archive"'], uid_exists=True, move_status="NO")

    assert email_imap_helpers.move_email_message(conn, "42", "Archive", role="archive") is True
    assert ("uid", "COPY", b"42", '"Archive"') in conn.calls
    assert ("uid", "STORE", b"42", "+FLAGS", "\\Deleted") in conn.calls
    assert conn.expunged is True
