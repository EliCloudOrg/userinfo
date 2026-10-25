"""资料与头像 / banner 路由。

对外由网关剥掉 `/userinfo` 前缀，服务内路径都是 `/v1/...`。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, File, Request, Response, UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import profiles_service
from ..config import Settings
from ..constants import (
    IMAGE_EXTENSIONS,
    IMAGE_KINDS,
    KIND_AVATAR,
    KIND_BANNER,
)
from ..deps import (
    DbDep,
    Identity,
    ImageReadIdentity,
    ImageWriteIdentity,
    ReadIdentity,
    SettingsDep,
    WriteIdentity,
    check_write_rate_limit,
    client_ip,
)
from ..errors import api_error
from ..file_client import (
    FileClient,
    FileClientError,
    FileNotFound,
    FileServiceContractError,
    FileServiceUnavailable,
)
from ..schemas import ProfileUpdate

logger = logging.getLogger(__name__)

router = APIRouter()


# ------------------------------------------------------------------ 依赖


def get_file_client(request: Request) -> FileClient:
    client = getattr(request.app.state, "file_client", None)
    if client is None:  # pragma: no cover - 装配错误
        raise api_error(503, "file_service_unavailable", "文件服务尚未配置")
    return client


# ------------------------------------------------------------------ 文本


@router.get("/v1/profile")
def get_my_profile(identity: ReadIdentity, db: DbDep) -> dict[str, Any]:
    return profiles_service.serialize_profile(db, identity.sub)


@router.put("/v1/profile")
def update_my_profile(
    request: Request,
    identity: WriteIdentity,
    settings: SettingsDep,
    db: DbDep,
    payload: ProfileUpdate,
) -> dict[str, Any]:
    ip = client_ip(request)
    check_write_rate_limit(settings=settings, user_id=identity.sub, ip=ip)
    nickname = profiles_service.normalize_nickname(payload.nickname, max_chars=settings.max_nickname_chars)
    bio = profiles_service.normalize_bio(payload.bio, max_chars=settings.max_bio_chars)
    return profiles_service.upsert_profile(
        db,
        user_id=identity.sub,
        nickname=nickname,
        bio=bio,
        request_ip=ip,
    )


@router.get("/v1/profiles/{sub}")
def get_public_profile(sub: str, _identity: ReadIdentity, db: DbDep) -> dict[str, Any]:
    profile = profiles_service.serialize_public_profile(db, sub)
    if profile is None:
        raise api_error(404, "profile_not_found", "还没有这个用户的资料")
    return profile


# ------------------------------------------------------------------ 图片读取


def _to_http_error(exc: FileClientError) -> StarletteHTTPException:
    if isinstance(exc, FileNotFound):
        return api_error(404, "image_not_found", "这张图片已经不在了")
    if isinstance(exc, FileServiceUnavailable):
        return api_error(503, "file_service_unavailable", exc.description, headers={"Retry-After": "30"})
    if isinstance(exc, FileServiceContractError):
        logger.error("file 服务违约：%s", exc.description)
        return api_error(502, "file_service_error", "图片服务返回了不符合预期的结果，请稍后再试")

    status = exc.status_code
    if status == 503:
        return api_error(503, "file_service_unavailable", exc.description, headers={"Retry-After": "30"})
    if status in (401, 403):
        return api_error(
            403,
            "insufficient_scope",
            "这次登录没有拿到文件服务的权限，重新登录后再试",
            headers={"WWW-Authenticate": 'Bearer error="insufficient_scope"'},
        )
    if status == 409:
        return api_error(409, "image_quota_exceeded", "图片空间已经满了，请先删除旧图片")
    if status == 413:
        return api_error(413, "image_too_large", "图片超过大小上限")
    if status == 429:
        return api_error(429, "rate_limited", "操作过于频繁，请稍后再试", headers={"Retry-After": "60"})
    logger.error("file 服务错误 status=%s error=%s desc=%s", status, exc.error, exc.description)
    return api_error(502, "file_service_error", "图片服务暂时不可用，请稍后再试")


def _image_response(request: Request, content: bytes, content_type: str, etag: str) -> Response:
    quoted = f'"{etag}"'
    inm = request.headers.get("if-none-match")
    if inm:
        candidates = {item.strip() for item in inm.split(",")}
        if quoted in candidates or etag in candidates or f"W/{quoted}" in candidates:
            return Response(status_code=304, headers={"ETag": quoted})

    return Response(
        content=content,
        media_type=content_type,
        headers={
            "ETag": quoted,
            "Cache-Control": "private, max-age=60",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _fetch_own_image(
    request: Request,
    db: DbDep,
    identity: Identity,
    kind: str,
) -> Response:
    record = profiles_service.get_image(db, identity.sub, kind)
    if record is None:
        raise api_error(404, "image_not_found", "还没有设置这张图片")
    client = get_file_client(request)
    try:
        fetched = client.fetch_file(identity.access_token, record.file_id)
    except FileClientError as exc:
        raise _to_http_error(exc) from exc
    return _image_response(request, fetched.content, record.content_type, record.sha256)


def _fetch_public_image(
    request: Request,
    db: DbDep,
    identity: Identity,
    sub: str,
    kind: str,
) -> Response:
    record = profiles_service.get_image(db, sub, kind)
    if record is None:
        raise api_error(404, "image_not_found", "还没有设置这张图片")
    client = get_file_client(request)
    try:
        fetched = client.fetch_file(identity.access_token, record.file_id)
    except FileClientError as exc:
        raise _to_http_error(exc) from exc
    return _image_response(request, fetched.content, record.content_type, record.sha256)


@router.get("/v1/profile/avatar")
def get_my_avatar(request: Request, identity: ImageReadIdentity, db: DbDep) -> Response:
    return _fetch_own_image(request, db, identity, KIND_AVATAR)


@router.get("/v1/profile/banner")
def get_my_banner(request: Request, identity: ImageReadIdentity, db: DbDep) -> Response:
    return _fetch_own_image(request, db, identity, KIND_BANNER)


@router.get("/v1/profiles/{sub}/avatar")
def get_public_avatar(request: Request, sub: str, identity: ImageReadIdentity, db: DbDep) -> Response:
    return _fetch_public_image(request, db, identity, sub, KIND_AVATAR)


@router.get("/v1/profiles/{sub}/banner")
def get_public_banner(request: Request, sub: str, identity: ImageReadIdentity, db: DbDep) -> Response:
    return _fetch_public_image(request, db, identity, sub, KIND_BANNER)


# ------------------------------------------------------------------ 图片写入


def _sniff_image(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def _validate_image(settings: Settings, kind: str, declared: str | None, content: bytes) -> tuple[str, str]:
    if kind not in IMAGE_KINDS:
        raise api_error(400, "invalid_request", "未知的图片类型")
    if not content:
        raise api_error(400, "invalid_image", "图片是空的")

    max_bytes = settings.max_bytes_for(kind)
    if len(content) > max_bytes:
        raise api_error(413, "image_too_large", f"图片超过上限 {max_bytes // (1024 * 1024)} MiB")

    declared_type = (declared or "").split(";")[0].strip().lower()
    if declared_type not in settings.allowed_image_type_list:
        raise api_error(400, "unsupported_image_type", "只支持 PNG / JPEG / WebP 图片")

    actual_type = _sniff_image(content)
    if actual_type is None:
        raise api_error(400, "invalid_image", "图片内容不是有效的 PNG / JPEG / WebP")
    if actual_type != declared_type:
        raise api_error(400, "unsupported_image_type", "图片声明的类型与内容不一致")
    return actual_type, IMAGE_EXTENSIONS[actual_type]


def _upload_image(
    request: Request,
    db: DbDep,
    identity: Identity,
    settings: Settings,
    kind: str,
    upload: UploadFile,
) -> dict[str, Any]:
    ip = client_ip(request)
    check_write_rate_limit(settings=settings, user_id=identity.sub, ip=ip, image=True)

    max_bytes = settings.max_bytes_for(kind)
    content = upload.file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise api_error(413, "image_too_large", f"图片超过上限 {max_bytes // (1024 * 1024)} MiB")

    content_type, extension = _validate_image(settings, kind, upload.content_type, content)
    client = get_file_client(request)
    try:
        uploaded = client.upload_image(
            identity.access_token,
            content=content,
            content_type=content_type,
            filename=f"{kind}.{extension}",
        )
    except FileClientError as exc:
        raise _to_http_error(exc) from exc

    return profiles_service.set_image(
        db,
        file_client=client,
        token=identity.access_token,
        user_id=identity.sub,
        kind=kind,
        uploaded=uploaded,
        request_ip=ip,
    )


def _delete_image(
    request: Request,
    db: DbDep,
    identity: Identity,
    settings: Settings,
    kind: str,
) -> dict[str, Any]:
    ip = client_ip(request)
    check_write_rate_limit(settings=settings, user_id=identity.sub, ip=ip, image=True)
    client = get_file_client(request)
    removed = profiles_service.clear_image(
        db,
        file_client=client,
        token=identity.access_token,
        user_id=identity.sub,
        kind=kind,
        request_ip=ip,
    )
    return {"deleted": True, "changed": removed}


@router.put("/v1/profile/avatar")
def put_my_avatar(
    request: Request,
    identity: ImageWriteIdentity,
    settings: SettingsDep,
    db: DbDep,
    file: UploadFile = File(..., description="头像图片（PNG / JPEG / WebP）"),
) -> dict[str, Any]:
    return _upload_image(request, db, identity, settings, KIND_AVATAR, file)


@router.put("/v1/profile/banner")
def put_my_banner(
    request: Request,
    identity: ImageWriteIdentity,
    settings: SettingsDep,
    db: DbDep,
    file: UploadFile = File(..., description="头图图片（PNG / JPEG / WebP）"),
) -> dict[str, Any]:
    return _upload_image(request, db, identity, settings, KIND_BANNER, file)


@router.delete("/v1/profile/avatar")
def delete_my_avatar(
    request: Request,
    identity: ImageWriteIdentity,
    settings: SettingsDep,
    db: DbDep,
) -> dict[str, Any]:
    return _delete_image(request, db, identity, settings, KIND_AVATAR)


@router.delete("/v1/profile/banner")
def delete_my_banner(
    request: Request,
    identity: ImageWriteIdentity,
    settings: SettingsDep,
    db: DbDep,
) -> dict[str, Any]:
    return _delete_image(request, db, identity, settings, KIND_BANNER)


# ------------------------------------------------------------------ 身份自检


@router.get("/v1/me")
def me(identity: ReadIdentity) -> dict[str, Any]:
    return {
        "sub": identity.sub,
        "username": identity.username,
        "scope": identity.scope,
    }
