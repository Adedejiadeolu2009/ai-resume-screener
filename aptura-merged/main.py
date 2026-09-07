"""
main.py — App Entry Point
==========================
Run with:   python main.py
Then open:  http://localhost:8000
"""

from fastapi.exceptions import HTTPException as _HTTPException
from fastapi.responses import Response as _Response, JSONResponse
from fastapi import Request as _Request
import screen_router
import auth_router
import admin_router
import career_router
import plans_router
import resume_builder_router
from security import get_current_user
from database import engine, get_db, Base, SessionLocal
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, inspect, text
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi import FastAPI, Request, Depends, HTTPException
from dotenv import load_dotenv
import secrets
import uvicorn
import os
import sys
from datetime import datetime
from pathlib import Path

# ── Fix Python path for uvicorn reload workers ────────────────────────────────
PROJECT_DIR = Path(__file__).parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


# ── Load .env using the exact location of this script ────────────────────────
ENV_PATH = PROJECT_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

if ENV_PATH.exists():
    print("✅ .env file found")
else:
    print(f"❌ .env file NOT found at: {ENV_PATH}")
    print("   See instructions below to create it.")

import models  # noqa: F401

# Create all DB tables on startup (safe to run every time — never deletes data)
Base.metadata.create_all(bind=engine)
print("✅ Database tables ready")


def ensure_db_columns():
    """
    create_all() only creates TABLES that don't exist yet — it does NOT add
    new COLUMNS to tables that already exist. Your real database was created
    before this round of model changes (is_admin, tier, total_files, status,
    file_content_b64, etc.), so without this, the app would crash with
    "no such column" the first time any of that code runs.

    This derives the correct column type for whichever database you're on
    (SQLite locally, Postgres/Supabase in production) straight from the
    SQLAlchemy model, so it doesn't need per-dialect SQL written by hand.
    Safe to run every startup — skips columns that already exist.
    """
    inspector = inspect(engine)
    tables_to_check = [models.User.__table__, models.Screening.__table__, models.Candidate.__table__]
    with engine.connect() as conn:
        for table in tables_to_check:
            if table.name not in inspector.get_table_names():
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                col_type = column.type.compile(dialect=engine.dialect)
                try:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {column.name} {col_type}'))
                    conn.commit()
                    print(f"✅ Added column {table.name}.{column.name}")
                except Exception as exc:
                    print(f"⚠️  Could not add column {table.name}.{column.name}: {exc}")


ensure_db_columns()

# ── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="Aptura AI", version="2.0.0")

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv(
        "SECRET_KEY", "dev-secret-key-change-before-going-live"),
    session_cookie="aptura_session",
    max_age=3600,
)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(auth_router.router)
app.include_router(screen_router.router)
app.include_router(admin_router.router)
app.include_router(career_router.router)
app.include_router(plans_router.router)
app.include_router(resume_builder_router.router)


def sync_admin_on_startup():
    """Promotes the ADMIN_EMAIL account's is_admin DB flag. Idempotent —
    safe to run every startup, no-op once the flag is already set."""
    db = SessionLocal()
    try:
        admin_router.sync_admin_flag(db)
    finally:
        db.close()


sync_admin_on_startup()
print("✅ Admin flag synced")


# ── CSRF helpers (shared across admin_router, plans_router, resume_builder_router) ──

def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def require_csrf(request: Request, csrf_token: str | None) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not csrf_token or csrf_token != expected:
        raise HTTPException(status_code=403, detail="CSRF validation failed")


# Mirrors the limits enforced server-side in screen_router — this copy is
# read-only, for display on the /screen page.
FREE_SCREENING_LIMIT = 5
PRO_SCREENING_LIMIT = 100


def screening_quota(user: models.User) -> dict:
    tier = (user.tier or "FREE").upper()
    now = datetime.utcnow()
    active = user.premium_until is None or user.premium_until > now
    if tier == "ENTERPRISE" and active:
        return {"tier": "ENTERPRISE", "limit": None, "used": user.screenings_used_this_month or 0, "remaining": None}
    if tier == "PRO" and active:
        limit = PRO_SCREENING_LIMIT
    else:
        tier = "FREE"
        limit = FREE_SCREENING_LIMIT
    used = user.screenings_used_this_month or 0
    return {"tier": tier, "limit": limit, "used": used, "remaining": max(limit - used, 0)}


