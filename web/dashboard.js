// dashboard.js — widget-based finance dashboard for the Budget page.
// Built on charts.js (hand-rolled SVG engine). No external dependencies.

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
};
const MONTH_LABELS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"];

function categoryColor(name) {
  return CATEGORY_COLORS[name] || "var(--cat-sonstiges)";
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

function isoDate(d) {
  return d.toISOString().slice(0, 10);
}

// ---------- period helpers (mirror server/app.py's _PERIOD_KEY_FORMATTERS) ----------

function isoWeekKey(dateStr) {
  const d = new Date(dateStr + "T00:00:00Z");
  const target = new Date(d.valueOf());
  const dayNr = (d.getUTCDay() + 6) % 7;
  target.setUTCDate(target.getUTCDate() - dayNr + 3);
  const firstThursday = new Date(Date.UTC(target.getUTCFullYear(), 0, 4));
  const diff = target - firstThursday;
  const week = 1 + Math.round(diff / (7 * 24 * 3600 * 1000));
  return `${target.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

function periodKeyFor(dateStr, granularity) {
  switch (granularity) {
    case "day": return dateStr;
    case "week": return isoWeekKey(dateStr);
    case "quarter": {
      const [y, m] = dateStr.slice(0, 7).split("-").map(Number);
      return `${y}-Q${Math.floor((m - 1) / 3) + 1}`;
    }
    case "year": return dateStr.slice(0, 4);
    case "month":
    default: return dateStr.slice(0, 7);
  }
}

function formatPeriodLabel(period, granularity) {
  if (granularity === "day") {
    const [, m, d] = period.split("-");
    return `${d}.${m}.`;
  }
  if (granularity === "week") return period.replace("-W", " W");
  if (granularity === "month") {
    const [y, m] = period.split("-");
    return `${MONTH_LABELS[parseInt(m, 10) - 1]} ${y.slice(2)}`;
  }
  return period;
}

function periodToDateRange(period, granularity) {
  if (granularity === "day") return { start: period, end: period };
  if (granularity === "month") {
    const [y, m] = period.split("-").map(Number);
    const lastDay = new Date(y, m, 0).getDate();
    return { start: `${period}-01`, end: `${period}-${String(lastDay).padStart(2, "0")}` };
  }
  if (granularity === "year") return { start: `${period}-01-01`, end: `${period}-12-31` };
  if (granularity === "quarter") {
    const [y, q] = period.split("-Q").map(Number);
    const startMonth = (q - 1) * 3 + 1;
    const endMonth = startMonth + 2;
    const lastDay = new Date(y, endMonth, 0).getDate();
    return {
      start: `${y}-${String(startMonth).padStart(2, "0")}-01`,
      end: `${y}-${String(endMonth).padStart(2, "0")}-${String(lastDay).padStart(2, "0")}`,
    };
  }
  if (granularity === "week") {
    const [yearStr, weekStr] = period.split("-W");
    const year = parseInt(yearStr, 10);
    const week = parseInt(weekStr, 10);
    const jan4 = new Date(Date.UTC(year, 0, 4));
    const jan4DayNr = (jan4.getUTCDay() + 6) % 7;
    const monday = new Date(jan4);
    monday.setUTCDate(jan4.getUTCDate() - jan4DayNr + (week - 1) * 7);
    const sunday = new Date(monday);
    sunday.setUTCDate(monday.getUTCDate() + 6);
    return { start: isoDate(monday), end: isoDate(sunday) };
  }
  return { start: period, end: period };
}

// Presets anchor to today and look back a fixed window; charts.js's brush
// (zoomable:true) lets the user narrow further within that window without
// another round-trip. "Benutzerdefiniert" hands full control to the date
// inputs instead of using a preset lookback.
function computeWindow(granularity) {
  const end = new Date();
  const start = new Date(end);
  if (granularity === "day") start.setDate(start.getDate() - 29);
  else if (granularity === "week") start.setDate(start.getDate() - 7 * 26);
  else if (granularity === "month") { start.setMonth(start.getMonth() - 11); start.setDate(1); }
  else if (granularity === "quarter") { start.setMonth(start.getMonth() - 3 * 7); start.setDate(1); }
  else if (granularity === "year") { start.setFullYear(start.getFullYear() - 5); start.setMonth(0); start.setDate(1); }
  else { start.setMonth(start.getMonth() - 11); start.setDate(1); }
  return { start: isoDate(start), end: isoDate(end) };
}

function computePreviousWindow(start, end) {
  const s = new Date(start);
  const e = new Date(end);
  const spanMs = e - s;
  const prevEnd = new Date(s);
  prevEnd.setDate(prevEnd.getDate() - 1);
  const prevStart = new Date(prevEnd.getTime() - spanMs);
  return { start: isoDate(prevStart), end: isoDate(prevEnd) };
}

// ---------- state ----------

const state = {
  granularity: "month",
  start: null,
  end: null,
  compare: false,
  accountId: "",
  categoryId: "",
  tagId: "",
  categories: [],
  accounts: [],
  tags: [],
  budgets: [],
  summary: null,
  prevSummary: null,
  transactions: [],
};

const LAYOUT_KEY = "budget_dashboard_layout_v2";

// ---------- widget registry ----------

const WIDGET_DEFS = [
  { id: "kpi-income", title: "Einnahmen", kind: "kpi", size: "sm" },
  { id: "kpi-expense", title: "Ausgaben", kind: "kpi", size: "sm" },
  { id: "kpi-net", title: "Cashflow", kind: "kpi", size: "sm" },
  { id: "kpi-savings-rate", title: "Sparquote", kind: "kpi", size: "sm" },
  { id: "kpi-budget-remaining", title: "Restbudget (Monat)", kind: "kpi", size: "sm" },
  { id: "kpi-avg-day", title: "Ø Ausgaben/Tag", kind: "kpi", size: "sm" },
  { id: "chart-income-expense", title: "Einnahmen vs. Ausgaben", kind: "chart", size: "lg", render: renderIncomeExpenseChart },
  { id: "chart-cashflow", title: "Cashflow & Prognose", kind: "chart", size: "lg", render: renderCashflowChart },
  { id: "chart-category-donut", title: "Ausgaben nach Kategorie", kind: "chart", size: "md", render: renderCategoryDonut },
  { id: "list-top-categories", title: "Top-Kategorien", kind: "list", size: "md", render: renderTopCategoriesList },
  { id: "chart-budget-actual", title: "Budget vs. Ist", kind: "chart", size: "lg", render: renderBudgetActualChart },
  { id: "chart-category-trend", title: "Kategorien-Trend", kind: "chart", size: "lg", render: renderCategoryTrendChart },
  { id: "chart-savings-rate", title: "Sparquoten-Verlauf", kind: "chart", size: "md", render: renderSavingsRateChart },
  { id: "chart-cumulative", title: "Kumulierter Cashflow", kind: "chart", size: "md", render: renderCumulativeChart },
  { id: "budgets-manage", title: "Budgets pro Kategorie", kind: "custom", size: "full", render: renderBudgetsManageWidget },
];
const WIDGET_BY_ID = Object.fromEntries(WIDGET_DEFS.map((w) => [w.id, w]));

function defaultLayout() {
  return {
    order: WIDGET_DEFS.map((w) => w.id),
    hidden: [],
    sizes: Object.fromEntries(WIDGET_DEFS.map((w) => [w.id, w.size])),
  };
}

function loadLayout() {
  let stored = null;
  try {
    stored = JSON.parse(localStorage.getItem(LAYOUT_KEY) || "null");
  } catch (err) {
    stored = null;
  }
  const fallback = defaultLayout();
  if (!stored || !Array.isArray(stored.order)) return fallback;
  const known = new Set(WIDGET_DEFS.map((w) => w.id));
  const order = stored.order.filter((id) => known.has(id));
  WIDGET_DEFS.forEach((w) => { if (!order.includes(w.id)) order.push(w.id); });
  const hidden = (stored.hidden || []).filter((id) => known.has(id));
  const sizes = { ...fallback.sizes, ...(stored.sizes || {}) };
  return { order, hidden, sizes };
}

let layout = loadLayout();

function saveLayout() {
  localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
}

// ---------- KPI compare badge ----------

function kpiCompareBadge(current, previous) {
  if (!state.compare || previous === null || previous === undefined) return "";
  if (previous === 0) return "";
  const pct = ((current - previous) / Math.abs(previous)) * 100;
  const dir = pct > 0.5 ? "up" : pct < -0.5 ? "down" : "flat";
  const arrow = dir === "up" ? "▲" : dir === "down" ? "▼" : "▬";
  return `<span class="kpi-compare ${dir}">${arrow} ${Math.abs(pct).toFixed(1)}%</span>`;
}

// ---------- widget renderers: KPI ----------

function renderKpiWidget(id, bodyEl) {
  const s = state.summary;
  const p = state.prevSummary;
  if (!s) { bodyEl.innerHTML = ""; return; }

  if (id === "kpi-income") {
    bodyEl.innerHTML = `<p class="widget-kpi-value credit tabular">${formatMoney(s.total_income)} ${kpiCompareBadge(s.total_income, p && p.total_income)}</p>`;
  } else if (id === "kpi-expense") {
    bodyEl.innerHTML = `<p class="widget-kpi-value debit tabular">${formatMoney(Math.abs(s.total_expense))} ${kpiCompareBadge(Math.abs(s.total_expense), p && Math.abs(p.total_expense))}</p>`;
  } else if (id === "kpi-net") {
    const net = s.total_income + s.total_expense;
    const prevNet = p ? p.total_income + p.total_expense : null;
    bodyEl.innerHTML = `<p class="widget-kpi-value tabular ${net >= 0 ? "credit" : "debit"}">${net >= 0 ? "+" : "−"}${formatMoney(Math.abs(net))} ${kpiCompareBadge(net, prevNet)}</p>`;
  } else if (id === "kpi-savings-rate") {
    const rate = s.total_income > 0 ? ((s.total_income + s.total_expense) / s.total_income) * 100 : 0;
    const prevRate = p && p.total_income > 0 ? ((p.total_income + p.total_expense) / p.total_income) * 100 : null;
    bodyEl.innerHTML = `<p class="widget-kpi-value tabular ${rate >= 0 ? "credit" : "debit"}">${rate.toFixed(1)}% ${kpiCompareBadge(rate, prevRate)}</p>`;
  } else if (id === "kpi-budget-remaining") {
    if (state.budgets.length === 0) {
      bodyEl.innerHTML = `<p class="widget-kpi-value tabular">—</p>`;
    } else {
      const spentByCategory = {};
      s.by_category.forEach((c) => { spentByCategory[c.category] = c.amount_cents; });
      const remaining = state.budgets.reduce(
        (sum, b) => sum + (b.monthly_limit_cents - (spentByCategory[b.category_name] || 0)), 0
      );
      bodyEl.innerHTML = `<p class="widget-kpi-value tabular ${remaining >= 0 ? "credit" : "debit"}">${remaining >= 0 ? "" : "−"}${formatMoney(Math.abs(remaining))}</p>`;
    }
  } else if (id === "kpi-avg-day") {
    const days = Math.max(1, Math.round((new Date(state.end) - new Date(state.start)) / (24 * 3600 * 1000)) + 1);
    const avg = Math.abs(s.total_expense) / days;
    bodyEl.innerHTML = `<p class="widget-kpi-value tabular debit">${formatMoney(avg)}</p>`;
  }
}

// ---------- widget renderers: charts ----------

function buildPeriodSeriesData() {
  const rows = state.summary ? state.summary.by_period : [];
  return rows.map((r) => ({
    x: r.period,
    income: r.income_cents,
    expense: r.expense_cents,
    net: r.net_cents,
  }));
}

function mergePreviousForCompare(data, key, label) {
  if (!state.compare || !state.prevSummary) return { data, series: [] };
  const prevRows = state.prevSummary.by_period;
  const merged = data.map((d, i) => ({ ...d, [`prev_${key}`]: prevRows[i] ? prevRows[i][`${key}_cents`] : 0 }));
  return {
    data: merged,
    series: [{ key: `prev_${key}`, label: `${label} (Vorperiode)`, color: "var(--ink-faint)" }],
  };
}

function openPeriodDrilldown(seriesKey, index, point) {
  const { start, end } = periodToDateRange(point.x, state.granularity);
  openDrilldown({ start, end }, `Buchungen: ${formatPeriodLabel(point.x, state.granularity)}`);
}

function renderIncomeExpenseChart(bodyEl) {
  const data = buildPeriodSeriesData();
  if (data.length === 0) { bodyEl.innerHTML = '<p class="panel-empty">Keine Daten im gewählten Zeitraum.</p>'; return; }
  const series = [
    { key: "income", label: "Einnahmen", color: "var(--series-2)", type: "bar" },
    { key: "expense", label: "Ausgaben", color: "var(--series-1)", type: "bar" },
  ];
  const chartData = data.map((d) => ({ ...d, expense: Math.abs(d.expense) }));
  const compareInfo = state.compare && state.prevSummary
    ? mergePreviousForCompareAbs(chartData)
    : { data: chartData, series: [] };
  renderChart(bodyEl, {
    type: "combo",
    data: compareInfo.data,
    series: [...series, ...compareInfo.series],
    formatValue: formatMoney,
    formatX: (x) => formatPeriodLabel(x, state.granularity),
    zoomable: true,
    onPointClick: openPeriodDrilldown,
  });
}

function mergePreviousForCompareAbs(data) {
  const prevRows = state.prevSummary.by_period;
  const merged = data.map((d, i) => ({
    ...d,
    prev_income: prevRows[i] ? prevRows[i].income_cents : 0,
    prev_expense: prevRows[i] ? Math.abs(prevRows[i].expense_cents) : 0,
  }));
  return {
    data: merged,
    series: [
      { key: "prev_income", label: "Einnahmen (Vorperiode)", color: "var(--ink-faint)", type: "line" },
      { key: "prev_expense", label: "Ausgaben (Vorperiode)", color: "var(--ink-soft)", type: "line" },
    ],
  };
}

// Simple linear-regression forecast over the last up-to-6 periods' net
// cashflow, projected 3 periods forward. Cashflow-trend-only, per product
// decision — no manual net-worth/asset tracking.
function forecastNextPeriods(data, count) {
  const sample = data.slice(-6);
  if (sample.length < 2) return [];
  const n = sample.length;
  const xs = sample.map((_, i) => i);
  const ys = sample.map((d) => d.net);
  const xMean = xs.reduce((a, b) => a + b, 0) / n;
  const yMean = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0, den = 0;
  xs.forEach((x, i) => { num += (x - xMean) * (ys[i] - yMean); den += (x - xMean) ** 2; });
  const slope = den === 0 ? 0 : num / den;
  const intercept = yMean - slope * xMean;

  const points = [];
  for (let i = 1; i <= count; i++) {
    points.push(Math.round(intercept + slope * (n - 1 + i)));
  }
  return points;
}

function advancePeriod(period, granularity, steps) {
  const { start } = periodToDateRange(period, granularity);
  const d = new Date(start);
  if (granularity === "day") d.setDate(d.getDate() + steps);
  else if (granularity === "week") d.setDate(d.getDate() + steps * 7);
  else if (granularity === "month") d.setMonth(d.getMonth() + steps);
  else if (granularity === "quarter") d.setMonth(d.getMonth() + steps * 3);
  else if (granularity === "year") d.setFullYear(d.getFullYear() + steps);
  return periodKeyFor(isoDate(d), granularity);
}

function renderCashflowChart(bodyEl) {
  const data = buildPeriodSeriesData();
  if (data.length === 0) { bodyEl.innerHTML = '<p class="panel-empty">Keine Daten im gewählten Zeitraum.</p>'; return; }

  const forecastFromIndex = data.length;
  const forecastValues = forecastNextPeriods(data, 3);
  const lastPeriod = data[data.length - 1].x;
  const forecastPoints = forecastValues.map((v, i) => ({
    x: advancePeriod(lastPeriod, state.granularity, i + 1),
    net: v,
  }));
  const fullData = [...data, ...forecastPoints];

  renderChart(bodyEl, {
    type: "area",
    data: fullData,
    series: [{ key: "net", label: "Cashflow", color: "var(--series-1)" }],
    formatValue: formatMoney,
    formatX: (x) => formatPeriodLabel(x, state.granularity),
    zoomable: true,
    forecastFromIndex,
    onPointClick: (seriesKey, index, point) => {
      if (index >= forecastFromIndex) return; // forecast points aren't real transactions
      openPeriodDrilldown(seriesKey, index, point);
    },
  });
}

function renderCategoryDonut(bodyEl) {
  const rows = state.summary ? state.summary.by_category : [];
  if (rows.length === 0) { bodyEl.innerHTML = '<p class="panel-empty">Keine Ausgaben im gewählten Zeitraum.</p>'; return; }
  const data = rows.map((c) => ({ x: c.category, value: c.amount_cents, color: categoryColor(c.category) }));
  renderChart(bodyEl, {
    type: "donut",
    data,
    series: [{ key: "value" }],
    formatValue: formatMoney,
    showLegend: false,
    onPointClick: (seriesKey, index, point) => openCategoryDrilldown(point.x),
  });
}

function renderTopCategoriesList(bodyEl) {
  const rows = state.summary ? state.summary.by_category : [];
  const sorted = [...rows].sort((a, b) => b.amount_cents - a.amount_cents).slice(0, 5);
  if (sorted.length === 0) { bodyEl.innerHTML = '<p class="panel-empty">Keine Ausgaben im gewählten Zeitraum.</p>'; return; }
  bodyEl.innerHTML = sorted.map((c, i) => `
    <div class="top-cat-row" data-category="${escapeHtml(c.category)}">
      <span class="top-cat-rank">${i + 1}</span>
      <span class="cat-dot" style="background: ${categoryColor(c.category)}"></span>
      <span class="top-cat-name">${escapeHtml(c.category)}</span>
      <span class="top-cat-amount tabular">${formatMoney(c.amount_cents)}</span>
    </div>
  `).join("");
  bodyEl.querySelectorAll(".top-cat-row").forEach((row) => {
    row.style.cursor = "pointer";
    row.addEventListener("click", () => openCategoryDrilldown(row.dataset.category));
  });
}

function openCategoryDrilldown(categoryName) {
  const cat = state.categories.find((c) => c.name === categoryName);
  openDrilldown(
    { start: state.start, end: state.end, category_id: cat ? cat.id : "" },
    `Buchungen: ${categoryName}`
  );
}

function renderBudgetActualChart(bodyEl) {
  if (state.budgets.length === 0) { bodyEl.innerHTML = '<p class="panel-empty">Noch keine Budgets festgelegt.</p>'; return; }
  const spentByCategory = {};
  (state.summary ? state.summary.by_category : []).forEach((c) => { spentByCategory[c.category] = c.amount_cents; });
  const data = state.budgets.map((b) => ({
    x: b.category_name,
    budget: b.monthly_limit_cents,
    actual: spentByCategory[b.category_name] || 0,
  }));
  renderChart(bodyEl, {
    type: "bar",
    data,
    series: [
      { key: "budget", label: "Budget", color: "var(--series-5)" },
      { key: "actual", label: "Ist", color: "var(--series-1)" },
    ],
    formatValue: formatMoney,
    formatX: (x) => x,
    onPointClick: (seriesKey, index, point) => openCategoryDrilldown(point.x),
  });
}

function renderCategoryTrendChart(bodyEl) {
  if (state.transactions.length === 0) { bodyEl.innerHTML = '<p class="panel-empty">Keine Daten im gewählten Zeitraum.</p>'; return; }

  const totalsByCategory = {};
  state.transactions.forEach((t) => {
    if (t.excluded_from_totals || t.amount_cents >= 0) return;
    const cat = t.category_name || "Unkategorisiert";
    totalsByCategory[cat] = (totalsByCategory[cat] || 0) + Math.abs(t.amount_cents);
  });
  const topCategories = Object.entries(totalsByCategory)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6)
    .map(([name]) => name);
  const topSet = new Set(topCategories);

  const byPeriod = {};
  state.transactions.forEach((t) => {
    if (t.excluded_from_totals || t.amount_cents >= 0) return;
    const period = periodKeyFor(t.date, state.granularity);
    const cat = t.category_name || "Unkategorisiert";
    const bucket = topSet.has(cat) ? cat : "Andere";
    if (!byPeriod[period]) byPeriod[period] = {};
    byPeriod[period][bucket] = (byPeriod[period][bucket] || 0) + Math.abs(t.amount_cents);
  });

  const periods = Object.keys(byPeriod).sort();
  const seriesKeys = [...topCategories, "Andere"];
  const data = periods.map((p) => ({ x: p, ...byPeriod[p] }));
  const series = seriesKeys
    .filter((k) => periods.some((p) => byPeriod[p][k]))
    .map((k, i) => ({ key: k, label: k, color: k === "Andere" ? "var(--ink-faint)" : categoryColor(k) }));

  if (series.length === 0) { bodyEl.innerHTML = '<p class="panel-empty">Keine Ausgaben im gewählten Zeitraum.</p>'; return; }

  renderChart(bodyEl, {
    type: "bar",
    data,
    series,
    stacked: true,
    formatValue: formatMoney,
    formatX: (x) => formatPeriodLabel(x, state.granularity),
    zoomable: true,
    onPointClick: (seriesKey, index, point) => {
      const { start, end } = periodToDateRange(point.x, state.granularity);
      const cat = state.categories.find((c) => c.name === seriesKey);
      openDrilldown(
        { start, end, category_id: cat ? cat.id : "" },
        `${seriesKey}: ${formatPeriodLabel(point.x, state.granularity)}`
      );
    },
  });
}

function renderSavingsRateChart(bodyEl) {
  const rows = state.summary ? state.summary.by_period : [];
  if (rows.length === 0) { bodyEl.innerHTML = '<p class="panel-empty">Keine Daten im gewählten Zeitraum.</p>'; return; }
  const data = rows.map((r) => ({
    x: r.period,
    rate: r.income_cents > 0 ? Math.round(((r.income_cents + r.expense_cents) / r.income_cents) * 10000) : 0,
  }));
  renderChart(bodyEl, {
    type: "line",
    data,
    series: [{ key: "rate", label: "Sparquote", color: "var(--series-2)" }],
    formatValue: (v) => `${(v / 100).toFixed(1)}%`,
    formatX: (x) => formatPeriodLabel(x, state.granularity),
    onPointClick: openPeriodDrilldown,
  });
}

function renderCumulativeChart(bodyEl) {
  const rows = state.summary ? state.summary.by_period : [];
  if (rows.length === 0) { bodyEl.innerHTML = '<p class="panel-empty">Keine Daten im gewählten Zeitraum.</p>'; return; }
  let running = 0;
  const data = rows.map((r) => {
    running += r.net_cents;
    return { x: r.period, cumulative: running };
  });
  renderChart(bodyEl, {
    type: "area",
    data,
    series: [{ key: "cumulative", label: "Kumulierter Cashflow", color: "var(--series-3)" }],
    formatValue: formatMoney,
    formatX: (x) => formatPeriodLabel(x, state.granularity),
    onPointClick: openPeriodDrilldown,
  });
}

// ---------- budgets management widget (custom, non-chart) ----------

function renderBudgetsManageWidget(bodyEl) {
  const spentByCategory = {};
  (state.summary ? state.summary.by_category : []).forEach((c) => { spentByCategory[c.category] = c.amount_cents; });

  const rowsHtml = state.budgets.length === 0
    ? '<p class="panel-empty">Noch keine Budgets festgelegt. Füge unten eines hinzu.</p>'
    : state.budgets.map((b) => {
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

  const budgetedIds = new Set(state.budgets.map((b) => b.category_id));
  const optionsHtml = state.categories
    .filter((c) => c.name !== "Unkategorisiert" && !budgetedIds.has(c.id))
    .map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`)
    .join("");

  bodyEl.innerHTML = `
    <div id="budget-rows">${rowsHtml}</div>
    <div class="budget-add-row">
      <select id="new-budget-category">${optionsHtml}</select>
      <input type="number" id="new-budget-amount" placeholder="Betrag CHF" step="1" min="0">
      <button id="add-budget-btn" class="btn btn-primary" type="button">Budget hinzufügen</button>
    </div>
  `;

  bodyEl.querySelector("#budget-rows").addEventListener("click", async (event) => {
    const row = event.target.closest(".budget-row");
    if (!row) return;
    const categoryId = row.dataset.categoryId;

    if (event.target.dataset.action === "update-budget") {
      const amount = parseFloat(row.querySelector('[data-field="limit"]').value);
      if (Number.isNaN(amount) || amount < 0) { alert("Bitte einen gültigen Betrag eingeben."); return; }
      const res = await fetch(`/api/budgets/${categoryId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ monthly_limit_cents: Math.round(amount * 100) }),
      });
      if (!res.ok) { alert("Fehler beim Speichern — bitte erneut versuchen."); return; }
      await loadBudgets();
      renderAllWidgets();
    }

    if (event.target.dataset.action === "delete-budget") {
      const res = await fetch(`/api/budgets/${categoryId}`, { method: "DELETE" });
      if (!res.ok) { alert("Fehler beim Entfernen — bitte erneut versuchen."); return; }
      await loadBudgets();
      renderAllWidgets();
    }
  });

  bodyEl.querySelector("#add-budget-btn").addEventListener("click", async () => {
    const select = bodyEl.querySelector("#new-budget-category");
    const amountInput = bodyEl.querySelector("#new-budget-amount");
    const categoryId = parseInt(select.value, 10);
    const amount = parseFloat(amountInput.value);
    if (Number.isNaN(categoryId)) { alert("Keine Kategorie verfügbar — allen Kategorien ist bereits ein Budget zugewiesen."); return; }
    if (Number.isNaN(amount) || amount <= 0) { alert("Bitte einen gültigen Betrag eingeben."); return; }
    const res = await fetch(`/api/budgets/${categoryId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ monthly_limit_cents: Math.round(amount * 100) }),
    });
    if (!res.ok) { alert("Fehler beim Hinzufügen — bitte erneut versuchen."); return; }
    await loadBudgets();
    renderAllWidgets();
  });
}

