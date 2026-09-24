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

