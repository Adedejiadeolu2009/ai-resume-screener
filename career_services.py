import base64
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

import models
from resume_builder_router import PROMPT as RESUME_BUILDER_PROMPT
from resume_builder_router import _chat_json
from screen_router import extract_text, screen_with_openai


def ai_key() -> str:
    return (
        os.getenv("OPENAI_API_KEY", "").strip()
        or os.getenv("DEEPSEEK_API_KEY", "").strip()
        or os.getenv("GROQ_API_KEY", "").strip()
        or os.getenv("GEMINI_API_KEY", "").strip()
    )


def latest_candidate(db: Session, user_id: int) -> models.Candidate | None:
    return (
        db.query(models.Candidate)
        .join(models.Screening)
        .filter(models.Screening.user_id == user_id)
        .order_by(models.Candidate.created_at.desc())
        .first()
    )


def get_or_create_profile(db: Session, user: models.User) -> models.CareerProfile:
    profile = (
        db.query(models.CareerProfile)
        .filter(models.CareerProfile.user_id == user.id)
        .first()
    )
    if profile:
        return profile
    profile = models.CareerProfile(user_id=user.id)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def extract_candidate_text(candidate: models.Candidate | None) -> str:
    if not candidate or not candidate.file_content_b64:
        return ""
    file_bytes = base64.b64decode(candidate.file_content_b64)
    return extract_text(candidate.filename, file_bytes)


def current_resume_text(db: Session, user: models.User, supplied: str | None = None) -> str:
    if supplied and supplied.strip():
        return supplied.strip()
    profile = get_or_create_profile(db, user)
    if profile.resume_text and profile.resume_text.strip():
        return profile.resume_text.strip()
    return extract_candidate_text(latest_candidate(db, user.id)).strip()


def default_job_description(db: Session, user: models.User) -> str:
    candidate = latest_candidate(db, user.id)
    if candidate and candidate.screening and candidate.screening.job:
        return candidate.screening.job.description or "General resume analysis"
    profile = get_or_create_profile(db, user)
    return f"Target role: {profile.target_role}" if profile.target_role else "General resume analysis"


def normalize_screening_result(result: dict[str, Any]) -> dict[str, Any]:
    scores = result.get("scores") or {}
    return {
        "score": result.get("overall_score"),
        "match_score": result.get("overall_score"),
        "recommendation": result.get("recommendation"),
        "summary": result.get("executive_summary"),
        "strengths": result.get("strengths") or [],
        "weaknesses": result.get("gaps") or [],
        "missing_skills": result.get("missing_skills") or result.get("gaps") or [],
        "matching_skills": result.get("key_skills") or [],
        "keywords": result.get("key_skills") or [],
        "experience_alignment": scores.get("experience"),
        "project_alignment": result.get("standout_achievements"),
        "recommended_improvements": result.get("rewrite_advice") or result.get("interview_questions") or [],
        "interview_questions": result.get("interview_questions") or [],
        "scores": scores,
        "candidate": {
            "name": result.get("candidate_name"),
            "experience_years": result.get("experience_years"),
            "highest_education": result.get("highest_education"),
        },
        "raw": result,
    }


def analyze_resume(db: Session, user: models.User, resume_text: str | None = None, job_description: str | None = None, candidate_name: str | None = None) -> dict[str, Any]:
    text = current_resume_text(db, user, resume_text)
    if len(text) < 50:
        raise HTTPException(400, "Upload, paste, or save resume text before analysis.")
    key = ai_key()
    if not key:
        raise HTTPException(500, "AI service is not configured.")
    result = screen_with_openai(
        key,
        (job_description or default_job_description(db, user)).strip(),
        text,
        (candidate_name or user.name or "Current resume").strip(),
    )
    normalized = normalize_screening_result(result)
    profile = get_or_create_profile(db, user)
    profile.latest_analysis_json = normalized
    profile.updated_at = datetime.utcnow()
    db.commit()
    return normalized


