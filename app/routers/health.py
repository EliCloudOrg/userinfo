"""健康检查：DB 必须可用；file 不可达只标记 degraded，不触发容器重启。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .. import __version__
from ..deps import DbDep

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/healthz")
def healthz(request: Request, db: DbDep) -> JSONResponse:
    checks: dict[str, Any] = {}
    healthy = True

    try:
        db.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception:  # noqa: BLE001
        logger.exception("healthz: 数据库不可用")
        checks["database"] = {"ok": False, "error": "database_unavailable"}
        healthy = False

    client = getattr(request.app.state, "file_client", None)
    if client is None:
        checks["file"] = {"ok": False, "error": "not_configured"}
    else:
        try:
            checks["file"] = {"ok": bool(client.health())}
        except Exception:  # noqa: BLE001
            checks["file"] = {"ok": False, "error": "file_service_unavailable"}

    file_ok = bool(checks["file"].get("ok"))
    status = "ok" if healthy and file_ok else ("degraded" if healthy else "error")

    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": status,
            "version": __version__,
            "checks": checks,
        },
    )