// ---------- drill-down modal ----------

async function openDrilldown(filters, title) {
  const params = new URLSearchParams();
  if (filters.start) params.set("start", filters.start);
  if (filters.end) params.set("end", filters.end);
  if (filters.category_id) params.set("category_id", filters.category_id);
  if (state.accountId) params.set("account_id", state.accountId);
  if (state.tagId) params.set("tag_id", state.tagId);

  document.getElementById("drilldown-title").textContent = title;
  const body = document.getElementById("drilldown-body");
  body.innerHTML = '<p class="panel-empty" style="padding: 1rem 1.25rem;">Lädt…</p>';
  document.getElementById("drilldown-modal").classList.remove("hidden");

  const rows = await fetch(`/api/transactions?${params.toString()}`).then((r) => r.json());
  if (rows.length === 0) {
    body.innerHTML = '<p class="panel-empty" style="padding: 1rem 1.25rem;">Keine Buchungen gefunden.</p>';
    return;
  }
  body.innerHTML = `
    <div class="ledger">
      <table>
        <thead><tr><th>Datum</th><th>Beschreibung</th><th>Kategorie</th><th class="num">Betrag</th></tr></thead>
        <tbody>
          ${rows.map((t) => `
            <tr>
              <td class="date">${escapeHtml(t.date)}</td>
              <td class="desc">${escapeHtml(t.description)}</td>
              <td>${escapeHtml(t.category_name || "—")}</td>
              <td class="amount ${t.amount_cents >= 0 ? "credit" : "debit"} tabular">${formatMoney(t.amount_cents)} <span class="currency-tag">${escapeHtml(t.currency)}</span></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

document.getElementById("drilldown-close").addEventListener("click", () => {
  document.getElementById("drilldown-modal").classList.add("hidden");
});
document.getElementById("drilldown-modal").addEventListener("click", (event) => {
  if (event.target.id === "drilldown-modal") event.target.classList.add("hidden");
});

// ---------- widget grid: build, drag, resize, hide/show ----------

const SIZE_CYCLE = ["sm", "md", "lg", "full"];

function buildWidgetEl(id) {
  const def = WIDGET_BY_ID[id];
  const el = document.createElement("div");
  el.className = `widget ${def.kind === "kpi" ? "kpi-widget" : ""}`;
  el.dataset.widgetId = id;
  el.dataset.size = layout.sizes[id] || def.size;
  el.draggable = true;

  const resizeBtn = def.kind === "kpi" ? "" : `<button type="button" class="widget-action-btn" data-action="resize" title="Grösse ändern">⤢</button>`;
  el.innerHTML = `
    <div class="widget-head">
      <span class="widget-drag-handle" title="Verschieben">⠿</span>
      <p class="widget-title">${escapeHtml(def.title)}</p>
      <div class="widget-actions">
        ${resizeBtn}
        <button type="button" class="widget-action-btn" data-action="hide" title="Ausblenden">✕</button>
      </div>
    </div>
    <div class="widget-body"></div>
  `;

  el.querySelector('[data-action="hide"]').addEventListener("click", () => {
    layout.hidden.push(id);
    layout.order = layout.order.filter((x) => x !== id);
    layout.order.push(id);
    saveLayout();
    renderAllWidgets();
  });
  const resizeEl = el.querySelector('[data-action="resize"]');
  if (resizeEl) {
    resizeEl.addEventListener("click", () => {
      const current = layout.sizes[id] || def.size;
      const next = SIZE_CYCLE[(SIZE_CYCLE.indexOf(current) + 1) % SIZE_CYCLE.length];
      layout.sizes[id] = next;
      el.dataset.size = next;
      saveLayout();
      def.render(el.querySelector(".widget-body"));
    });
  }

  el.addEventListener("dragstart", (event) => {
    el.classList.add("dragging");
    event.dataTransfer.setData("text/plain", id);
    event.dataTransfer.effectAllowed = "move";
  });
  el.addEventListener("dragend", () => el.classList.remove("dragging"));
  el.addEventListener("dragover", (event) => {
    event.preventDefault();
    el.classList.add("drag-over");
  });
  el.addEventListener("dragleave", () => el.classList.remove("drag-over"));
  el.addEventListener("drop", (event) => {
    event.preventDefault();
    el.classList.remove("drag-over");
    const draggedId = event.dataTransfer.getData("text/plain");
    if (!draggedId || draggedId === id) return;
    layout.order = layout.order.filter((x) => x !== draggedId);
    const targetIndex = layout.order.indexOf(id);
    layout.order.splice(targetIndex, 0, draggedId);
    saveLayout();
    renderAllWidgets();
  });

  const body = el.querySelector(".widget-body");
  if (def.kind === "kpi") {
    renderKpiWidget(id, body);
  } else if (def.render) {
    def.render(body);
  }
  return el;
}

function renderAllWidgets() {
  const grid = document.getElementById("dashboard-grid");
  grid.innerHTML = "";
  layout.order
    .filter((id) => !layout.hidden.includes(id))
    .forEach((id) => grid.appendChild(buildWidgetEl(id)));
  renderWidgetPicker();
}

function renderWidgetPicker() {
  const menu = document.getElementById("widget-picker-menu");
  const hiddenDefs = layout.hidden.map((id) => WIDGET_BY_ID[id]).filter(Boolean);
  if (hiddenDefs.length === 0) {
    menu.innerHTML = '<p class="widget-picker-empty">Alle Widgets sind sichtbar.</p>';
    return;
  }
  menu.innerHTML = hiddenDefs.map((def) => `
    <button type="button" class="widget-picker-item" data-widget-id="${def.id}">+ ${escapeHtml(def.title)}</button>
  `).join("");
  menu.querySelectorAll(".widget-picker-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      layout.hidden = layout.hidden.filter((x) => x !== btn.dataset.widgetId);
      saveLayout();
      renderAllWidgets();
      menu.classList.add("hidden");
    });
  });
}

