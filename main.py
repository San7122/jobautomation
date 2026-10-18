"""UAE Job Copilot — daily search, AI scoring, cover letters, digest.

Usage:
    python3 main.py run        # full daily cycle: search -> score -> prep -> digest
    python3 main.py search     # just search + score (+ description fetch)
    python3 main.py prep       # generate cover letters for top matches
    python3 main.py digest     # build + print today's digest (also delivers it)
    python3 main.py stats      # show tracker stats
    python3 main.py ready      # list applications ready to submit
    python3 main.py applied <job_id>   # mark an application as applied
    python3 main.py apply <job_id>     # apply: autofill browser / --mode email / --mode packet
    python3 main.py autoapply          # submit ready applications automatically
    python3 main.py linkedin-login     # one-time LinkedIn login (enables Easy Apply)
    python3 main.py serve      # launch the review UI (http://127.0.0.1:8787)

Run `python3 main.py <command> --help` for flags (queries, sources, thresholds).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# ensure project root on path
ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from config.settings import (  # noqa: E402
    auto_apply_enabled,
    auto_apply_headless,
    get_auto_apply_limit,
    get_auto_apply_min_score,
    get_location,
    get_min_score,
    get_prep_limit,
    get_queries,
    get_sources,
    load_env,
)
from config.validation import print_config_report, validate_config  # noqa: E402

load_env()

from ai.assistant import score_job, tailor_cover_letter, tailor_resume_bullets  # noqa: E402
from notify.channels import send_digest  # noqa: E402
from notify.digest import build_digest, save_digest  # noqa: E402
from scraper.jobs import enrich_descriptions, search_all, deduplicate_jobs  # noqa: E402
from tracker.db import (  # noqa: E402
    _db,
    get_applied_recent,
    get_job,
    get_pending_ready,
    get_stats,
    get_top_jobs,
    init_db,
    job_exists,
    job_ids_with_description,
    mark_applied,
    save_application,
    set_job_description,
    upsert_job,
)

(ROOT / "logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(ROOT / "logs" / "copilot.log"),
    ],
)
log = logging.getLogger("main")


def _merge_pw_jobs(
    jobs: list, run: bool, queries: list[str], location: str, sources: list[str] | None = None,
) -> list:
    """Merge Playwright results: fresh run with --playwright, else cached file.

    sources: filter to the requested platforms (Playwright scrapers and the
    cache are both per-source — `--sources bayt` must not spin up Indeed).
    """
    try:
        from scraper.pw_jobs import load_pw_jobs, run_all
    except Exception as e:
        log.warning("Playwright module unavailable: %s", e)
        return jobs
    try:
        jobs = jobs + (
            run_all(queries, location, sources=sources) if run else load_pw_jobs(sources=sources)
        )
    except Exception as e:
        log.error("Playwright pass failed: %s", e)
    return jobs


def cmd_search(
    queries: list[str] | None = None,
    location: str | None = None,
    sources: list[str] | None = None,
    desc_limit: int = 50,
    playwright: bool = False,
):
    init_db()
    queries = queries or get_queries()
    location = location or get_location()
    sources = sources or get_sources()
    log.info("Searching %d queries | %s | sources: %s", len(queries), location, ",".join(sources))

    jobs = search_all(queries, location, sources=sources)
    jobs = _merge_pw_jobs(jobs, run=playwright, queries=queries, location=location, sources=sources)

    # Cross-source dedup: same job on LinkedIn + Indeed + Bayt should count once.
    jobs = deduplicate_jobs(jobs)

    if desc_limit > 0:
        fetched = enrich_descriptions(
            jobs,
            max_fetches=desc_limit,
            skip_ids=job_ids_with_description(),
            use_pw=playwright,  # browser fallback for pages that 403/429 plain requests
        )
        log.info("Fetched %d job descriptions", fetched)

    new = must = 0
    scored = skipped = 0
    for job in jobs:
        is_new = not job_exists(job.job_id)
        # Skip re-scoring if job already has a score (unless it just got a description)
        existing = get_job(job.job_id) if not is_new else None
        already_scored = existing and existing.get("score", 0) > 0 and existing.get("description")
        if already_scored and job.description and job.description == existing.get("description"):
            skipped += 1
            upsert_job(job)  # update metadata only, keep existing score
            continue
        info = score_job(job.title, job.company, job.description or job.title)
        upsert_job(job, info)
        scored += 1
        if is_new:
            new += 1
        if info.get("must_apply"):
            must += 1
    log.info("Indexed %d jobs (%d new, %d scored, %d skipped-already-scored), %d must-apply",
             len(jobs), new, scored, skipped, must)
    return jobs


def cmd_prep(min_score: int | None = None, limit: int | None = None, force: bool = False):
    init_db()
    min_score = get_min_score(min_score)
    limit = get_prep_limit(limit)
    top = get_top_jobs(min_score=min_score, limit=limit, exclude_prepped=not force)
    log.info("Preparing %d applications (score >= %d, force=%s)", len(top), min_score, force)
    for j in top:
        try:
            reasons = json.loads(j.get("match_reasons") or "[]")
        except (json.JSONDecodeError, TypeError):
            reasons = []
        desc = j.get("description") or ""
        letter = tailor_cover_letter(j["title"], j["company"], desc, reasons)
        bullets = tailor_resume_bullets(j["title"], desc)
        save_application(j["job_id"], letter, bullets, status="ready")
        log.info("Prepped: %s @ %s (score %s)", j["title"], j["company"], j["score"])
    if not top:
        log.info("Nothing to prep — run `python3 main.py search` first, or lower --min-score")
    return len(top)


def cmd_digest(no_notify: bool = False, run_results: dict | None = None):
    init_db()
    stats = get_stats()
    top = get_top_jobs(min_score=60, limit=15)
    ready = get_pending_ready()
    content = build_digest(
        top, ready, stats,
        applied_recent=get_applied_recent(24),
        run_results=run_results,
    )
    path = save_digest(content)
    print(content)
    print(f"\n[Saved: {path}]")
    if not no_notify:
        sent = send_digest(content)
        if sent:
            print(f"[Delivered via: {', '.join(sent)}]")
    return path


def cmd_stats():
    init_db()
    stats = get_stats()
    try:
        from apply.answercache import stats as cache_stats

        stats["learned_answers"] = cache_stats()
    except Exception:
        pass
    print(json.dumps(stats, indent=2))


def cmd_ready():
    init_db()
    ready = get_pending_ready()
    if not ready:
        print("No applications ready. Run `python3 main.py prep` first.")
        return
    for a in ready:
        print(f"\n{'='*60}")
        print(f"JOB: {a['title']} @ {a['company']} (score {a['score']})")
        print(f"URL: {a['url']}")
        print(f"\n--- COVER LETTER ---\n{a['cover_letter']}")
        print(f"\n--- RESUME BULLETS ---\n{a['resume_bullets']}")
    print(f"\n{len(ready)} applications ready. Apply, then: python3 main.py applied <job_id>")


def cmd_applied(job_id: str):
    init_db()
    if mark_applied(job_id):
        print(f"Marked {job_id} as applied.")
    else:
        print(f"No ready application found for {job_id}.")
        print("Run `python3 main.py prep` first, or check the job id with `python3 main.py ready`.")
        sys.exit(1)


def cmd_serve(host: str = "127.0.0.1", port: int = 8787):
    from apps.review import create_app

    create_app().run(host=host, port=port, debug=False)


def cmd_apply(
    job_id: str,
    mode: str = "fill",
    auto_submit: bool = False,
    yes: bool = False,
    resume: str | None = None,
    no_resume: bool = False,
    force: bool = False,
    dry_run: bool = False,
):
    init_db()
    from apply.packet import build_packet

    try:
        packet = build_packet(job_id, force=force, resume_path=resume, no_resume=no_resume)
    except LookupError as e:
        print(f"Error: {e}")
        sys.exit(1)

    job = packet["job"]
    print(f"PACKET: {job['title']} @ {job['company']} (score {job['score']})")
    print(f"  cover letter : {'reused' if packet['reused'] else 'generated'} ({len(packet['cover_letter'])} chars)")
    print(f"  resume       : {packet['resume_file'] or 'none'} — {packet['resume_how']}")
    print(f"  hr email     : {packet['hr_email'] or 'not found in description'}")

    if mode == "packet":
        print("\n--- COVER LETTER ---\n" + packet["cover_letter"])
        print("\n--- RESUME BULLETS ---\n" + packet["resume_bullets"])
        return

    if mode == "email":
        from apply.emailer import send_application

        try:
            msg = send_application(packet, dry_run=dry_run)
        except Exception as e:
            print(f"Email apply failed: {e}")
            sys.exit(1)
        print(msg)
        return

    # mode == "fill": open browser, autofill, gate (or auto-submit)
    from apply.autofill import run_fill
    from tracker.db import mark_applied

    print("\n--- COVER LETTER (autofill inserts it; keep for manual paste) ---\n")
    print(packet["cover_letter"])

    if auto_submit and not yes:
        try:
            ans = input(f"\nAuto-submit to {job['url']}? Type 'yes' to confirm: ").strip().lower()
        except EOFError:
            ans = ""
        if ans != "yes":
            print("Auto-submit cancelled — switching to review gate (you submit manually).")
            auto_submit = False

    result = run_fill(
        job,
        packet["cover_letter"],
        packet["resume_file"],
        auto_submit=auto_submit,
        headless=False,
    )
    status = result.get("status", "error")
    print(f"\nAutofill status : {status}")
    print(f"Filled          : {', '.join(result.get('filled', [])) or 'nothing'}")
    if result.get("missing"):
        print(f"Missing         : {', '.join(result['missing'])}")
    print(f"Page            : {result.get('url', '')}")
    if result.get("screenshot"):
        print(f"Failure screenshot: {result['screenshot']}")

    if status == "submitted":
        mark_applied(job_id, notes=f"auto-submitted at {result.get('url', '')}")
        print("Confirmed + marked applied.")
    elif status == "submitted_unconfirmed":
        print("Submit clicked, but confirmation text not detected — verify in the browser, then Mark applied.")
    elif status == "filled":
        print("Form filled, browser left open for your review + submit.")
    else:
        print("Autofill hit an error — use the cover letter above to apply manually.")
        sys.exit(1)


def cmd_auto_apply(
    limit: int | None = None,
    min_score: int | None = None,
    mode: str = "auto",
    headless: bool | None = None,
    resume: str | None = None,
) -> dict:
    """Submit prepped ('ready') applications with no human in the loop.

    Safe by construction: only touches status='ready' rows (applied ones are
    never re-touched), caps at --limit per run, and skips anything below
    --min-score. Failures stay 'ready' so the next run can retry them.

    Returns dict with run results: {submitted, unconfirmed, failed, skipped}.
    """
    init_db()
    from apply.packet import build_packet

    limit = get_auto_apply_limit(limit)
    min_score = get_auto_apply_min_score(min_score)
    headless = auto_apply_headless() if headless is None else headless

    ready = [a for a in get_pending_ready() if (a.get("score") or 0) >= min_score]
    if not ready:
        log.info("Auto-apply: nothing ready at score >= %d — run `python3 main.py prep` first", min_score)
        return {"submitted": [], "unconfirmed": [], "failed": [], "skipped": []}

    # Duplicate protection: skip jobs whose title+company already has an 'applied' application
    with _db() as conn:
        rows = conn.execute(
            """SELECT LOWER(TRIM(j.title)) || '|' || LOWER(TRIM(j.company)) as key
               FROM applications a JOIN jobs j ON a.job_id = j.job_id
               WHERE a.status IN ('applied', 'submitted_unconfirmed')"""
        ).fetchall()
        applied_titles = {r["key"] for r in rows}

    results = {"submitted": [], "unconfirmed": [], "failed": [], "skipped": []}
    todo = []
    for a in ready:
        key = f"{(a.get('title') or '').strip().lower()}|{(a.get('company') or '').strip().lower()}"
        if key in applied_titles:
            log.info("Auto-apply: SKIP duplicate — %s @ %s (already applied via another listing)", a["title"], a["company"])
            results["skipped"].append({"title": a["title"], "company": a["company"], "reason": "duplicate"})
            continue
        todo.append(a)

    dup_skipped = len(results["skipped"])
    if dup_skipped:
        log.info("Auto-apply: skipped %d duplicates (already applied same job)", dup_skipped)

    if not todo:
        log.info("Auto-apply: nothing to do after duplicate filtering")
        print(f"Auto-apply: 0 submitted — {dup_skipped} duplicates skipped, nothing new ready")
        return results

    log.info(
        "Auto-apply: %d candidates ready (target limit=%d, mode=%s, score >= %d, headless=%s)",
        len(todo), limit, mode, min_score, headless,
    )
    from apply.ratelimit import cap_reached, human_delay

    submitted_this_run = 0
    for a in todo:
        if submitted_this_run >= limit:
            log.info("Auto-apply: reached target submission limit (%d/%d) — run complete", submitted_this_run, limit)
            break

        job_id, title, company = a["job_id"], a["title"], a["company"]
        platform = (a.get("source") or "").lower()

        # Daily cap per platform — stop when hit (account safety)
        if platform and cap_reached(platform):
            remaining = len(todo) - len(results["submitted"]) - len(results["failed"]) - len(results["skipped"])
            log.warning("Auto-apply: %s daily cap reached — stopping run (%d not attempted)", platform, remaining)
            break

        # Human-like delay between submissions (skip before the first job)
        if submitted_this_run > 0:
            human_delay()

        try:
            packet = build_packet(job_id, resume_path=resume)
        except Exception as e:
            log.error("Auto-apply: packet failed for %s @ %s — %s", title, company, e)
            results["failed"].append({"title": title, "company": company, "error": str(e)})
            continue

        use_email = mode == "email" or (mode == "auto" and bool(packet.get("hr_email")))
        if use_email:
            if not packet.get("hr_email"):
                log.info("Auto-apply: no HR email for %s @ %s — skipping (use --mode fill)", title, company)
                results["skipped"].append({"title": title, "company": company, "reason": "no HR email"})
                continue
            from apply.emailer import send_application

            try:
                log.info("Auto-apply: %s", send_application(packet))
                results["submitted"].append({"title": title, "company": company, "url": a.get("url", "")})
                submitted_this_run += 1
            except Exception as e:
                log.error("Auto-apply: email failed for %s @ %s — %s", title, company, e)
                results["failed"].append({"title": title, "company": company, "error": str(e)})
            continue

        # mode == "fill": browser autofill + submit click
        from apply.autofill import run_fill

        result = run_fill(
            packet["job"],
            packet["cover_letter"],
            packet["resume_file"],
            auto_submit=True,
            headless=headless,
            wait_for_close=False,
        )
        status = result.get("status", "error")
        if status == "submitted":
            mark_applied(job_id, notes=f"auto-submitted at {result.get('url', '')}")
            log.info("Auto-apply: submitted %s @ %s", title, company)
            results["submitted"].append({"title": title, "company": company, "url": result.get("url", "")})
            submitted_this_run += 1
            if submitted_this_run >= limit:
                log.info("Auto-apply: reached target limit (%d/%d) — run complete", submitted_this_run, limit)
                break
        elif status == "external_apply":
            # not an Easy Apply job — skip without burning retries; keep ready for manual apply
            reason = (result.get("missing") or ["external apply"])[0]
            log.info("Auto-apply: SKIP external apply — %s @ %s", title, company)
            results["skipped"].append({"title": title, "company": company, "reason": reason})
        elif status == "submitted_unconfirmed":
            # mark it anyway — leaving it 'ready' would submit a duplicate tomorrow
            mark_applied(
                job_id,
                notes=f"submit clicked, confirmation not detected at {result.get('url', '')} — verify manually",
            )
            log.warning("Auto-apply: %s @ %s submitted but unconfirmed — verify manually", title, company)
            results["unconfirmed"].append({"title": title, "company": company, "url": result.get("url", "")})
            submitted_this_run += 1
            if submitted_this_run >= limit:
                log.info("Auto-apply: reached target limit (%d/%d) — run complete", submitted_this_run, limit)
                break
        else:
            error_msg = status
            if result.get("missing"):
                error_msg += f": {', '.join(result['missing'][:2])}"
            log.warning(
                "Auto-apply: %s @ %s failed (%s%s%s) — left ready for retry",
                title, company, status,
                f", missing: {', '.join(result.get('missing', []))}" if result.get("missing") else "",
                f", see {result['screenshot']}" if result.get("screenshot") else "",
            )
            results["failed"].append({
                "title": title, "company": company,
                "url": a.get("url", ""), "error": error_msg,
            })

    summary = (
        f"{len(results['submitted'])} submitted, {len(results['unconfirmed'])} unconfirmed, "
        f"{len(results['failed'])} failed, {len(results['skipped'])} skipped — "
        f"{len(ready) - len(todo)} still queued"
    )
    log.info("Auto-apply done: %s", summary)
    print(f"Auto-apply: {summary}")

    # Save CSV report for this run
    report_path = _save_autoapply_report(results)
    if report_path:
        print(f"Report: {report_path}")

    return results


def _save_autoapply_report(results: dict) -> str | None:
    """Write a CSV report of this auto-apply run to logs/."""
    import csv as _csv
    from datetime import datetime as _dt

    try:
        reports_dir = ROOT / "logs" / "autoapply_reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        ts = _dt.now().strftime("%Y%m%d-%H%M%S")
        path = reports_dir / f"autoapply-{ts}.csv"

        with open(path, "w", newline="", encoding="utf-8") as f:
            w = _csv.writer(f)
            w.writerow(["timestamp", "status", "title", "company", "url", "detail"])
            now = _dt.now().isoformat(timespec="seconds")
            for item in results.get("submitted", []):
                w.writerow([now, "submitted", item.get("title", ""), item.get("company", ""), item.get("url", ""), ""])
            for item in results.get("unconfirmed", []):
                w.writerow([now, "unconfirmed", item.get("title", ""), item.get("company", ""), item.get("url", ""), "verify manually"])
            for item in results.get("failed", []):
                w.writerow([now, "failed", item.get("title", ""), item.get("company", ""), item.get("url", ""), item.get("error", "")])
            for item in results.get("skipped", []):
                w.writerow([now, "skipped", item.get("title", ""), item.get("company", ""), "", item.get("reason", "")])

        # Also append to a cumulative CSV
        all_path = reports_dir / "autoapply-all.csv"
        write_header = not all_path.exists()
        with open(all_path, "a", newline="", encoding="utf-8") as f:
            w = _csv.writer(f)
            if write_header:
                w.writerow(["timestamp", "status", "title", "company", "url", "detail"])
            now2 = _dt.now().isoformat(timespec="seconds")
            for status_key in ("submitted", "unconfirmed", "failed", "skipped"):
                for item in results.get(status_key, []):
                    detail = item.get("error") or item.get("reason") or ""
                    w.writerow([now2, status_key, item.get("title", ""), item.get("company", ""), item.get("url", ""), detail])

        return str(path)
    except Exception as e:
        log.warning("Could not save auto-apply report: %s", e)
        return None


def cmd_linkedin_login():
    """One-time LinkedIn login — uses your Chrome profile if possible, else browser login."""
    init_db()
    from apply.linkedin import has_session, login_interactive

    print("Chrome khol rahi hoon aapke default profile ke saath — agar pehle se LinkedIn")
    print("login hai to kuch karna nahi padega, session apne aap save ho jayega.")
    print("Nahi hai to wahi window me log in kar lo (ya .env me LINKEDIN_EMAIL /")
    print("LINKEDIN_PASSWORD set karo — kabhi mat chat me bhejo). Max ~7 min wait.")
    if login_interactive():
        print("Session saved — auto-apply LinkedIn Easy Apply use kar sakega.")
    elif has_session():
        print("Purana session file me hai — woh use hoga.")
    else:
        print("Login detect nahi hua — dobara try: python3 main.py linkedin-login")


def cmd_indeed_login():
    """One-time Indeed login — uses your Chrome profile if possible, else browser login."""
    init_db()
    from apply.indeed import has_session, login_interactive

    print("Chrome khol rahi hoon aapke default profile ke saath — agar pehle se Indeed")
    print("login hai to kuch karna nahi padega, session apne aap save ho jayega.")
    print("Nahi hai to wahi window me log in kar lo (ya .env me INDEED_EMAIL /")
    print("INDEED_PASSWORD set karo — kabhi mat chat me bhejo). Max ~7 min wait.")
    if login_interactive():
        print("Session saved — auto-apply Indeed Easy Apply use kar sakega.")
    elif has_session():
        print("Purana session file me hai — woh use hoga.")
    else:
        print("Login detect nahi hua — dobara try: python3 main.py indeed-login")


def cmd_naukri_login():
    """One-time Naukri login — uses your Chrome profile if possible, else browser login."""
    init_db()
    from apply.naukri import has_session, login_interactive

    print("Chrome khol rahi hoon aapke default profile ke saath — agar pehle se Naukri")
    print("login hai to kuch karna nahi padega, session apne aap save ho jayega.")
    print("Nahi hai to wahi window me log in kar lo (ya .env me NAUKRI_EMAIL /")
    print("NAUKRI_PASSWORD set karo — kabhi mat chat me bhejo). Max ~7 min wait.")
    if login_interactive():
        print("Session saved — auto-apply Naukri Easy Apply use kar sakega.")
    elif has_session():
        print("Purana session file me hai — woh use hoga.")
    else:
        print("Login detect nahi hua — dobara try: python3 main.py naukri-login")
        sys.exit(1)


def cmd_naukrigulf_login():
    """One-time NaukriGulf login — uses your Chrome profile if possible, else browser login."""
    init_db()
    from apply.naukrigulf import has_session, login_interactive

    print("Chrome khol rahi hoon aapke default profile ke saath — agar pehle se NaukriGulf")
    print("login hai to kuch karna nahi padega, session apne aap save ho jayega.")
    print("Nahi hai to wahi window me log in kar lo (ya .env me NAUKRIGULF_EMAIL /")
    print("NAUKRIGULF_PASSWORD set karo with ALLOW_STORED_CREDENTIALS=1). Max ~7 min wait.")
    if login_interactive():
        print("Session saved — auto-apply NaukriGulf Easy Apply use kar sakega.")
    elif has_session():
        print("Purana session file me hai — woh use hoga.")
    else:
        print("Login detect nahi hua — dobara try: python3 main.py naukrigulf-login")
        sys.exit(1)


def cmd_bayt_login():
    """One-time Bayt login — uses your Chrome profile if possible, else browser login."""
    init_db()
    from apply.bayt import has_session, login_interactive

    print("Chrome khol rahi hoon aapke default profile ke saath — agar pehle se Bayt")
    print("login hai to kuch karna nahi padega, session apne aap save ho jayega.")
    print("Nahi hai to wahi window me log in kar lo (ya .env me BAYT_EMAIL /")
    print("BAYT_PASSWORD set karo with ALLOW_STORED_CREDENTIALS=1). Max ~7 min wait.")
    if login_interactive():
        print("Session saved — auto-apply Bayt Easy Apply use kar sakega.")
    elif has_session():
        print("Purana session file me hai — woh use hoga.")
    else:
        print("Login detect nahi hua — dobara try: python3 main.py bayt-login")
        sys.exit(1)


def cmd_naukri_batch(
    limit: int = 30,
    min_score: int = 40,
    headless: bool = False,
    queries: list[str] | None = None,
):
    """Naukri batch mode — search, score, prep, and apply to 30+ Indian jobs."""
    init_db()
    from apply.naukri import has_session

    if not has_session():
        print("Naukri session nahi hai! Pehle login karo:")
        print("  python3 main.py naukri-login")
        sys.exit(1)

    log.info("=== Naukri Batch Mode: targeting %d jobs ===", limit)

    # Step 1: Search Naukri
    queries = queries or [
        "AI Engineer", "Machine Learning Engineer", "LLM Engineer",
        "GenAI Engineer", "Data Scientist", "Python Developer",
        "Agentic AI", "NLP Engineer",
    ]
    log.info("Searching Naukri with %d queries...", len(queries))

    from scraper.pw_jobs import scrape_naukri, fetch_naukri_descriptions_pw
    from tracker.db import upsert_job, set_job_description, get_pending_ready, save_application, mark_applied
    from ai.assistant import score_job

    all_jobs = []
    for q in queries:
        try:
            jobs = scrape_naukri(q, location="India", limit=20)
            all_jobs.extend(jobs)
            log.info("Naukri '%s': %d jobs", q, len(jobs))
        except Exception as e:
            log.warning("Naukri search failed for '%s': %s", q, e)

    # Dedupe
    seen = set()
    unique_jobs = []
    for j in all_jobs:
        if j.job_id not in seen:
            seen.add(j.job_id)
            unique_jobs.append(j)
    log.info("Total unique Naukri jobs: %d", len(unique_jobs))

    # Step 2: Upsert to DB
    for j in unique_jobs:
        upsert_job(j)

    # Step 3: Fetch descriptions
    log.info("Fetching descriptions...")
    urls_to_fetch = [j.url for j in unique_jobs if j.url][:30]
    try:
        descs = fetch_naukri_descriptions_pw(urls_to_fetch)
        for j in unique_jobs:
            if j.url in descs:
                set_job_description(j.job_id, descs[j.url])
                j.description = descs[j.url]
    except Exception as e:
        log.warning("Description fetch failed: %s", e)

    # Step 4: Score jobs
    log.info("Scoring jobs with AI...")
    scored = 0
    for j in unique_jobs:
        if j.description and len(j.description) > 100:
            try:
                result = score_job(j.title, j.company, j.description)
                score = result.get("score", 0)
                with _db() as conn:
                    conn.execute(
                        "UPDATE jobs SET score = ?, match_reasons = ?, gaps = ?, must_apply = ? WHERE job_id = ?",
                        (score, json.dumps(result.get("match_reasons", [])), json.dumps(result.get("gaps", [])),
                         result.get("must_apply", False), j.job_id),
                    )
                j.score = score
                scored += 1
            except Exception as e:
                log.debug("Scoring failed for %s: %s", j.title[:30], e)
    log.info("Scored %d jobs", scored)

    # Step 5: Prep top jobs
    from config.settings import load_env
    load_env()
    top_jobs = sorted(
        [j for j in unique_jobs if j.description and (j.score or 0) >= min_score],
        key=lambda x: x.score or 0,
        reverse=True,
    )[:limit]

    log.info("Prepping %d applications (score >= %d)...", len(top_jobs), min_score)
    for j in top_jobs:
        # Read match_reasons from DB (already scored in step 4) — don't re-score
        reasons = []
        try:
            got = get_job(j.job_id)
            if got:
                reasons = json.loads(got.get("match_reasons") or "[]")
        except (json.JSONDecodeError, TypeError):
            reasons = []
        from ai.assistant import tailor_cover_letter, tailor_resume_bullets
        letter = tailor_cover_letter(j.title, j.company, j.description or "", reasons)
        bullets = tailor_resume_bullets(j.title, j.description or "")
        save_application(j.job_id, letter, bullets, status="ready")
        log.info("Prepped: %s @ %s (score %s)", j.title[:40], j.company, j.score)

    # Step 6: Auto-apply
    ready = [a for a in get_pending_ready() if (a.get("score") or 0) >= min_score]
    todo = ready[:limit]
    log.info("Applying to %d Naukri jobs...", len(todo))

    from apply.packet import build_packet
    from apply.autofill import run_fill

    results = {"submitted": [], "unconfirmed": [], "failed": [], "skipped": []}
    for a in todo:
        job_id, title, company = a["job_id"], a["title"], a["company"]
        try:
            packet = build_packet(job_id)
            result = run_fill(
                packet["job"],
                packet["cover_letter"],
                packet["resume_file"],
                auto_submit=True,
                headless=headless,
                wait_for_close=False,
            )
            status = result.get("status", "error")
            if status == "submitted":
                mark_applied(job_id, notes=f"naukri batch at {result.get('url', '')}")
                results["submitted"].append({"title": title, "company": company})
                log.info("✅ Submitted: %s @ %s", title[:40], company)
            elif status == "submitted_unconfirmed":
                mark_applied(job_id, notes=f"naukri batch unconfirmed at {result.get('url', '')}")
                results["unconfirmed"].append({"title": title, "company": company})
                log.warning("⚠️ Unconfirmed: %s @ %s", title[:40], company)
            else:
                results["failed"].append({"title": title, "company": company, "error": status})
                log.warning("❌ Failed: %s @ %s (%s)", title[:40], company, status)
        except Exception as e:
            results["failed"].append({"title": title, "company": company, "error": str(e)})
            log.error("Error applying to %s: %s", title[:30], e)

    summary = (
        f"Naukri Batch: {len(results['submitted'])} submitted, "
        f"{len(results['unconfirmed'])} unconfirmed, "
        f"{len(results['failed'])} failed"
    )
    log.info(summary)
    print(f"\n{summary}")
    return results


def cmd_collect_emails(source: str | None = None):
    """Collect recruiter emails from job descriptions."""
    from apply.coldmail import collect_emails_from_jobs
    count = collect_emails_from_jobs(source=source)
    print(f"Collected {count} new recruiter emails from job descriptions.")
    return count


def cmd_import_contacts(csv_path: str):
    """Import contacts from CSV file."""
    from apply.coldmail import import_contacts_from_csv
    count = import_contacts_from_csv(csv_path)
    print(f"Imported {count} new contacts from {csv_path}.")
    return count


def cmd_contacts(status: str | None = None, limit: int = 50):
    """List contacts."""
    from tracker.db import get_contacts, get_contact_stats
    stats = get_contact_stats()
    print(f"\n📊 Contact Stats:")
    print(f"   Total: {stats['total_contacts']} | New: {stats['new']} | "
          f"Contacted: {stats['contacted']} | Replied: {stats['replied']}")
    print(f"   Sent today: {stats['sent_today']}/10\n")

    contacts = get_contacts(status=status, limit=limit)
    if not contacts:
        print("No contacts found. Run: python3 main.py collect-emails")
        return

    for c in contacts:
        status_icon = {"new": "🆕", "contacted": "📧", "replied": "💬", "interested": "✅"}.get(c["status"], "❓")
        print(f"{status_icon} {c['email']}")
        if c.get("name"):
            print(f"   Name: {c['name']}")
        if c.get("company"):
            print(f"   Company: {c['company']}")
        if c.get("role"):
            print(f"   Role: {c['role']}")
        print(f"   Status: {c['status']} | Source: {c['source']}")
        print()


def cmd_coldmail_send(
    limit: int = 10,
    daily_limit: int = 10,
    status: str = "new",
    message: str = "",
    dry_run: bool = False,
):
    """Send cold emails to contacts."""
    from apply.coldmail import send_cold_emails

    if dry_run:
        print("🔍 DRY RUN — no emails will be sent\n")

    results = send_cold_emails(
        limit=limit,
        daily_limit=daily_limit,
        status=status,
        custom_message=message,
        dry_run=dry_run,
    )

    print(f"\n📧 Cold Mail Results:")
    print(f"   Sent: {results['sent']}")
    print(f"   Failed: {results['failed']}")
    if results.get("reason"):
        print(f"   Reason: {results['reason']}")
    return results


def cmd_run(args):
    init_db()
    log.info("=== Daily cycle start ===")
    cmd_search(
        queries=args.queries,
        location=args.location,
        sources=args.sources,
        desc_limit=args.desc_limit,
        playwright=args.playwright,
    )
    cmd_prep(min_score=args.min_score, limit=args.limit, force=args.force)
    run_results = None
    if args.auto_apply or auto_apply_enabled():
        run_results = cmd_auto_apply(
            limit=args.apply_limit,
            min_score=args.apply_min_score,
            mode=args.apply_mode,
            headless=args.headless,
            resume=args.resume,
        )
    cmd_digest(no_notify=args.no_notify, run_results=run_results)
    log.info("=== Daily cycle done ===")


def cmd_linkedin_collect(queries: list[str] | None = None, limit: int = 20):
    """Collect recruiter emails from LinkedIn posts."""
    from apply.coldmail import upsert_contact
    from scraper.linkedin_posts import scrape_linkedin_posts

    print("🔍 Searching LinkedIn for urgent hiring posts...")
    print("   (Make sure you're logged in: python3 main.py linkedin-login)\n")

    results = scrape_linkedin_posts(queries=queries, limit_per_query=limit)

    saved = 0
    for r in results:
        if upsert_contact(
            email=r["email"],
            name=r.get("name", ""),
            company=r.get("company", ""),
            role="Recruiter (from LinkedIn post)",
            source="linkedin_post",
        ):
            saved += 1

    print(f"\n✅ Collected {saved} new recruiter emails from LinkedIn posts!")

    # Show stats
    from tracker.db import get_contact_stats
    stats = get_contact_stats()
    print(f"\n📊 Contact Stats:")
    print(f"   Total: {stats['total_contacts']} | New: {stats['new']} | Contacted: {stats['contacted']} | Replied: {stats['replied']}")


def cmd_coldmail_automation(
    target: int = 20,
    queries: list[str] | None = None,
    dry_run: bool = False,
    no_notify: bool = False,
):
    """One-click cold mail automation."""
    from automation.coldmail_automation import run_coldmail_automation

    print("🚀 Starting Cold Mail Automation...")
    print(f"   Daily target: {target} emails")
    if dry_run:
        print("   ⚠️ DRY RUN mode - no emails will be sent")
    print()

    results = run_coldmail_automation(
        daily_target=target,
        linkedin_queries=queries,
        send_notification=not no_notify,
        dry_run=dry_run,
    )

    print("\n" + "="*50)
    print("📊 COLD MAIL AUTOMATION RESULTS")
    print("="*50)
    print(f"✅ Status: {results['status']}")
    print(f"📧 Emails Collected: {results['emails_collected']}")
    print(f"📤 Emails Sent: {results['emails_sent']}")
    print(f"❌ Failed: {results['emails_failed']}")

    if results.get("stats"):
        stats = results["stats"]
        print(f"\n📈 Contact Statistics:")
        print(f"   Total: {stats['total_contacts']}")
        print(f"   New (pending): {stats['new']}")
        print(f"   Contacted: {stats['contacted']}")
        print(f"   Replied: {stats['replied']}")
        print(f"   Sent Today: {stats['sent_today']}/{target}")

    if results.get("notification_sent"):
        print(f"\n📧 Notification sent to your email!")

    print("="*50)


def cmd_coldmail_history(limit: int = 10):
    """View cold mail automation history."""
    from automation.coldmail_automation import get_coldmail_history

    history = get_coldmail_history(limit=limit)

    print("\n" + "="*50)
    print("📜 COLD MAIL AUTOMATION HISTORY")
    print("="*50)

    if not history["runs"]:
        print("No automation runs found.")
        return

    for run in history["runs"][:limit]:
        print(f"\n🔄 Run #{run['id']}")
        print(f"   Started: {run['started_at']}")
        print(f"   Status: {run['status']}")

        if run.get("results"):
            import json
            results = json.loads(run["results"]) if isinstance(run["results"], str) else run["results"]
            print(f"   Collected: {results.get('emails_collected', 0)}")
            print(f"   Sent: {results.get('emails_sent', 0)}")
            print(f"   Failed: {results.get('emails_failed', 0)}")

    print("\n" + "="*50)
    print("📧 Recent Emails:")
    print("="*50)

    for email in history["emails"][:15]:
        name = email.get("name") or "Unknown"
        company = email.get("company") or ""
        print(f"   → {email['email']} ({name} - {company})")

    print("\n" + "="*50)
    stats = history["stats"]
    print(f"📈 Current Stats: {stats['total_contacts']} total | {stats['new']} new | {stats['contacted']} contacted")
    print(f"📅 Sent Today: {stats['sent_today']}")
    print("="*50)


def cmd_ratelimit():
    """Show daily submission caps vs used (account safety)."""
    from apply.ratelimit import rate_limit_enabled, rate_limit_report

    report = rate_limit_report()
    print("\n🛡️  RATE LIMIT STATUS (account safety)")
    print("=" * 44)
    if not rate_limit_enabled():
        print("⚠️  RATE_LIMIT_ENABLED=0 — limits are OFF")
    for platform, info in report.items():
        bar = "█" * info["used"] + "░" * info["remaining"]
        status = "🔴 CAP REACHED" if info["remaining"] == 0 else "🟢 ok"
        print(f"  {platform.capitalize():<10} {bar} {info['used']}/{info['cap']}  {status}")
    print("=" * 44)
    print("  Caps: LINKEDIN_DAILY_CAP=15, NAUKRI_DAILY_CAP=15, INDEED_DAILY_CAP=10")
    print("  Delays: APPLY_DELAY_MIN=30s, APPLY_DELAY_MAX=90s between submissions")


def cmd_daily(
    no_notify: bool = False,
    apply_limit: int | None = None,
    apply_min_score: int | None = None,
    coldmail_target: int = 20,
):
    """Complete daily cycle: search -> score -> prep -> auto-apply -> cold mail -> digest."""
    from apply.ratelimit import rate_limit_report

    log.info("=== Daily cycle START ===")
    print("🌅 Daily cycle start")

    # 1. Search + score + descriptions
    cmd_search(playwright=True)

    # 2. Prep cover letters
    cmd_prep()

    # 3. Auto-apply (rate-limited)
    run_results = cmd_auto_apply(limit=apply_limit, min_score=apply_min_score)

    # 4. Cold mail automation (collect + send, capped at daily target)
    coldmail_results = None
    try:
        from automation.coldmail_automation import run_coldmail_automation
        coldmail_results = run_coldmail_automation(daily_target=coldmail_target, send_notification=False)
    except Exception as e:
        log.error("Cold mail automation failed: %s", e)

    # 4.5. Reply scan — update outcomes + contact statuses before digest
    try:
        from notify.inbox import scan_replies

        scan = scan_replies(days=10, limit=100)
        if not scan.get("error"):
            log.info(
                "Reply scan: %d contact replies, %d application replies",
                len(scan.get("contact_replies", [])),
                len(scan.get("application_replies", [])),
            )
    except Exception as e:
        log.error("Reply scan failed: %s", e)

    # 5. Digest (email + telegram)
    cmd_digest(no_notify=no_notify, run_results=run_results)

    # 6. Print safety summary
    rl = rate_limit_report()
    print("\n🛡️  End-of-run rate limits:")
    for platform, info in rl.items():
        print(f"  {platform}: {info['used']}/{info['cap']} used today")

    log.info("=== Daily cycle DONE ===")
    print("✅ Daily cycle complete")


def cmd_schedule(action: str, hour: int = 9, minute: int = 30):
    """Install/uninstall/status of the daily auto-run via macOS launchd."""
    import subprocess

    label = "com.uaejobcopilot.daily"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    # real binary — launchd can't execute pyenv shims (shell scripts)
    import sys

    python = sys.executable or "/usr/bin/python3"
    project = str(ROOT)
    log_file = str(ROOT / "logs" / "scheduled-daily.log")

    if action == "status":
        if not plist_path.exists():
            print("❌ Not installed (no plist found). Run: python3 main.py schedule install")
            return
        print(f"📋 Plist: {plist_path}")
        try:
            out = subprocess.run(
                ["launchctl", "list"], capture_output=True, text=True, check=False
            )
            installed = label in out.stdout
            print(f"{'✅ Loaded in launchd' if installed else '⚠️ Plist exists but not loaded — run: schedule install'}")
        except Exception as e:
            print(f"launchctl check failed: {e}")
        print(f"⏰ Schedule: daily at {hour:02d}:{minute:02d} local time")
        return

    if action == "uninstall":
        try:
            subprocess.run(["launchctl", "unload", str(plist_path)], check=False, capture_output=True)
        except Exception:
            pass
        if plist_path.exists():
            plist_path.unlink()
            print(f"✅ Removed {plist_path}")
        else:
            print("Nothing to remove.")
        return

    # install
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>main.py</string>
        <string>daily</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{project}</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>{hour}</integer>
        <key>Minute</key>
        <integer>{minute}</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{log_file}</string>
    <key>StandardErrorPath</key>
    <string>{log_file}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
"""
    plist_path.write_text(plist)
    try:
        subprocess.run(["launchctl", "unload", str(plist_path)], check=False, capture_output=True)
        out = subprocess.run(
            ["launchctl", "load", str(plist_path)], capture_output=True, text=True, check=False
        )
        if out.returncode != 0:
            print(f"⚠️ launchctl load issue: {out.stderr.strip()}")
    except Exception as e:
        print(f"⚠️ launchctl failed: {e}")

    print(f"✅ Daily schedule installed — runs every day at {hour:02d}:{minute:02d}")
    print(f"   Plist: {plist_path}")
    print(f"   Log:   {log_file}")
    print("   Manage: python3 main.py schedule status|uninstall")


