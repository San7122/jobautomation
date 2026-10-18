"""Learned question→answer memory for application forms.

Why: LinkedIn/Naukri throw questions our static profile can't answer
("Invalid input", proficiency dropdowns, years counters). First run we
brute-force/truncate/retry — this module remembers what actually worked and
reuses it on the next job. Pairs are stored unconfirmed on fill and marked
confirmed only after a successful submission (so bad guesses don't spread).

Never raises — a broken cache must never break an application.
"""
from __future__ import annotations

import logging

log = logging.getLogger("answercache")

# question (as seen) -> answer used, for the current run — confirmed on submit
_session: list[tuple[str, str]] = []


def reset_session() -> None:
    """Start a new application run."""
    _session.clear()


def lookup(question: str) -> str | None:
    """Best known answer for a question, else None (caller falls back to profile)."""
    if not question:
        return None
    try:
        from tracker.db import get_cached_answer

        return get_cached_answer(question)
    except Exception as e:
        log.debug("answer cache lookup failed: %s", e)
        return None


def remember(question: str, answer: str) -> None:
    """Record a question→answer pair used during this run (unconfirmed)."""
    if not question or not answer:
        return
    _session.append((question, answer))
    try:
        from tracker.db import cache_answer

        cache_answer(question, answer)
    except Exception as e:
        log.debug("answer cache store failed: %s", e)


def confirm_session() -> None:
    """Submission succeeded — trust everything answered in this run."""
    try:
        from tracker.db import confirm_answer

        for question, _ in _session:
            confirm_answer(question)
        if _session:
            log.info("Answer cache: confirmed %d learned answers", len(_session))
    except Exception as e:
        log.debug("answer cache confirm failed: %s", e)


def stats() -> dict:
    try:
        from tracker.db import question_cache_stats

        return question_cache_stats()
    except Exception:
        return {"total": 0, "confirmed": 0, "learning": 0}