document.getElementById("add-widget-btn").addEventListener("click", () => {
  document.getElementById("widget-picker-menu").classList.toggle("hidden");
});
document.addEventListener("click", (event) => {
  const picker = document.querySelector(".widget-picker");
  if (picker && !picker.contains(event.target)) {
    document.getElementById("widget-picker-menu").classList.add("hidden");
  }
});

// ---------- toolbar / filters ----------

function updateCustomFieldsVisibility() {
  const isCustom = state.granularity === "custom";
  document.getElementById("custom-start-field").classList.toggle("hidden", !isCustom);
  document.getElementById("custom-end-field").classList.toggle("hidden", !isCustom);
}

document.getElementById("granularity-picker").addEventListener("click", async (event) => {
  const btn = event.target.closest(".segmented-option");
  if (!btn) return;
  document.querySelectorAll("#granularity-picker .segmented-option").forEach((b) => b.classList.toggle("active", b === btn));
  state.granularity = btn.dataset.granularity;
  updateCustomFieldsVisibility();
  if (state.granularity !== "custom") {
    await loadData();
  }
});

document.getElementById("compare-toggle").addEventListener("change", async (event) => {
  state.compare = event.target.checked;
  await loadData();
});

document.getElementById("apply-filters-btn").addEventListener("click", async () => {
  state.accountId = document.getElementById("filter-account").value;
  state.categoryId = document.getElementById("filter-category").value;
  state.tagId = document.getElementById("filter-tag").value;
  if (state.granularity === "custom") {
    state.start = document.getElementById("filter-start").value || state.start;
    state.end = document.getElementById("filter-end").value || state.end;
  }
  await loadData();
});

