import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

import models
import security as auth
from database import get_db
from resume_builder_router import PROMPT as RESUME_BUILDER_PROMPT
from resume_builder_router import _chat_json
from screen_router import extract_text, screen_with_openai


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webmcp", tags=["webmcp"])


class AnalyzeResumeInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    resume_text: str | None = Field(default=None, max_length=12000)
    job_description: str | None = Field(default=None, validation_alias=AliasChoices("jobDescription", "job_description"), max_length=20000)
    candidate_name: str | None = Field(default=None, validation_alias=AliasChoices("candidateName", "candidate_name"), max_length=120)


class MatchResumeInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    job_title: str = Field(..., validation_alias=AliasChoices("jobTitle", "job_title"), min_length=1, max_length=120)
    job_description: str = Field(..., validation_alias=AliasChoices("jobDescription", "job_description"), min_length=20, max_length=20000)
    required_skills: list[str] = Field(default_factory=list, validation_alias=AliasChoices("requiredSkills", "required_skills"), max_length=40)
    resume_text: str | None = Field(default=None, max_length=12000)
    candidate_name: str | None = Field(default=None, validation_alias=AliasChoices("candidateName", "candidate_name"), max_length=120)


class ImproveResumeInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    resume_text: str | None = Field(default=None, max_length=12000)
    target_role: str = Field(default="Target role", validation_alias=AliasChoices("targetRole", "target_role"), max_length=120)
    instructions: str | None = Field(default=None, max_length=2000)
    seniority: str = Field(default="Mid-level", max_length=60)
    industry: str = Field(default="General", max_length=120)
    job_description: str | None = Field(default=None, validation_alias=AliasChoices("targetJobDescription", "jobDescription", "job_description"), max_length=20000)


class CoverLetterInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    job_title: str = Field(..., validation_alias=AliasChoices("jobTitle", "job_title"), min_length=1, max_length=120)
    company_name: str | None = Field(default=None, validation_alias=AliasChoices("company", "companyName", "company_name"), max_length=120)
    job_description: str = Field(..., validation_alias=AliasChoices("jobDescription", "job_description"), min_length=20, max_length=20000)
    resume_text: str | None = Field(default=None, max_length=12000)
    tone: str = Field(default="professional", max_length=60)


class SkillGapInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    target_role: str = Field(..., validation_alias=AliasChoices("targetRole", "target_role"), min_length=1, max_length=120)
    required_skills: list[str] = Field(..., validation_alias=AliasChoices("requiredSkills", "required_skills"), min_length=1, max_length=60)
    resume_text: str | None = Field(default=None, max_length=12000)


def _ai_key() -> str:
    return (
        os.getenv("OPENAI_API_KEY", "").strip()
        or os.getenv("DEEPSEEK_API_KEY", "").strip()
        or os.getenv("GROQ_API_KEY", "").strip()
        or os.getenv("GEMINI_API_KEY", "").strip()
    )


def _latest_candidate(db: Session, user_id: int) -> models.Candidate | None:
    return (
        db.query(models.Candidate)
        .join(models.Screening)
        .filter(models.Screening.user_id == user_id)
        .order_by(models.Candidate.created_at.desc())
        .first()
    )


def _candidate_resume_text(candidate: models.Candidate | None) -> str:
    if not candidate or not candidate.file_content_b64:
        return ""
    import base64

    file_bytes = base64.b64decode(candidate.file_content_b64)
    return extract_text(candidate.filename, file_bytes)


def _default_job_description(candidate: models.Candidate | None) -> str:
    if candidate and candidate.screening and candidate.screening.job:
        return candidate.screening.job.description or "General resume analysis"
    return "General resume analysis"


def _normalize_screening_result(result: dict[str, Any]) -> dict[str, Any]:
    scores = result.get("scores") or {}
    return {
        "score": result.get("overall_score"),
        "recommendation": result.get("recommendation"),
        "summary": result.get("executive_summary"),
        "strengths": result.get("strengths") or [],
        "weaknesses": result.get("gaps") or [],
        "missing_skills": result.get("missing_skills") or result.get("gaps") or [],
        "keywords": result.get("key_skills") or [],
        "recommendations": result.get("rewrite_advice") or result.get("interview_questions") or [],
        "scores": scores,
        "candidate": {
            "name": result.get("candidate_name"),
            "experience_years": result.get("experience_years"),
            "highest_education": result.get("highest_education"),
        },
        "raw": result,
    }


