# TODO - Paywall + Plans upgrade (Free / Pro / Enterprise)

- [ ] Update `screen_router.py`:
  - Replace PREMIUM logic with FREE / PRO / ENTERPRISE
  - Enforce limits: FREE=2/month, PRO=100/month, ENTERPRISE=unlimited
  - Update upgrade error messages to remove Paystack links
- [ ] Update `auth_router.py`:
  - Remove Paystack verify-payment endpoint and Payment model usage
  - Add manual bank transfer approval endpoint (admin/manual flow)
- [ ] Update `templates/dashboard.html`:
  - Replace Premium banner + Paystack button with a Plans UI (Free / Pro / Enterprise)
  - Add manual bank transfer instructions + CTA
  - Add tier mapping & usage bar for PRO/ENTERPRISE
- [ ] Update `.env` usage:
  - Add env placeholders for manual bank transfer instructions/amounts
- [ ] Run server and test:
  - Login
  - Check plan display
  - Check screening limit enforcement
