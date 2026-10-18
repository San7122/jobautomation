"""Job scrapers for UAE market: Indeed, GulfTalent, Bayt, LinkedIn public pages."""
from __future__ import annotations

import hashlib
import logging
import random
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("scraper")

HEADERS_LIST = [
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    },
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-GB,en;q=0.8",
    },
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    },
]


@dataclass
class Job:
    source: str
    title: str
    company: str
    location: str
    url: str
    description: str = ""
    salary: str = ""
    posted: str = ""
    job_id: str = field(default="")

    def __post_init__(self):
        if not self.job_id:
            raw = f"{self.source}|{self.title}|{self.company}|{self.url}"
            self.job_id = hashlib.sha256(raw.encode()).hexdigest()[:16]


def _get(
    url: str,
    params: dict | None = None,
    timeout: int = 20,
    delay: tuple[float, float] = (3, 7),
) -> Optional[requests.Response]:
    headers = random.choice(HEADERS_LIST)
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=timeout)
            if resp.status_code == 429:
                wait = (attempt + 1) * 8 + random.uniform(2, 5)
                log.warning("429 rate-limited on %s — waiting %.1fs", url, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            time.sleep(random.uniform(*delay))
            return resp
        except requests.RequestException as e:
            log.warning("Request failed %s: %s", url, e)
            if attempt < 2:
                time.sleep(5)
    return None


def search_indeed(query: str, location: str = "United Arab Emirates", limit: int = 25) -> list[Job]:
    """Scrape Indeed.ae public job search results."""
    jobs: list[Job] = []
    resp = _get(
        "https://ae.indeed.com/jobs",
        params={"q": query, "l": location, "fromage": 7, "sort": "date"},
    )
    if not resp:
        return jobs
    soup = BeautifulSoup(resp.text, "html.parser")
    for card in soup.select("div.job_seen_beacon, div.jobsearch-ResultsList > div")[:limit]:
        title_el = card.select_one("h2.jobTitle a, a.jtJNEc, span.jobTitle")
        company_el = card.select_one("span.companyName, div.company_location a")
        loc_el = card.select_one("div.companyLocation, span.location")
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        href = title_el.get("href") or ""
        if href.startswith("/"):
            href = f"https://ae.indeed.com{href}"
        jobs.append(Job(
            source="indeed",
            title=title,
            company=company_el.get_text(strip=True) if company_el else "Unknown",
            location=loc_el.get_text(strip=True) if loc_el else location,
            url=href,
        ))
    log.info("Indeed: %d jobs for '%s'", len(jobs), query)
    return jobs


def search_naukri(query: str, location: str = "India", limit: int = 25) -> list[Job]:
    """Scrape Naukri.com public job search results (for Indian jobs)."""
    jobs: list[Job] = []
    # Naukri URL format: https://www.naukri.com/<query>-jobs-in-<location>
    query_slug = query.lower().replace(" ", "-")
    loc_slug = location.lower().replace(" ", "-") if location else "india"
    url = f"https://www.naukri.com/{query_slug}-jobs-in-{loc_slug}"
    resp = _get(url)
    if not resp:
        # Try alternative search URL format
        resp = _get(
            "https://www.naukri.com/jobs",
            params={"q": query, "l": location},
        )
    if not resp:
        return jobs
    soup = BeautifulSoup(resp.text, "html.parser")
    for card in soup.select(
        "article.jobTuple, div.jobTuple, div.srp-experimented-jobTuple, "
        "div[data-job-id], li.newTuple"
    )[:limit]:
        title_el = card.select_one(
            "a.title, a[title], h2 a, h3 a, a.jobtitle, span.title"
        )
        company_el = card.select_one(
            "a.company, span.companyName, span.company, a.subTitle"
        )
        loc_el = card.select_one(
            "span.location, span.loc, li.location, span.city"
        )
        if not title_el:
            continue
        title = title_el.get_text(strip=True)
        href = title_el.get("href") or ""
        if href.startswith("/"):
            href = f"https://www.naukri.com{href}"
        if not href.startswith("http"):
            continue
        jobs.append(Job(
            source="naukri",
            title=title,
            company=company_el.get_text(strip=True) if company_el else "Unknown",
            location=loc_el.get_text(strip=True) if loc_el else location,
            url=href.split("?")[0],  # remove tracking params
        ))
    log.info("Naukri: %d jobs for '%s'", len(jobs), query)
    return jobs


def search_gulf_talent(query: str, location: str = "United Arab Emirates", limit: int = 25) -> list[Job]:
    """Scrape GulfTalent public search."""
    jobs: list[Job] = []
    resp = _get(
        "https://www.gulftalent.com/jobs/search",
        params={"keywords": query, "location": location},
    )
    if not resp:
        return jobs
    soup = BeautifulSoup(resp.text, "html.parser")
    for card in soup.select("div.job-card, div.search-result, article.job")[:limit]:
        title_el = card.select_one("h2 a, h3 a, a.job-title, span.job-title")
        company_el = card.select_one("span.company, div.company, a.company")
        loc_el = card.select_one("span.location, div.location")
        if not title_el:
            continue
        href = title_el.get("href") or ""
        if href.startswith("/"):
            href = f"https://www.gulftalent.com{href}"
        jobs.append(Job(
            source="gulftalent",
            title=title_el.get_text(strip=True),
            company=company_el.get_text(strip=True) if company_el else "Unknown",
            location=loc_el.get_text(strip=True) if loc_el else location,
            url=href,
        ))
    log.info("GulfTalent: %d jobs for '%s'", len(jobs), query)
    return jobs


def search_bayt(query: str, location: str = "United Arab Emirates", limit: int = 25) -> list[Job]:
    """Scrape Bayt.com public search."""
    jobs: list[Job] = []
    resp = _get(
        "https://www.bayt.com/en/uae/jobs/",
        params={"q": query, "where": location},
    )
    if not resp:
        return jobs
    soup = BeautifulSoup(resp.text, "html.parser")
    for card in soup.select("li.w-100, div.job-card, div[data-job-id]")[:limit]:
        title_el = card.select_one("h2 a, h3 a, a[href*='/jobs/']")
        company_el = card.select_one("span.clickable, div.companyName, h5")
        loc_el = card.select_one("span.t-small, div.location")
        if not title_el:
            continue
        href = title_el.get("href") or ""
        if href.startswith("/"):
            href = f"https://www.bayt.com{href}"
        jobs.append(Job(
            source="bayt",
            title=title_el.get_text(strip=True),
            company=company_el.get_text(strip=True) if company_el else "Unknown",
            location=loc_el.get_text(strip=True) if loc_el else location,
            url=href,
        ))
    log.info("Bayt: %d jobs for '%s'", len(jobs), query)
    return jobs


def _company_from_url(url: str, fallback: str = "Unknown") -> str:
    """Extract company name from LinkedIn job URL slug: .../jobs/view/{title}-at-{company}-{id}"""
    m = re.search(r"-at-([a-z0-9-]+)-\d+", url)
    if m:
        return m.group(1).replace("-", " ").title()
    return fallback


def search_linkedin_public(query: str, location: str = "United Arab Emirates", limit: int = 25, pages: int = 3) -> list[Job]:
    """Scrape LinkedIn public job search pages (logged-out), with pagination."""
    jobs: list[Job] = []
    for page_num in range(pages):
        start = page_num * 25
        resp = _get(
            "https://www.linkedin.com/jobs/search",
            params={
                "keywords": query,
                "location": location,
                "f_TPR": "r604800",
                "start": start,
            },
        )
        if not resp:
            break
        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("div.base-card, li.jobs-search-results__list-item")
        if not cards:
            break
        for card in cards[:limit]:
            title_el = card.select_one("h3.base-search-card__title, h3")
            loc_el = card.select_one("span.job-search-card__location")
            link_el = card.select_one("a.base-card__full-link, a")
            if not title_el or not link_el:
                continue
            href = link_el.get("href") or ""
            href = href.split("?")[0]
            title = title_el.get_text(strip=True)
            jobs.append(Job(
                source="linkedin",
                title=title,
                company=_company_from_url(href, fallback=title),
                location=loc_el.get_text(strip=True) if loc_el else location,
                url=href,
            ))
        if len(cards) < 5:
            break
    log.info("LinkedIn '%s': %d jobs (%d pages)", query, len(jobs), pages)
    return jobs


def search_all(
    queries: list[str],
    location: str = "United Arab Emirates",
    sources: list[str] | None = None,
    limit: int = 25,
) -> list[Job]:
    """Search each requested source for each query, dedupe by job_id.

    sources: subset of SOURCES keys; defaults to all sources.
    Sources that come back empty/failing 3 times in a row are skipped for the
    rest of the run (usually means bot-blocked — try --playwright instead).
    """
    wanted = [s.lower() for s in (sources or list(SOURCES))]
    seen: dict[str, Job] = {}
    empty_streak: dict[str, int] = {}
    disabled: set[str] = set()
    for q in queries:
        for src in wanted:
            if src in disabled:
                continue
            fn = SOURCES.get(src)
            if fn is None:
                # Playwright-only source (indeed/naukri) — handled elsewhere
                if src in ("indeed", "naukri"):
                    continue
                log.warning("Unknown source '%s' (known: %s)", src, ", ".join(SOURCES))
                continue
            jobs: list[Job] = []
            try:
                jobs = fn(q, location, limit)
                for job in jobs:
                    if job.job_id not in seen:
                        seen[job.job_id] = job
            except Exception as e:
                log.error("%s failed for '%s': %s", src, q, e)
            if jobs:
                empty_streak[src] = 0
            else:
                empty_streak[src] = empty_streak.get(src, 0) + 1
                if empty_streak[src] >= 3:
                    disabled.add(src)
                    log.warning(
                        "Source '%s' empty/failed %d times — skipping it for the rest of this run "
                        "(bot-blocked? try --playwright)",
                        src, empty_streak[src],
                    )
            time.sleep(random.uniform(3, 6))
    log.info("Total unique jobs: %d (sources: %s)", len(seen), ", ".join(wanted))
    return list(seen.values())


# Indeed & Naukri need Playwright (plain requests get 403/Akamai-blocked).
# They're handled by scraper/pw_jobs.py when --playwright is used.
SOURCES = {
    "linkedin": lambda q, loc, limit: search_linkedin_public(q, loc, limit=limit, pages=2),
    "indeed": None,  # Playwright only (403 on plain requests)
    "naukri": None,  # Playwright only (Akamai blocks plain requests)
    "gulftalent": search_gulf_talent,
    "bayt": search_bayt,
}

DESC_SELECTORS = [
    "div.show-more-less-html__markup",     # LinkedIn
    "div.jobsearch-jobDescriptionText",    # Indeed
    "#jobDescriptionText",                 # Indeed
    "div.jobview-description",             # GulfTalent
    ".job-description",                    # Bayt / generic
    "div.job-description",
    "[data-testid='job-description']",
    "div#job-description",
    "section.job-details",
]

MAX_DESC_CHARS = 4000


def fetch_description(url: str, timeout: int = 15) -> str:
    """Best-effort fetch of a job detail page's description text.

    Returns '' when the page blocks us or no known selector matches.
    """
    if not url:
        return ""
    resp = _get(url, timeout=timeout, delay=(1, 2))
    if not resp:
        return ""
    soup = BeautifulSoup(resp.text, "html.parser")
    for sel in DESC_SELECTORS:
        el = soup.select_one(sel)
        if el:
            text = _clean_text(el.get_text("\n"))
            if len(text) >= 80:
                return text[:MAX_DESC_CHARS]
    meta = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    if meta and meta.get("content"):
        return _clean_text(meta["content"])[:MAX_DESC_CHARS]
    return ""


def _clean_text(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Cross-source job deduplication
# ---------------------------------------------------------------------------
# The same vacancy often appears on LinkedIn, Indeed, Bayt, GulfTalent and
# Naukri with different source-specific job_ids.  We dedupe by:
#   1. canonical URL  (same link = same job)
#   2. normalized (title, company)  (same role at same org = same job)
# When two records match we keep the one with more complete data.
# ---------------------------------------------------------------------------

import re as _re

_URL_STRIP = _re.compile(r"(utm_.*=|&utm_.*|ref=.*|source=.*|campaign=.*|mc_cid=.*|mc_eid=.*)")


def _norm_url(url: str) -> str:
    """Canonicalize a job URL so tracking params don't break dedup."""
    if not url:
        return ""
    u = url.strip().lower()
    # strip fragment
    u = u.split("#", 1)[0]
    # strip common tracking query params
    u = _URL_STRIP.sub("", u)
    # strip trailing slash
    u = u.rstrip("/")
    # normalize www
    u = u.replace("www.", "")
    return u


def _norm_text(s: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation for fuzzy matching."""
    if not s:
        return ""
    s = s.lower()
    s = _re.sub(r"[^\w\s]", " ", s)
    s = _re.sub(r"\s+", " ", s).strip()
    return s


def _job_key(job: Job) -> tuple[str, str]:
    """Stable grouping key: (canonical_url, norm_title|norm_company)."""
    return (_norm_url(job.url), f"{_norm_text(job.title)}||{_norm_text(job.company)}")


def _complete_score(job: Job) -> int:
    """Higher = more complete record (prefer this one when merging)."""
    score = 0
    if job.description:
        score += 3
    if job.salary:
        score += 2
    if job.posted:
        score += 1
    if job.url:
        score += 1
    return score


def deduplicate_jobs(jobs: list[Job]) -> list[Job]:
    """Merge duplicate jobs across sources.

    Returns a deduplicated list preserving the first-seen order of each
    unique job.  Merged records keep the most complete fields.
    """
    if not jobs:
        return []

    # Group by canonical URL first (strongest signal).
    by_url: dict[str, list[Job]] = {}
    for j in jobs:
        by_url.setdefault(_norm_url(j.url), []).append(j)

    # Within each URL-group, collapse to a single best record.
    merged: list[Job] = []
    for group in by_url.values():
        best = max(group, key=_complete_score)
        merged.append(best)

    # Second pass: collapse by normalized (title, company) for jobs whose
    # URLs differ but describe the same role at the same company.
    by_key: dict[str, list[Job]] = {}
    order: list[str] = []
    for j in merged:
        key = _job_key(j)
        if key not in by_key:
            order.append(key)
            by_key[key] = []
        by_key[key].append(j)

    result: list[Job] = []
    for key in order:
        group = by_key[key]
        best = max(group, key=_complete_score)
        result.append(best)

    log.info("Deduped %d jobs → %d unique (%d removed)", len(jobs), len(result), len(jobs) - len(result))
    return result


def enrich_descriptions(
    jobs: list[Job],
    max_fetches: int = 50,
    skip_ids: set[str] | None = None,
    use_pw: bool = False,
) -> int:
    """Fetch missing descriptions for jobs (up to max_fetches). Returns fetch count.

    use_pw=True retries the failures through a headless browser (Playwright),
    which gets past plain-requests blocks on boards like LinkedIn/Indeed.
    Indeed/Naukri URLs go straight to Playwright (plain requests always 403).
    """
    skip_ids = skip_ids or set()
    fetched = 0
    failed: list[Job] = []
    playwright_only: list[Job] = []  # Indeed/Naukri — skip plain, go straight to PW

    for job in jobs:
        if fetched >= max_fetches:
            break
        if job.description or job.job_id in skip_ids or not job.url:
            continue

        # Indeed/Naukri: plain requests always fail — route to Playwright
        if "indeed.com" in job.url or "naukri.com" in job.url:
            playwright_only.append(job)
            continue

        desc = fetch_description(job.url)
        fetched += 1
        if desc:
            job.description = desc
            log.info("Description fetched: %s (%d chars)", job.title[:60], len(desc))
        else:
            failed.append(job)

    # Playwright for Indeed/Naukri (always) + plain-request failures
    pw_targets = playwright_only[:max_fetches]
    if use_pw and failed:
        pw_targets += failed[:max(0, max_fetches - len(pw_targets))]

    if pw_targets:
        log.info("PW description fetch for %d pages (%d Indeed/Naukri + %d blocked)",
                 len(pw_targets), len(playwright_only[:max_fetches]), len(failed))
        try:
            # Split by source for specialized fetchers
            indeed_urls = [j.url for j in pw_targets if "indeed.com" in j.url]
            naukri_urls = [j.url for j in pw_targets if "naukri.com" in j.url]
            other_urls = [j.url for j in pw_targets
                          if "indeed.com" not in j.url and "naukri.com" not in j.url]

            got: dict[str, str] = {}
            if indeed_urls:
                from scraper.pw_jobs import fetch_indeed_descriptions_pw
                got.update(fetch_indeed_descriptions_pw(indeed_urls))
            if naukri_urls:
                from scraper.pw_jobs import fetch_naukri_descriptions_pw
                got.update(fetch_naukri_descriptions_pw(naukri_urls))
            if other_urls:
                from scraper.pw_jobs import fetch_descriptions_pw
                got.update(fetch_descriptions_pw(other_urls))

            for job in pw_targets:
                text = got.get(job.url)
                if text:
                    job.description = text
                    fetched += 1
        except Exception as e:
            log.warning("PW description fetch failed: %s", e)

    return fetched
