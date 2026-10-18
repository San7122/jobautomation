"""Cold mail module — collect recruiter emails and send outreach campaigns."""
from __future__ import annotations

import csv
import logging
import os
import re
from pathlib import Path

from config.profile import PROFILE
from tracker.db import (
    get_contacts,
    get_sent_today_count,
    init_db,
    log_coldmail,
    mark_contact_contacted,
    upsert_contact,
)

log = logging.getLogger("coldmail")

# Email regex (same as emailer.py)
EMAIL_RE = r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"

# Bad email patterns to skip
BAD_LOCAL = {"no-reply", "noreply", "bounce", "postmaster", "abuse", "support", "sales", "marketing", "billing"}
BAD_DOMAINS = {"example.com", "example.org", "sentry.io", "wixpress.com", "glassdoor.com"}

# HR/Recruiter keywords for prioritization
HR_KEYWORDS = {"hr", "career", "talent", "recruit", "job", "apply", "hiring", "people", "ta"}

# Tech/AI specific keywords
TECH_KEYWORDS = {
    "ai", "ml", "machine learning", "artificial intelligence", "data science",
    "data scientist", "data engineer", "software engineer", "python",
    "tensorflow", "pytorch", "nlp", "computer vision", "deep learning",
    "mlops", "llm", "genai", "generative ai", "rag", "agent",
    "cloud", "aws", "azure", "gcp", "devops", "kubernetes", "docker",
    "backend", "frontend", "full stack", "api", "microservices",
}

# Fake/non-tech patterns to skip
FAKE_PATTERNS = {
    "sushi", "chef", "security guard", "hse officer", "construction",
    "hospitality", "hotel", "restaurant", "driver", "cleaner", "waiter",
    "electrician", "plumber", "welder", "mechanic", "laborer",
    "housekeeping", "receptionist", "cashier", "sales executive",
}

# Tech company domains (real companies)
TECH_DOMAINS = {
    "google.com", "microsoft.com", "amazon.com", "meta.com", "apple.com",
    "netflix.com", "uber.com", "airbnb.com", "stripe.com", "paypal.com",
    "salesforce.com", "oracle.com", "ibm.com", "intel.com", "nvidia.com",
    "adobe.com", "vmware.com", "cisco.com", "dell.com",
    "openai.com", "anthropic.com", "huggingface.co", "databricks.com",
    "snowflake.com", "palantir.com",
    # UAE tech
    "careem.com", "emiratesdigital.ae", "dtec.ae",
    # Indian tech
    "tcs.com", "infosys.com", "wipro.com", "hcltech.com", "techmahindra.com",
    "cognizant.com", "accenture.com", "capgemini.com", "ltimindtree.com",
}

# Indian domains/companies (for detecting Indian recruiters)
INDIAN_DOMAINS = {
    ".in", ".co.in", ".org.in", ".net.in", ".gov.in", ".ac.in",
    # Indian company domains
    "tcs.com", "infosys.com", "wipro.com", "hcltech.com", "techmahindra.com",
    "cognizant.com", "accenture.com", "capgemini.com", "ltimindtree.com",
    "mindtree.com", "mphasis.com", "hexaware.com", "niit.com",
    "careernet.in", "teamlease.com", "quesscorp.com", "quadrantresource.com",
}

# UAE/Gulf domains (for detecting Gulf recruiters)
GULF_DOMAINS = {
    ".ae", ".com.ae", ".net.ae", ".org.ae", ".gov.ae", ".ac.ae",
    ".sa", ".com.sa", ".qa", ".com.qa", ".kw", ".com.kw", ".bh", ".com.bh",
    ".om", ".com.om", ".eg", ".com.eg",
    # UAE company domains
    "emirates.com", "etihad.ae", "adnoc.ae", "dmcc.ae", "jafza.ae",
    "careem.com", "emiratesnbd.com", "mashreqbank.com", "dib.ae",
}


def is_indian_recruiter(email: str, company: str = "") -> bool:
    """Detect if recruiter is from India."""
    email_lower = email.lower()
    company_lower = company.lower()
    
    # Check email domain
    domain = email_lower.split("@")[1] if "@" in email_lower else ""
    for indian_domain in INDIAN_DOMAINS:
        if domain.endswith(indian_domain) or indian_domain in domain:
            return True
    # Domain contains india/indian markers
    if "india" in domain or "hind" in domain:
        return True
    
    # Check company name for Indian indicators
    indian_indicators = ["india", "pvt ltd", "private limited", "bangalore", 
                        "mumbai", "delhi", "gurgaon", "noida", "hyderabad",
                        "chennai", "pune", "ahmedabad", "jaipur", "kolkata"]
    for indicator in indian_indicators:
        if indicator in company_lower:
            return True
    
    return False


