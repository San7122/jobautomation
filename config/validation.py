"""Startup configuration validation.

Call `validate_config()` at the top of `main()` (or in `load_env()`) so the
user gets a clear, actionable list of problems before the run starts instead of
cryptic failures mid-flight.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ConfigIssue:
    level: str  # "error" | "warn" | "info"
    key: str
    message: str


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def validate_config() -> list[ConfigIssue]:
    """Return a list of config problems (never raises)."""
    issues: list[ConfigIssue] = []

    # --- AI: at least one provider should be configured (or scoring fails) ---
    has_claude = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_openai = bool(os.environ.get("OPENAI_API_KEY"))
    has_ollama = bool(os.environ.get("OLLAMA_URL"))
    if not (has_claude or has_openai or has_ollama):
        issues.append(
            ConfigIssue(
                "warn",
                "AI_PROVIDER",
                "No LLM provider configured (ANTHROPIC_API_KEY / OPENAI_API_KEY / OLLAMA_URL). "
                "Scoring and cover letters will fail. Set one in .env.",
            )
        )

    # --- Notifications: at least one channel configured for digest delivery ---
    has_tg = bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))
    has_smtp = bool(os.environ.get("SMTP_HOST"))
    has_slack = bool(os.environ.get("SLACK_WEBHOOK_URL"))
    if not (has_tg or has_smtp or has_slack):
        issues.append(
            ConfigIssue(
                "info",
                "NOTIFICATIONS",
                "No digest channel configured (TELEGRAM_*, SMTP_*, or SLACK_WEBHOOK_URL). "
                "Digest will only be saved to data/digests/.",
            )
        )

    # --- Auto-apply sanity ---
    if _truthy(os.environ.get("AUTO_APPLY", "")):
        limit = os.environ.get("AUTO_APPLY_LIMIT", "3")
        try:
            lim = int(limit)
            if lim < 1:
                issues.append(ConfigIssue("error", "AUTO_APPLY_LIMIT", f"AUTO_APPLY_LIMIT must be >= 1, got {limit}"))
        except ValueError:
            issues.append(ConfigIssue("error", "AUTO_APPLY_LIMIT", f"AUTO_APPLY_LIMIT must be an integer, got {limit}"))

        if not (has_claude or has_openai or has_ollama):
            issues.append(
                ConfigIssue(
                    "error",
                    "AUTO_APPLY_WITHOUT_AI",
                    "AUTO_APPLY=1 but no AI provider configured — cover letters cannot be generated.",
                )
            )

    # --- .env presence hint ---
    if not (ROOT / ".env").exists():
        issues.append(
            ConfigIssue(
                "info",
                "ENV_FILE",
                ".env not found — copy .env.example to .env and fill in your keys.",
            )
        )

    # --- Resume hint for apply flows ---
    resume_path = os.environ.get("RESUME_PATH", "")
    data_dir = ROOT / "data"
    has_resume = bool(resume_path and Path(resume_path).exists()) or any(
        data_dir.glob("resume.*")
    )
    if not has_resume:
        issues.append(
            ConfigIssue(
                "info",
                "RESUME",
                "No resume file found (set RESUME_PATH or drop resume.pdf in data/). "
                "Generated PDFs will be used instead.",
            )
        )

    return issues


def print_config_report(issues: list[ConfigIssue] | None = None) -> int:
    """Pretty-print the validation report. Returns exit code (1 if any errors)."""
    if issues is None:
        issues = validate_config()

    errors = [i for i in issues if i.level == "error"]
    warns = [i for i in issues if i.level == "warn"]
    infos = [i for i in issues if i.level == "info"]

    if not issues:
        print("✅ Config looks good — no issues found.")
        return 0

    if errors:
        print("\n❌ CONFIG ERRORS (fix before running):")
        for i in errors:
            print(f"   • [{i.key}] {i.message}")
    if warns:
        print("\n⚠️  CONFIG WARNINGS:")
        for i in warns:
            print(f"   • [{i.key}] {i.message}")
    if infos:
        print("\nℹ️  CONFIG INFO:")
        for i in infos:
            print(f"   • [{i.key}] {i.message}")

    print()
    return 1 if errors else 0
