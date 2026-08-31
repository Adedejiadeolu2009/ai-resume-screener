# Aptura AI

## What Is Aptura?

Aptura is a FastAPI and Jinja web app for authenticated resume screening, resume building, manual plan upgrades, dashboard reporting, and compatible AI-agent access through WebMCP.

Core positioning:

> Build your career. Find the right opportunities. Close your skill gaps. Apply with confidence.

The current application is a production-style FastAPI and Jinja web app. It keeps the existing resume screening, resume builder, authentication, dashboard, plans, admin, and deployment structure.

## Career Platform

The current implementation supports resume analysis, resume building, career readiness scoring from existing screening results, job-description matching, skill gap analysis, proposed resume improvements, and cover letter generation. It does not implement a persistent career profile, job-board scraping, application tracking, automated outreach, or silent modification of user data.

## WebMCP

WebMCP lets a web page expose real site capabilities as tools that compatible AI agents can discover and invoke in the browser. Aptura uses WebMCP so an agent can work with the user's authenticated Aptura session instead of relying on a separate chatbot or duplicated logic.

The browser integration lives in `static/js/webmcp.js` and uses:

```js
const modelContext = document.modelContext || navigator.modelContext;

await modelContext.registerTool({
  name: "analyze_resume",
  title: "Analyze Resume",
  description: "Analyze the current user's resume or supplied resume text with Aptura's resume screening logic.",
  inputSchema: {
    type: "object",
    properties: {
      resume_text: { type: "string" },
      jobDescription: { type: "string" },
      candidateName: { type: "string" }
    },
    additionalProperties: false
  },
  annotations: { readOnlyHint: true, untrustedContentHint: true },
  execute: async (input = {}) => {
    const response = await fetch("/api/webmcp/analyze-resume", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input)
    });
    return response.json();
  }
}, { signal: controller.signal });
```

Registration is feature-detected. `document.modelContext` is the canonical WebMCP API; the browser script keeps `navigator.modelContext` only as a deprecated compatibility fallback. Browsers without a compatible `registerTool` API continue to run Aptura normally and show an offline agent status.

The shared WebMCP include is loaded only on authenticated user pages where the current-user tools are relevant: dashboard, screening, resume builder, analytics, settings, plans, and manual payment request/status pages. Public pages, login, and admin pages do not expose these user-scoped tools.

## WebMCP Tools

- `analyze_resume`: uses Aptura's existing screening logic to analyze supplied resume text or the signed-in user's latest screened resume.
- `get_resume_score`: returns the signed-in user's latest stored resume score and scoring breakdown.
- `match_resume_to_job`: reuses Aptura's screening logic to compare a resume against a user-provided job title, job description, and optional required skills.
- `improve_resume`: reuses the resume-builder AI flow to generate proposed improvements. It returns `requires_human_approval: true` and never saves changes.
- `generate_cover_letter`: generates a tailored cover letter from supplied resume text or the signed-in user's latest screened resume and a supplied job description.
- `analyze_skill_gap`: deterministically compares required skills against stored resume analysis and optional supplied resume text. It does not infer unverified skills.

Backend endpoints for these tools are in `webmcp_router.py` under `/api/webmcp/*`.

## Human Approval Model

Aptura's product rule is:

> AI proposes. Human decides. Aptura executes.

Agent actions that create resume improvements or application materials are review-only. The WebMCP layer returns proposed changes and shows them in the Aptura UI. No WebMCP tool overwrites a resume, profile, application, or account setting.

## Local Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

Open `http://localhost:8000`, sign in, then visit an authenticated user page such as `/dashboard`, `/screen`, `/resume-builder`, `/analytics`, `/settings`, or `/plans`.

## Environment Variables

Required for production:

- `APP_ENV`
- `APP_BASE_URL`
- `SECRET_KEY`
- `DATABASE_URL`

AI providers, reused by screening, matching, resume improvement, and cover letters:

- `GROQ_API_KEY`
- `GROQ_API_BASE`
- `GROQ_MODEL_NAME`
- `OPENAI_API_KEY`
- `OPENAI_API_BASE`
- `OPENAI_MODEL_NAME`
- `DEEPSEEK_API_KEY`
- `DEEPSEEK_API_BASE`
- `DEEPSEEK_MODEL_NAME`

Authentication and email:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GITHUB_CLIENT_ID`
- `GITHUB_CLIENT_SECRET`
- `RESEND_API_KEY`
- `EMAIL_FROM`
- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USER`
- `SMTP_PASSWORD`
- `SMTP_FROM`

Plans and admin:

- `PRO_AMOUNT_NGN`
- `ENTERPRISE_AMOUNT_NGN`
- `MANUAL_TRANSFER_BANK_NAME`
- `MANUAL_TRANSFER_ACCOUNT_NAME`
- `MANUAL_TRANSFER_ACCOUNT_NUMBER`
- `MANUAL_TRANSFER_INSTRUCTIONS`
- `ADMIN_EMAIL`

Async processing:

- `CELERY_BROKER_URL`
- `CELERY_RESULT_BACKEND`
- `REDIS_URL`
- `SCREENING_WORKER_DELAY_SECONDS`

Do not hardcode secrets in source code.

## Testing Instructions

Run the lightweight local checks:

```bash
python -m compileall main.py webmcp_router.py screen_router.py resume_builder_router.py plans_router.py
python main.py
```

Manual checks:

1. Register or sign in.
2. Open `/dashboard` and verify career readiness appears.
3. Open `/screen`, upload PDF/DOCX/TXT resumes, and verify queueing plus results.
4. Open `/resume-builder`, generate a draft, and verify copy actions.
5. Open `/plans` and verify naira pricing and manual upgrade flow.
6. In a WebMCP-capable browser, sign in and verify `window.apturaWebMCP.tools` includes all six tools on authenticated user pages.
7. In a normal browser, verify Aptura still works and the UI shows agent mode unavailable.
8. Call WebMCP endpoints while signed out and confirm they return authentication errors.
9. Call invalid tool inputs and confirm validation errors.
10. Call `improve_resume` and confirm it returns proposed changes only.

## Deployment

Render deployment is configured in `render.yaml`:

- Web service: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Worker service: `celery -A celery_worker.celery_app worker --loglevel=info --concurrency=1`
- Database: managed PostgreSQL
- Health check: `/health`

Production requires a strong `SECRET_KEY`, HTTPS `APP_BASE_URL`, non-SQLite `DATABASE_URL`, and configured AI provider credentials.

## License

The project uses the MIT License. See `LICENSE`.