def build_career_readiness(profile: "models.CareerProfile | None") -> dict:
    """
    Readiness for the SIGNED-IN USER'S OWN resume, from their career profile
    (populated by career_services.analyze_resume / skill_gap) — not from
    resumes they've screened as a recruiter, which is a different person's
    resume than the one viewing the dashboard.
    """
    analysis = (profile.latest_analysis_json if profile else None) or {}
    gap = (profile.latest_skill_gap_json if profile else None) or {}
    scores = analysis.get("scores") or {}

    actions = list((gap.get("recommendedNextSteps") or [])[:4])
    if not actions:
        actions = list((analysis.get("recommended_improvements") or [])[:4])
    if not actions:
        actions = ["Analyze your resume in the Career Agent to get your readiness score and a personalized action list."]

    return {
        "overall": analysis.get("score"),
        "resume": analysis.get("score"),
        "skills": scores.get("technical_skills"),
        "experience": scores.get("experience"),
        "communication": scores.get("communication"),
        "recommendation": analysis.get("recommendation"),
        "actions": actions,
        "methodology": "Your latest Aptura resume analysis and skill-gap check from the Career Agent.",
    }


# ── Page Routes ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Homepage → redirect to dashboard if logged in, else login."""
    if request.cookies.get("access_token"):
        return RedirectResponse("/dashboard")
    return RedirectResponse("/login")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login / Register page."""
    if request.cookies.get("access_token"):
        return RedirectResponse("/dashboard")
    error = request.query_params.get("error", "")
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": error}
    )


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Dashboard — requires login."""
    if not request.cookies.get("access_token"):
        return RedirectResponse("/login")

    total_screenings = db.query(models.Screening).filter(
        models.Screening.user_id == current_user.id
    ).count()

    total_candidates = db.query(func.sum(models.Screening.total_candidates)).filter(
        models.Screening.user_id == current_user.id
    ).scalar() or 0

    recent = (
        db.query(models.Screening)
        .options(joinedload(models.Screening.job), joinedload(models.Screening.candidates))
        .filter(models.Screening.user_id == current_user.id)
        .order_by(models.Screening.created_at.desc())
        .limit(20)
        .all()
    )

    screenings_data = []
    strong_hires = 0
    score_sum, score_count = 0, 0

    for s in recent:
        top = max(s.candidates, key=lambda c: c.overall_score or 0, default=None)
        if top and top.overall_score:
            score_sum += top.overall_score
            score_count += 1
            if top.recommendation == "Strong Hire":
                strong_hires += 1

        screenings_data.append({
            "id": s.id,
            "job_title": s.job.title,
            "company": s.job.company or "—",
            "total_candidates": s.total_candidates,
            "created_at": s.created_at.strftime("%b %d, %Y"),
            "top_candidate": top.candidate_name if top else "—",
            "top_score": top.overall_score if top else None,
            "top_rec": top.recommendation if top else "—",
        })

    avg_score = round(score_sum / score_count) if score_count else "—"

    import career_services
    career_profile = career_services.get_or_create_profile(db, current_user)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "user": current_user,
            "total_screenings": total_screenings,
            "total_candidates": total_candidates,
            "strong_hires": strong_hires,
            "avg_score": avg_score,
            "screenings": screenings_data,
            "tier": current_user.tier or "FREE",
            "is_admin": admin_router.is_admin(current_user),
            "career_readiness": build_career_readiness(career_profile),
        }
    )


