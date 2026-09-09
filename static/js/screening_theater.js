(function () {
  "use strict";

  const form = document.getElementById("screening-form");
  const dropzone = document.getElementById("dropzone");
  const fileInput = document.getElementById("file-input");
  const fileChips = document.getElementById("file-chips");
  const startBtn = document.getElementById("start-btn");
  const setupPanel = document.getElementById("setup-panel");
  const theater = document.getElementById("theater");
  const board = document.getElementById("board");
  const progressFill = document.getElementById("progress-fill");
  const progressLabel = document.getElementById("progress-label");
  const nowReading = document.getElementById("now-reading");
  const theaterTitle = document.getElementById("theater-title");
  const errorList = document.getElementById("error-list");
  const formError = document.getElementById("form-error");
  const jobDescription = document.getElementById("job_description");
  const charCount = document.getElementById("char-count");
  const quotaPill = document.getElementById("quota-pill");

  if (!form) return; // page not present

  const ALLOWED_EXT = [".pdf", ".docx", ".txt"];
  const MAX_FILE_BYTES = 10 * 1024 * 1024;
  const MAX_FILES = 50;
  const EASE = "cubic-bezier(.16,1,.3,1)";

  let selectedFiles = [];
  let queuedFilenames = [];
  let currentScreeningId = null;
  const renderedIds = new Set();
  const shortlisted = new Set();

  // ── Character counter ──────────────────────────────────────────
  if (jobDescription && charCount) {
    jobDescription.addEventListener("input", () => {
      charCount.textContent = jobDescription.value.length;
    });
  }

  // ── File selection ─────────────────────────────────────────────
  function extOf(name) {
    const i = name.lastIndexOf(".");
    return i === -1 ? "" : name.slice(i).toLowerCase();
  }

  function addFiles(fileList) {
    let rejected = 0;
    for (const f of Array.from(fileList)) {
      if (!ALLOWED_EXT.includes(extOf(f.name))) { rejected++; continue; }
      if (f.size > MAX_FILE_BYTES) { rejected++; continue; }
      if (selectedFiles.length >= MAX_FILES) break;
      if (selectedFiles.some((s) => s.name === f.name && s.size === f.size)) continue;
      selectedFiles.push(f);
    }
    if (rejected) {
      showFormError(`${rejected} file${rejected > 1 ? "s" : ""} skipped — only PDF, DOCX, or TXT under 10MB are accepted.`);
    } else {
      hideFormError();
    }
    renderChips();
  }

  function renderChips() {
    fileChips.innerHTML = "";
    selectedFiles.forEach((f, i) => {
      const chip = document.createElement("span");
      chip.className = "file-chip";
      const label = document.createElement("span");
      label.textContent = f.name;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.setAttribute("aria-label", "Remove " + f.name);
      remove.textContent = "\u00D7";
      remove.dataset.i = String(i);
      chip.appendChild(label);
      chip.appendChild(remove);
      fileChips.appendChild(chip);
    });
    startBtn.disabled = selectedFiles.length === 0;
  }

  fileChips.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-i]");
    if (!btn) return;
    selectedFiles.splice(Number(btn.dataset.i), 1);
    renderChips();
  });

  dropzone.addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    addFiles(fileInput.files);
    fileInput.value = "";
  });

  ["dragenter", "dragover"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.add("drag");
    })
  );
  ["dragleave", "drop"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.remove("drag");
    })
  );
  dropzone.addEventListener("drop", (e) => {
    if (e.dataTransfer && e.dataTransfer.files) addFiles(e.dataTransfer.files);
  });

  function showFormError(msg) {
    formError.textContent = msg;
    formError.hidden = false;
  }
  function hideFormError() {
    formError.hidden = true;
  }

  // ── Submit ──────────────────────────────────────────────────────
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!selectedFiles.length) return;

    startBtn.disabled = true;
    startBtn.textContent = "Starting\u2026";
    hideFormError();

    const fd = new FormData();
    fd.append("job_title", form.job_title.value.trim() || "Open Position");
    fd.append("company_name", form.company_name.value.trim());
    fd.append("job_description", form.job_description.value);
    fd.append("csrf_token", form.csrf_token.value);
    selectedFiles.forEach((f) => fd.append("files", f));

    queuedFilenames = selectedFiles.map((f) => f.name);

    let res;
    try {
      res = await fetch("/api/screen", { method: "POST", body: fd });
    } catch (err) {
      showFormError("Could not reach the server. Check your connection and try again.");
      resetStartBtn();
      return;
    }

    if (!res.ok) {
      let detail = "Something went wrong starting this screening.";
      try {
        const body = await res.json();
        detail = body.detail || detail;
      } catch (_) {}
      showFormError(detail);
      resetStartBtn();
      return;
    }

    const data = await res.json();
    bumpQuotaUsed();
    currentScreeningId = data.screening_id;
    openTheater(data.job_title, data.company_name, data.total_files);
    pollScreening(data.screening_id);
  });

  function resetStartBtn() {
    startBtn.disabled = false;
    startBtn.textContent = "Start screening \u2192";
  }

  function bumpQuotaUsed() {
    if (!quotaPill || quotaPill.classList.contains("unlimited")) return;
    const match = quotaPill.textContent.match(/(\d+) of (\d+)/);
    if (!match) return;
    const remaining = Math.max(0, Number(match[1]) - 1);
    const limit = Number(match[2]);
    quotaPill.textContent = `${remaining} of ${limit} screenings left this month`;
    quotaPill.classList.toggle("low", remaining <= 1);
  }

  // ── Theater ─────────────────────────────────────────────────────
  function openTheater(jobTitle, company, totalFiles) {
    setupPanel.hidden = true;
    theater.hidden = false;
    theaterTitle.textContent = company ? `${jobTitle} \u00B7 ${company}` : jobTitle;
    board.innerHTML = "";
    renderedIds.clear();
    updateProgress(0, totalFiles || queuedFilenames.length);
    updateNowReading([]);
  }

  function updateProgress(processed, total) {
    const pct = total ? Math.round((processed / total) * 100) : 0;
    progressFill.style.width = pct + "%";
    progressLabel.textContent = `${processed} / ${total} read`;
  }

  function updateNowReading(doneFilenames) {
    const doneSet = new Set(doneFilenames);
    const next = queuedFilenames.find((f) => !doneSet.has(f));
    if (next) {
      nowReading.textContent = `Reading ${next}\u2026`;
    } else if (queuedFilenames.length) {
      nowReading.textContent = "Wrapping up\u2026";
    } else {
      nowReading.textContent = "";
    }
  }

  async function pollScreening(id) {
    async function tick() {
      let res;
      try {
        res = await fetch(`/api/screening/${id}`);
      } catch (e) {
        setTimeout(tick, 2000);
        return;
      }
      if (!res.ok) {
        setTimeout(tick, 2000);
        return;
      }
      const data = await res.json();
      renderBoard(data.results);
      renderErrors(data.errors);
      updateProgress(data.processed_candidates, data.total_files);
      updateNowReading([
        ...data.results.map((r) => r.filename),
        ...data.errors.map((e) => e.file),
      ]);

      const terminal = ["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"];
      if (terminal.includes(data.status)) {
        nowReading.textContent = data.status === "FAILED" ? "Screening failed." : "Done.";
        return;
      }
      setTimeout(tick, 1500);
    }
    tick();
  }

  function tierClass(rec) {
    const r = (rec || "").toLowerCase();
    if (r.includes("strong")) return "strong-hire";
    if (r.includes("no hire") || r.includes("reject")) return "no-hire";
    if (r.includes("hire")) return "hire";
    return "maybe";
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  function rowInner(r, rank) {
    const cls = tierClass(r.recommendation);
    const isShortlisted = shortlisted.has(String(r.candidate_id));
    const isStrong = cls === "strong-hire";
    return `
      <div class="rank"><span class="rank-num">#${rank}</span></div>
      <div class="row-main">
        <div class="row-name">${escapeHtml(r.candidate_name || r.filename || "Candidate")}</div>
        <div class="row-file">${escapeHtml(r.filename || "")}</div>
      </div>
      <div class="row-score" data-target="${r.overall_score != null ? r.overall_score : ""}">${r.overall_score != null ? 0 : "\u2014"}</div>
      <div class="row-badge ${cls}${isStrong ? " stamp-in" : ""}">${escapeHtml(r.recommendation || "\u2014")}</div>
      <div class="row-actions">
        <button type="button" class="row-shortlist${isShortlisted ? " on" : ""}" ${isShortlisted ? "disabled" : ""}>${isShortlisted ? "\u2605 Shortlisted" : "\u2606 Shortlist"}</button>
        <button type="button" class="row-expand">Details</button>
      </div>
    `;
  }

  // Counts a score up from 0 to its target instead of just appearing —
  // a small thing, but it's the difference between a number that arrives
  // and a number that feels like it was just computed.
  function animateScoreCountUp(row) {
    const el = row.querySelector(".row-score");
    if (!el) return;
    const target = Number(el.dataset.target);
    if (!Number.isFinite(target)) return;
    const duration = 900;
    const start = performance.now();
    function tick(now) {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3); // ease-out-cubic
      el.textContent = Math.round(target * eased);
      if (t < 1) requestAnimationFrame(tick);
      else el.textContent = target;
    }
    requestAnimationFrame(tick);
  }

  function buildRow(r, rank) {
    const row = document.createElement("div");
    row.className = "theater-row";
    row.dataset.candidateId = String(r.candidate_id);
    row.innerHTML = rowInner(r, rank);
    row.querySelector(".row-expand").addEventListener("click", () => toggleDetail(row, r));
    row.querySelector(".row-shortlist").addEventListener("click", (e) => shortlistCandidate(r, e.currentTarget));
    return row;
  }

  async function shortlistCandidate(r, btn) {
    if (!currentScreeningId) return;
    btn.disabled = true;
    btn.textContent = "Shortlisting\u2026";
    try {
      const res = await fetch("/api/recruiter/shortlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ candidate_id: r.candidate_id, screening_id: currentScreeningId }),
      });
      const data = await res.json();
      if (data.success) {
        shortlisted.add(String(r.candidate_id));
        btn.textContent = "\u2605 Shortlisted";
        btn.classList.add("on");
      } else {
        btn.disabled = false;
        btn.textContent = "\u2606 Shortlist";
      }
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "\u2606 Shortlist";
    }
  }

  function toggleDetail(row, r) {
    const next = row.nextElementSibling;
    if (next && next.classList.contains("row-detail")) {
      next.remove();
      return;
    }
    const detail = document.createElement("div");
    detail.className = "row-detail";
    const strengths = (r.strengths || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("");
    const gaps = (r.gaps || r.missing_skills || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("");
    const questions = (r.interview_questions || []).map((s) => `<li>${escapeHtml(s)}</li>`).join("");
    detail.innerHTML = `
      <p class="detail-summary">${escapeHtml(r.executive_summary || "No summary available.")}</p>
      <div class="detail-grid">
        <div><h4>Strengths</h4><ul>${strengths || "<li>\u2014</li>"}</ul></div>
        <div><h4>Gaps</h4><ul>${gaps || "<li>\u2014</li>"}</ul></div>
        <div><h4>Ask in interview</h4><ul>${questions || "<li>\u2014</li>"}</ul></div>
      </div>
    `;
    row.after(detail);
  }

  function renderBoard(results) {
    const prevRects = {};
    board.querySelectorAll(".theater-row").forEach((row) => {
      prevRects[row.dataset.candidateId] = row.getBoundingClientRect();
    });

    const sorted = [...results].sort((a, b) => (b.overall_score || 0) - (a.overall_score || 0));

    sorted.forEach((r, idx) => {
      const id = String(r.candidate_id);
      let row = board.querySelector(`.theater-row[data-candidate-id="${id}"]`);
      if (!row) {
        row = buildRow(r, idx + 1);
        renderedIds.add(id);
      } else {
        row.querySelector(".rank-num").textContent = "#" + (idx + 1);
      }
      board.appendChild(row);
    });

    board.querySelectorAll(".theater-row").forEach((row) => {
      const id = row.dataset.candidateId;
      const prev = prevRects[id];
      if (!prev) {
        const isStrong = row.querySelector(".row-badge")?.classList.contains("strong-hire");
        row.classList.add("row-enter");
        if (isStrong) row.classList.add("tier-glow");
        animateScoreCountUp(row);
        setTimeout(() => row.classList.remove("row-enter", "tier-glow"), 900);
        return;
      }
      const next = row.getBoundingClientRect();
      const dy = prev.top - next.top;
      if (Math.abs(dy) > 1) {
        row.style.transition = "none";
        row.style.transform = `translateY(${dy}px)`;
        requestAnimationFrame(() => {
          row.style.transition = `transform .5s ${EASE}`;
          row.style.transform = "";
        });
      }
    });
  }

  function renderErrors(errors) {
    if (!errors || !errors.length) {
      errorList.hidden = true;
      return;
    }
    errorList.hidden = false;
    errorList.innerHTML =
      `<div class="error-title">${errors.length} file${errors.length > 1 ? "s" : ""} couldn't be read</div>` +
      errors.map((e) => `<div class="error-row"><strong>${escapeHtml(e.file)}</strong> \u2014 ${escapeHtml(e.error)}</div>`).join("");
  }

  // ── Seed from a completed past screening, if the server rendered one ──
  const seedEl = document.getElementById("theater-seed");
  if (seedEl) {
    try {
      const seed = JSON.parse(seedEl.textContent);
      if (seed && seed.results) {
        currentScreeningId = seed.screening_id || null;
        queuedFilenames = seed.results
          .map((r) => r.filename)
          .concat((seed.errors || []).map((e) => e.file));
        const total = seed.total_processed + (seed.total_errors || 0);
        openTheater(seed.job_title, seed.company_name, total);
        renderBoard(seed.results);
        renderErrors(seed.errors);
        updateProgress(total, total);
        nowReading.textContent = "Completed.";
      }
    } catch (e) {
      /* malformed seed — ignore, user can just start a new screening */
    }
  }
})();