"""资料与图片的业务逻辑。

两条不变量：

1. 文字只进 userinfo 的 SQLite；图片字节只进 file 服务。
2. 任何一次图片写入，都先让 file 成功、再提交 DB；DB 失败必须把刚上传的
   file 对象删掉，绝不留下「有文件、没记录」的孤儿。
"""

from __future__ import annotations

import json
import logging
import unicodedata
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session as DbSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from .constants import (
    ACTION_IMAGE_DELETE,
    ACTION_IMAGE_UPDATE,
    ACTION_PROFILE_UPDATE,
    KIND_AVATAR,
    KIND_BANNER,
    RESULT_IDEMPOTENT,
    RESULT_OK,
)
from .errors import api_error
from .file_client import FileClient, UploadedFile
from .models import AuditLog, Profile, ProfileImage, new_audit_id, to_iso, utcnow

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ 审计


def audit(
    db: DbSession,
    *,
    user_id: str | None,
    action: str,
    target: str | None,
    result: str,
    detail: dict[str, Any] | None = None,
    request_ip: str | None = None,
) -> None:
    db.add(
        AuditLog(
            id=new_audit_id(),
            user_id=user_id,
            action=action,
            target=target,
            result=result,
            detail=json.dumps(detail, ensure_ascii=False, sort_keys=True) if detail else None,
            request_ip=request_ip,
            created_at=to_iso(utcnow()),
        )
    )


# ------------------------------------------------------------------ 文本


def _has_control(value: str, *, allow_newline: bool = False) -> bool:
    for char in value:
        if allow_newline and char == "\n":
            continue
        if unicodedata.category(char).startswith("C"):
            return True
    return False


def normalize_nickname(value: str, *, max_chars: int) -> str:
    if not isinstance(value, str):
        raise api_error(400, "invalid_nickname", "昵称必须是文本")
    cleaned = unicodedata.normalize("NFC", value).strip()
    if not cleaned:
        raise api_error(400, "invalid_nickname", "昵称不能为空")
    if len(cleaned) > max_chars:
        raise api_error(400, "invalid_nickname", f"昵称最多 {max_chars} 个字符")
    if _has_control(cleaned):
        raise api_error(400, "invalid_nickname", "昵称不能包含控制字符或换行")
    return cleaned


