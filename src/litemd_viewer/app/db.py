"""Engine, session factory and startup bootstrap.

WAL mode plus a busy timeout matter here for the same reason they did in the .NET host: a
background writer (the file sync service and the indexer) competes with request handlers
for one SQLite file. WAL lets readers and a single writer proceed concurrently, and the
timeout makes any remaining contention wait briefly instead of raising "database is locked".
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event, inspect, select, text
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


# Columns added to already-existing tables after they first shipped. Each entry is
# (table, column, sql_type) and is applied only when the table exists but the column does
# not, so upgraded installs gain the column without losing data. New *tables* need no entry
# here -- Base.metadata.create_all adds them.
_COLUMN_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("dashboard_notes", "width", "INTEGER"),
    ("dashboard_notes", "height", "INTEGER"),
    ("document_note_groups", "width", "INTEGER"),
    ("document_note_groups", "height", "INTEGER"),
    ("boards", "color", "VARCHAR(7)"),
    ("dashboard_notes", "color", "VARCHAR(7)"),
)


class DatabaseUpgradeRequired(RuntimeError):
    """An existing database is behind the current schema and upgrades were not allowed.

    Raised instead of touching the database, so setup fails loudly rather than migrating a
    user's data without permission. Carries the pending changes for a clear message.
    """

    def __init__(
        self, missing_tables: list[str], missing_columns: list[tuple[str, str]]
    ) -> None:
        self.missing_tables = missing_tables
        self.missing_columns = missing_columns
        parts = []
        if missing_tables:
            parts.append("missing tables: " + ", ".join(sorted(missing_tables)))
        if missing_columns:
            parts.append(
                "missing columns: "
                + ", ".join(f"{t}.{c}" for t, c in missing_columns)
            )
        detail = "; ".join(parts) if parts else "schema changes are pending"
        super().__init__(
            "The existing database is behind this version's schema "
            f"({detail}).\n"
            "Setup will not change it automatically, so it has been left untouched. "
            "To migrate it in place -- which only adds tables and columns and never "
            "deletes data -- re-run with database upgrades allowed:\n"
            "    run.bat --update-db\n"
            "or set the environment variable LITEMD_ALLOW_DB_UPGRADE=1 before starting."
        )


def _add_column_if_missing(conn, table: str, column: str, sql_type: str = "INTEGER") -> None:
    """Idempotent ALTER TABLE for existing installs."""
    cols = [r[1] for r in conn.execute(text(f"PRAGMA table_info({table})"))]
    if column not in cols:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"))


def _pending_schema_changes(conn) -> tuple[list[str], list[tuple[str, str]]]:
    """Tables and columns the current schema expects that the database does not yet have."""
    insp = inspect(conn)
    existing_tables = set(insp.get_table_names())
    missing_tables = [t for t in Base.metadata.tables if t not in existing_tables]
    missing_columns: list[tuple[str, str]] = []
    for table, column, _sql_type in _COLUMN_MIGRATIONS:
        if table in existing_tables:
            cols = {c["name"] for c in insp.get_columns(table)}
            if column not in cols:
                missing_columns.append((table, column))
    return missing_tables, missing_columns


def init_db(allow_upgrade: bool | None = None) -> None:
    """Bring the database up to the current schema, without ever dropping data.

    A brand-new database (none of our tables present yet) is created in full -- that is
    initial setup, not an upgrade, so it never needs permission. An existing database that
    is behind the schema is migrated only when upgrades are allowed (the ``allow_upgrade``
    argument, defaulting to ``LITEMD_ALLOW_DB_UPGRADE``); otherwise this raises
    :class:`DatabaseUpgradeRequired` and leaves the database untouched. Every migration is
    additive -- ``create_all`` never drops a table and ``_add_column_if_missing`` only adds
    columns -- so the existing database is never deleted or recreated.
    """
    if allow_upgrade is None:
        allow_upgrade = config.ALLOW_DB_UPGRADE

    with engine.begin() as conn:
        existing_tables = set(inspect(conn).get_table_names())
        # A database "pre-exists" once any of our tables is present; only then is creating
        # the rest an upgrade of someone's data rather than a first-time install.
        db_preexists = any(t in existing_tables for t in Base.metadata.tables)
        missing_tables, missing_columns = _pending_schema_changes(conn)

    if db_preexists and (missing_tables or missing_columns) and not allow_upgrade:
        raise DatabaseUpgradeRequired(missing_tables, missing_columns)

    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for table, column, sql_type in _COLUMN_MIGRATIONS:
            _add_column_if_missing(conn, table, column, sql_type)

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
