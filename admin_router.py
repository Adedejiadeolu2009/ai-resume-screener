import os
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

import models
from database import get_db
from security import get_current_user

router = APIRouter(prefix="/admin", tags=["admin"])

templates = Jinja2Templates(directory="templates")


def is_admin(user: models.User) -> bool:
    # Simple: use an env secret so you don't need to add roles in DB.
    # Admin can be identified by email.
    admin_email = (os.getenv("ADMIN_EMAIL", "")
                   or "").strip().strip('"').strip("'")
    user_email = (user.email or "").strip().lower()
    return bool(admin_email) and user_email == admin_email.lower()


def require_admin(current_user: models.User) -> None:
    if not is_admin(current_user):
        raise PermissionError("Admin only")


@router.get("")
async def admin_home(current_user: models.User = Depends(get_current_user)):
    try:
        require_admin(current_user)
    except PermissionError:
        return RedirectResponse("/dashboard")
    return RedirectResponse("/admin/requests")


def send_email(to_email: str, subject: str, body: str, html_body: str = None) -> bool:
    """Send an email using SMTP configured via env vars.

    Required env vars: SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM
    html_body: optional HTML string to send as alternative content
    """
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "0") or 0)
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM") or user

    if not host or not port or not sender:
        return False

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body or "")
    if html_body:
        try:
            msg.add_alternative(html_body, subtype="html")
        except Exception:
            # fallback: ignore html if adding fails
            pass

    try:
        use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() in (
            "1", "true", "yes")
        if use_ssl:
            with smtplib.SMTP_SSL(host, port) as s:
                if user and password:
                    s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port) as s:
                s.ehlo()
                if os.getenv("SMTP_STARTTLS", "true").lower() in ("1", "true", "yes"):
                    s.starttls()
                    s.ehlo()
                if user and password:
                    s.login(user, password)
                s.send_message(msg)
        return True
    except Exception:
        return False


def app_base_url() -> str:
    return os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")