def normalize_bio(value: str | None, *, max_chars: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise api_error(400, "invalid_bio", "简介必须是文本")
    cleaned = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not cleaned:
        return None
    if len(cleaned) > max_chars:
        raise api_error(400, "invalid_bio", f"简介最多 {max_chars} 个字符")
    if _has_control(cleaned, allow_newline=True):
        raise api_error(400, "invalid_bio", "简介不能包含控制字符")
    return cleaned


def get_profile(db: DbSession, user_id: str) -> Profile | None:
    return db.get(Profile, user_id)


def _ensure_profile(db: DbSession, user_id: str) -> Profile:
    profile = db.get(Profile, user_id)
    if profile is None:
        now = to_iso(utcnow())
        profile = Profile(user_id=user_id, nickname=None, bio=None, created_at=now, updated_at=now)
        db.add(profile)
        db.flush()
    return profile


def _image(db: DbSession, user_id: str, kind: str) -> ProfileImage | None:
    return db.get(ProfileImage, (user_id, kind))


def _serialize(db: DbSession, profile: Profile | None, user_id: str) -> dict[str, Any]:
    avatar = _image(db, user_id, KIND_AVATAR) if profile is not None else None
    banner = _image(db, user_id, KIND_BANNER) if profile is not None else None
    return {
        "user_id": user_id,
        "nickname": profile.nickname if profile is not None else None,
        "bio": profile.bio if profile is not None else None,
        "avatar": avatar.to_public_dict() if avatar is not None else None,
        "banner": banner.to_public_dict() if banner is not None else None,
        "created_at": profile.created_at if profile is not None else None,
        "updated_at": profile.updated_at if profile is not None else None,
    }


def serialize_profile(db: DbSession, user_id: str) -> dict[str, Any]:
    return _serialize(db, get_profile(db, user_id), user_id)


def serialize_public_profile(db: DbSession, user_id: str) -> dict[str, Any] | None:
    profile = get_profile(db, user_id)
    if profile is None:
        return None
    return _serialize(db, profile, user_id)


def upsert_profile(
    db: DbSession,
    *,
    user_id: str,
    nickname: str,
    bio: str | None,
    request_ip: str | None = None,
) -> dict[str, Any]:
    profile = _ensure_profile(db, user_id)
    profile.nickname = nickname
    profile.bio = bio
    profile.updated_at = to_iso(utcnow())
    audit(
        db,
        user_id=user_id,
        action=ACTION_PROFILE_UPDATE,
        target=user_id,
        result=RESULT_OK,
        detail={
            "nickname_length": len(nickname),
            "bio_length": len(bio) if bio else 0,
        },
        request_ip=request_ip,
    )
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("保存资料失败 sub=%s", user_id)
        raise api_error(503, "database_unavailable", "资料暂时保存不了，请稍后再试") from exc
    return _serialize(db, profile, user_id)


# ------------------------------------------------------------------ 图片


def get_image(db: DbSession, user_id: str, kind: str) -> ProfileImage | None:
    return _image(db, user_id, kind)


def set_image(
    db: DbSession,
    *,
    file_client: FileClient,
    token: str,
    user_id: str,
    kind: str,
    uploaded: UploadedFile,
    request_ip: str | None = None,
) -> dict[str, Any]:
    """把已上传到 file 的对象接到资料上；DB 失败时删除刚上传的对象。"""
    old_file_id: str | None = None
    try:
        profile = _ensure_profile(db, user_id)
        profile.updated_at = to_iso(utcnow())
        existing = _image(db, user_id, kind)
        if existing is not None:
            old_file_id = existing.file_id
            existing.file_id = uploaded.file_id
            existing.content_type = uploaded.content_type
            existing.size_bytes = uploaded.size_bytes
            existing.sha256 = uploaded.sha256
            existing.updated_at = profile.updated_at
        else:
            db.add(
                ProfileImage(
                    user_id=user_id,
                    kind=kind,
                    file_id=uploaded.file_id,
                    content_type=uploaded.content_type,
                    size_bytes=uploaded.size_bytes,
                    sha256=uploaded.sha256,
                    updated_at=profile.updated_at,
                )
            )
        audit(
            db,
            user_id=user_id,
            action=ACTION_IMAGE_UPDATE,
            target=uploaded.file_id,
            result=RESULT_OK,
            detail={
                "kind": kind,
                "content_type": uploaded.content_type,
                "size_bytes": uploaded.size_bytes,
                "replaced": old_file_id,
            },
            request_ip=request_ip,
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        try:
            file_client.delete_file(token, uploaded.file_id)
        except Exception:  # noqa: BLE001 - 清理失败只记日志，不掩盖原始错误
            logger.warning("回滚时删除新上传文件失败 file_id=%s", uploaded.file_id)
        if isinstance(exc, (HTTPException, StarletteHTTPException)):
            raise
        logger.exception("保存图片记录失败 sub=%s kind=%s", user_id, kind)
        raise api_error(503, "database_unavailable", "图片暂时保存不了，请稍后再试") from exc

    # 新图已生效，旧图删除失败不影响结果（只占 file 配额）。
    if old_file_id and old_file_id != uploaded.file_id:
        try:
            file_client.delete_file(token, old_file_id)
        except Exception:  # noqa: BLE001
            logger.warning("旧图片删除失败 sub=%s kind=%s file_id=%s", user_id, kind, old_file_id)

    return _serialize(db, get_profile(db, user_id), user_id)


def clear_image(
    db: DbSession,
    *,
    file_client: FileClient,
    token: str,
    user_id: str,
    kind: str,
    request_ip: str | None = None,
) -> bool:
    existing = _image(db, user_id, kind)
    if existing is None:
        audit(
            db,
            user_id=user_id,
            action=ACTION_IMAGE_DELETE,
            target=None,
            result=RESULT_IDEMPOTENT,
            detail={"kind": kind},
            request_ip=request_ip,
        )
        db.commit()
        return False

    file_id = existing.file_id
    db.delete(existing)
    audit(
        db,
        user_id=user_id,
        action=ACTION_IMAGE_DELETE,
        target=file_id,
        result=RESULT_OK,
        detail={"kind": kind},
        request_ip=request_ip,
    )
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("删除图片记录失败 sub=%s kind=%s", user_id, kind)
        raise api_error(503, "database_unavailable", "图片暂时删除不了，请稍后再试") from exc

    try:
        file_client.delete_file(token, file_id)
    except Exception:  # noqa: BLE001
        logger.warning("图片记录已删，但 file 对象删除失败 file_id=%s", file_id)
    return True
