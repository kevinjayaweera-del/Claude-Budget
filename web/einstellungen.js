function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

let categories = [];

// ---------- Kategorien ----------

async function loadCategories() {
  const res = await fetch("/api/categories");
  categories = await res.json();
  renderCategoryList();
  await populateDeleteFilters();
}

function renderCategoryList() {
  const container = document.getElementById("category-list");
  container.innerHTML = categories.map((c) => `
    <div class="settings-list-row" data-id="${c.id}">
      <input type="text" class="settings-category-input" value="${escapeHtml(c.name)}">
      <button type="button" class="btn-ghost btn-small" data-action="rename">Umbenennen</button>
      <button type="button" class="btn-danger btn-small" data-action="delete">Löschen</button>
    </div>
  `).join("");
}

document.getElementById("category-list").addEventListener("click", async (event) => {
  const row = event.target.closest(".settings-list-row");
  if (!row) return;
  const id = row.dataset.id;
  const input = row.querySelector(".settings-category-input");

  if (event.target.dataset.action === "rename") {
    const newName = input.value.trim();
    if (!newName) {
      alert("Name darf nicht leer sein.");
      return;
    }
    const res = await fetch(`/api/categories/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newName }),
    });
    if (!res.ok) {
      const body = await res.json();
      alert(body.error || "Fehler beim Umbenennen.");
      return;
    }
    await loadCategories();
  }

  if (event.target.dataset.action === "delete") {
    let res = await fetch(`/api/categories/${id}`, { method: "DELETE" });
    if (res.status === 409) {
      const deps = await res.json();
      const parts = [];
      if (deps.transaction_count) parts.push(`${deps.transaction_count} Buchung(en)`);
      if (deps.pending_count) parts.push(`${deps.pending_count} offene Buchung(en)`);
      if (deps.rule_count) parts.push(`${deps.rule_count} Regel(n)`);
      if (deps.budget_count) parts.push(`${deps.budget_count} Budget(s)`);
      const confirmed = confirm(
        `Diese Kategorie wird verwendet von: ${parts.join(", ")}. ` +
        'Buchungen und Regeln werden auf "Unkategorisiert" umgestellt, Budgets entfernt. Fortfahren?'
      );
      if (!confirmed) return;
      res = await fetch(`/api/categories/${id}?confirm=true`, { method: "DELETE" });
    }
    if (!res.ok) {
      const body = await res.json();
      alert(body.error || "Fehler beim Löschen.");
      return;
    }
    await loadCategories();
  }
});

document.getElementById("add-category-btn").addEventListener("click", async () => {
  const input = document.getElementById("new-category-name");
  const name = input.value.trim();
  if (!name) {
    alert("Bitte einen Namen eingeben.");
    return;
  }
  const res = await fetch("/api/categories", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    const body = await res.json();
    alert(body.error || "Fehler beim Hinzufügen.");
    return;
  }
  input.value = "";
  await loadCategories();
});

// ---------- Konten ----------

async function loadAccounts() {
  const res = await fetch("/api/accounts");
  const accounts = await res.json();
  const container = document.getElementById("account-list");
  if (accounts.length === 0) {
    container.innerHTML = '<p class="panel-empty">Noch keine Konten — werden beim ersten Import automatisch angelegt.</p>';
    return;
  }
  container.innerHTML = accounts.map((a) => `
    <div class="settings-list-row" data-id="${a.id}">
      <input type="text" class="settings-category-input" value="${escapeHtml(a.name)}">
      <button type="button" class="btn-ghost btn-small" data-action="rename">Umbenennen</button>
      <button type="button" class="btn-danger btn-small" data-action="delete">Löschen</button>
    </div>
  `).join("");
}

document.getElementById("account-list").addEventListener("click", async (event) => {
  const row = event.target.closest(".settings-list-row");
  if (!row) return;
  const id = row.dataset.id;
  const input = row.querySelector(".settings-category-input");

  if (event.target.dataset.action === "rename") {
    const newName = input.value.trim();
    if (!newName) { alert("Name darf nicht leer sein."); return; }
    const res = await fetch(`/api/accounts/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newName }),
    });
    if (!res.ok) {
      const body = await res.json();
      alert(body.error || "Fehler beim Umbenennen.");
      return;
    }
    await loadAccounts();
  }

  if (event.target.dataset.action === "delete") {
    const res = await fetch(`/api/accounts/${id}`, { method: "DELETE" });
    if (res.status === 409) {
      const deps = await res.json();
      const parts = [];
      if (deps.transaction_count) parts.push(`${deps.transaction_count} Buchung(en)`);
      if (deps.pending_count) parts.push(`${deps.pending_count} offene Buchung(en)`);
      alert(`Dieses Konto kann nicht gelöscht werden — verwendet von: ${parts.join(", ")}.`);
      return;
    }
    if (!res.ok) {
      const body = await res.json();
      alert(body.error || "Fehler beim Löschen.");
      return;
    }
    await loadAccounts();
  }
});

