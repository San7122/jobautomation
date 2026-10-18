"""SQLite application tracker."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

DB_PATH = Path(__file__).parent.parent / "data" / "applications.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """Connection that always closes (and rolls back on error)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db():
    with _db() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            source TEXT,
            title TEXT,
            company TEXT,
            location TEXT,
            url TEXT,
            description TEXT,
            first_seen TEXT,
            score INTEGER DEFAULT 0,
            match_reasons TEXT DEFAULT '[]',
            gaps TEXT DEFAULT '[]',
            must_apply INTEGER DEFAULT 0
        )""")
        conn.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            job_id TEXT PRIMARY KEY,
            status TEXT DEFAULT 'pending',
            cover_letter TEXT,
            resume_bullets TEXT,
            applied_at TEXT,
            notes TEXT,
            FOREIGN KEY (job_id) REFERENCES jobs(job_id)
        )""")
        # Outcome tracking (feedback loop): which applications actually got replies
        app_cols = [r["name"] for r in conn.execute("PRAGMA table_info(applications)").fetchall()]
        if "outcome" not in app_cols:
            conn.execute("ALTER TABLE applications ADD COLUMN outcome TEXT")
        if "outcome_at" not in app_cols:
            conn.execute("ALTER TABLE applications ADD COLUMN outcome_at TEXT")
        # Cold mail contacts table
        conn.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            email TEXT PRIMARY KEY,
            name TEXT,
            company TEXT,
            role TEXT,
            source TEXT DEFAULT 'manual',
            status TEXT DEFAULT 'new',
            -- new → contacted → replied → interested → hired
            -- statuses: new, contacted, opened, replied, interested, not_interested, bounced
            first_seen TEXT,
            last_contacted TEXT,
            notes TEXT
        )""")
        # Cold mail send log
        conn.execute("""
        CREATE TABLE IF NOT EXISTS coldmail_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT,
            subject TEXT,
            sent_at TEXT,
            status TEXT DEFAULT 'sent',
            error_message TEXT,
            FOREIGN KEY (email) REFERENCES contacts(email)
        )""")
        
        # Automation runs log
        conn.execute("""
        CREATE TABLE IF NOT EXISTS automation_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_type TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            status TEXT DEFAULT 'running',
            results TEXT,
            email_sent INTEGER DEFAULT 0
        )""")

        # Learned question -> answer cache (stuck-question memory)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS question_answers (
            question_norm TEXT PRIMARY KEY,
            answer TEXT NOT NULL,
            confirmed INTEGER NOT NULL DEFAULT 0,
            times_used INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")


def upsert_job(job, score_info: dict | None = None) -> bool:
    """Insert or update a job. Returns True if newly inserted."""
    with _db() as conn:
        existing = conn.execute(
            "SELECT job_id, description FROM jobs WHERE job_id = ?", (job.job_id,)
        ).fetchone()
        if existing:
            if job.description and not (existing["description"] or "").strip():
                conn.execute(
                    "UPDATE jobs SET description=? WHERE job_id=?",
                    (job.description, job.job_id),
                )
            if score_info:
                conn.execute(
                    """UPDATE jobs SET score=?, match_reasons=?, gaps=?, must_apply=? WHERE job_id=?""",
                    (
                        score_info.get("score", 0),
                        json.dumps(score_info.get("match_reasons", [])),
                        json.dumps(score_info.get("gaps", [])),
                        int(score_info.get("must_apply", False)),
                        job.job_id,
                    ),
                )
            return False
        conn.execute(
            """INSERT INTO jobs (job_id, source, title, company, location, url, description, first_seen,
               score, match_reasons, gaps, must_apply)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job.job_id,
                job.source,
                job.title,
                job.company,
                job.location,
                job.url,
                job.description,
                datetime.now(timezone.utc).isoformat(),
                (score_info or {}).get("score", 0),
                json.dumps((score_info or {}).get("match_reasons", [])),
                json.dumps((score_info or {}).get("gaps", [])),
                int((score_info or {}).get("must_apply", False)),
            ),
        )
        return True


