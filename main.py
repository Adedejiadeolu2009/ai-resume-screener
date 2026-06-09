"""
main.py — App Entry Point
==========================
Run with:   python main.py
Then open:  http://localhost:8000
"""

from fastapi.exceptions import HTTPException as _HTTPException
from fastapi.responses import Response as _Response
from fastapi import Request as _Request
import screen_router
import auth_router
import plans_router
import admin_router
from security import get_current_user

from database import engine, get_db, Base
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi import FastAPI, Request, Depends
from dotenv import load_dotenv
import uvicorn
import os
import sys
import logging
from pathlib import Path
from sqlalchemy import inspect, text

# ── Fix Python path for uvicorn reload workers ────────────────────────────────
# Uvicorn's --reload spawns new processes that don't know your project folder.
# This ensures 'routers', 'security', 'models' etc. are always findable.
PROJECT_DIR = Path(__file__).parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


# ── Load .env using the exact location of this script ────────────────────────
ENV_PATH = PROJECT_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger("aptura")

if ENV_PATH.exists():
    logger.info(".env file found")
else:
    logger.warning(".env file not found at: %s", ENV_PATH)

import models  # noqa: F401

# Create all DB tables on startup (safe to run every time — never deletes data)
Base.metadata.create_all(bind=engine)
logger.info("Database tables ready")

# ── Lightweight DB migrations for local SQLite (adds new columns if missing)


def ensure_db_columns():
    insp = inspect(engine)
    tables = insp.get_table_names()
    if 'users' not in tables:
        return
    existing = [c['name'] for c in insp.get_columns('users')]
    with engine.connect() as conn:
        # Add `tier` column
        if 'tier' not in existing:
            try:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN tier VARCHAR(20) DEFAULT 'FREE'"))
            except Exception:
                pass
        # Add `premium_until` column
        if 'premium_until' not in existing:
            try:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN premium_until DATETIME"))
            except Exception:
                pass
        # Add `screenings_used_this_month` column
        if 'screenings_used_this_month' not in existing:
            try:
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN screenings_used_this_month INTEGER DEFAULT 0"))
            except Exception:
                pass
        # Add `usage_reset_date` column
        if 'usage_reset_date' not in existing:
            try:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN usage_reset_date DATETIME"))
            except Exception:
                pass
        # Add `is_admin` column
        if 'is_admin' not in existing:
            try:
                conn.execute(
                    text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT 0"))
            except Exception:
                pass


# Run migrations now
ensure_db_columns()

# ── App ──────────────────────────────────────────────────────────────────────


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


APP_ENV = os.getenv("APP_ENV", "development").lower()
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")
IS_PRODUCTION = APP_ENV == "production" or APP_BASE_URL.startswith("https://")
SECRET_KEY = os.getenv("SECRET_KEY", "")

if IS_PRODUCTION:
    if not SECRET_KEY or SECRET_KEY in {
        "dev-secret-key-change-before-going-live",
        "dev-secret-key-change-in-production",
    } or len(SECRET_KEY) < 32:
        raise RuntimeError(
            "Production requires a strong SECRET_KEY of at least 32 characters."
        )
    if APP_BASE_URL.startswith("http://localhost"):
        raise RuntimeError(
            "Production requires APP_BASE_URL to be your HTTPS domain.")

app = FastAPI(title="Aptura AI", version="2.0.0")

allowed_hosts = [
    host.strip()
    for host in os.getenv("ALLOWED_HOSTS", "").split(",")
    if host.strip()
]
if IS_PRODUCTION and allowed_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)

app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY or "dev-secret-key-change-before-going-live",
    session_cookie="aptura_session",
    max_age=3600,
    same_site="lax",
    https_only=IS_PRODUCTION,
)


# CSRF utilities for cookie-auth
# We store a per-session token in Starlette's session (server-side)
# and require it on all state-changing POST requests.

def get_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        import secrets
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def require_csrf(request: Request, csrf_token: str | None) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not csrf_token or csrf_token != expected:
        raise _HTTPException(status_code=403, detail="CSRF validation failed")


BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(auth_router.router)
app.include_router(screen_router.router)
app.include_router(plans_router.router)
app.include_router(admin_router.router)


@app.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request):
    return templates.TemplateResponse(request=request, name="admin_contact.html", context={})


@app.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    return templates.TemplateResponse(request=request, name="about.html", context={})


@app.get("/why-choose-us", response_class=HTMLResponse)
async def why_us_page(request: Request):
    return templates.TemplateResponse(request=request, name="why-choose-us.html", context={})


