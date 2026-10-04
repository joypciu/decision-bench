const sample = document.getElementById("sample-diff");
const field = document.querySelector("textarea[name=diff]");
if (sample && field) {
  const samples = JSON.parse(sample.textContent || "[]");
  document.querySelectorAll("[data-sample]").forEach((button) => {
    button.addEventListener("click", () => {
      const item = samples.find((entry) => entry.id === button.dataset.sample);
      if (!item) return;
      field.value = item.diff;
      const job = document.getElementById("bot-id");
      if (job && item.bot_id) job.value = item.bot_id;
      field.focus();
    });
  });
}
const copy = document.getElementById("copy-comment");
const comment = document.getElementById("comment-text");
if (copy && comment) {
  copy.addEventListener("click", async () => {
    await navigator.clipboard.writeText(comment.textContent || "");
    copy.textContent = "Copied";
  });
}
document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", () => {
    const submit = form.querySelector("button[type=submit]");
    if (!submit) return;
    window.setTimeout(() => {
      submit.disabled = true;
      submit.textContent = "Reviewing…";
    }, 0);
  });
});
