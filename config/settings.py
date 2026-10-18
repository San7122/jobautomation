"""Central configuration: .env loading + search/prep settings (env-overridable)."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from .env into os.environ (never overriding existing vars)."""
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

DEFAULT_QUERIES = [
    "AI Engineer",
    "Machine Learning Engineer",
    "LLM Engineer",
    "GenAI Engineer",
    "RAG Engineer",
    "NLP Engineer",
    "Python AI Engineer",
    "Agentic AI",
]


def get_queries(override: str | None = None) -> list[str]:
    """Queries: --queries flag > QUERIES env (comma/newline separated) > defaults."""
    raw = override or os.environ.get("QUERIES", "")
    if raw:
        return [q.strip() for q in raw.replace("\n", ",").split(",") if q.strip()]
    return list(DEFAULT_QUERIES)


def get_location(override: str | None = None) -> str:
    return override or os.environ.get("JOB_LOCATION", "United Arab Emirates")


def get_sources(override: str | None = None) -> list[str]:
    raw = override or os.environ.get("SOURCES", "linkedin,indeed,naukri,bayt,gulftalent")
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


def get_min_score(override: int | None = None) -> int:
    if override is not None:
        return override
    return int(os.environ.get("MIN_SCORE", "70"))


def get_prep_limit(override: int | None = None) -> int:
    if override is not None:
        return override
    return int(os.environ.get("PREP_LIMIT", "5"))


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def auto_apply_enabled() -> bool:
    """AUTO_APPLY=1 in .env (or the environment) turns on submissions in `run`."""
    return _truthy(os.environ.get("AUTO_APPLY", ""))


def get_auto_apply_limit(override: int | None = None) -> int:
    if override is not None:
        return override
    return int(os.environ.get("AUTO_APPLY_LIMIT", "3"))


def get_auto_apply_min_score(override: int | None = None) -> int:
    if override is not None:
        return override
    return int(os.environ.get("AUTO_APPLY_MIN_SCORE", str(get_min_score())))


def auto_apply_headless() -> bool:
    return _truthy(os.environ.get("AUTO_APPLY_HEADLESS", ""))
