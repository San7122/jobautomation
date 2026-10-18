"""Tests for config/settings.py"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from config.settings import (
    get_auto_apply_limit,
    get_auto_apply_min_score,
    get_location,
    get_min_score,
    get_prep_limit,
    get_queries,
    get_sources,
    load_env,
)


def test_default_queries(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("QUERIES", raising=False)
    qs = get_queries()
    assert "AI Engineer" in qs
    assert "Agentic AI" in qs


def test_env_queries(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("QUERIES", "LLM Engineer, NLP Engineer")
    qs = get_queries()
    assert qs == ["LLM Engineer", "NLP Engineer"]


def test_default_location(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("JOB_LOCATION", raising=False)
    assert get_location() == "United Arab Emirates"


def test_env_location(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JOB_LOCATION", "Dubai")
    assert get_location() == "Dubai"


def test_default_min_score(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MIN_SCORE", raising=False)
    assert get_min_score() == 70


def test_env_min_score(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MIN_SCORE", "60")
    assert get_min_score() == 60


def test_default_prep_limit(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PREP_LIMIT", raising=False)
    assert get_prep_limit() == 5


def test_default_sources(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("SOURCES", raising=False)
    srcs = get_sources()
    assert "linkedin" in srcs
    assert "indeed" in srcs


def test_load_env_does_not_override_existing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("MIN_SCORE=50\nFOO=bar\n")
    monkeypatch.setenv("MIN_SCORE", "99")  # already set — should win
    load_env(env_file)
    assert os.environ["MIN_SCORE"] == "99"
    assert os.environ["FOO"] == "bar"  # new key gets set
