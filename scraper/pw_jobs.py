"""Playwright-based scrapers for sites that block plain requests.

Run with: python3 scraper/pw_jobs.py
Writes JSON to data/pw_jobs.json; main.py merges it via load_pw_jobs().
"""
import json
import logging
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from scraper.jobs import Job  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pw")

OUT = ROOT / "data" / "pw_jobs.json"


def _launch_browser(pw_instance, headless: bool = True):
    return pw_instance.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    )


def _new_page(browser, use_indeed_session: bool = False, use_naukri_session: bool = False, use_naukrigulf_session: bool = False, use_bayt_session: bool = False):
    ctx_kwargs = {
        "viewport": {"width": 1440, "height": 900},
        "locale": "en-US",
        "user_agent": random.choice([
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        ]),
    }
    if use_indeed_session:
        try:
            from apply.indeed import STATE_FILE, has_session
            if has_session():
                ctx_kwargs["storage_state"] = str(STATE_FILE)
        except Exception:
            pass
    elif use_naukri_session:
        try:
            from apply.naukri import STATE_FILE as NAUKRI_STATE, has_session as naukri_has_session
            if naukri_has_session():
                ctx_kwargs["storage_state"] = str(NAUKRI_STATE)
                ctx_kwargs["locale"] = "en-IN"
        except Exception:
            pass
    elif use_naukrigulf_session:
        try:
            from apply.naukrigulf import STATE_FILE as NAUKRIGULF_STATE, has_session as naukrigulf_has_session
            if naukrigulf_has_session():
                ctx_kwargs["storage_state"] = str(NAUKRIGULF_STATE)
        except Exception:
            pass
    elif use_bayt_session:
        try:
            from apply.bayt import STATE_FILE as BAYT_STATE, has_session as bayt_has_session
            if bayt_has_session():
                ctx_kwargs["storage_state"] = str(BAYT_STATE)
        except Exception:
            pass
    ctx = browser.new_context(**ctx_kwargs)
    ctx.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    )
    return ctx.new_page()


def scrape_indeed(pw_instance, query: str, location: str = "United Arab Emirates", limit: int = 20) -> list[Job]:
    jobs: list[Job] = []
    url = f"https://ae.indeed.com/jobs?q={query.replace(' ', '+')}&l={location.replace(' ', '+')}&fromage=7"
    browser = _launch_browser(pw_instance)
    page = _new_page(browser, use_indeed_session=True)

    _DESC_JS = """() => {
        const pane = document.querySelector('.jobsearch-RightPane, #jobDescriptionText');
        if (!pane) return null;
        if (pane.id === 'jobDescriptionText') return pane.innerText || '';
        const text = pane.innerText || '';
        const marker = 'Full job description';
        const idx = text.indexOf(marker);
        if (idx === -1) return null;
        let desc = text.substring(idx + marker.length);
        for (const stop of ['Report this job', 'Similar jobs', 'Browse jobs', 'Company reviews']) {
            const i = desc.indexOf(stop);
            if (i !== -1) desc = desc.substring(0, i);
        }
        return desc.trim();
    }"""
    try:
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        page.wait_for_selector("h3.jobTitle, h2.jobTitle, div.job_seen_beacon", timeout=20000)
        cards = page.query_selector_all("div.job_seen_beacon")[:limit]

        # Pass 1: collect card metadata (element handles go stale after clicking)
        raw: list[dict] = []
        for c in cards:
            t = c.query_selector("h3.jobTitle a, h2.jobTitle a, a.jcs-JobTitle")
            comp = c.query_selector("span.companyName, span[data-testid='company-name']")
            loc = c.query_selector("div.companyLocation, div[data-testid='text-location']")
            if not t:
                continue
            href = t.get_attribute("href") or ""
            if href.startswith("/"):
                href = f"https://ae.indeed.com{href}"
            # Keep the jk= param (unique job id); drop tracking params
            import re as _re
            jk_match = _re.search(r"[?&]jk=([a-f0-9]+)", href)
            if jk_match:
                url_out = f"https://ae.indeed.com/viewjob?jk={jk_match.group(1)}"
            else:
                url_out = href.split("&fccid=")[0].split("&vjs=")[0]
            raw.append({
                "title": (t.inner_text() or "").strip(),
                "company": (comp.inner_text() if comp else "Unknown").strip(),
                "location": (loc.inner_text() if loc else location).strip(),
                "url": url_out,
                "jk": jk_match.group(1) if jk_match else "",
            })

        # Pass 2: click each card -> full description appears in right pane (no page nav)
        from scraper.jobs import MAX_DESC_CHARS, _clean_text

        got_desc = 0
        for item in raw:
            desc = ""
            if item["jk"]:
                try:
                    link = page.locator(f'a[data-jk="{item["jk"]}"], a[href*="jk={item["jk"]}"]').first
                    if link.count():
                        link.click(timeout=4000)
                        page.wait_for_timeout(1800)
                        desc = page.evaluate(_DESC_JS) or ""
                except Exception as e:
                    log.debug("Indeed card click failed for %s: %s", item["jk"], e)
            if desc:
                got_desc += 1
                desc = _clean_text(desc)[:MAX_DESC_CHARS]
            jobs.append(Job(
                source="indeed",
                title=item["title"],
                company=item["company"],
                location=item["location"],
                url=item["url"],
                description=desc,
            ))
        log.info("Indeed: %d jobs, %d with descriptions (search-time extraction)", len(jobs), got_desc)
    except Exception as e:
        log.warning("Indeed scrape failed: %s", e)
    finally:
        page.close()
        browser.close()
    log.info("Indeed(playwright): %d jobs", len(jobs))
    return jobs


