"""请求体模型。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nickname: str
    bio: str | None = None
