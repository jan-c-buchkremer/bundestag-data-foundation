import shutil
import sqlite3
from pathlib import Path

import pytest

from bdf import db, ingest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A data directory whose raw/ is a copy of tests/fixtures (the store is built fresh)."""
    shutil.copytree(FIXTURES, tmp_path / "raw")
    monkeypatch.setenv("BDF_DATA_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def store(data_dir: Path) -> sqlite3.Connection:
    conn = db.connect(data_dir / "bundestag.sqlite")
    ingest.ingest_all(conn)
    return conn
