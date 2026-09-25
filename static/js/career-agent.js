(function () {
  "use strict";

  const thread = document.getElementById("chat-thread");
  const composer = document.getElementById("composer");
  const chatInput = document.getElementById("chat-input");
  const chips = document.querySelectorAll(".chip");

  const ctxResume = document.getElementById("ctx-resume");
  const ctxResumeFile = document.getElementById("ctx-resume-file");
  const ctxUploadBtn = document.getElementById("ctx-upload-btn");
  const ctxSaveBtn = document.getElementById("ctx-save-btn");
  const ctxResumeStatus = document.getElementById("ctx-resume-status");
  const ctxTargetRole = document.getElementById("ctx-target-role");
  const ctxJobTitle = document.getElementById("ctx-job-title");
  const ctxCompany = document.getElementById("ctx-company");
  const ctxJobDescription = document.getElementById("ctx-job-description");
  const ctxSkills = document.getElementById("ctx-skills");

  const readinessCard = document.getElementById("readiness-card");

  if (!thread) return;

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function skillsArray() {
    return ctxSkills.value.split(",").map((s) => s.trim()).filter(Boolean);
  }

  function tierClass(rec) {
    const r = (rec || "").toLowerCase();
    if (r.includes("strong")) return "strong-hire";
    if (r.includes("no hire") || r.includes("reject")) return "no-hire";
    if (r.includes("hire")) return "hire";
    return "maybe";
  }

  function listBlock(title, items) {
    if (!items || !items.length) return "";
    return `<div class="block-title">${escapeHtml(title)}</div><ul>${items.map((i) => `<li>${escapeHtml(i)}</li>`).join("")}</ul>`;
  }

  ctxUploadBtn.addEventListener("click", () => ctxResumeFile.click());
  ctxResumeFile.addEventListener("change", async () => {
    const file = ctxResumeFile.files[0];
    if (!file) return;
    ctxResumeStatus.textContent = "Uploading...";
    ctxResumeStatus.className = "ctx-status";
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await fetch("/api/career/upload-resume", { method: "POST", body: fd });
      const data = await res.json();
      if (data.success) {
        ctxResume.value = data.profile.resume_text || "";
        ctxResumeStatus.textContent = `Saved from ${file.name}`;
        ctxResumeStatus.className = "ctx-status saved";
      } else {
        ctxResumeStatus.textContent = data.error || "Upload failed.";
      }
    } catch (e) {
      ctxResumeStatus.textContent = "Upload failed - check your connection.";
    }
    ctxResumeFile.value = "";
  });

  ctxSaveBtn.addEventListener("click", async () => {
    const text = ctxResume.value.trim();
    if (text.length < 20) {
      ctxResumeStatus.textContent = "Add a bit more resume text before saving.";
      ctxResumeStatus.className = "ctx-status";
      return;
    }
    ctxResumeStatus.textContent = "Saving...";
    try {
      const res = await fetch("/api/career/save-resume", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resume_text: text, target_role: ctxTargetRole.value.trim() || null }),
      });
      const data = await res.json();
      if (data.success) {
        ctxResumeStatus.textContent = "Saved.";
        ctxResumeStatus.className = "ctx-status saved";
      } else {
        ctxResumeStatus.textContent = data.error || "Couldn't save.";
        ctxResumeStatus.className = "ctx-status";
      }
    } catch (e) {
      ctxResumeStatus.textContent = "Couldn't save - check your connection.";
      ctxResumeStatus.className = "ctx-status";
    }
  });

  function addUserMessage(text) {
    const el = document.createElement("div");
    el.className = "msg user";
    el.innerHTML = `<div class="msg-bubble">${escapeHtml(text)}</div>`;
    thread.appendChild(el);
    thread.scrollTop = thread.scrollHeight;
  }

  const THINKING_LABELS = [
    "Reading your resume...",
    "Weighing the evidence...",
    "Cross-checking the role...",
    "Drafting a straight answer...",
  ];

  function addAssistantTyping() {
    const el = document.createElement("div");
    el.className = "msg assistant";
    el.innerHTML = `<div class="msg-bubble"><span class="agent-pulse"><span class="bars"><span></span><span></span><span></span><span></span><span></span></span><span class="pulse-label">${THINKING_LABELS[0]}</span></span></div>`;
    thread.appendChild(el);
    thread.scrollTop = thread.scrollHeight;

    let i = 0;
    const label = el.querySelector(".pulse-label");
    const timer = setInterval(() => {
      i = (i + 1) % THINKING_LABELS.length;
      if (label) label.textContent = THINKING_LABELS[i];
      else clearInterval(timer);
    }, 1400);
    el._pulseTimer = timer;
    return el;
  }

  function setAssistantHtml(el, html, isError) {
    if (el._pulseTimer) clearInterval(el._pulseTimer);
    el.className = "msg assistant" + (isError ? " error" : "");
    el.querySelector(".msg-bubble").innerHTML = html;
    thread.scrollTop = thread.scrollHeight;
  }

  function activityTrail(activity) {
    if (!activity || !activity.length) return "";
    return `<div class="activity-trail">${activity.map((a) => `<span>${escapeHtml(a)}</span>`).join("")}</div>`;
  }

  function renderAnalysis(a) {
    const cls = tierClass(a.recommendation);
    return `
      <div class="result-card">
        <div class="result-head">
          <div class="score-pill ${cls}">${a.score != null ? a.score : "-"}</div>
          <div><div class="result-title">Resume analysis</div><div class="result-sub">${escapeHtml(a.recommendation || "")}</div></div>
        </div>
        <p class="result-summary">${escapeHtml(a.summary || "No summary available.")}</p>
        ${listBlock("Strengths", a.strengths)}
        ${listBlock("Watch out for", a.weaknesses)}
        ${listBlock("Do this next", a.recommended_improvements)}
      </div>`;
  }

  function renderMatch(m) {
    const cls = tierClass(m.analysis && m.analysis.recommendation);
    return `
      <div class="result-card">
        <div class="result-head">
          <div class="score-pill ${cls}">${m.match_score != null ? m.match_score : "-"}</div>
          <div><div class="result-title">Match: ${escapeHtml(m.job_title || "this role")}</div><div class="result-sub">${escapeHtml(m.company || "")}</div></div>
        </div>
        ${listBlock("You already match", m.matching_skills)}
        ${listBlock("Missing for this role", m.missing_skills)}
        ${listBlock("Do this next", m.recommended_improvements)}
      </div>`;
  }

  function renderSkillGap(g) {
    return `
      <div class="result-card">
        <div class="result-head">
          <div class="score-pill">${g.skillCoverageScore != null ? g.skillCoverageScore + "%" : "-"}</div>
          <div><div class="result-title">Skill coverage for ${escapeHtml(g.targetRole || "this role")}</div><div class="result-sub">of required skills verified in your resume</div></div>
        </div>
        ${listBlock("You've got these covered", g.matchingSkills)}
        ${listBlock("Missing", g.missingSkills)}
        ${listBlock("Do this next", g.recommendedNextSteps)}
      </div>`;
  }

  function renderCareerPlan(p) {
    const milestones = (p.milestones || [])
      .map((m) => `<div class="block-title">${escapeHtml(m.title || "Milestone")}</div><ul><li>${escapeHtml(m.why || "")}</li>${(m.actions || []).map((a) => `<li>${escapeHtml(a)}</li>`).join("")}</ul>`)
      .join("");
    return `
      <div class="result-card">
        <div class="result-title" style="margin-bottom:8px;">Career plan: ${escapeHtml(p.target_role || "")}</div>
        ${listBlock("Next 7 days", p.next_7_days)}
        ${listBlock("Next 30 days", p.next_30_days)}
        ${milestones}
      </div>`;
  }

  function renderProposal(p) {
    const resume = (p.proposed_changes && p.proposed_changes.resume) || {};
    const id = "proposal-" + Math.random().toString(36).slice(2, 8);
    return `
      <div class="result-card" id="${id}">
        <div class="result-title" style="margin-bottom:6px;">Proposed resume improvements</div>
        <p class="result-summary">${escapeHtml(p.message || "Review the changes below before saving anything.")}</p>
        <div class="before-after">
          <div><div class="ba-label">Current</div><div class="ba-col">${escapeHtml(p.before || "")}</div></div>
          <div><div class="ba-label">Proposed</div><div class="ba-col">${escapeHtml(p.after || "")}</div></div>
        </div>
        ${listBlock("Why these changes", resume.rewrite_advice)}
        ${listBlock("Still missing evidence for", resume.keyword_gaps)}
        <div class="review-copy">Review the evidence and wording before approving. Aptura will not replace your saved resume automatically.</div>
        <button type="button" class="btn btn-primary btn-sm approve-btn" style="margin-top:12px;" data-target="${id}">Approve and save this version</button>
      </div>`;
  }

  function renderCoverLetter(c) {
    const letter = c.cover_letter || {};
    const id = "cover-" + Math.random().toString(36).slice(2, 8);
    return `
      <div class="result-card" id="${id}">
        <div class="result-title" style="margin-bottom:6px;">${escapeHtml(letter.subject || "Cover letter")}</div>
        <div class="ba-col" style="max-height:220px;">${escapeHtml(letter.body || "")}</div>
        ${listBlock("Resume evidence used", letter.highlights)}
        <button type="button" class="btn btn-sm copy-btn" style="margin-top:10px;" data-text="${escapeHtml(letter.body || "")}">Copy text</button>
      </div>`;
  }

  function renderResult(result) {
    let html = "";
    if (result.analysis) html += renderAnalysis(result.analysis);
    if (result.match) html += renderMatch(result.match);
    if (result.skill_gap) html += renderSkillGap(result.skill_gap);
    if (result.career_plan) html += renderCareerPlan(result.career_plan);
    if (result.proposal) html += renderProposal(result.proposal);
    if (result.cover_letter) html += renderCoverLetter(result.cover_letter);
    return html || `<p>I couldn't generate anything useful from that - try rephrasing or adding more detail in the panel on the left.</p>`;
  }

  function updateReadiness(analysis, gap) {
    if (!analysis || analysis.score == null) return;
    const steps = (gap && gap.recommendedNextSteps) || analysis.recommended_improvements || [];
    readinessCard.innerHTML = `
      <div class="readiness-head">
        <div class="readiness-ring" style="--pct:${analysis.score};"><span>${analysis.score}</span></div>
        <div>
          <div class="readiness-title">Career readiness</div>
          <div class="readiness-sub">${escapeHtml(analysis.recommendation || "Based on your latest resume")}</div>
        </div>
      </div>
      <div class="block-title">Do this next</div>
      <ul class="action-list">${(steps.slice(0, 4).map((s) => `<li>${escapeHtml(s)}</li>`).join("")) || "<li>Ask the agent to find your skill gaps for a target role.</li>"}</ul>
    `;
  }

  function detectIntent(text) {
    const m = text.toLowerCase();
    if (m.includes("cover")) return "cover";
    if (m.includes("match") || m.includes("internship") || m.includes("job")) return "match";
    if (m.includes("gap") || m.includes("plan")) return "gap_or_plan";
    if (m.includes("fix") || m.includes("improve") || m.includes("tailor")) return "improve";
    return "analyze";
  }

  function missingContextMessage(intent) {
    const jobDesc = ctxJobDescription.value.trim();
    const jobTitle = ctxJobTitle.value.trim();
    if (intent === "cover" && (!jobDesc || !jobTitle)) {
      return "Add a job title and job description in the panel on the left first - I need those to write a cover letter.";
    }
    if (intent === "gap_or_plan" && !skillsArray().length) {
      return "Add a few required skills (comma-separated) in the panel on the left first, so I know what to check your resume against.";
    }
    return null;
  }

  async function sendMessage(text) {
    text = text.trim();
    if (!text) return;
    addUserMessage(text);

    const intent = detectIntent(text);
    const blocker = missingContextMessage(intent);
    if (blocker) {
      const el = addAssistantTyping();
      setTimeout(() => setAssistantHtml(el, escapeHtml(blocker)), 350);
      return;
    }

    const typingEl = addAssistantTyping();
    const payload = {
      message: text,
      job_title: ctxJobTitle.value.trim() || null,
      company: ctxCompany.value.trim(),
      job_description: ctxJobDescription.value.trim() || null,
      required_skills: skillsArray(),
      resume_text: ctxResume.value.trim() || null,
    };

    let res, data;
    try {
      res = await fetch("/api/career/agent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      data = await res.json();
    } catch (e) {
      setAssistantHtml(typingEl, "I couldn't reach the server - check your connection and try again.", true);
      return;
    }

    if (!data.success) {
      setAssistantHtml(typingEl, escapeHtml(data.error || "Something went wrong. Try again."), true);
      return;
    }

    const html = activityTrail(data.activity) + renderResult(data.result || {});
    setAssistantHtml(typingEl, html, false);

    if (data.result && data.result.analysis) {
      updateReadiness(data.result.analysis, null);
    }
    if (data.result && data.result.skill_gap) {
      updateReadiness(data.result.analysis || { score: null }, data.result.skill_gap);
    }
  }

  composer.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = chatInput.value;
    chatInput.value = "";
    sendMessage(text);
  });

  chips.forEach((chip) => {
    chip.addEventListener("click", () => sendMessage(chip.dataset.msg));
  });

  thread.addEventListener("click", async (e) => {
    const approveBtn = e.target.closest(".approve-btn");
    if (approveBtn) {
      approveBtn.disabled = true;
      approveBtn.textContent = "Saving...";
      const card = document.getElementById(approveBtn.dataset.target);
      const proposedText = card.querySelectorAll(".ba-col")[1].textContent;
      try {
        const res = await fetch("/api/career/approve-resume", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ approved_resume_text: proposedText, target_role: ctxTargetRole.value.trim() || null }),
        });
        const data = await res.json();
        if (data.success) {
          ctxResume.value = data.profile.resume_text || proposedText;
          approveBtn.textContent = "Saved";
        } else {
          approveBtn.textContent = "Couldn't save - try again";
          approveBtn.disabled = false;
        }
      } catch (err) {
        approveBtn.textContent = "Couldn't save - try again";
        approveBtn.disabled = false;
      }
      return;
    }

    const copyBtn = e.target.closest(".copy-btn");
    if (copyBtn) {
      try {
        await navigator.clipboard.writeText(copyBtn.dataset.text);
        const original = copyBtn.textContent;
        copyBtn.textContent = "Copied";
        setTimeout(() => (copyBtn.textContent = original), 1500);
      } catch (err) {
        /* clipboard unavailable - silently ignore */
      }
    }
  });

  const matchSeedEl = document.getElementById("latest-match-seed");
  if (matchSeedEl) {
    try {
      const seed = JSON.parse(matchSeedEl.textContent);
      if (seed && seed.job_title) {
        const el = document.createElement("div");
        el.className = "msg assistant";
        const pct = seed.match_score != null ? seed.match_score + "% match" : "a match check";
        el.innerHTML = `<div class="msg-bubble">Last time, you got ${pct} for <strong>${escapeHtml(seed.job_title)}</strong>${seed.company ? " at " + escapeHtml(seed.company) : ""}. Want to check a new role, or pick up where you left off?</div>`;
        thread.appendChild(el);
      }
    } catch (e) {
      /* malformed seed - ignore */
    }
  }
})();
