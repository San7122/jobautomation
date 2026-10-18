"""Playwright application autofill: open the listing, fill the form, then either

- gate (default): leave the browser open for the human to review + submit, or
- auto-submit (--auto-submit): click submit and verify a confirmation page.

Field matching is label/autocomplete/name-based so it works across ATSes
(Greenhouse, Lever, Workday, Ashby, Workable, company career pages).
Fragile by nature — every step is guarded; failures fall back to manual paste.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.profile import PROFILE  # noqa: E402

log = logging.getLogger("apply")

ATS_HOST_PATTERNS = (
    "greenhouse.io",
    "jobs.lever.co",
    "apply.workable.com",
    "myworkdayjobs.com",
    "ashbyhq.com",
    "smartrecruiters.com",
    "jobvite.com",
    "bamboohr.com",
    "recruitee.com",
    "personio.",
    "naukrigulf.com",
    "gulfjob",
)

FIELD_SELECTORS = {
    "first name": ["input[name*='first' i]", "input[autocomplete='given-name']", "input[aria-label*='first name' i]"],
    "last name": ["input[name*='last' i]", "input[autocomplete='family-name']", "input[aria-label*='last name' i]"],
    "full name": ["input[name*='full_name' i]", "input[name*='fullname' i]", "input[name*='your-name' i]", "input[autocomplete='name']"],
    "email": ["input[type=email]", "input[name*='email' i]", "input[autocomplete='email']", "input[aria-label*='email' i]"],
    "phone": ["input[type=tel]", "input[name*='phone' i]", "input[autocomplete='tel']", "input[aria-label*='phone' i]"],
    "cover letter": [
        "textarea[name*='cover' i]", "textarea[id*='cover' i]",
        "textarea[aria-label*='cover' i]", "#cover_letter",
        "textarea[name*='coverLetter' i]",
    ],
}

SUBMIT_SELECTORS = [
    "input[type=submit]",
    "button[type=submit]",
    "button:has-text('Submit application')",
    "button:has-text('Submit Application')",
    "button:has-text('Send application')",
    "button:has-text('Apply now')",
    "button:has-text('Apply Now')",
    "button:has-text('Submit')",
    "button:has-text('Apply')",
]

SUCCESS_RE = re.compile(
    r"thank you|application (has been )?(received|submitted)|successfully (submitted|received)"
    r"|we(?:'|’)ve received your|congratulations|your application (is|was) (complete|sent)",
    re.I,
)


def _fill(page, selectors: list[str], value: str, field: str, filled: list[str]) -> bool:
    if not value:
        return False
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0 or not loc.is_visible():
                continue
            loc.fill(value, timeout=3000)
            log.info("Filled %s via %s", field, sel)
            filled.append(field)
            return True
        except Exception:
            continue
    return False


def _has_form(page) -> bool:
    try:
        return page.locator("input:not([type=hidden]), textarea").count() > 0
    except Exception:
        return False


def _find_apply_link(page) -> str | None:
    try:
        hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
    except Exception:
        return None
    clean = [
        h for h in hrefs
        if h.startswith(("http://", "https://", "file://"))
        and not re.search(r"#[a-z]*apply|mailto:|javascript:", h, re.I)
    ]
    for pat in ATS_HOST_PATTERNS:  # known ATS hosts first
        for h in clean:
            if pat in h:
                return h
    for h in clean:
        if re.search(r"/apply([/?]|$)|[?&]apply=|application", h, re.I):
            return h
    return None


def run_fill(
    job: dict,
    cover_letter: str,
    resume_file: str | Path | None = None,
    *,
    auto_submit: bool = False,
    headless: bool = False,
    wait_for_close: bool = True,
    timeout_ms: int = 45000,
) -> dict:
    """Open job['url'], autofill what we can. Returns a status dict."""
    from playwright.sync_api import sync_playwright

    result: dict = {"status": "error", "filled": [], "missing": [], "url": job.get("url", "")}
    name = PROFILE.get("name", "")
    first_name, _, last_name = name.partition(" ")

    with sync_playwright() as pw:
        # Naukri SPA + Akamai requires headed mode
        job_url = job.get("url") or ""
        is_naukri = "naukri.com" in job_url
        if is_naukri:
            headless = False

        browser = pw.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx_kwargs: dict = {
            "viewport": {"width": 1440, "height": 900},
            "locale": "en-US",
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        }
        is_linkedin = "linkedin.com" in job_url
        is_indeed = "indeed.com" in job_url
        is_naukrigulf = "naukrigulf.com" in job_url
        is_bayt = "bayt.com" in job_url
        if is_linkedin:
            try:
                from apply.linkedin import STATE_FILE, has_session

                if has_session():
                    ctx_kwargs["storage_state"] = str(STATE_FILE)
            except Exception as e:
                log.warning("LinkedIn session unavailable: %s", e)
        elif is_indeed:
            try:
                from apply.indeed import STATE_FILE as INDEED_STATE, has_session as indeed_has_session

                if indeed_has_session():
                    ctx_kwargs["storage_state"] = str(INDEED_STATE)
            except Exception as e:
                log.warning("Indeed session unavailable: %s", e)
        elif is_naukri:
            try:
                from apply.naukri import STATE_FILE as NAUKRI_STATE, has_session as naukri_has_session

                if naukri_has_session():
                    ctx_kwargs["storage_state"] = str(NAUKRI_STATE)
            except Exception as e:
                log.warning("Naukri session unavailable: %s", e)
        elif is_naukrigulf:
            try:
                from apply.naukrigulf import STATE_FILE as NAUKRIGULF_STATE, has_session as naukrigulf_has_session

                if naukrigulf_has_session():
                    ctx_kwargs["storage_state"] = str(NAUKRIGULF_STATE)
            except Exception as e:
                log.warning("NaukriGulf session unavailable: %s", e)
        elif is_bayt:
            try:
                from apply.bayt import STATE_FILE as BAYT_STATE, has_session as bayt_has_session

                if bayt_has_session():
                    ctx_kwargs["storage_state"] = str(BAYT_STATE)
            except Exception as e:
                log.warning("Bayt session unavailable: %s", e)
        ctx = browser.new_context(**ctx_kwargs)
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page = ctx.new_page()
        try:
            # Naukri needs homepage first (SPA + Akamai) before job URL
            if is_naukri:
                from apply.naukri import HOME_URL
                page.goto(HOME_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(3000)

            page.goto(job["url"], wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(1500)

            # ---- LinkedIn Easy Apply path (needs a saved login session) ----
            if is_linkedin:
                from apply.linkedin import _looks_logged_in, run_easy_apply

                if not _looks_logged_in(page):
                    result["status"] = "no_session"
                    result["missing"].append("LinkedIn session missing/expired — run `python3 main.py linkedin-login`")
                    return result
                result = run_easy_apply(page, job, cover_letter, resume_file, result, timeout_ms)
                if result.get("status") not in ("no_easy_apply",):
                    return result
                # External apply (company-site redirect) — skip early, generic ATS fill wastes time
                ext = result.pop("external_url", "")
                result["status"] = "external_apply"
                result["missing"].clear()
                result["missing"].append(
                    f"external apply (not Easy Apply) — {ext or 'company site'} — skipped to save time"
                )
                if ext:
                    result["url"] = ext
                return result

            # ---- Indeed Easy Apply path (needs a saved login session) ----
            elif is_indeed:
                from apply.indeed import _looks_logged_in as indeed_logged_in, run_easy_apply as indeed_easy_apply

                # Indeed blocks direct /viewjob URLs — navigate via homepage first
                if "viewjob" in job.get("url", ""):
                    try:
                        page.goto("https://ae.indeed.com/", wait_until="domcontentloaded", timeout=timeout_ms)
                        page.wait_for_timeout(2000)
                    except Exception:
                        pass

                if not indeed_logged_in(page):
                    # Try homepage check separately
                    try:
                        page.goto("https://ae.indeed.com/", wait_until="domcontentloaded", timeout=timeout_ms)
                        page.wait_for_timeout(2000)
                    except Exception:
                        pass
                    if not indeed_logged_in(page):
                        result["status"] = "no_session"
                        result["missing"].append("Indeed session missing/expired — run `python3 main.py indeed-login`")
                        return result

                # Navigate to job via search (direct viewjob is blocked)
                job_url = job.get("url", "")
                if "viewjob" in job_url:
                    import re as _re
                    jk_match = _re.search(r"jk=([a-f0-9]+)", job_url)
                    if jk_match:
                        jk = jk_match.group(1)
                        # Search and click through
                        try:
                            page.goto(
                                f"https://ae.indeed.com/jobs?q={job.get('title', 'engineer').replace(' ', '+')[:30]}&l=Dubai",
                                wait_until="domcontentloaded", timeout=timeout_ms,
                            )
                            page.wait_for_selector("div.job_seen_beacon", timeout=15000)
                            page.wait_for_timeout(1500)
                            link = page.locator(f'a[data-jk="{jk}"]').first
                            if link.count():
                                link.click(timeout=5000)
                                page.wait_for_timeout(2500)
                            else:
                                # Try any job match
                                log.info("Indeed: jk %s not on search page, trying direct", jk)
                                page.goto(job_url, wait_until="domcontentloaded", timeout=timeout_ms)
                                page.wait_for_timeout(2000)
                        except Exception as e:
                            log.warning("Indeed search navigation failed: %s", e)
                            try:
                                page.goto(job_url, wait_until="domcontentloaded", timeout=timeout_ms)
                                page.wait_for_timeout(2000)
                            except Exception:
                                pass
                else:
                    try:
                        page.goto(job_url, wait_until="domcontentloaded", timeout=timeout_ms)
                        page.wait_for_timeout(2000)
                    except Exception:
                        pass

                result = indeed_easy_apply(page, job, cover_letter, resume_file, result, timeout_ms)
                if result.get("status") not in ("no_easy_apply",):
                    return result
                # no Easy Apply button -> follow the company-site link and fill there
                result["status"] = "error"
                result["missing"].clear()
                ext = result.pop("external_url", "")
                if ext:
                    log.info("Following external apply link: %s", ext)
                    result["url"] = ext
                    page.goto(ext, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(2500)

            # ---- Naukri Easy Apply path (needs a saved login session) ----
            elif is_naukri:
                from apply.naukri import _looks_logged_in as naukri_logged_in, run_easy_apply as naukri_easy_apply

                if not naukri_logged_in(page):
                    result["status"] = "no_session"
                    result["missing"].append("Naukri session missing/expired — run `python3 main.py naukri-login`")
                    return result
                result = naukri_easy_apply(page, job, cover_letter, resume_file, result, timeout_ms)
                if result.get("status") not in ("no_easy_apply",):
                    return result
                # no Easy Apply button -> follow the company-site link and fill there
                result["status"] = "error"
                result["missing"].clear()
                ext = result.pop("external_url", "")
                if ext:
                    log.info("Following external apply link: %s", ext)
                    result["url"] = ext
                    page.goto(ext, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(2500)

            # no form on the listing itself? follow the board's apply link (ATS)
            if not _has_form(page):
                link = _find_apply_link(page)
                if link and link != job["url"]:
                    log.info("Following apply link: %s", link)
                    result["url"] = link
                    page.goto(link, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(1500)

            filled, missing = result["filled"], result["missing"]

            # name: prefer first/last split, fall back to a single full-name field
            got_first = _fill(page, FIELD_SELECTORS["first name"], first_name, "first name", filled)
            got_last = _fill(page, FIELD_SELECTORS["last name"], last_name, "last name", filled)
            if not (got_first and got_last):
                _fill(page, FIELD_SELECTORS["full name"], name, "name", filled)

            if not _fill(page, FIELD_SELECTORS["email"], PROFILE.get("email", ""), "email", filled):
                missing.append("email")
            _fill(page, FIELD_SELECTORS["phone"], PROFILE.get("phone", ""), "phone", filled)
            if not _fill(page, FIELD_SELECTORS["cover letter"], cover_letter, "cover letter", filled):
                missing.append("cover letter (paste manually — see terminal output)")

            if resume_file:
                try:
                    file_loc = page.locator("input[type=file]").first
                    if file_loc.count():
                        file_loc.set_input_files(str(resume_file), timeout=5000)
                        log.info("Attached resume: %s", resume_file)
                        filled.append("resume file")
                    else:
                        missing.append("resume upload field")
                except Exception as e:
                    log.warning("Resume attach failed: %s", e)
                    missing.append(f"resume upload ({e.__class__.__name__})")

            result["url"] = page.url

            if not auto_submit:
                result["status"] = "filled"
                if wait_for_close:
                    print(
                        "\n--- Browser me form bhara hua hai ---\n"
                        "1. Check karo, CAPTCHA/consent khud do.\n"
                        "2. Submit kar do (ya mat karo — bhi chalega).\n"
                        "3. Browser band karo — tab tak yeh process wait karega.\n"
                        "4. Submit hone ke baad UI/CLI se 'Mark applied' karo.\n"
                    )
                    try:
                        page.wait_for_event("close", timeout=0)
                    except Exception as e:
                        log.info("Wait for browser close ended: %s", e)
                return result

            # ---- explicit auto-submit (opt-in) ----
            clicked = False
            for sel in SUBMIT_SELECTORS:
                try:
                    btn = page.locator(sel).first
                    if btn.count() and btn.is_visible():
                        btn.click(timeout=3000)
                        log.info("Clicked submit via %s", sel)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                result["status"] = "error"
                result["missing"].append("submit button not found")
                return result

            page.wait_for_timeout(4000)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
            result["url"] = page.url
            try:
                body_text = page.locator("body").inner_text(timeout=5000)
            except Exception:
                body_text = ""
            result["status"] = "submitted" if SUCCESS_RE.search(body_text) else "submitted_unconfirmed"
            page.wait_for_timeout(2000)
            return result

        except Exception as e:
            log.exception("Autofill failed for %s", job.get("title", ""))
            result["error"] = str(e)
            try:
                shot = ROOT / "logs" / f"apply-fail-{job.get('job_id', 'unknown')}.png"
                page.screenshot(path=str(shot))
                result["screenshot"] = str(shot)
            except Exception:
                pass
            return result
        finally:
            try:
                browser.close()
            except Exception:
                pass
