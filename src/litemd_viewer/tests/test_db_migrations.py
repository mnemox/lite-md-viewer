"""Schema-upgrade policy for init_db.

Setup must never delete or recreate an existing database. A fresh database is created in
full; an existing one that is behind the schema is migrated only when upgrades are allowed,
and otherwise startup fails with a clear error while the database is left untouched.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from app.db import DatabaseUpgradeRequired, SessionLocal, engine, init_db
from app.models import Base, Board


def _tables() -> set[str]:
    return set(inspect(engine).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


def _drop(*tables: str) -> None:
    # Children before parents, so foreign keys never block the drop.
    with engine.begin() as conn:
        for name in tables:
            conn.execute(text(f"DROP TABLE IF EXISTS {name}"))


def _board_count() -> int:
    # Raw count, so it works even mid-migration when the ORM's columns aren't all present yet.
    with engine.begin() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM boards")).scalar_one()


def test_fresh_database_is_created_without_the_flag():
    # A brand-new install has no tables; creating them is not an "upgrade".
    Base.metadata.drop_all(engine)
    assert "boards" not in _tables()

    init_db(allow_upgrade=False)

    assert {"boards", "board_lists", "board_cards", "settings"} <= _tables()


def test_up_to_date_database_is_a_noop_without_the_flag():
    # clean_database has already created the full, current schema.
    with SessionLocal() as s:
        s.add(Board(name="keep me"))
        s.commit()

    init_db(allow_upgrade=False)   # nothing pending -> succeeds

    assert _board_count() == 1


def test_missing_table_fails_without_the_flag_and_leaves_data_intact():
    with SessionLocal() as s:
        s.add(Board(name="keep me"))
        s.commit()
    _drop("board_cards")
    assert "board_cards" not in _tables()

    with pytest.raises(DatabaseUpgradeRequired) as excinfo:
        init_db(allow_upgrade=False)

    # The database was not touched: the table is still absent and the data survives.
    assert "board_cards" not in _tables()
    assert "board_cards" in excinfo.value.missing_tables
    assert _board_count() == 1
    # The message tells the user how to proceed.
    assert "--update-db" in str(excinfo.value)


def test_missing_table_is_migrated_with_the_flag_without_losing_data():
    with SessionLocal() as s:
        s.add(Board(name="keep me"))
        s.commit()
    _drop("board_cards")

    init_db(allow_upgrade=True)

    assert "board_cards" in _tables()   # created
    assert _board_count() == 1          # existing data preserved


def test_missing_column_is_detected_and_added_only_with_the_flag():
    # Recreate `boards` as an older install would have it: without the later `color` column.
    _drop("board_cards", "board_lists", "boards")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE boards ("
            "  id INTEGER PRIMARY KEY, name VARCHAR, x FLOAT, y FLOAT, z INTEGER,"
            "  created_utc DATETIME, updated_utc DATETIME)"
        ))
        conn.execute(text("INSERT INTO boards (id, name) VALUES (1, 'old')"))
    assert "color" not in _columns("boards")

    with pytest.raises(DatabaseUpgradeRequired) as excinfo:
        init_db(allow_upgrade=False)
    assert ("boards", "color") in excinfo.value.missing_columns
    assert "color" not in _columns("boards")   # untouched
    assert _board_count() == 1

    init_db(allow_upgrade=True)
    assert "color" in _columns("boards")        # added in place
    assert _board_count() == 1                  # row kept