def job_ids_with_description() -> set[str]:
    """Job ids that already have a description stored (avoids refetching)."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT job_id FROM jobs WHERE TRIM(IFNULL(description, '')) <> ''"
        ).fetchall()
        return {r["job_id"] for r in rows}


def set_job_description(job_id: str, description: str) -> None:
    with _db() as conn:
        conn.execute("UPDATE jobs SET description=? WHERE job_id=?", (description, job_id))


def save_application(job_id: str, cover_letter: str, resume_bullets: str, status: str = "ready"):
    with _db() as conn:
        conn.execute(
            """INSERT INTO applications (job_id, status, cover_letter, resume_bullets, notes)
               VALUES (?,?,?,?,'') ON CONFLICT(job_id) DO UPDATE SET
               status=CASE WHEN applications.status='applied' THEN 'applied' ELSE excluded.status END,
               cover_letter=excluded.cover_letter, resume_bullets=excluded.resume_bullets""",
            (job_id, status, cover_letter, resume_bullets),
        )


def mark_applied(job_id: str, notes: str = "") -> bool:
    """Mark an application applied. Returns False if no prepped application exists."""
    with _db() as conn:
        cur = conn.execute(
            "UPDATE applications SET status='applied', applied_at=?, notes=? WHERE job_id=?",
            (datetime.now(timezone.utc).isoformat(), notes, job_id),
        )
        return cur.rowcount > 0


def job_exists(job_id: str) -> bool:
    with _db() as conn:
        return conn.execute("SELECT 1 FROM jobs WHERE job_id=?", (job_id,)).fetchone() is not None


def get_job(job_id: str) -> dict | None:
    with _db() as conn:
        row = conn.execute(
            """SELECT j.*, a.status AS app_status, a.cover_letter, a.resume_bullets, a.applied_at
               FROM jobs j LEFT JOIN applications a ON j.job_id = a.job_id
               WHERE j.job_id = ?""",
            (job_id,),
        ).fetchone()
        return dict(row) if row else None


def list_jobs(
    min_score: int = 0,
    source: str = "",
    status: str = "",
    query: str = "",
    limit: int = 300,
) -> list[dict]:
    """Jobs for the review UI. status: '', 'none' (never prepped), 'ready', 'applied'."""
    sql = """SELECT j.job_id, j.source, j.title, j.company, j.location, j.url, j.description,
                    j.first_seen, j.score, j.match_reasons, j.gaps, j.must_apply,
                    a.status AS app_status, a.cover_letter, a.resume_bullets, a.applied_at
             FROM jobs j LEFT JOIN applications a ON j.job_id = a.job_id
             WHERE j.score >= ?"""
    params: list = [min_score]
    if source:
        sql += " AND j.source = ?"
        params.append(source)
    if status == "none":
        sql += " AND a.job_id IS NULL"
    elif status:
        sql += " AND a.status = ?"
        params.append(status)
    if query:
        sql += " AND (j.title LIKE ? OR j.company LIKE ? OR j.description LIKE ?)"
        like = f"%{query}%"
        params += [like, like, like]
    sql += " ORDER BY j.score DESC, j.first_seen DESC LIMIT ?"
    params.append(limit)
    with _db() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_pending_ready() -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            """SELECT j.job_id, j.title, j.company, j.location, j.url, j.score, j.match_reasons,
                      j.source, a.cover_letter, a.resume_bullets, a.status
               FROM jobs j JOIN applications a ON j.job_id = a.job_id
               WHERE a.status = 'ready' ORDER BY j.score DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_applied_recent(hours: int = 24) -> list[dict]:
    """Applications marked applied in the last N hours (for digest + reports)."""
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _db() as conn:
        rows = conn.execute(
            """SELECT j.job_id, j.title, j.company, j.location, j.url, j.score, j.source,
                      a.applied_at, a.notes
               FROM applications a JOIN jobs j ON j.job_id = a.job_id
               WHERE a.status = 'applied' AND COALESCE(a.applied_at, '') >= ?
               ORDER BY a.applied_at DESC""",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_top_jobs(min_score: int = 60, limit: int = 20, exclude_prepped: bool = False) -> list[dict]:
    sql = """SELECT job_id, title, company, location, url, source, description, score,
                    match_reasons, gaps, must_apply
             FROM jobs WHERE score >= ? AND job_id NOT IN (SELECT job_id FROM applications WHERE status='applied')"""
    params: list = [min_score]
    if exclude_prepped:
        sql += " AND job_id NOT IN (SELECT job_id FROM applications)"
    sql += " ORDER BY score DESC LIMIT ?"
    params.append(limit)
    with _db() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_stats() -> dict:
    with _db() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
        applied = conn.execute("SELECT COUNT(*) c FROM applications WHERE status='applied'").fetchone()["c"]
        ready = conn.execute("SELECT COUNT(*) c FROM applications WHERE status='ready'").fetchone()["c"]
        must = conn.execute("SELECT COUNT(*) c FROM jobs WHERE must_apply=1").fetchone()["c"]
        no_desc = conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE description IS NULL OR TRIM(description)=''"
        ).fetchone()["c"]
        stats = {
            "total_jobs": total,
            "applied": applied,
            "ready": ready,
            "must_apply": must,
            "missing_description": no_desc,
        }
    try:
        stats["outcomes"] = get_outcome_stats()
    except Exception:
        pass
    return stats


