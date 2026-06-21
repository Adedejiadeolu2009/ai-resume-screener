"""
routers/auth_router.py — All Authentication Routes

NOTE: We do NOT use starlette Config() here because load_dotenv() in main.py
already loads all .env values into os.environ before this file runs.
We just read directly from os.environ using os.getenv().
"""

import os
import logging
import secrets
from datetime import datetime, timedelta

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session

import security as auth
import models
from database import get_db
from email_utils import send_email

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

# ── Read credentials directly from environment ────────────────────────────────
# These are read at request time (inside route functions), not at import time,
# so load_dotenv() in main.py always runs first.


def get_google_creds():
    return os.getenv("GOOGLE_CLIENT_ID", "").strip(), os.getenv("GOOGLE_CLIENT_SECRET", "").strip()


def get_github_creds():
    return os.getenv("GITHUB_CLIENT_ID", "").strip(), os.getenv("GITHUB_CLIENT_SECRET", "").strip()


def get_app_base_url() -> str:
    return os.getenv("APP_BASE_URL", "http://localhost:8000").strip().rstrip("/")


async def read_request_data(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = await request.json()
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    try:
        form = await request.form()
        return dict(form)
    except Exception:
        return {}


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
                client_kwargs={"scope": "openid email profile",
                               "prompt": "select_account"},
            )
            logger.info("Google OAuth ready")
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
            logger.info("GitHub OAuth ready")
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
    app_base_url = get_app_base_url()
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=app_base_url.startswith("https"),
        max_age=60 * 60 * 24 * 7   # 7 days
    )
    return response


# ── Email Registration ────────────────────────────────────────────────────────
@router.post("/register")
async def register(
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    request: Request = None,
):
    # CSRF validation
    import main as _main
    _main.require_csrf(request, csrf_token)

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
    # bcrypt has a 72-byte input limit; reject overly long passwords early.
    if len(password.encode("utf-8")) > 72:
        return JSONResponse(
            {"error": "Password is too long. Use a password under 72 bytes (≈72 characters)."},
            status_code=400,
        )

    try:
        hashed = auth.hash_password(password)
    except ValueError:
        return JSONResponse({"error": "Password too long for the hashing backend. Trim to 72 bytes."}, status_code=400)

    user = models.User(
        email=email.lower().strip(),
        name=name.strip(),
        hashed_password=hashed,
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
    request: Request,
    db: Session = Depends(get_db),
):
    data = await read_request_data(request)
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    if not email or not password:
        return JSONResponse({"error": "Email and password are required."}, status_code=400)

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


@router.post("/forgot-password")
async def forgot_password(request: Request, db: Session = Depends(get_db)):
    data = await read_request_data(request)
    email = (data.get("email") or "").lower().strip()
    if not email:
        return JSONResponse({"error": "Email address is required."}, status_code=400)

    user = db.query(models.User).filter(models.User.email == email).first()
    if user and user.hashed_password:
        otp = f"{secrets.randbelow(1_000_000):06d}"
        reset = models.PasswordResetOTP(
            user_id=user.id,
            email=user.email,
            otp_hash=auth.hash_password(otp),
            expires_at=datetime.utcnow() + timedelta(minutes=15),
        )
        db.add(reset)
        db.commit()

        base_url = get_app_base_url()
        subject = "Aptura AI password reset code"
        plain = (
            f"Hi {user.name or user.email},\n\n"
            f"Your Aptura AI password reset code is {otp}. "
            "It expires in 15 minutes.\n\n"
            f"Return to {base_url}/login to set a new password.\n\n"
            "If you did not request this, you can ignore this email."
        )
        html = f"""
        <div style="font-family:Arial,Helvetica,sans-serif;background:#0A0B0E;color:#F0EDE6;padding:24px">
          <div style="max-width:560px;margin:auto;background:#13151C;border:1px solid rgba(255,255,255,.08);border-radius:12px;padding:24px">
            <h1 style="margin:0 0 10px;color:#F0EDE6">Reset your Aptura AI password</h1>
            <p style="color:#C9C5BE;line-height:1.6">Use this one-time code to choose a new password. It expires in 15 minutes.</p>
            <div style="font-size:34px;letter-spacing:8px;font-weight:800;color:#C9A84C;margin:22px 0">{otp}</div>
            <p style="color:#9A9691;font-size:13px">If you did not request this, ignore this email.</p>
          </div>
        </div>
        """
        email_sent = send_email(user.email, subject, plain, html_body=html)
        if not email_sent:
            logger.warning("Password reset email failed for %s", user.email)
            if os.getenv("APP_ENV", "development").lower() != "production":
                return JSONResponse(
                    {"error": "Could not send reset code. Check RESEND_API_KEY, EMAIL_FROM, and the Resend package installation."},
                    status_code=500,
                )

    return JSONResponse({
        "success": True,
        "message": "If that email has an Aptura account, a reset code has been sent.",
    })


