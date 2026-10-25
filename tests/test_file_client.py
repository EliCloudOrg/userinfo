"""file 客户端：强制 public + 永久、契约校验、错误映射。"""

from __future__ import annotations

import httpx
import pytest

from app.file_client import (
    FileClient,
    FileNotFound,
    FileServiceContractError,
    FileServiceUnavailable,
)


def client_with(handler) -> FileClient:
    return FileClient("http://file.test", transport=httpx.MockTransport(handler))


def test_upload_sends_public_permanent_and_forwards_token():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        body = request.read().decode("utf-8", errors="replace")
        seen["body"] = body
        return httpx.Response(
            201,
            json={
                "id": "f_" + "a" * 22,
                "filename": "avatar.png",
                "content_type": "image/png",
                "size_bytes": 3,
                "sha256": "b" * 64,
                "visibility": "public",
                "permanent": True,
                "status": "active",
                "expires_at": None,
                "expires_in": None,
            },
        )

    uploaded = client_with(handler).upload_image(
        "tok",
        content=b"png",
        content_type="image/png",
        filename="avatar.png",
    )
    assert uploaded.file_id == "f_" + "a" * 22
    assert seen["auth"] == "Bearer tok"
    assert 'name="visibility"' in seen["body"]
    assert "public" in seen["body"]
    assert 'name="ttl_seconds"' in seen["body"]


def test_upload_rejects_old_file_service_without_permanent():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={
                "id": "f_" + "a" * 22,
                "content_type": "image/png",
                "size_bytes": 3,
                "sha256": "b" * 64,
                "visibility": "public",
                "permanent": False,
            },
        )

    with pytest.raises(FileServiceContractError):
        client_with(handler).upload_image("tok", content=b"png", content_type="image/png", filename="a.png")


def test_fetch_404_is_not_found():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not_found", "error_description": "gone"})

    with pytest.raises(FileNotFound):
        client_with(handler).fetch_file("tok", "f_" + "a" * 22)


def test_fetch_uses_proxy_and_reads_etag():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["direct"] == "0"
        return httpx.Response(200, headers={"ETag": '"abc"', "Content-Type": "image/png"}, content=b"data")

    fetched = client_with(handler).fetch_file("tok", "f_" + "a" * 22)
    assert fetched.content == b"data"
    assert fetched.content_type == "image/png"
    assert fetched.etag == "abc"


def test_network_failure_is_unavailable():
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(FileServiceUnavailable):
        client_with(handler).fetch_file("tok", "f_" + "a" * 22)
