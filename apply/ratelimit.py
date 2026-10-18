"""Rate limiting for auto-apply — keeps automation human-like and account-safe.

Daily caps per platform + random delays between submissions. Caps are counted
from the applications table (applied today, per job source); delays are random
sleeps so runs never look like a bot burst.

Env overrides:
    LINKEDIN_DAILY_CAP   (default 15)
    NAUKRI_DAILY_CAP     (default 15)
    INDEED_DAILY_CAP     (default 10)
    APPLY_DELAY_MIN      (default 30 seconds)
    APPLY_DELAY_MAX      (default 90 seconds)
    RATE_LIMIT_ENABLED   (default 1) — set 0 to disable entirely
"""
from __future__ import annotations

import logging
import os
import random
import time
from datetime import datetime, timezone

log = logging.getLogger("ratelimit")

DEFAULT_CAPS = {"linkedin": 15, "naukri": 15, "indeed": 10}
ENV_CAP_KEYS = {"linkedin": "LINKEDIN_DAILY_CAP", "naukri": "NAUKRI_DAILY_CAP", "indeed": "INDEED_DAILY_CAP"}


def rate_limit_enabled() -> bool:
    return os.environ.get("RATE_LIMIT_ENABLED", "1") not in ("0", "false", "False", "")


def daily_cap(platform: str) -> int:
    """Max submissions per day for a platform (env-overridable)."""
    env_key = ENV_CAP_KEYS.get(platform.lower())
    if env_key and os.environ.get(env_key):
        try:
            return int(os.environ[env_key])
        except ValueError:
            pass
    return DEFAULT_CAPS.get(platform.lower(), 15)


def applications_today(platform: str) -> int:
    """How many applications were submitted today for this platform (job source)."""
    from tracker.db import _db

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _db() as conn:
        row = conn.execute(
            """SELECT COUNT(*) c FROM applications a
               JOIN jobs j ON a.job_id = j.job_id
               WHERE LOWER(j.source) = ? AND a.status IN ('applied', 'submitted_unconfirmed')
                 AND a.applied_at IS NOT NULL AND substr(a.applied_at, 1, 10) >= ?""",
            (platform.lower(), today),
        ).fetchone()
        return row["c"] if row else 0


def cap_reached(platform: str) -> bool:
    """True when today's submissions for the platform hit the daily cap."""
    if not rate_limit_enabled():
        return False
    used = applications_today(platform)
    cap = daily_cap(platform)
    if used >= cap:
        log.warning(
            "Rate limit: %s daily cap reached (%d/%d) — skipping further submissions",
            platform, used, cap,
        )
    return used >= cap


def remaining_today(platform: str) -> int:
    """How many more submissions are allowed today for the platform."""
    if not rate_limit_enabled():
        return 999
    return max(0, daily_cap(platform) - applications_today(platform))


def human_delay(min_sec: float | None = None, max_sec: float | None = None) -> float:
    """Sleep a random human-like interval between submissions. Returns seconds slept."""
    if not rate_limit_enabled():
        return 0.0
    lo = float(os.environ.get("APPLY_DELAY_MIN", "30")) if min_sec is None else min_sec
    hi = float(os.environ.get("APPLY_DELAY_MAX", "90")) if max_sec is None else max_sec
    if hi < lo:
        lo, hi = hi, lo
    secs = round(random.uniform(lo, hi), 1)
    log.info("Rate limit: waiting %.0fs before next submission (human delay)", secs)
    time.sleep(secs)
    return secs


def rate_limit_report() -> dict:
    """Status of all platform caps — for `python3 main.py ratelimit` display."""
    out = {}
    for platform in DEFAULT_CAPS:
        used = applications_today(platform)
        cap = daily_cap(platform)
        out[platform] = {"used": used, "cap": cap, "remaining": max(0, cap - used)}
    return out
