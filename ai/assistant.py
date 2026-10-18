"""AI scoring, resume tailoring, and cover letter generation.

Priority: Claude API > OpenAI API > local Ollama.
"""
import json
import logging
import os

log = logging.getLogger("ai")

try:
    import anthropic
    _anthropic_available = True
except ImportError:
    _anthropic_available = False

try:
    from openai import OpenAI
    _openai_available = True
except ImportError:
    _openai_available = False

try:
    import requests as _requests
except ImportError:
    _requests = None

from config.profile import PROFILE

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")


def _call_claude(system: str, user: str, max_tokens: int = 2000) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key or not _anthropic_available:
        raise RuntimeError("Claude not available")
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text


def _call_openai(system: str, user: str, max_tokens: int = 2000) -> str:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not _openai_available:
        raise RuntimeError("OpenAI not available")
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=max_tokens,
        temperature=0.3,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content


def _call_ollama(system: str, user: str, max_tokens: int = 2000) -> str:
    if _requests is None:
        raise RuntimeError("requests not installed")
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "options": {"num_predict": max_tokens, "temperature": 0.3},
    }
    resp = _requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=180)
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def _call_gemini(system: str, user: str, max_tokens: int = 2000) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    if _requests is None:
        raise RuntimeError("requests not installed")
        
    models_to_try = [
        ("v1beta", "gemini-flash-lite-latest"),
        ("v1", "gemini-3.6-flash"),
        ("v1beta", "gemini-3-flash-preview"),
        ("v1", "gemini-3.5-flash"),
        ("v1", "gemini-3.7-flash"),
        ("v1", "gemini-3.8-flash")
    ]
    prompt = f"{system}\n\n{user}" if system else user
    generation_config = {"maxOutputTokens": max_tokens}
    if "json" in prompt.lower() or "json" in system.lower():
        generation_config["responseMimeType"] = "application/json"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": generation_config
    }
    headers = {"Content-Type": "application/json"}
    
    for api_version, model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/{api_version}/models/{model}:generateContent?key={api_key}"
        try:
            resp = _requests.post(url, headers=headers, json=payload, timeout=25)
            if resp.status_code == 200:
                res_json = resp.json()
                return res_json["candidates"][0]["content"]["parts"][0]["text"]
            elif resp.status_code in [404, 503, 429]:
                continue
            else:
                log.warning("Gemini %s failed with %s: %s", model, resp.status_code, resp.text)
        except Exception:
            continue
    raise RuntimeError("All Gemini model fallback attempts failed")


def _call(system: str, user: str, max_tokens: int = 2000) -> str:
    errors = []
    for name, fn in [("Gemini", _call_gemini), ("Claude", _call_claude), ("OpenAI", _call_openai), ("Ollama", _call_ollama)]:
        try:
            return fn(system, user, max_tokens)
        except Exception as e:
            errors.append(f"{name}: {e}")
            log.info("%s failed: %s", name, e)
    raise RuntimeError("All providers failed: " + " | ".join(errors))


def score_job(title: str, company: str, description: str) -> dict:
    """Score a job 0-100 against the candidate profile."""
    profile_text = json.dumps(PROFILE, indent=2)
    prompt = f"""Score this job match for the candidate. Return ONLY valid JSON.

CANDIDATE PROFILE:
{profile_text}

JOB:
Title: {title}
Company: {company}
Description: {description[:3000]}

Return JSON with:
- "score": integer 0-100 (how well candidate matches)
- "match_reasons": list of 2-3 strings explaining strong matches
- "gaps": list of 0-2 strings for missing requirements (empty if none)
- "must_apply": boolean (true if score >= 70)
"""
    try:
        text = _call(
            "You are a strict job-match scorer. Return only valid JSON, no markdown fences.",
            prompt,
            max_tokens=1500,
        )
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        import re
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return json.loads(text)
    except Exception as e:
        log.error("Score failed: %s", e)
        return {"score": 0, "match_reasons": [], "gaps": ["scoring_error"], "must_apply": False}


def tailor_cover_letter(title: str, company: str, description: str, score_reasons: list[str]) -> str:
    """Generate a tailored cover letter."""
    profile_text = json.dumps(PROFILE, indent=2)
    prompt = f"""Write a concise, professional cover letter (max 350 words) for this job.

CANDIDATE:
{profile_text}

JOB:
Title: {title}
Company: {company}
Description: {description[:2500]}

MATCH REASONS TO HIGHLIGHT:
{json.dumps(score_reasons)}

Requirements:
- Address the hiring team
- Open with strong hook about AI/LLM production experience
- Connect 2-3 specific achievements to their needs
- Mention UAE relocation readiness
- Confident but not arrogant closing
- No placeholders like [Company] — use the real company name
- Plain text, ready to paste into an application form
"""
    try:
        return _call(
            "You write sharp, specific cover letters. No clichés. No markdown.",
            prompt,
            max_tokens=800,
        )
    except Exception as e:
        log.error("Cover letter failed: %s", e)
        return f"Cover letter generation failed: {e}"


def tailor_resume_bullets(title: str, description: str) -> str:
    """Generate 3-5 resume bullet points tailored to the job."""
    profile_text = json.dumps(PROFILE, indent=2)
    prompt = f"""Given this candidate profile and job description, write 3-5 RESUME BULLET POINTS
(achievement-style, with metrics) that best match this specific job. Use the candidate's
real experience only — never invent. Return plain text, one bullet per line, starting with "•".

CANDIDATE:
{profile_text}

JOB TITLE: {title}
JOB DESCRIPTION:
{description[:2000]}
"""
    try:
        return _call(
            "You tailor resumes ethically — only rephrase real experience, never fabricate.",
            prompt,
            max_tokens=600,
        )
    except Exception as e:
        log.error("Resume tailoring failed: %s", e)
        return ""
