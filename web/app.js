let categories = [];

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
}

async function refreshDashboard() {
  await Promise.all([loadTransactions(), loadSummary()]);
}

document.getElementById("apply-filters-btn").addEventListener("click", refreshDashboard);

(async function init() {
  await loadCategories();
  await loadSources();
  await refreshDashboard();
})();
