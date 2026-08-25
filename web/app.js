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
  "Sparen": "var(--cat-sparen-anlegen)",
  "Lohn/Einkommen": "var(--cat-lohn-einkommen)",
  "Sonstiges": "var(--cat-sonstiges)",
  "Kreditkarten-Ausgleich": "var(--cat-kreditkarten-ausgleich)",
  "Unkategorisiert": "var(--cat-unkategorisiert)",
  "Auto": "var(--cat-auto)",
  "Hobby": "var(--cat-hobby)",
  "Steuern": "var(--cat-steuern)",
  "Haustier": "var(--cat-haustier)",
  "Anlegen": "var(--cat-anlegen)",
  "Vorsorge": "var(--cat-vorsorge)",
  "Kleidung": "var(--cat-kleidung)",
  "Elektronik": "var(--cat-elektronik)",
  "Körperpflege": "var(--cat-koerperpflege)",
  "Sport/Fahrrad": "var(--cat-sport-fahrrad)",
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

// index.html/import.html/analyse.html/regeln.html are separate page loads,
// not SPA routes — switching tabs and coming back re-runs this file from
// scratch, so nothing in memory survives. localStorage is what makes "the
// filters stay as I left them" possible across that reload (same pattern
// as analyse.js's LAYOUT_KEY for widget positions).
const UEBERSICHT_FILTER_KEY = "budget_uebersicht_filters_v1";

function saveFilterState() {
  localStorage.setItem(UEBERSICHT_FILTER_KEY, JSON.stringify({
    start: document.getElementById("filter-start").value,
    end: document.getElementById("filter-end").value,
    categoryId: document.getElementById("filter-category").value,
    source: document.getElementById("filter-source").value,
    type: document.getElementById("filter-type").value,
    minAmount: document.getElementById("filter-min-amount").value,
    maxAmount: document.getElementById("filter-max-amount").value,
    search: document.getElementById("filter-search").value,
  }));
}

