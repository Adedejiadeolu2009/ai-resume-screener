from __future__ import annotations

from sqlalchemy.orm import Session
from fastapi import HTTPException

import models


ROLE_CANDIDATE = "candidate"
ROLE_RECRUITER = "recruiter"

VALID_ROLES = {ROLE_CANDIDATE, ROLE_RECRUITER}

ROLE_META = {
    ROLE_CANDIDATE: {
        "label": "Candidate / Job Seeker",
        "short_label": "Candidate",
        "legacy_workspace": "APPLICANT",
        "dashboard": "/candidate/dashboard",
        "description": "Build your career profile, improve your CV, and find relevant opportunities.",
        "setup_label": "Setting up your Candidate workspace...",
        "primary_action": "Match my resume",
        "primary_action_href": "/agent",
        "secondary_action": "Improve my CV",
        "secondary_action_href": "/resume-builder",
        "nav": [
            ("Overview", "/candidate/dashboard"),
            ("Career Passport", "/agent"),
            ("Resume Intelligence", "/resume-builder"),
            ("Opportunities", "/job-match"),
            ("Interview Prep", "/agent"),
            ("Copilot", "/agent"),
        ],
        "onboarding_steps": ["Target role", "Experience", "Skills", "CV upload", "Career preferences"],
    },
    ROLE_RECRUITER: {
        "label": "Recruiter / Employer",
        "short_label": "Recruiter",
        "legacy_workspace": "RECRUITER",
        "dashboard": "/recruiter/dashboard",
        "description": "Post jobs, understand candidates, and build evidence-backed shortlists.",
        "setup_label": "Setting up your Recruiter workspace...",
        "primary_action": "Review shortlist",
        "primary_action_href": "/screen",
        "secondary_action": "Create vacancy",
        "secondary_action_href": "/recruiter",
        "nav": [
            ("Overview", "/recruiter/dashboard"),
            ("Jobs", "/recruiter"),
            ("Applications", "/screen"),
            ("Candidates", "/screen"),
            ("Shortlists", "/screen"),
            ("Talent Search", "/screen"),
            ("Verification", "/analytics"),
            ("Analytics", "/analytics"),
        ],
        "onboarding_steps": ["Company", "Hiring role", "Team size", "Hiring needs", "First job creation"],
    },
}

LEGACY_TO_ROLE = {
    # Older student accounts continue as candidate accounts.
    "STUDENT": ROLE_CANDIDATE,
    "APPLICANT": ROLE_CANDIDATE,
    "RECRUITER": ROLE_RECRUITER,
}


def normalize_role(role: str | None) -> str | None:
    value = (role or "").strip().lower()
    if value in {"applicant", "job_seeker", "job-seeker", "jobseeker"}:
        value = ROLE_CANDIDATE
    if value == "employer":
        value = ROLE_RECRUITER
    return value if value in VALID_ROLES else None


def legacy_workspace_for(role: str | None) -> str:
    normalized = normalize_role(role) or ROLE_CANDIDATE
    return ROLE_META[normalized]["legacy_workspace"]


def active_role(user: models.User) -> str | None:
    return normalize_role(user.active_workspace) or normalize_role(user.primary_role)


def available_roles(user: models.User) -> list[str]:
    roles = user.available_roles if isinstance(
        user.available_roles, list) else []
    normalized = [role for role in (normalize_role(item)
                                    for item in roles) if role]
    primary = normalize_role(user.primary_role)
    if primary and primary not in normalized:
        normalized.insert(0, primary)
    if not normalized and user.onboarding_completed:
        legacy = LEGACY_TO_ROLE.get((user.workspace or "").upper())
        if legacy:
            normalized.append(legacy)
    return list(dict.fromkeys(normalized))


def role_options(user: models.User) -> list[dict]:
    active = active_role(user)
    enabled = set(available_roles(user))
    return [
        {
            **meta,
            "key": role,
            "active": role == active,
            "enabled": role in enabled,
            "activation_label": f"Add {meta['short_label']} Workspace",
        }
        for role, meta in ROLE_META.items()
    ]