def scrape_bayt(pw_instance, query: str, location: str = "United Arab Emirates", limit: int = 20) -> list[Job]:
    jobs: list[Job] = []
    url = f"https://www.bayt.com/en/uae/jobs/?q={query.replace(' ', '+')}"
    # Bayt sits behind Cloudflare: headless Chromium gets the "Just a moment"
    # challenge even with a valid cf_clearance — run headed with the session.
    browser = _launch_browser(pw_instance, headless=False)
    page = _new_page(browser, use_bayt_session=True)
    try:
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        deadline = time.time() + 20
        while time.time() < deadline:
            if "Just a moment" not in page.title():
                break
            page.wait_for_timeout(1500)
        page.wait_for_selector("li[data-js-job]", timeout=20000)
        cards = page.query_selector_all("li[data-js-job]")[:limit]
        for c in cards:
            t = c.query_selector("h2 a[href*='/jobs/']")
            if not t:
                continue
            comp = c.query_selector(".job-company-location-wrapper a")
            loc = c.query_selector(".jb-tags a.t-default")
            href = t.get_attribute("href") or ""
            if href.startswith("/"):
                href = f"https://www.bayt.com{href}"
            jobs.append(Job(
                source="bayt",
                title=(t.get_attribute("title") or t.inner_text() or "").strip(),
                company=(comp.inner_text() if comp else "Unknown").strip(),
                location=(loc.inner_text() if loc else location).strip(),
                url=href,
            ))
    except Exception as e:
        log.warning("Bayt scrape failed: %s", e)
    finally:
        page.close()
        browser.close()
    log.info("Bayt(playwright): %d jobs", len(jobs))
    return jobs


