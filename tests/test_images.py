"""头像 / banner：上传、读取、替换、删除、校验与 file 故障。"""

from __future__ import annotations

import pytest

from tests.conftest import JPEG_BYTES, PNG_BYTES, WEBP_BYTES
from tests.helpers import TEST_FILE_READ_SCOPE, TEST_READ_SCOPE


def upload_avatar(client, headers, content=PNG_BYTES, content_type="image/png"):
    return client.put(
        "/v1/profile/avatar",
        files={"file": ("avatar.png", content, content_type)},
        headers=headers,
    )


def upload_banner(client, headers, content=JPEG_BYTES, content_type="image/jpeg"):
    return client.put(
        "/v1/profile/banner",
        files={"file": ("banner.jpg", content, content_type)},
        headers=headers,
    )


def test_upload_and_fetch_avatar(client, auth):
    headers = auth(sub="user_avatar")
    uploaded = upload_avatar(client, headers)
    assert uploaded.status_code == 200
    body = uploaded.json()
    assert body["avatar"]["content_type"] == "image/png"
    assert body["avatar"]["size_bytes"] == len(PNG_BYTES)

    fetched = client.get("/v1/profile/avatar", headers=headers)
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/png"
    assert fetched.content == PNG_BYTES
    assert fetched.headers["etag"].startswith('"')

    cached = client.get(
        "/v1/profile/avatar",
        headers={**headers, "If-None-Match": fetched.headers["etag"]},
    )
    assert cached.status_code == 304


def test_upload_banner_and_public_fetch(client, auth, read_auth):
    headers = auth(sub="user_banner")
    assert upload_banner(client, headers).status_code == 200

    own = client.get("/v1/profile/banner", headers=headers)
    assert own.status_code == 200
    assert own.content == JPEG_BYTES

    public = client.get("/v1/profiles/user_banner/banner", headers=read_auth(sub="user_other"))
    assert public.status_code == 200
    assert public.content == JPEG_BYTES


def test_avatar_and_banner_are_independent(client, auth):
    headers = auth(sub="user_both")
    assert upload_avatar(client, headers).status_code == 200
    assert upload_banner(client, headers).status_code == 200

    avatar = client.get("/v1/profile/avatar", headers=headers)
    banner = client.get("/v1/profile/banner", headers=headers)
    assert avatar.status_code == 200
    assert banner.status_code == 200

    assert client.delete("/v1/profile/avatar", headers=headers).status_code == 200
    assert client.get("/v1/profile/avatar", headers=headers).status_code == 404
    assert client.get("/v1/profile/banner", headers=headers).status_code == 200


def test_replace_deletes_old_file(client, auth, fake_file):
    headers = auth(sub="user_replace")
    assert upload_avatar(client, headers).status_code == 200
    assert len(fake_file.objects) == 1
    old_ids = set(fake_file.objects)

    second = upload_avatar(client, headers, content=JPEG_BYTES, content_type="image/jpeg")
    assert second.status_code == 200
    assert len(fake_file.objects) == 1
    assert set(fake_file.objects).isdisjoint(old_ids)
    assert old_ids.issubset(set(fake_file.deleted))


def test_delete_is_idempotent(client, auth):
    headers = auth(sub="user_delete")
    first = client.delete("/v1/profile/avatar", headers=headers)
    assert first.status_code == 200
    assert first.json()["changed"] is False

    assert upload_avatar(client, headers).status_code == 200
    second = client.delete("/v1/profile/avatar", headers=headers)
    assert second.status_code == 200
    assert second.json()["changed"] is True
    third = client.delete("/v1/profile/avatar", headers=headers)
    assert third.status_code == 200
    assert third.json()["changed"] is False


@pytest.mark.parametrize(
    ("content", "content_type"),
    [
        (b"not an image", "image/png"),
        (PNG_BYTES, "image/jpeg"),
        (PNG_BYTES, "image/gif"),
        (b"", "image/png"),
    ],
)
def test_invalid_image_is_400(client, auth, content, content_type):
    response = upload_avatar(client, auth(), content=content, content_type=content_type)
    assert response.status_code == 400
    assert response.json()["error"] in ("invalid_image", "unsupported_image_type")


def test_webp_is_accepted(client, auth):
    response = upload_avatar(client, auth(), content=WEBP_BYTES, content_type="image/webp")
    assert response.status_code == 200
    assert response.json()["avatar"]["content_type"] == "image/webp"


def test_avatar_size_limit(client_factory, auth):
    limited = client_factory(MAX_AVATAR_BYTES=16)
    response = upload_avatar(limited, auth(sub="user_small"))
    assert response.status_code == 413
    assert response.json()["error"] == "image_too_large"


def test_banner_has_its_own_larger_limit(client_factory, auth):
    limited = client_factory(MAX_AVATAR_BYTES=16, MAX_BANNER_BYTES=131072)
    assert upload_avatar(limited, auth(sub="user_limits")).status_code == 413
    assert upload_banner(limited, auth(sub="user_limits")).status_code == 200


def test_image_needs_file_scope(client, auth):
    no_file_read = auth(scope=f"openid {TEST_READ_SCOPE} userinfo:write")
    response = client.get("/v1/profile/avatar", headers=no_file_read)
    assert response.status_code == 403
    assert response.json()["error"] == "insufficient_scope"

    no_file_write = auth(scope=f"openid {TEST_READ_SCOPE} userinfo:write")
    response = upload_avatar(client, no_file_write)
    assert response.status_code == 403


def test_file_upload_failure_is_503(client, auth, fake_file):
    fake_file.fail_upload = True
    response = upload_avatar(client, auth(sub="user_down"))
    assert response.status_code == 503
    assert response.json()["error"] == "file_service_unavailable"


def test_file_fetch_failure_is_503(client, auth, fake_file):
    headers = auth(sub="user_fetch_down")
    assert upload_avatar(client, headers).status_code == 200
    fake_file.fail_fetch = True
    response = client.get("/v1/profile/avatar", headers=headers)
    assert response.status_code == 503
    assert response.json()["error"] == "file_service_unavailable"


def test_missing_file_object_is_404(client, auth, fake_file):
    headers = auth(sub="user_missing")
    assert upload_avatar(client, headers).status_code == 200
    fake_file.objects.clear()
    response = client.get("/v1/profile/avatar", headers=headers)
    assert response.status_code == 404
    assert response.json()["error"] == "image_not_found"


def test_no_image_is_404(client, auth):
    response = client.get("/v1/profile/banner", headers=auth(sub="user_no_image"))
    assert response.status_code == 404
    assert response.json()["error"] == "image_not_found"
