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
