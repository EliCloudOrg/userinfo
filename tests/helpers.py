"""测试共享常量与工具（唯一真源）。"""

from __future__ import annotations

import base64
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

TEST_ISSUER = "https://sso.test/auth"
TEST_AUDIENCE = "elicloud-services"
TEST_KID = "test-kid"
TEST_READ_SCOPE = "userinfo:read"
TEST_WRITE_SCOPE = "userinfo:write"
TEST_FILE_READ_SCOPE = "files:read"
TEST_FILE_WRITE_SCOPE = "files:write"
TEST_SCOPE = (
    f"openid profile {TEST_READ_SCOPE} {TEST_WRITE_SCOPE} "
    f"{TEST_FILE_READ_SCOPE} {TEST_FILE_WRITE_SCOPE}"
)


def generate_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def public_jwk(key, kid: str) -> dict:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _b64url_uint(numbers.n),
        "e": _b64url_uint(numbers.e),
    }


TEST_KEY = generate_key()
TEST_OTHER_KEY = generate_key()


def make_token(
    key=TEST_KEY,
    *,
    kid: str = TEST_KID,
    issuer: str = TEST_ISSUER,
    audience: str = TEST_AUDIENCE,
    sub: str = "user_0001",
    scope: str | None = TEST_SCOPE,
    expires_in: int = 3600,
    algorithm: str = "RS256",
    extra: dict | None = None,
) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": issuer,
        "aud": audience,
        "sub": sub,
        "iat": now,
        "exp": now + expires_in,
    }
    if scope is not None:
        claims["scope"] = scope
    claims.update(extra or {})
    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": kid})


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}