// ---------- data loading ----------

async function loadFilterOptions() {
  const [categories, accounts, tags] = await Promise.all([
    fetch("/api/categories").then((r) => r.json()),
    fetch("/api/accounts").then((r) => r.json()),
    fetch("/api/tags").then((r) => r.json()),
  ]);
  state.categories = categories;
  state.accounts = accounts;
  state.tags = tags;

  document.getElementById("filter-account").innerHTML = '<option value="">Alle Konten</option>' +
    accounts.map((a) => `<option value="${a.id}">${escapeHtml(a.name)}</option>`).join("");
  document.getElementById("filter-category").innerHTML = '<option value="">Alle Kategorien</option>' +
    categories.map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");
  document.getElementById("filter-tag").innerHTML = '<option value="">Alle Tags</option>' +
    tags.map((t) => `<option value="${t.id}">${escapeHtml(t.name)}</option>`).join("");
}

async function loadBudgets() {
  state.budgets = await fetch("/api/budgets").then((r) => r.json());
}

function summaryParams(start, end) {
  const params = new URLSearchParams({ start, end, granularity: state.granularity === "custom" ? "month" : state.granularity });
  if (state.accountId) params.set("account_id", state.accountId);
  if (state.categoryId) params.set("category_id", state.categoryId);
  if (state.tagId) params.set("tag_id", state.tagId);
  return params;
}

async function loadData() {
  if (state.granularity !== "custom") {
    const win = computeWindow(state.granularity);
    state.start = win.start;
    state.end = win.end;
    document.getElementById("filter-start").value = win.start;
    document.getElementById("filter-end").value = win.end;
  }

  const [summary, transactions] = await Promise.all([
    fetch(`/api/summary?${summaryParams(state.start, state.end)}`).then((r) => r.json()),
    fetch(`/api/transactions?${summaryParams(state.start, state.end)}`).then((r) => r.json()),
  ]);
  state.summary = summary;
  state.transactions = transactions;

  if (state.compare) {
    const prevWin = computePreviousWindow(state.start, state.end);
    state.prevSummary = await fetch(`/api/summary?${summaryParams(prevWin.start, prevWin.end)}`).then((r) => r.json());
  } else {
    state.prevSummary = null;
  }

  await loadBudgets();
  renderAllWidgets();
}

(async function init() {
  updateCustomFieldsVisibility();
  await loadFilterOptions();
  await loadData();
})();
