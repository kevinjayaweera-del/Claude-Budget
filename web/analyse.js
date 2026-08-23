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

// Budgets are stored as a monthly figure (monthly_limit_cents), but the
// budget-vs-actual widgets need to compare against whatever window
// state.start..state.end currently covers — a fixed "always this calendar
// month" comparison (an earlier version of this) never changed when the
// toolbar's own period picker did, which read as "the numbers don't
// update". Scaling the monthly limit by the selected window's length
// (relative to an average 30.44-day month) keeps the comparison
// meaningful for any period while still visibly responding to it: a
// 3-month "Quartal" window compares against ~3x the monthly limit, a
// single "Tag" window against ~1/30 of it, etc.
const AVG_DAYS_PER_MONTH = 30.436875;
function periodScaleFactor() {
  const days = Math.round((new Date(state.end) - new Date(state.start)) / (24 * 3600 * 1000)) + 1;
  return Math.max(days, 1) / AVG_DAYS_PER_MONTH;
}

function computePreviousWindow(start, end) {
  const s = new Date(start);
  const e = new Date(end);
  const spanMs = e - s;
  const prevEnd = new Date(s);
  prevEnd.setUTCDate(prevEnd.getUTCDate() - 1); // UTC setter — see advancePeriod() for why (DST safety)
  const prevStart = new Date(prevEnd.getTime() - spanMs);
  return { start: isoDate(prevStart), end: isoDate(prevEnd) };
}

// summaryParams() collapses "custom" to "month" before hitting the backend
// (see below) — anywhere we need to walk/step actual period keys, we must
// use the same collapsed granularity or periodKeyFor()/advancePeriod() will
// disagree with what the server actually grouped by.
function effectiveGranularity() {
  return state.granularity === "custom" ? "month" : state.granularity;
}

// /api/summary groups by period via pandas groupby, so periods with zero
// transactions are simply absent from the response (sparse), not zero-filled.
// Chart code needs a dense, gap-filled list of period keys spanning the
// requested window so the x-axis doesn't silently compress past empty
// periods and so index-based alignment (see mergePreviousForCompare) has
// something safe to fall back to.
function generatePeriodRange(start, end, granularity) {
  const startKey = periodKeyFor(start, granularity);
  const endKey = periodKeyFor(end, granularity);
  const keys = [];
  let cursor = startKey;
  for (let i = 0; i < 5000 && cursor <= endKey; i++) {
    keys.push(cursor);
    if (cursor === endKey) break;
    cursor = advancePeriod(cursor, granularity, 1);
  }
  return keys;
}

// Maps a period in the CURRENT window to its counterpart in the previous
// window by shifting dates, not array position — mirrors the exact shift
// computePreviousWindow() applies to the whole window. Index-based zipping
// (the previous approach) silently misaligns whenever either window has a
// gap period, which filtering by account/category/tag makes common.
function previousPeriodKeyFor(periodKey, granularity) {
  const { start } = periodToDateRange(periodKey, granularity);
  const spanMs = new Date(state.end) - new Date(state.start);
  const shiftMs = spanMs + 24 * 3600 * 1000;
  const shifted = new Date(new Date(start).getTime() - shiftMs);
  return periodKeyFor(isoDate(shifted), granularity);
}

// ---------- state ----------

const state = {
  granularity: "month",
  start: null,
  end: null,
  compare: false,
  accountIds: [],
  categoryIds: [],
  tagIds: [],
  type: "",
  minAmount: "",
  maxAmount: "",
  search: "",
  categories: [],
  accounts: [],
  tags: [],
  budgets: [],
  summary: null,
  prevSummary: null,
  transactions: [],
  recurring: [],
};

const LAYOUT_KEY = "budget_dashboard_layout_v2";

// ---------- widget registry ----------

