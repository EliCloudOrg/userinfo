"""测试环境准备：假 JWKS + 内存 file + 临时 SQLite。"""

from __future__ import annotations

import base64
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.fake_jwks import FakeJwksServer  # noqa: E402
from tests.helpers import (  # noqa: E402
    TEST_AUDIENCE,
    TEST_FILE_READ_SCOPE,
    TEST_FILE_WRITE_SCOPE,
    TEST_ISSUER,
    TEST_KID,
    TEST_KEY,
    TEST_READ_SCOPE,
    TEST_SCOPE,
    TEST_WRITE_SCOPE,
    bearer,
    make_token,
)

TEST_DIR = Path(tempfile.mkdtemp(prefix="userinfo-tests-"))
_JWKS = FakeJwksServer(TEST_KEY, TEST_KID, max_age=300).start()

os.environ.update(
    {
        "SSO_ISSUER": TEST_ISSUER,
        "SSO_JWKS_URL": _JWKS.url,
        "JWT_AUDIENCE": TEST_AUDIENCE,
        "REQUIRED_READ_SCOPE": TEST_READ_SCOPE,
        "REQUIRED_WRITE_SCOPE": TEST_WRITE_SCOPE,
        "REQUIRED_FILE_READ_SCOPE": TEST_FILE_READ_SCOPE,
        "REQUIRED_FILE_WRITE_SCOPE": TEST_FILE_WRITE_SCOPE,
        "DATABASE_URL": f"sqlite:///{(TEST_DIR / 'userinfo-test.db').as_posix()}",
        "FILE_SERVICE_BASE_URL": "http://file.test",
        "MAX_AVATAR_BYTES": "65536",
        "MAX_BANNER_BYTES": "131072",
        "MAX_NICKNAME_CHARS": "32",
        "MAX_BIO_CHARS": "500",
        "WRITE_ATTEMPTS_PER_WINDOW": "100000",
        "IMAGE_ATTEMPTS_PER_WINDOW": "100000",
        "WRITE_WINDOW_SECONDS": "600",
        "LOG_LEVEL": "warning",
    }
)

from tests.fake_file import FakeFileService  # noqa: E402


# --------------------------------------------------------------- 基础设施


@pytest.fixture(scope="session", autouse=True)
def _session_cleanup():
    yield
    from app.db import reset_db_state

    reset_db_state()
    _JWKS.stop()
    shutil.rmtree(TEST_DIR, ignore_errors=True)


@pytest.fixture(scope="session")
def fake_file() -> FakeFileService:
    return FakeFileService()


# ------------------------------------------------------------------- 应用


@pytest.fixture(scope="session")
def client(fake_file):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(file_client=fake_file)) as test_client:
        yield test_client


@pytest.fixture
def client_factory(fake_file):
    from fastapi.testclient import TestClient

    from app.config import Settings, get_settings
    from app.main import create_app

    created = []

    def _make(**overrides) -> TestClient:
        settings = Settings(**overrides)
        app = create_app(settings, file_client=fake_file)
        app.dependency_overrides[get_settings] = lambda: settings
        test_client = TestClient(app)
        test_client.__enter__()
        created.append(test_client)
        return test_client

    yield _make
    for test_client in created:
        test_client.__exit__(None, None, None)


# ------------------------------------------------------------------ 令牌


@pytest.fixture
def token():
    def _make(**kwargs) -> str:
        return make_token(**kwargs)

    return _make


@pytest.fixture
def auth():
    def _make(sub: str = "user_0001", scope: str | None = TEST_SCOPE, **kwargs) -> dict[str, str]:
        return bearer(make_token(sub=sub, scope=scope, **kwargs))

    return _make


@pytest.fixture
def read_auth():
    def _make(sub: str = "user_0001", **kwargs) -> dict[str, str]:
        return bearer(
            make_token(sub=sub, scope=f"openid {TEST_READ_SCOPE} {TEST_FILE_READ_SCOPE}", **kwargs)
        )

    return _make


# --------------------------------------------------------------- 隔离与工具


@pytest.fixture(autouse=True)
def _clean_state(fake_file):
    from sqlalchemy import delete

    from app.config import get_settings
    from app.db import init_db, session_scope
    from app.errors import limiter
    from app.models import AuditLog, Profile, ProfileImage

    init_db(get_settings())
    with session_scope() as session:
        session.execute(delete(ProfileImage))
        session.execute(delete(Profile))
        session.execute(delete(AuditLog))
        session.commit()

    limiter.clear()
    fake_file.clear()
    yield
    limiter.clear()


@pytest.fixture
def db_rows():
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import AuditLog, Profile, ProfileImage

    def _rows(model, **filters):
        with session_scope() as session:
            statement = select(model)
            for column, value in filters.items():
                statement = statement.where(getattr(model, column) == value)
            return list(session.scalars(statement).all())

    return {
        "profiles": lambda **filters: _rows(Profile, **filters),
        "images": lambda **filters: _rows(ProfileImage, **filters),
        "audits": lambda **filters: _rows(AuditLog, **filters),
    }


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"0" * 64
WEBP_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"0" * 32