def save_resume_text(db: Session, user: models.User, resume_text: str, filename: str | None = None, target_role: str | None = None) -> models.CareerProfile:
    text = (resume_text or "").strip()
    if len(text) < 20:
        raise HTTPException(400, "Resume text is too short to save.")
    if len(text) > 12000:
        raise HTTPException(400, "Resume text is too long. Keep it under 12,000 characters.")
    profile = get_or_create_profile(db, user)
    profile.resume_text = text
    if filename:
        profile.resume_filename = filename
    if target_role is not None:
        profile.target_role = target_role.strip() or None
    profile.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(profile)
    return profile


def save_uploaded_resume(db: Session, user: models.User, filename: str, file_bytes: bytes) -> models.CareerProfile:
    ext = Path(filename).suffix.lower()
    if ext not in {".pdf", ".docx", ".txt"}:
        raise HTTPException(400, "Upload a PDF, DOCX, or TXT resume.")
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(400, "Resume file is too large. Maximum size is 10MB.")
    text = extract_text(filename, file_bytes)
    return save_resume_text(db, user, text, filename=filename)


def clean_skill(value: str) -> str:
    return " ".join((value or "").replace("/", " / ").replace(",", " ").split()).strip()


def dedupe_skills(values: list[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        skill = clean_skill(value)
        key = skill.casefold()
        if skill and key not in seen:
            seen.add(key)
            cleaned.append(skill)
    return cleaned


def skill_gap(db: Session, user: models.User, target_role: str, required_skills: list[str], resume_text: str | None = None) -> dict[str, Any]:
    required = dedupe_skills(required_skills)
    if not required:
        raise HTTPException(400, "Add at least one required skill.")
    text = current_resume_text(db, user, resume_text).casefold()
    latest = latest_candidate(db, user.id)
    analyzed = []
    if latest and latest.result_json:
        analyzed = latest.result_json.get("key_skills") or []
    current = dedupe_skills([*analyzed, *[skill for skill in required if skill.casefold() in text]])
    current_keys = {skill.casefold() for skill in current}
    matching = [skill for skill in required if skill.casefold() in current_keys]
    missing = [skill for skill in required if skill.casefold() not in current_keys]
    result = {
        "targetRole": target_role,
        "methodology": "Compares required skills against saved resume text and latest Aptura analysis. Aptura does not infer unverified skills.",
        "skillCoverageScore": round((len(matching) / len(required)) * 100),
        "currentSkills": current,
        "matchingSkills": matching,
        "missingSkills": missing,
        "recommendedSkills": missing,
        "recommendedNextSteps": [
            f"Add evidence for {skill} through a project, role bullet, certification, or measurable achievement."
            for skill in missing[:6]
        ] or ["Your resume already shows the required skills Aptura could verify. Strengthen it with measurable outcomes and role-specific keywords."],
    }
    profile = get_or_create_profile(db, user)
    profile.latest_skill_gap_json = result
    profile.updated_at = datetime.utcnow()
    db.commit()
    return result


def match_resume_to_job(db: Session, user: models.User, job_title: str, company: str | None, job_description: str, required_skills: list[str] | None = None, resume_text: str | None = None) -> dict[str, Any]:
    text = current_resume_text(db, user, resume_text)
    if len(text) < 50:
        raise HTTPException(400, "Upload, paste, or save resume text before matching a job.")
    key = ai_key()
    if not key:
        raise HTTPException(500, "AI service is not configured.")
    skills = dedupe_skills(required_skills or [])
    job_context = f"Job title: {job_title}\nCompany: {company or 'Not specified'}\n\n{job_description}"
    if skills:
        job_context += "\n\nRequired skills:\n" + "\n".join(f"- {skill}" for skill in skills)
    result = screen_with_openai(key, job_context, text, user.name or "Current resume")
    normalized = normalize_screening_result(result)
    payload = {
        "job_title": job_title,
        "company": company or "",
        "match_score": normalized["match_score"],
        "matching_skills": normalized["matching_skills"],
        "missing_skills": normalized["missing_skills"],
        "experience_alignment": normalized["experience_alignment"],
        "project_alignment": normalized["project_alignment"],
        "recommended_improvements": normalized["recommended_improvements"],
        "analysis": normalized,
    }
    match = models.JobMatch(
        user_id=user.id,
        job_title=job_title,
        company=company or "",
        job_description=job_description,
        required_skills=skills,
        result_json=payload,
    )
    db.add(match)
    db.commit()
    db.refresh(match)
    payload["id"] = match.id
    return payload


def improve_resume(db: Session, user: models.User, target_role: str, instructions: str | None = None, job_description: str | None = None, resume_text: str | None = None) -> dict[str, Any]:
    text = current_resume_text(db, user, resume_text)
    if len(text) < 50:
        raise HTTPException(400, "Upload, paste, or save resume text before improving it.")
    prompt = RESUME_BUILDER_PROMPT.format(
        job_title=target_role or instructions or "Target role",
        seniority="Mid-level",
        industry="General",
        target_company=job_description or instructions or "Not specified",
        resume_text=text[:12000],
    )
    result = _chat_json(prompt)
    return {
        "requires_human_approval": True,
        "saved": False,
        "message": "Aptura generated proposed resume changes. Review before saving.",
        "before": text,
        "after": proposed_resume_text(result),
        "proposed_changes": result,
    }


def proposed_resume_text(result: dict[str, Any]) -> str:
    resume = result.get("resume") or {}
    parts = [
        resume.get("headline") or "",
        resume.get("professional_summary") or "",
        "Skills: " + ", ".join(resume.get("skills") or []),
        "Experience",
        *[f"- {item}" for item in resume.get("experience_bullets") or []],
        "Projects",
        *[f"- {item}" for item in resume.get("project_ideas") or []],
    ]
    return "\n\n".join(part for part in parts if part).strip()


def cover_letter(db: Session, user: models.User, job_title: str, company: str | None, job_description: str, resume_text: str | None = None, tone: str = "professional") -> dict[str, Any]:
    text = current_resume_text(db, user, resume_text)
    if len(text) < 50:
        raise HTTPException(400, "Upload, paste, or save resume text before generating a cover letter.")
    prompt = f"""You are an expert career writer.

Return ONLY valid JSON with this exact shape:
{{
  "cover_letter": {{
    "job_title": {json.dumps(job_title)},
    "company_name": {json.dumps(company or "the company")},
    "tone": {json.dumps(tone)},
    "subject": "<short email subject line>",
    "body": "<tailored cover letter, 3-5 concise paragraphs>",
    "highlights": ["<resume evidence used>", "<resume evidence used>", "<resume evidence used>"],
    "customization_notes": ["<why this fits the job>", "<keyword or responsibility addressed>"]
  }}
}}

JOB DESCRIPTION:
{job_description[:20000]}

RESUME:
{text[:12000]}

Do not invent credentials. If evidence is missing, keep the phrasing modest."""
    return _chat_json(prompt)


def career_plan(db: Session, user: models.User, target_role: str, required_skills: list[str] | None = None) -> dict[str, Any]:
    profile = get_or_create_profile(db, user)
    text = current_resume_text(db, user, None)
    gaps = skill_gap(db, user, target_role, required_skills or [], text) if required_skills else profile.latest_skill_gap_json
    prompt = f"""Return ONLY valid JSON for a practical career plan.

Shape:
{{"target_role":"{target_role}","milestones":[{{"title":"<milestone>","why":"<reason>","actions":["<action>"],"evidence_to_create":["<resume/project proof>"]}}],"priority_gaps":["<gap>"],"next_7_days":["<task>"],"next_30_days":["<task>"]}}

Resume evidence:
{text[:8000]}

Known skill gaps:
{json.dumps(gaps or {})}

Do not invent experience. Focus on evidence the user can build."""
    return _chat_json(prompt)


def profile_payload(profile: models.CareerProfile, latest_match: models.JobMatch | None = None) -> dict[str, Any]:
    return {
        "target_role": profile.target_role,
        "resume_text": profile.resume_text or "",
        "resume_filename": profile.resume_filename,
        "latest_analysis": profile.latest_analysis_json,
        "latest_skill_gap": profile.latest_skill_gap_json,
        "latest_match": latest_match.result_json if latest_match else None,
        "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
    }
