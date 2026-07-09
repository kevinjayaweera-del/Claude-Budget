function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

let categories = [];

async function loadCategories() {
  const res = await fetch("/api/categories");
  categories = await res.json();
}

async function loadRules() {
  const [rulesRes] = await Promise.all([fetch("/api/rules"), loadCategories()]);
  const rules = await rulesRes.json();
  const tbody = document.querySelector("#rules-table tbody");
  tbody.innerHTML = "";

  rules.forEach((rule) => {
    const tr = document.createElement("tr");
    tr.dataset.id = rule.id;
    const categoryOptions = categories
      .map((c) => `<option value="${c.id}" ${c.id === rule.category_id ? "selected" : ""}>${escapeHtml(c.name)}</option>`)
      .join("");
    const confidencePct = Math.round(rule.confidence * 100);
    const confidenceClass = rule.confidence >= 0.75 ? "high" : "low";
    tr.innerHTML = `
      <td class="mono">${escapeHtml(rule.keyword)}</td>
      <td><select data-field="category_id">${categoryOptions}</select></td>
      <td class="amount tabular"><span class="confidence-dot ${confidenceClass}"></span> ${confidencePct}%</td>
      <td class="amount tabular">${rule.match_count}</td>
      <td class="amount tabular">${rule.correction_count}</td>
      <td>${rule.is_seeded ? "vordefiniert" : "gelernt"}</td>
      <td><button type="button" class="btn-danger" data-action="delete">Löschen</button></td>
    `;
    tbody.appendChild(tr);
  });
}

document.querySelector("#rules-table tbody").addEventListener("change", async (event) => {
  if (event.target.dataset.field === "category_id") {
    const tr = event.target.closest("tr");
    const categoryId = parseInt(event.target.value, 10);
    const res = await fetch(`/api/rules/${tr.dataset.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ category_id: categoryId }),
    });
    if (!res.ok) {
      alert("Fehler beim Speichern — bitte erneut versuchen.");
      await loadRules();
    }
  }
});

document.querySelector("#rules-table tbody").addEventListener("click", async (event) => {
  if (event.target.dataset.action === "delete") {
    const tr = event.target.closest("tr");
    const res = await fetch(`/api/rules/${tr.dataset.id}`, { method: "DELETE" });
    if (!res.ok) {
      alert("Fehler beim Löschen — bitte erneut versuchen.");
      return;
    }
    tr.remove();
  }
});

loadRules();
