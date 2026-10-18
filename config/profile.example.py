"""Template candidate profile — copy to profile.py and fill in your details.

profile.py is gitignored so your real name/phone/email never leave this machine.
"""
PROFILE = {
    "name": "Your Name",
    "headline": "AI Engineer | LLM · RAG · Agentic Systems",
    "email": "you@example.com",
    "phone": "+971-5X-XXX-XXXX",
    "location": "Dubai, UAE",
    "linkedin": "",  # your LinkedIn URL (printed on generated resumes)
    "github": "github.com/yourusername",
    "nationality": "Your nationality",
    "languages": "English (fluent)",
    "open_to": "UAE (Dubai, Abu Dhabi, Sharjah)",
    "target_roles": [
        "AI Engineer",
        "ML Engineer",
        "LLM Engineer",
    ],
    "target_industries": ["AI/ML", "SaaS", "FinTech"],
    "must_have_keywords": [
        "AI", "ML", "LLM", "RAG", "Python", "FastAPI",
    ],
    "salary_expectation_aed": "25000-45000 AED/month",
    "visa_status": "Needs sponsorship",  # or "Authorized to work in UAE"
    "notice_period": "Immediate to 30 days",
    "summary": (
        "One-paragraph professional summary used on generated resumes and cover letters."
    ),
    "skills": {
        "Agentic AI & LLMs": ["LangGraph", "RAG", "Prompt Engineering"],
        "Backend & Production": ["Python", "FastAPI", "REST APIs"],
        "Data & Retrieval": ["PostgreSQL", "SQL", "Pandas"],
        "Cloud & DevOps": ["AWS", "Docker", "CI/CD"],
    },
    "experience": [
        {
            "title": "Your Title",
            "company": "Company Name",
            "location": "City, Country",
            "dates": "Jan 2023 – Present",
            "bullets": [
                "Achieved X metric by doing Y, resulting in Z impact.",
            ],
        },
    ],
    "projects": [
        {
            "name": "Project Name",
            "stack": "Python · FastAPI · LangGraph",
            "bullets": [
                "What it does and the measurable outcome.",
            ],
        },
    ],
    "education": [
        {
            "degree": "Degree Name",
            "school": "University",
            "dates": "2020 – 2024",
            "detail": "",
        },
    ],
    "certifications": [
        "Relevant Certification",
    ],
    "highlights": [
        "One-line measurable highlight #1",
        "One-line measurable highlight #2",
    ],
    # Best-effort answers for application-form questions (pattern -> answer).
    # First matching pattern wins; unmatched required questions abort the auto-apply.
    "form_answers": [
        ("authorized to work", "No — will require sponsorship"),
        ("visa", "Needs sponsorship"),
        ("relocat", "Yes"),
        ("notice period", "Immediate to 30 days"),
        ("how many years", "3"),
        ("years of experience", "3"),
        ("programming language", "Python"),
        ("salary", "35000"),
        ("gender", "Prefer not to say"),
    ],
}
