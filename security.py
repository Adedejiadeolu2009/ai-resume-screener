"""
auth.py — Authentication Utilities
====================================
This file handles all the security logic:
  1. Password hashing  — never store plain-text passwords
  2. JWT tokens        — the "ticket" users carry after logging in
  3. get_current_user  — a dependency that reads the token and finds the user
"""

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Request, HTTPException, status, Depends
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from database import get_db
import models

load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=False)

# ── Config ────────────────────────────────────────────────────────────────────
SECRET_KEY  = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
ALGORITHM   = "HS256"
# Token lasts 7 days — user stays logged in for a week without re-entering password
TOKEN_EXPIRE_DAYS = 7

# ── Password Hashing ──────────────────────────────────────────────────────────
# bcrypt is the gold-standard algorithm for hashing passwords.
# It's intentionally slow to make brute-force attacks very hard.
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(plain_password: str) -> str:
    """Turn a plain password into a safe hash. e.g. 'mypassword' → '$2b$12$...'"""
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check if a plain password matches its hash. Used at login."""
    return pwd_context.verify(plain_password, hashed_password)


# ── JWT Tokens ────────────────────────────────────────────────────────────────
# JWT (JSON Web Token) = a signed "ticket" that proves who you are.
# It's stored in a cookie in the user's browser.
# Structure: header.payload.signature  — only our server can create a valid signature.

def create_access_token(user_id: int) -> str:
    """
    Create a JWT token for a logged-in user.
    The token contains the user's ID and an expiry timestamp.
    """
    expire = datetime.utcnow() + timedelta(days=TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": str(user_id),   # "sub" = subject (who is this token for)
        "exp": expire,          # expiry datetime
        "iat": datetime.utcnow() # issued at
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[int]:
    """
    Read a JWT token and return the user_id inside it.
    Returns None if the token is invalid or expired.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
        return user_id
    except (JWTError, TypeError, ValueError):
        return None


# ── Current User Dependency ───────────────────────────────────────────────────
def get_current_user(request: Request, db: Session = Depends(get_db)) -> models.User:
    """
    FastAPI dependency. Add this to any route that requires login:
        current_user: models.User = Depends(get_current_user)

    It reads the JWT token from the browser cookie, decodes it,
    looks up the user in the database, and returns the User object.
    Raises a 401 error if not logged in.
    """
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated"
        )

    user_id = decode_access_token(token)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session. Please log in again."
        )

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found."
        )

    return user


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> Optional[models.User]:
    """
    Like get_current_user but doesn't raise an error if not logged in.
    Returns None instead. Used for pages that work for both guests and logged-in users.
    """
    try:
        return get_current_user(request, db)
    except HTTPException:
        return None


# ── OAuth Helper ──────────────────────────────────────────────────────────────
def get_or_create_oauth_user(
    db: Session,
    email: str,
    name: str,
    provider: str,
    provider_id: str,
    avatar_url: Optional[str] = None
) -> models.User:
    """
    Called after a successful Google or Apple login.
    Either finds the existing user or creates a new one.
    Also handles the case where someone already has an email account
    and then tries to log in with Google — it links the accounts.
    """
    # 1. Try to find by provider + provider_id (most reliable)
    user = db.query(models.User).filter(
        models.User.provider == provider,
        models.User.provider_id == provider_id
    ).first()

    if user:
        # Update their name/avatar in case it changed on Google
        user.name = name or user.name
        user.avatar_url = avatar_url or user.avatar_url
        user.last_login = datetime.utcnow()
        db.commit()
        return user

    # 2. Try to find by email (link existing account)
    user = db.query(models.User).filter(models.User.email == email).first()
    if user:
        # Link the OAuth provider to this existing account
        user.provider = provider
        user.provider_id = provider_id
        user.avatar_url = avatar_url or user.avatar_url
        user.name = name or user.name
        user.last_login = datetime.utcnow()
        db.commit()
        return user

    # 3. Create a brand new user
    user = models.User(
        email=email,
        name=name,
        provider=provider,
        provider_id=provider_id,
        avatar_url=avatar_url,
        last_login=datetime.utcnow()
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