// Returns true if a saved state was found and applied — the caller uses
// this to decide whether the (only otherwise-relevant-on-first-visit)
// default_date_range_days setting should still apply.
function restoreFilterState() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem(UEBERSICHT_FILTER_KEY) || "null");
  } catch (err) {
    saved = null;
  }
  if (!saved) return false;

  document.getElementById("filter-start").value = saved.start || "";
  document.getElementById("filter-end").value = saved.end || "";
  // Guard against a stale id from a since-deleted category — fall back to
  // "Alle Kategorien" rather than silently filtering on nothing.
  const categorySelect = document.getElementById("filter-category");
  categorySelect.value = saved.categoryId || "";
  if (categorySelect.value !== (saved.categoryId || "")) categorySelect.value = "";
  const sourceSelect = document.getElementById("filter-source");
  sourceSelect.value = saved.source || "";
  if (sourceSelect.value !== (saved.source || "")) sourceSelect.value = "";
  document.getElementById("filter-type").value = saved.type || "";
  document.getElementById("filter-min-amount").value = saved.minAmount || "";
  document.getElementById("filter-max-amount").value = saved.maxAmount || "";
  document.getElementById("filter-search").value = saved.search || "";
  return true;
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
    // Hidden categories (e.g. "Versteckt") are excluded from this picker —
    // see the matching note in import.js — except the row's own current
    // category, which stays selectable so the dropdown never silently
    // shows the wrong thing.
    const categoryOptions = categories
      .filter((c) => !c.is_hidden || c.id === row.category_id)
      .map((c) => `
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

function renderCategoryBars(containerId, byCategory, emptyMessage) {
  const container = document.getElementById(containerId);
  container.innerHTML = "";
  if (byCategory.length === 0) {
    container.innerHTML = `<p class="panel-empty">${escapeHtml(emptyMessage)}</p>`;
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
  if (byMonth.length === 0) {
    container.innerHTML = '<p class="panel-empty">Keine Daten im gewählten Zeitraum.</p>';
    return;
  }
  const sorted = [...byMonth].sort((a, b) => a.month.localeCompare(b.month));
  const data = sorted.map((m) => ({ x: m.month, net: m.amount_cents }));
  // container's height comes from CSS (.panel-body's clamp()), not from
  // renderChart's own default — that's what lets this chart grow/shrink
  // together with the category-bars panel next to it instead of always
  // rendering at a fixed pixel height regardless of available space.
  renderChart(container, {
    type: "area",
    data,
    series: [{ key: "net", label: "Netto", color: "var(--accent)" }],
    formatValue: formatMoney,
    formatX: formatMonthLabel,
    showLegend: false,
    height: container.clientHeight || 300,
  });
}

async function loadSummary() {
  const query = buildQuery();
  const res = await fetch(`/api/summary?${query}`);
  const summary = await res.json();

  document.getElementById("total-income").textContent = formatMoney(summary.total_income);
  document.getElementById("total-expense").textContent = formatMoney(Math.abs(summary.total_expense));
  document.getElementById("total-balance").textContent = formatMoney(summary.total_income + summary.total_expense);

  renderCategoryBars("category-bars", summary.by_category, "Keine Ausgaben im gewählten Zeitraum.");
  renderCategoryBars("category-bars-income", summary.by_category_income, "Keine Einnahmen im gewählten Zeitraum.");
  renderTrendChart(summary.by_month);
}

async function refreshDashboard() {
  await Promise.all([loadTransactions(), loadSummary()]);
}

document.getElementById("apply-filters-btn").addEventListener("click", () => {
  saveFilterState();
  refreshDashboard();
});

(async function init() {
  // Categories/sources populate the <select> options first — restoring a
  // saved filter has to happen after that, or setting e.g. filter-category's
  // value to a not-yet-existing <option> would silently fail.
  await Promise.all([loadCategories(), loadSources(), loadAllTags()]);
  populateTravelTagOptions();
  const restored = restoreFilterState();
  if (!restored) {
    // First-ever visit (nothing saved yet) — fall back to the existing
    // default_date_range_days behavior instead of an unfiltered view.
    await loadAppSettings();
  }
  await refreshDashboard();
})();

// ---------- travel detection / bulk tagging ----------

function populateTravelTagOptions() {
  document.getElementById("travel-tag-options").innerHTML = allTags
    .map((t) => `<option value="${escapeHtml(t.name)}"></option>`)
    .join("");
}

function renderTravelCandidates(candidates) {
  const list = document.getElementById("travel-candidates-list");
  if (candidates.length === 0) {
    list.innerHTML = '<p class="panel-empty">Keine Reise-Hinweise gefunden.</p>';
    list.classList.remove("hidden");
    return;
  }
  list.innerHTML = candidates.map((c) => `
    <div class="travel-candidate-row">
      <span class="travel-candidate-date mono">${escapeHtml(formatDateSwiss(c.date))}</span>
      <span class="travel-candidate-desc">${escapeHtml(c.description)}</span>
      <span class="travel-candidate-keyword">${escapeHtml(c.matched_keywords.join(", "))}</span>
      <span class="travel-candidate-amount tabular ${c.amount_cents >= 0 ? "credit" : "debit"}">${formatMoney(c.amount_cents)}</span>
      <button type="button" class="btn-ghost btn-small" data-action="use-date" data-date="${c.date}">Zeitraum übernehmen</button>
    </div>
  `).join("");
  list.classList.remove("hidden");

  list.querySelectorAll('[data-action="use-date"]').forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById("travel-tag-start").value = btn.dataset.date;
      document.getElementById("travel-tag-end").value = btn.dataset.date;
      document.getElementById("travel-tag-input").focus();
    });
  });
}

document.getElementById("scan-travel-btn").addEventListener("click", async () => {
  const status = document.getElementById("travel-scan-status");
  status.textContent = "Suche läuft…";
  const candidates = await fetch("/api/transactions/travel-candidates").then((r) => r.json());
  status.textContent = `${candidates.length} Hinweis(e) gefunden`;
  renderTravelCandidates(candidates);
});

document.getElementById("apply-travel-tag-btn").addEventListener("click", async () => {
  const tagName = document.getElementById("travel-tag-input").value.trim();
  const start = document.getElementById("travel-tag-start").value;
  const end = document.getElementById("travel-tag-end").value;
  const resultEl = document.getElementById("travel-tag-result");

  if (!tagName) { alert("Bitte einen Tag-Namen eingeben."); return; }
  if (!start || !end) { alert("Bitte Von- und Bis-Datum wählen."); return; }

  // Reuse an existing tag by exact (case-insensitive) name instead of always
  // creating a new one — lets "Ferien", typed repeatedly across trips, keep
  // accumulating under the same tag unless the user deliberately types a
  // more specific new name (e.g. "Ferien Berlin").
  let tag = allTags.find((t) => t.name.toLowerCase() === tagName.toLowerCase());
  if (!tag) {
    const res = await fetch("/api/tags", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: tagName }),
    });
    if (!res.ok) { alert("Fehler beim Erstellen des Tags."); return; }
    tag = await res.json();
    await loadAllTags();
    populateTravelTagOptions();
  }

  const bulkRes = await fetch("/api/transactions/tags/bulk", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tag_id: tag.id, start, end }),
  });
  if (!bulkRes.ok) { alert("Fehler beim Taggen der Buchungen."); return; }
  const { tagged } = await bulkRes.json();

  resultEl.textContent = `${tagged} Buchung(en) vom ${formatDateSwiss(start)} bis ${formatDateSwiss(end)} mit "${tagName}" getaggt.`;
  resultEl.classList.remove("hidden");
  await refreshDashboard();
});