def scrape_naukri(pw_instance, query: str, location: str = "India", limit: int = 20) -> list[Job]:
    """Naukri.com Playwright scraper (SPA + Akamai - needs homepage first + session)."""
    jobs: list[Job] = []
    query_slug = query.lower().replace(" ", "-")
    loc_slug = location.lower().replace(" ", "-") if location and location != "India" else "india"
    url = f"https://www.naukri.com/{query_slug}-jobs-in-{loc_slug}"
    browser = _launch_browser(pw_instance)
    page = _new_page(browser, use_naukri_session=True)
    try:
        # Must visit homepage first (Akamai bot protection)
        page.goto("https://www.naukri.com/", timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # Then navigate to search
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        # Naukri job cards - based on actual page structure
        # Cards: div[data-job-id], title: a.title, company: a.comp-name
        cards = page.query_selector_all("[data-job-id]")[:limit]
        
        for c in cards:
            t = c.query_selector("a.title, a[title]")
            comp = c.query_selector("a.comp-name, a.company, span.companyName")
            loc = c.query_selector("span.location, span.loc, li.location, span.city")
            if not t:
                continue
            href = t.get_attribute("href") or ""
            if href.startswith("/"):
                href = f"https://www.naukri.com{href}"
            if not href.startswith("http"):
                continue
            jobs.append(Job(
                source="naukri",
                title=(t.inner_text() or t.get_attribute("title") or "").strip(),
                company=(comp.inner_text() if comp else "Unknown").strip(),
                location=(loc.inner_text() if loc else location).strip(),
                url=href.split("?")[0],
            ))
    except Exception as e:
        log.warning("Naukri scrape failed: %s", e)
    finally:
        page.close()
        browser.close()
    log.info("Naukri(playwright): %d jobs", len(jobs))
    return jobs


def scrape_naukrigulf(pw_instance, query: str, location: str = "United Arab Emirates", limit: int = 20) -> list[Job]:
    """NaukriGulf.com Playwright scraper."""
    jobs: list[Job] = []
    query_slug = query.lower().replace(" ", "-")
    loc_slug = location.lower().replace(" ", "-") if location else "united-arab-emirates"
    url = f"https://www.naukrigulf.com/{query_slug}-jobs-in-{loc_slug}"
    browser = _launch_browser(pw_instance)
    page = _new_page(browser, use_naukrigulf_session=True)
    try:
        # Visit homepage first (bot protection)
        page.goto("https://www.naukrigulf.com/", timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)

        # Try common job-card selectors
        cards = page.query_selector_all(
            "div.job-card, div[class*='jobCard' i], div[class*='job-card' i], "
            "article, li[class*='job' i], div[data-job-id], div[class*='result' i]"
        )[:limit]

        for c in cards:
            t = c.query_selector(
                "a[class*='title' i], h2 a, h3 a, a[class*='job' i], a[title]"
            )
            comp = c.query_selector(
                "span[class*='company' i], div[class*='company' i], a[class*='company' i]"
            )
            loc = c.query_selector(
                "span[class*='loc' i], div[class*='loc' i], span[class*='city' i]"
            )
            if not t:
                continue
            href = t.get_attribute("href") or ""
            if href.startswith("/"):
                href = f"https://www.naukrigulf.com{href}"
            if not href.startswith("http"):
                continue
            jobs.append(Job(
                source="naukrigulf",
                title=(t.inner_text() or t.get_attribute("title") or "").strip(),
                company=(comp.inner_text() if comp else "Unknown").strip(),
                location=(loc.inner_text() if loc else location).strip(),
                url=href.split("?")[0],
            ))
    except Exception as e:
        log.warning("NaukriGulf scrape failed: %s", e)
    finally:
        page.close()
        browser.close()
    log.info("NaukriGulf(playwright): %d jobs", len(jobs))
    return jobs


def scrape_gulftalent(pw_instance, query: str, location: str = "United Arab Emirates", limit: int = 20) -> list[Job]:
    jobs: list[Job] = []
    url = f"https://www.gulftalent.com/jobs/search?keywords={query.replace(' ', '+')}"
    browser = _launch_browser(pw_instance)
    page = _new_page(browser)
    try:
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        cards = page.query_selector_all("div.job-card, div.search-result, article")[:limit]
        for c in cards:
            t = c.query_selector("h2 a, h3 a, a.job-title")
            comp = c.query_selector("span.company, div.company")
            loc = c.query_selector("span.location, div.location")
            if not t:
                continue
            href = t.get_attribute("href") or ""
            if href.startswith("/"):
                href = f"https://www.gulftalent.com{href}"
            jobs.append(Job(
                source="gulftalent",
                title=(t.inner_text() or "").strip(),
                company=(comp.inner_text() if comp else "Unknown").strip(),
                location=(loc.inner_text() if loc else location).strip(),
                url=href,
            ))
    except Exception as e:
        log.warning("GulfTalent scrape failed: %s", e)
    finally:
        page.close()
        browser.close()
    log.info("GulfTalent(playwright): %d jobs", len(jobs))
    return jobs


PW_SCRAPERS = {
    "indeed": scrape_indeed,
    "bayt": scrape_bayt,
    "gulftalent": scrape_gulftalent,
    "naukri": scrape_naukri,
    "naukrigulf": scrape_naukrigulf,
}


def run_all(
    queries: list[str],
    location: str = "United Arab Emirates",
    sources: list[str] | None = None,
) -> list[Job]:
    from playwright.sync_api import sync_playwright
    wanted = [s.lower() for s in (sources or list(PW_SCRAPERS))]
    fns = [fn for name, fn in PW_SCRAPERS.items() if name in wanted]
    if not fns:
        log.warning("No Playwright scrapers match sources: %s", ",".join(wanted))
        return []
    seen: dict[str, Job] = {}
    with sync_playwright() as pw:
        for q in queries:
            for fn in fns:
                try:
                    for j in fn(pw, q, location):
                        if j.job_id not in seen:
                            seen[j.job_id] = j
                except Exception as e:
                    log.error("%s failed '%s': %s", fn.__name__, q, e)
                time.sleep(random.uniform(1, 2))
    result = list(seen.values())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps([{
        "source": j.source, "title": j.title, "company": j.company,
        "location": j.location, "url": j.url, "job_id": j.job_id,
    } for j in result], indent=2))
    log.info("Playwright total: %d -> %s", len(result), OUT)
    return result


