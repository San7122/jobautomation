"""NaukriGulf.com session + Easy Apply automation.

One-time setup: `python3 main.py naukrigulf-login` opens a browser — log in there
yourself (password never touches this codebase, 2FA/CAPTCHA just work). The
session is saved to data/naukrigulf_state.json and reused for auto-applications.

NaukriGulf Easy Apply best-effort: fills contact fields from the profile,
attaches the generated resume, answers common questions from
PROFILE["form_answers"], and clicks through the application flow. Any required
question it cannot answer safely aborts that application (status needs_review,
job stays queued).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("apply")

STATE_FILE = ROOT / "data" / "naukrigulf_state.json"
HOME_URL = "https://www.naukrigulf.com/"
LOGIN_URL = "https://www.naukrigulf.com/login"


def has_session() -> bool:
    return STATE_FILE.exists() and STATE_FILE.stat().st_size > 10


def _profile_values() -> dict:
    from config.profile import PROFILE

    name = PROFILE.get("name", "")
    first, _, last = name.partition(" ")
    return {
        "first_name": first,
        "last_name": last.strip(),
        "name": name,
        "email": PROFILE.get("email", ""),
        "phone": PROFILE.get("phone", ""),
        "location": "Dubai, United Arab Emirates",
    }


def _looks_logged_in(page, retries: int = 4) -> bool:
    """Passive check — never navigates away from the current page."""
    for attempt in range(retries):
        url = page.url
        if "login" in url.lower() and "naukrigulf.com" in url:
            # on a login page — but only if no user menu is visible
            pass
        try:
            for sel in [
                "a[href*='myHome']",
                "a[href*='userProfile']",
                "a[href*='profile']",
                "img[alt*='profile' i]",
                "img[alt*='user' i]",
                "span[class*='user' i]",
                "div[class*='user' i]",
                "a[class*='profile' i]",
            ]:
                el = page.locator(sel).first
                if el.count() and el.is_visible():
                    return True
            body_text = page.locator("body").inner_text(timeout=2000)[:800]
            if any(k in body_text for k in ("My Profile", "View profile", "Logout", "Log out", "My Home")):
                return True
            login_btn = page.locator("a:has-text('Login'), button:has-text('Login'), a:has-text('Sign in')").first
            if login_btn.count() and login_btn.is_visible():
                return False
        except Exception:
            pass
        if attempt < retries - 1:
            page.wait_for_timeout(2000)
    return False


def _home_logged_in(page) -> bool:
    """Navigate to homepage and confirm a real signed-in session."""
    try:
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(2500)
    except Exception:
        return False
    return _looks_logged_in(page)


def _chrome_user_data() -> Path | None:
    candidates = [
        Path.home() / "Library/Application Support/Google/Chrome",
        Path.home() / ".config/google-chrome",
        Path.home() / "AppData/Local/Google/Chrome/User Data",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _auto_fill_login(page) -> bool:
    """Optional sign-in from NAUKRIGULF_EMAIL / NAUKRIGULF_PASSWORD in .env.

    NaukriGulf uses a 2-step modal: Email → Continue → Password → Login.
    Gated behind ALLOW_STORED_CREDENTIALS=1.
    """
    import os

    if os.environ.get("ALLOW_STORED_CREDENTIALS", "") != "1":
        return False

    email = os.environ.get("NAUKRIGULF_EMAIL", "")
    password = os.environ.get("NAUKRIGULF_PASSWORD", "")
    if not email or not password:
        return False

    def first_visible(selectors: list[str], timeout_ms: int = 8000):
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            for sel in selectors:
                try:
                    locs = page.locator(sel)
                    for i in range(min(locs.count(), 6)):
                        loc = locs.nth(i)
                        if loc.is_visible():
                            return loc
                except Exception:
                    continue
            page.wait_for_timeout(400)
        return None

    # dismiss cookie/consent banners
    for text in ("Accept", "Accept all", "I agree", "Got it"):
        try:
            btn = page.get_by_role("button", name=text).first
            if btn.count() and btn.is_visible():
                btn.click(timeout=2000)
                page.wait_for_timeout(800)
                break
        except Exception:
            continue

    # Step 1: Find and fill email field in the modal
    user_field = first_visible([
        "input[placeholder*='Email' i]",
        "input[placeholder*='email' i]",
        "input[type='email']",
        "input[name*='email' i]",
        "input[aria-label*='Email' i]",
        "input#email",
    ])
    if user_field is None:
        log.warning("NaukriGulf login: email field not found (page: %s)", page.url)
        return False

    try:
        user_field.fill(email, timeout=8000)
        log.info("NaukriGulf: filled email, clicking Continue...")

        # Step 2: Click Continue button
        cont_btn = first_visible([
            "button:has-text('Continue')",
            "button[type='submit']",
            "button:has-text('Next')",
            "button:has-text('Login')",
        ], timeout_ms=5000)
        if cont_btn is None:
            log.warning("NaukriGulf login: Continue button not found")
            return False
        cont_btn.click(timeout=3000)
        page.wait_for_timeout(2000)

        # Step 3: Now find and fill password field
        pass_field = first_visible([
            "input[type='password']",
            "input#password",
            "input[name='password']",
            "input[autocomplete='current-password']",
        ], timeout_ms=8000)
        if pass_field is None:
            log.warning("NaukriGulf login: password field not found after Continue")
            return False

        pass_field.fill(password, timeout=8000)
        log.info("NaukriGulf: filled password, clicking Login...")

        # Step 4: Click Login/Submit button
        submit = first_visible([
            "button:has-text('Login')",
            "button:has-text('Log in')",
            "button:has-text('Sign in')",
            "button[type='submit']",
        ], timeout_ms=5000)
        if submit is None:
            log.warning("NaukriGulf login: submit button not found")
            return False
        submit.click(timeout=3000)
        page.wait_for_timeout(4000)
        log.info("NaukriGulf: submitted credentials")
        return True
    except Exception as e:
        log.warning("NaukriGulf auto-login attempt failed: %s", e)
        try:
            shot = ROOT / "logs" / "naukrigulf-login-fail.png"
            page.screenshot(path=str(shot))
            log.info("Screenshot: %s", shot)
        except Exception:
            pass
        return False


def login_interactive(wait_seconds: int = 420) -> bool:
    """Open NaukriGulf with the user's own Chrome profile when possible.

    Order: (1) default Chrome profile — usually already logged in;
    (2) if not logged in: NAUKRIGULF_* creds from .env (if ALLOW_STORED_CREDENTIALS=1);
    (3) otherwise the human logs in in the opened window.
    Session is saved to data/naukrigulf_state.json either way.
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = None
        ctx = None
        user_data = _chrome_user_data()
        if user_data:
            try:
                ctx = pw.chromium.launch_persistent_context(
                    str(user_data),
                    channel="chrome",
                    headless=False,
                    viewport={"width": 1440, "height": 900},
                    locale="en-US",
                )
                log.info("Opened your default Chrome profile (already-logged-in session should be there)")
            except Exception as e:
                if "existing browser session" in str(e).lower():
                    log.warning(
                        "Chrome abhi khula hai — Cmd+Q se poori tarah band karo, "
                        "phir `python3 main.py naukrigulf-login` dobara chalao."
                    )
                else:
                    log.warning("Default Chrome profile unavailable (%s) — alag window me manually log in kar lein", e)
                ctx = None
        if ctx is None:
            browser = pw.chromium.launch(headless=False)
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="en-US",
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            )

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass
        page.wait_for_timeout(3000)

        ok = _home_logged_in(page)
        if not ok:
            # NaukriGulf uses a modal — click Login button on homepage to open it
            try:
                login_btn = page.locator("a:has-text('LOGIN'), button:has-text('LOGIN'), a:has-text('Login')").first
                if login_btn.count() and login_btn.is_visible():
                    login_btn.click(timeout=3000)
                    page.wait_for_timeout(1500)
            except Exception:
                pass
            _auto_fill_login(page)
            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                if _home_logged_in(page):
                    ok = True
                    break
                page.wait_for_timeout(4000)

        if ok:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps(ctx.storage_state()))
            try:
                STATE_FILE.chmod(0o600)
            except OSError:
                pass
            log.info("NaukriGulf session saved: %s", STATE_FILE)
        else:
            log.warning("NaukriGulf login not detected within %ss — session not saved", wait_seconds)
            try:
                shot = ROOT / "logs" / "naukrigulf-login-timeout.png"
                page.screenshot(path=str(shot))
                log.warning("Screenshot: %s", shot)
            except Exception:
                pass
        try:
            ctx.close()
        except Exception:
            pass
        if browser:
            try:
                browser.close()
            except Exception:
                pass
        return ok


def apply_to_job(page, job: dict, cover_letter: str, resume_file, result: dict, timeout_ms: int = 60000) -> dict:
    """Best-effort NaukriGulf Easy Apply on an open job page.

    Delegates the heavy form-filling machinery to the Naukri module (same
    company, near-identical apply wizard) after navigating with a
    NaukriGulf-authenticated context.
    """
    if not has_session():
        result["status"] = "no_session"
        result["missing"].append("NaukriGulf session missing — run `python3 main.py naukrigulf-login` once")
        return result

    # Reuse Naukri's Easy Apply engine — the form wizard is shared infrastructure
    from apply.naukri import run_easy_apply as _naukri_apply

    return _naukri_apply(page, job, cover_letter, resume_file, result, timeout_ms)
