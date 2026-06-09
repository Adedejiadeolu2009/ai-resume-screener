import logging
import os
import smtplib
from email.message import EmailMessage


logger = logging.getLogger(__name__)


def send_email(to_email: str, subject: str, body: str, html_body: str | None = None) -> bool:
    """Send email with SMTP settings from environment variables."""
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "0") or 0)
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM") or user

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
