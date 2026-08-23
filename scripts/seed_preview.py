"""One-off seeder for data/preview.db, used only by Claude's browser-preview
verification (never touches data/budget.db). Not part of the app; safe to
delete after chart verification is done.
"""
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "preview.db"

random.seed(42)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# Idempotent re-seed: clear only what this script inserts, scoped to
# preview.db (never touches data/budget.db).
conn.execute("DELETE FROM transaction_tags")
conn.execute("DELETE FROM transactions")
conn.execute("DELETE FROM budgets")
conn.commit()

cat_ids = {row["name"]: row["id"] for row in conn.execute("SELECT id, name FROM categories")}

accounts = [("zkb_konto", "ZKB Privatkonto"), ("cornercard", "Cornercard Kreditkarte")]
account_ids = {}
for source_key, name in accounts:
    cur = conn.execute(
        "INSERT INTO accounts (source_key, name) VALUES (?, ?) "
        "ON CONFLICT(source_key) DO UPDATE SET name = excluded.name",
        (source_key, name),
    )
    account_ids[source_key] = conn.execute(
        "SELECT id FROM accounts WHERE source_key = ?", (source_key,)
    ).fetchone()["id"]

tag_names = ["Urlaub", "Geschäft", "Fixkosten", "Einmalig"]
tag_ids = {}
for t in tag_names:
    conn.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (t,))
    tag_ids[t] = conn.execute("SELECT id FROM tags WHERE name = ?", (t,)).fetchone()["id"]

income_cat = cat_ids.get("Lohn/Einkommen")
expense_cats = [c for c in cat_ids if c not in ("Lohn/Einkommen", "Kreditkarten-Ausgleich", "Unkategorisiert")]

merchants = {
    "Lebensmittel": ["Migros", "Coop", "Denner"],
    "Restaurants/Ausgang": ["Restaurant Rössli", "Starbucks", "McDonald's"],
    "Transport": ["SBB", "Uber", "Parkhaus"],
    "Reisen": ["SWISS Airlines", "Booking.com", "Hotel Bellevue"],
    "Miete/Wohnen": ["Hausverwaltung Muster AG"],
    "Versicherungen": ["CSS Versicherung", "AXA"],
    "Gesundheit": ["Apotheke", "Dr. Meier"],
    "Shopping": ["Zalando", "Galaxus", "H&M"],
    "Abos": ["Netflix", "Spotify", "Swisscom"],
    "Freizeit": ["Kino", "Fitnesspark"],
    "Bargeldbezug": ["Bancomat ZKB"],
    "Privatüberweisungen": ["Twint an Freund"],
    "Sparen/Anlegen": ["Sparplan ETF"],
    "Sport/Fahrrad": ["Canyon", "Velofactory AG", "Ochsner Sport"],
}

today = date(2026, 7, 10)
start = today - timedelta(days=545)  # ~18 months of history

rows = []
d = start
while d <= today:
    # Monthly salary on the 25th
    if d.day == 25 and income_cat:
        rows.append((
            d.isoformat(), "Lohn Juli", int(random.uniform(6800, 7200) * 100),
            "CHF", income_cat, "zkb_konto", account_ids["zkb_konto"], 0,
        ))
    # Monthly rent on the 1st
    if d.day == 1 and "Miete/Wohnen" in cat_ids:
        rows.append((
            d.isoformat(), "Miete Wohnung", -220000,
            "CHF", cat_ids["Miete/Wohnen"], "zkb_konto", account_ids["zkb_konto"], 0,
        ))
    # A handful of random expenses per day (sparse)
    if random.random() < 0.55:
        cat = random.choice(expense_cats)
        if cat not in merchants:
            d += timedelta(days=1)
            continue
        merchant = random.choice(merchants[cat])
        amount = -int(random.uniform(8, 180) * 100)
        source_key = "cornercard" if cat in ("Reisen", "Shopping", "Abos") and random.random() < 0.5 else "zkb_konto"
        rows.append((
            d.isoformat(), merchant, amount, "CHF",
            cat_ids[cat], source_key, account_ids[source_key], 0,
        ))
    d += timedelta(days=1)

conn.executemany(
    "INSERT INTO transactions (date, description, amount_cents, currency, "
    "category_id, source, account_id, manually_corrected) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    rows,
)

budget_targets = {
    "Lebensmittel": 60000,
    "Restaurants/Ausgang": 40000,
    "Shopping": 30000,
    "Abos": 8000,
}
for cat_name, limit in budget_targets.items():
    if cat_name in cat_ids:
        conn.execute(
            "INSERT OR REPLACE INTO budgets (category_id, monthly_limit_cents) VALUES (?, ?)",
            (cat_ids[cat_name], limit),
        )
conn.commit()

# Tag a sample of the inserted rows for tag-filter testing.
txn_ids = [r["id"] for r in conn.execute("SELECT id FROM transactions").fetchall()]
for tid in random.sample(txn_ids, k=min(60, len(txn_ids))):
    tag = random.choice(list(tag_ids.values()))
    conn.execute(
        "INSERT OR IGNORE INTO transaction_tags (transaction_id, tag_id) VALUES (?, ?)",
        (tid, tag),
    )
conn.commit()

print(f"Inserted {len(rows)} transactions into {DB_PATH}")
conn.close()
