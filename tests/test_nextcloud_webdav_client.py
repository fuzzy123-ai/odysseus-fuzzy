from pathlib import Path

import httpx

from src.nextcloud_webdav_client import (
    NextcloudWebDAVClient,
    NextcloudWebDAVClientError,
    build_nextcloud_webdav_client_from_env,
)


def _client(handler, *, root: str = ""):
    transport = httpx.MockTransport(handler)
    return NextcloudWebDAVClient(
        base_url="https://nextcloud.example/remote.php/dav/files/odysseus",
        username="odysseus",
        app_password="app-password",
        root=root,
        client=httpx.Client(transport=transport),
    )


def test_stat_parses_propfind_metadata():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PROPFIND"
        assert request.headers["Depth"] == "0"
        return httpx.Response(
            207,
            text=(
                '<?xml version="1.0"?>'
                '<d:multistatus xmlns:d="DAV:"><d:response><d:propstat><d:prop>'
                "<d:getcontentlength>42</d:getcontentlength><d:getetag>\"abc\"</d:getetag>"
                "</d:prop></d:propstat></d:response></d:multistatus>"
            ),
        )

    metadata = _client(handler).stat("Documents/file.txt")

    assert metadata == {
        "relative_path": "Documents/file.txt",
        "size_bytes": 42,
        "etag": "abc",
        "is_collection": False,
    }


def test_put_file_creates_parent_collections_and_uploads(tmp_path):
    source = tmp_path / "file.txt"
    source.write_text("payload", encoding="utf-8")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url), request.content))
        if request.method == "MKCOL":
            return httpx.Response(201)
        if request.method == "PUT":
            return httpx.Response(201, headers={"ETag": '"uploaded"'})
        return httpx.Response(500)

    result = _client(handler, root="AI Inbox").put_file(source, "Documents/Private/file.txt")

    assert result == {"size_bytes": 7, "etag": "uploaded"}
    assert [call[0] for call in calls] == ["MKCOL", "MKCOL", "MKCOL", "PUT"]
    assert calls[-1][1].endswith("/AI%20Inbox/Documents/Private/file.txt")
    assert calls[-1][2] == b"payload"


def test_put_text_uploads_sidecar_without_raw_source(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url), request.content))
        return httpx.Response(201, headers={"ETag": '"sidecar"'})

    result = _client(handler).put_text("AI Inbox/Metadata/file.odysseus.json", '{"safe":true}')

    assert result == {"size_bytes": 13, "etag": "sidecar"}
    assert calls[-1][0] == "PUT"
    assert calls[-1][2] == b'{"safe":true}'


def test_put_bytes_create_only_keeps_exact_readable_bytes():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "MKCOL":
            return httpx.Response(201)
        if request.method == "PUT":
            return httpx.Response(201, headers={"ETag": '"created"'})
        return httpx.Response(500)

    result = _client(handler, root="Forge Root").put_bytes_create_only(
        "Projects/demo/file.bin",
        b"plain-readable-bytes",
    )

    put = calls[-1]
    assert put.method == "PUT"
    assert put.headers["If-None-Match"] == "*"
    assert put.content == b"plain-readable-bytes"
    assert result == {"size_bytes": 20, "etag": "created"}


def test_put_bytes_create_only_rejects_non_created_status_and_redacts_body():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "MKCOL":
            return httpx.Response(201)
        return httpx.Response(204, text="Bearer should-not-escape")

    try:
        _client(handler).put_bytes_create_only("Projects/demo.txt", b"data")
    except NextcloudWebDAVClientError as exc:
        assert str(exc) == "WebDAV request failed: HTTP 204"
        assert "Bearer" not in str(exc)
    else:
        raise AssertionError("create-only PUT must require HTTP 201")


def test_put_bytes_size_limit_blocks_before_webdav_io():
    calls = []
    client = _client(lambda request: calls.append(request) or httpx.Response(201))

    try:
        client.put_bytes_create_only("Projects/demo.bin", b"12345", max_bytes=4)
    except NextcloudWebDAVClientError as exc:
        assert str(exc) == "WebDAV payload exceeds upload limit"
    else:
        raise AssertionError("oversize in-memory payload must be blocked")
    assert calls == []


def test_move_create_only_uses_absolute_destination_and_forbids_overwrite():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "MKCOL":
            return httpx.Response(201)
        if request.method == "MOVE":
            return httpx.Response(201)
        return httpx.Response(500)

    client = _client(handler, root="Forge Root")
    result = client.move_create_only(
        "Projects/demo/staging/op",
        "Projects/demo/Versions/v1",
    )

    move = calls[-1]
    assert result == {"created": True, "etag": ""}
    assert move.method == "MOVE"
    assert move.headers["Overwrite"] == "F"
    assert move.headers["Destination"].endswith(
        "/Forge%20Root/Projects/demo/Versions/v1"
    )
    assert "overwrite" not in client.move_create_only.__code__.co_varnames


