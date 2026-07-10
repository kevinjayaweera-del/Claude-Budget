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

function groupRulesByCategory(rules) {
  const groups = new Map();
  rules.forEach((rule) => {
    const key = rule.category_name || "Unkategorisiert";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(rule);
  });
  return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0], "de"));
}

function ruleCountLabel(n) {
  return `${n} Regel${n === 1 ? "" : "n"}`;
}

function renderRuleRow(rule) {
  const categoryOptions = categories
    .map((c) => `<option value="${c.id}" ${c.id === rule.category_id ? "selected" : ""}>${escapeHtml(c.name)}</option>`)
    .join("");
  const confidencePct = Math.round(rule.confidence * 100);
  const confidenceClass = rule.confidence >= 0.75 ? "high" : "low";
  return `
    <tr class="rules-row" data-id="${rule.id}">
      <td class="mono">${escapeHtml(rule.keyword)}</td>
      <td><select data-field="category_id">${categoryOptions}</select></td>
      <td class="amount tabular"><span class="confidence-dot ${confidenceClass}"></span> ${confidencePct}%</td>
      <td class="amount tabular">${rule.match_count}</td>
      <td class="amount tabular">${rule.correction_count}</td>
      <td>${rule.is_seeded ? "vordefiniert" : "gelernt"}</td>
      <td><button type="button" class="btn-danger" data-action="delete">Löschen</button></td>
    </tr>
  `;
}

async function loadRules() {
  const [rulesRes] = await Promise.all([fetch("/api/rules"), loadCategories()]);
  const rules = await rulesRes.json();
  const table = document.getElementById("rules-table");
  const emptyState = document.getElementById("rules-empty-state");

  table.querySelectorAll("tbody.rules-group").forEach((el) => el.remove());
  emptyState.classList.toggle("hidden", rules.length > 0);
  table.classList.toggle("hidden", rules.length === 0);

  groupRulesByCategory(rules).forEach(([categoryName, groupRules]) => {
    const tbody = document.createElement("tbody");
    tbody.className = "rules-group collapsed";
    tbody.innerHTML = `
      <tr class="rules-group-header-row" data-action="toggle-group">
        <td colspan="7">
          <span class="rules-group-chevron">▸</span>
          <span class="rules-group-name">${escapeHtml(categoryName)}</span>
          <span class="rules-group-count">${ruleCountLabel(groupRules.length)}</span>
        </td>
      </tr>
      ${groupRules.map(renderRuleRow).join("")}
    `;
    table.appendChild(tbody);
  });
}

document.getElementById("rules-table").addEventListener("click", async (event) => {
  const headerRow = event.target.closest(".rules-group-header-row");
  if (headerRow) {
    const tbody = headerRow.closest("tbody");
    const collapsed = tbody.classList.toggle("collapsed");
    headerRow.querySelector(".rules-group-chevron").textContent = collapsed ? "▸" : "▾";
    return;
  }

  if (event.target.dataset.action === "delete") {
    const tr = event.target.closest("tr");
    const res = await fetch(`/api/rules/${tr.dataset.id}`, { method: "DELETE" });
    if (!res.ok) {
      alert("Fehler beim Löschen — bitte erneut versuchen.");
      return;
    }
    const tbody = tr.closest("tbody");
    tr.remove();
    const remaining = tbody.querySelectorAll(".rules-row").length;
    if (remaining === 0) {
      tbody.remove();
    } else {
      tbody.querySelector(".rules-group-count").textContent = ruleCountLabel(remaining);
    }
  }
});

document.getElementById("rules-table").addEventListener("change", async (event) => {
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
    }
    // Re-render either way: on success the rule moved to a different
    // category's group; on failure this also resets the dropdown back to
    // the rule's actual (unchanged) category.
    await loadRules();
  }
});

loadRules();