def needs_role_onboarding(user: models.User) -> bool:
    return not bool(user.onboarding_completed and normalize_role(user.primary_role))


def dashboard_path_for(role: str | None) -> str:
    normalized = normalize_role(role) or ROLE_CANDIDATE
    return ROLE_META[normalized]["dashboard"]


def dashboard_meta_for(role: str | None) -> dict:
    normalized = normalize_role(role) or ROLE_CANDIDATE
    return {"key": normalized, **ROLE_META[normalized]}


def has_role(user: models.User, role: str) -> bool:
    normalized = normalize_role(role)
    return bool(normalized and normalized in available_roles(user))


def require_role(user: models.User, role: str) -> None:
    if not has_role(user, role):
        raise HTTPException(
            status_code=403,
            detail="Activate this workspace before using this feature.",
        )


def onboarding_progress(user: models.User, role: str | None = None) -> dict:
    active = normalize_role(role) or active_role(user) or ROLE_CANDIDATE
    prefs = user.workspace_preferences if isinstance(
        user.workspace_preferences, dict) else {}
    role_prefs = prefs.get(active) if isinstance(
        prefs.get(active), dict) else {}
    progress = role_prefs.get("onboarding") if isinstance(
        role_prefs.get("onboarding"), dict) else {}
    completed = progress.get("completed_steps") if isinstance(
        progress.get("completed_steps"), list) else []
    skipped = progress.get("skipped_steps") if isinstance(
        progress.get("skipped_steps"), list) else []
    valid_steps = ROLE_META[active]["onboarding_steps"]
    return {
        "role": active,
        "completed_steps": [step for step in completed if step in valid_steps],
        "skipped_steps": [step for step in skipped if step in valid_steps],
        "is_skipped": bool(progress.get("is_skipped")),
    }


def save_onboarding_progress(
    db: Session,
    user: models.User,
    role: str | None,
    completed_steps: list[str] | None = None,
    skipped_steps: list[str] | None = None,
    is_skipped: bool = False,
) -> models.User:
    active = normalize_role(role) or active_role(user) or ROLE_CANDIDATE
    if active not in VALID_ROLES:
        raise ValueError("Invalid workspace role.")

    valid_steps = set(ROLE_META[active]["onboarding_steps"])
    prefs = user.workspace_preferences if isinstance(
        user.workspace_preferences, dict) else {}
    role_prefs = prefs.get(active) if isinstance(
        prefs.get(active), dict) else {}
    role_prefs["onboarding"] = {
        "completed_steps": [step for step in (completed_steps or []) if step in valid_steps],
        "skipped_steps": [step for step in (skipped_steps or []) if step in valid_steps],
        "is_skipped": bool(is_skipped),
    }
    prefs[active] = role_prefs
    user.workspace_preferences = prefs
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def apply_primary_role(db: Session, user: models.User, role: str) -> models.User:
    normalized = normalize_role(role)
    if not normalized:
        raise ValueError("Invalid workspace role.")
    user.primary_role = normalized
    user.active_workspace = normalized
    user.available_roles = [normalized]
    user.workspace = legacy_workspace_for(normalized)
    user.onboarding_completed = True
    prefs = user.workspace_preferences if isinstance(
        user.workspace_preferences, dict) else {}
    prefs.setdefault(normalized, {})
    user.workspace_preferences = prefs
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def activate_role(db: Session, user: models.User, role: str) -> models.User:
    normalized = normalize_role(role)
    if not normalized:
        raise ValueError("Invalid workspace role.")
    roles = available_roles(user)
    if normalized not in roles:
        roles.append(normalized)
    user.available_roles = roles
    user.active_workspace = normalized
    user.workspace = legacy_workspace_for(normalized)
    prefs = user.workspace_preferences if isinstance(
        user.workspace_preferences, dict) else {}
    prefs.setdefault(normalized, {})
    user.workspace_preferences = prefs
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def switch_role(db: Session, user: models.User, role: str) -> models.User:
    normalized = normalize_role(role)
    if not normalized or normalized not in available_roles(user):
        raise PermissionError("Workspace is not active for this account.")
    user.active_workspace = normalized
    user.workspace = legacy_workspace_for(normalized)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