@router.post("/reset-password")
async def reset_password(request: Request, db: Session = Depends(get_db)):
    data = await read_request_data(request)
    email = (data.get("email") or "").lower().strip()
    otp = (data.get("otp") or "").strip()
    password = data.get("password") or ""

    if not email or not otp or not password:
        return JSONResponse({"error": "Email, OTP, and new password are required."}, status_code=400)
    if len(password) < 8:
        return JSONResponse({"error": "Password must be at least 8 characters."}, status_code=400)
    if len(password.encode("utf-8")) > 72:
        return JSONResponse({"error": "Password is too long. Use a password under 72 bytes."}, status_code=400)

    user = db.query(models.User).filter(models.User.email == email).first()
    reset = (
        db.query(models.PasswordResetOTP)
        .filter(
            models.PasswordResetOTP.email == email,
            models.PasswordResetOTP.used_at.is_(None),
            models.PasswordResetOTP.expires_at > datetime.utcnow(),
        )
        .order_by(models.PasswordResetOTP.created_at.desc())
        .first()
    )
    if not user or not reset or reset.attempts >= 5:
        return JSONResponse({"error": "Invalid or expired reset code."}, status_code=400)

    reset.attempts = (reset.attempts or 0) + 1
    if not auth.verify_password(otp, reset.otp_hash):
        db.commit()
        return JSONResponse({"error": "Invalid or expired reset code."}, status_code=400)

    user.hashed_password = auth.hash_password(password)
    user.provider = user.provider or "email"
    reset.used_at = datetime.utcnow()
    db.commit()

    return JSONResponse({"success": True, "message": "Password reset successfully. You can sign in now."})


# ── Google OAuth ──────────────────────────────────────────────────────────────
@router.get("/google")
async def google_login(request: Request):
    ensure_oauth_ready()
    google_id, _ = get_google_creds()
    if not google_id or google_id.startswith("your-") or google_id.startswith("paste-"):
        return RedirectResponse(
            "/login?error=Google sign-in is not set up yet. Use email login for now."
        )
    redirect_uri = get_app_base_url() + "/auth/google/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/google/callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError:
        return RedirectResponse("/login?error=Google sign-in failed. Please try again.")

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
    redirect_uri = get_app_base_url() + "/auth/github/callback"
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
            (e["email"]
             for e in emails if e.get("primary") and e.get("verified")),
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
    response.delete_cookie(
        "access_token",
        httponly=True,
        samesite="lax",
        secure=get_app_base_url().startswith("https"),
    )
    return response


# ── Premium Upgrade (Manual Bank Transfer) ────────────────────────────────
# Since Paystack is removed, users will upgrade by:
#  1) Sending bank transfer (you confirm manually)
#  2) Hitting this endpoint to request tier activation
# NOTE: This endpoint does NOT validate bank transfers automatically.
# You should manually confirm and activate tiers (dev-safe for first business).

@router.post("/request-upgrade")
async def request_upgrade(
    plan: str = Form(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user)
):
    plan = (plan or "").upper().strip()
    if plan not in {"PRO", "ENTERPRISE"}:
        return JSONResponse({"success": False, "message": "Invalid plan. Choose PRO or ENTERPRISE."}, status_code=400)

    # Mark the request in DB by creating/using payments table for audit.
    # We reuse the Payment table structure but store paystack_ref as a human reference.
    # (If Payment table is unused in your DB, ensure migrations by recreating or creating tables.)
    try:
        # Generate an internal request id
        req_ref = f"manual_{current_user.id}_{int(datetime.utcnow().timestamp())}"

        existing = db.query(models.Payment).filter(
            models.Payment.paystack_ref == req_ref).first()
        if existing:
            return JSONResponse({"success": True, "message": "Upgrade request already recorded."})

        payment = models.Payment(
            user_id=current_user.id,
            paystack_ref=req_ref,
            amount=0,
            status="pending",
            plan=plan,
            verified_at=None,
        )
        db.add(payment)
        db.commit()

        return JSONResponse({
            "success": True,
            "message": "Upgrade request received. Await confirmation from admin.",
            "request_ref": req_ref,
        })
    except Exception as e:
        return JSONResponse({"success": False, "message": f"Error recording request: {str(e)}"}, status_code=500)
