"""
models.py — Database Tables
============================
Each class here = one table in your database.
SQLAlchemy automatically converts these Python classes into SQL tables.

Relationships:
  User ──< Job ──< Screening ──< Candidate
  (One user has many jobs, each job has many screenings, each screening has many candidates)
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from database import Base


class User(Base):
    """
    The 'users' table. Stores everyone who has an account.
    Works for all three login methods: email, Google, and Apple.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    name = Column(String(255), nullable=True)
    # Profile pic from Google/Apple
    avatar_url = Column(String(512), nullable=True)

    # Password auth — None for Google/Apple users (they don't need a password)
    hashed_password = Column(String(255), nullable=True)

    # Which login method did they use? "email", "google", or "apple"
    provider = Column(String(50), default="email", nullable=False)
    # The unique ID from Google or Apple (so we can find them on future logins)
    provider_id = Column(String(255), nullable=True, index=True)

    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)
    is_admin = Column(Boolean, default=False)

    # Premium tier — FREE or PREMIUM
    tier = Column(String(20), default="FREE",
                  nullable=False)  # "FREE" or "PREMIUM"
    # When does premium expire?
    premium_until = Column(DateTime, nullable=True)
    screenings_used_this_month = Column(
        Integer, default=0)    # Track monthly usage
    # When does monthly reset happen?
    usage_reset_date = Column(DateTime, nullable=True)

    # One user → many jobs
    jobs = relationship("Job", back_populates="owner",
                        cascade="all, delete-orphan")
    screenings = relationship(
        "Screening", back_populates="user", cascade="all, delete-orphan")
    payments = relationship(
        "Payment", back_populates="user", cascade="all, delete-orphan")
    career_profile = relationship(
        "CareerProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    job_matches = relationship(
        "JobMatch", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User {self.email}>"


class Job(Base):
    """
    The 'jobs' table. Each row is one job position a user is hiring for.
    """
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255), nullable=False)
    company = Column(String(255), nullable=True)
    description = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    owner = relationship("User", back_populates="jobs")
    screenings = relationship(
        "Screening", back_populates="job", cascade="all, delete-orphan")

    @property
    def total_candidates(self):
        """Total candidates screened across all screening sessions for this job."""
        return sum(s.total_candidates for s in self.screenings)


class Screening(Base):
    """
    The 'screenings' table. One screening = one batch of resumes uploaded for one job.
    A user might screen the same job multiple times as new candidates apply.
    """
    __tablename__ = "screenings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    total_candidates = Column(Integer, default=0)
    total_files = Column(Integer, default=0)
    processed_candidates = Column(Integer, default=0)
    status = Column(String(50), default="QUEUED", nullable=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="screenings")
    job = relationship("Job", back_populates="screenings")
    candidates = relationship(
        "Candidate", back_populates="screening", cascade="all, delete-orphan")


class Candidate(Base):
    """
    The 'candidates' table. One row = one resume screened.
    Stores the full AI result so the user can revisit it any time.
    """
    __tablename__ = "candidates"

    id = Column(Integer, primary_key=True, index=True)
    screening_id = Column(Integer, ForeignKey("screenings.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    candidate_name = Column(String(255), nullable=True)
    overall_score = Column(Integer, nullable=True)            # 0–100
    # "Strong Hire", "Hire", etc.
    recommendation = Column(String(50), nullable=True)
    status = Column(String(50), default="QUEUED", nullable=False)
    error_message = Column(Text, nullable=True)
    file_content_b64 = Column(Text, nullable=True)
    # The complete AI analysis
    result_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    screening = relationship("Screening", back_populates="candidates")


class CareerProfile(Base):
    """
    Candidate-side career workspace. Stores editable resume/profile state so
    uploads can be corrected, saved, and reused by Aptura Agent workflows.
    """
    __tablename__ = "career_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    target_role = Column(String(255), nullable=True)
    resume_text = Column(Text, nullable=True)
    resume_filename = Column(String(255), nullable=True)
    latest_analysis_json = Column(JSON, nullable=True)
    latest_skill_gap_json = Column(JSON, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="career_profile")


class JobMatch(Base):
    """
    Candidate-side match result for a user-provided opportunity.
    """
    __tablename__ = "job_matches"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    job_title = Column(String(255), nullable=False)
    company = Column(String(255), nullable=True)
    job_description = Column(Text, nullable=False)
    required_skills = Column(JSON, nullable=True)
    result_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="job_matches")


class RecruiterShortlist(Base):
    """
    Lightweight recruiter shortlist tied to existing screening candidates.
    """
    __tablename__ = "recruiter_shortlists"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    screening_id = Column(Integer, ForeignKey("screenings.id"), nullable=False)
    candidate_id = Column(Integer, ForeignKey("candidates.id"), nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class Payment(Base):
    """
    Tracks all Paystack payments for premium upgrades.
    Links to user for subscription management.
    """
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    paystack_ref = Column(String(255), unique=True,
                          nullable=False, index=True)  # Paystack reference
    # Amount in kobo (e.g., 3900 = ₦39.00)
    amount = Column(Integer, nullable=False)
    # "pending", "success", "failed"
    status = Column(String(50), default="pending")
    plan = Column(String(50), default="PREMIUM_MONTHLY")  # Plan type
    created_at = Column(DateTime, default=datetime.utcnow)
    # When payment was verified
    verified_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="payments")


class PaymentAudit(Base):
    """
    Record admin actions performed on payments for auditing.
    """
    __tablename__ = "payment_audit"

    id = Column(Integer, primary_key=True, index=True)
    payment_id = Column(Integer, ForeignKey("payments.id"), nullable=False)
    admin_email = Column(String(255), nullable=True)
    # e.g., 'confirmed', 'declined', 'recorded'
    action = Column(String(100), nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PasswordResetOTP(Base):
    """
    One-time password reset codes sent by email.
    Codes are stored hashed and expire quickly.
    """
    __tablename__ = "password_reset_otps"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    email = Column(String(255), index=True, nullable=False)
    otp_hash = Column(String(255), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    attempts = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