def is_gulf_recruiter(email: str, company: str = "") -> bool:
    """Detect if recruiter is from UAE/Gulf region."""
    email_lower = email.lower()
    company_lower = company.lower()
    
    # Check email domain
    domain = email_lower.split("@")[1] if "@" in email_lower else ""
    for gulf_domain in GULF_DOMAINS:
        if domain.endswith(gulf_domain) or gulf_domain in domain:
            return True
    
    # Check company name for Gulf indicators
    gulf_indicators = ["dubai", "uae", "abu dhabi", "sharjah", "gulf", "middle east",
                      "emirates", "qatar", "saudi", "kuwait", "bahrain", "oman"]
    for indicator in gulf_indicators:
        if indicator in company_lower:
            return True
    
    return False


def is_valid_email(email: str, candidate_email: str = "") -> bool:
    """Validate an email address."""
    email = email.strip().lower()
    if not email or email == candidate_email:
        return False
    local = email.split("@")[0]
    domain = email.split("@")[1] if "@" in email else ""
    if local in BAD_LOCAL:
        return False
    if domain in BAD_DOMAINS:
        return False
    # Basic TLD check
    tld = domain.split(".")[-1] if "." in domain else ""
    if len(tld) < 2:
        return False
    return True


def extract_emails_from_text(text: str, candidate_email: str = "") -> list[str]:
    """Extract valid emails from text, prioritizing HR/Recruiter addresses."""
    if not text:
        return []
    emails = re.findall(EMAIL_RE, text)
    valid = [e.strip().lower() for e in emails if is_valid_email(e.strip(), candidate_email)]
    # Dedupe
    seen = set()
    unique = []
    for e in valid:
        if e not in seen:
            seen.add(e)
            unique.append(e)
    # Prioritize HR emails
    hr_emails = [e for e in unique if any(kw in e for kw in HR_KEYWORDS)]
    other_emails = [e for e in unique if e not in hr_emails]
    return hr_emails + other_emails


def is_tech_relevant(text: str, company: str = "") -> bool:
    """Check if text is relevant to AI/Tech roles."""
    text_lower = text.lower()
    company_lower = company.lower()
    
    # Check for fake patterns (non-tech roles)
    for pattern in FAKE_PATTERNS:
        if pattern in text_lower:
            return False
    
    # Check for tech keywords
    tech_score = 0
    for keyword in TECH_KEYWORDS:
        if keyword in text_lower:
            tech_score += 1
    
    # Check for tech company
    for domain in TECH_DOMAINS:
        if domain in company_lower:
            tech_score += 3
    
    # Require minimum tech relevance
    return tech_score >= 1


def collect_emails_from_jobs(source: str | None = None) -> int:
    """Collect recruiter emails from AI/Tech job descriptions in DB."""
    init_db()
    from tracker.db import _db

    candidate_email = PROFILE.get("email", "")
    collected = 0

    with _db() as conn:
        sql = """SELECT job_id, company, title, description FROM jobs 
                 WHERE description IS NOT NULL AND description != ''"""
        params: list = []
        if source:
            sql += " AND source = ?"
            params.append(source)
        jobs = conn.execute(sql, params).fetchall()

    for job in jobs:
        # Filter: Only AI/Tech relevant jobs
        description = job["description"] or ""
        title = job["title"] or ""
        company = job["company"] or ""
        
        # Check if job is tech-relevant
        if not is_tech_relevant(f"{title} {description}", company):
            continue
        
        emails = extract_emails_from_text(description, candidate_email)
        for email in emails[:3]:  # Max 3 per job
            if upsert_contact(
                email=email,
                company=company,
                role="Tech Recruiter (from job posting)",
                source="job_description",
            ):
                collected += 1

    log.info("Collected %d new AI/Tech emails from %d relevant jobs", collected, len(jobs))
    return collected


