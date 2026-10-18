"""Daily digest notification — prints markdown summary + saves to file."""
import json
import logging
from datetime import date
from pathlib import Path

log = logging.getLogger("notify")

DIGEST_DIR = Path(__file__).parent.parent / "data" / "digests"
DIGEST_DIR.mkdir(parents=True, exist_ok=True)


def build_digest(
    top_jobs: list[dict],
    ready_apps: list[dict],
    stats: dict,
    applied_recent: list[dict] | None = None,
    run_results: dict | None = None,
) -> str:
    """Build the daily digest markdown.

    run_results: dict with keys:
        - submitted: list of {title, company, url, source}
        - unconfirmed: list of same
        - failed: list of {title, company, url, error}
        - skipped: list of {title, company, reason}
    """
    today = date.today().isoformat()
    applied_recent = applied_recent or []
    run_results = run_results or {}

    lines = [
        f"# UAE Job Digest — {today}",
        "",
        f"**Stats:** {stats.get('total_jobs',0)} jobs found | "
        f"{stats.get('must_apply',0)} must-apply | "
        f"{stats.get('ready',0)} ready to submit | "
        f"{stats.get('applied',0)} applied total | "
        f"{len(applied_recent)} applied in last 24h",
        "",
    ]

    # Run results section (if this run did auto-apply)
    submitted = run_results.get("submitted", [])
    unconfirmed = run_results.get("unconfirmed", [])
    failed = run_results.get("failed", [])
    skipped = run_results.get("skipped", [])

    if submitted or unconfirmed or failed:
        lines.append("## This Run — Auto-Apply Results")
        lines.append("")

        if submitted:
            lines.append(f"### ✅ Submitted ({len(submitted)})")
            lines.append("")
            for s in submitted:
                lines.append(f"- [x] **{s.get('company','')}** — {s.get('title','')}")
                if s.get("url"):
                    lines.append(f"  - {s['url']}")
            lines.append("")

        if unconfirmed:
            lines.append(f"### ⚠️ Submitted but Unconfirmed ({len(unconfirmed)}) — verify manually")
            lines.append("")
            for s in unconfirmed:
                lines.append(f"- [~] **{s.get('company','')}** — {s.get('title','')}")
                if s.get("url"):
                    lines.append(f"  - {s['url']}")
            lines.append("")

        if failed:
            lines.append(f"### ❌ Failed ({len(failed)}) — left ready for retry")
            lines.append("")
            for f in failed:
                lines.append(f"- [ ] **{f.get('company','')}** — {f.get('title','')}")
                if f.get("error"):
                    lines.append(f"  - Error: {f['error']}")
                if f.get("url"):
                    lines.append(f"  - {f['url']}")
            lines.append("")

        if skipped:
            lines.append(f"### ⏭️ Skipped ({len(skipped)})")
            lines.append("")
            for s in skipped:
                lines.append(f"- **{s.get('company','')}** — {s.get('title','')}: {s.get('reason','')}")
            lines.append("")

    if applied_recent:
        lines.append("## Applied Today (last 24h)")
        lines.append("")
        for a in applied_recent:
            lines.append(
                f"- [x] {a['company']} — {a['title']} (score {a['score']}, via {a['source']})"
            )
            lines.append(f"  - {a['url']}")
        lines.append("")

    # Outcomes / feedback loop (replies, interviews, offers)
    oc = stats.get("outcomes") or {}
    if oc.get("applied"):
        lines.append("## 📬 Outcomes (feedback loop)")
        lines.append("")
        lines.append(
            f"- Response rate: **{oc.get('response_rate', 0)}%** "
            f"({oc.get('responded', 0)} of {oc.get('applied', 0)} applied)"
        )
        counts = oc.get("counts", {})
        bits = [
            f"{counts.get(k, 0)} {k}"
            for k in ("replied", "interview", "offer", "rejected")
            if counts.get(k)
        ]
        if bits:
            lines.append(f"- {' | '.join(bits)}")
        for s in oc.get("by_source", []):
            if s.get("applied"):
                rate = round(100 * s["responded"] / s["applied"], 1)
                lines.append(f"- {s['source']}: {s['responded']}/{s['applied']} responded ({rate}%)")
        lines.append("- Update: `python3 main.py outcome <job_id> <replied|interview|offer|rejected>`")
        lines.append("")

    lines.append("## Top Matches (score >= 60)")
    lines.append("")
    if not top_jobs:
        lines.append("_No high-match jobs today. Try broadening search queries._")
    for j in top_jobs:
        try:
            reasons = json.loads(j.get("match_reasons") or "[]")
        except (json.JSONDecodeError, TypeError):
            reasons = []
        reason_str = "; ".join(reasons[:2])
        lines.append(f"### {j['score']} — {j['title']}")
        lines.append(f"- **Company:** {j['company']} | {j['location']} | via {j['source']}")
        lines.append(f"- **Why:** {reason_str}")
        lines.append(f"- **Link:** {j['url']}")
        lines.append("")

    if ready_apps:
        lines.append("## Ready to Submit (cover letter + bullets prepped)")
        lines.append("")
        for a in ready_apps:
            lines.append(f"- [ ] [{a['title']}]({a['url']}) — {a['company']} (score {a['score']})")
        lines.append("")

    lines.append("---")
    lines.append(
        "_Auto-submissions are marked applied automatically. "
        "Review the rest in the UI: `python3 main.py serve`._"
    )
    return "\n".join(lines)


def save_digest(content: str) -> Path:
    today = date.today().isoformat()
    path = DIGEST_DIR / f"digest-{today}.md"
    path.write_text(content, encoding="utf-8")
    log.info("Digest saved: %s", path)
    return path