def cmd_doctor() -> int:
    """Full environment + dependency health check."""
    import importlib
    from pathlib import Path

    print("🩺 UAE Job Copilot — doctor / health check\n")

    # 1. Config validation
    issues = validate_config()
    code = print_config_report(issues)
    print()

    # 2. Dependency checks
    print("📦 Dependencies:")
    deps = [
        "requests",
        "bs4",
        "playwright",
        "anthropic",
        "openai",
        "flask",
        "reportlab",
        "pytest",
    ]
    all_ok = True
    for d in deps:
        try:
            importlib.import_module(d)
            print(f"   ✅ {d}")
        except ImportError:
            print(f"   ❌ {d}  (pip install {d})")
            all_ok = False
    print()

    # 3. Playwright browser check
    print("🌐 Playwright browsers:")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browsers = ["chromium", "firefox", "webkit"]
            for b in browsers:
                try:
                    getattr(p, b).launch(headless=True).close()
                    print(f"   ✅ {b}")
                except Exception:
                    print(f"   ⚠️  {b}  (run: playwright install {b})")
                    all_ok = False
    except Exception as e:
        print(f"   ❌ Playwright not usable: {e}")
        all_ok = False
    print()

    # 4. DB check
    print("🗄️  Database:")
    try:
        from tracker.db import init_db, DB_PATH

        init_db()
        print(f"   ✅ SQLite at {DB_PATH} (init OK)")
    except Exception as e:
        print(f"   ❌ DB init failed: {e}")
        all_ok = False
    print()

    # 5. Data dirs
    print("📁 Data directories:")
    root = Path(__file__).resolve().parent.parent
    for d in ["data", "data/resumes", "data/digests", "logs"]:
        p = root / d
        ok = p.exists()
        print(f"   {'✅' if ok else '❌'} {d}/ {'(exists)' if ok else '(missing — will be created)'}")
        if not ok:
            all_ok = False
    print()

    # 6. .env check
    env_file = root / ".env"
    print(f"🔐 .env file: {'✅ present' if env_file.exists() else '❌ missing (copy .env.example)'}")
    if not env_file.exists():
        all_ok = False

    print()
    if all_ok and code == 0:
        print("✅ All checks passed — ready to run.")
        return 0
    if code != 0:
        print("❌ Config errors found — fix them before running.")
        return 1
    print("⚠️  Some optional pieces are missing, but the tool can still run.")
    return 0


