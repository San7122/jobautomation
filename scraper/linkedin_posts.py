"""LinkedIn post scraper — collect recruiter emails from urgent hiring posts."""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

log = logging.getLogger("linkedin_posts")

ROOT = Path(__file__).parent.parent
STATE_FILE = ROOT / "data" / "linkedin_state.json"

# Email regex
EMAIL_RE = r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"

# Bad email patterns
BAD_LOCAL = {"no-reply", "noreply", "bounce", "postmaster", "abuse", "support", "sales", "marketing", "billing"}
BAD_DOMAINS = {"example.com", "example.org", "sentry.io", "wixpress.com", "glassdoor.com", "linkedin.com"}

# Search queries focused on AI/Tech roles
URGENT_QUERIES = [
    "urgent hiring AI engineer UAE",
    "hiring machine learning engineer Dubai",
    "urgent hiring data scientist UAE",
    "hiring AI ML engineer Dubai",
    "urgent requirement data engineer UAE",
    "hiring software engineer AI Dubai",
    "recruitment AI ML UAE",
    "hiring NLP engineer Dubai",
    "hiring MLOps engineer UAE",
]

# Tech/AI related keywords in post content
TECH_KEYWORDS = {
    # AI/ML roles
    "ai", "artificial intelligence", "machine learning", "ml", "deep learning",
    "data scientist", "data science", "data engineer", "data analyst",
    "nlp", "natural language", "computer vision", "cv",
    "mlops", "mlops engineer", "ai engineer", "ml engineer",
    "python", "tensorflow", "pytorch", "keras", "scikit",
    "llm", "large language model", "genai", "generative ai",
    "rag", "retrieval augmented", "prompt engineering", "agent framework",
    "neural network", "deep neural", "convolutional", "recurrent",
    
    # Software/Cloud roles
    "software engineer", "software developer", "full stack", "backend",
    "frontend", "devops", "cloud engineer", "aws", "azure", "gcp",
    "kubernetes", "docker", "microservices", "api",
    
    # Data roles
    "etl", "data pipeline", "big data", "spark", "hadoop", "kafka",
    "database", "sql", "nosql", "mongodb", "postgresql",
    
    # Tech companies/keywords
    "tech", "technology", "it ", "information technology",
    "startup", "saas", "platform", "api", "saas",
}

# Tech company indicators (real companies hiring tech talent)
TECH_COMPANY_INDICATORS = {
    # Major tech companies
    "google", "microsoft", "amazon", "meta", "facebook", "apple",
    "netflix", "uber", "airbnb", "stripe", "square", "paypal",
    "salesforce", "oracle", "ibm", "intel", "nvidia", "amd",
    "adobe", "vmware", "cisco", "dell", "hp ", "lenovo",
    
    # AI/ML specific
    "openai", "anthropic", "deepmind", "hugging face", "stability ai",
    "databricks", "snowflake", "palantir", "scale ai",
    
    # UAE tech companies
    "careem", "emirates digital", "dubai internet city", "dtec",
    "in5", "sharjah research", "masdar", "addl", "emirates nbd digital",
    
    # Indian tech (hiring for UAE roles)
    "tcs", "infosys", "wipro", "hcl", "tech mahindra", "ltimindtree",
    "cognizant", "accenture", "capgemini", "genpact", "wells fargo",
    
    # Known tech recruiters/agencies
    "robert half technology", "teksystems", "experis", "hays technology",
    "michaelpage technology", "randstad technologies",
}

# Fake/suspicious patterns
FAKE_PATTERNS = {
    "sushi", "chef", "security guard", "hse officer", "construction",
    "hospitality", "hotel", "restaurant", "driver", "cleaner", "waiter",
    "electrician", "plumber", "welder", "mechanic", "laborer",
    "housekeeping", "receptionist", "cashier", "sales executive",
    "marketing executive", "business development", "field sales",
}

# Real tech recruiter title patterns
TECH_RECRUITER_TITLES = {
    "technical recruiter", "it recruiter", "tech recruiter",
    "talent acquisition", "recruitment specialist", "hiring manager",
    "hr ", "human resources", "people operations",
    "engineering manager", "cto", "vp engineering", "head of technology",
    "ai recruiter", "data recruiter", "software recruiter",
}


def has_session() -> bool:
    """Check if LinkedIn session exists."""
    return STATE_FILE.exists() and STATE_FILE.stat().st_size > 100


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


def is_tech_relevant(post_text: str, company: str = "") -> bool:
    """Check if post is relevant to AI/Tech roles."""
    text_lower = post_text.lower()
    company_lower = company.lower()
    
    # Check for fake patterns (non-tech roles)
    for pattern in FAKE_PATTERNS:
        if pattern in text_lower:
            # If it's clearly non-tech, skip
            return False
    
    # Check for tech keywords in post
    tech_score = 0
    for keyword in TECH_KEYWORDS:
        if keyword in text_lower:
            tech_score += 1
    
    # Check for tech company
    for company_name in TECH_COMPANY_INDICATORS:
        if company_name in company_lower or company_name in text_lower:
            tech_score += 3  # Tech company is strong indicator
    
    # Check for tech recruiter title
    for title in TECH_RECRUITER_TITLES:
        if title in company_lower or title in text_lower:
            tech_score += 2
    
    # Require minimum tech relevance score
    return tech_score >= 2


