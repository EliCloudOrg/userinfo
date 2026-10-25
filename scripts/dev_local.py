"""本机直接跑 userinfo（不经过 Docker）。

用法（仓库根目录）：

    python scripts/dev_local.py

默认连本地 SSO/JWKS 与本地 file；数据库落在 ./data/userinfo.db。
生产/容器部署请用 docker-compose.yml 或 /srv/userinfo/deploy.sh。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{(ROOT / 'data' / 'userinfo.db').as_posix()}")
os.environ.setdefault("SSO_ISSUER", "http://127.0.0.1:8080/auth")
os.environ.setdefault("SSO_JWKS_URL", "http://127.0.0.1:8080/auth/.well-known/jwks.json")
os.environ.setdefault("JWT_AUDIENCE", "elicloud-services")
os.environ.setdefault("FILE_SERVICE_BASE_URL", "http://127.0.0.1:8080/file")
os.environ.setdefault("LOG_LEVEL", "info")


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8003, reload=False)


if __name__ == "__main__":
    main()
