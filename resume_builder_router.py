import json
import logging
import os

import requests
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse

import models
import security as auth


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/resume-builder", tags=["resume-builder"])


PROMPT = """You are an expert recruiter and resume strategist.

Return ONLY valid JSON. Do not include markdown, commentary, or text outside the JSON.

TARGET ROLE
Job title: {job_title}
Seniority: {seniority}
Industry: {industry}
Target company/context: {target_company}

CANDIDATE BACKGROUND OR CURRENT RESUME
{resume_text}

Return this exact JSON shape:
{{
  "job_requirements": {{
    "role_summary": "<2 sentence role overview>",
    "must_have_skills": ["<skill>", "<skill>", "<skill>", "<skill>", "<skill>"],
    "preferred_skills": ["<skill>", "<skill>", "<skill>", "<skill>"],
    "core_responsibilities": ["<responsibility>", "<responsibility>", "<responsibility>", "<responsibility>", "<responsibility>"],
    "tools_and_keywords": ["<keyword>", "<keyword>", "<keyword>", "<keyword>", "<keyword>", "<keyword>"],
    "experience_expectations": "<expected years, domain depth, and seniority signals>",
    "education_or_certifications": ["<education/certification>", "<education/certification>"]
  }},
  "resume": {{
    "headline": "<targeted resume headline>",
    "professional_summary": "<3-4 sentence ATS-friendly summary>",
    "skills": ["<skill>", "<skill>", "<skill>", "<skill>", "<skill>", "<skill>", "<skill>", "<skill>"],
    "experience_bullets": ["<achievement bullet>", "<achievement bullet>", "<achievement bullet>", "<achievement bullet>", "<achievement bullet>", "<achievement bullet>"],
    "project_ideas": ["<project idea>", "<project idea>", "<project idea>"],
    "keyword_gaps": ["<missing keyword or evidence>", "<missing keyword or evidence>", "<missing keyword or evidence>"],
    "rewrite_advice": ["<specific improvement>", "<specific improvement>", "<specific improvement>"]
  }}
}}

Use truthful, non-fabricated phrasing. If the candidate background lacks evidence, phrase bullets as editable drafts and identify the missing proof in keyword_gaps."""


def _strip_json_fence(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return raw


def _chat_json(prompt: str) -> dict:
    groq_base = (os.getenv("GROQ_API_BASE") or "").strip()
    groq_key = (os.getenv("GROQ_API_KEY") or "").strip()

    if groq_base and groq_key:
        base = groq_base.rstrip("/")
        if base.endswith("/models"):
            base = base.rsplit("/models", 1)[0]
        endpoint = f"{base}/chat/completions"
        payload = {
            "model": os.getenv("GROQ_MODEL_NAME") or "llama-3.1-8b-instant",
            "messages": [
                {"role": "system", "content": "You return precise recruiting and resume-building JSON."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.25,
        }
        response = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        if response.status_code == 401:
            raise RuntimeError("Groq authentication failed. Check GROQ_API_KEY.")
        if response.status_code >= 400:
            raise RuntimeError(f"Groq request failed: {response.status_code} - {response.text}")
        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(_strip_json_fence(content))

    openai_key = (os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not openai_key:
        raise RuntimeError("No AI provider configured. Set GROQ_API_KEY/GROQ_API_BASE or OPENAI_API_KEY.")

    try:
        from openai import OpenAI
    except ModuleNotFoundError as exc:
        raise RuntimeError("OpenAI SDK is not installed. Install project requirements.") from exc

    api_base = os.getenv("OPENAI_API_BASE") or os.getenv("DEEPSEEK_API_BASE")
    client = OpenAI(api_key=openai_key, base_url=api_base) if api_base else OpenAI(api_key=openai_key)
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL_NAME") or os.getenv("DEEPSEEK_MODEL_NAME") or "gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You return precise recruiting and resume-building JSON."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.25,
    )
    content = response.choices[0].message.content
    return json.loads(_strip_json_fence(content))


@router.post("/generate")
async def generate_resume_builder(
    request: Request,
    job_title: str = Form(...),
    seniority: str = Form(default="Mid-level"),
    industry: str = Form(default=""),
    target_company: str = Form(default=""),
    resume_text: str = Form(default=""),
    csrf_token: str = Form(...),
    current_user: models.User = Depends(auth.get_current_user),
):
    import main as _main

    _main.require_csrf(request, csrf_token)

    job_title = (job_title or "").strip()
    seniority = (seniority or "").strip() or "Mid-level"
    industry = (industry or "").strip() or "General"
    target_company = (target_company or "").strip() or "Not specified"
    resume_text = (resume_text or "").strip()

    if not job_title:
        raise HTTPException(400, "Job title is required.")
    if len(job_title) > 120:
        raise HTTPException(400, "Job title is too long.")
    if len(resume_text) > 12000:
        raise HTTPException(400, "Resume/background text is too long. Keep it under 12,000 characters.")

    prompt = PROMPT.format(
        job_title=job_title,
        seniority=seniority,
        industry=industry,
        target_company=target_company,
        resume_text=resume_text[:12000] or "No candidate background provided.",
    )

    try:
        result = _chat_json(prompt)
    except json.JSONDecodeError:
        logger.exception("Resume builder returned invalid JSON for user %s", current_user.email)
        raise HTTPException(502, "The AI returned an unexpected response. Please try again.")
    except Exception as exc:
        logger.exception("Resume builder failed for user %s", current_user.email)
        raise HTTPException(502, str(exc))

    return JSONResponse({"success": True, "result": result})
