import logging
import os
import smtplib
from email.message import EmailMessage

import resend


logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, body: str, html_body: str | None = None) -> bool:
    """Send email using Resend if configured, otherwise SMTP."""
    resend_api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    resend_sender = (os.getenv("EMAIL_FROM") or "").strip()
    if resend_api_key and resend_sender:
        try:
            resend.api_key = resend_api_key
            params = {
                "from": resend_sender,
                "to": [to_email],
                "subject": subject,
                "text": body or "",
            }
            if html_body:
                params["html"] = html_body
            resend.Emails.send(params)
            return True
        except Exception:
            logger.exception("Resend email send failed for %s", to_email)
            return False

    host = (os.getenv("SMTP_HOST") or "").strip()
    port = int(os.getenv("SMTP_PORT", "0") or 0)
    user = (os.getenv("SMTP_USER") or "").strip()
    password = os.getenv("SMTP_PASSWORD")
    sender = (os.getenv("SMTP_FROM") or "").strip() or user

    if not host or not port or not sender:
        logger.warning("Email not sent: SMTP_HOST, SMTP_PORT, or SMTP_FROM/SMTP_USER is missing")
        return False

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body or "")
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        use_ssl = os.getenv("SMTP_USE_SSL", "false").lower() in {"1", "true", "yes"}
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
                if user and password:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.ehlo()
                if os.getenv("SMTP_STARTTLS", "true").lower() in {"1", "true", "yes"}:
                    smtp.starttls()
                    smtp.ehlo()
                if user and password:
                    smtp.login(user, password)
                smtp.send_message(msg)
        return True
    except Exception:
        logger.exception("Email send failed for %s", to_email)
        return False
