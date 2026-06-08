import os
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from fastapi import Form
from datetime import datetime
import uuid

import models
from database import get_db
from security import get_current_user

router = APIRouter(prefix="", tags=["plans"])

templates = Jinja2Templates(directory="templates")


@router.get("/plans", response_class=HTMLResponse)
async def plans_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    # Keep it simple: show instructions based on your configured tier.
    tier = (current_user.tier or "FREE").upper()

    return templates.TemplateResponse(
        request=request,
        name="plans.html",
        context={
            "user": current_user,
            "tier": tier,
            "pro_amount": os.getenv("PRO_AMOUNT_NGN", "1000"),
            "enterprise_amount": os.getenv("ENTERPRISE_AMOUNT_NGN", "2499"),
            "transfer_instructions": os.getenv(
                "MANUAL_TRANSFER_INSTRUCTIONS",
                "Send the bank transfer amount to the account on this page and then contact admin to activate your plan."
            ),
            "bank_name": os.getenv("MANUAL_TRANSFER_BANK_NAME", ""),
            "account_name": os.getenv("MANUAL_TRANSFER_ACCOUNT_NAME", ""),
            # NOTE: do not hardcode account number in code; keep it in env.
            "account_number": os.getenv("MANUAL_TRANSFER_ACCOUNT_NUMBER", ""),
        },
    )


@router.get("/create-request", response_class=HTMLResponse)
async def create_request_get(request: Request, current_user: models.User = Depends(get_current_user)):
    return templates.TemplateResponse(request=request, name="create_request.html", context={"created": False})


@router.post("/create-request", response_class=HTMLResponse)
async def create_request_post(request: Request, plan: str = Form(...), amount: int = Form(...), db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    # generate a unique reference
    ref = f"manual_{current_user.id}_{int(datetime.utcnow().timestamp())}_{uuid.uuid4().hex[:6]}"
    p = models.Payment(
        user_id=current_user.id,
        paystack_ref=ref,
        amount=amount or 0,
        status="pending",
        plan=plan.upper() if plan else "PRO",
    )
    db.add(p)
    db.commit()
    return templates.TemplateResponse(request=request, name="create_request.html", context={"created": True, "ref": ref, "plan": p.plan, "amount": amount})


@router.get('/plans/status', response_class=HTMLResponse)
async def plan_status(request: Request, ref: str = None, db: Session = Depends(get_db), current_user: models.User = Depends(get_current_user)):
    """Show payment status for a given request reference."""
    if not ref:
        return templates.TemplateResponse(request=request, name='payment_pending.html', context={})

    payment = db.query(models.Payment).filter(
        models.Payment.paystack_ref == ref,
        models.Payment.user_id == current_user.id,
    ).first()
    if not payment:
        # show pending page with message
        return templates.TemplateResponse(request=request, name='payment_pending.html', context={"message": "No matching payment found. Please check your reference."})

    if payment.status == 'pending':
        return templates.TemplateResponse(request=request, name='payment_pending.html', context={"reference": ref, "message": "Your payment is pending verification."})
    elif payment.status == 'success':
        # show confirmation page (reuse email template for consistency)
        try:
            html = templates.env.get_template('payment_confirm_email.html').render(
                user_name=(payment.user.name if payment.user else ''),
                reference=ref,
                plan=(payment.plan or ''),
                amount=payment.amount,
                expires=(payment.verified_at.strftime(
                    '%Y-%m-%d') if payment.verified_at else '')
            )
            return HTMLResponse(content=html)
        except Exception:
            return templates.TemplateResponse(request=request, name='payment_pending.html', context={"message": "Payment confirmed."})
    else:
        return templates.TemplateResponse(request=request, name='payment_pending.html', context={"message": f"Payment status: {payment.status}"})
