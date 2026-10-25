"""FastAPI 应用装配。

服务内路由（对外由网关剥掉 `/userinfo` 前缀）：

    GET/PUT  /v1/profile              GET/PUT/DELETE /v1/profile/avatar
    GET/PUT/DELETE /v1/profile/banner
    GET      /v1/profiles/{sub}       GET /v1/profiles/{sub}/avatar|banner
    GET      /v1/me                   GET /healthz

`/docs`、`/redoc`、`/openapi.json` 全部关掉。
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .config import Settings, get_settings
from .db import init_db
from .file_client import FileClient
from .routers import health, profiles

logger = logging.getLogger("userinfo")

_MULTIPART_OVERHEAD = 64 * 1024


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level).strip().upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    logger.info("userinfo starting version=%s %s", __version__, settings.log_summary())
    if not settings.required_read_scope or not settings.required_write_scope:
        logger.warning("REQUIRED_*_SCOPE 有空值：对应 scope 校验已关闭（只应在本地调试时如此）")
    try:
        yield
    finally:
        client = getattr(app.state, "file_client", None)
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                logger.exception("关闭 file client 失败")
        logger.info("userinfo stopped")


def create_app(settings: Settings | None = None, *, file_client: FileClient | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    init_db(settings)

    app = FastAPI(
        title="EliCloud UserInfo Service",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    if file_client is None:
        file_client = FileClient(
            settings.file_service_base_url,
            timeout=settings.file_service_timeout_seconds,
        )
    app.state.file_client = file_client

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def access_log(request: Request, call_next):
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled error path=%s", request.url.path)
            raise
        elapsed_ms = (time.perf_counter() - started) * 1000
        user_id = getattr(request.state, "user_id", "-")
        logger.info(
            "%s %s -> %s sub=%s %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            user_id,
            elapsed_ms,
        )
        return response

    @app.middleware("http")
    async def upload_size_precheck(request: Request, call_next):
        if request.method == "PUT" and request.url.path.rstrip("/") in (
            "/v1/profile/avatar",
            "/v1/profile/banner",
        ):
            kind = request.url.path.rstrip("/").rsplit("/", 1)[-1]
            max_bytes = settings.max_bytes_for(kind)
            raw = request.headers.get("content-length")
            if raw:
                try:
                    length = int(raw)
                except ValueError:
                    length = 0
                if length > max_bytes + _MULTIPART_OVERHEAD:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": "image_too_large",
                            "error_description": f"图片超过上限 {max_bytes // (1024 * 1024)} MiB",
                        },
                    )
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        content: dict[str, Any]
        if isinstance(detail, dict) and "error" in detail:
            content = dict(detail)
        else:
            content = {"error": "http_error", "error_description": str(detail)}
        return JSONResponse(status_code=exc.status_code, content=content, headers=dict(exc.headers or {}))

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(item.get("loc", ())), "msg": item.get("msg"), "type": item.get("type")}
            for item in exc.errors()
        ]
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_request", "error_description": "请求参数不合法", "details": details},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception")
        return JSONResponse(
            status_code=500,
            content={"error": "server_error", "error_description": "服务内部错误"},
        )

    app.include_router(profiles.router)
    app.include_router(health.router)
    return app


app = create_app()
