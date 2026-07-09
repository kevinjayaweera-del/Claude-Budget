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

function currentMonthString() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

function monthRange(monthStr) {
  const [year, month] = monthStr.split("-").map(Number);
  const start = `${monthStr}-01`;
  const lastDay = new Date(year, month, 0).getDate();
  const end = `${monthStr}-${String(lastDay).padStart(2, "0")}`;
  return { start, end };
}

let categories = [];
let latestByCategory = [];
let latestBudgets = [];

async function loadCategories() {
  const res = await fetch("/api/categories");
  categories = await res.json();
}

function renderKpis(summary, budgets) {
  document.getElementById("kpi-income").textContent = formatMoney(summary.total_income);
  document.getElementById("kpi-expense").textContent = formatMoney(Math.abs(summary.total_expense));

  const net = summary.total_income + summary.total_expense;
  const netEl = document.getElementById("kpi-net");
  netEl.textContent = `${net >= 0 ? "+" : "−"}${formatMoney(Math.abs(net))}`;
  netEl.classList.toggle("credit", net >= 0);
  netEl.classList.toggle("debit", net < 0);

  const spentByCategory = {};
  summary.by_category.forEach((c) => { spentByCategory[c.category] = c.amount_cents; });

  const remainingEl = document.getElementById("kpi-remaining");
  if (budgets.length === 0) {
    remainingEl.textContent = "—";
    remainingEl.classList.remove("credit", "debit");
    return;
  }
  const remaining = budgets.reduce(
    (sum, b) => sum + (b.monthly_limit_cents - (spentByCategory[b.category_name] || 0)),
    0
  );
  remainingEl.textContent = `${remaining >= 0 ? "" : "−"}${formatMoney(Math.abs(remaining))}`;
  remainingEl.classList.toggle("credit", remaining >= 0);
  remainingEl.classList.toggle("debit", remaining < 0);
}

function renderDonut(byCategory) {
  const container = document.getElementById("category-donut");
  const total = byCategory.reduce((sum, c) => sum + c.amount_cents, 0);
  if (total === 0) {
    container.innerHTML = '<p class="panel-empty">Keine Ausgaben in diesem Monat.</p>';
    return;
  }
  const sorted = [...byCategory].sort((a, b) => b.amount_cents - a.amount_cents);
  const r = 52;
  const cx = 70;
  const cy = 70;
  const strokeWidth = 20;
  const circumference = 2 * Math.PI * r;
  let offset = 0;

  const segments = sorted.map((c) => {
    const len = (c.amount_cents / total) * circumference;
    const segment = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${categoryColor(c.category)}" ` +
      `stroke-width="${strokeWidth}" stroke-dasharray="${len.toFixed(2)} ${(circumference - len).toFixed(2)}" ` +
      `stroke-dashoffset="${(-offset).toFixed(2)}"><title>${escapeHtml(c.category)}: CHF ${formatMoney(c.amount_cents)}</title></circle>`;
    offset += len;
    return segment;
  }).join("");

  container.innerHTML = `
    <svg viewBox="0 0 140 140" width="100%" height="220" role="img" aria-label="Ausgaben nach Kategorie, Total CHF ${formatMoney(total)}">
      <g transform="rotate(-90 ${cx} ${cy})">${segments}</g>
      <text x="${cx}" y="${cy - 4}" text-anchor="middle" class="donut-total">${formatMoney(total)}</text>
      <text x="${cx}" y="${cy + 14}" text-anchor="middle" class="donut-total-label">CHF diesen Monat</text>
    </svg>
  `;
}

function renderTopCategories(byCategory) {
  const container = document.getElementById("top-categories");
  const sorted = [...byCategory].sort((a, b) => b.amount_cents - a.amount_cents).slice(0, 5);
  if (sorted.length === 0) {
    container.innerHTML = '<p class="panel-empty">Keine Ausgaben in diesem Monat.</p>';
    return;
  }
  container.innerHTML = sorted.map((c, i) => `
    <div class="top-cat-row">
      <span class="top-cat-rank">${i + 1}</span>
      <span class="cat-dot" style="background: ${categoryColor(c.category)}"></span>
      <span class="top-cat-name">${escapeHtml(c.category)}</span>
      <span class="top-cat-amount tabular">${formatMoney(c.amount_cents)}</span>
    </div>
  `).join("");
}

