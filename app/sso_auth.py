"""SSO access token 验签。

与 file / mc 相同的 fail-closed 规则：只接受 RS256、校验 iss/aud/exp，scope 整词匹配。
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

import jwt

from .config import Settings
from .errors import api_error

logger = logging.getLogger(__name__)

ALGORITHM = "RS256"
_MAX_AGE_RE = re.compile(r"max-age=(\d+)", re.IGNORECASE)

Fetcher = Callable[[str, float], tuple[bytes, str | None]]


def _default_fetcher(url: str, timeout: float) -> tuple[bytes, str | None]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.read(), response.headers.get("Cache-Control")
    except (urllib.error.URLError, OSError) as exc:
        raise JwksError(f"拉取 JWKS 失败：{exc}") from exc


class JwksError(RuntimeError):
    pass


class JwksCache:
    def __init__(
        self,
        url: str,
        *,
        ttl: int = 300,
        timeout: float = 5.0,
        fetcher: Fetcher | None = None,
    ) -> None:
        self.url = url
        self.ttl = max(int(ttl), 1)
        self.timeout = float(timeout)
        self._fetcher = fetcher or _default_fetcher
        self._keys: dict[str, Any] = {}
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def key_for(self, kid: str | None) -> Any | None:
        key = self._lookup(kid)
        if key is not None:
            return key
        self.refresh(force=True)
        return self._lookup(kid)

    def refresh(self, *, force: bool = False) -> None:
        with self._lock:
            now = time.monotonic()
            if not force and self._keys and now < self._expires_at:
                return
            body, cache_control = self._fetcher(self.url, self.timeout)
            keys = self._parse(body)
            self._keys = keys
            self._expires_at = now + self._ttl_from(cache_control)
            logger.info("JWKS 已刷新 keys=%s", sorted(keys))

    def _lookup(self, kid: str | None) -> Any | None:
        with self._lock:
            if kid is None:
                if len(self._keys) == 1:
                    return next(iter(self._keys.values()))
                return None
            return self._keys.get(str(kid))

    def _ttl_from(self, cache_control: str | None) -> int:
        if cache_control:
            match = _MAX_AGE_RE.search(cache_control)
            if match:
                return max(int(match.group(1)), 1)
        return self.ttl

    @staticmethod
    def _parse(body: bytes) -> dict[str, Any]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise JwksError(f"JWKS 不是合法 JSON：{exc}") from exc

        keys: dict[str, Any] = {}
        for item in payload.get("keys") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("kty", "")).upper() != "RSA":
                continue
            kid = item.get("kid")
            try:
                key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(item))
            except Exception as exc:  # noqa: BLE001
                logger.warning("JWKS 中跳过无法解析的 key kid=%s：%s", kid, exc)
                continue
            keys[str(kid) if kid is not None else ""] = key
        if not keys:
            raise JwksError("JWKS 里没有任何可用的 RSA 公钥")
        return keys


_cache_lock = threading.Lock()
_caches: dict[str, JwksCache] = {}


def get_jwks_cache(settings: Settings) -> JwksCache:
    key = settings.sso_jwks_url
    with _cache_lock:
        cache = _caches.get(key)
        if cache is None:
            cache = JwksCache(key, ttl=settings.jwks_cache_seconds, timeout=settings.jwks_timeout_seconds)
            _caches[key] = cache
        return cache


def reset_jwks_cache() -> None:
    with _cache_lock:
        _caches.clear()


def split_scope(value: str | None) -> list[str]:
    if not value:
        return []
    result: list[str] = []
    for item in str(value).split():
        item = item.strip()
        if item and item not in result:
            result.append(item)
    return result


def scope_has(scope: str | None, name: str) -> bool:
    return bool(name) and name in split_scope(scope)


def decode_access_token(settings: Settings, jwks: JwksCache, token: str) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise api_error(401, "invalid_token", "令牌格式不合法", headers={"WWW-Authenticate": "Bearer"}) from exc

    alg = str(header.get("alg", "")).upper()
    if alg != ALGORITHM:
        raise api_error(
            401,
            "invalid_token",
            "令牌签名算法不被接受（只接受 RS256）",
            headers={"WWW-Authenticate": "Bearer"},
        )

    kid = header.get("kid")
    try:
        public_key = jwks.key_for(str(kid) if kid is not None else None)
    except JwksError as exc:
        logger.error("JWKS 不可用：%s", exc)
        raise api_error(503, "jwks_unavailable", "暂时无法校验令牌签名，请稍后重试") from exc

    if public_key is None:
        raise api_error(
            401,
            "invalid_token",
            "令牌引用了未知的 kid（JWKS 里没有对应公钥）",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        claims = jwt.decode(
            token,
            key=public_key,
            algorithms=[ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.sso_issuer,
            leeway=settings.jwt_leeway_seconds,
            options={"require": ["exp", "iss", "sub", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise api_error(401, "invalid_token", "令牌已过期", headers={"WWW-Authenticate": "Bearer"}) from exc
    except jwt.InvalidAudienceError as exc:
        raise api_error(401, "invalid_token", "令牌的 aud 不是本平台", headers={"WWW-Authenticate": "Bearer"}) from exc
    except jwt.InvalidIssuerError as exc:
        raise api_error(401, "invalid_token", "令牌的 iss 与 SSO_ISSUER 不一致", headers={"WWW-Authenticate": "Bearer"}) from exc
    except jwt.PyJWTError as exc:
        raise api_error(401, "invalid_token", "令牌校验失败", headers={"WWW-Authenticate": "Bearer"}) from exc

    if not claims.get("sub"):
        raise api_error(401, "invalid_token", "令牌缺少 sub", headers={"WWW-Authenticate": "Bearer"})
    return claims


def require_scope(claims: dict[str, Any], required_scope: str) -> None:
    if not required_scope:
        return
    if not scope_has(claims.get("scope"), required_scope):
        raise api_error(
            403,
            "insufficient_scope",
            f"令牌缺少 scope：{required_scope}（需要重新登录并携带该 scope）",
            headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{required_scope}"'},
        )
