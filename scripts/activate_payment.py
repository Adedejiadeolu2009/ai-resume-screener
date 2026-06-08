"""CLI for offline activation of payments.

Usage:
  python scripts/activate_payment.py --ref <reference> --days 30

This script runs where your app environment is available (DB, SMTP env vars).
"""
import admin_router
import models
from database import SessionLocal
import sys
import os
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# Ensure project path
PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


parser = argparse.ArgumentParser()
parser.add_argument('--ref', required=True,
                    help='Payment reference to activate')
parser.add_argument('--days', type=int, default=30,
                    help='Days of premium to grant')
parser.add_argument('--amount', type=int, default=0, help='Amount (kobo)')
args = parser.parse_args()

ref = args.ref
days = args.days
amount = args.amount

with SessionLocal() as db:
    payment = db.query(models.Payment).filter(
        models.Payment.paystack_ref == ref).first()
    if not payment:
        print('No payment found with ref:', ref)
        sys.exit(2)

    user = payment.user
    if not user:
        print('Payment has no linked user')
        sys.exit(2)

    payment.status = 'success'
    payment.verified_at = datetime.utcnow()
    payment.amount = amount or payment.amount
    db.add(payment)

    user.tier = 'PREMIUM'
    user.premium_until = datetime.utcnow() + timedelta(days=days)
    user.screenings_used_this_month = 0
    db.add(user)

    # audit
    try:
        audit = models.PaymentAudit(payment_id=payment.id, admin_email=os.getenv(
            'ADMIN_EMAIL'), action='recorded_offline', notes='Activated via CLI')
        db.add(audit)
    except Exception:
        pass

    db.commit()

    # send email
    try:
        subj = f"Aptura — Payment Confirmed (Ref: {ref})"
        plain = f"Hi {user.name or user.email},\n\nYour payment (ref: {ref}) has been confirmed and your account upgraded until {user.premium_until.strftime('%Y-%m-%d')}.\n\nThanks,\nAptura AI"
        html = None
        try:
            from fastapi.templating import Jinja2Templates
            templates = Jinja2Templates(
                directory=str(PROJECT_DIR / 'templates'))
            html = templates.env.get_template('payment_confirm_email.html').render(user_name=(
                user.name or user.email), reference=ref, plan='PREMIUM', amount=payment.amount, expires=user.premium_until.strftime('%Y-%m-%d'))
        except Exception:
            pass
        admin_router.send_email(user.email, subj, plain, html_body=html)
    except Exception as e:
        print('Failed to send email:', e)

    print('Activated', ref, 'for user', user.email)