def import_contacts_from_csv(csv_path: str) -> int:
    """Import contacts from a CSV file.

    Expected columns (flexible): email, name, company, role
    Or single column with emails.
    """
    init_db()
    path = Path(csv_path)
    if not path.exists():
        log.error("CSV file not found: %s", csv_path)
        return 0

    imported = 0
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        # Check if we have email column
        fields = reader.fieldnames or []
        email_col = None
        for col in fields:
            if "email" in col.lower() or "mail" in col.lower():
                email_col = col
                break

        if email_col:
            for row in reader:
                email = (row.get(email_col) or "").strip()
                if not email:
                    continue
                name = ""
                company = ""
                role = ""
                for col in fields:
                    lower = col.lower()
                    if "name" in lower:
                        name = row.get(col, "")
                    elif "company" in lower or "org" in lower:
                        company = row.get(col, "")
                    elif "role" in lower or "title" in lower or "designation" in lower:
                        role = row.get(col, "")
                if upsert_contact(email=email, name=name, company=company, role=role, source="csv"):
                    imported += 1
        else:
            # Single column - just emails
            f.seek(0)
            reader2 = csv.reader(f)
            for row in reader2:
                if row and is_valid_email(row[0]):
                    if upsert_contact(email=row[0].strip(), source="csv"):
                        imported += 1

    log.info("Imported %d new contacts from %s", imported, csv_path)
    return imported


def build_cold_email(
    recruiter_name: str = "",
    company: str = "",
    role: str = "",
    custom_message: str = "",
    email: str = "",
) -> tuple[str, str]:
    """Build a cold email. Returns (subject, body).
    
    Automatically detects if recruiter is Indian/Gulf and customizes template.
    """
    name = PROFILE.get("name", "")
    headline = PROFILE.get("headline", "")
    skills = ", ".join(list(PROFILE.get("skills", {}).keys())[:5])
    location = PROFILE.get("location", "")
    
    # Detect recruiter region
    is_indian = is_indian_recruiter(email, company)
    is_gulf = is_gulf_recruiter(email, company)
    
    # Region-specific settings
    if is_indian:
        # For Indian recruiters - no visa needed, India-based role
        target_location = "India (Bangalore, Hyderabad, Pune, Delhi-NCR, Mumbai)"
        visa_line = "• Visa status: Indian Citizen (no sponsorship needed for India roles)"
        relocation_line = "• Open to: Remote / Hybrid / On-site in India"
        subject_prefix = "AI/ML Engineer"
    elif is_gulf:
        # For Gulf recruiters - visa sponsorship needed
        target_location = "UAE (Dubai, Abu Dhabi, Sharjah)"
        visa_line = "• Visa status: Open to relocate, need sponsorship"
        relocation_line = "• Open to relocation: UAE"
        subject_prefix = "Experienced AI/ML Engineer"
    else:
        # Default - flexible
        target_location = "India / UAE / Remote"
        visa_line = "• Visa status: Open to India roles (no sponsorship) or UAE roles (with sponsorship)"
        relocation_line = "• Open to: India, UAE, or Remote"
        subject_prefix = "AI/ML Engineer"

    # Subject line
    if company:
        subject = f"{subject_prefix} — {name} interested in opportunities at {company}"
    else:
        subject = f"{subject_prefix} — {name} (Available Immediately)"

    # Body
    greeting = f"Dear {recruiter_name}," if recruiter_name else "Dear Hiring Manager,"

    if custom_message:
        body = f"""{greeting}

{custom_message}

Best regards,
{name}
{headline}
Email: {PROFILE.get('email', '')}
Phone: {PROFILE.get('phone', '')}
LinkedIn: {PROFILE.get('linkedin', '')}
"""
    else:
        # Region-specific body
        if is_indian:
            body = f"""{greeting},

I hope this email finds you well. My name is {name}, and I'm an experienced AI/ML engineer with 3+ years of experience building production-grade AI systems.

With hands-on experience in {skills}, I've built LLM applications, RAG pipelines, and agentic AI solutions that are deployed in production.

I noticed {company if company else "your company"} is doing innovative work, and I'd love to explore how my skills could contribute to your team.

Key highlights:
• {PROFILE.get('summary', '')[:200]}
• {relocation_line}
• Available: Immediate to 30 days
• {visa_line}

I've attached my resume for your reference. Would you be open to a brief call to discuss potential opportunities?

Best regards,
{name}
{headline}

Email: {PROFILE.get('email', '')}
Phone: {PROFILE.get('phone', '')}
LinkedIn: {PROFILE.get('linkedin', '')}
GitHub: {PROFILE.get('github', '')}
"""
        else:
            # Gulf/UAE template
            body = f"""{greeting},

I hope this email finds you well. My name is {name}, and I'm an experienced AI/ML engineer currently exploring opportunities in {target_location}.

With hands-on experience in {skills}, I've built production-grade AI systems including LLM applications, RAG pipelines, and agentic AI solutions.

I noticed {company if company else "your company"} is doing innovative work, and I'd love to explore how my skills could contribute to your team.

Key highlights:
• {PROFILE.get('summary', '')[:200]}
• {relocation_line}
• Available: Immediate to 30 days
• {visa_line}

I've attached my resume for your reference. Would you be open to a brief call to discuss potential opportunities?

Best regards,
{name}
{headline}

Email: {PROFILE.get('email', '')}
Phone: {PROFILE.get('phone', '')}
LinkedIn: {PROFILE.get('linkedin', '')}
GitHub: {PROFILE.get('github', '')}
"""
    return subject, body


