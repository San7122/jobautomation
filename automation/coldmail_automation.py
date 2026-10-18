"""Cold Mail Automation - One-click daily cold mail system."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from apply.coldmail import (
    collect_emails_from_jobs,
    send_cold_emails,
)
from config.profile import PROFILE
from notify.channels import smtp_send
from tracker.db import (
    get_contact_stats,
    get_sent_today_count,
    init_db,
    log_automation_run,
    update_automation_run,
)

log = logging.getLogger("coldmail_automation")

# Default LinkedIn queries for AI/Tech email collection
DEFAULT_LINKEDIN_QUERIES = [
    "urgent hiring AI engineer UAE",
    "hiring machine learning engineer Dubai",
    "urgent hiring data scientist UAE",
    "hiring AI ML engineer Dubai",
    "urgent requirement data engineer UAE",
    "hiring software engineer AI Dubai",
]


def run_coldmail_automation(
    daily_target: int = 20,
    linkedin_queries: Optional[list[str]] = None,
    send_notification: bool = True,
    dry_run: bool = False,
) -> dict:
    """One-click cold mail automation.
    
    1. Collects fresh emails from LinkedIn posts + job descriptions
    2. Sends cold mails up to daily_target
    3. Sends email notification with summary
    
    Returns dict with results.
    """
    init_db()
    
    # Start automation run tracking
    run_id = log_automation_run("coldmail", {"daily_target": daily_target})
    
    results = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "emails_collected": 0,
        "emails_sent": 0,
        "emails_failed": 0,
        "daily_target": daily_target,
        "sent_today_before": get_sent_today_count(),
    }
    
    try:
        # Step 1: Collect fresh emails from LinkedIn posts
        log.info("📧 Step 1: Collecting emails from LinkedIn posts...")
        from scraper.linkedin_posts import scrape_linkedin_posts
        
        queries = linkedin_queries or DEFAULT_LINKEDIN_QUERIES
        linkedin_results = scrape_linkedin_posts(
            queries=queries,
            limit_per_query=15,
        )
        
        # Save LinkedIn contacts to DB
        from tracker.db import upsert_contact
        for result in linkedin_results:
            if upsert_contact(
                email=result["email"],
                name=result.get("name", ""),
                company=result.get("company", ""),
                role="Recruiter (from LinkedIn post)",
                source="linkedin_post",
            ):
                results["emails_collected"] += 1
        
        log.info("✅ Collected %d new emails from LinkedIn", results["emails_collected"])
        
        # Step 2: Collect from job descriptions
        log.info("📧 Step 2: Collecting emails from job descriptions...")
        job_emails = collect_emails_from_jobs()
        results["emails_collected"] += job_emails
        log.info("✅ Collected %d new emails from job descriptions", job_emails)
        
        # Step 3: Calculate remaining quota
        sent_today = get_sent_today_count()
        remaining = max(0, daily_target - sent_today)
        results["sent_today_before"] = sent_today
        
        if remaining == 0:
            log.warning("⚠️ Daily limit already reached (%d sent today)", sent_today)
            results["message"] = f"Daily limit reached: {sent_today}/{daily_target}"
        else:
            # Step 4: Send cold emails
            log.info("📧 Step 3: Sending cold emails (target: %d)...", remaining)
            send_results = send_cold_emails(
                limit=remaining,
                daily_limit=daily_target,
                status="new",
                dry_run=dry_run,
            )
            results["emails_sent"] = send_results.get("sent", 0)
            results["emails_failed"] = send_results.get("failed", 0)
            
            if send_results.get("reason"):
                results["message"] = send_results["reason"]
            
            log.info("✅ Sent %d emails (%d failed)", 
                    results["emails_sent"], results["emails_failed"])
        
        # Get final stats
        results["stats"] = get_contact_stats()
        results["completed_at"] = datetime.now(timezone.utc).isoformat()
        results["status"] = "completed"
        
        # Update automation run
        update_automation_run(run_id, status="completed", results=results)
        
        # Step 5: Send notification email
        if send_notification and not dry_run:
            _send_notification_email(results)
            results["notification_sent"] = True
        
        log.info("✅ Cold mail automation completed!")
        
    except Exception as e:
        results["status"] = "failed"
        results["error"] = str(e)
        results["completed_at"] = datetime.now(timezone.utc).isoformat()
        update_automation_run(run_id, status="failed", results=results)
        log.error("❌ Automation failed: %s", e)
        
        # Send failure notification
        if send_notification and not dry_run:
            _send_notification_email(results)
        
        raise
    
    return results


def _send_notification_email(results: dict):
    """Send email notification with automation summary."""
    try:
        name = PROFILE.get("name", "User")
        stats = results.get("stats", {})
        
        subject = f"📧 Cold Mail Automation Complete - {results.get('emails_sent', 0)} emails sent"
        
        # Build email body
        body = f"""Hi {name},

Your cold mail automation has completed!

📊 Summary:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📧 Emails Collected Today: {results.get('emails_collected', 0)}
📤 Emails Sent: {results.get('emails_sent', 0)}
❌ Failed: {results.get('emails_failed', 0)}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📈 Contact Statistics:
• Total Contacts: {stats.get('total_contacts', 0)}
• New (pending): {stats.get('new', 0)}
• Contacted: {stats.get('contacted', 0)}
• Replied: {stats.get('replied', 0)}

📅 Daily Progress: {stats.get('sent_today', 0)}/{results.get('daily_target', 20)} emails

"""
        
        if results.get("error"):
            body += f"""⚠️ Error: {results['error']}

"""
        
        body += f"""View full history: python3 main.py contacts
View automation logs: python3 main.py coldmail-history

Best regards,
UAE Job Copilot
"""

        smtp_send(
            subject=subject,
            body=body,
            recipients=[PROFILE.get("email", "")],
        )
        
        log.info("✅ Notification email sent to %s", PROFILE.get("email", ""))
        
    except Exception as e:
        log.warning("⚠️ Failed to send notification: %s", e)


def get_coldmail_history(limit: int = 30) -> dict:
    """Get cold mail automation history."""
    init_db()
    from tracker.db import _db
    
    with _db() as conn:
        # Get automation runs
        runs = conn.execute(
            """SELECT * FROM automation_runs 
               WHERE run_type = 'coldmail' 
               ORDER BY started_at DESC LIMIT ?""",
            (limit,)
        ).fetchall()
        
        # Get email log
        emails = conn.execute(
            """SELECT cl.*, c.name, c.company 
               FROM coldmail_log cl
               LEFT JOIN contacts c ON cl.email = c.email
               ORDER BY cl.sent_at DESC LIMIT ?""",
            (limit * 2,)
        ).fetchall()
        
        return {
            "runs": [dict(r) for r in runs],
            "emails": [dict(e) for e in emails],
            "stats": get_contact_stats(),
        }
