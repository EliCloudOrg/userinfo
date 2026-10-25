"""服务级常量：图片种类、scope、审计动作与 ISO 时间格式。"""

from __future__ import annotations

import re

# 头像与 banner 共用同一套存储模型，但上限不同。
KIND_AVATAR = "avatar"
KIND_BANNER = "banner"
IMAGE_KINDS = (KIND_AVATAR, KIND_BANNER)

# SSO 令牌里的 scope（整词匹配）。
SCOPE_USERINFO_READ = "userinfo:read"
SCOPE_USERINFO_WRITE = "userinfo:write"
SCOPE_FILES_READ = "files:read"
SCOPE_FILES_WRITE = "files:write"

# 与 file 服务一致的 id 形状。
FILE_ID_RE = re.compile(r"^f_[A-Za-z0-9_-]{22}$")

ALLOWED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
IMAGE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
}

# 审计动作。
ACTION_PROFILE_UPDATE = "profile_update"
ACTION_IMAGE_UPDATE = "image_update"
ACTION_IMAGE_DELETE = "image_delete"
ACTION_DENY = "deny"

RESULT_OK = "ok"
RESULT_IDEMPOTENT = "idempotent"
RESULT_DENIED = "denied"
RESULT_INVALID = "invalid"
RESULT_NOT_FOUND = "not_found"
RESULT_TOO_LARGE = "too_large"
RESULT_RATE_LIMITED = "rate_limited"
RESULT_UNAVAILABLE = "unavailable"

# 与 file / sso / mc 一致的秒级 UTC 文本格式。
ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# multipart 边界/头开销；只用于 Content-Length 预判。
MULTIPART_OVERHEAD = 64 * 1024
