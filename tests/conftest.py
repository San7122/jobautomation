"""Pytest fixtures for UAE Job Copilot tests."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Point the DB at a temp file so tests never touch the real data/applications.db
os.environ["UAEJOBCOPILOT_TEST_DB"] = "1"


@pytest.fixture()
def tmp_db_path(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Give each test a fresh, isolated SQLite DB path."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    tmp = Path(path)
    monkeypatch.setenv("UAEJOBCOPILOT_TEST_DB_PATH", str(tmp))
    # Patch tracker.db.DB_PATH at import time is not possible, so we patch the
    # module attribute after import inside tests that need it.
    yield tmp
    try:
        tmp.unlink()
    except OSError:
        pass


@pytest.fixture()
def sample_job() -> dict:
    return {
        "job_id": "testjob123",
        "source": "indeed",
        "title": "AI Engineer",
        "company": "TechCorp",
        "location": "Dubai, UAE",
        "url": "https://ae.indeed.com/viewjob?jk=abc",
        "description": "We are hiring an AI Engineer with Python and LangGraph experience.",
        "salary": "25000 AED",
        "posted": "2 days ago",
    }
