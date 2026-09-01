from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import career_services as career
import models
from database import get_db
from security import get_current_user


router = APIRouter(prefix="", tags=["career"])
templates = Jinja2Templates(directory="templates")


class SaveResumeInput(BaseModel):
    resume_text: str = Field(..., max_length=12000)
    target_role: str | None = Field(default=None, max_length=255)


class AnalyzeInput(BaseModel):
    resume_text: str | None = Field(default=None, max_length=12000)
    job_description: str | None = Field(default=None, max_length=20000)


class MatchJobInput(BaseModel):
    job_title: str = Field(..., min_length=1, max_length=255)
    company: str | None = Field(default="", max_length=255)
    job_description: str = Field(..., min_length=20, max_length=20000)
    required_skills: list[str] = Field(default_factory=list, max_length=60)
    resume_text: str | None = Field(default=None, max_length=12000)


class ImproveInput(BaseModel):
    target_role: str = Field(default="Target role", max_length=255)
    instructions: str | None = Field(default=None, max_length=2000)
    job_description: str | None = Field(default=None, max_length=20000)
    resume_text: str | None = Field(default=None, max_length=12000)


class CoverLetterInput(BaseModel):
    job_title: str = Field(..., min_length=1, max_length=255)
    company: str | None = Field(default="", max_length=255)
    job_description: str = Field(..., min_length=20, max_length=20000)
    resume_text: str | None = Field(default=None, max_length=12000)
    tone: str = Field(default="professional", max_length=60)


class SkillGapInput(BaseModel):
    target_role: str = Field(..., min_length=1, max_length=255)
    required_skills: list[str] = Field(..., min_length=1, max_length=60)
    resume_text: str | None = Field(default=None, max_length=12000)


class ApproveResumeInput(BaseModel):
    approved_resume_text: str = Field(..., min_length=20, max_length=12000)
    target_role: str | None = Field(default=None, max_length=255)


class AgentInput(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    job_title: str | None = Field(default=None, max_length=255)
    company: str | None = Field(default="", max_length=255)
    job_description: str | None = Field(default=None, max_length=20000)
    required_skills: list[str] = Field(default_factory=list, max_length=60)
    resume_text: str | None = Field(default=None, max_length=12000)


class ShortlistInput(BaseModel):
    candidate_id: int
    screening_id: int
    notes: str | None = Field(default=None, max_length=2000)


def _json_success(data: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"success": True, **data})


def _safe_error(exc: Exception) -> JSONResponse:
    status = exc.status_code if isinstance(exc, HTTPException) else 500
    detail = exc.detail if isinstance(exc, HTTPException) else "AI service is unavailable. Please try again later."
    return JSONResponse(status_code=status, content={"success": False, "error": detail})