def fetch_indeed_descriptions_pw(urls: list[str], timeout_ms: int = 40000) -> dict[str, str]:
    """Indeed-specific description fetcher: searches + clicks job + extracts.

    Indeed blocks direct /viewjob URLs; must navigate from search results.
    """
    from scraper.jobs import MAX_DESC_CHARS, _clean_text

    out: dict[str, str] = {}
    if not urls:
        return out
    from playwright.sync_api import sync_playwright

    # Extract jk ids from URLs
    import re
    jk_ids = {}
    for url in urls:
        m = re.search(r"jk=([a-f0-9]+)", url)
        if m:
            jk_ids[m.group(1)] = url

    if not jk_ids:
        return out

    with sync_playwright() as pw:
        browser = _launch_browser(pw)
        page = _new_page(browser, use_indeed_session=True)
        try:
            # Multiple search queries to find target jobs
            queries = [
                "AI Engineer", "Machine Learning", "Software Engineer",
                "Data Scientist", "LLM Engineer", "GenAI",
            ]
            card_map = {}

            for query in queries:
                if len(card_map) >= len(jk_ids):
                    break
                try:
                    page.goto(
                        f"https://ae.indeed.com/jobs?q={query.replace(' ', '+')}&l=Dubai&fromage=14",
                        timeout=45000,
                        wait_until="domcontentloaded",
                    )
                    page.wait_for_selector("div.job_seen_beacon", timeout=15000)

                    cards = page.query_selector_all("div.job_seen_beacon")
                    for c in cards:
                        link = c.query_selector("a.jcs-JobTitle, h3.jobTitle a")
                        if link:
                            href = link.get_attribute("href") or ""
                            m = re.search(r"jk=([a-f0-9]+)", href)
                            if m and m.group(1) in jk_ids and m.group(1) not in card_map:
                                card_map[m.group(1)] = link
                except Exception as e:
                    log.debug("Indeed search %r failed: %s", query, e)

            log.info("Indeed: found %d cards for %d target jk ids", len(card_map), len(jk_ids))

            for jk, url in jk_ids.items():
                if url in out:
                    continue
                if jk not in card_map:
                    continue
                try:
                    # Re-find the link by jk id (element handles go stale)
                    link = page.locator(f'a[data-jk=\"{jk}\"]').first
                    if not link.count():
                        continue
                    link.click(timeout=5000)
                    page.wait_for_timeout(2500)

                    desc = page.evaluate('''() => {
                        const rightPane = document.querySelector(".jobsearch-RightPane");
                        if (!rightPane) return null;
                        const text = rightPane.innerText || "";
                        const marker = "Full job description";
                        const idx = text.indexOf(marker);
                        if (idx === -1) return null;
                        let desc = text.substring(idx + marker.length);
                        for (const stop of ["Report this job", "Similar jobs", "Browse jobs", "Company reviews"]) {
                            const sIdx = desc.indexOf(stop);
                            if (sIdx > 100) desc = desc.substring(0, sIdx);
                        }
                        return desc.trim();
                    }''')

                    if desc and len(desc) >= 80:
                        out[url] = _clean_text(desc)[:MAX_DESC_CHARS]
                        log.info("Indeed description ok: %s (%d chars)", jk, len(desc))
                except Exception as e:
                    log.warning("Indeed desc click failed %s: %s", jk, e)
        except Exception as e:
            log.warning("Indeed desc fetch failed: %s", e)
        finally:
            try:
                page.close()
                browser.close()
            except Exception:
                pass
    return out


