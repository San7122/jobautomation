"""Inbox reply scanner — IMAP-based feedback loop.

Detects two kinds of replies in the mailbox:
1. Cold mail replies   → sender is a known contact → contacts.status = 'replied'
2. Application replies → sender domain matches an applied company → outcome = 'replied'

Config reuses SMTP credentials (Gmail: same app-password works for IMAP):
    IMAP_HOST (default imap.gmail.com), IMAP_PORT (default 993)
    SMTP_USER / SMTP_PASSWORD
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parseaddr

log = logging.getLogger("inbox")

# domains that never mean a human reply (job boards, free mail, our own sender)
_GENERIC_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "live.com", "icloud.com", "proton.me", "protonmail.com", "aol.com",
    "linkedin.com", "mail.linkedin.com", "indeed.com", "naukri.com",
    "bayt.com", "glassdoor.com", "monster.com", "ziprecruiter.com",
    "no-reply", "donotreply", "github.com", "apple.com", "microsoft.com",
    "facebook.com", "instagram.com", "twitter.com", "x.com",
}

# company-name words that are never a domain signal
_COMPANY_STOPWORDS = {
    "llc", "ltd", "limited", "inc", "fze", "dmcc", "fz", "co", "group",
    "trading", "general", "services", "service", "solutions", "solution",
    "middle", "east", "est", "contracting", "consulting", "international",
    "careers", "hiring", "recruitment", "staffing", "hr", "human",
    "resources", "technology", "technologies", "tech", "systems",
    "digital", "global", "holdings", "holding", "industries", "enterprise",
}


def _decode_str(raw: str) -> str:
    parts = decode_header(raw or "")
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return " ".join(out).strip()


def fetch_messages(days: int = 10, limit: int = 100) -> list[dict]:
    """Fetch recent INBOX messages (headers + short body preview) over IMAP."""
    host = _env("IMAP_HOST", "imap.gmail.com")
    port = int(_env("IMAP_PORT", "993"))
    user = _env("SMTP_USER", "")
    password = _env("SMTP_PASSWORD", "")
    if not user or not password:
        raise RuntimeError("IMAP not configured — SMTP_USER/SMTP_PASSWORD missing in .env")

    since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")  # IMAP date format
    msgs: list[dict] = []

    conn = imaplib.IMAP4_SSL(host, port)
    try:
        conn.login(user, password)
        conn.select("INBOX")
        typ, data = conn.search(None, f'(SINCE "{since}")')
        if typ != "OK":
            return msgs
        ids = data[0].split()
        ids = ids[-limit:]  # newest last
        for msg_id in reversed(ids):  # newest first
            typ, chunk = conn.fetch(msg_id, "(BODY.PEEK[])")
            if typ != "OK":
                continue
            full = b""
            for part in chunk or []:
                if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], bytes):
                    full = part[1]
            if not full:
                continue
            try:
                head = email.message_from_bytes(full)
                body_text = ""
                if head.is_multipart():
                    for p in head.walk():
                        if p.get_content_type() == "text/plain":
                            payload = p.get_payload(decode=True) or b""
                            body_text = payload.decode("utf-8", errors="replace")
                            break
                else:
                    payload = head.get_payload(decode=True) or b""
                    body_text = payload.decode("utf-8", errors="replace")
            except Exception:
                continue
            from_name, from_addr = parseaddr(head.get("From", ""))
            in_reply_to = (head.get("In-Reply-To") or "").strip()
            references = (head.get("References") or "").strip()
            date_hdr = head.get("Date", "")
            msgs.append({
                "from_email": (from_addr or "").strip().lower(),
                "from_name": _decode_str(from_name),
                "subject": _decode_str(head.get("Subject", "")),
                "date": date_hdr,
                "message_id": (head.get("Message-ID") or "").strip(),
                "in_reply_to": in_reply_to,
                "references": references,
                "body_preview": re.sub(r"\s+", " ", body_text)[:400],
            })
            if len(msgs) >= limit:
                break
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    log.info("Inbox: fetched %d messages (last %d days)", len(msgs), days)
    return msgs


def _env(key: str, default: str = "") -> str:
    import os

    return os.environ.get(key, default) or default


def _company_tokens(company: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (company or "").lower())
    return [w for w in words if len(w) >= 4 and w not in _COMPANY_STOPWORDS]


def _domain_matches(from_email: str, company: str) -> bool:
    """True when the sender's domain plausibly belongs to the company."""
    domain = from_email.split("@")[-1].lower()
    if not domain or domain in _GENERIC_DOMAINS:
        return False
    parts = [p for p in domain.split(".") if p and p not in ("com", "net", "org", "ae", "in", "co", "uk", "www")]
    if not parts:
        return False
    for token in _company_tokens(company):
        for p in parts:
            if p == token or (p.startswith(token) and len(token) >= 4) or token.startswith(p) and len(p) >= 5:
                return True
    return False


