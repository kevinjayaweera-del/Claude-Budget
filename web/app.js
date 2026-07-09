let categories = [];

const CATEGORY_COLORS = {
  "Lebensmittel": "var(--cat-lebensmittel)",
  "Miete/Wohnen": "var(--cat-miete-wohnen)",
  "Freizeit": "var(--cat-freizeit)",
  "Transport": "var(--cat-transport)",
  "Versicherungen": "var(--cat-versicherungen)",
  "Gesundheit": "var(--cat-gesundheit)",
  "Shopping": "var(--cat-shopping)",
  "Abos": "var(--cat-abos)",
  "Sonstiges": "var(--cat-sonstiges)",
  "Unkategorisiert": "var(--cat-unkategorisiert)",
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
    const categoryName = row.category_name || "Unkategorisiert";
    const color = categoryColor(categoryName);
    const isCredit = row.amount_cents >= 0;
    const sign = isCredit ? "+" : "−";
    const amountStr = formatMoney(Math.abs(row.amount_cents));
    const currencyTag = row.currency && row.currency !== "CHF"
      ? ` <span class="currency-tag">${escapeHtml(row.currency)}</span>`
      : "";
    tr.innerHTML = `
      <td class="date mono">${escapeHtml(formatDateSwiss(row.date))}</td>
      <td class="desc">${escapeHtml(row.description)}</td>
      <td><span class="chip"><span class="dot" style="background: ${color}"></span>${escapeHtml(categoryName)}</span></td>
      <td>${escapeHtml(row.source)}</td>
      <td class="amount ${isCredit ? "credit" : "debit"} tabular">${sign}${amountStr}${currencyTag}</td>
    `;
    tbody.appendChild(tr);
  });
}

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
    const currencyTag = row.currency && row.currency !== "CHF"
      ? `<span class="currency-tag">${escapeHtml(row.currency)}</span>`
      : "";
    const confidence = row.category_confidence;
    let confidenceBadge = "";
    if (confidence !== null && confidence !== undefined) {
      const level = confidence >= 0.75 ? "high" : "low";
      const title = `${Math.round(confidence * 100)}% Konfidenz`;
      confidenceBadge = `<span class="confidence-dot ${level}" title="${title}"></span>`;
    }
    tr.innerHTML = `
      <td><input type="date" value="${escapeHtml(row.date)}" data-field="date"></td>
      <td><input type="text" value="${escapeHtml(row.description)}" data-field="description"></td>
      <td class="amount-cell">
        <input type="number" step="0.01" value="${(row.amount_cents / 100).toFixed(2)}" data-field="amount">${currencyTag}
      </td>
      <td><select data-field="category_id">${categoryOptions}</select>${confidenceBadge}</td>
      <td><button type="button" class="btn-danger" data-action="delete">Löschen</button></td>
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

function showScanStatus(newPending, duplicatesSkipped) {
  const status = document.getElementById("scan-status");
  if (newPending === 0 && duplicatesSkipped === 0) {
    status.textContent = "Keine neuen Dateien gefunden.";
  } else if (newPending === 0 && duplicatesSkipped > 0) {
    status.textContent = `Alle ${duplicatesSkipped} gefundenen Buchungen sind bereits vorhanden — keine neuen Buchungen.`;
  } else if (duplicatesSkipped > 0) {
    status.textContent = `${duplicatesSkipped} Dopplungen übersprungen, ${newPending} neue Buchungen zur Prüfung.`;
  } else {
    status.textContent = `${newPending} neue Buchungen zur Prüfung.`;
  }
  status.classList.remove("hidden");
}

document.getElementById("scan-btn").addEventListener("click", async () => {
  const res = await fetch("/api/scan", { method: "POST" });
  if (!res.ok) {
    alert("Fehler beim Scannen — bitte erneut versuchen.");
    return;
  }
  const result = await res.json();
  showScanStatus(result.new_pending, result.duplicates_skipped);
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
