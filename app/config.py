"""全部配置从环境变量读取（与 sso / file / mc 同风格）。"""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .constants import ALLOWED_IMAGE_TYPES


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # ---- SSO 接入 -----------------------------------------------------------
    sso_issuer: str = "https://146.56.237.33/auth"
    sso_jwks_url: str = "https://146.56.237.33/auth/.well-known/jwks.json"
    jwt_audience: str = "elicloud-services"
    # 空串 = 关闭该校验（只允许本地调试；启动时打 WARNING）
    required_read_scope: str = "userinfo:read"
    required_write_scope: str = "userinfo:write"
    required_file_read_scope: str = "files:read"
    required_file_write_scope: str = "files:write"
    jwks_cache_seconds: int = 300
    jwt_leeway_seconds: int = 30
    jwks_timeout_seconds: float = 5.0

    # ---- 元数据存储 ---------------------------------------------------------
    database_url: str = "sqlite:////data/userinfo.db"

    # ---- file 服务 ----------------------------------------------------------
    file_service_base_url: str = "http://file:8000"
    file_service_timeout_seconds: float = 15.0

    # ---- 图片 ---------------------------------------------------------------
    max_avatar_bytes: int = 5 * 1024 * 1024
    max_banner_bytes: int = 10 * 1024 * 1024
    allowed_image_types: str = ",".join(ALLOWED_IMAGE_TYPES)

    # ---- 文本 ---------------------------------------------------------------
    max_nickname_chars: int = 32
    max_bio_chars: int = 500

    # ---- 限流 ---------------------------------------------------------------
    write_attempts_per_window: int = 60
    image_attempts_per_window: int = 20
    write_window_seconds: int = 600

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    log_level: str = "info"

    # ------------------------------------------------------------------ 校验
    @field_validator("sso_issuer")
    @classmethod
    def _check_issuer(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("SSO_ISSUER 必须以 http:// 或 https:// 开头")
        if cleaned.endswith("/v1") or "/.well-known" in cleaned:
            raise ValueError("SSO_ISSUER 只应到服务前缀（如 .../auth），不要带 /v1 或 /.well-known")
        return cleaned

    @field_validator("sso_jwks_url")
    @classmethod
    def _check_jwks_url(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("SSO_JWKS_URL 必须以 http:// 或 https:// 开头")
        return cleaned

    @field_validator(
        "required_read_scope",
        "required_write_scope",
        "required_file_read_scope",
        "required_file_write_scope",
    )
    @classmethod
    def _check_scope(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned and any(ch.isspace() for ch in cleaned):
            raise ValueError("REQUIRED_*_SCOPE 只能是单个 scope（不能含空格）")
        return cleaned

    @field_validator("database_url", "file_service_base_url")
    @classmethod
    def _check_non_empty(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("该配置项不能为空")
        return cleaned

    @field_validator("file_service_base_url")
    @classmethod
    def _check_file_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError("FILE_SERVICE_BASE_URL 必须以 http:// 或 https:// 开头")
        return cleaned

    @field_validator("allowed_image_types")
    @classmethod
    def _check_image_types(cls, value: str) -> str:
        items = [item.strip().lower() for item in str(value).split(",") if item.strip()]
        if not items:
            raise ValueError("ALLOWED_IMAGE_TYPES 不能为空")
        unknown = [item for item in items if item not in ALLOWED_IMAGE_TYPES]
        if unknown:
            raise ValueError(f"ALLOWED_IMAGE_TYPES 含不支持的类型：{', '.join(unknown)}")
        return ",".join(dict.fromkeys(items))

    @field_validator(
        "max_avatar_bytes",
        "max_banner_bytes",
        "max_nickname_chars",
        "max_bio_chars",
        "write_attempts_per_window",
        "image_attempts_per_window",
        "write_window_seconds",
        "jwks_cache_seconds",
    )
    @classmethod
    def _check_positive(cls, value: int) -> int:
        if int(value) < 1:
            raise ValueError("该配置项必须是正整数")
        return int(value)

    @field_validator("file_service_timeout_seconds", "jwks_timeout_seconds")
    @classmethod
    def _check_timeout(cls, value: float) -> float:
        if float(value) <= 0:
            raise ValueError("超时必须是正数")
        return float(value)

    # ------------------------------------------------------------ 派生属性
    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def allowed_image_type_list(self) -> tuple[str, ...]:
        return tuple(item for item in self.allowed_image_types.split(",") if item)

    def max_bytes_for(self, kind: str) -> int:
        return self.max_avatar_bytes if kind == "avatar" else self.max_banner_bytes

    def log_summary(self) -> dict[str, object]:
        return {
            "issuer": self.sso_issuer,
            "jwks_url": self.sso_jwks_url,
            "audience": self.jwt_audience,
            "required_read_scope": self.required_read_scope or "(disabled)",
            "required_write_scope": self.required_write_scope or "(disabled)",
            "required_file_read_scope": self.required_file_read_scope or "(disabled)",
            "required_file_write_scope": self.required_file_write_scope or "(disabled)",
            "database_url": self.database_url,
            "file_service_base_url": self.file_service_base_url,
            "max_avatar_bytes": self.max_avatar_bytes,
            "max_banner_bytes": self.max_banner_bytes,
            "allowed_image_types": self.allowed_image_type_list,
            "max_nickname_chars": self.max_nickname_chars,
            "max_bio_chars": self.max_bio_chars,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """测试用：清掉缓存，让下一次 get_settings() 重新读环境变量。"""
    get_settings.cache_clear()