function renderBudgetRows(byCategory, budgets) {
  const spentByCategory = {};
  byCategory.forEach((c) => { spentByCategory[c.category] = c.amount_cents; });

  const container = document.getElementById("budget-rows");
  if (budgets.length === 0) {
    container.innerHTML = '<p class="panel-empty">Noch keine Budgets festgelegt. Füge unten eines hinzu.</p>';
  } else {
    container.innerHTML = budgets.map((b) => {
      const spent = spentByCategory[b.category_name] || 0;
      const pct = b.monthly_limit_cents > 0 ? (spent / b.monthly_limit_cents) * 100 : 0;
      const level = pct >= 100 ? "over" : pct >= 75 ? "warn" : "ok";
      return `
        <div class="budget-row" data-category-id="${b.category_id}">
          <div class="budget-row-head">
            <span class="cat-dot" style="background: ${categoryColor(b.category_name)}"></span>
            <span class="budget-row-name">${escapeHtml(b.category_name)}</span>
            <span class="budget-row-amounts tabular">${formatMoney(spent)} / ${formatMoney(b.monthly_limit_cents)} CHF</span>
          </div>
          <div class="budget-track">
            <div class="budget-fill ${level}" style="width: ${Math.min(pct, 100)}%"></div>
          </div>
          <div class="budget-row-actions">
            <input type="number" step="1" min="0" class="budget-limit-input" value="${Math.round(b.monthly_limit_cents / 100)}" data-field="limit">
            <button type="button" class="btn-ghost btn-small" data-action="update-budget">Speichern</button>
            <button type="button" class="btn-danger btn-small" data-action="delete-budget">Entfernen</button>
          </div>
        </div>
      `;
    }).join("");
  }

  const budgetedIds = new Set(budgets.map((b) => b.category_id));
  const select = document.getElementById("new-budget-category");
  select.innerHTML = categories
    .filter((c) => c.name !== "Unkategorisiert" && !budgetedIds.has(c.id))
    .map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`)
    .join("");
}

function renderCashflowChart(monthlyData) {
  const container = document.getElementById("cashflow-chart");
  if (monthlyData.length === 0) {
    container.innerHTML = '<p class="panel-empty">Keine Daten vorhanden.</p>';
    return;
  }
  const width = 760;
  const height = 220;
  const padY = 20;
  const values = monthlyData.flatMap((m) => [m.income / 100, m.expense / 100]);
  const maxVal = Math.max(...values, 1);

  const xFor = (i) => (monthlyData.length === 1 ? width / 2 : (i / (monthlyData.length - 1)) * width);
  const yFor = (v) => height - padY - (v / maxVal) * (height - padY * 2);

  const pathFor = (values) => monthlyData
    .map((m, i) => `${i === 0 ? "M" : "L"} ${xFor(i).toFixed(1)} ${yFor(values[i]).toFixed(1)}`)
    .join(" ");

  const incomePath = pathFor(monthlyData.map((m) => m.income / 100));
  const expensePath = pathFor(monthlyData.map((m) => m.expense / 100));
  const gridLines = [0.25, 0.5, 0.75]
    .map((f) => `<line x1="0" y1="${(height * f).toFixed(1)}" x2="${width}" y2="${(height * f).toFixed(1)}" stroke="var(--line)" stroke-width="1" />`)
    .join("");

  const ariaLabel = escapeHtml(
    `Cashflow-Entwicklung über ${monthlyData.length} Monate, Einnahmen und Ausgaben`
  );

  container.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" width="100%" height="220" role="img" aria-label="${ariaLabel}">
      ${gridLines}
      <path d="${incomePath}" fill="none" stroke="var(--credit)" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" />
      <path d="${expensePath}" fill="none" stroke="var(--debit)" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" />
    </svg>
    <div class="cashflow-legend">
      <span class="legend-item"><span class="legend-dot credit"></span>Einnahmen</span>
      <span class="legend-item"><span class="legend-dot debit"></span>Ausgaben</span>
    </div>
    <figcaption class="trend-caption">
      ${monthlyData.map((m) => `<span>${escapeHtml(formatMonthLabel(m.month))}</span>`).join("")}
    </figcaption>
  `;
}