def send_cold_emails(
    limit: int = 10,
    daily_limit: int = 10,
    status: str = "new",
    custom_message: str = "",
    dry_run: bool = False,
) -> dict:
    """Send cold emails to contacts.

    Args:
        limit: max emails to send this run
        daily_limit: max emails per day (safety)
        status: contact status to filter (default: 'new')
        custom_message: optional custom message to override default
        dry_run: if True, don't actually send

    Returns dict with sent/failed/skipped counts.
    """
    init_db()
    from apply.emailer import smtp_send

    # Check daily limit
    sent_today = get_sent_today_count()
    remaining_today = max(0, daily_limit - sent_today)
    if remaining_today == 0:
        log.warning("Daily limit reached (%d sent today). Try again tomorrow.", sent_today)
        return {"sent": 0, "failed": 0, "skipped": 0, "reason": "daily_limit"}

    # Get contacts to email
    contacts = get_contacts(status=status, limit=min(limit, remaining_today))
    if not contacts:
        log.info("No contacts with status='%s'", status)
        return {"sent": 0, "failed": 0, "skipped": 0, "reason": "no_contacts"}

    results = {"sent": 0, "failed": 0, "skipped": 0}

    # Generate region-specific resumes once
    resumes: dict[str, tuple[str, bytes] | None] = {}
    for region in ("india", "gulf"):
        resumes[region] = _get_region_resume(region)

    for contact in contacts:
        email = contact["email"]
        company = contact.get("company", "") or ""

        # Detect region for this contact
        if is_indian_recruiter(email, company):
            region = "india"
        elif is_gulf_recruiter(email, company):
            region = "gulf"
        else:
            region = "gulf"  # default (UAE-focused tool)

        subject, body = build_cold_email(
            recruiter_name=contact.get("name", ""),
            company=company,
            role=contact.get("role", ""),
            custom_message=custom_message,
            email=email,
        )

        if dry_run:
            log.info("[DRY RUN] Would send to %s (%s template): %s", email, region, subject)
            results["sent"] += 1
            continue

        try:
            resume_att = resumes.get(region)
            attachments = [resume_att] if resume_att else []
            smtp_send(
                subject=subject,
                body=body,
                recipients=[email],
                attachments=attachments,
            )
            mark_contact_contacted(email, notes=f"[{region}] Subject: {subject[:70]}")
            log_coldmail(email, subject, status="sent")
            results["sent"] += 1
            log.info("✅ Cold mail sent to %s (%s resume)", email, region)
        except Exception as e:
            results["failed"] += 1
            log.error("❌ Failed to send to %s: %s", email, e)

    return results


def _get_region_resume(region: str) -> tuple[str, bytes] | None:
    """Generate (or reuse) a region-specific resume PDF. Returns (filename, bytes)."""
    import time as _time

    try:
        from apply.resume_doc import generate_resume_pdf

        out_path = Path(__file__).parent.parent / "data" / "resumes" / f"coldmail_{region}.pdf"
        # Regenerate if missing or older than 1 day
        if not out_path.exists() or (_time.time() - out_path.stat().st_mtime) > 86400:
            dummy_job = {"title": "", "description": ""}
            generate_resume_pdf(dummy_job, "", out_path, region=region)
        return (out_path.name, out_path.read_bytes())
    except Exception as e:
        log.warning("Could not generate %s resume: %s", region, e)
        # Fallback to existing resume
        fallback = _find_resume()
        if fallback and Path(fallback).exists():
            return (Path(fallback).name, Path(fallback).read_bytes())
        return None


def _find_resume() -> str | None:
    """Find the best resume file to attach."""
    # Check env
    resume_env = os.environ.get("RESUME_PATH", "")
    if resume_env and Path(resume_env).exists():
        return resume_env

    # Check data/ for personal resume
    data_dir = Path(__file__).parent.parent / "data"
    for pattern in ["resume.pdf", "resume.docx", "resume.doc", "Sanjana*.pdf"]:
        matches = list(data_dir.glob(pattern))
        if matches:
            return str(matches[0])

    # Check data/resumes/ for any generated PDF
    resumes_dir = data_dir / "resumes"
    if resumes_dir.exists():
        pdfs = sorted(resumes_dir.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
        if pdfs:
            return str(pdfs[0])

    return None
