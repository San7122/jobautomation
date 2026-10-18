"""Pytest fixtures for UAE Job Copilot tests."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from scraper.jobs import Job


@pytest.fixture()
def tmp_db_path(monkeypatch: pytest.MonkeyPatch) -> Path:
    """Give each test a fresh, isolated SQLite DB path."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    tmp = Path(path)
    monkeypatch.setenv("UAEJOBCOPILOT_TEST_DB_PATH", str(tmp))
    yield tmp
    try:
        tmp.unlink()
    except OSError:
        pass


@pytest.fixture()
def sample_job() -> Job:
    return Job(
        source="indeed",
        title="AI Engineer",
        company="TechCorp",
        location="Dubai, UAE",
        url="https://ae.indeed.com/viewjob?jk=abc",
        description="We are hiring an AI Engineer with Python and LangGraph experience.",
        salary="25000 AED",
        posted="2 days ago",
        job_id="testjob123",
    )