// ---------- Tags ----------

async function loadTags() {
  const res = await fetch("/api/tags");
  const tags = await res.json();
  const container = document.getElementById("tag-list");
  if (tags.length === 0) {
    container.innerHTML = '<p class="panel-empty">Noch keine Tags. Füge unten einen hinzu.</p>';
    return;
  }
  container.innerHTML = tags.map((t) => `
    <div class="settings-list-row" data-id="${t.id}">
      <input type="text" class="settings-category-input" value="${escapeHtml(t.name)}">
      <button type="button" class="btn-ghost btn-small" data-action="rename">Umbenennen</button>
      <button type="button" class="btn-danger btn-small" data-action="delete">Löschen</button>
    </div>
  `).join("");
}

document.getElementById("tag-list").addEventListener("click", async (event) => {
  const row = event.target.closest(".settings-list-row");
  if (!row) return;
  const id = row.dataset.id;
  const input = row.querySelector(".settings-category-input");

  if (event.target.dataset.action === "rename") {
    const newName = input.value.trim();
    if (!newName) { alert("Name darf nicht leer sein."); return; }
    const res = await fetch(`/api/tags/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newName }),
    });
    if (!res.ok) {
      const body = await res.json();
      alert(body.error || "Fehler beim Umbenennen.");
      return;
    }
    await loadTags();
  }

  if (event.target.dataset.action === "delete") {
    const confirmed = confirm("Diesen Tag wirklich löschen? Er wird von allen Buchungen entfernt.");
    if (!confirmed) return;
    const res = await fetch(`/api/tags/${id}`, { method: "DELETE" });
    if (!res.ok) {
      const body = await res.json();
      alert(body.error || "Fehler beim Löschen.");
      return;
    }
    await loadTags();
  }
});

document.getElementById("add-tag-btn").addEventListener("click", async () => {
  const input = document.getElementById("new-tag-name");
  const name = input.value.trim();
  if (!name) { alert("Bitte einen Namen eingeben."); return; }
  const res = await fetch("/api/tags", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    const body = await res.json();
    alert(body.error || "Fehler beim Hinzufügen.");
    return;
  }
  input.value = "";
  await loadTags();
});

// ---------- Automatische Kategorisierung / Dashboard settings ----------

async function loadSettings() {
  const settings = await fetch("/api/settings").then((r) => r.json());
  document.getElementById("auto-categorize-toggle").checked = settings.auto_categorize_enabled;
  document.getElementById("confidence-slider").value = settings.confidence_threshold;
  document.getElementById("confidence-value").textContent = `${Math.round(settings.confidence_threshold * 100)}%`;
  document.getElementById("default-date-range").value = settings.default_date_range_days || "";
  renderDbInfo(settings.db_info);
}

function renderDbInfo(info) {
  const container = document.getElementById("db-info");
  const sizeKb = (info.db_size_bytes / 1024).toFixed(1);
  container.innerHTML = `
    <div class="kpi-card"><p class="kpi-label">Buchungen</p><p class="kpi-value tabular">${info.transaction_count}</p></div>
    <div class="kpi-card"><p class="kpi-label">Offene Buchungen</p><p class="kpi-value tabular">${info.pending_count}</p></div>
    <div class="kpi-card"><p class="kpi-label">Kategorien</p><p class="kpi-value tabular">${info.category_count}</p></div>
    <div class="kpi-card"><p class="kpi-label">Regeln</p><p class="kpi-value tabular">${info.rule_count}</p></div>
    <div class="kpi-card"><p class="kpi-label">Datenbankgrösse</p><p class="kpi-value tabular">${sizeKb} KB</p></div>
  `;
}

document.getElementById("auto-categorize-toggle").addEventListener("change", async (event) => {
  await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ auto_categorize_enabled: event.target.checked }),
  });
});

document.getElementById("confidence-slider").addEventListener("input", (event) => {
  document.getElementById("confidence-value").textContent = `${Math.round(event.target.value * 100)}%`;
});
document.getElementById("confidence-slider").addEventListener("change", async (event) => {
  await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confidence_threshold: parseFloat(event.target.value) }),
  });
});

document.getElementById("default-date-range").addEventListener("change", async (event) => {
  const value = event.target.value;
  await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ default_date_range_days: value ? parseInt(value, 10) : null }),
  });
});

// ---------- Datenverwaltung ----------

async function populateDeleteFilters() {
  const categorySelect = document.getElementById("delete-category");
  categorySelect.innerHTML = '<option value="">Alle Kategorien</option>' +
    categories.map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");

  const sources = await fetch("/api/sources").then((r) => r.json());
  const sourceSelect = document.getElementById("delete-source");
  sourceSelect.innerHTML = '<option value="">Alle Konten</option>' +
    sources.map((s) => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
}

document.getElementById("delete-filtered-btn").addEventListener("click", async () => {
  const params = new URLSearchParams();
  const start = document.getElementById("delete-start").value;
  const end = document.getElementById("delete-end").value;
  const category = document.getElementById("delete-category").value;
  const source = document.getElementById("delete-source").value;
  const type = document.getElementById("delete-type").value;
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  if (category) params.set("category_id", category);
  if (source) params.set("source", source);
  if (type) params.set("type", type);

  if ([...params.keys()].length === 0) {
    alert("Bitte mindestens einen Filter auswählen.");
    return;
  }

  const confirmed = confirm(
    "Wirklich alle Buchungen löschen, die den gewählten Filtern entsprechen? " +
    "Dies kann nicht rückgängig gemacht werden."
  );
  if (!confirmed) return;

  if (document.getElementById("delete-filtered-backup").checked) {
    params.set("backup", "true");
  }

  const res = await fetch(`/api/transactions?${params.toString()}`, { method: "DELETE" });
  if (!res.ok) {
    const body = await res.json();
    alert(body.error || "Fehler beim Löschen.");
    return;
  }
  const result = await res.json();
  alert(`${result.deleted} Buchung(en) gelöscht.`);
  await loadSettings();
});

document.getElementById("reset-imports-btn").addEventListener("click", async () => {
  const confirmed = confirm(
    "Wirklich alle importierten Buchungen löschen? Kategorien, gelernte Regeln, " +
    "Budgets und Einstellungen bleiben erhalten. Dies kann nicht rückgängig gemacht werden."
  );
  if (!confirmed) return;
  const backup = document.getElementById("reset-imports-backup").checked;
  const res = await fetch(`/api/database/reset-imports${backup ? "?backup=true" : ""}`, { method: "POST" });
  if (!res.ok) {
    alert("Fehler beim Zurücksetzen — bitte erneut versuchen.");
    return;
  }
  window.location.reload();
});

document.getElementById("reset-all-btn").addEventListener("click", async () => {
  const confirmed = confirm(
    "Wirklich die gesamte Datenbank löschen? Alle Buchungen, Importe und gelernten " +
    "Regeln werden entfernt und die Standardkategorien neu geladen. " +
    "Dies kann nicht rückgängig gemacht werden."
  );
  if (!confirmed) return;
  const backup = document.getElementById("reset-all-backup").checked;
  const res = await fetch(`/api/database/reset${backup ? "?backup=true" : ""}`, { method: "POST" });
  if (!res.ok) {
    alert("Fehler beim Zurücksetzen — bitte erneut versuchen.");
    return;
  }
  window.location.reload();
});

// ---------- Allgemein: Theme ----------

const THEME_KEY = "theme";

function currentThemeChoice() {
  const stored = localStorage.getItem(THEME_KEY);
  return stored === "light" || stored === "dark" ? stored : "system";
}

function updateThemeSwitcherUI() {
  const current = currentThemeChoice();
  document.querySelectorAll("#theme-switcher .segmented-option").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.themeChoice === current);
  });
}

function applyTheme(choice) {
  if (choice === "system") {
    document.documentElement.removeAttribute("data-theme");
    localStorage.removeItem(THEME_KEY);
  } else {
    document.documentElement.setAttribute("data-theme", choice);
    localStorage.setItem(THEME_KEY, choice);
  }
  updateThemeSwitcherUI();
}

document.getElementById("theme-switcher").addEventListener("click", (event) => {
  const choice = event.target.dataset.themeChoice;
  if (!choice) return;
  applyTheme(choice);
});

(async function init() {
  await loadCategories();
  await loadAccounts();
  await loadTags();
  await loadSettings();
  updateThemeSwitcherUI();
})();
