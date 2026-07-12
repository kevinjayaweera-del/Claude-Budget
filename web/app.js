let categories = [];
let allTags = [];

const CATEGORY_COLORS = {
  "Lebensmittel": "var(--cat-lebensmittel)",
  "Restaurants/Ausgang": "var(--cat-restaurants-ausgang)",
  "Transport": "var(--cat-transport)",
  "Reisen": "var(--cat-reisen)",
  "Miete/Wohnen": "var(--cat-miete-wohnen)",
  "Versicherungen": "var(--cat-versicherungen)",
  "Gesundheit": "var(--cat-gesundheit)",
  "Shopping": "var(--cat-shopping)",
  "Abos": "var(--cat-abos)",
  "Freizeit": "var(--cat-freizeit)",
  "Bargeldbezug": "var(--cat-bargeldbezug)",
  "Privatüberweisungen": "var(--cat-privatuberweisungen)",
  "Sparen/Anlegen": "var(--cat-sparen-anlegen)",
  "Lohn/Einkommen": "var(--cat-lohn-einkommen)",
  "Sonstiges": "var(--cat-sonstiges)",
  "Kreditkarten-Ausgleich": "var(--cat-kreditkarten-ausgleich)",
  "Unkategorisiert": "var(--cat-unkategorisiert)",
  "Auto": "var(--cat-auto)",
  "Hobby": "var(--cat-hobby)",
  "Steuern": "var(--cat-steuern)",
};
const MONTH_LABELS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"];

function categoryColor(name) {
  return CATEGORY_COLORS[name] || "var(--cat-sonstiges)";
}

function formatMonthLabel(yearMonth) {
  const [year, month] = yearMonth.split("-");
  const label = MONTH_LABELS[parseInt(month, 10) - 1] || month;
  return `${label} ${year.slice(2)}`;
}

function formatDateSwiss(isoDate) {
  const [year, month, day] = isoDate.split("-");
  if (!year || !month || !day) return isoDate;
  return `${day}.${month}.${year}`;
}

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

function applyDefaultDateRange(days) {
  if (!days) return;
  const end = new Date();
  const start = new Date();
  start.setDate(start.getDate() - days);
  const toIso = (d) => d.toISOString().slice(0, 10);
  document.getElementById("filter-start").value = toIso(start);
  document.getElementById("filter-end").value = toIso(end);
}

async function loadAppSettings() {
  const settings = await fetch("/api/settings").then((r) => r.json());
  applyDefaultDateRange(settings.default_date_range_days);
}

async function loadAllTags() {
  const res = await fetch("/api/tags");
  allTags = await res.json();
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

function formatMoney(cents) {
  return (cents / 100).toLocaleString("de-CH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
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
  const type = document.getElementById("filter-type").value;
  const minAmount = document.getElementById("filter-min-amount").value;
  const maxAmount = document.getElementById("filter-max-amount").value;
  const search = document.getElementById("filter-search").value;
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  if (categoryId) params.set("category_id", categoryId);
  if (source) params.set("source", source);
  if (type) params.set("type", type);
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
    const categoryName = row.category_name || "Unkategorisiert";
    const color = categoryColor(categoryName);
    const isCredit = row.amount_cents >= 0;
    const sign = isCredit ? "+" : "−";
    const amountStr = formatMoney(Math.abs(row.amount_cents));
    const currencyTag = row.currency && row.currency !== "CHF"
      ? ` <span class="currency-tag">${escapeHtml(row.currency)}</span>`
      : "";
    const rowTags = row.tags || [];
    const tagChips = rowTags.map((t) => `
      <span class="tag-chip" data-tag-id="${t.id}">${escapeHtml(t.name)}<button type="button" class="tag-chip-remove" data-action="remove-tag" data-tag-id="${t.id}" title="Entfernen">&times;</button></span>
    `).join("");
    const assignableTags = allTags.filter((t) => !rowTags.some((rt) => rt.id === t.id));
    const addTagSelect = assignableTags.length > 0
      ? `<select class="tag-add-select" data-action="add-tag">
          <option value="">+ Tag</option>
          ${assignableTags.map((t) => `<option value="${t.id}">${escapeHtml(t.name)}</option>`).join("")}
        </select>`
      : "";
    const categoryOptions = categories.map((c) => `
      <option value="${c.id}" ${c.id === row.category_id ? "selected" : ""}>${escapeHtml(c.name)}</option>
    `).join("");
    tr.dataset.transactionId = row.id;
    tr.innerHTML = `
      <td class="date mono">${escapeHtml(formatDateSwiss(row.date))}</td>
      <td class="desc">${escapeHtml(row.description)}</td>
      <td>
        <span class="chip chip-editable" data-action="category-chip">
          <span class="dot" style="background: ${color}"></span>
          <select class="category-edit-select" data-action="edit-category" data-prev-value="${row.category_id ?? ""}" aria-label="Kategorie ändern">
            ${categoryOptions}
          </select>
        </span>
      </td>
      <td>${escapeHtml(row.source)}</td>
      <td><div class="tag-chip-list">${tagChips}${addTagSelect}</div></td>
      <td class="amount ${isCredit ? "credit" : "debit"} tabular">${sign}${amountStr}${currencyTag}</td>
    `;
    tbody.appendChild(tr);
  });
}