def is_real_recruiter(email: str, post_text: str) -> bool:
    """Filter out fake/suspicious emails."""
    email_lower = email.lower()
    text_lower = post_text.lower()
    
    # Skip generic/free email domains (often fake)
    free_email_domains = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com"}
    domain = email_lower.split("@")[1] if "@" in email_lower else ""
    
    # Allow some free emails if they're from tech companies
    if domain in free_email_domains:
        # Check if post mentions real tech company
        for company in TECH_COMPANY_INDICATORS:
            if company in text_lower:
                return True
        # Skip generic free emails
        return False
    
    # Skip suspicious patterns
    suspicious = ["test", "dummy", "sample", "example", "fake", "spam"]
    for pattern in suspicious:
        if pattern in email_lower:
            return False
    
    return True


def extract_emails_from_text(text: str, candidate_email: str = "") -> list[str]:
    """Extract valid emails from text."""
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
    return unique


def scrape_linkedin_posts(
    queries: list[str] | None = None,
    limit_per_query: int = 20,
    location: str = "United Arab Emirates",
) -> list[dict]:
    """Scrape LinkedIn posts for AI/Tech hiring with emails.

    Returns list of {email, name, company, post_text, post_url, query}.
    """
    from playwright.sync_api import sync_playwright

    if not has_session():
        log.error("LinkedIn session missing — run `python3 main.py linkedin-login` first")
        return []

    state = json.loads(STATE_FILE.read_text())
    queries = queries or URGENT_QUERIES
    all_results = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = browser.new_context(
            storage_state=state,
            viewport={"width": 1440, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page = ctx.new_page()

        for query in queries:
            log.info("Searching LinkedIn posts: '%s'", query)
            try:
                # Search posts
                search_url = f"https://www.linkedin.com/search/results/content/?keywords={query.replace(' ', '%20')}"
                page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(4000)

                # Scroll to load more posts
                for _ in range(3):
                    page.mouse.wheel(0, 2000)
                    page.wait_for_timeout(1500)

                # Find posts by looking for "Feed post" text containers
                # LinkedIn uses dynamic class names, so we find by content
                post_texts = page.locator("text=/Feed post/i").all()
                log.info("Found %d potential posts for '%s'", len(post_texts), query)

                processed = 0
                for post_marker in post_texts:
                    if processed >= limit_per_query:
                        break
                    try:
                        # Get the parent container (div[5] gives full post)
                        container = post_marker.locator("xpath=ancestor::div[5]")
                        if not container.count():
                            continue

                        post_text = container.inner_text(timeout=2000)[:3000]
                        emails = extract_emails_from_text(post_text)

                        if not emails:
                            continue

                        # Get poster name - first line after "Feed post"
                        poster_name = ""
                        try:
                            lines = post_text.split("\n")
                            for i, line in enumerate(lines):
                                if "Feed post" in line and i + 1 < len(lines):
                                    poster_name = lines[i + 1].strip()
                                    break
                        except Exception:
                            pass

                        # Get company/headline
                        company = ""
                        try:
                            lines = post_text.split("\n")
                            for line in lines:
                                if any(kw in line.lower() for kw in ["recruit", "hiring", "hr ", "talent", "consultant", "tech", "ai", "data"]):
                                    company = line.strip()[:100]
                                    break
                        except Exception:
                            pass

                        # Filter: Only AI/Tech relevant posts
                        if not is_tech_relevant(post_text, company):
                            log.debug("Skipping non-tech post: %s", post_text[:100])
                            continue

                        processed += 1

                        # Get post URL
                        post_url = ""
                        try:
                            link_el = container.locator("a[href*='/posts/'], a[href*='/feed/update/']").first
                            if link_el.count():
                                post_url = link_el.get_attribute("href") or ""
                        except Exception:
                            pass

                        for email in emails:
                            # Filter: Only real recruiters
                            if not is_real_recruiter(email, post_text):
                                log.debug("Skipping fake email: %s", email)
                                continue
                            
                            all_results.append({
                                "email": email,
                                "name": poster_name,
                                "company": company,
                                "post_text": post_text[:500],
                                "post_url": post_url,
                                "query": query,
                            })

                    except Exception as e:
                        log.debug("Post parsing failed: %s", e)
                        continue

                # Rate limiting
                time.sleep(3)

            except Exception as e:
                log.warning("Search failed for '%s': %s", query, e)
                continue

        browser.close()

    # Dedupe by email
    seen = set()
    unique = []
    for r in all_results:
        if r["email"] not in seen:
            seen.add(r["email"])
            unique.append(r)

    log.info("Extracted %d unique AI/Tech recruiter emails from %d posts", len(unique), len(all_results))
    return unique
