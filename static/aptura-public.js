const demoData = {
  "Product Manager": {
    requirements: ["Discovery with customers", "Roadmap prioritisation", "Stakeholder communication", "Metrics and experimentation"],
    strengths: ["3 years shipping B2B workflow tools", "Evidence of roadmap ownership", "Clear user research examples"],
    gaps: ["No pricing experiment evidence", "Analytics tooling not named"],
    evidence: ["Case study link detected", "Employer project evidence self reported"],
    improvements: ["Add one measurable launch result", "Name the analytics tools used", "Attach a portfolio case study"],
    questions: ["Tell us about a roadmap tradeoff you made.", "Which metric changed after your last launch?"],
    recommendation: "AI recommendation: strong fit for associate or mid-level PM. Human review should confirm product metrics evidence."
  },
  "Data Analyst": {
    requirements: ["SQL", "Dashboarding", "Business reporting", "Data cleaning"],
    strengths: ["SQL projects visible", "Excel and Power BI named", "Business reporting experience"],
    gaps: ["No warehouse tool listed", "Limited statistical evidence"],
    evidence: ["Portfolio linked", "Document verified resume content"],
    improvements: ["Add dashboard screenshots", "Show one business decision influenced by analysis"],
    questions: ["How do you validate messy source data?", "Describe a report that changed a team decision."],
    recommendation: "AI recommendation: promising fit. Human review should inspect portfolio quality."
  },
  "Customer Support Lead": {
    requirements: ["Team leadership", "Ticket operations", "Escalation handling", "Customer communication"],
    strengths: ["Leadership experience present", "Escalation examples visible", "Strong communication signals"],
    gaps: ["No helpdesk platform named", "SLA metrics missing"],
    evidence: ["Employer evidence self reported", "Resume content document verified"],
    improvements: ["Add ticket volume and SLA outcomes", "Name tools such as Zendesk, Freshdesk, or Intercom"],
    questions: ["How do you coach an underperforming support agent?", "What escalation metric do you watch weekly?"],
    recommendation: "AI recommendation: good fit if operations metrics can be confirmed by a recruiter."
  }
};

function renderDemo(role) {
  const data = demoData[role] || demoData["Product Manager"];
  const output = document.querySelector("[data-demo-output]");
  if (!output) return;
  const groups = [
    ["Role requirements", data.requirements],
    ["Candidate strengths", data.strengths],
    ["Missing skills", data.gaps],
    ["Evidence", data.evidence],
    ["Suggested improvements", data.improvements],
    ["Interview questions", data.questions],
    ["Final recommendation", [data.recommendation]]
  ];
  output.innerHTML = groups.map(([title, items]) => `
    <div class="demo-item">
      <strong>${title}</strong>
      <span>${items.join("<br>")}</span>
    </div>
  `).join("");
}

document.addEventListener("DOMContentLoaded", () => {
  const nav = document.querySelector(".site-nav");
  const mobileMenu = document.querySelector(".mobile-menu");
  if (nav && mobileMenu) {
    mobileMenu.addEventListener("click", () => {
      const isOpen = nav.classList.toggle("menu-open");
      mobileMenu.setAttribute("aria-expanded", String(isOpen));
      mobileMenu.setAttribute("aria-label", isOpen ? "Close navigation" : "Open navigation");
    });
  }

  const demoSelect = document.querySelector("[data-demo-role]");
  if (demoSelect) {
    renderDemo(demoSelect.value);
    demoSelect.addEventListener("change", event => renderDemo(event.target.value));
  }
});
