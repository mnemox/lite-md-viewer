"""Engine, session factory and startup bootstrap.

WAL mode plus a busy timeout matter here for the same reason they did in the .NET host: a
background writer (the file sync service and the indexer) competes with request handlers
for one SQLite file. WAL lets readers and a single writer proceed concurrently, and the
timeout makes any remaining contention wait briefly instead of raising "database is locked".
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import config
from .models import Base, Setting

config.ensure_dirs()

engine: Engine = create_engine(
    f"sqlite:///{config.DB_PATH}",
    future=True,
    # SQLAlchemy's default pool would hand the same connection to the background threads.
    connect_args={"check_same_thread": False, "timeout": 3.0},
)


@event.listens_for(engine, "connect")
def _configure_connection(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=3000")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False,
                            future=True, class_=Session)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


DEFAULT_SETTINGS = {
    "theme": "light",
    "openBrowserOnStart": "false",
}


def init_db() -> None:
    """Create any missing tables and seed default settings."""
    Base.metadata.create_all(engine)
    with SessionLocal() as session:
        existing = set(session.scalars(select(Setting.key)).all())
        added = False
        for key, value in DEFAULT_SETTINGS.items():
            if key not in existing:
                session.add(Setting(key=key, value=value))
                added = True
        if added:
            session.commit()


def get_setting(session: Session, key: str) -> str | None:
    setting = session.get(Setting, key)
    return setting.value if setting else None
