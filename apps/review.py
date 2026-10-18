"""Local review UI: inspect scored jobs, prep cover letters, mark applied.

Run:  python3 apps/review.py [--port 8787]   (or: python3 main.py serve)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.settings import load_env  # noqa: E402

load_env()

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for  # noqa: E402

from datetime import datetime, timezone, timedelta
from ai.assistant import tailor_cover_letter, tailor_resume_bullets  # noqa: E402
from apply.emailer import extract_hr_email  # noqa: E402
from apply.packet import resume_filename  # noqa: E402
from scraper.jobs import fetch_description  # noqa: E402
from tracker.db import (  # noqa: E402
    _db,
    get_job,
    get_sent_today_count,
    get_stats,
    list_jobs,
    mark_applied,
    save_application,
    set_job_description,
)

IST = timezone(timedelta(hours=5, minutes=30))


def _format_applied_row(r: dict) -> dict:
    iso_str = r.get("applied_at") or ""
    date_label = "Earlier"
    is_today = False
    if iso_str:
        try:
            clean = iso_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt_ist = dt.astimezone(IST)
            now_ist = datetime.now(IST)
            is_today = dt_ist.date() == now_ist.date()
            prefix = "⚡ Today" if is_today else dt_ist.strftime("%d %b %Y")
            date_label = f"{prefix}, {dt_ist.strftime('%I:%M %p')}"
        except Exception:
            date_label = iso_str[:16].replace("T", " ")
    r["applied_at_pretty"] = date_label
    r["is_today"] = is_today
    return r

SOURCES = ["linkedin", "indeed", "naukri", "naukrigulf", "bayt", "gulftalent"]

# One automation run at a time (search -> score -> prep -> auto-apply -> digest).
_AUTOMATION: dict = {"proc": None, "log_file": None, "started": None}


def _automation_state() -> dict:
    proc = _AUTOMATION.get("proc")
    log_file = _AUTOMATION.get("log_file")
    state = {
        "running": False,
        "started": _AUTOMATION.get("started"),
        "log_file": log_file.name if log_file else None,
        "returncode": None,
        "tail": "",
    }
    if proc is not None:
        rc = proc.poll()
        if rc is None:
            state["running"] = True
        else:
            state["returncode"] = rc
    if log_file:
        try:
            lines = Path(log_file).read_text(errors="replace").splitlines()
            state["tail"] = "\n".join(lines[-40:])
        except OSError:
            pass
    return state


def _views(rows: list[dict]) -> list[dict]:
    views = []
    for r in rows:
        v = dict(r)
        try:
            v["reasons"] = json.loads(r.get("match_reasons") or "[]")
        except (json.JSONDecodeError, TypeError):
            v["reasons"] = []
        try:
            v["gaps"] = json.loads(r.get("gaps") or "[]")
        except (json.JSONDecodeError, TypeError):
            v["gaps"] = []
        v["desc_lines"] = (r.get("description") or "").splitlines()
        v["is_new"] = not r.get("app_status")
        v["hr_email"] = extract_hr_email(r.get("description") or "")
        views.append(v)
    return views


def create_app() -> Flask:
    app = Flask(__name__)
    # CSRF token: random per-process secret; embedded in POST forms and
    # verified on every state-changing request. Prevents drive-by cross-origin
    # POSTs from other tabs/websites to this localhost endpoint.
    import secrets
    app.config["SECRET_KEY"] = secrets.token_hex(32)

    @app.context_processor
    def _inject_csrf():
        from flask import session
        if "_csrf_token" not in session:
            session["_csrf_token"] = secrets.token_hex(16)
        return {"csrf_token": session["_csrf_token"]}

    @app.before_request
    def _check_csrf():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
            from flask import session
            expected = session.get("_csrf_token")
            if not expected or token != expected:
                abort(400, description="CSRF token missing or invalid")

    @app.route("/")
    def index():
        tab = request.args.get("tab", "jobs")
        filters = {
            "min_score": request.args.get("min_score", type=int, default=0),
            "source": request.args.get("source", ""),
            "status": request.args.get("status", ""),
            "q": request.args.get("q", "").strip(),
            "tab": tab,
        }
        rows = list_jobs(
            min_score=filters["min_score"],
            source=filters["source"],
            status=filters["status"],
            query=filters["q"],
        )
        
        timeframe = request.args.get("timeframe", "all")
        applied_jobs = []
        with _db() as conn:
            rows_applied = conn.execute(
                """SELECT j.job_id, j.title, j.company, j.location, j.url, j.score, j.source,
                          a.applied_at, a.notes, a.cover_letter, a.resume_bullets, a.status
                   FROM applications a JOIN jobs j ON j.job_id = a.job_id
                   WHERE a.status IN ('applied', 'submitted_unconfirmed')
                   ORDER BY a.applied_at DESC"""
            ).fetchall()
            applied_jobs = [_format_applied_row(dict(r)) for r in rows_applied]

        today_jobs = [a for a in applied_jobs if a.get("is_today")]
        session_stats = {
            "today_total": len(today_jobs),
            "today_linkedin": sum(1 for a in today_jobs if (a.get("source") or "").lower() == "linkedin"),
            "today_naukri": sum(1 for a in today_jobs if (a.get("source") or "").lower() == "naukri"),
            "today_indeed": sum(1 for a in today_jobs if (a.get("source") or "").lower() == "indeed"),
            "coldmail_today": get_sent_today_count(),
            "all_time_total": len(applied_jobs),
        }

        display_applied = today_jobs if timeframe == "today" else applied_jobs

        return render_template(
            "review.html",
            jobs=_views(rows),
            applied_jobs=display_applied,
            all_applied_count=len(applied_jobs),
            session_stats=session_stats,
            timeframe=timeframe,
            stats=get_stats(),
            filters=filters,
            sources=SOURCES,
            tab=tab,
            msg=request.args.get("msg", ""),
            automation=_automation_state(),
        )

    @app.route("/resume/<job_id>")
    def view_resume(job_id):
        job = get_job(job_id)
        if not job:
            abort(404)
        target = ROOT / "data" / "resumes" / resume_filename(job)
        if not target.exists():
            from apply.packet import build_packet
            try:
                pkt = build_packet(job_id)
                target = pkt.get("resume_file")
            except Exception:
                abort(404)
        if target and Path(target).exists():
            return send_file(str(target), mimetype="application/pdf", as_attachment=False)
        abort(404)

    def _back(extra: dict) -> str:
        """Redirect back to the list, preserving filters + optional message."""
        params = {
            k: request.args.get(k)
            for k in ("min_score", "source", "status", "q")
            if request.args.get(k)
        }
        params.update(extra)
        return url_for("index", **params)

    @app.route("/job/<job_id>/prep", methods=["POST"])
    def prep(job_id):
        job = get_job(job_id)
        if not job:
            abort(404)
        desc = job.get("description") or fetch_description(job.get("url") or "")
        if desc and not job.get("description"):
            set_job_description(job_id, desc)  # persist for future scoring/prep
        reasons = json.loads(job.get("match_reasons") or "[]")
        letter = tailor_cover_letter(job["title"], job["company"], desc, reasons)
        bullets = tailor_resume_bullets(job["title"], desc)
        save_application(job_id, letter, bullets, status="ready")
        return redirect(_back({"msg": f"Prepped: {job['title']} @ {job['company']}"}))

    @app.route("/job/<job_id>/applied", methods=["POST"])
    def applied(job_id):
        job = get_job(job_id)
        ok = mark_applied(job_id)
        title = job["title"] if job else job_id
        msg = f"Marked {title} as applied." if ok else f"No ready application for {title} — prep it first."
        return redirect(_back({"msg": msg}))

    @app.route("/job/<job_id>/apply-browser", methods=["POST"])
    def apply_browser(job_id):
        """Spawn `main.py apply <id> --mode fill` in the background — the
        autofill browser opens on this machine; gate mode waits for close."""
        import subprocess
        import sys as _sys

        job = get_job(job_id)
        if not job:
            abort(404)
        log_file = ROOT / "logs" / f"apply-{job_id}.log"
        with open(log_file, "ab") as fh:
            subprocess.Popen(
                [_sys.executable, str(ROOT / "main.py"), "apply", job_id, "--mode", "fill"],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=fh,
                stderr=fh,
                start_new_session=True,
            )
        return redirect(_back({
            "msg": (
                f"Browser opening for: {job['title']} — form autofill hoga. "
                "Review, CAPTCHA do, Submit karo, browser band karo, phir 'Mark applied' dabao. "
                f"Log: logs/apply-{job_id}.log"
            )
        }))

    @app.route("/job/<job_id>/apply-email", methods=["POST"])
    def apply_email(job_id):
        """Compose + send the application by email (marks applied on success)."""
        job = get_job(job_id)
        if not job:
            abort(404)
        try:
            from apply.emailer import send_application
            from apply.packet import build_packet

            packet = build_packet(job_id)
            msg = send_application(packet)
        except Exception as e:
            msg = f"Email apply failed: {e}"
        return redirect(_back({"msg": msg}))

    @app.route("/automation/start", methods=["POST"])
    def automation_start():
        """One-click full cycle: search -> score -> prep -> auto-apply -> digest (email)."""
        if _automation_state()["running"]:
            return redirect(_back({"msg": "Automation already running — see status below."}))
        import subprocess
        import sys as _sys
        from datetime import datetime

        log_file = ROOT / "logs" / f"automation-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
        log_fh = open(log_file, "ab")
        proc = subprocess.Popen(
            [_sys.executable, str(ROOT / "main.py"), "run", "--auto-apply", "--playwright"],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        log_fh.close()  # child holds its own dup; safe to close parent copy
        _AUTOMATION.update(
            proc=proc, log_file=log_file,
            started=datetime.now().isoformat(timespec="seconds"),
        )
        return redirect(_back({
            "msg": (
                "Automation started: search → score → prep → auto-apply (up to "
                "AUTO_APPLY_LIMIT) → digest email. Progress updates below; "
                f"log: logs/{log_file.name}"
            )
        }))

    @app.route("/automation/status")
    def automation_status():
        return jsonify(_automation_state())

    return app


def main():
    parser = argparse.ArgumentParser(description="UAE Job Copilot review UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    print(f"Review UI: http://{args.host}:{args.port}")
    create_app().run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
