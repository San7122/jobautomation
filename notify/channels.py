"""Digest delivery channels: Telegram, Email (SMTP), Slack webhook.

Any subset can be configured in .env — see .env.example. Unconfigured channels
are skipped; delivery failures are logged, never fatal to the digest run.
`smtp_send` is the shared SMTP primitive (also used for application emails).
"""
from __future__ import annotations

import logging
import os
import smtplib
from datetime import date
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

import requests

log = logging.getLogger("notify")

CHUNK = 3900  # Telegram message limit is 4096 — stay safely under


def smtp_configured() -> bool:
    return bool(os.environ.get("SMTP_HOST", ""))


def smtp_send(
    subject: str,
    body: str,
    recipients: list[str],
    attachments: list[tuple[str, bytes]] | None = None,
    sender: str | None = None,
    sender_name: str = "Sanjana Thakur",
) -> None:
    """Send a plain-text email with optional attachments over SMTP.

    Raises on failure. Recipients must be non-empty; host must be configured.
    """
    host = os.environ.get("SMTP_HOST", "")
    if not host:
        raise RuntimeError("SMTP not configured — set SMTP_HOST (and SMTP_USER/SMTP_PASSWORD) in .env")
    if not recipients:
        raise ValueError("no recipients")

    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = sender or os.environ.get("EMAIL_FROM", "") or user or "uae-job-copilot@localhost"

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = formataddr((sender_name, sender))
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(body, "plain", "utf-8"))
    for fname, data in attachments or []:
        part = MIMEApplication(data, Name=fname)
        part["Content-Disposition"] = f'attachment; filename="{fname}"'
        msg.attach(part)

    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        try:
            server.starttls()
        except smtplib.SMTPNotSupportedError:
            pass  # local/plain relay
    try:
        if user and password:
            server.login(user, password)
        server.sendmail(sender, recipients, msg.as_string())
    finally:
        server.quit()


def _chunk(text: str, size: int = CHUNK) -> list[str]:
    if len(text) <= size:
        return [text]
    parts, current = [], ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > size:
            parts.append(current)
            current = ""
        current += line
    if current:
        parts.append(current)
    return parts


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    for part in _chunk(text):
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": part, "disable_web_page_preview": True},
            timeout=30,
        )
        resp.raise_for_status()
    log.info("Digest sent to Telegram (chat %s)", chat_id)
    return True


def send_email(text: str) -> bool:
    if not smtp_configured():
        return False
    to_raw = os.environ.get("EMAIL_TO", "") or os.environ.get("SMTP_USER", "")
    recipients = [a.strip() for a in to_raw.split(",") if a.strip()]
    if not recipients:
        raise ValueError("EMAIL_TO (or SMTP_USER) is not set")
    smtp_send(f"UAE Job Digest — {date.today().isoformat()}", text, recipients)
    log.info("Digest emailed to %s", ", ".join(recipients))
    return True


def send_slack(text: str) -> bool:
    url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not url:
        return False
    resp = requests.post(url, json={"text": text}, timeout=30)
    resp.raise_for_status()
    log.info("Digest sent to Slack")
    return True


CHANNELS = [
    ("telegram", send_telegram),
    ("email", send_email),
    ("slack", send_slack),
]


def send_digest(content: str) -> list[str]:
    """Send the digest to every configured channel. Returns names delivered to."""
    sent: list[str] = []
    for name, fn in CHANNELS:
        try:
            if fn(content):
                sent.append(name)
        except Exception as e:
            log.error("%s delivery failed: %s", name, e)
    if sent:
        log.info("Digest delivered via: %s", ", ".join(sent))
    else:
        log.info("No notification channel delivered (configure TELEGRAM_*/SMTP_*/SLACK_WEBHOOK_URL in .env)")
    return sent
