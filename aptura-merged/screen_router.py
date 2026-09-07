"""
routers/screen_router.py — Resume Screening Routes
=====================================================
Uses Google Gemini API — completely free tier, no dollar payment needed.
Free limit: 1,500 requests/day which is plenty for a growing business.

Screening runs on a Celery worker instead of blocking the request:
POST /api/screen creates the Screening + Candidate rows (status=QUEUED,
with each file's content stashed as base64 on the row) and dispatches a
Celery task, then returns immediately with a screening_id. The worker
(screening_tasks.py) processes each resume and updates the Candidate rows
as it goes. GET /api/screening/{id} reports live progress so the frontend
can poll it and show results as they land.

Running this requires, in addition to `python main.py`:
  1) A Redis server actually running (not just the `redis` pip package —
     that's just the client library). Locally: `redis-server`, or via Docker.
  2) A Celery worker process: `celery -A celery_worker.celery_app worker --loglevel=info`
Both need to be running alongside the web server for screenings to process.
"""

import base64
import io
import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

import security as auth
import models
from database import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["screening"])

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB per file
MAX_FILES = 50
ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.doc', '.txt'}


# ── Text Extraction ───────────────────────────────────────────────────────────

def extract_text(filename: str, file_bytes: bytes) -> str:
    """Extract plain text from PDF, DOCX, or TXT files."""
    ext = Path(filename).suffix.lower()

    if ext == ".pdf":
        import pdfplumber
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            pages = [p.extract_text() for p in pdf.pages if p.extract_text()]
        return "\n\n".join(pages)

    elif ext in (".docx", ".doc"):
        import docx
        doc = docx.Document(io.BytesIO(file_bytes))
        texts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text.strip():
                        texts.append(cell.text.strip())
        return "\n".join(texts)

    elif ext == ".txt":
        return file_bytes.decode("utf-8", errors="ignore")

    raise ValueError(
        f"Unsupported file type: {ext}. Please upload PDF, DOCX, or TXT.")


# ── AI Screening Prompt ───────────────────────────────────────────────────────

PROMPT = """You are a world-class talent acquisition specialist with 20+ years of experience.

Analyse this resume against the job description and return ONLY valid JSON — no markdown, no extra text.

JOB DESCRIPTION:
{job_description}

RESUME (Candidate: {candidate_name}):
{resume_text}

Return ONLY this JSON structure with no other text before or after it:
{{
  "candidate_name": "Full name from resume or '{candidate_name}'",
  "overall_score": <integer 0-100>,
  "recommendation": "Strong Hire",
  "executive_summary": "<2-3 sentence summary of candidate fit>",
  "scores": {{
    "technical_skills": <0-100>,
    "experience": <0-100>,
    "education": <0-100>,
    "cultural_fit": <0-100>,
    "leadership": <0-100>,
    "communication": <0-100>
  }},
  "strengths": ["<strength 1>", "<strength 2>", "<strength 3>", "<strength 4>"],
  "gaps": ["<gap 1>", "<gap 2>", "<gap 3>"],
  "key_skills": ["<skill1>", "<skill2>", "<skill3>", "<skill4>", "<skill5>"],
  "experience_years": <integer>,
  "highest_education": "<degree and field>",
  "standout_achievements": "<most impressive achievement>",
  "interview_questions": ["<question 1>", "<question 2>", "<question 3>"],
  "red_flags": [],
  "salary_expectation": "<estimated range>",
  "availability_signals": "<notice period or availability>"
}}

The recommendation field must be one of: "Strong Hire", "Hire", "Maybe", "No Hire"."""


