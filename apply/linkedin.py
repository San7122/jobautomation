"""LinkedIn session + Easy Apply automation.

One-time setup: `python3 main.py linkedin-login` opens a browser — log in there
yourself (password never touches this codebase, 2FA/CAPTCHA just work). The
session is saved to data/linkedin_state.json and reused for auto-applications.

Easy Apply best-effort: fills contact fields from the profile, attaches the
generated resume, answers common questions from PROFILE["form_answers"], and
clicks through Next → Review → Submit. Any required question it cannot answer
safely aborts that application (status needs_review, job stays queued).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("apply")

STATE_FILE = ROOT / "data" / "linkedin_state.json"
FEED_URL = "https://www.linkedin.com/feed/"
LOGIN_URL = "https://www.linkedin.com/login"

# profile keys -> things worth typing into generic text fields
TEXT_FIELD_MAP = (
    ("first", "first_name"),
    ("last", "last_name"),
    ("full", "name"),
    ("email", "email"),
    ("phone", "phone", "tel"),
    ("mobile", "phone"),
    ("linkedin", "linkedin"),
    ("github", "github"),
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
        "linkedin": PROFILE.get("linkedin", ""),
        "github": PROFILE.get("github", ""),
        "location": "Dubai, United Arab Emirates",  # UAE apps: show target location
    }


def _looks_logged_in(page, retries: int = 4) -> bool:
    """Passive strict check — never navigates away from the current page.

    Logged-out LinkedIn still renders a nav bar, so the discriminator is
    auth-only controls (notification bell, me-menu) plus never being on
    login/checkpoint/authwall. Retries a few times for slow nav rendering.
    """
    for attempt in range(retries):
        url = page.url
        if "/login" in url or "/checkpoint" in url or "authwall" in url:
            return False
        try:
            if page.locator(
                "[aria-label*='notification' i], img.global-nav__me-photo, "
                "button.global-nav__primary-link-me, a.global-nav__me-menu-trigger, "
                "img[alt*='Your profile' i]"
            ).first.count():
                return True
        except Exception:
            pass
        if attempt < retries - 1:
            page.wait_for_timeout(2000)
    return False


def _feed_logged_in(page) -> bool:
    """Navigate to the feed and confirm a real signed-in session."""
    try:
        page.goto(FEED_URL, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(2500)
    except Exception:
        return False
    return _looks_logged_in(page)


def _chrome_user_data() -> Path | None:
    """The user's real Chrome profile (macOS first) — already has her LinkedIn login."""
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
    """Optional sign-in from LINKEDIN_EMAIL / LINKEDIN_PASSWORD in .env.

    Gated behind ALLOW_STORED_CREDENTIALS=1 — passwords sitting in .env are a
    security risk, so this is opt-in only. The default path is interactive
    login (human types the password; it never touches disk).

    Best-effort with several selector generations + consent-dialog handling.
    2FA / CAPTCHA is NOT automated — the browser stays open for the human then.
    """
    import os

    if os.environ.get("ALLOW_STORED_CREDENTIALS", "") != "1":
        return False

    email = os.environ.get("LINKEDIN_EMAIL", "")
    password = os.environ.get("LINKEDIN_PASSWORD", "")
    if not email or not password:
        return False

    def first_visible(selectors: list[str], timeout_ms: int = 8000):
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline:
            for sel in selectors:
                try:
                    locs = page.locator(sel)
                    for i in range(min(locs.count(), 6)):  # LinkedIn renders hidden dupes first
                        loc = locs.nth(i)
                        if loc.is_visible():
                            return loc
                except Exception:
                    continue
            page.wait_for_timeout(400)
        return None

    # dismiss cookie/consent banners that can block the form
    for text in ("Accept all", "Accept", "Reject all", "Only necessary"):
        try:
            btn = page.get_by_role("button", name=text).first
            if btn.count() and btn.is_visible():
                btn.click(timeout=2000)
                page.wait_for_timeout(800)
                break
        except Exception:
            continue

    user_field = first_visible([
        "input#username",
        "input[name='session_key']",
        "input[type='email']",
        "input[aria-label*='Email' i]",
        "input[autocomplete='username']",
    ])
    if user_field is None:
        log.warning("LinkedIn login: email field not found (page: %s)", page.url)
        return False
    try:
        user_field.fill(email, timeout=8000)
        pass_field = first_visible([
            "input#password",
            "input[name='session_password']",
            "input[type='password']",
        ], timeout_ms=8000)
        if pass_field is None:
            log.warning("LinkedIn login: password field not found")
            return False
        pass_field.fill(password, timeout=8000)
        submit = first_visible([
            "button[type='submit']",
            "button[data-litl-control='submit']",
            "button:has-text('Sign in')",
            "button:has-text('Sign In')",
        ], timeout_ms=5000)
        if submit is None:
            log.warning("LinkedIn login: submit button not found")
            return False
        submit.click(timeout=3000)
        page.wait_for_timeout(4000)
        log.info("LinkedIn: submitted configured credentials (from .env)")
        return True
    except Exception as e:
        log.warning("LinkedIn auto-login attempt failed: %s", e)
        try:
            shot = ROOT / "logs" / "linkedin-login-fail.png"
            page.screenshot(path=str(shot))
            log.info("Screenshot: %s", shot)
        except Exception:
            pass
        return False