def cmd_outcome(job_id: str, outcome: str, notes: str = ""):
    """Record what happened after an application (feedback loop)."""
    init_db()
    from tracker.db import set_outcome

    if set_outcome(job_id, outcome, notes=notes):
        print(f"✅ {job_id} → outcome: {outcome}")
        if notes:
            print(f"   notes: {notes}")
    else:
        print(f"❌ No application found for job {job_id}")
        print("   (see `python3 main.py outcomes` or `ready` for ids)")
        sys.exit(1)


def cmd_outcomes(outcome: str | None = None, limit: int = 20):
    """List applications + outcomes; default shows untracked ones."""
    init_db()
    from tracker.db import get_outcome_stats, list_applied_with_outcome

    rows = list_applied_with_outcome(outcome=outcome, limit=limit)
    label = outcome or "untracked (no outcome yet)"
    print(f"\n📋 Applications — {label}: {len(rows)}")
    print("=" * 70)
    for r in rows:
        oc = r.get("outcome") or "—"
        print(f"  {r['job_id']}  [{oc:^9}]  {r['title'][:38]:<38} @ {r['company'][:24]:<24} ({r['source']})")
        if r.get("applied_at"):
            print(f"     applied: {r['applied_at'][:10]}" + (f" | outcome: {r['outcome_at'][:10]}" if r.get("outcome_at") else ""))
    st = get_outcome_stats()
    print("=" * 70)
    print(f"  Response rate: {st['responded']}/{st['applied']} applied = {st['response_rate']}%")
    for s in st["by_source"]:
        rate = round(100 * s["responded"] / s["applied"], 1) if s["applied"] else 0
        print(f"    {s['source']:<10} {s['responded']}/{s['applied']} responded ({rate}%)")
    if outcome is None and rows:
        print("\nRecord: python3 main.py outcome <job_id> <replied|interview|offer|rejected>")


