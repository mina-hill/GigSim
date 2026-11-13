"""Database engine and session for GigSim (SQLite by default, any SQLAlchemy URL works)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def init_engine(url: str) -> Engine:
    global _engine, _SessionLocal
    if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url:
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    _engine = create_engine(url, future=True, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(_engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("GigSim database not initialised; call init_engine() first")
    return _engine


def session_scope() -> Session:
    if _SessionLocal is None:
        raise RuntimeError("GigSim database not initialised; call init_engine() first")
    return _SessionLocal()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    session = session_scope()
    try:
        yield session
    finally:
        session.close()


def create_all() -> None:
    from gigsim import models  # noqa: F401  (registers tables)

    Base.metadata.create_all(get_engine())


def drop_all() -> None:
    from gigsim import models  # noqa: F401

    Base.metadata.drop_all(get_engine())