def _is_reply_like(msg: dict) -> bool:
    subj = (msg.get("subject") or "").lower().strip()
    return (
        subj.startswith(("re:", "aw:", "sv:", "reply"))
        or bool(msg.get("in_reply_to"))
        or bool(msg.get("references"))
    )


def scan_replies(days: int = 10, limit: int = 100) -> dict:
    """Scan inbox and update outcomes. Never raises — returns {'error': ...} on failure."""
    from tracker.db import (
        init_db,
        list_applied_with_outcome,
        set_outcome,
        update_contact_status,
        _db,
    )

    init_db()
    try:
        messages = fetch_messages(days=days, limit=limit)
    except Exception as e:
        log.error("Reply scan failed: %s", e)
        return {"error": str(e), "fetched": 0}

    with _db() as conn:
        contacts = {
            r["email"].lower(): dict(r)
            for r in conn.execute("SELECT * FROM contacts").fetchall()
        }
        sent_subjects = {
            (r["email"].lower(), (r["subject"] or "").lower().strip()): r
            for r in conn.execute("SELECT email, subject FROM coldmail_log").fetchall()
        }

    self_addr = _env("SMTP_USER", "").lower()
    applied = list_applied_with_outcome(outcome=None, limit=500)

    contact_replies: list[dict] = []
    application_replies: list[dict] = []
    no_match: list[dict] = []
    seen_contacts: set[str] = set()
    seen_jobs: set[str] = set()

    for msg in messages:
        frm = msg.get("from_email") or ""
        if not frm or frm == self_addr:
            continue
        reply_like = _is_reply_like(msg)
        if not reply_like:
            continue

        # 1) cold mail reply: known contact
        contact = contacts.get(frm)
        subj_key = (msg.get("subject") or "").lower().strip()
        is_cold_reply = bool(contact) or any(
            e == frm and s.replace("re: ", "") == subj_key.replace("re: ", "")
            for (e, s) in sent_subjects
        )
        if is_cold_reply and frm not in seen_contacts:
            seen_contacts.add(frm)
            try:
                update_contact_status(frm, "replied", notes=f"reply: {msg.get('subject','')[:80]}")
            except Exception as e:
                log.debug("contact status update failed for %s: %s", frm, e)
            contact_replies.append({
                "email": frm,
                "company": (contact or {}).get("company", ""),
                "subject": msg.get("subject", ""),
                "date": msg.get("date", ""),
            })
            continue

        # 2) application reply: sender domain matches an applied company
        matched_jobs = [j for j in applied if j["job_id"] not in seen_jobs and _domain_matches(frm, j["company"])]
        if matched_jobs:
            for j in matched_jobs:
                seen_jobs.add(j["job_id"])
                note = f"auto-detected reply from {frm} — {msg.get('subject','')[:70]}"
                try:
                    set_outcome(j["job_id"], "replied", notes=note)
                except Exception as e:
                    log.debug("outcome set failed for %s: %s", j["job_id"], e)
            application_replies.append({
                "from_email": frm,
                "subject": msg.get("subject", ""),
                "title": matched_jobs[0]["title"],
                "company": matched_jobs[0]["company"],
                "outcome": "replied",
                "jobs_matched": [j["job_id"] for j in matched_jobs],
            })
            continue

        no_match_domain = frm.split("@")[-1]
        if no_match_domain not in _GENERIC_DOMAINS:
            no_match.append({
                "from_email": frm,
                "subject": msg.get("subject", ""),
                "date": msg.get("date", ""),
            })

    if contact_replies or application_replies:
        log.info(
            "Reply scan: %d contact replies, %d application replies",
            len(contact_replies), len(application_replies),
        )

    return {
        "fetched": len(messages),
        "contact_replies": contact_replies,
        "application_replies": application_replies,
        "no_match": no_match,
        "scanned_at": datetime.now(timezone.utc).isoformat(),
    }
