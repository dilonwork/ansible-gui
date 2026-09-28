"""Database setup: SQLAlchemy engine + session handling.

DATABASE_URL selects the backend:
- postgresql://user:pass@host/db   (docker compose)
- sqlite:///./ansible_gui.db        (local dev default)
- tests point it at a temp SQLite file via the env var
"""
import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", "sqlite:///./ansible_gui.db")


def _engine_kwargs(url: str) -> dict:
    if url.startswith("sqlite"):
        # background job threads share this engine
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True}


_engine = create_engine(_database_url(), **_engine_kwargs(_database_url()))
SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
Base = declarative_base()


def init_db() -> None:
    from . import models  # noqa: F401  (register tables before create_all)
    Base.metadata.create_all(_engine)


@contextmanager
def session_scope():
    """Short-lived session per operation; commits on success, rolls back on error."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