def cmd_check_replies(days: int = 10, limit: int = 100):
    """Scan inbox for replies to cold mails and applications."""
    init_db()
    from notify.inbox import scan_replies

    result = scan_replies(days=days, limit=limit)

    if result.get("error"):
        print(f"❌ Reply scan failed: {result['error']}")
        return

    print(f"\n📬 Reply scan (last {days} days, {result.get('fetched', 0)} messages)")
    print("=" * 60)
    cold = result.get("contact_replies", [])
    print(f"1. Cold mail replies: {len(cold)}")
    for c in cold[:20]:
        print(f"   ✅ {c['email']}  ({c.get('company') or '?'}) — {c.get('subject', '')[:50]}")
    apps = result.get("application_replies", [])
    print(f"\n2. Application replies (auto-detected): {len(apps)}")
    for a in apps[:20]:
        print(f"   💬 {a['company'][:28]:<28} — {a['title'][:34]}")
        print(f"      from: {a.get('from_email','')} | outcome → {a['outcome']}")
    no = result.get("no_match", [])
    if no:
        print(f"\n3. Unmatched reply-like emails: {len(no)} (sample)")
        for m in no[:5]:
            print(f"   ? {m.get('from_email','')} — {m.get('subject','')[:55]}")
    print("=" * 60)
    print("Verify application matches in: python3 main.py outcomes")