@router.get("/requests", response_class=HTMLResponse)
async def admin_requests(
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        require_admin(current_user)
    except PermissionError:
        return RedirectResponse("/login")

    pending = (
        db.query(models.Payment)
        .filter(models.Payment.status == "pending")
        .order_by(models.Payment.created_at.desc())
        .limit(200)
        .all()
    )

    payload = []
    for p in pending:
        payload.append(
            {
                "id": p.id,
                "user_id": p.user_id,
                "user_email": (p.user.email if p.user else "unknown"),
                "plan": p.plan,
                "status": p.status,
                "request_ref": p.paystack_ref,
                "created_at": p.created_at.strftime("%Y-%m-%d %H:%M"),
            }
        )

    return templates.TemplateResponse(
        request=request,
        name="admin_requests.html",
        context={"requests": payload},
    )


@router.post("/confirm-upgrade")
async def confirm_upgrade(
    request_ref: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    request: Request = None,
):
    try:
        require_admin(current_user)
    except PermissionError:
        return RedirectResponse("/login")

    payment = db.query(models.Payment).filter(
        models.Payment.paystack_ref == request_ref).first()
    if not payment:
        return RedirectResponse("/admin/requests")

    payment.status = "success"
    payment.verified_at = datetime.utcnow()

    # record audit
    try:
        audit = models.PaymentAudit(
            payment_id=payment.id,
            admin_email=current_user.email if current_user else None,
            action="confirmed",
            notes=f"Confirmed via admin UI by {current_user.email if current_user else 'unknown'}",
        )
        db.add(audit)
    except Exception:
        pass

    tier = (payment.plan or "PRO").upper()
    if tier not in {"PRO", "ENTERPRISE"}:
        tier = "PRO"

    current_user_row = db.query(models.User).filter(
        models.User.id == payment.user_id).first()
    if current_user_row:
        current_user_row.tier = tier
        # Enterprise/Pro both activate for 30 days (tweak as desired)
        current_user_row.premium_until = datetime.utcnow() + timedelta(days=30)
        db.commit()

    # Send confirmation email if possible
    # Send branded HTML + plain-text confirmation email
    try:
        if payment and payment.user and payment.user.email:
            subj = f"Aptura — Payment Confirmed (Ref: {payment.paystack_ref})"
            plain = f"Hi {payment.user.name or payment.user.email},\n\nYour payment ({payment.paystack_ref}) has been confirmed and your account upgraded to {tier} until {current_user_row.premium_until.strftime('%Y-%m-%d')}.\n\nThanks,\nAptura AI"
            # Render HTML template if available
            try:
                html = templates.env.get_template('payment_confirm_email.html').render(
                    user_name=(payment.user.name or payment.user.email),
                    reference=payment.paystack_ref,
                    plan=tier,
                    amount=payment.amount,
                    expires=current_user_row.premium_until.strftime(
                        '%Y-%m-%d'),
                    url=f"{app_base_url()}/dashboard",
                )
            except Exception:
                html = None

            ok = send_email(payment.user.email, subj, plain, html_body=html)
            email_status = "sent" if ok else "failed"
            return RedirectResponse(
                f"/admin/requests?confirmed={payment.paystack_ref}&email={email_status}",
                status_code=303,
            )
    except Exception:
        pass

    return RedirectResponse(
        f"/admin/requests?confirmed={request_ref}&email=failed",
        status_code=303,
    )


@router.post("/decline-upgrade")
async def decline_upgrade(
    request_ref: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    request: Request = None,
):
    try:
        require_admin(current_user)
    except PermissionError:
        return RedirectResponse("/login")

    payment = db.query(models.Payment).filter(
        models.Payment.paystack_ref == request_ref).first()
    if payment:
        payment.status = "failed"
        payment.verified_at = datetime.utcnow()
        db.commit()

    return RedirectResponse("/admin/requests", status_code=303)


@router.post("/record-offline")
async def record_offline_payment(
    user_email: str = Form(...),
    reference: str = Form(...),
    amount: int = Form(...),
    days: int = Form(default=30),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    request: Request = None,
):
    try:
        require_admin(current_user)
    except PermissionError:
        return RedirectResponse("/login")

    user = db.query(models.User).filter(
        models.User.email == user_email.lower().strip()).first()
    if not user:
        return RedirectResponse("/admin/requests")

    # create payment record
    existing = db.query(models.Payment).filter(
        models.Payment.paystack_ref == reference).first()
    if existing:
        existing.status = "success"
        existing.verified_at = datetime.utcnow()
    else:
        p = models.Payment(
            user_id=user.id,
            paystack_ref=reference,
            amount=amount,
            status="success",
            plan="OFFLINE",
            verified_at=datetime.utcnow(),
        )
        db.add(p)

    # grant premium
    user.tier = "PRO"
    user.premium_until = datetime.utcnow() + timedelta(days=days)
    user.screenings_used_this_month = 0
    db.commit()

    # notify user
    email_status = "failed"
    try:
        subj = f"Aptura - Payment Confirmed (Ref: {reference})"
        body = f"Hi {user.name or user.email},\n\nWe have recorded and confirmed your payment (ref: {reference}). Your account has been upgraded to PRO until {user.premium_until.strftime('%Y-%m-%d')}.\n\nThanks,\nAptura AI"
        html = templates.env.get_template('payment_confirm_email.html').render(
            user_name=(user.name or user.email),
            reference=reference,
            plan="PRO",
            amount=amount,
            expires=user.premium_until.strftime('%Y-%m-%d'),
            url=f"{app_base_url()}/dashboard",
        )
        email_status = "sent" if send_email(
            user.email, subj, body, html_body=html) else "failed"
    except Exception:
        pass

    return RedirectResponse(
        f"/admin/payments?recorded={reference}&email={email_status}",
        status_code=303,
    )


@router.get("/payments")
async def payments_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    try:
        require_admin(current_user)
    except PermissionError:
        return RedirectResponse("/login")

    payments = (
        db.query(models.Payment)
        .order_by(models.Payment.created_at.desc())
        .limit(500)
        .all()
    )

    payload = []
    for p in payments:
        payload.append({
            "id": p.id,
            "user_id": p.user_id,
            "user_email": p.user.email if p.user else "unknown",
            "plan": p.plan,
            "status": p.status,
            "request_ref": p.paystack_ref,
            "amount": p.amount,
            "created_at": p.created_at.strftime("%Y-%m-%d %H:%M"),
        })

    return templates.TemplateResponse(request=request, name="admin_payments.html", context={"payments": payload})


@router.post("/resend-confirmation")
async def resend_confirmation(
    request_ref: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    request: Request = None,
):
    try:
        require_admin(current_user)
    except PermissionError:
        return RedirectResponse("/login")

    payment = db.query(models.Payment).filter(
        models.Payment.paystack_ref == request_ref).first()
    if not payment or not payment.user or not payment.user.email:
        return RedirectResponse("/admin/payments")

    # Prepare email
    subj = f"Aptura — Payment Confirmation (Ref: {payment.paystack_ref})"
    plain = f"Hi {payment.user.name or payment.user.email},\n\nThis is a confirmation that we have recorded your payment (ref: {payment.paystack_ref}). If you believe this is an error, please contact support.\n\nThanks,\nAptura AI"
    try:
        html = templates.env.get_template('payment_confirm_email.html').render(
            user_name=(payment.user.name or payment.user.email),
            reference=payment.paystack_ref,
            plan=(payment.plan or "PREMIUM"),
            amount=payment.amount,
            expires=(payment.verified_at.strftime(
                '%Y-%m-%d') if payment.verified_at else ''),
            url=f"{app_base_url()}/dashboard",
        )
    except Exception:
        html = None

    try:
        email_status = "sent" if send_email(
            payment.user.email, subj, plain, html_body=html) else "failed"
    except Exception:
        email_status = "failed"

    # record audit
    try:
        audit = models.PaymentAudit(
            payment_id=payment.id,
            admin_email=current_user.email if current_user else None,
            action="resend_confirmation",
            notes=f"Resent confirmation via admin UI by {current_user.email if current_user else 'unknown'}",
        )
        db.add(audit)
        db.commit()
    except Exception:
        pass

    # Redirect back to payments page and indicate which ref was resent
    return RedirectResponse(
        f"/admin/payments?sent={request_ref}&email={email_status}",
        status_code=303,
    )