@router.get("/agent", response_class=HTMLResponse)
async def agent_page(request: Request, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    profile = career.get_or_create_profile(db, current_user)
    latest_match = (
        db.query(models.JobMatch)
        .filter(models.JobMatch.user_id == current_user.id)
        .order_by(models.JobMatch.created_at.desc())
        .first()
    )
    return templates.TemplateResponse(
        request=request,
        name="agent.html",
        context={
            "user": current_user,
            "profile": profile,
            "latest_match": latest_match.result_json if latest_match else None,
        },
    )


@router.get("/job-match", response_class=HTMLResponse)
async def job_match_page(request: Request, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    return await agent_page(request, db, current_user)


@router.get("/recruiter", response_class=HTMLResponse)
async def recruiter_page(request: Request, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    import main as _main

    return await _main.screen_page(request, db, current_user)


@router.get("/api/career/profile")
async def get_profile(db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    profile = career.get_or_create_profile(db, current_user)
    latest_match = (
        db.query(models.JobMatch)
        .filter(models.JobMatch.user_id == current_user.id)
        .order_by(models.JobMatch.created_at.desc())
        .first()
    )
    return _json_success({"profile": career.profile_payload(profile, latest_match)})


@router.post("/api/career/upload-resume")
async def upload_resume(file: UploadFile = File(...), db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    try:
        content = await file.read()
        profile = career.save_uploaded_resume(db, current_user, file.filename or "resume.txt", content)
        return _json_success({"profile": career.profile_payload(profile), "activity": ["Resume uploaded", "Resume text extracted", "Editable profile updated"]})
    except Exception as exc:
        return _safe_error(exc)


@router.post("/api/career/save-resume")
async def save_resume(payload: SaveResumeInput, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    try:
        profile = career.save_resume_text(db, current_user, payload.resume_text, target_role=payload.target_role)
        return _json_success({"profile": career.profile_payload(profile), "activity": ["Resume saved"]})
    except Exception as exc:
        return _safe_error(exc)


@router.post("/api/career/analyze-resume")
async def analyze_resume(payload: AnalyzeInput, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    try:
        result = career.analyze_resume(db, current_user, payload.resume_text, payload.job_description)
        return _json_success({"analysis": result, "activity": ["Resume analyzed", "Readiness calculated"]})
    except Exception as exc:
        return _safe_error(exc)


@router.post("/api/career/match-job")
async def match_job(payload: MatchJobInput, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    try:
        result = career.match_resume_to_job(
            db,
            current_user,
            payload.job_title,
            payload.company,
            payload.job_description,
            payload.required_skills,
            payload.resume_text,
        )
        return _json_success({"match": result, "activity": ["Job requirements extracted", "Resume compared", "Match calculated"]})
    except Exception as exc:
        return _safe_error(exc)


@router.post("/api/career/improve-resume")
async def improve_resume(payload: ImproveInput, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    try:
        result = career.improve_resume(db, current_user, payload.target_role, payload.instructions, payload.job_description, payload.resume_text)
        return _json_success({"proposal": result, "activity": ["Resume weaknesses inspected", "Proposed edits generated", "Waiting for approval"]})
    except Exception as exc:
        return _safe_error(exc)


@router.post("/api/career/approve-resume")
async def approve_resume(payload: ApproveResumeInput, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    try:
        profile = career.save_resume_text(db, current_user, payload.approved_resume_text, target_role=payload.target_role)
        return _json_success({"profile": career.profile_payload(profile), "activity": ["Approved changes saved"]})
    except Exception as exc:
        return _safe_error(exc)


@router.post("/api/career/generate-cover-letter")
async def generate_cover_letter(payload: CoverLetterInput, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    try:
        result = career.cover_letter(db, current_user, payload.job_title, payload.company, payload.job_description, payload.resume_text, payload.tone)
        return _json_success({"cover_letter": result, "activity": ["Resume evidence reviewed", "Cover letter generated"]})
    except Exception as exc:
        return _safe_error(exc)


@router.post("/api/career/analyze-skill-gap")
async def analyze_skill_gap(payload: SkillGapInput, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    try:
        result = career.skill_gap(db, current_user, payload.target_role, payload.required_skills, payload.resume_text)
        return _json_success({"skill_gap": result, "activity": ["Required skills compared", "Skill gap found"]})
    except Exception as exc:
        return _safe_error(exc)


@router.post("/api/career/career-plan")
async def build_career_plan(payload: SkillGapInput, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    try:
        result = career.career_plan(db, current_user, payload.target_role, payload.required_skills)
        return _json_success({"career_plan": result, "activity": ["Profile inspected", "Skill gaps prioritized", "Career plan generated"]})
    except Exception as exc:
        return _safe_error(exc)


@router.post("/api/career/agent")
async def agent_chat(payload: AgentInput, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    message = payload.message.casefold()
    try:
        activity: list[str] = []
        result: dict[str, Any] = {}
        if "cover" in message:
            if not payload.job_description or not payload.job_title:
                raise HTTPException(400, "Add a job title and job description before generating a cover letter.")
            result["cover_letter"] = career.cover_letter(db, current_user, payload.job_title, payload.company, payload.job_description, payload.resume_text)
            activity += ["Resume evidence reviewed", "Cover letter generated"]
        elif "match" in message or "internship" in message or "job" in message:
            if payload.job_description:
                result["match"] = career.match_resume_to_job(db, current_user, payload.job_title or "Target opportunity", payload.company, payload.job_description, payload.required_skills, payload.resume_text)
                activity += ["Job requirements extracted", "Resume compared", "Match calculated"]
            else:
                result["analysis"] = career.analyze_resume(db, current_user, payload.resume_text)
                activity += ["Resume analyzed", "Waiting for job description"]
        elif "gap" in message or "plan" in message:
            if not payload.required_skills:
                raise HTTPException(400, "Add required skills to calculate skill gaps or build a plan.")
            result["skill_gap"] = career.skill_gap(db, current_user, payload.job_title or "Target role", payload.required_skills, payload.resume_text)
            if "plan" in message:
                result["career_plan"] = career.career_plan(db, current_user, payload.job_title or "Target role", payload.required_skills)
                activity += ["Profile inspected", "Skill gaps prioritized", "Career plan generated"]
            else:
                activity += ["Required skills compared", "Skill gap found"]
        elif "fix" in message or "improve" in message or "tailor" in message:
            result["proposal"] = career.improve_resume(db, current_user, payload.job_title or "Target role", payload.message, payload.job_description, payload.resume_text)
            activity += ["Resume weaknesses inspected", "Proposed edits generated", "Waiting for approval"]
        else:
            result["analysis"] = career.analyze_resume(db, current_user, payload.resume_text, payload.job_description)
            activity += ["Resume analyzed", "Readiness calculated"]
        return _json_success({
            "reply": "I used Aptura's career tools and returned structured results below.",
            "activity": activity,
            "result": result,
        })
    except Exception as exc:
        return _safe_error(exc)


@router.post("/api/recruiter/shortlist")
async def shortlist_candidate(payload: ShortlistInput, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    screening = (
        db.query(models.Screening)
        .filter(models.Screening.id == payload.screening_id, models.Screening.user_id == current_user.id)
        .first()
    )
    if not screening:
        raise HTTPException(404, "Screening not found.")
    candidate = (
        db.query(models.Candidate)
        .filter(models.Candidate.id == payload.candidate_id, models.Candidate.screening_id == screening.id)
        .first()
    )
    if not candidate:
        raise HTTPException(404, "Candidate not found.")
    existing = (
        db.query(models.RecruiterShortlist)
        .filter(
            models.RecruiterShortlist.user_id == current_user.id,
            models.RecruiterShortlist.screening_id == screening.id,
            models.RecruiterShortlist.candidate_id == candidate.id,
        )
        .first()
    )
    if not existing:
        db.add(models.RecruiterShortlist(user_id=current_user.id, screening_id=screening.id, candidate_id=candidate.id, notes=payload.notes))
        db.commit()
    return _json_success({"activity": ["Candidate shortlisted"], "candidate_id": candidate.id})