const WIDGET_DEFS = [
  { id: "kpi-income", title: "Einnahmen", kind: "kpi", size: "sm" },
  { id: "kpi-expense", title: "Ausgaben", kind: "kpi", size: "sm" },
  { id: "kpi-net", title: "Cashflow", kind: "kpi", size: "sm" },
  { id: "kpi-savings-rate", title: "Sparquote", kind: "kpi", size: "sm" },
  { id: "kpi-budget-remaining", title: "Restbudget", kind: "kpi", size: "sm" },
  { id: "kpi-avg-day", title: "Ø Ausgaben/Tag", kind: "kpi", size: "sm" },
  { id: "chart-income-expense", title: "Einnahmen vs. Ausgaben", kind: "chart", size: "lg", render: renderIncomeExpenseChart },
  { id: "chart-cashflow", title: "Cashflow & Prognose", kind: "chart", size: "lg", render: renderCashflowChart },
  { id: "chart-category-donut", title: "Ausgaben nach Kategorie", kind: "chart", size: "md", render: renderCategoryDonut },
  { id: "list-top-categories", title: "Top-Kategorien", kind: "list", size: "md", render: renderTopCategoriesList },
  { id: "chart-tag-breakdown", title: "Ausgaben nach Tag", kind: "chart", size: "md", render: renderTagBreakdownChart },
  { id: "chart-budget-actual", title: "Budget vs. Ist", kind: "chart", size: "lg", render: renderBudgetActualChart },
  { id: "chart-category-trend", title: "Kategorien-Trend", kind: "chart", size: "lg", render: renderCategoryTrendChart },
  { id: "chart-savings-rate", title: "Sparquoten-Verlauf", kind: "chart", size: "md", render: renderSavingsRateChart },
  { id: "chart-cumulative", title: "Kumulierter Cashflow", kind: "chart", size: "md", render: renderCumulativeChart },
  { id: "list-top-merchants", title: "Top-Händler", kind: "list", size: "md", render: renderTopMerchantsList },
  { id: "list-recurring", title: "Wiederkehrende Zahlungen", kind: "list", size: "md", render: renderRecurringList },
  { id: "list-largest-transactions", title: "Größte Einzelausgaben", kind: "list", size: "md", render: renderLargestTransactionsList },
  { id: "list-category-movers", title: "Größte Veränderungen", kind: "list", size: "md", render: renderCategoryMoversList },
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
      // state.summary — the same period-filtered data every other widget
      // uses — with each monthly_limit_cents scaled to match the selected
      // window (see periodScaleFactor), so this responds to the toolbar's
      // period picker like everything else instead of silently staying
      // fixed to the current calendar month.
      const scale = periodScaleFactor();
      const spentByCategory = {};
      s.by_category.forEach((c) => { spentByCategory[c.category] = c.amount_cents; });
      const remaining = state.budgets.reduce(
        (sum, b) => sum + (b.monthly_limit_cents * scale - (spentByCategory[b.category_name] || 0)), 0
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
  if (!state.summary || !state.start || !state.end) return [];
  const granularity = effectiveGranularity();
  const byKey = new Map(state.summary.by_period.map((r) => [r.period, r]));
  return generatePeriodRange(state.start, state.end, granularity).map((p) => {
    const r = byKey.get(p);
    return {
      x: p,
      income: r ? r.income_cents : 0,
      expense: r ? r.expense_cents : 0,
      net: r ? r.net_cents : 0,
    };
  });
}

function mergePreviousForCompare(data, key, label) {
  if (!state.compare || !state.prevSummary) return { data, series: [] };
  const granularity = effectiveGranularity();
  const prevByKey = new Map(state.prevSummary.by_period.map((r) => [r.period, r]));
  const merged = data.map((d) => {
    const prevRow = prevByKey.get(previousPeriodKeyFor(d.x, granularity));
    return { ...d, [`prev_${key}`]: prevRow ? prevRow[`${key}_cents`] : 0 };
  });
  return {
    data: merged,
    series: [{ key: `prev_${key}`, label: `${label} (Vorperiode)`, color: "var(--ink-faint)" }],
  };
}

function openPeriodDrilldown(seriesKey, index, point) {
  const granularity = effectiveGranularity();
  const { start, end } = periodToDateRange(point.x, granularity);
  openDrilldown({ start, end }, `Buchungen: ${formatPeriodLabel(point.x, granularity)}`);
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
    formatX: (x) => formatPeriodLabel(x, effectiveGranularity()),
    zoomable: true,
    onPointClick: openPeriodDrilldown,
  });
}

function mergePreviousForCompareAbs(data) {
  const granularity = effectiveGranularity();
  const prevByKey = new Map(state.prevSummary.by_period.map((r) => [r.period, r]));
  const merged = data.map((d) => {
    const prevRow = prevByKey.get(previousPeriodKeyFor(d.x, granularity));
    return {
      ...d,
      prev_income: prevRow ? prevRow.income_cents : 0,
      prev_expense: prevRow ? Math.abs(prevRow.expense_cents) : 0,
    };
  });
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
  // UTC setters only: `d` is a UTC-midnight instant (parsed from a plain
  // YYYY-MM-DD string). Local setters (setMonth/setDate) would apply Europe/
  // Zurich's DST offset, which can roll the result back onto the previous
  // day/month right around a DST transition (e.g. late March), making this
  // function silently fail to advance and looping callers spin forever.
  if (granularity === "day") d.setUTCDate(d.getUTCDate() + steps);
  else if (granularity === "week") d.setUTCDate(d.getUTCDate() + steps * 7);
  else if (granularity === "month") d.setUTCMonth(d.getUTCMonth() + steps);
  else if (granularity === "quarter") d.setUTCMonth(d.getUTCMonth() + steps * 3);
  else if (granularity === "year") d.setUTCFullYear(d.getUTCFullYear() + steps);
  return periodKeyFor(isoDate(d), granularity);
}

