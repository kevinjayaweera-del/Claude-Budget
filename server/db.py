import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS imported_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hash TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    source TEXT NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS category_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL UNIQUE,
    category_id INTEGER NOT NULL REFERENCES categories(id)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CHF',
    category_id INTEGER REFERENCES categories(id),
    source TEXT NOT NULL,
    file_id INTEGER REFERENCES imported_files(id),
    manually_corrected INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pending_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CHF',
    category_id INTEGER REFERENCES categories(id),
    source TEXT NOT NULL,
    file_id INTEGER REFERENCES imported_files(id)
);
"""

# Columns added after the initial schema. Applied via idempotent ALTER TABLE
# so both fresh databases and Kevin's existing local data/budget.db pick
# them up on next start — see test_init_db_migrates_existing_database_*.
MIGRATIONS = [
    ("category_rules", "match_count", "INTEGER NOT NULL DEFAULT 0"),
    ("category_rules", "correction_count", "INTEGER NOT NULL DEFAULT 0"),
    ("category_rules", "iban", "TEXT"),
    ("category_rules", "is_seeded", "INTEGER NOT NULL DEFAULT 0"),
    ("category_rules", "created_at", "TEXT NOT NULL DEFAULT ''"),
    ("pending_transactions", "suggested_category_id", "INTEGER REFERENCES categories(id)"),
    ("pending_transactions", "suggested_rule_id", "INTEGER REFERENCES category_rules(id)"),
    ("pending_transactions", "category_confidence", "REAL"),
]

# Derived from Kevin's real ZKB bank statements (CSV/PDF) and Swisscard/
# Cornercard credit card statements — see docs/superpowers/specs for the
# analysis. Goal: the first real import needs as little manual
# categorization as possible.
DEFAULT_CATEGORIES = [
    "Lebensmittel", "Restaurants/Ausgang", "Transport", "Reisen",
    "Miete/Wohnen", "Versicherungen", "Gesundheit", "Shopping", "Abos",
    "Freizeit", "Bargeldbezug", "Privatüberweisungen", "Sparen/Anlegen",
    "Lohn/Einkommen", "Sonstiges", "Unkategorisiert",
]

# (keyword, category_name) — keyword must be lowercase (categorize() matches
# against a lowercased description) and is matched as a substring, so a more
# specific/longer keyword should be used wherever a shorter one would
# otherwise misfire (see "sbb mobile" vs "twint" below).
DEFAULT_CATEGORY_RULES = [
    # Lebensmittel
    ("migros", "Lebensmittel"),
    ("coop", "Lebensmittel"),
    ("volg", "Lebensmittel"),
    ("denner", "Lebensmittel"),
    ("metzg", "Lebensmittel"),
    # A single umlaut spelling is enough now — RuleBasedCategorizer.predict()
    # normalizes stored keywords before matching (see Task 3), so "bäckerei"
    # transparently also matches an incoming "BAECKEREI"/"Backerei" spelling.
    ("bäckerei", "Lebensmittel"),
    ("back.-conf.", "Lebensmittel"),
    ("confis", "Lebensmittel"),
    # Restaurants/Ausgang
    ("pizza falcone", "Restaurants/Ausgang"),
    ("curry factory", "Restaurants/Ausgang"),
    ("bar / restaurant caled", "Restaurants/Ausgang"),
    ("kuhn back & gastro", "Restaurants/Ausgang"),
    ("autogrill", "Restaurants/Ausgang"),
    ("* eats", "Restaurants/Ausgang"),
    # Well-known fast-food/café chains — a curated starter list so common
    # chains are recognized on first encounter instead of needing a manual
    # correction each time (see docs/superpowers chat context: "Subway" and
    # "Steiner Flughafebeck" were the motivating examples). "mcdonald" and
    # "domino" are deliberately the bare brand stem, not the full name, so
    # they match both "McDonald's"/"McDonalds" and "Domino's"/"Dominos"
    # spelling variants (normalize_description doesn't strip apostrophes).
    ("subway", "Restaurants/Ausgang"),
    ("mcdonald", "Restaurants/Ausgang"),
    ("burger king", "Restaurants/Ausgang"),
    ("kfc", "Restaurants/Ausgang"),
    ("starbucks", "Restaurants/Ausgang"),
    ("dunkin", "Restaurants/Ausgang"),
    ("domino", "Restaurants/Ausgang"),
    ("manora", "Restaurants/Ausgang"),
    ("vapiano", "Restaurants/Ausgang"),
    ("nordsee", "Restaurants/Ausgang"),
    # Real vendor from Kevin's own statements: a bakery/food counter at the
    # airport — food bought there is eating-out, not grocery shopping, so
    # it's mapped here rather than under the generic "bäckerei" keyword
    # (Lebensmittel) below.
    ("steiner flughafebeck", "Restaurants/Ausgang"),
    # Transport (checked before generic "twint" below due to length)
    ("sbb", "Transport"),
    ("sbb mobile", "Transport"),
    ("tankstell", "Transport"),
    ("parkingpay", "Transport"),
    ("taxifahrt", "Transport"),
    ("ubr* pending", "Transport"),
    # Generic ride/refund fallback — shorter than "* eats" above, so a food
    # delivery line still wins Restaurants/Ausgang; this only catches plain
    # Uber rides and refunds like "Rückerstattung ... UBER 00000 AMSTERDAM".
    ("uber", "Transport"),
    ("bergbahnen", "Freizeit"),
    # Reisen
    ("swiss intl air lines", "Reisen"),
    ("easyjet", "Reisen"),
    ("emirates", "Reisen"),
    ("hotel", "Reisen"),
    ("airbnb", "Reisen"),
    ("meininger", "Reisen"),
    # Versicherungen
    ("ökk", "Versicherungen"),
    ("helsana", "Versicherungen"),
    ("axa leben", "Versicherungen"),
    # Gesundheit
    ("apotheke", "Gesundheit"),
    # Shopping
    ("zalando", "Shopping"),
    ("digitec galaxus", "Shopping"),
    ("galaxus mobile", "Shopping"),
    ("media markt", "Shopping"),
    ("velotec", "Shopping"),
    ("calzedoni", "Shopping"),
    ("cutie socks", "Shopping"),
    ("scooter planet", "Shopping"),
    ("ofinto", "Shopping"),
    ("baby-walz", "Shopping"),
    # Abos
    ("spotify", "Abos"),
    ("netflix", "Abos"),
    ("apple.com/bill", "Abos"),
    ("sayintentions", "Abos"),
    # Freizeit
    ("steamgames", "Freizeit"),
    ("coiffure", "Freizeit"),
    # Bargeldbezug
    ("bezug zkb visa debit card", "Bargeldbezug"),
    # Sparen/Anlegen
    ("findependent", "Sparen/Anlegen"),
    # Sonstiges — card-bill settlements and unclear small vendors, kept out
    # of real spending categories
    ("ihre zahlung", "Sonstiges"),
    ("saldovortrag", "Sonstiges"),
    ("ubs - zahlungen div", "Sonstiges"),
    ("corporate benefits", "Sonstiges"),
    ("marko switzerland", "Sonstiges"),
    # Privatüberweisungen — generic TWINT catch-all. Kept last / shortest on
    # purpose: every merchant-routed "TWINT: X" line above has a longer,
    # more specific keyword that must win first (categorize() prefers the
    # longest matching keyword), so this only catches person-to-person
    # transfers like "TWINT: SCHWARTZ, PATRICK +4176..." that have no
    # merchant-specific rule.
    ("twint", "Privatüberweisungen"),
]


def get_connection(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _apply_migrations(conn):
    for table, column, ddl in MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db(db_path):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(db_path)
    conn.executescript(SCHEMA)
    _apply_migrations(conn)
    for name in DEFAULT_CATEGORIES:
        conn.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (name,))
    for keyword, category_name in DEFAULT_CATEGORY_RULES:
        category_id = conn.execute(
            "SELECT id FROM categories WHERE name = ?", (category_name,)
        ).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO category_rules "
            "(keyword, category_id, match_count, correction_count, is_seeded, created_at) "
            "VALUES (?, ?, 3, 0, 1, datetime('now'))",
            (keyword, category_id),
        )
    conn.commit()
    return conn


def reset_db(db_path):
    """Wipe all data (transactions, pending rows, imported-file records,
    learned/seeded rules, categories) and reseed the defaults — a clean-slate
    reset for repeated test imports, not a normal-operation code path."""
    conn = get_connection(db_path)
    conn.execute("DELETE FROM transactions")
    conn.execute("DELETE FROM pending_transactions")
    conn.execute("DELETE FROM imported_files")
    conn.execute("DELETE FROM category_rules")
    conn.execute("DELETE FROM categories")
    conn.commit()
    conn.close()
    return init_db(db_path)