def screen_with_gemini(api_key: str, job_description: str,
                       resume_text: str, candidate_name: str) -> dict:
    """Use Google Gemini (free) to screen a resume."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)

    prompt = PROMPT.format(
        job_description=job_description,
        resume_text=resume_text[:8000],
        candidate_name=candidate_name
    )

    response = client.models.generate_content(
        model="gemini-2.0-flash",   # Free tier, fast and smart
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,        # Low temperature = more consistent JSON output
        )
    )

    raw = response.text.strip()

    # Strip markdown fences if Gemini added them
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1] if lines[-1].strip()
                        == "```" else lines[1:])

    return json.loads(raw.strip())


# ── Main Screening Route ──────────────────────────────────────────────────────

@router.post("/screen")
async def screen_resumes(
    request: Request,
    job_description: str = Form(...),
    job_title: str = Form(default="Open Position"),
    company_name: str = Form(default=""),
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    if not job_description.strip():
        raise HTTPException(400, "Job description is required.")
    if not files or all(f.filename == "" for f in files):
        raise HTTPException(400, "At least one resume file is required.")
    if len(files) > MAX_FILES:
        raise HTTPException(400, f"Maximum {MAX_FILES} files allowed per screening.")

    file_payloads = []
    for upload in files:
        if not upload.filename:
            continue
        ext = Path(upload.filename).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(400, f"File '{upload.filename}' has unsupported format. Allowed: PDF, DOCX, TXT")
        content = await upload.read()
        if len(content) > MAX_FILE_SIZE:
            size_mb = len(content) / (1024 * 1024)
            raise HTTPException(400, f"File '{upload.filename}' is too large ({size_mb:.1f}MB). Maximum: 10MB")
        file_payloads.append({"filename": upload.filename, "content": content})

    if not file_payloads:
        raise HTTPException(400, "At least one resume file is required.")

    resolved_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not resolved_key:
        raise HTTPException(500, "AI service is not configured. Please contact support.")

    # ── Save job to DB ────────────────────────────────────────────────────────
    job = db.query(models.Job).filter(
        models.Job.user_id == current_user.id,
        models.Job.title == job_title,
        models.Job.company == company_name
    ).first()

    if not job:
        job = models.Job(
            user_id=current_user.id,
            title=job_title,
            company=company_name,
            description=job_description
        )
        db.add(job)
        db.commit()
        db.refresh(job)

    screening = models.Screening(
        user_id=current_user.id,
        job_id=job.id,
        total_files=len(file_payloads),
        status="PROCESSING",
    )
    db.add(screening)
    db.commit()
    db.refresh(screening)

    # Create a Candidate row per file, with content stashed as base64 —
    # Celery's broker message is JSON, so raw bytes can't travel with the
    # task itself. The worker (screening_tasks.py) reads this back by id.
    candidate_rows = []
    for fp in file_payloads:
        c = models.Candidate(
            screening_id=screening.id,
            filename=fp["filename"],
            status="QUEUED",
            file_content_b64=base64.b64encode(fp["content"]).decode("ascii"),
        )
        db.add(c)
        candidate_rows.append(c)
    db.commit()
    for c in candidate_rows:
        db.refresh(c)

    # Dispatch to the Celery worker (import here, not at module load, so the
    # web process can still start even if celery_worker/Redis aren't ready).
    from screening_tasks import process_screening_task
    task_files = [{"candidate_id": c.id, "filename": c.filename} for c in candidate_rows]
    process_screening_task.delay(screening.id, task_files)

    return JSONResponse({
        "job_title": job_title,
        "company_name": company_name,
        "screening_id": screening.id,
        "total_files": len(file_payloads),
        "status": "PROCESSING",
    })


# ── History Routes ────────────────────────────────────────────────────────────

@router.get("/history")
async def get_history(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    screenings = (
        db.query(models.Screening)
        .filter(models.Screening.user_id == current_user.id)
        .order_by(models.Screening.created_at.desc())
        .limit(50)
        .all()
    )
    data = []
    for s in screenings:
        completed = [c for c in s.candidates if c.status == "COMPLETED"]
        top = max(completed, key=lambda c: c.overall_score or 0, default=None)
        data.append({
            "id": s.id,
            "job_title": s.job.title,
            "company": s.job.company,
            "total_candidates": s.total_candidates,
            "status": s.status,
            "created_at": s.created_at.isoformat(),
            "top_candidate": top.candidate_name if top else None,
            "top_score": top.overall_score if top else None,
        })
    return JSONResponse({"screenings": data})


@router.get("/screening/{screening_id}")
async def get_screening(
    screening_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    screening = db.query(models.Screening).filter(
        models.Screening.id == screening_id,
        models.Screening.user_id == current_user.id
    ).first()

    if not screening:
        raise HTTPException(404, "Screening not found.")

    results = []
    errors = []
    for c in screening.candidates:
        if c.status == "COMPLETED" and c.result_json:
            results.append(c.result_json)
        elif c.status == "FAILED":
            errors.append({"file": c.filename, "error": c.error_message or "Processing failed."})

    results.sort(key=lambda x: x.get("overall_score", 0), reverse=True)

    return JSONResponse({
        "job_title": screening.job.title,
        "company_name": screening.job.company or "",
        "screening_id": screening.id,
        "created_at": screening.created_at.isoformat(),
        "status": screening.status,
        "total_files": screening.total_files,
        "processed_candidates": screening.processed_candidates,
        "total_processed": len(results),
        "total_errors": len(errors),
        "results": results,
        "errors": errors
    })
