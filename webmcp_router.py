import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

import models
import career_services as career
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


class AnalyzeJobInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    job_title: str = Field(..., validation_alias=AliasChoices("jobTitle", "job_title"), min_length=1, max_length=120)
    job_description: str = Field(..., validation_alias=AliasChoices("jobDescription", "job_description"), min_length=20, max_length=20000)


class ScreeningIdInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    screening_id: int = Field(..., validation_alias=AliasChoices("screeningId", "screening_id"))


class CompareCandidatesInput(ScreeningIdInput):
    candidate_ids: list[int] = Field(default_factory=list, validation_alias=AliasChoices("candidateIds", "candidate_ids"), max_length=10)


class ShortlistCandidateInput(ScreeningIdInput):
    candidate_id: int = Field(..., validation_alias=AliasChoices("candidateId", "candidate_id"))
    notes: str | None = Field(default=None, max_length=2000)


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
        result = career.analyze_resume(db, current_user, payload.resume_text, payload.job_description, payload.candidate_name)
        return {"success": True, "source": "generated_analysis", "analysis": result}
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
        normalized = career.match_resume_to_job(
            db,
            current_user,
            payload.job_title,
            "",
            payload.job_description,
            payload.required_skills,
            payload.resume_text,
        )
        return {
            "success": True,
            "job_title": payload.job_title,
            "match_score": normalized["match_score"],
            "matching_skills": normalized["matching_skills"],
            "missing_skills": normalized["missing_skills"],
            "recommendations": normalized["recommended_improvements"],
            "analysis": normalized["analysis"],
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
        result = career.improve_resume(db, current_user, payload.target_role, payload.instructions, payload.job_description, payload.resume_text)
        return {
            "success": True,
            "requires_human_approval": True,
            "saved": False,
            "message": result["message"],
            "proposed_changes": result["proposed_changes"],
            "before": result["before"],
            "after": result["after"],
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
        result = career.cover_letter(db, current_user, payload.job_title, payload.company_name, payload.job_description, payload.resume_text, payload.tone)
        return {"success": True, "result": result}
    except Exception as exc:
        return _safe_error(exc)


@router.post("/analyze-skill-gap")
async def analyze_skill_gap(
    payload: SkillGapInput,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    result = career.skill_gap(db, current_user, payload.target_role, payload.required_skills, payload.resume_text)
    return {"success": True, **result}


@router.post("/analyze-job")
async def analyze_job(payload: AnalyzeJobInput, current_user: models.User = Depends(auth.get_current_user)):
    try:
        prompt = f"""Return ONLY valid JSON.
{{"job_title":"{payload.job_title}","role_summary":"<summary>","requirements":["<requirement>"],"required_skills":["<skill>"],"preferred_skills":["<skill>"],"screening_notes":["<note>"]}}

JOB DESCRIPTION:
{payload.job_description[:20000]}

Do not invent requirements; extract only what is stated or clearly implied."""
        return {"success": True, "analysis": _chat_json(prompt)}
    except Exception as exc:
        return _safe_error(exc)


def _owned_screening(db: Session, user_id: int, screening_id: int) -> models.Screening:
    screening = db.query(models.Screening).filter(models.Screening.id == screening_id, models.Screening.user_id == user_id).first()
    if not screening:
        raise HTTPException(404, "Screening not found.")
    return screening


def _candidate_payload(candidate: models.Candidate) -> dict[str, Any]:
    result = candidate.result_json or {}
    return {
        "candidate_id": candidate.id,
        "candidate_name": candidate.candidate_name,
        "match_score": candidate.overall_score,
        "recommendation": candidate.recommendation,
        "matching_requirements": result.get("strengths") or [],
        "missing_or_uncertain_requirements": result.get("gaps") or [],
        "explanation": result.get("executive_summary"),
    }


@router.post("/rank-candidates")
async def rank_candidates(payload: ScreeningIdInput, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    screening = _owned_screening(db, current_user.id, payload.screening_id)
    ranked = sorted([_candidate_payload(c) for c in screening.candidates if c.result_json], key=lambda c: c.get("match_score") or 0, reverse=True)
    return {"success": True, "screening_id": screening.id, "ranked_candidates": ranked, "note": "AI recommendation only. Aptura does not make autonomous hiring decisions."}


@router.post("/compare-candidates")
async def compare_candidates(payload: CompareCandidatesInput, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    screening = _owned_screening(db, current_user.id, payload.screening_id)
    allowed = set(payload.candidate_ids or [])
    candidates = [c for c in screening.candidates if c.result_json and (not allowed or c.id in allowed)]
    compared = sorted([_candidate_payload(c) for c in candidates], key=lambda c: c.get("match_score") or 0, reverse=True)
    return {"success": True, "screening_id": screening.id, "comparison": compared, "note": "Use this as a screening aid, not a hiring decision."}


@router.post("/shortlist-candidate")
async def shortlist_candidate(payload: ShortlistCandidateInput, db: Session = Depends(get_db), current_user: models.User = Depends(auth.get_current_user)):
    screening = _owned_screening(db, current_user.id, payload.screening_id)
    candidate = next((c for c in screening.candidates if c.id == payload.candidate_id), None)
    if not candidate:
        raise HTTPException(404, "Candidate not found.")
    existing = (
        db.query(models.RecruiterShortlist)
        .filter(models.RecruiterShortlist.user_id == current_user.id, models.RecruiterShortlist.screening_id == screening.id, models.RecruiterShortlist.candidate_id == candidate.id)
        .first()
    )
    if not existing:
        db.add(models.RecruiterShortlist(user_id=current_user.id, screening_id=screening.id, candidate_id=candidate.id, notes=payload.notes))
        db.commit()
    return {"success": True, "candidate": _candidate_payload(candidate), "status": "shortlisted"}