def fetch_naukri_descriptions_pw(urls: list[str], timeout_ms: int = 40000) -> dict[str, str]:
    """Naukri-specific description fetcher (SPA + Akamai - needs homepage first)."""
    from scraper.jobs import MAX_DESC_CHARS, _clean_text

    out: dict[str, str] = {}
    if not urls:
        return out
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        # Naukri needs headed mode (Akamai blocks headless)
        browser = pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        page = _new_page(browser, use_naukri_session=True)
        try:
            # Homepage first (Akamai bot protection)
            page.goto("https://www.naukri.com/", timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)

            for url in urls:
                if url in out:
                    continue
                try:
                    page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)

                    # Extract description
                    desc = page.evaluate('''() => {
                        const selectors = [
                            'div[class*="JD"]', '#jobDescription',
                            'div.job-description', 'div.JD_desc',
                            'section.job-description'
                        ];
                        for (const sel of selectors) {
                            const el = document.querySelector(sel);
                            if (el) {
                                const text = el.innerText || '';
                                if (text.length > 100) return text;
                            }
                        }
                        return null;
                    }''')

                    if desc and len(desc) >= 80:
                        out[url] = _clean_text(desc)[:MAX_DESC_CHARS]
                        log.info("Naukri description ok: %s (%d chars)", url[:60], len(desc))
                except Exception as e:
                    log.warning("Naukri desc failed %s: %s", url[:60], e)
        except Exception as e:
            log.warning("Naukri desc fetch failed: %s", e)
        finally:
            try:
                page.close()
                browser.close()
            except Exception:
                pass
    return out


def fetch_naukrigulf_descriptions_pw(urls: list[str], timeout_ms: int = 40000) -> dict[str, str]:
    """NaukriGulf-specific description fetcher."""
    from scraper.jobs import MAX_DESC_CHARS, _clean_text

    out: dict[str, str] = {}
    if not urls:
        return out
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = _launch_browser(pw)
        page = _new_page(browser, use_naukrigulf_session=True)
        try:
            # Homepage first (bot protection)
            page.goto("https://www.naukrigulf.com/", timeout=45000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)

            for url in urls:
                if url in out:
                    continue
                try:
                    page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)

                    desc = page.evaluate('''() => {
                        const selectors = [
                            'div[class*="jobDescription" i]', '#jobDescription',
                            'div.job-description', 'div[class*="description" i]',
                            'section[class*="description" i]', 'div[class*="JD" i]'
                        ];
                        for (const sel of selectors) {
                            const el = document.querySelector(sel);
                            if (el) {
                                const text = el.innerText || '';
                                if (text.length > 100) return text;
                            }
                        }
                        return null;
                    }''')

                    if desc and len(desc) >= 80:
                        out[url] = _clean_text(desc)[:MAX_DESC_CHARS]
                        log.info("NaukriGulf description ok: %s (%d chars)", url[:60], len(desc))
                except Exception as e:
                    log.warning("NaukriGulf desc failed %s: %s", url[:60], e)
        except Exception as e:
            log.warning("NaukriGulf desc fetch failed: %s", e)
        finally:
            try:
                page.close()
                browser.close()
            except Exception:
                pass
    return out