async function loadMonthData() {
  const monthStr = document.getElementById("budget-month").value || currentMonthString();
  const { start, end } = monthRange(monthStr);
  const [summary, budgets] = await Promise.all([
    fetch(`/api/summary?start=${start}&end=${end}`).then((r) => r.json()),
    fetch("/api/budgets").then((r) => r.json()),
  ]);
  latestByCategory = summary.by_category;
  latestBudgets = budgets;
  renderKpis(summary, budgets);
  renderDonut(summary.by_category);
  renderTopCategories(summary.by_category);
  renderBudgetRows(summary.by_category, budgets);
}

async function loadCashflowTrend() {
  const rows = await fetch("/api/transactions").then((r) => r.json());
  const byMonth = {};
  rows.forEach((r) => {
    const month = r.date.slice(0, 7);
    if (!byMonth[month]) byMonth[month] = { income: 0, expense: 0 };
    if (r.amount_cents > 0) {
      byMonth[month].income += r.amount_cents;
    } else {
      byMonth[month].expense += Math.abs(r.amount_cents);
    }
  });
  const months = Object.keys(byMonth).sort().slice(-12);
  renderCashflowChart(months.map((m) => ({ month: m, income: byMonth[m].income, expense: byMonth[m].expense })));
}

document.getElementById("budget-month").addEventListener("change", loadMonthData);

document.getElementById("budget-rows").addEventListener("click", async (event) => {
  const row = event.target.closest(".budget-row");
  if (!row) return;
  const categoryId = row.dataset.categoryId;

  if (event.target.dataset.action === "update-budget") {
    const amount = parseFloat(row.querySelector('[data-field="limit"]').value);
    if (Number.isNaN(amount) || amount < 0) {
      alert("Bitte einen gültigen Betrag eingeben.");
      return;
    }
    const res = await fetch(`/api/budgets/${categoryId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ monthly_limit_cents: Math.round(amount * 100) }),
    });
    if (!res.ok) {
      alert("Fehler beim Speichern — bitte erneut versuchen.");
      return;
    }
    await loadMonthData();
  }

  if (event.target.dataset.action === "delete-budget") {
    const res = await fetch(`/api/budgets/${categoryId}`, { method: "DELETE" });
    if (!res.ok) {
      alert("Fehler beim Entfernen — bitte erneut versuchen.");
      return;
    }
    await loadMonthData();
  }
});

document.getElementById("add-budget-btn").addEventListener("click", async () => {
  const select = document.getElementById("new-budget-category");
  const amountInput = document.getElementById("new-budget-amount");
  const categoryId = parseInt(select.value, 10);
  const amount = parseFloat(amountInput.value);

  if (Number.isNaN(categoryId)) {
    alert("Keine Kategorie verfügbar — allen Kategorien ist bereits ein Budget zugewiesen.");
    return;
  }
  if (Number.isNaN(amount) || amount <= 0) {
    alert("Bitte einen gültigen Betrag eingeben.");
    return;
  }

  const res = await fetch(`/api/budgets/${categoryId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ monthly_limit_cents: Math.round(amount * 100) }),
  });
  if (!res.ok) {
    alert("Fehler beim Hinzufügen — bitte erneut versuchen.");
    return;
  }
  amountInput.value = "";
  await loadMonthData();
});

(async function init() {
  document.getElementById("budget-month").value = currentMonthString();
  await loadCategories();
  await loadMonthData();
  await loadCashflowTrend();
})();
