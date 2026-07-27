"""Shared fixtures.

The environment is configured before `app` is imported anywhere, because config.py reads
these variables at import time. Every test therefore runs against a throwaway data directory
with embedding switched off, so nothing touches the real database or downloads a model.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE_ROOT))

_TMP_DATA = tempfile.mkdtemp(prefix="litemd_tests_")
os.environ["LITEMD_DATA_DIR"] = _TMP_DATA
os.environ["LITEMD_DISABLE_EMBEDDING"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    """A fresh schema for every test, so ids and counts are predictable."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client():
    """A TestClient used without its context manager, so the lifespan does not run.

    That keeps the filesystem watcher and the indexer task out of API tests; the schema is
    created by the clean_database fixture instead.
    """
    from app.main import app

    return TestClient(app)


@pytest.fixture()
def session():
    with SessionLocal() as db:
        yield db


@pytest.fixture()
def docs_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "docs"
    directory.mkdir()
    return directory


def write_doc(directory: Path, name: str, text: str) -> str:
    path = directory / name
    path.write_text(text, encoding="utf-8", newline="")
    return str(path)


@pytest.fixture()
def data_dir() -> Path:
    return config.DATA_DIR
