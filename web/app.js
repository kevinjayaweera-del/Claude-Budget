let categories = [];
let categoryChart = null;
let monthChart = null;

async function loadCategories() {
  const res = await fetch("/api/categories");
  categories = await res.json();
  const select = document.getElementById("filter-category");
  categories.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = c.name;
    select.appendChild(opt);
  });
}

async function loadSources() {
  const res = await fetch("/api/sources");
  const sources = await res.json();
  const select = document.getElementById("filter-source");
  sources.forEach((source) => {
    const opt = document.createElement("option");
    opt.value = source;
    opt.textContent = source;
    select.appendChild(opt);
  });
}

function formatAmount(cents, currency) {
  return (cents / 100).toLocaleString("de-CH", { style: "currency", currency: currency || "CHF" });
}

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function buildQuery() {
  const params = new URLSearchParams();
  const start = document.getElementById("filter-start").value;
  const end = document.getElementById("filter-end").value;
  const categoryId = document.getElementById("filter-category").value;
  const source = document.getElementById("filter-source").value;
  const minAmount = document.getElementById("filter-min-amount").value;
  const maxAmount = document.getElementById("filter-max-amount").value;
  const search = document.getElementById("filter-search").value;
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  if (categoryId) params.set("category_id", categoryId);
  if (source) params.set("source", source);
  if (minAmount) params.set("min_amount", minAmount);
  if (maxAmount) params.set("max_amount", maxAmount);
  if (search) params.set("q", search);
  return params.toString();
}

async function loadTransactions() {
  const query = buildQuery();
  const res = await fetch(`/api/transactions?${query}`);
  const rows = await res.json();
  const tbody = document.querySelector("#transactions-table tbody");
  tbody.innerHTML = "";
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(row.date)}</td>
      <td>${escapeHtml(row.description)}</td>
      <td>${escapeHtml(row.category_name || "Unkategorisiert")}</td>
      <td>${escapeHtml(row.source)}</td>
      <td>${escapeHtml(formatAmount(row.amount_cents, row.currency))}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function loadSummary() {
  const query = buildQuery();
  const res = await fetch(`/api/summary?${query}`);
  const summary = await res.json();

  document.getElementById("total-income").textContent = formatAmount(summary.total_income, "CHF");
  document.getElementById("total-expense").textContent = formatAmount(summary.total_expense, "CHF");
  document.getElementById("total-balance").textContent = formatAmount(
    summary.total_income + summary.total_expense, "CHF"
  );

  const categoryCtx = document.getElementById("category-chart");
  if (categoryChart) categoryChart.destroy();
  categoryChart = new Chart(categoryCtx, {
    type: "doughnut",
    data: {
      labels: summary.by_category.map((c) => c.category),
      datasets: [{ data: summary.by_category.map((c) => c.amount_cents / 100) }],
    },
    options: { plugins: { title: { display: true, text: "Ausgaben nach Kategorie" } } },
  });

  const monthCtx = document.getElementById("month-chart");
  if (monthChart) monthChart.destroy();
  monthChart = new Chart(monthCtx, {
    type: "line",
    data: {
      labels: summary.by_month.map((m) => m.month),
      datasets: [{
        label: "Saldo pro Monat (CHF)",
        data: summary.by_month.map((m) => m.amount_cents / 100),
        borderColor: "#2f6f4f",
        tension: 0.2,
      }],
    },
    options: { plugins: { title: { display: true, text: "Verlauf über Zeit" } } },
  });
}

async function refreshDashboard() {
  await Promise.all([loadTransactions(), loadSummary()]);
}

document.getElementById("apply-filters-btn").addEventListener("click", refreshDashboard);

async function loadPending() {
  const res = await fetch("/api/pending");
  const rows = await res.json();
  const section = document.getElementById("pending-section");
  const tbody = document.querySelector("#pending-table tbody");
  tbody.innerHTML = "";

  if (rows.length === 0) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");

  rows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.dataset.id = row.id;
    tr.dataset.currency = row.currency;
    const categoryOptions = categories
      .map((c) => `<option value="${c.id}" ${c.id === row.category_id ? "selected" : ""}>${escapeHtml(c.name)}</option>`)
      .join("");
    tr.innerHTML = `
      <td><input type="date" value="${escapeHtml(row.date)}" data-field="date"></td>
      <td><input type="text" value="${escapeHtml(row.description)}" data-field="description"></td>
      <td><input type="number" step="0.01" value="${(row.amount_cents / 100).toFixed(2)}" data-field="amount"></td>
      <td>${escapeHtml(row.currency)}</td>
      <td><select data-field="category_id">${categoryOptions}</select></td>
      <td><button type="button" data-action="delete">Löschen</button></td>
    `;
    tbody.appendChild(tr);
  });
}

async function savePendingRow(tr) {
  const id = tr.dataset.id;
  const date = tr.querySelector('[data-field="date"]').value;
  const description = tr.querySelector('[data-field="description"]').value;
  const amount = parseFloat(tr.querySelector('[data-field="amount"]').value);
  const categoryId = parseInt(tr.querySelector('[data-field="category_id"]').value, 10);
  const currency = tr.dataset.currency;

  if (Number.isNaN(amount) || Number.isNaN(categoryId)) {
    alert(`Ungültiger Betrag oder keine Kategorie in Zeile für "${description}" — bitte korrigieren.`);
    return false;
  }

  const res = await fetch(`/api/pending/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      date,
      description,
      amount_cents: Math.round(amount * 100),
      currency,
      category_id: categoryId,
    }),
  });

  if (!res.ok) {
    alert("Fehler beim Speichern — bitte erneut versuchen.");
    return false;
  }
  return true;
}

document.getElementById("scan-btn").addEventListener("click", async () => {
  const res = await fetch("/api/scan", { method: "POST" });
  if (!res.ok) {
    alert("Fehler beim Scannen — bitte erneut versuchen.");
    return;
  }
  await loadPending();
});

document.getElementById("confirm-btn").addEventListener("click", async () => {
  const rows = document.querySelectorAll("#pending-table tbody tr");
  let allSaved = true;
  const ids = [];
  for (const tr of rows) {
    const saved = await savePendingRow(tr);
    if (!saved) {
      allSaved = false;
    }
    ids.push(parseInt(tr.dataset.id, 10));
  }
  if (!allSaved) {
    alert("Einige Zeilen konnten nicht gespeichert werden — Import abgebrochen.");
    return;
  }
  const res = await fetch("/api/import/confirm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids }),
  });
  if (!res.ok) {
    alert("Fehler beim Importieren — bitte erneut versuchen.");
    return;
  }
  await loadPending();
  await refreshDashboard();
});

document.querySelector("#pending-table tbody").addEventListener("click", async (event) => {
  if (event.target.dataset.action === "delete") {
    const tr = event.target.closest("tr");
    const res = await fetch(`/api/pending/${tr.dataset.id}`, { method: "DELETE" });
    if (!res.ok) {
      alert("Fehler beim Löschen — bitte erneut versuchen.");
      return;
    }
    tr.remove();
  }
});

(async function init() {
  await loadCategories();
  await loadSources();
  await loadPending();
  await refreshDashboard();
})();
