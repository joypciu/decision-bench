const data = document.getElementById("pack-data");
const select = document.getElementById("pack-select");
const schema = document.getElementById("schema");

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