# ─── Cold Mail Contacts ──────────────────────────────────────────────────────

def upsert_contact(email: str, name: str = "", company: str = "", role: str = "", source: str = "manual") -> bool:
    """Insert or update a contact. Returns True if newly inserted."""
    email = email.strip().lower()
    with _db() as conn:
        existing = conn.execute("SELECT email FROM contacts WHERE email = ?", (email,)).fetchone()
        if existing:
            # Update non-empty fields
            if name:
                conn.execute("UPDATE contacts SET name = ? WHERE email = ?", (name, email))
            if company:
                conn.execute("UPDATE contacts SET company = ? WHERE email = ?", (company, email))
            if role:
                conn.execute("UPDATE contacts SET role = ? WHERE email = ?", (role, email))
            return False
        conn.execute(
            """INSERT INTO contacts (email, name, company, role, source, first_seen)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (email, name, company, role, source, datetime.now(timezone.utc).isoformat()),
        )
        return True


def get_contacts(status: str | None = None, limit: int = 100) -> list[dict]:
    """Get contacts, optionally filtered by status."""
    sql = "SELECT * FROM contacts"
    params: list = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY first_seen DESC LIMIT ?"
    params.append(limit)
    with _db() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def mark_contact_contacted(email: str, notes: str = ""):
    """Mark a contact as contacted."""
    email = email.strip().lower()
    with _db() as conn:
        conn.execute(
            """UPDATE contacts SET status = 'contacted', last_contacted = ?, notes = ?
               WHERE email = ?""",
            (datetime.now(timezone.utc).isoformat(), notes, email),
        )


def update_contact_status(email: str, status: str, notes: str = ""):
    """Update contact status (new, contacted, opened, replied, interested, not_interested, bounced)."""
    email = email.strip().lower()
    with _db() as conn:
        conn.execute(
            "UPDATE contacts SET status = ?, notes = ? WHERE email = ?",
            (status, notes, email),
        )


def get_contact_stats() -> dict:
    """Get contact statistics."""
    with _db() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM contacts").fetchone()["c"]
        new = conn.execute("SELECT COUNT(*) c FROM contacts WHERE status='new'").fetchone()["c"]
        contacted = conn.execute("SELECT COUNT(*) c FROM contacts WHERE status='contacted'").fetchone()["c"]
        replied = conn.execute("SELECT COUNT(*) c FROM contacts WHERE status IN ('replied','interested')").fetchone()["c"]
        today_sent = conn.execute(
            """SELECT COUNT(*) c FROM coldmail_log
               WHERE sent_at >= date('now') AND status='sent'"""
        ).fetchone()["c"]
        return {
            "total_contacts": total,
            "new": new,
            "contacted": contacted,
            "replied": replied,
            "sent_today": today_sent,
        }


def log_coldmail(email: str, subject: str, status: str = "sent"):
    """Log a cold mail send."""
    email = email.strip().lower()
    with _db() as conn:
        conn.execute(
            "INSERT INTO coldmail_log (email, subject, sent_at, status) VALUES (?, ?, ?, ?)",
            (email, subject, datetime.now(timezone.utc).isoformat(), status),
        )


def get_sent_today_count() -> int:
    """Get count of cold mails sent today."""
    with _db() as conn:
        return conn.execute(
            "SELECT COUNT(*) c FROM coldmail_log WHERE sent_at >= date('now') AND status='sent'"
        ).fetchone()["c"]


def log_automation_run(run_type: str, params: dict | None = None) -> int:
    """Log the start of an automation run. Returns run_id."""
    import json
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO automation_runs (run_type, started_at, results) VALUES (?, ?, ?)",
            (run_type, datetime.now(timezone.utc).isoformat(), json.dumps(params or {}))
        )
        return cur.lastrowid


def update_automation_run(run_id: int, status: str, results: dict | None = None):
    """Update an automation run with completion status and results."""
    import json
    with _db() as conn:
        conn.execute(
            """UPDATE automation_runs 
               SET status = ?, completed_at = ?, results = ?
               WHERE id = ?""",
            (status, datetime.now(timezone.utc).isoformat(), 
             json.dumps(results) if results else None, run_id)
        )


# ---------------------------------------------------------------
# Question -> answer learning cache (stuck-question memory)
# ---------------------------------------------------------------

def get_cached_answer(question: str) -> str | None:
    """Best learned answer for a form question: confirmed first, else unconfirmed."""
    norm = " ".join((question or "").lower().split())
    if not norm:
        return None
    with _db() as conn:
        row = conn.execute(
            "SELECT answer, confirmed FROM question_answers WHERE question_norm = ?",
            (norm,),
        ).fetchone()
        return row["answer"] if row else None


def cache_answer(question: str, answer: str) -> None:
    """Learn a question -> answer pair (unconfirmed until a submit succeeds)."""
    norm = " ".join((question or "").lower().split())
    if not norm or not (answer or "").strip():
        return
    now = datetime.now(timezone.utc).isoformat()
    with _db() as conn:
        conn.execute(
            """INSERT INTO question_answers (question_norm, answer, confirmed, times_used, created_at, updated_at)
               VALUES (?, ?, 0, 1, ?, ?)
               ON CONFLICT(question_norm) DO UPDATE SET
                   answer = excluded.answer,
                   times_used = question_answers.times_used + 1,
                   updated_at = excluded.updated_at""",
            (norm, answer, now, now),
        )


def confirm_answer(question: str) -> None:
    """Mark a learned answer as confirmed (used in a successful submission)."""
    norm = " ".join((question or "").lower().split())
    if not norm:
        return
    with _db() as conn:
        conn.execute(
            "UPDATE question_answers SET confirmed = 1, updated_at = ? WHERE question_norm = ?",
            (datetime.now(timezone.utc).isoformat(), norm),
        )


def question_cache_stats() -> dict:
    """Summary of the learned answer cache (confirmed vs learning)."""
    with _db() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM question_answers").fetchone()["c"]
        confirmed = conn.execute(
            "SELECT COUNT(*) c FROM question_answers WHERE confirmed = 1"
        ).fetchone()["c"]
        return {"total": total, "confirmed": confirmed, "learning": total - confirmed}


# ---------------------------------------------------------------
# Application outcomes (feedback loop: replied / interview / offer / rejected)
# ---------------------------------------------------------------

OUTCOMES = ("replied", "interview", "offer", "rejected", "spam")


def set_outcome(job_id: str, outcome: str, notes: str = "") -> bool:
    """Record what happened after an application. Returns False if job unknown."""
    outcome = (outcome or "").lower().strip()
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of {OUTCOMES}, got {outcome!r}")
    now = datetime.now(timezone.utc).isoformat()
    with _db() as conn:
        row = conn.execute("SELECT job_id FROM applications WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return False
        if notes:
            conn.execute(
                """UPDATE applications SET outcome=?, outcome_at=?,
                   notes = CASE WHEN COALESCE(notes,'')='' THEN ? ELSE notes || ' | ' || ? END
                   WHERE job_id=?""",
                (outcome, now, notes, notes, job_id),
            )
        else:
            conn.execute(
                "UPDATE applications SET outcome=?, outcome_at=? WHERE job_id=?",
                (outcome, now, job_id),
            )
        return True


def get_outcome_stats() -> dict:
    """Outcome counts + response rate by source (for digest + scoring feedback)."""
    with _db() as conn:
        rows = conn.execute(
            """SELECT COALESCE(a.outcome, 'none') outcome, COUNT(*) c
               FROM applications a WHERE a.status IN ('applied','submitted_unconfirmed')
               GROUP BY 1"""
        ).fetchall()
        counts = {r["outcome"]: r["c"] for r in rows}
        total_applied = sum(counts.values())
        replied = counts.get("replied", 0) + counts.get("interview", 0) + counts.get("offer", 0)
        by_source = conn.execute(
            """SELECT j.source,
                      COUNT(*) applied,
                      SUM(CASE WHEN a.outcome IN ('replied','interview','offer') THEN 1 ELSE 0 END) responded
               FROM applications a JOIN jobs j ON j.job_id = a.job_id
               WHERE a.status IN ('applied','submitted_unconfirmed')
               GROUP BY j.source"""
        ).fetchall()
        return {
            "counts": counts,
            "applied": total_applied,
            "responded": replied,
            "response_rate": round(100 * replied / total_applied, 1) if total_applied else 0.0,
            "by_source": [dict(r) for r in by_source],
        }


def list_applied_with_outcome(outcome: str | None = None, limit: int = 50) -> list[dict]:
    """Applied jobs with outcome — outcome=None returns those not yet tracked."""
    sql = """SELECT j.job_id, j.title, j.company, j.source, j.score,
                    a.applied_at, a.outcome, a.outcome_at
             FROM applications a JOIN jobs j ON j.job_id = a.job_id
             WHERE a.status IN ('applied','submitted_unconfirmed')"""
    params: list = []
    if outcome is None:
        sql += " AND a.outcome IS NULL"
    else:
        sql += " AND a.outcome = ?"
        params.append(outcome)
    sql += " ORDER BY a.applied_at DESC LIMIT ?"
    params.append(limit)
    with _db() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
