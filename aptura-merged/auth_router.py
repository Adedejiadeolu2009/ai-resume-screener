"""
routers/auth_router.py — All Authentication Routes

NOTE: We do NOT use starlette Config() here because load_dotenv() in main.py
already loads all .env values into os.environ before this file runs.
We just read directly from os.environ using os.getenv().
"""

import os
from datetime import datetime

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

import security as auth
import models
from database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])

# ── Read credentials directly from environment ────────────────────────────────
# These are read at request time (inside route functions), not at import time,
# so load_dotenv() in main.py always runs first.
def get_google_creds():
    return os.getenv("GOOGLE_CLIENT_ID", ""), os.getenv("GOOGLE_CLIENT_SECRET", "")

def get_github_creds():
    return os.getenv("GITHUB_CLIENT_ID", ""), os.getenv("GITHUB_CLIENT_SECRET", "")

APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:8000")

# ── OAuth client ──────────────────────────────────────────────────────────────
oauth = OAuth()

def setup_oauth():
    """Called once when the first request comes in — by then load_dotenv has run."""
    google_id, google_secret = get_google_creds()
    github_id, github_secret = get_github_creds()

    if google_id and not google_id.startswith("your-") \
       and not google_id.startswith("paste-"):
        try:
            oauth.register(
                name="google",
                client_id=google_id,
                client_secret=google_secret,
                server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                client_kwargs={"scope": "openid email profile", "prompt": "select_account"},
            )
            print("✅ Google OAuth ready")
        except Exception:
            pass  # Already registered on a previous request

    if github_id and not github_id.startswith("your-") \
       and not github_id.startswith("paste-"):
        try:
            oauth.register(
                name="github",
                client_id=github_id,
                client_secret=github_secret,
                access_token_url="https://github.com/login/oauth/access_token",
                authorize_url="https://github.com/login/oauth/authorize",
                api_base_url="https://api.github.com/",
                client_kwargs={"scope": "user:email"},
            )
            print("✅ GitHub OAuth ready")
        except Exception:
            pass  # Already registered on a previous request

_oauth_setup_done = False

def ensure_oauth_ready():
    global _oauth_setup_done
    if not _oauth_setup_done:
        setup_oauth()
        _oauth_setup_done = True


# ── Helper: set JWT token as a cookie ─────────────────────────────────────────
def set_auth_cookie(response, user: models.User):
    token = auth.create_access_token(user.id)
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=APP_BASE_URL.startswith("https"),
        max_age=60 * 60 * 24 * 7   # 7 days
    )
    return response


# ── Email Registration ────────────────────────────────────────────────────────
@router.post("/register")
async def register(
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    existing = db.query(models.User).filter(
        models.User.email == email.lower().strip()
    ).first()
    if existing:
        return JSONResponse(
            {"error": "An account with this email already exists. Try signing in instead."},
            status_code=400
        )
    if len(password) < 8:
        return JSONResponse(
            {"error": "Password must be at least 8 characters."},
            status_code=400
        )

    user = models.User(
        email=email.lower().strip(),
        name=name.strip(),
        hashed_password=auth.hash_password(password),
        provider="email",
        last_login=datetime.utcnow()
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    response = JSONResponse({"success": True, "redirect": "/dashboard"})
    return set_auth_cookie(response, user)


# ── Email Login ───────────────────────────────────────────────────────────────
@router.post("/login")
async def login(
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(models.User).filter(
        models.User.email == email.lower().strip()
    ).first()

    if not user or not user.hashed_password or \
       not auth.verify_password(password, user.hashed_password):
        return JSONResponse({"error": "Incorrect email or password."}, status_code=401)

    if not user.is_active:
        return JSONResponse({"error": "This account has been disabled."}, status_code=403)

    user.last_login = datetime.utcnow()
    db.commit()

    response = JSONResponse({"success": True, "redirect": "/dashboard"})
    return set_auth_cookie(response, user)


# ── Google OAuth ──────────────────────────────────────────────────────────────
@router.get("/google")
async def google_login(request: Request):
    ensure_oauth_ready()
    google_id, _ = get_google_creds()
    if not google_id or google_id.startswith("your-") or google_id.startswith("paste-"):
        return RedirectResponse(
            "/login?error=Google sign-in is not set up yet. Use email login for now."
        )
    redirect_uri = os.getenv("APP_BASE_URL", "http://localhost:8000") + "/auth/google/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as e:
        return RedirectResponse(f"/login?error=Google sign-in failed. Please try again.")

    user_info = token.get("userinfo")
    if not user_info:
        return RedirectResponse("/login?error=Could not retrieve your Google profile.")

    user = auth.get_or_create_oauth_user(
        db=db,
        email=user_info["email"],
        name=user_info.get("name", ""),
        provider="google",
        provider_id=user_info["sub"],
        avatar_url=user_info.get("picture")
    )
    response = RedirectResponse("/dashboard", status_code=302)
    return set_auth_cookie(response, user)


# ── GitHub OAuth ──────────────────────────────────────────────────────────────
@router.get("/github")
async def github_login(request: Request):
    ensure_oauth_ready()
    github_id, _ = get_github_creds()
    if not github_id or github_id.startswith("your-") or github_id.startswith("paste-"):
        return RedirectResponse(
            "/login?error=GitHub sign-in is not set up yet. Use email login for now."
        )
    redirect_uri = os.getenv("APP_BASE_URL", "http://localhost:8000") + "/auth/github/callback"
    return await oauth.github.authorize_redirect(request, redirect_uri)


@router.get("/github/callback")
async def github_callback(request: Request, db: Session = Depends(get_db)):
    try:
        token = await oauth.github.authorize_access_token(request)
    except OAuthError:
        return RedirectResponse("/login?error=GitHub sign-in failed. Please try again.")

    # Fetch GitHub profile
    resp = await oauth.github.get("user", token=token)
    profile = resp.json()

    # GitHub doesn't always include email in profile — fetch separately
    email = profile.get("email")
    if not email:
        email_resp = await oauth.github.get("user/emails", token=token)
        emails = email_resp.json()
        primary = next(
            (e["email"] for e in emails if e.get("primary") and e.get("verified")),
            None
        )
        email = primary or f"github_{profile['id']}@users.noreply.github.com"

    user = auth.get_or_create_oauth_user(
        db=db,
        email=email,
        name=profile.get("name") or profile.get("login", "GitHub User"),
        provider="github",
        provider_id=str(profile["id"]),
        avatar_url=profile.get("avatar_url")
    )
    response = RedirectResponse("/dashboard", status_code=302)
    return set_auth_cookie(response, user)


# ── Logout ────────────────────────────────────────────────────────────────────
@router.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("access_token")
    return response