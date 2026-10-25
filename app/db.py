"""数据库连接、会话与建表。"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings
from .models import Base

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _sqlite_path(database_url: str) -> str | None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None
    return database_url[len(prefix) :]


def init_db(settings: Settings) -> Engine:
    """建引擎、建表（含索引）。可重复调用。"""
    global _engine, _session_factory

    if _engine is not None:
        return _engine

    url = settings.database_url
    is_sqlite = url.startswith("sqlite")
    connect_args: dict[str, object] = {"check_same_thread": False} if is_sqlite else {}

    sqlite_path = _sqlite_path(url)
    if sqlite_path:
        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, connect_args=connect_args, future=True)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):  # pragma: no cover
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    Base.metadata.create_all(engine)

    _engine = engine
    _session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    return engine


def get_db() -> Iterator[Session]:
    if _session_factory is None:
        raise RuntimeError("数据库尚未初始化，请先调用 init_db()")
    session = _session_factory()
    try:
        yield session
    finally:
        session.close()


def get_session_factory() -> sessionmaker[Session]:
    if _session_factory is None:
        raise RuntimeError("数据库尚未初始化，请先调用 init_db()")
    return _session_factory


def session_scope() -> Session:
    return get_session_factory()()


def reset_db_state() -> None:
    """测试用：丢弃引擎与会话工厂。"""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
