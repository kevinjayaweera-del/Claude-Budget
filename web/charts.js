// charts.js — hand-rolled SVG chart engine for the Budget dashboard.
// No external dependencies, consistent with the rest of this app.
//
// Public API:
//   renderChart(container, config) -> { destroy(), setSeriesVisible(key, visible) }
//
// config:
//   type: "line" | "bar" | "area" | "pie" | "donut" | "combo"
//   data: [{ x: "2026-06", seriesKey: 12345, ... }, ...]   (amounts in cents)
//   series: [{ key, label, color, type? }]  (type only meaningful for "combo")
//   width, height: numbers (px); width defaults to container width
//   formatValue(cents) -> string, formatX(x) -> string
//   stacked: bool (bar/area only)
//   showLegend: bool (default true)
//   zoomable: bool (default false) — adds a brush/range selector below the chart
//   onPointClick(seriesKey, index, point): called when a bar/point/pie segment is clicked
//   forecastFromIndex: number|null — data at/after this index is rendered dashed/muted
//     (used for the cashflow forecast continuation)

const CHART_PADDING = { top: 12, right: 16, bottom: 28, left: 52 };
const BRUSH_HEIGHT = 36;

function _escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function _defaultFormatValue(cents) {
  return (cents / 100).toLocaleString("de-CH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// ---------- shared tooltip singleton ----------

let _tooltipEl = null;
function _getTooltip() {
  if (!_tooltipEl) {
    _tooltipEl = document.createElement("div");
    _tooltipEl.className = "chart-tooltip hidden";
    document.body.appendChild(_tooltipEl);
  }
  return _tooltipEl;
}
function _showTooltip(html, clientX, clientY) {
  const el = _getTooltip();
  el.innerHTML = html;
  el.classList.remove("hidden");
  const rect = el.getBoundingClientRect();
  el.style.left = `${Math.min(clientX + 12, window.innerWidth - rect.width - 8)}px`;
  el.style.top = `${Math.max(clientY - rect.height - 12, 8)}px`;
}
function _hideTooltip() {
  if (_tooltipEl) _tooltipEl.classList.add("hidden");
}

// ---------- scales ----------

function _scaleLinear(domain, range) {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0 || 1;
  return (v) => r0 + ((v - d0) / span) * (r1 - r0);
}

function _scaleBand(count, range, paddingRatio = 0.3) {
  const [r0, r1] = range;
  const step = count > 0 ? (r1 - r0) / count : 0;
  const bandwidth = step * (1 - paddingRatio);
  return {
    step,
    bandwidth,
    center: (i) => r0 + step * i + step / 2,
    x0: (i) => r0 + step * i + (step - bandwidth) / 2,
  };
}

function _niceMax(max) {
  if (max <= 0) return 1;
  const magnitude = Math.pow(10, Math.floor(Math.log10(max)));
  const normalized = max / magnitude;
  const niced = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return niced * magnitude;
}

// ---------- legend ----------

function _renderLegend(container, series, visibility, onToggle) {
  const legend = document.createElement("div");
  legend.className = "chart-legend";
  legend.innerHTML = series.map((s) => `
    <button type="button" class="chart-legend-item ${visibility[s.key] === false ? "off" : ""}" data-series-key="${_escapeHtml(s.key)}">
      <span class="chart-legend-dot" style="background: ${visibility[s.key] === false ? "transparent" : s.color}; border-color: ${s.color}"></span>
      ${_escapeHtml(s.label)}
    </button>
  `).join("");
  legend.addEventListener("click", (event) => {
    const btn = event.target.closest(".chart-legend-item");
    if (!btn) return;
    onToggle(btn.dataset.seriesKey);
  });
  container.appendChild(legend);
  return legend;
}

// ---------- main entry point ----------

function renderChart(container, config) {
  const {
    type,
    data,
    series,
    width = container.clientWidth || 600,
    height = 260,
    formatValue = _defaultFormatValue,
    formatX = (x) => String(x),
    stacked = false,
    showLegend = true,
    zoomable = false,
    onPointClick = null,
    forecastFromIndex = null,
  } = config;

  const state = {
    visibility: {},
    zoomRange: [0, data.length],
  };

  container.innerHTML = "";
  container.classList.add("chart-root");

  const chartHost = document.createElement("div");
  container.appendChild(chartHost);

  function activeSeries() {
    return series.filter((s) => state.visibility[s.key] !== false);
  }

  function draw() {
    chartHost.innerHTML = "";
    const [lo, hi] = state.zoomRange;
    const visibleData = data.slice(lo, hi);

    if (type === "pie" || type === "donut") {
      chartHost.appendChild(_drawPie(visibleData, series, { width, height, type, formatValue, onPointClick }));
    } else {
      chartHost.appendChild(_drawCartesian(visibleData, activeSeries(), {
        type, width, height, formatValue, formatX, stacked, onPointClick,
        forecastFromIndex: forecastFromIndex === null ? null : forecastFromIndex - lo,
      }));
      if (zoomable && data.length > 4) {
        chartHost.appendChild(_drawBrush(data, series, { width, formatX }, (newRange) => {
          state.zoomRange = newRange;
          draw();
        }));
      }
    }

    if (showLegend && series.length > 1) {
      _renderLegend(chartHost, series, state.visibility, (key) => {
        state.visibility[key] = state.visibility[key] === false ? true : false;
        draw();
      });
    }
  }

  draw();

  return {
    destroy() {
      container.innerHTML = "";
    },
    setSeriesVisible(key, visible) {
      state.visibility[key] = visible;
      draw();
    },
  };
}

// ---------- cartesian (line/bar/area/combo) ----------

function _drawCartesian(data, series, opts) {
  const { type, width, height, formatValue, formatX, stacked, onPointClick, forecastFromIndex } = opts;
  const innerW = width - CHART_PADDING.left - CHART_PADDING.right;
  const innerH = height - CHART_PADDING.top - CHART_PADDING.bottom;

  const values = [];
  data.forEach((d) => {
    if (stacked) {
      let pos = 0, neg = 0;
      series.forEach((s) => {
        const v = d[s.key] || 0;
        if (v >= 0) pos += v; else neg += v;
      });
      values.push(pos, neg);
    } else {
      series.forEach((s) => values.push(d[s.key] || 0));
    }
  });
  const maxVal = _niceMax(Math.max(...values, 0, 1));
  const minVal = Math.min(...values, 0) < 0 ? -_niceMax(Math.abs(Math.min(...values, 0))) : 0;

  const yScale = _scaleLinear([minVal, maxVal], [innerH, 0]);
  const band = _scaleBand(data.length, [0, innerW], type === "line" || type === "area" ? 0 : 0.35);
  const xForIndex = (i) => (data.length === 1 ? innerW / 2 : (type === "line" || type === "area" ? (i / Math.max(data.length - 1, 1)) * innerW : band.center(i)));

  const zeroY = yScale(0);
  const gridSteps = 4;
  const gridLines = Array.from({ length: gridSteps + 1 }, (_, i) => {
    const y = (innerH / gridSteps) * i;
    return `<line x1="0" y1="${y.toFixed(1)}" x2="${innerW}" y2="${y.toFixed(1)}" stroke="var(--line)" stroke-width="1" />`;
  }).join("");
  const yLabels = Array.from({ length: gridSteps + 1 }, (_, i) => {
    const v = maxVal - ((maxVal - minVal) / gridSteps) * i;
    const y = (innerH / gridSteps) * i;
    return `<text x="-8" y="${(y + 4).toFixed(1)}" text-anchor="end" class="chart-axis-label">${formatValue(v)}</text>`;
  }).join("");

  let seriesSvg = "";
  const stackedTops = new Array(data.length).fill(0);
  const stackedBottoms = new Array(data.length).fill(0);

  series.forEach((s, seriesIndex) => {
    const isForecastSeries = s.key === "forecast";
    const points = data.map((d, i) => [xForIndex(i), yScale(d[s.key] || 0)]);
    const seriesType = type === "combo" ? (s.type || "line") : type;

    if (seriesType === "bar") {
      const bars = data.map((d, i) => {
        const raw = d[s.key] || 0;
        let barY0, barY1;
        if (stacked) {
          if (raw >= 0) {
            barY0 = yScale(stackedTops[i] + raw);
            barY1 = yScale(stackedTops[i]);
            stackedTops[i] += raw;
          } else {
            barY0 = yScale(stackedBottoms[i]);
            barY1 = yScale(stackedBottoms[i] + raw);
            stackedBottoms[i] += raw;
          }
        } else {
          barY0 = yScale(Math.max(raw, 0));
          barY1 = yScale(Math.min(raw, 0));
        }
        const barW = stacked ? band.bandwidth : band.bandwidth / series.length;
        const barX = stacked ? band.x0(i) : band.x0(i) + barW * seriesIndex;
        const isFuture = forecastFromIndex !== null && i >= forecastFromIndex;
        return `<rect class="chart-mark" data-series-key="${_escapeHtml(s.key)}" data-index="${i}" `
          + `x="${barX.toFixed(1)}" y="${Math.min(barY0, barY1).toFixed(1)}" width="${Math.max(barW - 2, 1).toFixed(1)}" `
          + `height="${Math.max(Math.abs(barY1 - barY0), 1).toFixed(1)}" fill="${s.color}" `
          + `opacity="${isFuture ? 0.4 : 1}" rx="2"></rect>`;
      }).join("");
      seriesSvg += bars;
    } else if (seriesType === "area" || seriesType === "line") {
      const solidEnd = forecastFromIndex === null ? points.length : Math.min(forecastFromIndex + 1, points.length);
      const solidPoints = points.slice(0, solidEnd);
      const forecastPoints = points.slice(Math.max(solidEnd - 1, 0));

      const pathFor = (pts) => pts.map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
      if (seriesType === "area" && solidPoints.length > 0) {
        const areaPath = `${pathFor(solidPoints)} L ${solidPoints[solidPoints.length - 1][0].toFixed(1)} ${zeroY.toFixed(1)} L ${solidPoints[0][0].toFixed(1)} ${zeroY.toFixed(1)} Z`;
        seriesSvg += `<path d="${areaPath}" fill="${s.color}" opacity="0.12" stroke="none"></path>`;
      }
      if (solidPoints.length > 0) {
        seriesSvg += `<path d="${pathFor(solidPoints)}" fill="none" stroke="${s.color}" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"></path>`;
      }
      if (forecastPoints.length > 1) {
        seriesSvg += `<path d="${pathFor(forecastPoints)}" fill="none" stroke="${s.color}" stroke-width="2.5" stroke-dasharray="5 4" stroke-linejoin="round" stroke-linecap="round" opacity="0.65"></path>`;
      }
      seriesSvg += points.map(([x, y], i) => {
        const isFuture = forecastFromIndex !== null && i >= forecastFromIndex;
        return `<circle class="chart-mark" data-series-key="${_escapeHtml(s.key)}" data-index="${i}" `
          + `cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="4" fill="${isFuture ? "var(--surface)" : s.color}" `
          + `stroke="${s.color}" stroke-width="2"></circle>`;
      }).join("");
    }
  });

  const xLabels = data.map((d, i) => {
    if (data.length > 14 && i % Math.ceil(data.length / 10) !== 0) return "";
    return `<text x="${xForIndex(i).toFixed(1)}" y="${innerH + 20}" text-anchor="middle" class="chart-axis-label">${_escapeHtml(formatX(d.x))}</text>`;
  }).join("");

  const zeroLine = minVal < 0
    ? `<line x1="0" y1="${zeroY.toFixed(1)}" x2="${innerW}" y2="${zeroY.toFixed(1)}" stroke="var(--line-strong)" stroke-width="1" />`
    : "";

  const wrap = document.createElement("div");
  wrap.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" width="100%" height="${height}" class="chart-svg">
      <g transform="translate(${CHART_PADDING.left}, ${CHART_PADDING.top})">
        ${gridLines}
        ${yLabels}
        ${seriesSvg}
        ${zeroLine}
        ${xLabels}
        <rect class="chart-hit-layer" x="0" y="0" width="${innerW}" height="${innerH}" fill="transparent"></rect>
      </g>
    </svg>
  `;
  const svg = wrap.firstElementChild;

  svg.addEventListener("mousemove", (event) => {
    const mark = event.target.closest(".chart-mark");
    if (!mark) { _hideTooltip(); return; }
    const seriesKey = mark.dataset.seriesKey;
    const index = parseInt(mark.dataset.index, 10);
    const s = series.find((x) => x.key === seriesKey);
    const d = data[index];
    if (!s || !d) return;
    _showTooltip(
      `<div class="chart-tooltip-label">${_escapeHtml(formatX(d.x))}</div>` +
      `<div class="chart-tooltip-row"><span class="chart-tooltip-dot" style="background:${s.color}"></span>${_escapeHtml(s.label)}: <strong>${formatValue(d[s.key] || 0)}</strong></div>`,
      event.clientX, event.clientY
    );
  });
  svg.addEventListener("mouseleave", _hideTooltip);
  svg.addEventListener("click", (event) => {
    const mark = event.target.closest(".chart-mark");
    if (!mark || !onPointClick) return;
    const seriesKey = mark.dataset.seriesKey;
    const index = parseInt(mark.dataset.index, 10);
    onPointClick(seriesKey, index, data[index]);
  });

  return svg;
}

// ---------- pie / donut ----------

function _drawPie(data, series, opts) {
  const { width, height, type, formatValue, onPointClick } = opts;
  const valueKey = series[0] ? series[0].key : "value";
  const total = data.reduce((sum, d) => sum + Math.abs(d[valueKey] || 0), 0);

  const size = Math.min(width, height);
  const r = size / 2 - 8;
  const strokeWidth = type === "donut" ? r * 0.4 : r;
  const drawR = type === "donut" ? r - strokeWidth / 2 : r / 2;
  const cx = size / 2;
  const cy = size / 2;
  const circumference = 2 * Math.PI * drawR;

  let offset = 0;
  const segments = total === 0 ? "" : data.map((d, i) => {
    const value = Math.abs(d[valueKey] || 0);
    const len = (value / total) * circumference;
    const color = d.color || `var(--series-${(i % 6) + 1})`;
    const seg = `<circle class="chart-mark" data-index="${i}" cx="${cx}" cy="${cy}" r="${drawR}" fill="none" stroke="${color}" `
      + `stroke-width="${strokeWidth}" stroke-dasharray="${len.toFixed(2)} ${(circumference - len).toFixed(2)}" `
      + `stroke-dashoffset="${(-offset).toFixed(2)}"></circle>`;
    offset += len;
    return seg;
  }).join("");

  const wrap = document.createElement("div");
  wrap.className = "chart-pie-wrap";
  wrap.innerHTML = `
    <svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" class="chart-svg">
      <g transform="rotate(-90 ${cx} ${cy})">${segments}</g>
      ${type === "donut" ? `<text x="${cx}" y="${cy - 4}" text-anchor="middle" class="chart-donut-total">${formatValue(total)}</text>` : ""}
    </svg>
  `;
  const svg = wrap.querySelector("svg");

  svg.addEventListener("mousemove", (event) => {
    const mark = event.target.closest(".chart-mark");
    if (!mark) { _hideTooltip(); return; }
    const index = parseInt(mark.dataset.index, 10);
    const d = data[index];
    if (!d) return;
    const value = Math.abs(d[valueKey] || 0);
    const pct = total > 0 ? Math.round((value / total) * 100) : 0;
    _showTooltip(
      `<div class="chart-tooltip-label">${_escapeHtml(d.x)}</div>` +
      `<div class="chart-tooltip-row"><strong>${formatValue(value)}</strong> (${pct}%)</div>`,
      event.clientX, event.clientY
    );
  });
  svg.addEventListener("mouseleave", _hideTooltip);
  svg.addEventListener("click", (event) => {
    const mark = event.target.closest(".chart-mark");
    if (!mark || !onPointClick) return;
    const index = parseInt(mark.dataset.index, 10);
    onPointClick(valueKey, index, data[index]);
  });

  return wrap;
}

// ---------- brush / zoom selector ----------

function _drawBrush(data, series, opts, onChange) {
  const { width, formatX } = opts;
  const innerW = width - CHART_PADDING.left - CHART_PADDING.right;
  const key = series[0] ? series[0].key : null;
  const values = key ? data.map((d) => d[key] || 0) : data.map(() => 0);
  const maxAbs = Math.max(...values.map(Math.abs), 1);
  const yScale = _scaleLinear([-maxAbs, maxAbs], [BRUSH_HEIGHT - 4, 4]);

  const points = data.map((d, i) => [
    data.length === 1 ? innerW / 2 : (i / Math.max(data.length - 1, 1)) * innerW,
    yScale(values[i]),
  ]);
  const path = points.map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");

  const wrap = document.createElement("div");
  wrap.className = "chart-brush";
  wrap.innerHTML = `
    <svg viewBox="0 0 ${width} ${BRUSH_HEIGHT}" width="100%" height="${BRUSH_HEIGHT}" class="chart-brush-svg">
      <g transform="translate(${CHART_PADDING.left}, 0)">
        <path d="${path}" fill="none" stroke="var(--ink-faint)" stroke-width="1.5"></path>
        <rect class="chart-brush-selection" x="0" y="0" width="${innerW}" height="${BRUSH_HEIGHT}" fill="var(--accent)" opacity="0.12" stroke="var(--accent)" stroke-width="1"></rect>
        <rect class="chart-brush-handle" data-handle="left" x="-4" y="0" width="8" height="${BRUSH_HEIGHT}" fill="var(--accent)" cursor="ew-resize"></rect>
        <rect class="chart-brush-handle" data-handle="right" x="${innerW - 4}" y="0" width="8" height="${BRUSH_HEIGHT}" fill="var(--accent)" cursor="ew-resize"></rect>
      </g>
    </svg>
    <p class="chart-brush-hint">Ziehen zum Zoomen · <button type="button" class="chart-brush-reset">Zurücksetzen</button></p>
  `;

  const svg = wrap.querySelector("svg");
  const selection = wrap.querySelector(".chart-brush-selection");
  const g = svg.querySelector("g");
  let selLeft = 0, selRight = innerW;
  let dragging = null;

  function updateSelection() {
    selection.setAttribute("x", selLeft);
    selection.setAttribute("width", Math.max(selRight - selLeft, 4));
    g.querySelector('[data-handle="left"]').setAttribute("x", selLeft - 4);
    g.querySelector('[data-handle="right"]').setAttribute("x", selRight - 4);
  }

  function indexForX(px) {
    return Math.round((px / innerW) * (data.length - 1));
  }

  svg.addEventListener("mousedown", (event) => {
    const handle = event.target.closest(".chart-brush-handle");
    dragging = handle ? handle.dataset.handle : "new";
    if (dragging === "new") {
      const rect = svg.getBoundingClientRect();
      const scale = width / rect.width;
      selLeft = selRight = (event.clientX - rect.left) * scale - CHART_PADDING.left;
    }
  });
  window.addEventListener("mousemove", (event) => {
    if (!dragging) return;
    const rect = svg.getBoundingClientRect();
    const scale = width / rect.width;
    const px = Math.max(0, Math.min(innerW, (event.clientX - rect.left) * scale - CHART_PADDING.left));
    if (dragging === "left") selLeft = Math.min(px, selRight - 8);
    else if (dragging === "right") selRight = Math.max(px, selLeft + 8);
    else selRight = px;
    updateSelection();
  });
  window.addEventListener("mouseup", () => {
    if (!dragging) return;
    dragging = null;
    const from = Math.max(0, indexForX(selLeft));
    const to = Math.min(data.length, indexForX(selRight) + 1);
    if (to - from >= 2) onChange([from, to]);
  });
  wrap.querySelector(".chart-brush-reset").addEventListener("click", () => {
    selLeft = 0; selRight = innerW; updateSelection();
    onChange([0, data.length]);
  });

  return wrap;
}
