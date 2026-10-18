"""Generate a dense, ATS-friendly one-page resume PDF tailored to a job.

Content source: config/profile.py (structured experience/projects/education).
ATS design rules: single column, standard font, real text (no tables/graphics),
keyword-rich headings, metric-heavy bullets. Output: data/resumes/<job_id>.pdf
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.profile import PROFILE  # noqa: E402

from reportlab.lib.colors import HexColor  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

ACCENT = HexColor("#1a3a5c")
GREY = HexColor("#444444")
LIGHT = HexColor("#666666")

STYLES = {
    "name": ParagraphStyle("name", fontName="Helvetica-Bold", fontSize=16, leading=18, textColor=ACCENT, spaceAfter=0),
    "headline": ParagraphStyle("headline", fontName="Helvetica", fontSize=9, leading=11, textColor=GREY, spaceAfter=1),
    "contact": ParagraphStyle("contact", fontName="Helvetica", fontSize=8.8, leading=10.6, textColor=GREY, spaceAfter=3),
    "section": ParagraphStyle("section", fontName="Helvetica-Bold", fontSize=9.8, leading=11.5, textColor=ACCENT, spaceBefore=6, spaceAfter=1.5),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9, leading=11, spaceAfter=1.5),
    "role": ParagraphStyle("role", fontName="Helvetica-Bold", fontSize=9.2, leading=11, spaceBefore=2.5, spaceAfter=0.5),
    "rolemeta": ParagraphStyle("rolemeta", fontName="Helvetica-Oblique", fontSize=8.6, leading=10.4, textColor=LIGHT, spaceAfter=0.5),
    "bullet": ParagraphStyle("bullet", fontName="Helvetica", fontSize=8.9, leading=10.9, leftIndent=9, bulletIndent=1, spaceAfter=1),
    "skills": ParagraphStyle("skills", fontName="Helvetica", fontSize=8.8, leading=10.8, spaceAfter=1),
    "foot": ParagraphStyle("foot", fontName="Helvetica", fontSize=8.4, leading=10.4, textColor=GREY),
}
BULLET = "-"  # plain ASCII: every ATS extracts it cleanly (fancy bullets -> (cid:..) garbage)


def _bullets(text: str, max_items: int = 6) -> list[str]:
    items = []
    for line in (text or "").splitlines():
        line = line.strip().lstrip("•-*–").strip()
        if line:
            items.append(line)
    return items[:max_items]


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _match_keywords(job: dict) -> list[str]:
    """Profile skills that appear in the job text (for a Core Competencies line)."""
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    matched: list[str] = []
    for items in PROFILE.get("skills", {}).values():
        for skill in items:
            # match on the distinctive part of the skill name
            tokens = [t for t in re.split(r"[(),/]| \+ ", skill) if len(t) > 2]
            if any(t.lower() in text for t in tokens) and skill not in matched:
                matched.append(skill)
    return matched[:14]


def generate_resume_pdf(job: dict, resume_bullets: str, out_path: Path, region: str = "gulf") -> Path:
    """Generate resume PDF.
    
    Args:
        region: 'india' for India-focused resume, 'gulf' for UAE/Gulf resume
    """
    p = PROFILE
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=13 * mm,
        rightMargin=13 * mm,
        topMargin=11 * mm,
        bottomMargin=10 * mm,
        title=f"{p['name']} — Resume",
        author=p["name"],
    )
    story = []

    # ---- header ----
    story.append(Paragraph(_esc(p["name"]), STYLES["name"]))
    story.append(Paragraph(_esc(p.get("headline", "")), STYLES["headline"]))
    contact = " | ".join(
        x for x in [p["email"], p["phone"], p["location"], p.get("linkedin", ""), p.get("github", "")] if x
    )
    story.append(Paragraph(_esc(contact), STYLES["contact"]))
    story.append(HRFlowable(width="100%", thickness=0.9, color=ACCENT, spaceAfter=2))

    # ---- summary (region-aware) ----
    story.append(Paragraph("PROFESSIONAL SUMMARY", STYLES["section"]))
    summary = p["summary"]
    if region == "india":
        # Remove UAE-specific text for India version
        summary = summary.replace(" Open to relocate to UAE.", "")
        summary += " Open to opportunities across India (Bangalore, Hyderabad, Pune, Delhi-NCR, Mumbai) and remote."
    story.append(Paragraph(_esc(summary), STYLES["body"]))

    # ---- skills + core competencies (real skills matched to this job) ----
    story.append(Paragraph("TECHNICAL SKILLS", STYLES["section"]))
    for cat, items in p.get("skills", {}).items():
        story.append(Paragraph(f"<b>{_esc(cat)}:</b> {_esc(', '.join(items))}", STYLES["skills"]))
    matches = _match_keywords(job or {})
    if matches:
        story.append(
            Paragraph("<b>Core Competencies:</b> " + _esc(", ".join(matches)), STYLES["skills"])
        )

    # ---- experience ----
    story.append(Paragraph("WORK EXPERIENCE", STYLES["section"]))
    for i, e in enumerate(p.get("experience", [])):
        dates = e.get("dates", "")
        story.append(
            Paragraph(
                f"{_esc(e['title'])} — {_esc(e['company'])}, {_esc(e.get('location', ''))}"
                f"{' &nbsp;|&nbsp; ' + _esc(dates) if dates else ''}",
                STYLES["role"],
            )
        )
        max_b = 4 if i == 0 else 2  # recent role full set; older roles trimmed to keep one page
        for b in e.get("bullets", [])[:max_b]:
            story.append(Paragraph(_esc(b), STYLES["bullet"], bulletText=BULLET))

    # ---- tailored section (job-specific) ----
    tailored = _bullets(resume_bullets, max_items=3)
    if tailored:
        title = (job.get("title", "") if isinstance(job, dict) else getattr(job, "title", "")) or "TARGET ROLE"
        story.append(Paragraph(f"RELEVANT FOR THIS ROLE — {_esc(title.upper())}", STYLES["section"]))
        for b in tailored:
            story.append(Paragraph(_esc(b), STYLES["bullet"], bulletText=BULLET))

    # ---- projects ----
    story.append(Paragraph("SELECTED PRODUCTION PROJECTS", STYLES["section"]))
    for proj in p.get("projects", [])[:2]:
        story.append(
            Paragraph(f"<b>{_esc(proj['name'])}</b> | {_esc(proj.get('stack', ''))}", STYLES["role"])
        )
        for b in proj.get("bullets", [])[:2]:
            story.append(Paragraph(_esc(b), STYLES["bullet"], bulletText=BULLET))

    # ---- education & certifications ----
    story.append(Paragraph("EDUCATION & CERTIFICATIONS", STYLES["section"]))
    for e in p.get("education", []):
        line = f"<b>{_esc(e['degree'])}</b> — {_esc(e['school'])}, {_esc(e.get('dates', ''))}"
        if e.get("detail"):
            line += f" ({_esc(e['detail'])})"
        story.append(Paragraph(line, STYLES["body"]))
    if p.get("certifications"):
        story.append(Paragraph("<b>Certifications:</b> " + _esc(" · ".join(p["certifications"])), STYLES["body"]))

    # ---- footer meta (region-aware) ----
    if region == "india":
        meta_bits = [
            f"Nationality: {p.get('nationality', '')}" if p.get("nationality") else "",
            f"Languages: {p.get('languages', '')}" if p.get("languages") else "",
            f"Notice period: {p.get('notice_period', '')}",
            "Open to: India (Bangalore, Hyderabad, Pune, Delhi-NCR, Mumbai) / Remote",
        ]
    else:
        meta_bits = [
            f"Nationality: {p.get('nationality', '')}" if p.get("nationality") else "",
            f"Languages: {p.get('languages', '')}" if p.get("languages") else "",
            f"Notice period: {p.get('notice_period', '')}",
            f"Visa: {p.get('visa_status', '')}",
            f"Open to relocate: {p.get('open_to', '')}",
        ]
    story.append(Spacer(1, 3))
    story.append(HRFlowable(width="100%", thickness=0.5, color=GREY, spaceBefore=1, spaceAfter=2))
    story.append(Paragraph(_esc(" | ".join(x for x in meta_bits if x)), STYLES["foot"]))

    doc.build(story)
    return out_path
