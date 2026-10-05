const data = document.getElementById("pack-data");
const select = document.getElementById("pack-select");
const schema = document.getElementById("schema");

const caseSearch = document.getElementById("eval-case-search");
const caseState = document.getElementById("eval-case-state");
if (caseSearch && caseState) {
  const saved = new URLSearchParams(location.search);
  caseSearch.value = saved.get("case_q") || "";
  const state = saved.get("case_state") || "all";
  caseState.value = [...caseState.options].some(option => option.value === state) ? state : "all";
  function filterCases(updateUrl = true) {
    const query = caseSearch.value.trim().toLowerCase();
    const rows = [...document.querySelectorAll("tr[data-case-state]")];
    let count = 0;
    rows.forEach(row => {
      row.hidden = !row.cells[0].textContent.toLowerCase().includes(query) || (caseState.value !== "all" && row.dataset.caseState !== caseState.value);
      if (!row.hidden) count++;
    });
    document.getElementById("eval-case-count").textContent = `${count} of ${rows.length} cases shown. CSV exports all cases.`;
    document.getElementById("no-case-matches").hidden = count > 0;
    if (updateUrl) {
      const url = new URL(location.href);
      if (query) url.searchParams.set("case_q", caseSearch.value); else url.searchParams.delete("case_q");
      if (caseState.value !== "all") url.searchParams.set("case_state", caseState.value); else url.searchParams.delete("case_state");
      history.replaceState(null, "", url);
    }
  }
  caseSearch.addEventListener("input", () => filterCases());
  caseState.addEventListener("change", () => filterCases());
  document.getElementById("clear-case-filters").addEventListener("click", () => {
    caseSearch.value = ""; caseState.value = "all"; filterCases(); caseSearch.focus();
  });
  filterCases(false);
}

const samples = {
  security: {bot:"change-lead",text:"diff --git a/auth.py b/auth.py\n--- a/auth.py\n+++ b/auth.py\n@@ -1,2 +1,2 @@\n-    if user.is_authenticated:\n+    if True:  # bypass auth\n         return True"},
  safe: {bot:"change-lead",text:"diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-Run the application.\n+Run the application with python -m app."},
  incident: {bot:"incident-lead",text:"Production checkout is unavailable for all customers. The error rate reached 100% after the latest deployment. On-call has been paged. Rollback has not started. We do not yet know whether payments were lost."},
};
document.querySelectorAll("[data-case]").forEach(button=>button.addEventListener("click",()=>{
  const form=document.querySelector('form[action="/runs"]');
  const sample=samples[button.dataset.case];
  if(form&&sample){form.elements.input.value=sample.text;form.elements.bot_id.value=sample.bot;form.elements.input.focus();}
}));
function filterRuns(){
  const query=(document.getElementById("run-search")?.value||"").toLowerCase();
  const status=document.getElementById("run-status")?.value||"all";
  let count=0;
  document.querySelectorAll("[data-run-row]").forEach(row=>{row.hidden=!row.dataset.search.toLowerCase().includes(query)||(status!=="all"&&row.dataset.status!==status);if(!row.hidden)count++;});
  const label=document.getElementById("run-count");if(label)label.textContent=`${count} runs`;
  const empty=document.getElementById("no-run-matches");if(empty)empty.hidden=count>0;
}
document.getElementById("run-search")?.addEventListener("input",filterRuns);
document.getElementById("run-status")?.addEventListener("change",filterRuns);
document.addEventListener("keydown",event=>{if(event.key==="/"&&!event.ctrlKey&&!event.metaKey&&!/INPUT|TEXTAREA|SELECT/.test(event.target.tagName)){const search=document.getElementById("run-search");if(search){event.preventDefault();search.focus();}}});

if (data && select && schema) {
  const packs = JSON.parse(data.textContent || "[]");
  select.addEventListener("change", () => {
    const pack = packs.find((item) => item.id === select.value);
    if (pack) {
      schema.value = JSON.stringify(pack.output_schema, null, 2);
    }
  });
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("decision-bench-theme", theme);
  document.querySelectorAll("[data-theme-value]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.themeValue === theme));
  });
}

applyTheme(document.documentElement.getAttribute("data-theme") || "light");
document.querySelectorAll("[data-theme-value]").forEach((button) => {
  button.addEventListener("click", () => applyTheme(button.dataset.themeValue));
});

document.querySelectorAll('form[action="/runs"], form[action$="/follow-up"]').forEach((form) => {
  form.addEventListener("submit", () => {
    const button = form.querySelector("button[type=submit]");
    if (!button) return;
    button.disabled = true;
    button.textContent = "Starting…";
  });
});

const live = document.getElementById("live-run");
if (live && live.dataset.status === "running") {
  const log = document.getElementById("live-log");
  const status = document.getElementById("live-status");
  const tick = async () => {
    let tree;
    try {
      const response = await fetch(`/api/runs/${live.dataset.runId}`);
      if (!response.ok) throw new Error("status");
      tree = await response.json();
    } catch (_error) {
      window.setTimeout(tick, 800);
      return;
    }
    status.textContent = tree.run.status;
    const note = document.getElementById("live-note");
    if (note && tree.summary) note.textContent = tree.summary;
    log.replaceChildren();
    const walk = (node, depth) => {
      const item = document.createElement("li");
      const decision = node.decision ? ` · ${node.decision}` : "";
      item.textContent = `${depth ? "↳ " : ""}${node.bot_name} · ${node.run.provider} · ${node.run.status}${decision}`;
      log.appendChild(item);
      (node.children || []).forEach((child) => walk(child, depth + 1));
    };
    walk(tree, 0);
    if (tree.run.status === "running") window.setTimeout(tick, 600);
    else window.location.reload();
  };
  tick();
}

