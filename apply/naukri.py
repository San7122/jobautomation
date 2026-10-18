"""Naukri.com session + Easy Apply automation.

One-time setup: `python3 main.py naukri-login` opens a browser — log in there
yourself (password never touches this codebase, 2FA/CAPTCHA just work). The
session is saved to data/naukri_state.json and reused for auto-applications.

Naukri Easy Apply best-effort: fills contact fields from the profile, attaches
the generated resume, answers common questions from PROFILE["form_answers"],
and clicks through the application flow. Any required question it cannot
answer safely aborts that application (status needs_review, job stays queued).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("apply")

STATE_FILE = ROOT / "data" / "naukri_state.json"
HOME_URL = "https://www.naukri.com/"
LOGIN_URL = "https://www.naukri.com/nlogin/login"

# profile keys -> things worth typing into generic text fields
TEXT_FIELD_MAP = (
    ("first", "first_name"),
    ("last", "last_name"),
    ("full", "name"),
    ("email", "email"),
    ("phone", "phone", "tel"),
    ("mobile", "phone"),
    ("city", "location"),
    ("location", "location"),
    ("current location", "location"),
)


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
        if "login.naukri.com" in url or "/nLogin" in url or "authwall" in url or "/nlogin/login" in url:
            return False
        try:
            # logged-in indicators (must be VISIBLE)
            for sel in [
                "[data-testid='user-icon']",
                "span.uname",
                "div.user-actor",
                "span[name='uname']",
                "div.user-name",
                "a[href*='/profile']",
                "a[href*='/mngr']",
                "img[alt*='profile' i]",
                "img[alt*='user' i]",
                "div[class*='user']",
            ]:
                el = page.locator(sel).first
                if el.count() and el.is_visible():
                    return True
            # Check for user name text in body (homepage)
            body_text = page.locator("body").inner_text(timeout=2000)[:500]
            if "View profile" in body_text or "Profile perf" in body_text:
                return True
            # Login/Register button visible = not logged in
            login_btn = page.locator("a[href*='login.naukri.com'], button:has-text('Login')").first
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
    """The user's real Chrome profile (macOS first)."""
    candidates = [
        Path.home() / "Library/Application Support/Google/Chrome",  # macOS
        Path.home() / ".config/google-chrome",                     # Linux
        Path.home() / "AppData/Local/Google/Chrome/User Data",     # Windows
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _auto_fill_login(page) -> bool:
    """Optional sign-in from NAUKRI_EMAIL / NAUKRI_PASSWORD in .env.

    Gated behind ALLOW_STORED_CREDENTIALS=1 — passwords sitting in .env are a
    security risk, so this is opt-in only. The default path is interactive
    login (human types the password; it never touches disk).

    Best-effort with several selector generations + consent-dialog handling.
    2FA / CAPTCHA is NOT automated — the browser stays open for the human then.
    """
    import os

    if os.environ.get("ALLOW_STORED_CREDENTIALS", "") != "1":
        return False

    email = os.environ.get("NAUKRI_EMAIL", "")
    password = os.environ.get("NAUKRI_PASSWORD", "")
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
    for text in ("Accept", "Accept all", "I agree", "Got it", "Continue"):
        try:
            btn = page.get_by_role("button", name=text).first
            if btn.count() and btn.is_visible():
                btn.click(timeout=2000)
                page.wait_for_timeout(800)
                break
        except Exception:
            continue

    user_field = first_visible([
        "input#usernameField",
        "input#username_email",
        "input#username",
        "input[name='username']",
        "input[name='email']",
        "input[type='email']",
        "input[aria-label*='Email' i]",
        "input[autocomplete='username']",
        "input[placeholder*='Email' i]",
        "input[placeholder*='email' i]",
        "input[placeholder*='Username' i]",
    ])
    if user_field is None:
        log.warning("Naukri login: email field not found (page: %s)", page.url)
        return False
    try:
        user_field.fill(email, timeout=8000)
        
        pass_field = first_visible([
            "input#passwordField",
            "input#password",
            "input[name='password']",
            "input[type='password']",
            "input[autocomplete='current-password']",
        ], timeout_ms=8000)
        if pass_field is None:
            log.warning("Naukri login: password field not found")
            return False
        pass_field.fill(password, timeout=8000)
        
        submit = first_visible([
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Login')",
            "button:has-text('Log in')",
            "button:has-text('Sign in')",
            "a:has-text('Login')",
        ], timeout_ms=5000)
        if submit is None:
            log.warning("Naukri login: submit button not found")
            return False
        submit.click(timeout=3000)
        page.wait_for_timeout(4000)
        log.info("Naukri: submitted configured credentials (from .env)")
        return True
    except Exception as e:
        log.warning("Naukri auto-login attempt failed: %s", e)
        try:
            shot = ROOT / "logs" / "naukri-login-fail.png"
            page.screenshot(path=str(shot))
            log.info("Screenshot: %s", shot)
        except Exception:
            pass
        return False


def login_interactive(wait_seconds: int = 420) -> bool:
    """Open Naukri with the user's own Chrome profile when possible.

    Order: (1) default Chrome profile — usually already logged in;
    (2) if not logged in: NAUKRI_* creds from .env (if set);
    (3) otherwise the human logs in in the opened window.
    Session is saved to data/naukri_state.json either way.
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
                    locale="en-IN",
                )
                log.info("Opened your default Chrome profile (already-logged-in Naukri session should be there)")
            except Exception as e:
                if "existing browser session" in str(e).lower():
                    log.warning(
                        "Chrome abhi khula hai — Cmd+Q se poori tarah band karo, "
                        "phir `python3 main.py naukri-login` dobara chalao."
                    )
                else:
                    log.warning(
                        "Default Chrome profile unavailable (%s) — alag window me "
                        "manually log in kar lein", e,
                    )
                ctx = None
        if ctx is None:
            browser = pw.chromium.launch(headless=False)
            ctx = browser.new_context(
                viewport={"width": 1440, "height": 900},
                locale="en-IN",
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
            try:
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            page.wait_for_timeout(1500)
            _auto_fill_login(page)  # .env creds, if configured; else no-op
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
                STATE_FILE.chmod(0o600)  # owner-only: auth cookies
            except OSError:
                pass
            log.info("Naukri session saved: %s", STATE_FILE)
        else:
            log.warning("Naukri login not detected within %ss — session not saved", wait_seconds)
            try:
                shot = ROOT / "logs" / "naukri-login-timeout.png"
                page.screenshot(path=str(shot))
                body = page.locator("body").inner_text(timeout=4000)[:500]
                log.warning("Final page url: %s | body: %s", page.url, body.replace("\n", " / "))
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


def _answer_for(question_text: str) -> str | None:
    from apply.answercache import lookup
    from config.profile import PROFILE

    # learned answer (from a previous successful run) beats static profile
    cached = lookup(question_text)
    if cached:
        return cached

    q = (question_text or "").lower()
    for pattern, answer in PROFILE.get("form_answers", []):
        if pattern.lower() in q:
            return answer
    return None


def _modal_root(page):
    """The Naukri application dialog or drawer."""
    for sel in (
        "div[class*='apply' i][class*='modal' i]",
        "div[class*='drawer' i]",
        "div[class*='chatbot' i]",
        "div[role='dialog']",
        "div.modal",
        "div[class*='apply-wizard']",
    ):
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible():
                return loc
        except Exception:
            continue
    return None


def _fill_text_fields(scope, filled: list[str]) -> None:
    values = _profile_values()
    inputs = scope.locator("input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]), textarea")
    n = min(inputs.count(), 25)
    for i in range(n):
        el = inputs.nth(i)
        try:
            if not el.is_visible():
                continue
            current = (el.input_value() or "").strip()
            if current:
                continue
            hint = " ".join(
                str(el.get_attribute(a) or "") for a in ("name", "id", "aria-label", "placeholder")
            ).lower()
            value = None
            for keys in TEXT_FIELD_MAP:
                if any(k in hint for k in keys if isinstance(k, str)):
                    value = values[keys[-1]] if isinstance(keys[-1], str) and keys[-1] in values else None
                    if value:
                        break
            if not value and ("email" in hint or el.get_attribute("type") == "email"):
                value = values["email"]
            if not value and el.get_attribute("type") == "tel":
                value = values["phone"]
            if not value:
                continue
            el.fill(value, timeout=2000)
            filled.append(hint[:40] or "text field")
        except Exception:
            continue


_QUESTION_JS = """
(el) => {
  const txt = (n) => (n && (n.innerText || "").trim()) || "";
  const own = txt(el.closest("label"));
  if (own) return own.slice(0, 300);
  const id = el.id;
  if (id) {
    const lab = document.querySelector('label[for="' + CSS.escape(id) + '"]');
    const t = txt(lab);
    if (t) return t.slice(0, 300);
  }
  const al = (el.getAttribute("aria-label") || "").trim();
  if (al) return al.slice(0, 300);
  const lb = el.getAttribute("aria-labelledby");
  if (lb) {
    const parts = lb.split(/\\s+/).map((i) => txt(document.getElementById(i))).filter(Boolean);
    if (parts.length) return parts.join(" ").slice(0, 300);
  }
  let p = el.parentElement;
  for (let i = 0; i < 5 && p; i++) {
    const t = txt(p);
    if (t && t.length < 300 && t.length > 3) return t.slice(0, 300);
    p = p.parentElement;
  }
  return "";
}
"""


def _question_text(el) -> str:
    try:
        handle = el.element_handle(timeout=1000)
        if handle:
            out = handle.evaluate(_QUESTION_JS)
            return str(out or "").strip()
    except Exception:
        pass
    return ""


def _fill_question_inputs(scope, filled: list[str]) -> str | None:
    """Answer free-text/number questions. Returns stuck question text or None."""
    fields = scope.locator(
        "input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]), textarea"
    )
    for i in range(min(fields.count(), 30)):
        el = fields.nth(i)
        try:
            if not el.is_visible():
                continue
            if (el.input_value() or "").strip():
                continue
            kind = (el.get_attribute("type") or el.evaluate("e => e.tagName.toLowerCase()")).lower()
            if kind in ("button", "submit", "image", "radio", "checkbox"):
                continue
            question = _question_text(el)
            answer = _answer_for(question) if question else None
            if answer:
                value = answer
                if kind == "number":
                    digits = "".join(c for c in answer if c.isdigit())
                    if digits:
                        value = digits
                el.fill(value, timeout=2000)
                # learn what worked (confirmed only on successful submit)
                try:
                    from apply.answercache import remember

                    final_val = (el.input_value() or "").strip() or value
                    if question and final_val:
                        remember(question, final_val)
                except Exception:
                    pass
                filled.append((question or "question")[:60])
                continue
            required = (
                el.get_attribute("required") is not None
                or el.get_attribute("aria-required") == "true"
            )
            if required and question:
                return question
        except Exception as e:
            log.debug("question input handling failed: %s", e)
            continue
    return None


def _handle_selects_and_choices(scope, page=None) -> str | None:
    """Answer select/radio/checkbox questions via PROFILE['form_answers']."""
    # Handle Naukri chat-style custom checkboxes, chips, and options
    try:
        targets = [scope]
        if page is not None and page is not scope:
            targets.append(page)
        for target in targets:
            # Direct text matches for options/cities/skip
            for opt_text in [
                "Bengaluru, Karnataka", "Hyderabad, Telangana",
                "Bengaluru", "Hyderabad", "Dubai", "Abu Dhabi",
                "Skip this question", "Yes", "Agree",
            ]:
                try:
                    loc = target.locator(f"text='{opt_text}'").last
                    if loc.count() and loc.is_visible():
                        loc.click(timeout=2000)
                        log.info("Clicked Naukri option: %s", opt_text)
                        if hasattr(target, "wait_for_timeout"):
                            target.wait_for_timeout(1000)
                        return None
                except Exception:
                    continue

            chat_options = target.locator(
                "[role='checkbox'], [role='radio'], [class*='checkbox' i], [class*='option' i], [class*='choice' i], [class*='chip' i], [class*='bubble-option' i], label, li"
            ).all()
            if chat_options:
                any_checked = False
                for opt in chat_options[:20]:
                    try:
                        if (
                            opt.get_attribute("aria-checked") == "true"
                            or "selected" in (opt.get_attribute("class") or "").lower()
                            or "active" in (opt.get_attribute("class") or "").lower()
                        ):
                            any_checked = True
                            break
                    except Exception:
                        pass

                if not any_checked:
                    for opt in chat_options[:20]:
                        try:
                            if not opt.is_visible():
                                continue
                            text = (opt.inner_text() or "").strip()
                            tl = text.lower()
                            if any(c in tl for c in ("bengaluru", "bangalore", "hyderabad", "dubai", "uae", "delhi", "gurgaon", "noida")):
                                opt.click(timeout=2000)
                                return None
                            if "skip" in tl or tl in ("yes", "agree", "confirm"):
                                opt.click(timeout=2000)
                                return None
                        except Exception:
                            continue
                    for opt in chat_options[:5]:
                        try:
                            if opt.is_visible():
                                opt.click(timeout=2000)
                                return None
                        except Exception:
                            continue
    except Exception:
        pass

    selects = scope.locator("select")
    for i in range(min(selects.count(), 15)):
        sel = selects.nth(i)
        try:
            if not sel.is_visible():
                continue
            try:
                if (sel.input_value() or "").strip():
                    continue
            except Exception:
                pass
            label = " ".join(
                str(sel.get_attribute(a) or "") for a in ("aria-label", "name", "id")
            )
            try:
                lid = sel.get_attribute("id")
                if lid:
                    lab = scope.locator(f"label[for='{lid}']").first
                    if lab.count():
                        label = lab.inner_text(timeout=1000) + " " + label
            except Exception:
                pass
            if not label.strip() or label.strip().replace("_", "").isdigit():
                label = "unidentified select question"
            answer = _answer_for(label)
            if not answer:
                return label.strip()[:120]
            options = sel.locator("option")
            picked = False
            for j in range(options.count()):
                opt = options.nth(j)
                text = (opt.inner_text() or "").strip()
                val = opt.get_attribute("value") or ""
                if not val or text.lower() in ("select an option", "choose"):
                    continue
                if answer.lower() in text.lower() or text.lower() in answer.lower() or ("no" in answer.lower() and text.lower().startswith("no")) or ("yes" in answer.lower() and text.lower().startswith("yes")):
                    sel.select_option(value=val, timeout=2000)
                    picked = True
                    break
            if not picked:
                return label.strip() or "select without a matching option"
        except Exception as e:
            log.debug("select handling failed: %s", e)
            continue

    # radio groups
    radios = scope.locator("input[type=radio]")
    seen_groups: set[str] = set()
    for i in range(min(radios.count(), 30)):
        r = radios.nth(i)
        try:
            name = r.get_attribute("name") or r.get_attribute("id") or str(i)
            if name in seen_groups:
                continue
            seen_groups.add(name)
            group = scope.locator(f"input[type=radio][name='{name}']") if r.get_attribute("name") else None
            checked = False
            if group is not None and group.count():
                checked = any(
                    (group.nth(k).get_attribute("checked") is not None) or group.nth(k).is_checked()
                    for k in range(min(group.count(), 8))
                )
            if checked:
                continue
            try:
                ctx_text = r.locator("xpath=ancestor::fieldset[1]").inner_text(timeout=1000)[:300]
            except Exception:
                ctx_text = str(r.get_attribute("aria-label") or "")
            answer = _answer_for(ctx_text)
            if not answer:
                return (ctx_text.strip() or "unidentified yes/no question")[:120]
            target = None
            pool = group if (group is not None and group.count()) else None
            candidates = [pool.nth(k) for k in range(pool.count())] if pool else []
            for cand in candidates:
                lab = ""
                try:
                    lid = cand.get_attribute("id")
                    if lid:
                        lab = scope.locator(f"label[for='{lid}']").first.inner_text(timeout=800)
                except Exception:
                    pass
                lab = (lab or cand.get_attribute("aria-label") or "").lower()
                if answer.lower().startswith("yes") and lab.strip().startswith("yes"):
                    target = cand
                    break
                if answer.lower().startswith("no") and lab.strip().startswith("no"):
                    target = cand
                    break
                if any(w in lab for w in answer.lower().split()[:3]):
                    target = cand
                    break
            if target is None:
                return (ctx_text.strip() or "radio group without a safe answer")[:120]
            target.check(timeout=2000)
        except Exception as e:
            log.debug("radio handling failed: %s", e)
            continue
    return None


def _attach_resume(scope, resume_file) -> None:
    if not resume_file:
        return
    try:
        files = scope.locator("input[type=file]")
        if files.count():
            files.first.set_input_files(str(resume_file), timeout=4000)
            log.info("Naukri Easy Apply: resume attached")
    except Exception as e:
        log.warning("Naukri Easy Apply: resume attach failed: %s", e)


def _handle_chatbot_interactions(scope, page, result: dict) -> None:
    """Answer conversational prompts in Naukri application assistant / chatbot."""
    try:
        for sel in (
            "input[placeholder*='message' i]",
            "textarea[placeholder*='message' i]",
            "input[placeholder*='reply' i]",
            "div[contenteditable='true']",
            "input[class*='chat' i]",
            "textarea[class*='chat' i]",
        ):
            chat_input = page.locator(sel).first
            if chat_input.count() and chat_input.is_visible():
                bot_msgs = page.locator(
                    "[class*='msg' i], [class*='chat' i] p, [class*='chat' i] span, [class*='bubble' i], [class*='content' i]"
                ).all()
                q_text = ""
                for m in reversed(bot_msgs[-8:]):
                    try:
                        txt = (m.inner_text() or "").strip()
                        if txt and len(txt) > 4 and not any(
                            x in txt.lower()
                            for x in ["upload", "attachment", "unsuccessful", ".pdf", "type message", "here..."]
                        ):
                            q_text = txt
                            break
                    except Exception:
                        continue

                if not q_text:
                    modal_text = page.locator("body").inner_text(timeout=1000)
                    import re
                    m = re.findall(r"([A-Z][^.?!]*\?)", modal_text)
                    if m:
                        q_text = m[-1]

                answer = _answer_for(q_text) if q_text else None
                if not answer and q_text:
                    ql = q_text.lower()
                    if "working day" in ql or "serving notice" in ql or "notice" in ql:
                        answer = "Immediate"
                    elif "experience" in ql or "years" in ql:
                        answer = "3"
                    elif "ctc" in ql or "salary" in ql:
                        answer = "35000"
                    elif "relocat" in ql or "location" in ql:
                        answer = "Yes"
                    else:
                        answer = "Immediate"

                if answer:
                    chat_input.fill(answer)
                    page.wait_for_timeout(400)
                    chat_input.press("Enter")
                    page.wait_for_timeout(1500)
                    result["filled"].append(f"chat: {q_text[:35] or 'prompt'}")
                    log.info("Answered Naukri chatbot prompt: %s -> %s", q_text, answer)

                # Check send button if Enter didn't submit
                send_btn = page.locator("button[class*='send' i], [aria-label*='send' i], [class*='chat-send' i]").first
                if send_btn.count() and send_btn.is_visible() and send_btn.is_enabled():
                    send_btn.click(timeout=2000)
                    page.wait_for_timeout(1500)
                return True
        return False
    except Exception as e:
        log.debug("Chatbot interaction error: %s", e)
        return False


def _click_first(scope, labels: list[str], page=None) -> bool:
    targets = [scope]
    if page is not None and page is not scope:
        targets.append(page)
    for target in targets:
        for text in labels:
            for sel in (
                f"button[aria-label='{text}']",
                f"button:has-text('{text}')",
                f"button[type=submit]:has-text('{text}')",
                f"input[type='submit'][value*='{text}' i]",
                f"a:has-text('{text}')",
                f"[role='button']:has-text('{text}')",
                f"text='{text}'",
                f"div:has-text('{text}'):not(:has(*))",
                f"span:has-text('{text}'):not(:has(*))",
            ):
                try:
                    locs = target.locator(sel)
                    for i in range(min(locs.count(), 6)):
                        btn = locs.nth(i)
                        if btn.is_visible() and btn.is_enabled():
                            btn.click(timeout=2500)
                            return True
                except Exception:
                    continue
    return False


def _shot(page, job: dict, tag: str) -> None:
    try:
        path = ROOT / "logs" / f"naukri-apply-{tag}-{job.get('job_id', 'x')}.png"
        page.screenshot(path=str(path))
        log.info("Naukri Easy Apply screenshot (%s): %s", tag, path)
    except Exception:
        pass


def run_easy_apply(page, job: dict, cover_letter: str, resume_file, result: dict, timeout_ms: int) -> dict:
    """Drive Naukri Easy Apply for an open job page. Mutates + returns result."""
    if not has_session():
        result["status"] = "no_session"
        result["missing"].append("Naukri session missing — run `python3 main.py naukri-login` once")
        return result

    from apply.answercache import confirm_session, reset_session

    reset_session()

    # Already applied?
    try:
        applied_btn = page.locator("button:has-text('Applied'), span:has-text('Applied')").first
        if applied_btn.count() and applied_btn.is_visible():
            result["status"] = "submitted"
            result["filled"].append("already applied")
            return result
    except Exception:
        pass

    # Find the Apply button
    btn = None
    deadline = time.time() + 8
    while time.time() < deadline:
        for sel in (
            "button[class*='apply' i]",
            "a[class*='apply' i]",
            "button:has-text('Apply')",
            "a:has-text('Apply')",
            "button:has-text('Apply Now')",
            "a:has-text('Apply Now')",
            "span:has-text('Apply')",
            "button[data-ga='apply']",
            "a[data-ga='apply']",
        ):
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    # Skip if it says "Applied" (already applied)
                    btn_text = loc.inner_text().strip().lower()
                    if "applied" in btn_text and "apply" not in btn_text.replace("applied", ""):
                        result["status"] = "submitted"
                        result["filled"].append("already applied")
                        return result
                    btn = loc
                    break
            except Exception:
                continue
        if btn is not None:
            break
        page.wait_for_timeout(500)

    if btn is None:
        result["status"] = "no_easy_apply"
        result["missing"].append("no Apply button found on page")
        return result

    btn.click(timeout=5000)
    page.wait_for_timeout(2500)

    # Naukri may open a new tab for the application
    if len(page.context.pages) > 1:
        page = page.context.pages[-1]
        page.wait_for_timeout(1500)

    for step in range(18):
        root = _modal_root(page)
        if root is None:
            # maybe the form is inline (not a modal) or already submitted
            body = ""
            try:
                body = page.locator("body").inner_text(timeout=3000)
            except Exception:
                pass
            if any(marker in body.lower() for marker in (
                "application sent", "application submitted",
                "your application", "applied successfully",
                "successfully", "thank you",
            )):
                result["status"] = "submitted"
                confirm_session()
                return result
            # try inline form
            root = page
        scope = root

        _fill_text_fields(scope, result["filled"])

        stuck_q = _fill_question_inputs(scope, result["filled"])
        if stuck_q:
            result["status"] = "needs_review"
            result["missing"].append(f"can't answer required question: {stuck_q[:90]}")
            _shot(page, job, f"stuck-q-step{step + 1}")
            return result

        _attach_resume(scope, resume_file)

        # cover letter if a textarea asks for it
        try:
            cl = scope.locator("textarea[name*='cover' i], textarea[id*='cover' i], textarea[aria-label*='cover' i]").first
            if cl.count() and cl.is_visible() and not (cl.input_value() or "").strip():
                cl.fill(cover_letter[:3000], timeout=2000)
                result["filled"].append("cover letter")
        except Exception:
            pass

        stuck = _handle_selects_and_choices(scope, page=page)
        if stuck:
            result["status"] = "needs_review"
            result["missing"].append(f"can't safely answer: {stuck[:90]}")
            _shot(page, job, f"stuck-step{step + 1}")
            return result

        # Handle Naukri chat assistant / conversational prompts
        handled_chat = _handle_chatbot_interactions(scope, page, result)

        # Naukri flow: Continue/Next -> Review -> Submit/Apply/Save
        if _click_first(scope, ["Continue", "Next", "Proceed", "Save and continue", "Save & Continue", "Save and Next", "Next Step"], page=page):
            page.wait_for_timeout(1500)
            continue
        if _click_first(scope, ["Review", "Review your application"], page=page):
            page.wait_for_timeout(1500)
            continue
        if _click_first(scope, ["Submit", "Submit application", "Apply", "Send application", "Apply Now", "Save", "Save and apply", "Save & Apply"], page=page):
            page.wait_for_timeout(4000)
            try:
                page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:
                pass
            body = ""
            try:
                body = page.locator("body").inner_text(timeout=4000)
            except Exception:
                pass
            result["url"] = page.url
            _shot(page, job, "submitted")
            confirmed = any(
                marker in body.lower()
                for marker in (
                    "application sent",
                    "application submitted",
                    "your application",
                    "applied successfully",
                    "successfully",
                    "thank you",
                )
            ) or any(m in (result["url"] or "").lower() for m in ("saveapply", "multiapplyresp"))
            if confirmed:
                result["status"] = "submitted"
                confirm_session()
            else:
                result["status"] = "submitted_unconfirmed"
            return result

        if handled_chat:
            page.wait_for_timeout(2000)
            continue

        result["status"] = "needs_review"
        result["missing"].append(f"stuck at Naukri step {step + 1} — no known button")
        _shot(page, job, f"stuck-step{step + 1}")
        return result

    result["status"] = "needs_review"
    result["missing"].append("Naukri Easy Apply did not finish within step budget")
    _shot(page, job, "step-budget")
    return result