@app.get("/terms", response_class=HTMLResponse)
async def terms_page(request: Request):
    return templates.TemplateResponse(request=request, name="terms.html", context={})


# ── Page Routes ───────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    """Homepage -> landing page for visitors, dashboard for signed-in users."""
    if request.cookies.get("access_token"):
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={},
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login / Register page."""
    if request.cookies.get("access_token"):
        return RedirectResponse("/dashboard")
    error = request.query_params.get("error", "")
    # ✅ New Starlette 0.41+ syntax — request is passed separately, NOT in context
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": error}
    )


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    # CSRF token (available in templates)
    csrf_token = get_csrf_token(request)

    """Dashboard — requires login."""
    if not request.cookies.get("access_token"):
        return RedirectResponse("/login")

    # ── Gather stats ──────────────────────────────────────────────────────────
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

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={

            "user": current_user,
            "tier": current_user.tier or "FREE",
            "premium_until": current_user.premium_until,
            "screenings_used": current_user.screenings_used_this_month or 0,
            "total_screenings": total_screenings,
            "total_candidates": total_candidates,
            "strong_hires": strong_hires,
            "avg_score": avg_score,
            "screenings": screenings_data,
            "is_admin": admin_router.is_admin(current_user),
            "csrf_token": csrf_token,
        }
    )


@app.get("/screen", response_class=HTMLResponse)
async def screen_page(request: Request, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Resume screening tool — requires login."""
    if not request.cookies.get("access_token"):
        return RedirectResponse("/login")

    # Load previous screening if ?session=ID is in the URL
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
                "job_title": screening.job.title,
                "company_name": screening.job.company or "",
                "total_processed": len(candidates),
                "total_errors": 0,
                "results": candidates,
                "errors": []
            }

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "user": current_user,
            "past_result": past_result,
        }
    )


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(request: Request, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Analytics dashboard — requires login."""
    if not request.cookies.get("access_token"):
        return RedirectResponse("/login")

    # Get screening statistics
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
        .limit(50)
        .all()
    )

    strong_hire_count = 0
    hire_count = 0
    maybe_count = 0
    no_hire_count = 0

    for s in recent:
        for c in s.candidates:
            if c.recommendation == "Strong Hire":
                strong_hire_count += 1
            elif c.recommendation == "Hire":
                hire_count += 1
            elif c.recommendation == "Maybe":
                maybe_count += 1
            elif c.recommendation == "No Hire":
                no_hire_count += 1

    return templates.TemplateResponse(
        request=request,
        name="analytics.html",
        context={
            "user": current_user,
            "tier": current_user.tier or "FREE",
            "total_screenings": total_screenings,
            "total_candidates": total_candidates,
            "strong_hire_count": strong_hire_count,
            "hire_count": hire_count,
            "maybe_count": maybe_count,
            "no_hire_count": no_hire_count,
        }
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Settings page — requires login."""
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


@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    reload_enabled = _is_truthy(
        os.getenv("UVICORN_RELOAD", "true" if not IS_PRODUCTION else "false")
    )
    logger.info("Aptura AI starting on http://0.0.0.0:8000")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=reload_enabled)


# ── Custom 404 Handler ────────────────────────────────────────────────────────
# Without this, FastAPI returns raw JSON like {"detail":"Not Found"} for
# unknown URLs. This replaces that with a friendly redirect.


@app.exception_handler(404)
async def not_found_handler(request: _Request, exc: _HTTPException):
    """
    When any URL doesn't exist:
    - If the user is logged in  → send to dashboard
    - If the user is not logged in → send to login page
    Instead of showing ugly {"detail":"Not Found"} JSON.
    """
    if request.cookies.get("access_token"):
        return RedirectResponse("/dashboard")
    return RedirectResponse("/login")


@app.exception_handler(405)
async def method_not_allowed_handler(request: _Request, exc: _HTTPException):
    """When someone visits an API URL directly in their browser (GET on a POST route)."""
    if request.cookies.get("access_token"):
        return RedirectResponse("/dashboard")
    return RedirectResponse("/login")


@app.exception_handler(401)
async def unauthorized_handler(request: _Request, exc: _HTTPException):
    """Send unauthenticated browser page requests to login instead of raw JSON."""
    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        return RedirectResponse("/login")
    return _Response(
        content='{"detail":"Not authenticated"}',
        status_code=401,
        media_type="application/json",
    )