def _split(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uae-job-copilot",
        description="Daily UAE job search: scrape, AI-score, prep applications, digest.",
    )
    sub = parser.add_subparsers(dest="cmd")

    def add_search_args(p):
        p.add_argument("--queries", help="comma-separated queries (overrides QUERIES env)")
        p.add_argument("--location", help="search location (default: United Arab Emirates)")
        p.add_argument(
            "--sources",
            help="comma-separated: linkedin,indeed,naukri,bayt,gulftalent (default: all)",
        )
        p.add_argument(
            "--desc-limit", type=int, default=50,
            help="max descriptions to fetch this run (0 disables; default 50)",
        )
        p.add_argument("--playwright", action="store_true",
                       help="run Playwright scrapers too (slow, needs installed browser)")

    def add_prep_args(p):
        p.add_argument("--min-score", type=int, help="minimum score to prep (default 70 or MIN_SCORE env)")
        p.add_argument("--limit", type=int, help="max applications to prep (default 5 or PREP_LIMIT env)")
        p.add_argument("--force", action="store_true",
                       help="regenerate cover letters for already-prepped jobs")

    def add_auto_apply_args(p):
        p.add_argument("--apply-limit", type=int, help="max auto-submissions per run (default 3 or AUTO_APPLY_LIMIT)")
        p.add_argument("--apply-min-score", type=int,
                       help="min score to auto-submit (default = MIN_SCORE or AUTO_APPLY_MIN_SCORE)")
        p.add_argument("--apply-mode", choices=["auto", "fill", "email"], default="auto",
                       help="auto = email if HR address found, else browser fill (default); "
                            "fill = browser only; email = HR-email jobs only")
        p.add_argument("--headless", action=argparse.BooleanOptionalAction, default=None,
                       help="run the browser without a window (default: AUTO_APPLY_HEADLESS env, else headed)")
        p.add_argument("--resume", help="resume file to attach (default: $RESUME_PATH, data/resume.pdf, or generated)")

    run_p = sub.add_parser("run", help="full daily cycle: search -> score -> prep -> [auto-apply] -> digest")
    add_search_args(run_p)
    add_prep_args(run_p)
    add_auto_apply_args(run_p)
    run_p.add_argument("--auto-apply", action="store_true",
                       help="submit ready applications in this run (or set AUTO_APPLY=1 in .env)")
    run_p.add_argument("--no-notify", action="store_true", help="skip digest delivery")

    auto_p = sub.add_parser("autoapply", help="submit ready applications automatically")
    add_auto_apply_args(auto_p)

    search_p = sub.add_parser("search", help="search + score only")
    add_search_args(search_p)

    prep_p = sub.add_parser("prep", help="generate cover letters for top matches")
    add_prep_args(prep_p)

    digest_p = sub.add_parser("digest", help="build/print today's digest")
    digest_p.add_argument("--no-notify", action="store_true", help="skip digest delivery")

    sub.add_parser("stats", help="show tracker stats")
    sub.add_parser("ready", help="list applications ready to submit")

    applied_p = sub.add_parser("applied", help="mark an application as applied")
    applied_p.add_argument("job_id")

    apply_p = sub.add_parser(
        "apply", help="apply to a job: browser fill (default), email, or just the packet"
    )
    apply_p.add_argument("job_id")
    apply_p.add_argument("--mode", choices=["fill", "email", "packet"], default="fill",
                         help="fill = browser autofill w/ human submit; email = send to HR address; "
                              "packet = print cover letter + bullets only")
    apply_p.add_argument("--auto-submit", action="store_true",
                         help="click Submit for you (fill mode; asks for typed confirmation unless --yes)")
    apply_p.add_argument("--yes", action="store_true", help="skip the auto-submit confirmation prompt")
    apply_p.add_argument("--resume", help="resume file to attach (default: $RESUME_PATH, data/resume.pdf, or generated)")
    apply_p.add_argument("--no-resume", action="store_true", help="don't attach or generate a resume")
    apply_p.add_argument("--force", action="store_true",
                         help="regenerate cover letter/bullets/resume even if a ready application exists")
    apply_p.add_argument("--dry-run", action="store_true", help="email mode: compose but don't send")

    serve_p = sub.add_parser("serve", help="launch the review UI")
    serve_p.add_argument("--host", default="127.0.0.1")
    serve_p.add_argument("--port", type=int, default=8787)

    sub.add_parser("linkedin-login", help="one-time LinkedIn login (opens browser; saves session for Easy Apply)")
    sub.add_parser("indeed-login", help="one-time Indeed login (opens browser; saves session for Easy Apply)")
    sub.add_parser("naukri-login", help="one-time Naukri login (opens browser; saves session for Easy Apply)")
    sub.add_parser("naukrigulf-login", help="one-time NaukriGulf login (opens browser; saves session for Easy Apply)")
    sub.add_parser("bayt-login", help="one-time Bayt login (opens browser; saves session for Easy Apply)")

    # Naukri batch mode
    nb = sub.add_parser("naukri-batch", help="search + apply to 30+ Naukri jobs in one run")
    nb.add_argument("--limit", type=int, default=30, help="max jobs to apply (default: 30)")
    nb.add_argument("--min-score", type=int, default=40, help="minimum AI score (default: 40)")
    nb.add_argument("--headless", action="store_true", help="run browser headless (default: headed for Naukri)")
    nb.add_argument("--queries", help="comma-separated Naukri search queries")

    # Cold mail commands
    ce = sub.add_parser("collect-emails", help="collect recruiter emails from job descriptions")
    ce.add_argument("--source", help="filter by job source (linkedin, naukri, etc.)")

    ic = sub.add_parser("import-contacts", help="import contacts from CSV file")
    ic.add_argument("csv_path", help="path to CSV file with emails")

    ct = sub.add_parser("contacts", help="list contacts")
    ct.add_argument("--status", help="filter by status (new, contacted, replied, etc.)")
    ct.add_argument("--limit", type=int, default=50, help="max contacts to show")

    cs = sub.add_parser("coldmail-send", help="send cold emails to contacts")
    cs.add_argument("--limit", type=int, default=10, help="max emails to send this run (default: 10)")
    cs.add_argument("--daily-limit", type=int, default=10, help="max emails per day (default: 10)")
    cs.add_argument("--status", default="new", help="contact status to send to (default: new)")
    cs.add_argument("--message", help="custom message to override default email body")
    cs.add_argument("--dry-run", action="store_true", help="preview emails without sending")

    # LinkedIn post collector
    lc = sub.add_parser("linkedin-collect", help="collect recruiter emails from LinkedIn urgent hiring posts")
    lc.add_argument("--queries", help="comma-separated search queries (default: urgent hiring UAE/Dubai)")
    lc.add_argument("--limit", type=int, default=20, help="max posts per query (default: 20)")

    # Cold mail automation
    cm = sub.add_parser("coldmail-automation", help="one-click cold mail automation (collect + send)")
    cm.add_argument("--target", type=int, default=20, help="daily email target (default: 20)")
    cm.add_argument("--queries", help="comma-separated LinkedIn queries")
    cm.add_argument("--dry-run", action="store_true", help="preview without sending")
    cm.add_argument("--no-notify", action="store_true", help="skip email notification")

    # Cold mail history
    ch = sub.add_parser("coldmail-history", help="view cold mail automation history")
    ch.add_argument("--limit", type=int, default=10, help="max runs to show (default: 10)")

    # Rate limit status
    sub.add_parser("ratelimit", help="show daily submission caps vs used (account safety)")

    # Application outcome tracking (feedback loop)
    oc = sub.add_parser("outcome", help="record what happened after an application")
    oc.add_argument("job_id", help="job id (see `outcomes` or `ready`)")
    oc.add_argument(
        "outcome",
        choices=["replied", "interview", "offer", "rejected", "spam"],
        help="what happened",
    )
    oc.add_argument("--notes", default="", help="optional note (e.g. email subject)")

    ocl = sub.add_parser("outcomes", help="list applications and their outcomes")
    ocl.add_argument("--outcome", choices=["replied", "interview", "offer", "rejected", "spam"],
                     help="filter by outcome (default: untracked only)")
    ocl.add_argument("--limit", type=int, default=20, help="max rows (default: 20)")

    # Inbox reply scanner (cold mail replies + application replies)
    rp = sub.add_parser("check-replies", help="scan inbox for cold-mail + application replies")
    rp.add_argument("--days", type=int, default=10, help="how far back to scan (default: 10)")
    rp.add_argument("--limit", type=int, default=100, help="max messages to fetch (default: 100)")

    # Full daily cycle: search -> score -> prep -> auto-apply -> cold mail -> digest
    dly = sub.add_parser("daily", help="complete daily cycle incl. cold mail automation (for scheduling)")
    dly.add_argument("--no-notify", action="store_true", help="skip digest delivery")
    dly.add_argument("--apply-limit", type=int, help="max auto-submissions this run")
    dly.add_argument("--apply-min-score", type=int, help="min score to auto-submit")
    dly.add_argument("--coldmail-target", type=int, default=20, help="cold mail daily target (default: 20)")

    # Daily schedule install/uninstall (launchd)
    sch = sub.add_parser("schedule", help="install/uninstall daily auto-run via launchd")
    sch.add_argument("action", choices=["install", "uninstall", "status"], help="manage the daily schedule")
    sch.add_argument("--hour", type=int, default=9, help="local hour to run daily (default: 9)")
    sch.add_argument("--minute", type=int, default=30, help="local minute to run daily (default: 30)")

    # Config / environment health check
    sub.add_parser("doctor", help="validate config + environment and report issues")

    return parser


