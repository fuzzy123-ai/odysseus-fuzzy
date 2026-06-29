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