function renderCashflowChart(bodyEl) {
  const data = buildPeriodSeriesData();
  if (data.length === 0) { bodyEl.innerHTML = '<p class="panel-empty">Keine Daten im gewählten Zeitraum.</p>'; return; }

  const forecastFromIndex = data.length;
  const forecastValues = forecastNextPeriods(data, 3);
  const lastPeriod = data[data.length - 1].x;
  const forecastPoints = forecastValues.map((v, i) => ({
    x: advancePeriod(lastPeriod, effectiveGranularity(), i + 1),
    net: v,
  }));
  const fullData = [...data, ...forecastPoints];

  renderChart(bodyEl, {
    type: "area",
    data: fullData,
    series: [{ key: "net", label: "Cashflow", color: "var(--series-1)" }],
    formatValue: formatMoney,
    formatX: (x) => formatPeriodLabel(x, effectiveGranularity()),
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
  // A donut's height IS its diameter (_drawPie draws a square of
  // Math.min(width, height)) — charts.js's general cartesian-chart height
  // formula is far too short for that, so this widget explicitly asks for
  // something close to its own width instead, capped so it doesn't dwarf
  // the "Top-Kategorien" widget next to it.
  const width = bodyEl.clientWidth || 600;
  renderChart(bodyEl, {
    type: "donut",
    data,
    width,
    height: Math.min(width * 0.85, 420),
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

// Groups expenses by tag (e.g. "Ferien Berlin" from a trip tagged via the
// Übersicht page's Reise-Erkennung tool) — a transaction with several tags
// contributes its full amount to each one, matching what filtering the
// ledger by that single tag would show.
function renderTagBreakdownChart(bodyEl) {
  const rows = state.summary ? state.summary.by_tag : [];
  if (rows.length === 0) { bodyEl.innerHTML = '<p class="panel-empty">Noch keine getaggten Buchungen im gewählten Zeitraum.</p>'; return; }
  const sorted = [...rows].sort((a, b) => b.amount_cents - a.amount_cents);
  const data = sorted.map((t) => ({ x: t.tag, amount: t.amount_cents }));
  renderChart(bodyEl, {
    type: "bar",
    data,
    series: [{ key: "amount", label: "Ausgaben", color: "var(--series-4)" }],
    formatValue: formatMoney,
    formatX: (x) => x,
    showLegend: false,
    onPointClick: (seriesKey, index, point) => openTagDrilldown(point.x),
  });
}

function openTagDrilldown(tagName) {
  const tag = state.tags.find((t) => t.name === tagName);
  openDrilldown(
    { start: state.start, end: state.end, tag_id: tag ? tag.id : "" },
    `Buchungen: ${tagName}`
  );
}

function renderBudgetActualChart(bodyEl) {
  if (state.budgets.length === 0) { bodyEl.innerHTML = '<p class="panel-empty">Noch keine Budgets festgelegt.</p>'; return; }
  // state.summary (the same period-filtered data every other widget uses)
  // with each monthly_limit_cents scaled to the selected window (see
  // periodScaleFactor) — "Ist" is this window's actual spend, "Budget" is
  // the proportional allowance for that same window, so both bars respond
  // to the toolbar's period picker instead of "Budget" silently staying
  // fixed to one calendar month regardless of what's selected.
  const scale = periodScaleFactor();
  const spentByCategory = {};
  (state.summary ? state.summary.by_category : []).forEach((c) => { spentByCategory[c.category] = c.amount_cents; });
  const data = state.budgets.map((b) => ({
    x: b.category_name,
    budget: Math.round(b.monthly_limit_cents * scale),
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
    onPointClick: (seriesKey, index, point) => {
      const cat = state.categories.find((c) => c.name === point.x);
      openDrilldown({ start: state.start, end: state.end, category_id: cat ? cat.id : "" }, `Buchungen: ${point.x}`);
    },
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

  const granularity = effectiveGranularity();
  const byPeriod = {};
  state.transactions.forEach((t) => {
    if (t.excluded_from_totals || t.amount_cents >= 0) return;
    const period = periodKeyFor(t.date, granularity);
    const cat = t.category_name || "Unkategorisiert";
    const bucket = topSet.has(cat) ? cat : "Andere";
    if (!byPeriod[period]) byPeriod[period] = {};
    byPeriod[period][bucket] = (byPeriod[period][bucket] || 0) + Math.abs(t.amount_cents);
  });

  // Dense-fill: state.transactions only contains periods with matching
  // activity, so without this a zero-activity period silently disappears
  // from the x-axis instead of showing as zero, compressing the timeline.
  const periods = state.start && state.end
    ? generatePeriodRange(state.start, state.end, granularity)
    : Object.keys(byPeriod).sort();
  const seriesKeys = [...topCategories, "Andere"];
  const data = periods.map((p) => ({ x: p, ...(byPeriod[p] || {}) }));
  const series = seriesKeys
    .filter((k) => periods.some((p) => byPeriod[p] && byPeriod[p][k]))
    .map((k, i) => ({ key: k, label: k, color: k === "Andere" ? "var(--ink-faint)" : categoryColor(k) }));

  if (series.length === 0) { bodyEl.innerHTML = '<p class="panel-empty">Keine Ausgaben im gewählten Zeitraum.</p>'; return; }

  renderChart(bodyEl, {
    type: "bar",
    data,
    series,
    stacked: true,
    formatValue: formatMoney,
    formatX: (x) => formatPeriodLabel(x, granularity),
    zoomable: true,
    onPointClick: (seriesKey, index, point) => {
      const { start, end } = periodToDateRange(point.x, granularity);
      const cat = state.categories.find((c) => c.name === seriesKey);
      openDrilldown(
        { start, end, category_id: cat ? cat.id : "" },
        `${seriesKey}: ${formatPeriodLabel(point.x, granularity)}`
      );
    },
  });
}

function renderSavingsRateChart(bodyEl) {
  const rows = buildPeriodSeriesData();
  if (rows.length === 0) { bodyEl.innerHTML = '<p class="panel-empty">Keine Daten im gewählten Zeitraum.</p>'; return; }
  const data = rows.map((r) => ({
    x: r.x,
    rate: r.income > 0 ? Math.round(((r.income + r.expense) / r.income) * 10000) : 0,
  }));
  renderChart(bodyEl, {
    type: "line",
    data,
    series: [{ key: "rate", label: "Sparquote", color: "var(--series-2)" }],
    formatValue: (v) => `${(v / 100).toFixed(1)}%`,
    formatX: (x) => formatPeriodLabel(x, effectiveGranularity()),
    onPointClick: openPeriodDrilldown,
  });
}

function renderCumulativeChart(bodyEl) {
  const rows = buildPeriodSeriesData();
  if (rows.length === 0) { bodyEl.innerHTML = '<p class="panel-empty">Keine Daten im gewählten Zeitraum.</p>'; return; }
  let running = 0;
  const data = rows.map((r) => {
    running += r.net;
    return { x: r.x, cumulative: running };
  });
  renderChart(bodyEl, {
    type: "area",
    data,
    series: [{ key: "cumulative", label: "Kumulierter Cashflow", color: "var(--series-3)" }],
    formatValue: formatMoney,
    formatX: (x) => formatPeriodLabel(x, effectiveGranularity()),
    onPointClick: openPeriodDrilldown,
  });
}

// ---------- widget renderers: insight lists (merchants, recurring, largest, movers) ----------

// Shared row markup with the four-column .top-cat-row grid (rank / dot /
// name / amount) used elsewhere on the page — a blank rank cell keeps the
// grid columns aligned for widgets (like movers) that don't have a
// meaningful rank of their own.
function insightRow({ rank = "", dot, name, meta, amount, amountClass = "", dataAttrs = {} }) {
  const attrs = Object.entries({ name, ...dataAttrs }).map(([k, v]) => `data-${k}="${escapeHtml(v)}"`).join(" ");
  return `
    <div class="top-cat-row" ${attrs}>
      <span class="top-cat-rank">${rank}</span>
      <span class="cat-dot" style="background: ${dot}"></span>
      <span class="top-cat-name" title="${escapeHtml(meta ? `${name} — ${meta}` : name)}">${escapeHtml(name)}${meta ? ` <span class="insight-meta">${escapeHtml(meta)}</span>` : ""}</span>
      <span class="top-cat-amount tabular ${amountClass}">${amount}</span>
    </div>
  `;
}

function renderTopMerchantsList(bodyEl) {
  const rows = state.summary ? state.summary.by_merchant : [];
  const sorted = rows.slice(0, 6);
  if (sorted.length === 0) { bodyEl.innerHTML = '<p class="panel-empty">Keine Ausgaben im gewählten Zeitraum.</p>'; return; }
  bodyEl.innerHTML = sorted.map((m, i) => insightRow({
    rank: i + 1,
    dot: "var(--ink-faint)",
    name: m.merchant,
    meta: `${m.count}×`,
    amount: formatMoney(m.amount_cents),
    dataAttrs: { "merchant-key": m.merchant_key },
  })).join("");
  bodyEl.querySelectorAll(".top-cat-row").forEach((row) => {
    row.style.cursor = "pointer";
    row.addEventListener("click", () => {
      openDrilldown(
        { start: state.start, end: state.end, merchant_key: row.dataset.merchantKey },
        `Buchungen: ${row.dataset.name}`
      );
    });
  });
}

// state.recurring is loaded once per loadData() call but deliberately
// ignores the toolbar's date-range filter (see loadRecurring) — a
// subscription's cadence only shows up looking back several months, not
// within whatever window "Woche"/"Monat"/etc. happens to be set to.
function renderRecurringList(bodyEl) {
  const rows = state.recurring || [];
  if (rows.length === 0) {
    bodyEl.innerHTML = '<p class="panel-empty">Keine wiederkehrenden Zahlungen erkannt (mind. 3 Monate in Folge, stabiler Betrag).</p>';
    return;
  }
  const monthlyTotal = rows.reduce((sum, r) => sum + r.avg_amount_cents, 0);
  const listHtml = rows.slice(0, 8).map((r, i) => insightRow({
    rank: i + 1,
    dot: categoryColor(r.category_name || "Sonstiges"),
    name: r.merchant,
    meta: `${r.distinct_months}× · zuletzt ${r.last_date}`,
    amount: formatMoney(r.avg_amount_cents),
    dataAttrs: { "merchant-key": r.merchant_key },
  })).join("");
  bodyEl.innerHTML = `<p class="insight-total">Ø ${formatMoney(monthlyTotal)} CHF/Monat in ${rows.length} erkannten Abos</p>${listHtml}`;
  bodyEl.querySelectorAll(".top-cat-row").forEach((row) => {
    row.style.cursor = "pointer";
    row.addEventListener("click", () => {
      openDrilldown({ merchant_key: row.dataset.merchantKey }, `Wiederkehrend: ${row.dataset.name}`);
    });
  });
}

function renderLargestTransactionsList(bodyEl) {
  const rows = (state.transactions || []).filter((t) => !t.excluded_from_totals && t.amount_cents < 0);
  const sorted = [...rows].sort((a, b) => a.amount_cents - b.amount_cents).slice(0, 6);
  if (sorted.length === 0) { bodyEl.innerHTML = '<p class="panel-empty">Keine Ausgaben im gewählten Zeitraum.</p>'; return; }
  bodyEl.innerHTML = sorted.map((t, i) => insightRow({
    rank: i + 1,
    dot: categoryColor(t.category_name || "Sonstiges"),
    name: t.description,
    meta: t.date,
    amount: formatMoney(t.amount_cents),
    amountClass: "debit",
    dataAttrs: { "txn-id": t.id },
  })).join("");
  bodyEl.querySelectorAll(".top-cat-row").forEach((row) => {
    row.style.cursor = "pointer";
    row.addEventListener("click", () => {
      const txn = sorted.find((t) => String(t.id) === row.dataset.txnId);
      if (!txn) return;
      openDrilldown({ start: txn.date, end: txn.date }, `Buchungen: ${formatPeriodLabel(txn.date, "day")}`);
    });
  });
}

// Compares the current period's by_category totals against the previous
// period's (state.prevSummary — the same "Vorperiode vergleichen" data
// every KPI badge already uses) rather than fetching anything new, so this
// widget is only meaningful once that toggle is on.
function renderCategoryMoversList(bodyEl) {
  if (!state.compare || !state.prevSummary) {
    bodyEl.innerHTML = '<p class="panel-empty">Aktiviere "Vorperiode vergleichen" im Filter, um Veränderungen zu sehen.</p>';
    return;
  }
  const current = {};
  (state.summary ? state.summary.by_category : []).forEach((c) => { current[c.category] = c.amount_cents; });
  const previous = {};
  (state.prevSummary ? state.prevSummary.by_category : []).forEach((c) => { previous[c.category] = c.amount_cents; });
  const allNames = new Set([...Object.keys(current), ...Object.keys(previous)]);
  const movers = [...allNames]
    .map((name) => {
      const cur = current[name] || 0;
      const prev = previous[name] || 0;
      return { name, delta: cur - prev, cur, prev };
    })
    .filter((m) => m.prev > 0 || m.cur > 0);
  const increases = movers.filter((m) => m.delta > 0).sort((a, b) => b.delta - a.delta).slice(0, 3);
  const decreases = movers.filter((m) => m.delta < 0).sort((a, b) => a.delta - b.delta).slice(0, 3);
  if (increases.length === 0 && decreases.length === 0) {
    bodyEl.innerHTML = '<p class="panel-empty">Keine Veränderungen gegenüber der Vorperiode.</p>';
    return;
  }
  function moverRow(m, dir) {
    const pct = m.prev > 0 ? Math.abs((m.delta / m.prev) * 100) : null;
    return `
      <div class="top-cat-row" data-category="${escapeHtml(m.name)}">
        <span class="top-cat-rank"></span>
        <span class="cat-dot" style="background: ${categoryColor(m.name)}"></span>
        <span class="top-cat-name">${escapeHtml(m.name)}</span>
        <span class="kpi-compare ${dir}">${dir === "up" ? "▲" : "▼"} ${formatMoney(Math.abs(m.delta))}${pct !== null ? ` (${pct.toFixed(0)}%)` : ""}</span>
      </div>
    `;
  }
  bodyEl.innerHTML = `
    ${increases.length ? `<p class="insight-total">Stärkste Anstiege</p>${increases.map((m) => moverRow(m, "up")).join("")}` : ""}
    ${decreases.length ? `<p class="insight-total">Stärkste Rückgänge</p>${decreases.map((m) => moverRow(m, "down")).join("")}` : ""}
  `;
  bodyEl.querySelectorAll(".top-cat-row").forEach((row) => {
    row.style.cursor = "pointer";
    row.addEventListener("click", () => openCategoryDrilldown(row.dataset.category));
  });
}

// ---------- budgets management widget (custom, non-chart) ----------

function renderBudgetsManageWidget(bodyEl) {
  // state.summary (the same period-filtered data every other widget uses)
  // with each monthly_limit_cents scaled to the selected window (see
  // periodScaleFactor), so "spent / limit" and the progress bar respond to
  // the toolbar's period picker instead of always comparing against one
  // calendar month no matter what's selected. The edit input below always
  // shows/saves the true monthly figure (that's what's actually stored) —
  // only the *comparison* shown here scales with the period.
  const scale = periodScaleFactor();
  const spentByCategory = {};
  (state.summary ? state.summary.by_category : []).forEach((c) => { spentByCategory[c.category] = c.amount_cents; });

  const rowsHtml = state.budgets.length === 0
    ? '<p class="panel-empty">Noch keine Budgets festgelegt. Füge unten eines hinzu.</p>'
    : state.budgets.map((b) => {
      const spent = spentByCategory[b.category_name] || 0;
      const scaledLimit = b.monthly_limit_cents * scale;
      const pct = scaledLimit > 0 ? (spent / scaledLimit) * 100 : 0;
      const level = pct >= 100 ? "over" : pct >= 75 ? "warn" : "ok";
      return `
        <div class="budget-row" data-category-id="${b.category_id}">
          <div class="budget-row-head">
            <span class="cat-dot" style="background: ${categoryColor(b.category_name)}"></span>
            <span class="budget-row-name">${escapeHtml(b.category_name)}</span>
            <span class="budget-row-amounts tabular">${formatMoney(spent)} / ${formatMoney(scaledLimit)} CHF</span>
          </div>
          <div class="budget-track">
            <div class="budget-fill ${level}" style="width: ${Math.min(pct, 100)}%"></div>
          </div>
          <div class="budget-row-actions">
            <input type="number" step="1" min="0" class="budget-limit-input" value="${Math.round(b.monthly_limit_cents / 100)}" data-field="limit" title="Monatliches Budget in CHF">
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
  // filters.category_id (set by the caller — e.g. a clicked donut slice or
  // budget bar) deliberately overrides state.categoryIds rather than
  // combining with it: drilling into one specific category should show
  // that category regardless of what the global filter happens to be.
  if (filters.category_id) params.set("category_id", filters.category_id);
  else if (state.categoryIds.length) params.set("category_id", state.categoryIds.join(","));
  if (state.accountIds.length) params.set("account_id", state.accountIds.join(","));
  // Same override rule as category_id above — a clicked tag bar should show
  // that tag regardless of the global tag filter.
  if (filters.tag_id) params.set("tag_id", filters.tag_id);
  else if (state.tagIds.length) params.set("tag_id", state.tagIds.join(","));
  if (state.type) params.set("type", state.type);
  if (state.minAmount) params.set("min_amount", state.minAmount);
  if (state.maxAmount) params.set("max_amount", state.maxAmount);
  if (state.search) params.set("q", state.search);
  // Set by the Top-Händler/Wiederkehrende-Zahlungen widgets — narrows to
  // exactly the merchant group a summary row aggregated (see _merchant_key
  // in server/app.py). No global equivalent filter exists to override.
  if (filters.merchant_key) params.set("merchant_key", filters.merchant_key);

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
      renderWidgetContent(id, el.querySelector(".widget-body"));
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

  // Content is deliberately NOT rendered here — buildWidgetEl() only
  // builds the DOM skeleton. The caller must append `el` to the document
  // first, then call renderWidgetContent(), or a chart's width calculation
  // (container.clientWidth inside charts.js's renderChart) silently reads
  // 0 — a detached element has no layout box yet — and falls back to a
  // fixed default width that doesn't match this widget's real,
  // CSS-grid-computed size. That mismatch is exactly why charts used to
  // render letterboxed (a fixed-aspect-ratio viewBox centered inside a
  // much wider box, wasting ~30% of the width as blank margin either
  // side) instead of filling the widget.
  return el;
}

function renderWidgetContent(id, body) {
  const def = WIDGET_BY_ID[id];
  if (def.kind === "kpi") {
    renderKpiWidget(id, body);
  } else if (def.render) {
    def.render(body);
  }
}

function renderAllWidgets() {
  const grid = document.getElementById("dashboard-grid");
  grid.innerHTML = "";
  layout.order
    .filter((id) => !layout.hidden.includes(id))
    .forEach((id) => {
      const el = buildWidgetEl(id);
      grid.appendChild(el);
      renderWidgetContent(id, el.querySelector(".widget-body"));
    });
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

// ---------- multi-select filter control ----------
// A dependency-free "N of M selected" dropdown: click opens a checkbox
// panel (with a search box once there are enough options to need one),
// closes on an outside click or Escape. Selection only lives in memory
// until the caller reads getSelected() — nothing is applied until
// "Anwenden" is pressed, matching every other filter field's existing
// pick-then-apply behavior instead of firing a fetch per checkbox click.
function createMultiSelect(container, { options, selected, allLabel, searchThreshold = 8 }) {
  const selectedIds = new Set((selected || []).map(String));
  let query = "";

  function summaryText() {
    if (selectedIds.size === 0) return allLabel;
    if (selectedIds.size === 1) {
      const opt = options.find((o) => String(o.id) === [...selectedIds][0]);
      return opt ? opt.name : allLabel;
    }
    return `${selectedIds.size} ausgewählt`;
  }

  function updateTrigger() {
    const trigger = container.querySelector(".multiselect-trigger");
    if (!trigger) return;
    trigger.querySelector("span").textContent = summaryText();
    trigger.classList.toggle("active", selectedIds.size > 0);
  }

  function filteredOptions() {
    if (!query) return options;
    const q = query.toLowerCase();
    return options.filter((o) => o.name.toLowerCase().includes(q));
  }

  function renderPanel() {
    const panel = container.querySelector(".multiselect-panel");
    if (!panel) return;
    panel.querySelector(".multiselect-list").innerHTML = filteredOptions().map((o) => `
      <label class="multiselect-option">
        <input type="checkbox" value="${o.id}" ${selectedIds.has(String(o.id)) ? "checked" : ""}>
        <span>${escapeHtml(o.name)}</span>
      </label>
    `).join("") || '<p class="multiselect-empty">Keine Treffer.</p>';
    panel.querySelectorAll('input[type="checkbox"]').forEach((cb) => {
      cb.addEventListener("change", () => {
        if (cb.checked) selectedIds.add(cb.value); else selectedIds.delete(cb.value);
        updateTrigger();
      });
    });
  }

  function open() {
    document.querySelectorAll(".multiselect.open").forEach((el) => { if (el !== container) closeEl(el); });
    container.classList.add("open");
    container.querySelector(".multiselect-panel").classList.remove("hidden");
    const search = container.querySelector(".multiselect-search");
    if (search) search.focus();
  }
  function closeEl(el) {
    el.classList.remove("open");
    const panel = el.querySelector(".multiselect-panel");
    if (panel) panel.classList.add("hidden");
  }

  container.innerHTML = `
    <button type="button" class="multiselect-trigger ${selectedIds.size > 0 ? "active" : ""}">
      <span>${escapeHtml(summaryText())}</span>
      <span class="multiselect-caret">▾</span>
    </button>
    <div class="multiselect-panel hidden">
      ${options.length > searchThreshold ? '<input type="text" class="multiselect-search" placeholder="Suchen…">' : ""}
      <div class="multiselect-actions">
        <button type="button" data-action="select-all">Alle</button>
        <button type="button" data-action="select-none">Keine</button>
      </div>
      <div class="multiselect-list"></div>
    </div>
  `;

  container.querySelector(".multiselect-trigger").addEventListener("click", (event) => {
    event.stopPropagation();
    if (container.classList.contains("open")) closeEl(container); else open();
  });
  container.querySelector(".multiselect-panel").addEventListener("click", (event) => event.stopPropagation());
  const search = container.querySelector(".multiselect-search");
  if (search) {
    search.addEventListener("input", (event) => { query = event.target.value; renderPanel(); });
  }
  container.querySelector('[data-action="select-all"]').addEventListener("click", () => {
    filteredOptions().forEach((o) => selectedIds.add(String(o.id)));
    renderPanel();
    updateTrigger();
  });
  container.querySelector('[data-action="select-none"]').addEventListener("click", () => {
    selectedIds.clear();
    renderPanel();
    updateTrigger();
  });

  renderPanel();

  return {
    getSelected: () => [...selectedIds],
    setSelected: (ids) => {
      selectedIds.clear();
      (ids || []).forEach((id) => selectedIds.add(String(id)));
      updateTrigger();
      renderPanel();
    },
  };
}

document.addEventListener("click", (event) => {
  document.querySelectorAll(".multiselect.open").forEach((el) => {
    if (!el.contains(event.target)) {
      el.classList.remove("open");
      const panel = el.querySelector(".multiselect-panel");
      if (panel) panel.classList.add("hidden");
    }
  });
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  document.querySelectorAll(".multiselect.open").forEach((el) => {
    el.classList.remove("open");
    const panel = el.querySelector(".multiselect-panel");
    if (panel) panel.classList.add("hidden");
  });
});

// ---------- toolbar / filters ----------

let categoryMultiSelect = null;
let accountMultiSelect = null;
let tagMultiSelect = null;

function initMultiSelects() {
  categoryMultiSelect = createMultiSelect(document.getElementById("filter-category"), {
    options: state.categories.map((c) => ({ id: c.id, name: c.name })),
    selected: state.categoryIds,
    allLabel: "Alle Kategorien",
  });
  accountMultiSelect = createMultiSelect(document.getElementById("filter-account"), {
    options: state.accounts.map((a) => ({ id: a.id, name: a.name })),
    selected: state.accountIds,
    allLabel: "Alle Konten",
  });
  tagMultiSelect = createMultiSelect(document.getElementById("filter-tag"), {
    options: state.tags.map((t) => ({ id: t.id, name: t.name })),
    selected: state.tagIds,
    allLabel: "Alle Tags",
  });
}

function updateCustomFieldsVisibility() {
  const isCustom = state.granularity === "custom";
  document.getElementById("custom-start-field").classList.toggle("hidden", !isCustom);
  document.getElementById("custom-end-field").classList.toggle("hidden", !isCustom);
}

// ---------- advanced-filter panel collapse ----------

const FILTERS_EXPANDED_KEY = "budget_dashboard_filters_expanded_v1";

function setFiltersExpanded(expanded) {
  document.getElementById("advanced-filters").classList.toggle("hidden", !expanded);
  document.getElementById("filter-toggle-btn").classList.toggle("active", expanded);
  document.getElementById("filter-toggle-caret").textContent = expanded ? "▴" : "▾";
  localStorage.setItem(FILTERS_EXPANDED_KEY, expanded ? "1" : "0");
}

document.getElementById("filter-toggle-btn").addEventListener("click", () => {
  const isExpanded = !document.getElementById("advanced-filters").classList.contains("hidden");
  setFiltersExpanded(!isExpanded);
});

// ---------- active-filter badge + removable chips ----------

// Mirrors renderFilterChips()'s grouping 1:1 (min/max amount is one chip,
// not two) so the badge number always matches how many chips are shown.
function activeFilterCount() {
  return state.categoryIds.length + state.accountIds.length + state.tagIds.length
    + (state.type ? 1 : 0) + (state.minAmount || state.maxAmount ? 1 : 0) + (state.search ? 1 : 0);
}

function updateFilterBadge() {
  const count = activeFilterCount();
  const badge = document.getElementById("filter-toggle-badge");
  badge.textContent = count;
  badge.classList.toggle("hidden", count === 0);
  document.getElementById("reset-filters-btn").classList.toggle("hidden", count === 0);
}

function namesForIds(ids, list) {
  return ids
    .map((id) => { const item = list.find((x) => String(x.id) === String(id)); return item ? item.name : null; })
    .filter(Boolean);
}

// Lets the user see (and undo) exactly what's narrowing the view without
// opening the advanced-filter panel again — click a chip's × to drop just
// that one filter and reload immediately.
function renderFilterChips() {
  const wrap = document.getElementById("active-filter-chips");
  const chips = [];
  if (state.categoryIds.length) chips.push({ key: "category", label: `Kategorie: ${namesForIds(state.categoryIds, state.categories).join(", ")}` });
  if (state.accountIds.length) chips.push({ key: "account", label: `Konto: ${namesForIds(state.accountIds, state.accounts).join(", ")}` });
  if (state.tagIds.length) chips.push({ key: "tag", label: `Tag: ${namesForIds(state.tagIds, state.tags).join(", ")}` });
  if (state.type) chips.push({ key: "type", label: state.type === "income" ? "Nur Einnahmen" : "Nur Ausgaben" });
  if (state.minAmount || state.maxAmount) {
    chips.push({ key: "amount", label: `Betrag: ${state.minAmount || "−∞"} bis ${state.maxAmount || "∞"}` });
  }
  if (state.search) chips.push({ key: "search", label: `Suche: "${state.search}"` });

  if (chips.length === 0) {
    wrap.innerHTML = "";
    wrap.classList.add("hidden");
    return;
  }
  wrap.classList.remove("hidden");
  wrap.innerHTML = chips.map((c) => `
    <span class="filter-chip">${escapeHtml(c.label)}<button type="button" class="filter-chip-remove" data-key="${c.key}" title="Entfernen">&times;</button></span>
  `).join("");
  wrap.querySelectorAll(".filter-chip-remove").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const key = btn.dataset.key;
      if (key === "category") { state.categoryIds = []; categoryMultiSelect.setSelected([]); }
      if (key === "account") { state.accountIds = []; accountMultiSelect.setSelected([]); }
      if (key === "tag") { state.tagIds = []; tagMultiSelect.setSelected([]); }
      if (key === "type") { state.type = ""; document.getElementById("filter-type").value = ""; }
      if (key === "amount") {
        state.minAmount = ""; state.maxAmount = "";
        document.getElementById("filter-min-amount").value = "";
        document.getElementById("filter-max-amount").value = "";
      }
      if (key === "search") { state.search = ""; document.getElementById("filter-search").value = ""; }
      saveFilterState();
      renderFilterChips();
      updateFilterBadge();
      await loadData();
    });
  });
}

// Each page here is a full navigation (index.html/import.html/budget.html/
// regeln.html), not an SPA route, so switching tabs and coming back always
// re-runs this file from scratch — nothing survives in memory. Persisting
// to localStorage (the same mechanism `layout`/LAYOUT_KEY above already
// uses for widget positions) is what makes "the filters stay as I left
// them" possible across that reload.
const FILTER_STATE_KEY = "budget_dashboard_filters_v1";

function saveFilterState() {
  localStorage.setItem(FILTER_STATE_KEY, JSON.stringify({
    granularity: state.granularity,
    compare: state.compare,
    accountIds: state.accountIds,
    categoryIds: state.categoryIds,
    tagIds: state.tagIds,
    type: state.type,
    minAmount: state.minAmount,
    maxAmount: state.maxAmount,
    search: state.search,
    start: state.start,
    end: state.end,
  }));
}

function loadFilterState() {
  try {
    return JSON.parse(localStorage.getItem(FILTER_STATE_KEY) || "null");
  } catch (err) {
    return null;
  }
}

// Applies a saved filter state to both `state` and the toolbar controls —
// called once on init(), before the first loadData(), so the very first
// fetch already reflects what was last applied rather than the defaults.
function restoreFilterState() {
  const saved = loadFilterState();
  if (!saved) return;

  state.granularity = saved.granularity || state.granularity;
  state.compare = !!saved.compare;

  const knownAccountIds = new Set(state.accounts.map((a) => String(a.id)));
  const knownCategoryIds = new Set(state.categories.map((c) => String(c.id)));
  const knownTagIds = new Set(state.tags.map((t) => String(t.id)));

  // v1 of this key stored a single id per field (accountId/categoryId/
  // tagId) from before these became multi-select; falling back to that
  // singular key wrapped in an array keeps an already-active filter from
  // silently vanishing for anyone whose browser still has the old shape
  // saved. Either way, stale ids from a since-deleted account/category/tag
  // are dropped rather than silently filtering on nothing.
  const rawAccountIds = saved.accountIds || (saved.accountId ? [saved.accountId] : []);
  const rawCategoryIds = saved.categoryIds || (saved.categoryId ? [saved.categoryId] : []);
  const rawTagIds = saved.tagIds || (saved.tagId ? [saved.tagId] : []);

  state.accountIds = rawAccountIds.map(String).filter((id) => knownAccountIds.has(id));
  state.categoryIds = rawCategoryIds.map(String).filter((id) => knownCategoryIds.has(id));
  state.tagIds = rawTagIds.map(String).filter((id) => knownTagIds.has(id));

  state.type = saved.type || "";
  state.minAmount = saved.minAmount || "";
  state.maxAmount = saved.maxAmount || "";
  state.search = saved.search || "";

  document.querySelectorAll("#granularity-picker .segmented-option").forEach((b) => {
    b.classList.toggle("active", b.dataset.granularity === state.granularity);
  });
  document.getElementById("compare-toggle").checked = state.compare;
  categoryMultiSelect.setSelected(state.categoryIds);
  accountMultiSelect.setSelected(state.accountIds);
  tagMultiSelect.setSelected(state.tagIds);
  document.getElementById("filter-type").value = state.type;
  document.getElementById("filter-min-amount").value = state.minAmount;
  document.getElementById("filter-max-amount").value = state.maxAmount;
  document.getElementById("filter-search").value = state.search;
  updateCustomFieldsVisibility();
  renderFilterChips();
  updateFilterBadge();

  if (state.granularity === "custom" && saved.start && saved.end) {
    state.start = saved.start;
    state.end = saved.end;
    document.getElementById("filter-start").value = saved.start;
    document.getElementById("filter-end").value = saved.end;
  }
}

document.getElementById("granularity-picker").addEventListener("click", async (event) => {
  const btn = event.target.closest(".segmented-option");
  if (!btn) return;
  document.querySelectorAll("#granularity-picker .segmented-option").forEach((b) => b.classList.toggle("active", b === btn));
  state.granularity = btn.dataset.granularity;
  updateCustomFieldsVisibility();
  saveFilterState();
  if (state.granularity !== "custom") {
    await loadData();
  }
});

document.getElementById("compare-toggle").addEventListener("change", async (event) => {
  state.compare = event.target.checked;
  saveFilterState();
  await loadData();
});

document.getElementById("apply-filters-btn").addEventListener("click", async () => {
  state.accountIds = accountMultiSelect.getSelected();
  state.categoryIds = categoryMultiSelect.getSelected();
  state.tagIds = tagMultiSelect.getSelected();
  state.type = document.getElementById("filter-type").value;
  state.minAmount = document.getElementById("filter-min-amount").value;
  state.maxAmount = document.getElementById("filter-max-amount").value;
  state.search = document.getElementById("filter-search").value;
  if (state.granularity === "custom") {
    state.start = document.getElementById("filter-start").value || state.start;
    state.end = document.getElementById("filter-end").value || state.end;
  }
  saveFilterState();
  renderFilterChips();
  updateFilterBadge();
  await loadData();
});

document.getElementById("reset-filters-btn").addEventListener("click", async () => {
  state.accountIds = [];
  state.categoryIds = [];
  state.tagIds = [];
  state.type = "";
  state.minAmount = "";
  state.maxAmount = "";
  state.search = "";
  categoryMultiSelect.setSelected([]);
  accountMultiSelect.setSelected([]);
  tagMultiSelect.setSelected([]);
  document.getElementById("filter-type").value = "";
  document.getElementById("filter-min-amount").value = "";
  document.getElementById("filter-max-amount").value = "";
  document.getElementById("filter-search").value = "";
  saveFilterState();
  renderFilterChips();
  updateFilterBadge();
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
}

async function loadBudgets() {
  state.budgets = await fetch("/api/budgets").then((r) => r.json());
}

// Deliberately NOT scoped to summaryParams(state.start, state.end) — /api/
// recurring uses its own fixed lookback window server-side regardless of
// the toolbar's period picker (see server/app.py's recurring() route), so
// only the non-date filters (account/category/tag/...) are passed through.
async function loadRecurring() {
  const params = applyCommonFilterParams(new URLSearchParams());
  state.recurring = await fetch(`/api/recurring?${params.toString()}`).then((r) => r.json());
}

// Applied everywhere a fetch is filtered — so every widget on the page
// (KPIs, every chart, the budget-vs-actual widgets) reflects the same
// filter set.
function applyCommonFilterParams(params) {
  if (state.accountIds.length) params.set("account_id", state.accountIds.join(","));
  if (state.categoryIds.length) params.set("category_id", state.categoryIds.join(","));
  if (state.tagIds.length) params.set("tag_id", state.tagIds.join(","));
  if (state.type) params.set("type", state.type);
  if (state.minAmount) params.set("min_amount", state.minAmount);
  if (state.maxAmount) params.set("max_amount", state.maxAmount);
  if (state.search) params.set("q", state.search);
  return params;
}

function summaryParams(start, end) {
  const params = new URLSearchParams({ start, end, granularity: state.granularity === "custom" ? "month" : state.granularity });
  return applyCommonFilterParams(params);
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

  await Promise.all([loadBudgets(), loadRecurring()]);
  renderAllWidgets();
}

(async function init() {
  updateCustomFieldsVisibility();
  await loadFilterOptions();
  initMultiSelects();
  setFiltersExpanded(localStorage.getItem(FILTERS_EXPANDED_KEY) !== "0");
  restoreFilterState();
  renderFilterChips();
  updateFilterBadge();
  await loadData();
})();