def login_interactive(wait_seconds: int = 420) -> bool:
    """Open LinkedIn with the user's own Chrome profile when possible.

    Order: (1) default Chrome profile — usually already logged in, nothing to type;
    (2) if not logged in there: LINKEDIN_* creds from .env (if set);
    (3) otherwise the human logs in in the opened window (password never in code).
    Session is saved to data/linkedin_state.json either way.
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
                # typically: Chrome is already running on this profile
                if "existing browser session" in str(e).lower():
                    log.warning(
                        "Chrome abhi khula hai — Cmd+Q se poori tarah band karo, "
                        "phir `python3 main.py linkedin-login` dobara chalao "
                        "(tab aapka default profile milega, pehle se logged-in)."
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
                locale="en-US",
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            )

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.goto(FEED_URL, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            pass
        page.wait_for_timeout(3000)

        ok = _feed_logged_in(page)
        if not ok:
            try:
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            page.wait_for_timeout(1500)
            _auto_fill_login(page)  # .env creds, if configured; else no-op
            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                if _feed_logged_in(page):
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
            log.info("LinkedIn session saved: %s", STATE_FILE)
        else:
            log.warning("LinkedIn login not detected within %ss — session not saved", wait_seconds)
            try:
                shot = ROOT / "logs" / "linkedin-login-timeout.png"
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


# Synonyms so answers match real dropdown option wording (LinkedIn language levels etc.)
_ANSWER_SYNONYMS = {
    "basic": ["elementary", "limited", "beginner", "a1", "a2", "pre-intermediate", "sufficient", "working knowledge"],
    "fluent": ["professional", "full professional", "advanced", "business", "c1", "c2"],
    "yes": ["yes", "y", "true"],
    "no": ["no", "n", "false"],
    "immediate": ["immediate", "asap", "now", "0 days", "instantly"],
    "3": ["3", "3 years", "three"],
}


def _answer_matches(answer: str, option_text: str) -> bool:
    """Check if a form answer matches a dropdown/radio option."""
    a = answer.lower().strip()
    o = option_text.lower().strip()
    if not o or o in ("select an option", "choose", "-- select --"):
        return False
    if a in o or o in a:
        return True
    if a.startswith("yes") and o.startswith("yes"):
        return True
    if a.startswith("no") and o.startswith("no"):
        return True
    # synonym expansion
    for key, syns in _ANSWER_SYNONYMS.items():
        if a == key or a.startswith(key):
            if any(s in o for s in syns):
                return True
    return False


def _modal_root(page):
    """The Easy Apply dialog — current LinkedIn uses a native <dialog> element."""
    for sel in ("dialog[open]", "dialog", "div.jobs-easy-apply-modal", "div[role='dialog']", "div.artdeco-modal"):
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible():
                return loc
        except Exception:
            continue
    return None


def _scope(root):
    """Prefer the Easy Apply iframe when present, else the modal itself."""
    try:
        iframe = root.locator("iframe").first
        if iframe.count():
            return root.frame_locator("iframe")
    except Exception:
        pass
    return root


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
  // nearest short ancestor block = the question container
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
    """Answer free-text/number questions whose text lives in a label, not the input.

    Returns None when all required inputs are answered, else the question text."""
    fields = scope.locator(
        "input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]):not([type=radio]), textarea"
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
                q_lower = (question or "").lower()
                # years/knowledge questions want digits, not Yes/No text
                if kind == "number" or any(
                    w in q_lower for w in ("how many", "years", "knowledge of", "how much", "experience with")
                ):
                    digits = "".join(c for c in answer if c.isdigit())
                    if digits:
                        value = digits
                # respect maxlength (LinkedIn shows x/20 — long answers = "Invalid input")
                try:
                    ml = el.get_attribute("maxlength")
                    if ml and ml.isdigit():
                        value = value[: int(ml)]
                    elif len(value) > 20 and kind != "number":
                        value = value[:20]
                except Exception:
                    pass
                el.fill(value, timeout=2000)
                # LinkedIn uses custom validation (not HTML5 pattern) — detect
                # "Invalid input" after firing events + blur, then retry numerically
                try:
                    err = el.evaluate(
                        """async e => {
                            e.dispatchEvent(new Event('input', {bubbles: true}));
                            e.dispatchEvent(new Event('change', {bubbles: true}));
                            e.blur();
                            await new Promise(r => setTimeout(r, 250));
                            if (e.getAttribute('aria-invalid') === 'true') return 'aria';
                            let p = e.parentElement;
                            for (let i = 0; i < 4 && p; i++) {
                                const t = (p.innerText || '');
                                if (/invalid input|enter a valid|must be/i.test(t)) return 'text';
                                p = p.parentElement;
                            }
                            return '';
                        }"""
                    )
                    if err:
                        digits = "".join(c for c in answer if c.isdigit())
                        retry = digits if digits else ("3" if any(
                            w in q_lower for w in ("knowledge", "years", "experience", "how many", "skills", "strong")
                        ) else "")
                        if retry and retry != value:
                            el.fill(retry, timeout=2000)
                            value = retry
                            log.debug("Invalid input retried: %s -> %r", question[:50], retry)
                except Exception:
                    pass
                # learn what we actually typed (validated on successful submit)
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


def _handle_selects_and_choices(scope) -> str | None:
    """Answer select/radio/checkbox questions via PROFILE['form_answers'].

    Returns None if all answerable, or the question text when stuck."""
    selects = scope.locator("select")
    for i in range(min(selects.count(), 15)):
        sel = selects.nth(i)
        try:
            if not sel.is_visible():
                continue
            # already answered/prefilled (value set via JS, not a selected attr)?
            try:
                if (sel.input_value() or "").strip():
                    continue
            except Exception:
                pass
            label = " ".join(
                str(sel.get_attribute(a) or "") for a in ("aria-label", "name", "id")
            )
            # real visible label text
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
            valid_opts = []
            for j in range(options.count()):
                opt = options.nth(j)
                text = (opt.inner_text() or "").strip()
                val = opt.get_attribute("value") or ""
                if not val or text.lower() in ("select an option", "choose"):
                    continue
                valid_opts.append((opt, text, val))
            picked = False
            for opt, text, val in valid_opts:
                if _answer_matches(answer, text):
                    sel.select_option(value=val, timeout=2000)
                    picked = True
                    break
            if not picked and "proficiency" in label.lower() and valid_opts:
                # Language level dropdowns rarely contain "Basic" verbatim —
                # pick a modest level (2nd valid option) instead of failing
                target = valid_opts[1] if len(valid_opts) > 1 else valid_opts[0]
                sel.select_option(value=target[2], timeout=2000)
                picked = True
                log.info("Select fallback: %s -> '%s'", label[:60], target[1])
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
            # Get labels for this radio group first
            pool = group if (group is not None and group.count()) else None
            candidates = [pool.nth(k) for k in range(pool.count())] if pool else []
            labels = []
            for cand in candidates:
                lab = ""
                try:
                    # LinkedIn puts Yes/No in a sibling div > p, not in label[for]
                    # Structure: <div><input radio></div><div><p>Yes</p></div>
                    sibling_p = cand.locator("xpath=ancestor::div[1]/following-sibling::div[1]//p[1]")
                    if sibling_p.count():
                        lab = sibling_p.inner_text(timeout=500)
                    if not lab:
                        # Try label[for]
                        lid = cand.get_attribute("id")
                        if lid:
                            lab_el = scope.locator(f"label[for='{lid}']").first
                            if lab_el.count():
                                lab = lab_el.inner_text(timeout=500)
                    if not lab:
                        lab = cand.get_attribute("aria-label") or ""
                        # If aria-label is the question, not the option, clear it
                        if "?" in lab:
                            lab = ""
                except Exception:
                    pass
                labels.append((lab or "").strip())
            
            log.debug("Radio group: name=%s labels=%s", name, labels)
            
            # Get question text from multiple sources
            ctx_text = ""
            for xpath in [
                "xpath=ancestor::div[5]",
                "xpath=ancestor::div[4]",
                "xpath=ancestor::div[3]",
                "xpath=ancestor::div[2]",
                "xpath=ancestor::fieldset[1]/preceding-sibling::*[1]",
                "xpath=ancestor::fieldset[1]",
            ]:
                try:
                    txt = r.locator(xpath).inner_text(timeout=500)[:300]
                    # Must contain a question mark to be a real question
                    if txt and "?" in txt:
                        ctx_text = txt
                        break
                except Exception:
                    continue
            if not ctx_text:
                ctx_text = str(r.get_attribute("aria-label") or "")
            
            # Extract question line (remove Yes/No/* from options)
            lines = [l.strip() for l in ctx_text.split("\n") if l.strip() and l.strip() not in ("Yes", "No", "*")]
            question_line = lines[0] if lines else ctx_text
            
            answer = _answer_for(question_line)
            log.debug("Radio question: '%s' answer: '%s'", question_line[:80], answer)
            
            # If no answer but options are Yes/No, default to Yes
            if not answer:
                has_yes = any("yes" in l.lower() for l in labels)
                has_no = any("no" in l.lower() for l in labels)
                log.debug("Radio fallback: has_yes=%s has_no=%s labels=%s", has_yes, has_no, labels)
                if has_yes and has_no:
                    # Sensitive questions need care
                    q_lower = question_line.lower()
                    if any(w in q_lower for w in ["disability", "veteran", "ethnicity", "race", "gender"]):
                        return question_line[:120]
                    answer = "Yes"
                else:
                    return question_line[:120]
            
            # Find matching option
            target = None
            for cand, lab in zip(candidates, labels):
                lab_lower = lab.lower().strip()
                if not lab_lower:
                    continue
                if _answer_matches(answer, lab_lower):
                    target = cand
                    break
            if target is None:
                log.debug("Radio target not found: answer=%s labels=%s", answer, labels)
                return question_line[:120]
            try:
                # LinkedIn custom radios: click the sibling div containing Yes/No text
                target_idx = candidates.index(target)
                sibling_div = target.locator(
                    "xpath=ancestor::div[1]/following-sibling::div[1]"
                )
                if sibling_div.count() and sibling_div.is_visible():
                    sibling_div.click(timeout=2000)
                else:
                    target.click(timeout=2000)
                log.debug("Radio clicked: %s", labels[target_idx] if target_idx < len(labels) else "?")
            except Exception as e:
                log.debug("Radio click failed: %s — trying JS click", e)
                try:
                    target.evaluate("e => e.click()")
                except Exception:
                    pass
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
            log.info("Easy Apply: resume attached")
    except Exception as e:
        log.warning("Easy Apply: resume attach failed: %s", e)


def _click_first(scope, labels: list[str]) -> bool:
    for text in labels:
        for sel in (
            f"button[aria-label='{text}']",
            f"button:has-text('{text}')",
            f"button[type=submit]:has-text('{text}')",
        ):
            try:
                locs = scope.locator(sel)
                for i in range(min(locs.count(), 6)):  # hidden duplicates render first
                    btn = locs.nth(i)
                    if btn.is_visible() and btn.is_enabled():
                        btn.click(timeout=2500)
                        return True
            except Exception:
                continue
    return False


def _shot(page, job: dict, tag: str) -> None:
    try:
        path = ROOT / "logs" / f"easyapply-{tag}-{job.get('job_id', 'x')}.png"
        page.screenshot(path=str(path))
        log.info("Easy Apply screenshot (%s): %s", tag, path)
    except Exception:
        pass


def _external_apply_url(page) -> str | None:
    """Resolve the 'Apply on company website' target (LinkedIn wraps it in safety/go)."""
    from urllib.parse import parse_qs, urlparse

    for sel in (
        "a[aria-label*='Apply on company' i]",
        "a[aria-label*='Apply' i]",
        "a:has-text('Apply on company site')",
        "a:has-text('Apply on company website')",
    ):
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 6)):
                el = loc.nth(i)
                if not el.is_visible():
                    continue
                href = (el.get_attribute("href") or "").strip()
                if not href.startswith(("http://", "https://")):
                    continue
                if "safety/go" in href:
                    real = (parse_qs(urlparse(href).query).get("url") or [""])[0]
                    if real:
                        return real
                elif "linkedin.com" not in href:
                    return href
        except Exception:
            continue
    return None


def run_easy_apply(page, job: dict, cover_letter: str, resume_file, result: dict, timeout_ms: int) -> dict:
    """Drive LinkedIn Easy Apply for an open job page. Mutates + returns result."""
    if not has_session():
        result["status"] = "no_session"
        result["missing"].append("LinkedIn session missing — run `python3 main.py linkedin-login` once")
        return result

    from apply.answercache import confirm_session, reset_session

    reset_session()

    # LinkedIn renames classes often — match on aria-label/text/href, old class as bonus
    btn = None
    deadline = time.time() + 8
    while time.time() < deadline:
        for sel in (
            "button[aria-label*='Easy Apply' i]",
            "a[aria-label*='Easy Apply' i]",
            "a[href*='/easyApply/']",
            "button.jobs-apply-button",
            "a.jobs-apply-button",
        ):
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    btn = loc
                    break
            except Exception:
                continue
        if btn is not None:
            break
        page.wait_for_timeout(500)
    if btn is None:
        result["status"] = "no_easy_apply"
        result["missing"].append("no Easy Apply button (external apply link — handled by ATS fill)")
        ext = _external_apply_url(page)
        if ext:
            result["external_url"] = ext
            log.info("Easy Apply: external apply target %s", ext)
        return result

    btn.click(timeout=5000)
    page.wait_for_timeout(2000)

    for step in range(25):
        root = _modal_root(page)
        if root is None:
            # maybe already submitted / modal closed
            body = ""
            try:
                body = page.locator("body").inner_text(timeout=3000)
            except Exception:
                pass
            if "application sent" in body.lower() or "application submitted" in body.lower():
                result["status"] = "submitted"
                confirm_session()
                return result
            result["status"] = "needs_review"
            result["missing"].append("Easy Apply dialog disappeared mid-flow")
            return result
        scope = _scope(root)

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

        stuck = _handle_selects_and_choices(scope)
        if stuck:
            result["status"] = "needs_review"
            result["missing"].append(f"can't safely answer: {stuck[:90]}")
            _shot(page, job, f"stuck-step{step + 1}")
            return result

        if _click_first(scope, ["Submit application", "Submit Application", "Send application", "Submit"]):
            page.wait_for_timeout(3500)
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
                    "application status",
                )
            )
            if confirmed:
                result["status"] = "submitted"
                confirm_session()
            else:
                result["status"] = "submitted_unconfirmed"
            return result

        if _click_first(scope, ["Review your application", "Review"]):
            page.wait_for_timeout(1200)
            continue
        if _click_first(scope, ["Next", "Continue", "Proceed"]):
            page.wait_for_timeout(1200)
            continue

        result["status"] = "needs_review"
        result["missing"].append(f"stuck at Easy Apply step {step + 1} — no known button")
        _shot(page, job, f"stuck-step{step + 1}")
        return result

    result["status"] = "needs_review"
    result["missing"].append("Easy Apply did not finish within step budget")
    _shot(page, job, "step-budget")
    return result