def main(argv: list[str] | None = None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0

    # Startup config validation (advisory; errors are reported but don't block
    # unless the user explicitly runs `doctor`).
    _issues = validate_config()
    if _issues:
        print_config_report(_issues)
        print()

    if args.cmd == "run":
        cmd_run(args)
    elif args.cmd == "search":
        cmd_search(
            queries=_split(args.queries),
            location=args.location,
            sources=_split(args.sources),
            desc_limit=args.desc_limit,
            playwright=args.playwright,
        )
    elif args.cmd == "prep":
        cmd_prep(min_score=args.min_score, limit=args.limit, force=args.force)
    elif args.cmd == "autoapply":
        cmd_auto_apply(
            limit=args.apply_limit,
            min_score=args.apply_min_score,
            mode=args.apply_mode,
            headless=args.headless,
            resume=args.resume,
        )
    elif args.cmd == "digest":
        cmd_digest(no_notify=args.no_notify)
    elif args.cmd == "stats":
        cmd_stats()
    elif args.cmd == "ready":
        cmd_ready()
    elif args.cmd == "applied":
        cmd_applied(args.job_id)
    elif args.cmd == "apply":
        cmd_apply(
            args.job_id,
            mode=args.mode,
            auto_submit=args.auto_submit,
            yes=args.yes,
            resume=args.resume,
            no_resume=args.no_resume,
            force=args.force,
            dry_run=args.dry_run,
        )
    elif args.cmd == "serve":
        cmd_serve(host=args.host, port=args.port)
    elif args.cmd == "linkedin-login":
        cmd_linkedin_login()
    elif args.cmd == "indeed-login":
        cmd_indeed_login()
    elif args.cmd == "naukri-login":
        cmd_naukri_login()
    elif args.cmd == "naukrigulf-login":
        cmd_naukrigulf_login()
    elif args.cmd == "bayt-login":
        cmd_bayt_login()
    elif args.cmd == "naukri-batch":
        cmd_naukri_batch(
            limit=args.limit,
            min_score=args.min_score,
            headless=args.headless,
            queries=_split(args.queries),
        )
    elif args.cmd == "collect-emails":
        cmd_collect_emails(source=args.source)
    elif args.cmd == "import-contacts":
        cmd_import_contacts(args.csv_path)
    elif args.cmd == "contacts":
        cmd_contacts(status=args.status, limit=args.limit)
    elif args.cmd == "coldmail-send":
        cmd_coldmail_send(
            limit=args.limit,
            daily_limit=args.daily_limit,
            status=args.status,
            message=args.message or "",
            dry_run=args.dry_run,
        )
    elif args.cmd == "linkedin-collect":
        cmd_linkedin_collect(
            queries=_split(args.queries),
            limit=args.limit,
        )
    elif args.cmd == "coldmail-automation":
        cmd_coldmail_automation(
            target=args.target,
            queries=_split(args.queries),
            dry_run=args.dry_run,
            no_notify=args.no_notify,
        )
    elif args.cmd == "coldmail-history":
        cmd_coldmail_history(limit=args.limit)
    elif args.cmd == "ratelimit":
        cmd_ratelimit()
    elif args.cmd == "outcome":
        cmd_outcome(job_id=args.job_id, outcome=args.outcome, notes=args.notes)
    elif args.cmd == "outcomes":
        cmd_outcomes(outcome=args.outcome, limit=args.limit)
    elif args.cmd == "check-replies":
        cmd_check_replies(days=args.days, limit=args.limit)
    elif args.cmd == "schedule":
        cmd_schedule(action=args.action, hour=args.hour, minute=args.minute)
    elif args.cmd == "daily":
        cmd_daily(
            no_notify=args.no_notify,
            apply_limit=args.apply_limit,
            apply_min_score=args.apply_min_score,
            coldmail_target=args.coldmail_target,
        )
    elif args.cmd == "doctor":
        return cmd_doctor()
    else:
        parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
