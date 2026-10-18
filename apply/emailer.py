"""Apply to jobs by email: extract HR address, compose tailored mail, send.

Legitimate full-auto path: many UAE listings give an HR/careers email directly
in the description — this sends the cover letter + resume there via SMTP.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.profile import PROFILE  # noqa: E402
from notify.channels import smtp_send  # noqa: E402
from tracker.db import mark_applied  # noqa: E402

log = logging.getLogger("apply")

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
BAD_LOCAL = re.compile(r"^(no-?reply|do-?not-?reply|bounce|postmaster|abuse|support|sales|marketing|billing)", re.I)
BAD_DOMAINS = {"example.com", "example.org", "sentry.io", "wixpress.com", "glassdoor.com"}
PRIORITY_LOCAL = re.compile(r"(hr|career|talent|recruit|job|apply|hiring|people)", re.I)
# guard against truncated text like "resumes@wafa.abdul Ahmed" — a real TLD is short
COMMON_TLDS = {
    "com", "net", "org", "io", "ai", "co", "me", "info", "jobs", "careers",
    "in", "ae", "sa", "qa", "kw", "om", "bh", "eg", "uk", "us", "de", "fr",
    "ca", "au", "ch", "nl", "se", "no", "es", "it", "pk", "ng", "ke", "za",
}


def _plausible_email(email: str) -> bool:
    domain = email.rpartition("@")[2]
    tld = domain.rpartition(".")[2].lower()
    return tld in COMMON_TLDS or len(tld) <= 3


def extract_hr_email(description: str, candidate_email: str = "") -> str | None:
    """Best-effort HR/careers email from the job description."""
    found = EMAIL_RE.findall(description or "")
    seen, candidates = set(), []
    for e in found:
        e = e.lower().rstrip(".")
        if e in seen:
            continue
        seen.add(e)
        local, _, domain = e.partition("@")
        if candidate_email and e == candidate_email.lower():
            continue
        if BAD_LOCAL.match(local) or domain in BAD_DOMAINS or not _plausible_email(e):
            continue
        candidates.append(e)
    if not candidates:
        return None
    candidates.sort(key=lambda e: (0 if PRIORITY_LOCAL.search(e) else 1, e))
    return candidates[0]


def build_application_email(packet: dict) -> dict:
    """Compose subject/body/to for an application packet (no sending)."""
    job = packet["job"]
    to = packet.get("hr_email")
    if not to:
        raise ValueError(
            "no HR email found in the job description — use `main.py apply <id> --mode fill` instead"
        )
    name = PROFILE["name"]
    subject = f"Application for {job['title']} — {name}"
    body = "\n\n".join(
        [
            "Dear Hiring Team,",
            "",
            packet["cover_letter"].strip(),
            "",
            "—",
            f"{name} | {PROFILE['email']} | {PROFILE['phone']} | {PROFILE['location']}",
            f"Notice period: {PROFILE['notice_period']} | Visa: {PROFILE['visa_status']}",
            f"Job listing: {job['url']}",
        ]
    )
    attachments: list[tuple[str, bytes]] = []
    if packet.get("resume_file"):
        path = Path(packet["resume_file"])
        attachments.append((path.name, path.read_bytes()))
    return {"to": [to], "subject": subject, "body": body, "attachments": attachments}


def send_application(packet: dict, dry_run: bool = False) -> str:
    """Send the application email. Marks the job applied on success.

    Returns a human-readable status message; raises on send failure.
    """
    mail = build_application_email(packet)
    att_names = [n for n, _ in mail["attachments"]]
    summary = (
        f"to={mail['to'][0]} | subject={mail['subject']!r} | "
        f"attachments={att_names or 'none'} | body={len(mail['body'])} chars"
    )
    if dry_run:
        return f"DRY RUN — would send: {summary}"
    smtp_send(mail["subject"], mail["body"], mail["to"], mail["attachments"])
    job = packet["job"]
    mark_applied(job["job_id"], notes=f"Applied by email to {mail['to'][0]}")
    log.info("Application emailed for %s (%s)", job["title"], summary)
    return f"Sent application to {mail['to'][0]} — marked applied. {summary}"
