"""表结构（SQLite，TEXT + ISO8601，与 SSO / file / mc 同风格）。

* ``profiles``       —— 用户文字资料（昵称、简介），以 SSO ``sub`` 为主键
* ``profile_images`` —— 头像 / banner 的**元数据**；图片字节在 file 服务
* ``audit_logs``     —— 全量审计（成功与失败都留痕）

图片只存 file_id，不存 URL、不存字节；file 的 ``public + permanent`` 属性在
上传时由 userinfo 强制并校验，因此这里的行可以安全地被任何已登录用户读取。
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, ForeignKey, Index, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .constants import ISO_FORMAT


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(ISO_FORMAT)


def parse_iso(value: str) -> datetime:
    return datetime.strptime(value, ISO_FORMAT).replace(tzinfo=timezone.utc)


def new_audit_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Profile(Base):
    __tablename__ = "profiles"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)  # SSO sub
    nickname: Mapped[str | None] = mapped_column(Text, nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class ProfileImage(Base):
    __tablename__ = "profile_images"
    __table_args__ = (
        CheckConstraint("kind IN ('avatar','banner')", name="ck_profile_images_kind"),
        Index("ix_profile_images_file_id", "file_id"),
    )

    user_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("profiles.user_id", ondelete="CASCADE"),
        primary_key=True,
    )
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    file_id: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)

    def to_public_dict(self) -> dict[str, object]:
        return {
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "updated_at": self.updated_at,
        }


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_user_id", "user_id"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    target: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON，不含正文/令牌
    request_ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)

    def to_public_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "sub": self.user_id,
            "action": self.action,
            "target": self.target,
            "result": self.result,
            "detail": json.loads(self.detail) if self.detail else None,
            "request_ip": self.request_ip,
            "created_at": self.created_at,
        }
