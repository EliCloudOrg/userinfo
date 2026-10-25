"""令牌验签：缺令牌、签发方、算法、过期、scope。"""

from __future__ import annotations

from tests.helpers import TEST_READ_SCOPE, TEST_SCOPE, bearer, make_token


def test_missing_token_is_401(client):
    response = client.get("/v1/profile")
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


def test_other_issuer_is_401(client):
    headers = bearer(make_token(issuer="https://other/auth", scope=TEST_SCOPE))
    response = client.get("/v1/profile", headers=headers)
    assert response.status_code == 401


def test_expired_token_is_401(client):
    headers = bearer(make_token(expires_in=-120, scope=TEST_SCOPE))
    response = client.get("/v1/profile", headers=headers)
    assert response.status_code == 401


def test_malformed_token_is_401(client):
    response = client.get("/v1/profile", headers=bearer("not-a-jwt"))
    assert response.status_code == 401


def test_missing_read_scope_is_403(client):
    headers = bearer(make_token(scope="openid userinfo:write"))
    response = client.get("/v1/profile", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"] == "insufficient_scope"


def test_read_scope_alone_is_enough(client, read_auth):
    response = client.get("/v1/profile", headers=read_auth())
    assert response.status_code == 200
    assert response.json()["user_id"] == "user_0001"


def test_scope_matching_is_word_wise(client):
    headers = bearer(make_token(scope="openid userinfo:read:extra"))
    response = client.get("/v1/profile", headers=headers)
    assert response.status_code == 403
