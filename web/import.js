let categories = [];
let autoAcceptedPendingIds = [];

// Rows the categorizer is this confident about don't need a manual look.
// They're still kept in pending_transactions (not silently moved
// server-side) so nothing here changes what "Import bestätigen" ultimately
// does or how learning treats it; they're just not rendered in the review
// table, and ridden along when the user confirms the batch. Loaded from
// /api/settings (Einstellungen → Automatische Kategorisierung) — 0.75 is
// just the fallback before that fetch resolves.
let AUTO_ACCEPT_THRESHOLD = 0.75;

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function loadAppSettings() {
  const settings = await fetch("/api/settings").then((r) => r.json());
  AUTO_ACCEPT_THRESHOLD = settings.confidence_threshold;
}

async function loadCategories() {
  const res = await fetch("/api/categories");
  categories = await res.json();
}

function updateEmptyState(hasPending) {
  document.getElementById("import-empty-state").classList.toggle("hidden", hasPending);
}

function formatMoney(cents) {
  return (cents / 100).toLocaleString("de-CH", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function updateImportKpis(total, autoAssigned, needsReview) {
  const kpis = document.getElementById("import-kpis");
  kpis.classList.toggle("hidden", total === 0);
  document.getElementById("kpi-total-imported").textContent = total;
  document.getElementById("kpi-auto-assigned").textContent = autoAssigned;
  document.getElementById("kpi-needs-review").textContent = needsReview;
}

// One row per file ever scanned (GET /api/imported-files), confirmed or
// still pending — so the count/income/expense/net totals stay available to
// eyeball against the "Total Belastungen"/"Total Gutschriften" a bank or
// card statement prints on its own last page, even after "Import
// bestätigen" clears the pending queue. Unlike grouping pending rows by
// their raw source string, this persists for the whole import history.
async function loadFileTotals() {
  const files = await fetch("/api/imported-files").then((r) => r.json());
  const section = document.getElementById("file-totals-section");
  const tbody = document.querySelector("#file-totals-table tbody");
  if (files.length === 0) {
    section.classList.add("hidden");
    tbody.innerHTML = "";
    return;
  }
  section.classList.remove("hidden");

  tbody.innerHTML = files.map((f) => `
    <tr>
      <td class="desc">${escapeHtml(f.filename)}</td>
      <td class="num tabular">${f.transaction_count}</td>
      <td class="num tabular debit">${formatMoney(f.expense_cents)}</td>
      <td class="num tabular credit">${formatMoney(f.income_cents)}</td>
      <td class="num tabular ${f.net_cents >= 0 ? "credit" : "debit"}">${formatMoney(f.net_cents)}</td>
    </tr>
  `).join("");
}

async function loadPending() {
  const res = await fetch("/api/pending");
  const rows = await res.json();
  const section = document.getElementById("pending-section");
  const ledger = document.getElementById("pending-ledger");
  const tbody = document.querySelector("#pending-table tbody");
  tbody.innerHTML = "";
  autoAcceptedPendingIds = [];

  updateEmptyState(rows.length > 0);
  await loadFileTotals();
  if (rows.length === 0) {
    updateImportKpis(0, 0, 0);
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");

  const reviewRows = [];
  rows.forEach((row) => {
    const confidence = row.category_confidence;
    const isConfident = confidence !== null && confidence !== undefined && confidence >= AUTO_ACCEPT_THRESHOLD;
    if (isConfident) {
      autoAcceptedPendingIds.push(row.id);
      return;
    }
    reviewRows.push(row);
  });

  // These three numbers are the single source of truth for "how much is
  // there to do" — the table below only ever shows reviewRows, so the KPI
  // and the table can never again disagree about what's left to check.
  updateImportKpis(rows.length, autoAcceptedPendingIds.length, reviewRows.length);

  ledger.classList.toggle("hidden", reviewRows.length === 0);

  reviewRows.forEach((row) => {
    const tr = document.createElement("tr");
    tr.dataset.id = row.id;
    tr.dataset.currency = row.currency;
    // Hidden categories (e.g. "Versteckt") are excluded from this picker —
    // they're meant to be assigned automatically by a matching rule, not
    // picked casually during review; doing so here made the row vanish
    // from every default view (ledger, dashboard) with no obvious reason
    // why. The row's own CURRENT category stays selectable even if hidden
    // (shouldn't normally happen — import_service.py routes hidden-rule
    // matches straight into transactions, bypassing pending review
    // entirely — but this keeps the dropdown honest if it ever does).
    const categoryOptions = categories
      .filter((c) => !c.is_hidden || c.id === row.category_id)
      .map((c) => `<option value="${c.id}" ${c.id === row.category_id ? "selected" : ""}>${escapeHtml(c.name)}</option>`)
      .join("");
    const currencyTag = row.currency && row.currency !== "CHF"
      ? `<span class="currency-tag">${escapeHtml(row.currency)}</span>`
      : "";
    const confidence = row.category_confidence;
    let confidenceBadge = "";
    if (confidence !== null && confidence !== undefined) {
      const level = confidence >= AUTO_ACCEPT_THRESHOLD ? "high" : "low";
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
  // Deliberately says "gefunden", not "importiert" or "zur Prüfung":
  // "importiert" would claim these rows are already committed to the
  // ledger, when at this point they've only been parsed and staged into
  // pending_transactions — "Import bestätigen" (see the confirm-status
  // message further down, which correctly says "importiert" because that
  // IS the step that commits them) hasn't happened yet. "zur Prüfung"
  // would overclaim in the other direction — newPending is every row the
  // scan just created, most of which loadPending() (called right after
  // this) will silently auto-accept without a manual look.
  const status = document.getElementById("scan-status");
  if (newPending === 0 && duplicatesSkipped === 0) {
    status.textContent = "Keine neuen Dateien gefunden.";
  } else if (newPending === 0 && duplicatesSkipped > 0) {
    status.textContent = `Alle ${duplicatesSkipped} gefundenen Buchungen sind bereits vorhanden — keine neuen Buchungen.`;
  } else if (duplicatesSkipped > 0) {
    status.textContent = `${duplicatesSkipped} Dopplungen übersprungen, ${newPending} neue Buchungen gefunden.`;
  } else {
    status.textContent = `${newPending} neue Buchungen gefunden.`;
  }
  status.classList.remove("hidden");
}

document.getElementById("scan-btn").addEventListener("click", async (event) => {
  // Guards against a double-click (or an impatient second click before the
  // first request chain resolves) firing this whole multi-request flow a
  // second time in parallel, which could duplicate-scan or produce a
  // confusing second confirm() dialog on top of the first.
  const btn = event.currentTarget;
  if (btn.disabled) return;
  btn.disabled = true;
  try {
    // Fail-safe against duplicate imports: preview what a scan would do
    // (dry_run=true writes nothing) so we can warn before anything lands in
    // the pending queue. Only worth asking when there's an actual choice —
    // some new bookings AND some duplicates — otherwise just proceed (a
    // scan that's either all-new or all-duplicate has nothing to decide).
    const previewRes = await fetch("/api/scan?dry_run=true", { method: "POST" });
    if (!previewRes.ok) {
      alert("Fehler beim Scannen — bitte erneut versuchen.");
      return;
    }
    const preview = await previewRes.json();

    if (preview.duplicates_skipped > 0 && preview.new_pending > 0) {
      const proceed = confirm(
        `${preview.duplicates_skipped} Dopplung(en) gefunden, ${preview.new_pending} neue Buchung(en). ` +
        "Import fortsetzen? Nur die neuen Buchungen werden importiert, Duplikate werden übersprungen."
      );
      if (!proceed) {
        const status = document.getElementById("scan-status");
        status.textContent = "Import abgebrochen — es wurde nichts importiert.";
        status.classList.remove("hidden");
        return;
      }
    }

    const res = await fetch("/api/scan", { method: "POST" });
    if (!res.ok) {
      alert("Fehler beim Scannen — bitte erneut versuchen.");
      return;
    }
    const result = await res.json();
    showScanStatus(result.new_pending, result.duplicates_skipped);
    await loadPending();
  } finally {
    btn.disabled = false;
  }
});

document.getElementById("confirm-btn").addEventListener("click", async (event) => {
  const btn = event.currentTarget;
  if (btn.disabled) return;
  btn.disabled = true;
  try {
  const rows = document.querySelectorAll("#pending-table tbody tr");
  let allSaved = true;
  const ids = [...autoAcceptedPendingIds];
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
  const status = document.getElementById("scan-status");
  status.textContent = `${ids.length} Buchung(en) importiert.`;
  status.classList.remove("hidden");
  await loadPending();
  } finally {
    btn.disabled = false;
  }
});

(async function init() {
  await loadAppSettings();
  await loadCategories();
  await loadPending();
})();
