"""文字资料：创建、更新、校验、公开读取、鉴权与审计。"""

from __future__ import annotations

import pytest

from tests.helpers import TEST_READ_SCOPE, TEST_SCOPE


def test_get_profile_without_record_returns_nulls(client, auth):
    response = client.get("/v1/profile", headers=auth(sub="user_empty"))
    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == "user_empty"
    assert body["nickname"] is None
    assert body["bio"] is None
    assert body["avatar"] is None
    assert body["banner"] is None


def test_put_profile_creates_and_updates(client, auth, db_rows):
    headers = auth(sub="user_a")
    first = client.put("/v1/profile", json={"nickname": "秋风", "bio": "你好"}, headers=headers)
    assert first.status_code == 200
    assert first.json()["nickname"] == "秋风"
    assert first.json()["bio"] == "你好"

    second = client.put("/v1/profile", json={"nickname": "秋风2", "bio": None}, headers=headers)
    assert second.status_code == 200
    assert second.json()["nickname"] == "秋风2"
    assert second.json()["bio"] is None

    rows = db_rows["profiles"](user_id="user_a")
    assert len(rows) == 1
    assert rows[0].nickname == "秋风2"


def test_nickname_is_trimmed_and_nfc_normalized(client, auth):
    response = client.put(
        "/v1/profile",
        json={"nickname": "  Cafe\u0301  ", "bio": "  你好\r\n世界  "},
        headers=auth(),
    )
    assert response.status_code == 200
    assert response.json()["nickname"] == "Café"
    assert response.json()["bio"] == "你好\n世界"


@pytest.mark.parametrize(
    ("nickname", "bio"),
    [
        ("", None),
        ("   ", None),
        ("坏\n名字", None),
        ("a" * 33, None),
        ("ok", "b" * 501),
        ("ok", "坏\x00简介"),
    ],
)
def test_invalid_text_is_400(client, auth, nickname, bio):
    response = client.put("/v1/profile", json={"nickname": nickname, "bio": bio}, headers=auth())
    assert response.status_code == 400
    assert response.json()["error"] in ("invalid_nickname", "invalid_bio")


def test_extra_field_is_400(client, auth):
    response = client.put(
        "/v1/profile",
        json={"nickname": "ok", "bio": "", "email": "x@example.com"},
        headers=auth(),
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_public_profile_requires_login_and_is_read_only(client, auth, read_auth):
    assert client.put("/v1/profile", json={"nickname": "公开"}, headers=auth(sub="user_pub")).status_code == 200

    anonymous = client.get("/v1/profiles/user_pub")
    assert anonymous.status_code == 401

    readable = client.get("/v1/profiles/user_pub", headers=read_auth(sub="user_other"))
    assert readable.status_code == 200
    assert readable.json()["nickname"] == "公开"

    missing = client.get("/v1/profiles/user_none", headers=read_auth())
    assert missing.status_code == 404
    assert missing.json()["error"] == "profile_not_found"


def test_write_requires_write_scope(client, read_auth):
    response = client.put("/v1/profile", json={"nickname": "x"}, headers=read_auth())
    assert response.status_code == 403
    assert response.json()["error"] == "insufficient_scope"


def test_read_requires_read_scope(client, auth):
    headers = auth(scope="openid userinfo:write files:read files:write")
    response = client.get("/v1/profile", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"] == "insufficient_scope"


def test_profile_is_per_user(client, auth):
    assert client.put("/v1/profile", json={"nickname": "甲"}, headers=auth(sub="user_1")).status_code == 200
    assert client.put("/v1/profile", json={"nickname": "乙"}, headers=auth(sub="user_2")).status_code == 200

    first = client.get("/v1/profile", headers=auth(sub="user_1")).json()
    second = client.get("/v1/profile", headers=auth(sub="user_2")).json()
    assert first["nickname"] == "甲"
    assert second["nickname"] == "乙"


def test_profile_write_is_audited_without_body(client, auth, db_rows):
    client.put("/v1/profile", json={"nickname": "审计", "bio": "不要记录正文"}, headers=auth(sub="user_audit"))
    audits = db_rows["audits"](action="profile_update", user_id="user_audit")
    assert len(audits) == 1
    assert "不要记录正文" not in (audits[0].detail or "")


def test_me_returns_token_identity(client, auth):
    response = client.get("/v1/me", headers=auth(sub="user_me"))
    assert response.status_code == 200
    assert response.json()["sub"] == "user_me"
    assert TEST_READ_SCOPE in response.json()["scope"]


def test_default_token_scope_is_sufficient(client, auth):
    response = client.get("/v1/profile", headers=auth(scope=TEST_SCOPE))
    assert response.status_code == 200
