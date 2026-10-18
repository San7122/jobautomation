"""Tests for tracker/db.py using an isolated temp DB."""
from __future__ import annotations

from pathlib import Path

import pytest

import tracker.db as db
from scraper.jobs import Job


@pytest.fixture(autouse=True)
def use_tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect DB_PATH to a temp file for every test in this module."""
    tmp_db = tmp_path / "test_applications.db"
    monkeypatch.setattr(db, "DB_PATH", tmp_db)
    db.init_db()
    yield


def test_upsert_and_get_job(sample_job: Job):
    assert db.upsert_job(sample_job) is True
    got = db.get_job(sample_job.job_id)
    assert got is not None
    assert got["title"] == "AI Engineer"
    assert got["company"] == "TechCorp"
    assert got["source"] == "indeed"


def test_job_exists(sample_job: Job):
    assert db.job_exists(sample_job.job_id) is False
    db.upsert_job(sample_job)
    assert db.job_exists(sample_job.job_id) is True


def test_upsert_job_idempotent(sample_job: Job):
    db.upsert_job(sample_job)
    db.upsert_job(sample_job)  # should not raise
    count = db.get_stats()["total_jobs"]
    assert count == 1


def test_save_and_get_application(sample_job: Job):
    db.upsert_job(sample_job)
    db.save_application(
        sample_job.job_id,
        cover_letter="Dear Hiring Team...",
        resume_bullets="• Built X\n• Shipped Y",
        status="ready",
    )
    got = db.get_job(sample_job.job_id)
    assert got is not None
    assert got["app_status"] == "ready"
    assert "Built X" in got["resume_bullets"]


def test_mark_applied(sample_job: Job):
    db.upsert_job(sample_job)
    db.save_application(
        sample_job.job_id,
        cover_letter="Dear Hiring Team...",
        resume_bullets="• Built X",
        status="ready",
    )
    assert db.mark_applied(sample_job.job_id, notes="Applied via email") is True
    got = db.get_job(sample_job.job_id)
    assert got["app_status"] == "applied"


def test_set_outcome(sample_job: Job):
    db.upsert_job(sample_job)
    db.save_application(
        sample_job.job_id,
        cover_letter="Dear Hiring Team...",
        resume_bullets="• Built X",
        status="applied",
    )
    assert db.set_outcome(sample_job.job_id, "interview", notes="Great call") is True
    # get_job doesn't select outcome — query directly
    with db._db() as conn:
        row = conn.execute(
            "SELECT outcome FROM applications WHERE job_id = ?", (sample_job.job_id,)
        ).fetchone()
    assert row is not None
    assert row["outcome"] == "interview"


def test_stats_shape(sample_job: Job):
    db.upsert_job(sample_job)
    st = db.get_stats()
    assert "total_jobs" in st
    assert "must_apply" in st
    assert st["total_jobs"] >= 1