def fetch_descriptions_pw(urls: list[str], timeout_ms: int = 40000) -> dict[str, str]:
    """Browser-based fallback for description pages that block plain requests.

    Returns {url: description_text} for pages where a known selector matched.
    """
    from bs4 import BeautifulSoup

    from scraper.jobs import DESC_SELECTORS, MAX_DESC_CHARS, _clean_text

    out: dict[str, str] = {}
    if not urls:
        return out
    from playwright.sync_api import sync_playwright

    # Bayt sits behind Cloudflare: description pages need the headed browser
    # + saved session (headless gets the "Just a moment" challenge).
    has_bayt = any("bayt.com" in u for u in urls)

    with sync_playwright() as pw:
        browser = _launch_browser(pw, headless=not has_bayt)
        page = _new_page(browser)
        bayt_page = None
        for url in urls:
            if url in out:
                continue
            # Indeed needs the session for description pages
            if "indeed.com" in url:
                try:
                    page.close()
                    page = _new_page(browser, use_indeed_session=True)
                except Exception:
                    pass
            # Bayt needs the session + a dedicated (headed) page
            if "bayt.com" in url and bayt_page is None:
                try:
                    bayt_page = _new_page(browser, use_bayt_session=True)
                except Exception:
                    pass
            nav_page = bayt_page if ("bayt.com" in url and bayt_page is not None) else page
            try:
                nav_page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                nav_page.wait_for_timeout(1200)
                soup = BeautifulSoup(nav_page.content(), "html.parser")
                for sel in DESC_SELECTORS:
                    el = soup.select_one(sel)
                    if el:
                        text = _clean_text(el.get_text("\n"))
                        if len(text) >= 80:
                            out[url] = text[:MAX_DESC_CHARS]
                            log.info("PW description ok: %s (%d chars)", url[:70], len(text))
                            break
                if url not in out:
                    # Indeed specific selector
                    if "indeed.com" in url:
                        el = soup.select_one("#jobDescriptionText, div#job-description, div.jobsearch-jobDescriptionText")
                        if el:
                            text = _clean_text(el.get_text("\n"))
                            if len(text) >= 80:
                                out[url] = text[:MAX_DESC_CHARS]
                                log.info("PW description ok (indeed): %s (%d chars)", url[:70], len(text))
                    if url not in out:
                        meta = soup.find("meta", attrs={"property": "og:description"})
                        if meta and meta.get("content") and len(meta["content"]) >= 60:
                            out[url] = _clean_text(meta["content"])[:MAX_DESC_CHARS]
            except Exception as e:
                log.warning("PW description failed %s: %s", url[:70], e)
        for p in (page, bayt_page):
            if p is not None:
                try:
                    p.close()
                except Exception:
                    pass
        try:
            browser.close()
        except Exception:
            pass
    return out


def load_pw_jobs(max_age_hours: int = 24, sources: list[str] | None = None) -> list[Job]:
    """Load cached data/pw_jobs.json results for merging (None-safe, age-bounded)."""
    if not OUT.exists():
        return []
    try:
        if time.time() - OUT.stat().st_mtime > max_age_hours * 3600:
            log.info("pw_jobs.json older than %dh — ignoring", max_age_hours)
            return []
        raw = json.loads(OUT.read_text())
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Could not read %s: %s", OUT, e)
        return []
    jobs = [Job(**item) for item in raw]
    if sources:
        wanted = {s.lower() for s in sources}
        jobs = [j for j in jobs if j.source.lower() in wanted]
    log.info("Loaded %d cached Playwright jobs from %s", len(jobs), OUT)
    return jobs


if __name__ == "__main__":
    QUERIES = ["AI Engineer", "Machine Learning Engineer", "LLM Engineer", "GenAI Engineer"]
    run_all(QUERIES)