def test_put_bytes_and_move_reject_backslash_nul_and_traversal_before_io():
    calls = []
    client = _client(lambda request: calls.append(request) or httpx.Response(201))

    unsafe = ("../escape", "folder\\file", "folder/\x00file", "/absolute")
    for path in unsafe:
        try:
            client.put_bytes_create_only(path, b"data")
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe PUT path accepted: {path!r}")
        try:
            client.move_create_only("safe/source", path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe MOVE path accepted: {path!r}")
    assert calls == []


def test_get_file_bytes_downloads_existing_file_with_size_guard():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "PROPFIND":
            return httpx.Response(
                207,
                text=(
                    '<?xml version="1.0"?>'
                    '<d:multistatus xmlns:d="DAV:"><d:response><d:propstat><d:prop>'
                    "<d:getcontentlength>7</d:getcontentlength>"
                    "</d:prop></d:propstat></d:response></d:multistatus>"
                ),
            )
        if request.method == "GET":
            return httpx.Response(200, content=b"payload")
        return httpx.Response(500)

    payload = _client(handler).get_file_bytes("Documents/file.txt", max_bytes=10)

    assert payload == b"payload"
    assert [call[0] for call in calls] == ["PROPFIND", "GET"]


def test_get_file_bytes_refuses_declared_oversize_before_get():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.method)
        return httpx.Response(
            207,
            text=(
                '<?xml version="1.0"?>'
                '<d:multistatus xmlns:d="DAV:"><d:response><d:propstat><d:prop>'
                "<d:getcontentlength>100</d:getcontentlength>"
                "</d:prop></d:propstat></d:response></d:multistatus>"
            ),
        )

    try:
        _client(handler).get_file_bytes("Documents/file.txt", max_bytes=10)
    except NextcloudWebDAVClientError as exc:
        assert "download limit" in str(exc)
    else:
        raise AssertionError("oversize WebDAV file should be refused")
    assert calls == ["PROPFIND"]


def test_rejects_absolute_or_traversal_paths():
    client = _client(lambda _request: httpx.Response(200))

    for path in ("C:/Users/private.txt", "../private.txt", "/absolute.txt"):
        try:
            client.stat(path)
        except ValueError as exc:
            assert "relative path" in str(exc)
        else:
            raise AssertionError(f"unsafe path should be rejected: {path}")


def test_http_errors_are_redacted():
    client = _client(lambda _request: httpx.Response(503, text="backend secret details"))

    try:
        client.stat("Documents/file.txt")
    except NextcloudWebDAVClientError as exc:
        assert str(exc) == "WebDAV request failed: HTTP 503"
        assert "backend secret" not in str(exc)
    else:
        raise AssertionError("HTTP errors should raise")


def test_env_factory_requires_config_without_leaking_secret_names(monkeypatch):
    for name in (
        "NEXTCLOUD_WEBDAV_BASE_URL",
        "NEXTCLOUD_WEBDAV_USERNAME",
        "NEXTCLOUD_WEBDAV_APP_PASSWORD",
        "NEXTCLOUD_WEBDAV_URL",
        "NEXTCLOUD_WEBDAV_PASSWORD",
        "NEXTCLOUD_WEBDAV_PASSWORT",
        "NEXTCLOUD_USERNAME",
        "NEXTCLOUD_PASSWORD",
        "NEXTCLOUD_WEBDAV_ROOT",
    ):
        monkeypatch.delenv(name, raising=False)

    try:
        build_nextcloud_webdav_client_from_env()
    except NextcloudWebDAVClientError as exc:
        text = str(exc)
        assert "NEXTCLOUD_WEBDAV_BASE_URL" in text
        assert "NEXTCLOUD_WEBDAV_APP_PASSWORD" in text
        assert "app-password" not in text
    else:
        raise AssertionError("missing env should block client creation")


def test_env_factory_creates_client_without_network_io(monkeypatch):
    monkeypatch.setenv("NEXTCLOUD_WEBDAV_BASE_URL", "https://nextcloud.example/remote.php/dav/files/odysseus")
    monkeypatch.setenv("NEXTCLOUD_WEBDAV_USERNAME", "odysseus")
    monkeypatch.setenv("NEXTCLOUD_WEBDAV_APP_PASSWORD", "secret-app-password")
    monkeypatch.setenv("NEXTCLOUD_WEBDAV_ROOT", "AI Inbox")

    client = build_nextcloud_webdav_client_from_env()
    try:
        assert client.base_url == "https://nextcloud.example/remote.php/dav/files/odysseus"
        assert client.username == "odysseus"
        assert client.root == "AI Inbox"
    finally:
        client.close()


def test_env_factory_accepts_url_and_password_aliases(monkeypatch):
    for name in (
        "NEXTCLOUD_WEBDAV_BASE_URL",
        "NEXTCLOUD_WEBDAV_APP_PASSWORD",
        "NEXTCLOUD_WEBDAV_URL",
        "NEXTCLOUD_WEBDAV_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NEXTCLOUD_WEBDAV_URL", "https://nextcloud.example/remote.php/dav/files/odysseus")
    monkeypatch.setenv("NEXTCLOUD_WEBDAV_USERNAME", "odysseus")
    monkeypatch.setenv("NEXTCLOUD_WEBDAV_PASSWORD", "secret-app-password")

    client = build_nextcloud_webdav_client_from_env()
    try:
        assert client.base_url == "https://nextcloud.example/remote.php/dav/files/odysseus"
        assert client.username == "odysseus"
    finally:
        client.close()