document.querySelector("#transactions-table tbody").addEventListener("change", async (event) => {
  if (event.target.dataset.action !== "edit-category") return;
  const select = event.target;
  const chip = select.closest(".chip");
  const tr = select.closest("tr");
  const transactionId = tr.dataset.transactionId;
  const newCategoryId = parseInt(select.value, 10);
  const prevValue = select.dataset.prevValue;

  select.disabled = true;
  const res = await fetch(`/api/transactions/${transactionId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ category_id: newCategoryId }),
  });
  select.disabled = false;

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    alert(body.error || "Fehler beim Ändern der Kategorie.");
    select.value = prevValue;
    return;
  }
  const result = await res.json();
  select.dataset.prevValue = String(newCategoryId);
  chip.querySelector(".dot").style.background = categoryColor(result.category_name);
  chip.classList.remove("chip-saved");
  void chip.offsetWidth; // restart the flash animation on repeated edits
  chip.classList.add("chip-saved");
});

document.querySelector("#transactions-table tbody").addEventListener("click", async (event) => {
  if (event.target.dataset.action !== "remove-tag") return;
  const tr = event.target.closest("tr");
  const transactionId = tr.dataset.transactionId;
  const tagId = event.target.dataset.tagId;
  await fetch(`/api/transactions/${transactionId}/tags/${tagId}`, { method: "DELETE" });
  await loadTransactions();
});

document.querySelector("#transactions-table tbody").addEventListener("change", async (event) => {
  if (event.target.dataset.action !== "add-tag" || !event.target.value) return;
  const tr = event.target.closest("tr");
  const transactionId = tr.dataset.transactionId;
  const tagId = event.target.value;
  await fetch(`/api/transactions/${transactionId}/tags`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tag_id: parseInt(tagId, 10) }),
  });
  await loadTransactions();
});

function renderCategoryBars(byCategory) {
  const container = document.getElementById("category-bars");
  container.innerHTML = "";
  if (byCategory.length === 0) {
    container.innerHTML = '<p class="panel-empty">Keine Ausgaben im gewählten Zeitraum.</p>';
    return;
  }
  const sorted = [...byCategory].sort((a, b) => b.amount_cents - a.amount_cents);
  const maxAmount = sorted[0].amount_cents;
  sorted.forEach((entry) => {
    const color = categoryColor(entry.category);
    const pct = maxAmount > 0 ? Math.max((entry.amount_cents / maxAmount) * 100, 2) : 0;
    const row = document.createElement("div");
    row.className = "cat-row";
    row.innerHTML = `
      <span class="cat-dot" style="background: ${color}"></span>
      <span class="cat-name">${escapeHtml(entry.category)}</span>
      <span class="cat-track"><span class="cat-fill" style="width: ${pct}%; background: ${color}"></span></span>
      <span class="cat-amount tabular">${formatMoney(entry.amount_cents)}</span>
    `;
    container.appendChild(row);
  });
}

function renderTrendChart(byMonth) {
  const container = document.getElementById("trend-chart");
  container.innerHTML = "";
  if (byMonth.length === 0) {
    container.innerHTML = '<p class="panel-empty">Keine Daten im gewählten Zeitraum.</p>';
    return;
  }

  const sorted = [...byMonth].sort((a, b) => a.month.localeCompare(b.month));
  const width = 400;
  const height = 140;
  const padY = 16;
  const values = sorted.map((m) => m.amount_cents / 100);
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const range = max - min || 1;

  const points = sorted.map((m, i) => {
    const x = sorted.length === 1 ? width / 2 : (i / (sorted.length - 1)) * width;
    const y = height - padY - ((m.amount_cents / 100 - min) / range) * (height - padY * 2);
    return [x, y];
  });

  const linePath = points.map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const areaPath = `${linePath} L ${width} ${height} L 0 ${height} Z`;
  const [lastX, lastY] = points[points.length - 1];
  const gridLines = [0.25, 0.5, 0.75]
    .map((f) => `<line x1="0" y1="${(height * f).toFixed(1)}" x2="${width}" y2="${(height * f).toFixed(1)}" stroke="var(--line)" stroke-width="1" />`)
    .join("");

  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  const ariaLabel = escapeHtml(
    `Netto pro Monat, von ${formatMonthLabel(first.month)} (CHF ${formatMoney(first.amount_cents)}) bis ${formatMonthLabel(last.month)} (CHF ${formatMoney(last.amount_cents)})`
  );

  container.innerHTML = `
    <figure class="trend-figure">
      <svg viewBox="0 0 ${width} ${height}" width="100%" height="140" role="img" aria-label="${ariaLabel}">
        ${gridLines}
        <path d="${areaPath}" fill="var(--accent)" opacity="0.08" stroke="none" />
        <path d="${linePath}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />
        <circle cx="${lastX.toFixed(1)}" cy="${lastY.toFixed(1)}" r="4" fill="var(--accent)" stroke="var(--surface)" stroke-width="2" />
      </svg>
      <figcaption class="trend-caption">
        ${sorted.map((m) => `<span>${escapeHtml(formatMonthLabel(m.month))}</span>`).join("")}
      </figcaption>
    </figure>
  `;
}

async function loadSummary() {
  const query = buildQuery();
  const res = await fetch(`/api/summary?${query}`);
  const summary = await res.json();

  document.getElementById("total-income").textContent = formatMoney(summary.total_income);
  document.getElementById("total-expense").textContent = formatMoney(Math.abs(summary.total_expense));
  document.getElementById("total-balance").textContent = formatMoney(summary.total_income + summary.total_expense);

  renderCategoryBars(summary.by_category);
  renderTrendChart(summary.by_month);
}

async function refreshDashboard() {
  await Promise.all([loadTransactions(), loadSummary()]);
}

document.getElementById("apply-filters-btn").addEventListener("click", refreshDashboard);

(async function init() {
  await loadAppSettings();
  await loadCategories();
  await loadSources();
  await loadAllTags();
  await refreshDashboard();
})();