def _clean_skill(value: str) -> str:
    return " ".join((value or "").replace("/", " / ").replace(",", " ").split()).strip()


def _dedupe_skills(values: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        skill = _clean_skill(value)
        key = skill.casefold()
        if skill and key not in seen:
            seen.add(key)
            cleaned.append(skill)
    return cleaned


def _skills_from_resume_text(resume_text: str, required_skills: list[str]) -> list[str]:
    text = f" {resume_text.casefold()} "
    found = []
    for skill in required_skills:
        normalized = _clean_skill(skill)
        if normalized and normalized.casefold() in text:
            found.append(normalized)
    return _dedupe_skills(found)


def _current_skills(candidate: models.Candidate | None, resume_text: str, required_skills: list[str]) -> list[str]:
    result_skills = []
    if candidate and candidate.result_json:
        result_skills = candidate.result_json.get("key_skills") or []
    return _dedupe_skills([*result_skills, *_skills_from_resume_text(resume_text, required_skills)])


def _safe_error(exc: Exception) -> JSONResponse:
    status = exc.status_code if isinstance(exc, HTTPException) else 500
    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
    if status >= 500:
        logger.exception("WebMCP endpoint failed")
        detail = "AI service is unavailable. Please try again later."
    return JSONResponse(status_code=status, content={"success": False, "error": detail})


@router.post("/analyze-resume")
async def analyze_resume(
    payload: AnalyzeResumeInput,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    try:
        candidate = _latest_candidate(db, current_user.id)
        if not payload.resume_text and candidate and candidate.result_json and not payload.job_description:
            return {"success": True, "source": "latest_screening", "analysis": _normalize_screening_result(candidate.result_json)}

        resume_text = (payload.resume_text or _candidate_resume_text(candidate)).strip()
        if len(resume_text) < 50:
            raise HTTPException(400, "Provide resume_text or run a resume screening first.")

        api_key = _ai_key()
        if not api_key:
            raise HTTPException(500, "AI service is not configured.")

        result = screen_with_openai(
            api_key,
            (payload.job_description or _default_job_description(candidate)).strip(),
            resume_text,
            (payload.candidate_name or (candidate.candidate_name if candidate else "Current resume")).strip(),
        )
        return {"success": True, "source": "generated_analysis", "analysis": _normalize_screening_result(result)}
    except Exception as exc:
        return _safe_error(exc)


@router.get("/resume-score")
async def get_resume_score(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    candidate = _latest_candidate(db, current_user.id)
    if not candidate or not candidate.result_json:
        raise HTTPException(404, "No scored resume was found for the current user.")
    result = candidate.result_json
    return {
        "success": True,
        "score": result.get("overall_score"),
        "recommendation": result.get("recommendation"),
        "scores": result.get("scores") or {},
        "candidate_name": result.get("candidate_name") or candidate.candidate_name,
        "screening_id": candidate.screening_id,
        "created_at": candidate.created_at.isoformat(),
    }


@router.post("/match-resume")
async def match_resume_to_job(
    payload: MatchResumeInput,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    try:
        candidate = _latest_candidate(db, current_user.id)
        resume_text = (payload.resume_text or _candidate_resume_text(candidate)).strip()
        if len(resume_text) < 50:
            raise HTTPException(400, "Provide resume_text or run a resume screening first.")

        skills = ", ".join(s.strip() for s in payload.required_skills if s.strip())
        job_description = f"Job title: {payload.job_title}\n\n{payload.job_description}"
        if skills:
            job_description += f"\n\nRequired skills: {skills}"

        api_key = _ai_key()
        if not api_key:
            raise HTTPException(500, "AI service is not configured.")

        result = screen_with_openai(
            api_key,
            job_description,
            resume_text,
            (payload.candidate_name or (candidate.candidate_name if candidate else "Current resume")).strip(),
        )
        normalized = _normalize_screening_result(result)
        return {
            "success": True,
            "job_title": payload.job_title,
            "match_score": normalized["score"],
            "matching_skills": normalized["keywords"],
            "missing_skills": normalized["missing_skills"],
            "recommendations": normalized["recommendations"],
            "analysis": normalized,
        }
    except Exception as exc:
        return _safe_error(exc)


@router.post("/improve-resume")
async def improve_resume(
    payload: ImproveResumeInput,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    try:
        candidate = _latest_candidate(db, current_user.id)
        resume_text = (payload.resume_text or _candidate_resume_text(candidate)).strip()
        if len(resume_text) < 50:
            raise HTTPException(400, "Provide resume_text or run a resume screening first.")

        prompt = RESUME_BUILDER_PROMPT.format(
            job_title=payload.target_role or payload.instructions or "Target role",
            seniority=payload.seniority or "Mid-level",
            industry=payload.industry or "General",
            target_company=payload.job_description or payload.instructions or "Not specified",
            resume_text=resume_text[:12000],
        )
        result = _chat_json(prompt)
        return {
            "success": True,
            "requires_human_approval": True,
            "saved": False,
            "message": "Proposed resume improvements generated. Review and approve before using or saving them.",
            "proposed_changes": result,
        }
    except Exception as exc:
        return _safe_error(exc)


@router.post("/generate-cover-letter")
async def generate_cover_letter(
    payload: CoverLetterInput,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    try:
        candidate = _latest_candidate(db, current_user.id)
        resume_text = (payload.resume_text or _candidate_resume_text(candidate)).strip()
        if len(resume_text) < 50:
            raise HTTPException(400, "Provide resume_text or run a resume screening first.")

        company = payload.company_name or "the company"
        prompt = f"""You are an expert career writer.

Return ONLY valid JSON with this exact shape:
{{
  "cover_letter": {{
    "job_title": "{payload.job_title}",
    "company_name": "{company}",
    "tone": "{payload.tone}",
    "subject": "<short email subject line>",
    "body": "<tailored cover letter, 3-5 concise paragraphs>",
    "highlights": ["<resume evidence used>", "<resume evidence used>", "<resume evidence used>"],
    "customization_notes": ["<why this fits the job>", "<keyword or responsibility addressed>"]
  }}
}}

JOB DESCRIPTION:
{payload.job_description[:20000]}

RESUME:
{resume_text[:12000]}

Do not invent credentials. If evidence is missing, keep the phrasing modest."""
        result = _chat_json(prompt)
        return {"success": True, "result": result}
    except Exception as exc:
        return _safe_error(exc)


@router.post("/analyze-skill-gap")
async def analyze_skill_gap(
    payload: SkillGapInput,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    candidate = _latest_candidate(db, current_user.id)
    resume_text = (payload.resume_text or _candidate_resume_text(candidate)).strip()
    required_skills = _dedupe_skills(payload.required_skills)
    current_skills = _current_skills(candidate, resume_text, required_skills)
    current_keys = {skill.casefold() for skill in current_skills}
    matching_skills = [skill for skill in required_skills if skill.casefold() in current_keys]
    missing_skills = [skill for skill in required_skills if skill.casefold() not in current_keys]
    coverage = round((len(matching_skills) / len(required_skills)) * 100) if required_skills else 0

    recommended_next_steps = [
        f"Add evidence for {skill} through a project, role bullet, certification, or measurable achievement."
        for skill in missing_skills[:6]
    ]
    if not recommended_next_steps:
        recommended_next_steps.append(
            "Your resume already shows the required skills Aptura could verify. Strengthen the application with measurable outcomes and role-specific keywords."
        )

    return {
        "success": True,
        "targetRole": payload.target_role,
        "methodology": "Compares the target role's required skills against Aptura's latest stored resume analysis and any supplied resume text. It does not infer unverified skills.",
        "skillCoverageScore": coverage,
        "currentSkills": current_skills,
        "matchingSkills": matching_skills,
        "missingSkills": missing_skills,
        "recommendedSkills": missing_skills,
        "recommendedNextSteps": recommended_next_steps,
    }
