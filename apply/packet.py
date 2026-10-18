"""Application packet: cover letter + resume bullets + resume file, per job.

Priority for the resume attachment:
  1. explicit --resume path
  2. $RESUME_PATH env
  3. data/resume.pdf|docx (drop your real resume there)
  4. generated tailored PDF from config/profile.py (data/resumes/<job_id>.pdf)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ai.assistant import tailor_cover_letter, tailor_resume_bullets  # noqa: E402
from apply.resume_doc import generate_resume_pdf  # noqa: E402
from tracker.db import get_job, save_application  # noqa: E402

log = logging.getLogger("apply")

RESUME_DIR = ROOT / "data" / "resumes"
DEFAULT_RESUMES = [ROOT / "data" / "resume.pdf", ROOT / "data" / "resume.docx", ROOT / "data" / "resume.doc"]


def _safe_name(s: str, max_len: int = 42) -> str:
    import re

    s = re.sub(r"[^A-Za-z0-9]+", "_", s or "").strip("_")
    return s[:max_len].rstrip("_")


def resume_filename(job: dict) -> str:
    """Readable attachment name: Sanjana_Thakur_AI_Engineer_At_Acme.pdf"""
    from config.profile import PROFILE

    parts = [
        _safe_name(PROFILE.get("name", "resume"), 24),
        _safe_name(job.get("title", ""), 40),
        _safe_name(job.get("company", ""), 24),
    ]
    return "_".join(p for p in parts if p) + ".pdf"


def find_resume(job_id: str, resume_path: str | None = None, no_resume: bool = False, generate: bool = True):
    """Returns (path|None, how)."""
    if no_resume:
        return None, "skipped (--no-resume)"
    for cand in ([Path(resume_path).expanduser()] if resume_path else []):
        if cand.exists():
            return cand, f"user file ({cand.name})"
        log.warning("--resume file not found: %s", cand)
    if resume_path:
        return None, f"missing file: {resume_path}"
    import os

    env_path = os.environ.get("RESUME_PATH", "")
    if env_path:
        cand = Path(env_path).expanduser()
        if cand.exists():
            return cand, f"user file ($RESUME_PATH: {cand.name})"
        log.warning("$RESUME_PATH not found: %s", env_path)
    for cand in DEFAULT_RESUMES:
        if cand.exists():
            return cand, f"fallback ({cand.name})"
    if generate:
        return None, "generate"  # caller generates after bullets exist
    return None, "none available"


def generate_for_job(job: dict, bullets: str) -> Path:
    out = RESUME_DIR / resume_filename(job)
    generate_resume_pdf(job, bullets, out)
    log.info("Generated resume PDF: %s", out)
    return out


def build_packet(
    job_id: str,
    force: bool = False,
    resume_path: str | None = None,
    no_resume: bool = False,
) -> dict:
    """Build (or reuse) the full application packet for a job.

    Reuses an existing 'ready' application unless force=True.
    """
    job = get_job(job_id)
    if not job:
        raise LookupError(f"job not found: {job_id}")

    have_letter = (job.get("cover_letter") or "").strip()
    if have_letter and not force and job.get("app_status") in ("ready", "applied"):
        letter = job["cover_letter"]
        bullets = (job.get("resume_bullets") or "").strip()
        reused = True
        if not bullets:  # older prep may have failed mid-way — fill the gap
            import json

            bullets = tailor_resume_bullets(job["title"], job.get("description") or "")
            if bullets:
                save_application(job_id, letter, bullets, status="ready")
                log.info("Regenerated missing resume bullets for %s", job["title"])
    else:
        import json

        try:
            reasons = json.loads(job.get("match_reasons") or "[]")
        except (json.JSONDecodeError, TypeError):
            reasons = []
        desc = job.get("description") or ""
        letter = tailor_cover_letter(job["title"], job["company"], desc, reasons)
        bullets = tailor_resume_bullets(job["title"], desc)
        save_application(job_id, letter, bullets, status="ready")
        reused = False
        log.info("Generated new cover letter + bullets for %s", job["title"])

    resume_file, resume_how = find_resume(job_id, resume_path, no_resume)
    if resume_how == "generate":
        resume_file = generate_for_job(job, bullets)
        resume_how = f"generated ({resume_file.name})"

    from apply.emailer import extract_hr_email  # local import: keeps packet importable without emailer

    return {
        "job": job,
        "cover_letter": letter,
        "resume_bullets": bullets,
        "resume_file": resume_file,
        "resume_how": resume_how,
        "hr_email": extract_hr_email(job.get("description") or ""),
        "reused": reused,
    }
