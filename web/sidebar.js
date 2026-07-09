const SIDEBAR_ICONS = {
  brand: `<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><rect x="3" y="12" width="4" height="9" rx="1"/><rect x="10" y="7" width="4" height="14" rx="1"/><rect x="17" y="3" width="4" height="18" rx="1"/></svg>`,
  dashboard: `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9.5 10 3l7 6.5"/><path d="M5 8.5V17h10V8.5"/></svg>`,
  import: `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10 2.5v8"/><path d="M6.8 7.7 10 10.9l3.2-3.2"/><path d="M4 12.5v2.8c0 .6.4 1 1 1h10c.6 0 1-.4 1-1v-2.8"/></svg>`,
  budget: `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="10" r="7"/><path d="M10 3v7h7"/></svg>`,
  regeln: `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="5" x2="16" y2="5"/><circle cx="8" cy="5" r="1.5" fill="currentColor" stroke="none"/><line x1="4" y1="10" x2="16" y2="10"/><circle cx="13" cy="10" r="1.5" fill="currentColor" stroke="none"/><line x1="4" y1="15" x2="16" y2="15"/><circle cx="10" cy="15" r="1.5" fill="currentColor" stroke="none"/></svg>`,
  settings: `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="10" cy="10" r="2.6"/><path d="M10 2.8v2.1M10 15.1v2.1M17.2 10h-2.1M4.9 10H2.8M15.1 4.9l-1.5 1.5M6.4 13.6l-1.5 1.5M15.1 15.1l-1.5-1.5M6.4 6.4 4.9 4.9"/></svg>`,
};

const SIDEBAR_LINKS = [
  { href: "/index.html", page: "dashboard", label: "Übersicht", icon: SIDEBAR_ICONS.dashboard },
  { href: "/import.html", page: "import", label: "Import", icon: SIDEBAR_ICONS.import },
  { href: "/budget.html", page: "budget", label: "Budget", icon: SIDEBAR_ICONS.budget },
  { href: "/regeln.html", page: "regeln", label: "Regeln", icon: SIDEBAR_ICONS.regeln },
];

const SIDEBAR_BOTTOM_LINKS = [
  { href: "/einstellungen.html", page: "einstellungen", label: "Einstellungen", icon: SIDEBAR_ICONS.settings },
];

function renderSidebarLinks(links, activePage) {
  return links.map(
    (link) => `
      <a href="${link.href}" class="sidebar-link${link.page === activePage ? " active" : ""}">
        <span class="sidebar-icon">${link.icon}</span>
        <span class="sidebar-label">${link.label}</span>
      </a>`
  ).join("");
}

function renderSidebar() {
  const nav = document.getElementById("sidebar");
  if (!nav) return;
  const activePage = nav.dataset.active;

  nav.innerHTML = `
    <div class="sidebar-brand">
      <span class="sidebar-brand-mark">${SIDEBAR_ICONS.brand}</span>
      <span class="sidebar-brand-name">Budget Tracker</span>
    </div>
    <div class="sidebar-nav">${renderSidebarLinks(SIDEBAR_LINKS, activePage)}</div>
    <div class="sidebar-nav sidebar-nav-bottom">${renderSidebarLinks(SIDEBAR_BOTTOM_LINKS, activePage)}</div>
  `;
}

renderSidebar();