@app.get("/screen", response_class=HTMLResponse)
async def screen_page(request: Request, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Resume screening tool — requires login."""
    if not request.cookies.get("access_token"):
        return RedirectResponse("/login")

    session_id = request.query_params.get("session")
    past_result = None
    if session_id:
        screening = db.query(models.Screening).options(joinedload(models.Screening.job), joinedload(models.Screening.candidates)).filter(
            models.Screening.id == int(session_id),
            models.Screening.user_id == current_user.id
        ).first()
        if screening:
            candidates = sorted(
                [c.result_json for c in screening.candidates if c.result_json],
                key=lambda x: x.get("overall_score", 0), reverse=True
            )
            past_result = {
                "screening_id": screening.id,
                "job_title": screening.job.title,
                "company_name": screening.job.company or "",
                "total_processed": len(candidates),
                "total_errors": 0,
                "results": candidates,
                "errors": []
            }

    return templates.TemplateResponse(
        request=request,
        name="screen.html",
        context={
            "user": current_user,
            "past_result": past_result,
            "csrf_token": get_csrf_token(request),
            "quota": screening_quota(current_user),
            "tier": current_user.tier or "FREE",
            "is_admin": admin_router.is_admin(current_user),
        }
    )


@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


@app.get("/resume-builder", response_class=HTMLResponse)
async def resume_builder_page(request: Request, current_user: models.User = Depends(get_current_user)):
    if not request.cookies.get("access_token"):
        return RedirectResponse("/login")
    return templates.TemplateResponse(
        request=request,
        name="resume_builder.html",
        context={
            "user": current_user,
            "csrf_token": get_csrf_token(request),
            "tier": current_user.tier or "FREE",
            "is_admin": admin_router.is_admin(current_user),
        }
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, current_user: models.User = Depends(get_current_user)):
    if not request.cookies.get("access_token"):
        return RedirectResponse("/login")
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "user": current_user,
            "tier": current_user.tier or "FREE",
            "premium_until": current_user.premium_until,
            "is_admin": admin_router.is_admin(current_user),
        }
    )


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    if not request.cookies.get("access_token"):
        return RedirectResponse("/login")

    total_screenings = db.query(models.Screening).filter(
        models.Screening.user_id == current_user.id
    ).count()
    total_candidates = db.query(func.sum(models.Screening.total_candidates)).filter(
        models.Screening.user_id == current_user.id
    ).scalar() or 0

    candidates = (
        db.query(models.Candidate)
        .join(models.Screening)
        .filter(models.Screening.user_id == current_user.id, models.Candidate.status == "COMPLETED")
        .all()
    )
    hire_count = sum(1 for c in candidates if c.recommendation in ("Hire", "Strong Hire"))
    strong_hire_count = sum(1 for c in candidates if c.recommendation == "Strong Hire")

    return templates.TemplateResponse(
        request=request,
        name="analytics.html",
        context={
            "user": current_user,
            "tier": current_user.tier or "FREE",
            "is_admin": admin_router.is_admin(current_user),
            "total_screenings": total_screenings,
            "total_candidates": total_candidates,
            "hire_count": hire_count,
            "strong_hire_count": strong_hire_count,
        }
    )


@app.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    return templates.TemplateResponse(request=request, name="about.html", context={})


@app.get("/why-choose-us", response_class=HTMLResponse)
async def why_choose_us_page(request: Request):
    return templates.TemplateResponse(request=request, name="why-choose-us.html", context={})


@app.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    return templates.TemplateResponse(request=request, name="terms.html", context={})


@app.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request):
    return templates.TemplateResponse(request=request, name="admin_contact.html", context={})


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n🚀 Aptura AI starting...")
    print("📍 Open in browser: http://localhost:8000\n")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)


# ── Custom 404 / 405 Handlers ─────────────────────────────────────────────────
# API calls (/api/*, /auth/*) get a JSON error back so frontend fetch() calls
# can actually parse the response — everything else gets the friendly redirect.

def _expects_json_response(request: _Request) -> bool:
    path = request.url.path
    return (
        path.startswith("/api/")
        or path.startswith("/auth/")
        or "application/json" in request.headers.get("accept", "")
    )


@app.exception_handler(404)
async def not_found_handler(request: _Request, exc: _HTTPException):
    if _expects_json_response(request):
        return JSONResponse(status_code=404, content={"success": False, "error": "Not found."})
    if request.cookies.get("access_token"):
        return RedirectResponse("/dashboard")
    return RedirectResponse("/login")


@app.exception_handler(405)
async def method_not_allowed_handler(request: _Request, exc: _HTTPException):
    if _expects_json_response(request):
        return JSONResponse(status_code=405, content={"success": False, "error": "Method not allowed."})
    if request.cookies.get("access_token"):
        return RedirectResponse("/dashboard")
    return RedirectResponse("/login")


@app.exception_handler(_HTTPException)
async def http_exception_handler(request: _Request, exc: _HTTPException):
    if _expects_json_response(request):
        return JSONResponse(
            status_code=exc.status_code,
            content={"success": False, "error": exc.detail, "detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )
    if exc.status_code in {401, 404, 405}:
        if request.cookies.get("access_token"):
            return RedirectResponse("/dashboard")
        return RedirectResponse("/login")
    raise exc
