(function () {
  const modelContext = document.modelContext || navigator.modelContext;
  const state = {
    modelContext,
    available: Boolean(modelContext && typeof modelContext.registerTool === "function"),
    registered: [],
    controller: null,
  };

  function $(selector) {
    return document.querySelector(selector);
  }

  function pageValue(selector) {
    const element = $(selector);
    return element && "value" in element ? element.value.trim() : "";
  }

  function currentResumeText(input) {
    return (
      (input && input.resume_text) ||
      pageValue("#resume_text") ||
      ""
    ).trim();
  }

  function currentJobTitle(input) {
    return (
      (input && (input.jobTitle || input.job_title || input.targetRole || input.target_role)) ||
      pageValue("#job_title") ||
      pageValue("#jobTitle") ||
      "Target role"
    ).trim();
  }

  function currentJobDescription(input) {
    return (
      (input && (input.jobDescription || input.job_description || input.targetJobDescription)) ||
      pageValue("#jobDesc") ||
      ""
    ).trim();
  }

  function injectStyles() {
    if ($("#aptura-webmcp-styles")) return;
    const style = document.createElement("style");
    style.id = "aptura-webmcp-styles";
    style.textContent = `
      .webmcp-badge{display:inline-flex;align-items:center;gap:7px;height:30px;padding:0 10px;border:1px solid var(--border);border-radius:7px;background:var(--card);color:var(--muted);font:700 11px var(--ff-sans);letter-spacing:.7px;text-transform:uppercase;white-space:nowrap}
      .webmcp-badge::before{content:'';width:7px;height:7px;border-radius:50%;background:var(--hint)}
      .webmcp-badge.ready{border-color:rgba(45,212,191,.32);color:var(--teal);background:rgba(45,212,191,.08)}
      .webmcp-badge.ready::before{background:var(--teal);box-shadow:0 0 10px rgba(45,212,191,.45)}
      .webmcp-panel{position:fixed;right:18px;bottom:18px;z-index:120;max-width:min(420px,calc(100vw - 28px));max-height:58vh;overflow:auto;background:var(--card);border:1px solid var(--border-lit);border-radius:10px;box-shadow:0 18px 48px rgba(0,0,0,.42);display:none}
      .webmcp-panel.on{display:block}
      .webmcp-head{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:13px 15px;border-bottom:1px solid var(--border)}
      .webmcp-title{font:600 14px var(--ff-serif);color:var(--gold-light)}
      .webmcp-close{width:28px;height:28px;border:1px solid var(--border);border-radius:7px;background:transparent;color:var(--muted);cursor:pointer}
      .webmcp-body{padding:14px 15px;color:var(--muted);font:400 13px/1.55 var(--ff-sans)}
      .webmcp-body h4{font:700 10px var(--ff-sans);letter-spacing:1.4px;text-transform:uppercase;color:var(--hint);margin:12px 0 7px}
      .webmcp-body ul{margin:0 0 0 18px}.webmcp-body li{margin:4px 0}
      .webmcp-actions{display:flex;gap:8px;padding:0 15px 14px}.webmcp-action{height:32px;padding:0 11px;border-radius:7px;border:1px solid var(--border);background:transparent;color:var(--muted);font:700 11px var(--ff-sans);cursor:pointer}
      .webmcp-action.primary{background:var(--gold);color:#0A0B0E;border-color:var(--gold)}
      @media(max-width:720px){.webmcp-badge{height:28px;padding:0 8px;font-size:10px}.webmcp-panel{right:10px;bottom:10px}}
    `;
    document.head.appendChild(style);
  }

  function ensureChrome() {
    injectStyles();
    let badge = $("#webmcpBadge");
    if (!badge) {
      badge = document.createElement("div");
      badge.id = "webmcpBadge";
      badge.className = "webmcp-badge";
      const navRight = $(".nav-right") || $("nav") || document.body;
      navRight.insertBefore(badge, navRight.firstChild);
    }
    badge.textContent = state.available ? "Agent Ready" : "Agent Offline";
    badge.classList.toggle("ready", state.available);
    badge.title = state.available
      ? "WebMCP tools are registered for compatible AI agents."
      : "This browser does not expose a compatible document.modelContext API.";

    if (!$("#webmcpPanel")) {
      const panel = document.createElement("aside");
      panel.id = "webmcpPanel";
      panel.className = "webmcp-panel";
      panel.innerHTML = `
        <div class="webmcp-head">
          <div class="webmcp-title">Agent Activity</div>
          <button class="webmcp-close" type="button" aria-label="Close">x</button>
        </div>
        <div class="webmcp-body" id="webmcpPanelBody"></div>
        <div class="webmcp-actions" id="webmcpPanelActions"></div>
      `;
      document.body.appendChild(panel);
      panel.querySelector(".webmcp-close").addEventListener("click", () => panel.classList.remove("on"));
    }
  }

  function showPanel(title, html, actions) {
    const panel = $("#webmcpPanel");
    if (!panel) return;
    panel.querySelector(".webmcp-title").textContent = title;
    $("#webmcpPanelBody").innerHTML = html;
    $("#webmcpPanelActions").innerHTML = actions || "";
    panel.classList.add("on");
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#039;",
    }[ch]));
  }

  function listHtml(items) {
    return Array.isArray(items) && items.length
      ? `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
      : "<p>No items returned.</p>";
  }

  async function jsonFetch(url, options) {
    const response = await fetch(url, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(options && options.headers) },
      signal: state.controller && state.controller.signal,
      ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.success === false) {
      return { success: false, error: data.error || data.detail || `Request failed with ${response.status}` };
    }
    return data;
  }

  async function registerTool(tool) {
    if (!state.controller) state.controller = new AbortController();
    await state.modelContext.registerTool(tool, { signal: state.controller.signal });
    state.registered.push(tool.name);
  }

  async function registerTools() {
    if (!state.available) return;

    await registerTool({
      name: "analyze_resume",
      title: "Analyze Resume",
      description: "Analyze the current user's resume or supplied resume text with Aptura's resume screening logic.",
      inputSchema: {
        type: "object",
        properties: {
          resume_text: { type: "string", description: "Optional resume text. If omitted, Aptura uses the user's latest screened resume when available." },
          jobDescription: { type: "string", description: "Optional job description to analyze against." },
          candidateName: { type: "string", description: "Optional candidate name." },
        },
        additionalProperties: false,
      },
      annotations: { readOnlyHint: true, untrustedContentHint: true },
      execute: async (input = {}) => jsonFetch("/api/webmcp/analyze-resume", {
        method: "POST",
        body: JSON.stringify({
          resume_text: currentResumeText(input),
          jobDescription: input.jobDescription || input.job_description || currentJobDescription(input),
          candidateName: input.candidateName || input.candidate_name || "",
        }),
      }),
    });

    await registerTool({
      name: "get_resume_score",
      title: "Get Resume Score",
      description: "Return the current user's latest Aptura resume score and scoring breakdown as structured JSON.",
      inputSchema: { type: "object", properties: {}, additionalProperties: false },
      annotations: { readOnlyHint: true },
      execute: async () => jsonFetch("/api/webmcp/resume-score", { method: "GET", headers: {} }),
    });

    await registerTool({
      name: "match_resume_to_job",
      title: "Match Resume to Job",
      description: "Compare the current user's resume or supplied resume text against a supplied job description.",
      inputSchema: {
        type: "object",
        required: ["jobTitle", "jobDescription"],
        properties: {
          jobTitle: { type: "string" },
          jobDescription: { type: "string" },
          requiredSkills: { type: "array", items: { type: "string" } },
          resume_text: { type: "string" },
          candidateName: { type: "string" },
        },
        additionalProperties: false,
      },
      annotations: { readOnlyHint: true, untrustedContentHint: true },
      execute: async (input = {}) => jsonFetch("/api/webmcp/match-resume", {
        method: "POST",
        body: JSON.stringify({
          jobTitle: input.jobTitle || input.job_title || currentJobTitle(input),
          jobDescription: input.jobDescription || input.job_description || currentJobDescription(input),
          requiredSkills: Array.isArray(input.requiredSkills) ? input.requiredSkills : Array.isArray(input.required_skills) ? input.required_skills : [],
          resume_text: currentResumeText(input),
          candidateName: input.candidateName || input.candidate_name || "",
        }),
      }),
    });

    await registerTool({
      name: "improve_resume",
      title: "Improve Resume",
      description: "Generate proposed resume improvements for human review. This tool does not save or overwrite the resume.",
      inputSchema: {
        type: "object",
        properties: {
          resume_text: { type: "string" },
          instructions: { type: "string" },
          targetJobDescription: { type: "string" },
          targetRole: { type: "string" },
          seniority: { type: "string" },
          industry: { type: "string" },
        },
        additionalProperties: false,
      },
      annotations: { readOnlyHint: false, untrustedContentHint: true },
      execute: async (input = {}) => {
        const data = await jsonFetch("/api/webmcp/improve-resume", {
          method: "POST",
          body: JSON.stringify({
            resume_text: currentResumeText(input),
            targetRole: input.targetRole || input.target_role || input.instructions || currentJobTitle(input),
            seniority: input.seniority || pageValue("#seniority") || "Mid-level",
            industry: input.industry || pageValue("#industry") || "General",
            targetJobDescription: input.targetJobDescription || input.job_description || currentJobDescription(input),
          }),
        });
        if (data.success) {
          const resume = ((data.proposed_changes || {}).resume) || {};
          showPanel(
            "Resume Improvements",
            `<p>${escapeHtml(data.message)}</p>
             <h4>Headline</h4><p>${escapeHtml(resume.headline || "")}</p>
             <h4>Summary</h4><p>${escapeHtml(resume.professional_summary || "")}</p>
             <h4>Rewrite Advice</h4>${listHtml(resume.rewrite_advice)}
             <h4>Keyword Gaps</h4>${listHtml(resume.keyword_gaps)}`,
            `<button class="webmcp-action primary" type="button" onclick="document.getElementById('webmcpPanel').classList.remove('on')">Reviewed</button>`
          );
        }
        return data;
      },
    });

    await registerTool({
      name: "generate_cover_letter",
      title: "Generate Cover Letter",
      description: "Generate a tailored cover letter from the current user's resume and a supplied job description.",
      inputSchema: {
        type: "object",
        required: ["jobTitle", "company", "jobDescription"],
        properties: {
          jobTitle: { type: "string" },
          company: { type: "string" },
          jobDescription: { type: "string" },
          resume_text: { type: "string" },
          tone: { type: "string" },
        },
        additionalProperties: false,
      },
      annotations: { readOnlyHint: false, untrustedContentHint: true },
      execute: async (input = {}) => {
        const data = await jsonFetch("/api/webmcp/generate-cover-letter", {
          method: "POST",
          body: JSON.stringify({
            jobTitle: input.jobTitle || input.job_title || currentJobTitle(input),
            company: input.company || input.company_name || pageValue("#company") || "",
            jobDescription: input.jobDescription || input.job_description || currentJobDescription(input),
            resume_text: currentResumeText(input),
            tone: input.tone || "professional",
          }),
        });
        if (data.success) {
          const letter = (((data.result || {}).cover_letter) || {});
          showPanel(
            "Cover Letter",
            `<h4>Subject</h4><p>${escapeHtml(letter.subject || "")}</p>
             <h4>Draft</h4><p style="white-space:pre-wrap">${escapeHtml(letter.body || "")}</p>
             <h4>Highlights Used</h4>${listHtml(letter.highlights)}`,
            `<button class="webmcp-action primary" type="button" onclick="document.getElementById('webmcpPanel').classList.remove('on')">Reviewed</button>`
          );
        }
        return data;
      },
    });

    await registerTool({
      name: "analyze_skill_gap",
      title: "Analyze Skill Gap",
      description: "Compare the current user's resume evidence against required skills for a target role and return transparent skill-gap guidance.",
      inputSchema: {
        type: "object",
        required: ["targetRole", "requiredSkills"],
        properties: {
          targetRole: { type: "string" },
          requiredSkills: { type: "array", items: { type: "string" } },
          resume_text: { type: "string" },
        },
        additionalProperties: false,
      },
      annotations: { readOnlyHint: true, untrustedContentHint: true },
      execute: async (input = {}) => {
        const data = await jsonFetch("/api/webmcp/analyze-skill-gap", {
          method: "POST",
          body: JSON.stringify({
            targetRole: input.targetRole || input.target_role || currentJobTitle(input),
            requiredSkills: Array.isArray(input.requiredSkills) ? input.requiredSkills : Array.isArray(input.required_skills) ? input.required_skills : [],
            resume_text: currentResumeText(input),
          }),
        });
        if (data.success) {
          showPanel(
            "Skill Gap",
            `<h4>Coverage</h4><p>${escapeHtml(data.skillCoverageScore)} / 100</p>
             <h4>Current Skills</h4>${listHtml(data.currentSkills)}
             <h4>Missing Skills</h4>${listHtml(data.missingSkills)}
             <h4>Next Steps</h4>${listHtml(data.recommendedNextSteps)}`,
            `<button class="webmcp-action primary" type="button" onclick="document.getElementById('webmcpPanel').classList.remove('on')">Reviewed</button>`
          );
        }
        return data;
      },
    });

    await registerTool({
      name: "analyze_job",
      title: "Analyze Job",
      description: "Extract role requirements and screening notes from a recruiter-provided job description.",
      inputSchema: {
        type: "object",
        required: ["jobTitle", "jobDescription"],
        properties: {
          jobTitle: { type: "string" },
          jobDescription: { type: "string" },
        },
        additionalProperties: false,
      },
      annotations: { readOnlyHint: true, untrustedContentHint: true },
      execute: async (input = {}) => jsonFetch("/api/webmcp/analyze-job", {
        method: "POST",
        body: JSON.stringify({
          jobTitle: input.jobTitle || input.job_title || currentJobTitle(input),
          jobDescription: input.jobDescription || input.job_description || currentJobDescription(input),
        }),
      }),
    });

    await registerTool({
      name: "rank_candidates",
      title: "Rank Candidates",
      description: "Return ranked candidates from an existing Aptura recruiter screening.",
      inputSchema: {
        type: "object",
        required: ["screeningId"],
        properties: { screeningId: { type: "number" } },
        additionalProperties: false,
      },
      annotations: { readOnlyHint: true },
      execute: async (input = {}) => jsonFetch("/api/webmcp/rank-candidates", {
        method: "POST",
        body: JSON.stringify({ screeningId: input.screeningId || input.screening_id || window.currentScreeningId }),
      }),
    });

    await registerTool({
      name: "compare_candidates",
      title: "Compare Candidates",
      description: "Compare candidates from an existing screening with AI match scores, requirements, gaps, and concise explanations.",
      inputSchema: {
        type: "object",
        required: ["screeningId"],
        properties: {
          screeningId: { type: "number" },
          candidateIds: { type: "array", items: { type: "number" } },
        },
        additionalProperties: false,
      },
      annotations: { readOnlyHint: true },
      execute: async (input = {}) => jsonFetch("/api/webmcp/compare-candidates", {
        method: "POST",
        body: JSON.stringify({
          screeningId: input.screeningId || input.screening_id || window.currentScreeningId,
          candidateIds: Array.isArray(input.candidateIds) ? input.candidateIds : Array.isArray(input.candidate_ids) ? input.candidate_ids : [],
        }),
      }),
    });

    await registerTool({
      name: "shortlist_candidate",
      title: "Shortlist Candidate",
      description: "Shortlist one candidate from an existing screening in the current recruiter's workspace.",
      inputSchema: {
        type: "object",
        required: ["screeningId", "candidateId"],
        properties: {
          screeningId: { type: "number" },
          candidateId: { type: "number" },
          notes: { type: "string" },
        },
        additionalProperties: false,
      },
      annotations: { readOnlyHint: false },
      execute: async (input = {}) => jsonFetch("/api/webmcp/shortlist-candidate", {
        method: "POST",
        body: JSON.stringify({
          screeningId: input.screeningId || input.screening_id || window.currentScreeningId,
          candidateId: input.candidateId || input.candidate_id,
          notes: input.notes || "",
        }),
      }),
    });

    window.apturaWebMCP = {
      available: true,
      tools: state.registered.slice(),
      unregister: () => state.controller && state.controller.abort(),
    };
  }

  window.addEventListener("DOMContentLoaded", async () => {
    ensureChrome();
    try {
      await registerTools();
    } catch (error) {
      console.error("Aptura WebMCP registration failed", error);
      state.available = false;
      ensureChrome();
    }
  });
}());
