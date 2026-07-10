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

CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL UNIQUE REFERENCES categories(id),
    monthly_limit_cents INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- source_key is the immutable identifier import derives from the
-- filename/folder (see import_service.py) — an account row is looked up
-- or created by source_key at import time. name is the user-editable
-- display name (defaults to source_key), so renaming an account doesn't
-- break the link back to future re-imports of the same source folder.
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

-- Tags apply to confirmed transactions only, not pending rows — tagging is
-- a post-review organizational step, not part of the import/categorize flow.
CREATE TABLE IF NOT EXISTS transaction_tags (
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (transaction_id, tag_id)
);
"""

# Stored as strings (settings.value is TEXT) and parsed by the API layer —
# a flat key/value store rather than dedicated columns so a new setting
# never needs a schema migration, just a new default entry here.
DEFAULT_SETTINGS = {
    "auto_categorize_enabled": "true",
    "confidence_threshold": "0.75",  # keep in sync with server.categorize.CONFIDENCE_THRESHOLD
    "default_date_range_days": "",  # empty = no default filtering ("alle")
}

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
    ("categories", "excluded_from_totals", "INTEGER NOT NULL DEFAULT 0"),
    ("transactions", "account_id", "INTEGER REFERENCES accounts(id)"),
    ("pending_transactions", "account_id", "INTEGER REFERENCES accounts(id)"),
]

# Categories seeded with excluded_from_totals=1 — money movement that isn't
# real income/spending (e.g. paying off a credit card bill from the linked
# checking account). Re-asserted idempotently on every init_db() call, since
# there's no UI to toggle this per-category; it's a fixed property of what
# the category represents, not a per-transaction user choice.
CATEGORIES_EXCLUDED_FROM_TOTALS = {"Kreditkarten-Ausgleich"}

# Derived from Kevin's real ZKB bank statements (CSV/PDF) and Swisscard/
# Cornercard credit card statements — see docs/superpowers/specs for the
# analysis. Goal: the first real import needs as little manual
# categorization as possible.
DEFAULT_CATEGORIES = [
    "Lebensmittel", "Restaurants/Ausgang", "Transport", "Reisen",
    "Miete/Wohnen", "Versicherungen", "Gesundheit", "Shopping", "Abos",
    "Freizeit", "Bargeldbezug", "Privatüberweisungen", "Sparen/Anlegen",
    "Lohn/Einkommen", "Sonstiges", "Kreditkarten-Ausgleich", "Unkategorisiert",
]

# Grouped by category so maintenance means "find the category, add a
# keyword" instead of hunting through one long flat list for where a new
# merchant belongs. Keyword must be lowercase (categorize() matches against
# a lowercased description) and is matched as a substring, so a more
# specific/longer keyword should be used wherever a shorter one would
# otherwise misfire (see "sbb mobile" vs "twint" below) — categorize()
# always prefers the longest matching keyword, regardless of list order.
# Flattened into the (keyword, category) pairs actually seeded by
# init_db() via _flatten_category_keyword_groups() below; that flattening
# doesn't change matching/precedence behavior at all, only this source
# layout is new.
DEFAULT_CATEGORY_KEYWORD_GROUPS = {
    "Lebensmittel": [
        "migros", "coop", "volg", "denner", "metzg", "suters hofmart",
        # A single umlaut spelling is enough now — RuleBasedCategorizer.
        # predict() normalizes stored keywords before matching (see Task 3),
        # so "bäckerei" transparently also matches an incoming "BAECKEREI"
        # spelling.
        "bäckerei", "back.-conf.", "confis",
        # ZKB's own statement text drops "ä" entirely rather than folding it
        # to "ae" the way Cornercard's export does (e.g. "Bäckerei" ->
        # "Backerei", not "Baeckerei") — normalize_description() can't
        # recover a dropped letter, so this needs its own keyword alongside
        # "bäckerei" above. Same issue recurs a few times below (ÖKK,
        # Bevölkerungsamt) — noted once here, not repeated at length.
        "backerei", "konditorei", "lidl",
        # The Spar chain's own receipt/statement text ("Spar dankt") rather
        # than the bare brand name — "spar" alone would false-positive on
        # "sparen"/"Sparen/Anlegen"-adjacent text.
        "spar dankt",
        # Second real-data mining pass (full year of statements): Tegut is
        # a German supermarket chain; "nahrungsmittel" (foodstuffs) is a
        # generic word that reliably signals a food wholesaler/supplier
        # regardless of the specific company name.
        "tegutfiliale", "nahrungsmittel", "new asia market", "asia store",
    ],
    "Restaurants/Ausgang": [
        # Generic dining-out words — catch any vendor that doesn't match a
        # specific chain keyword below, so a new restaurant/café doesn't
        # need its own rule the way a genuinely distinct merchant does.
        "restaurant", "cafe",
        "pizza falcone", "curry factory", "bar / restaurant caled",
        "kuhn back & gastro", "autogrill", "* eats", "uber eats",
        # Well-known fast-food/café chains — a curated starter list so
        # common chains are recognized on first encounter instead of
        # needing a manual correction each time. "mcdonald" and "domino"
        # are deliberately the bare brand stem, not the full name, so they
        # match both "McDonald's"/"McDonalds" and "Domino's"/"Dominos"
        # spelling variants (normalize_description doesn't strip
        # apostrophes).
        "subway", "mcdonald", "burger king", "kfc", "starbucks", "dunkin",
        "domino", "manora", "vapiano", "nordsee", "legend doener",
        # Real vendor from Kevin's own statements: a bakery/food counter at
        # the airport — food bought there is eating-out, not grocery
        # shopping, so it's mapped here rather than under the generic
        # bakery keywords above.
        "steiner flughafebeck",
        # More real vendors mined from Kevin's full statement history —
        # small bakery/café/take-away stops where he's eating, not grocery
        # shopping. "wal*cafe" is deliberately NOT listed separately: it
        # already contains "cafe" as a substring, so the generic keyword
        # above catches it without a dedicated rule.
        "backer-imbiss", "take-away", "ristorante", "berggasthaus",
        # Kept longer/more specific than the generic "cafe" above so the
        # hospital's on-site café still wins Restaurants/Ausgang over the
        # shorter "stadtspital triemli" (Gesundheit) keyword below —
        # categorize() prefers the longest match.
        "stadtspital triemli cafe",
        "estia home of taste", "tillystante",
        # Cornercard's own comma-joined "MERCHANT,CITY" format has no space
        # between words — the existing "pizza falcone" keyword (with a
        # space) never matches it, so this is a second keyword for the
        # same vendor.
        "pizzafalcone",
        # Marché (SV Group) — a restaurant chain at Swiss train stations/
        # airports/highway stops, e.g. "Marche-6137 Firehouse".
        "marche-",
        # Second real-data mining pass (full year of statements). "pizza"
        # is a generic catch-all — like "restaurant"/"cafe" above, it
        # covers any pizzeria that isn't "pizza falcone" specifically.
        "pizza", "brezelkonig", "starkebab", "pezzodipane", "lsmpanadastore",
        "namastey", "luckys thai", "jack's thai", "marmar cuisine",
        "elvetino", "triemlis food shop", "triemlisfoodshop", "butegar",
        "boostbar", "quadrifoglio",
        # Cornercard glues "Companys" (a tapas-bar chain) directly onto the
        # city with no space, same issue as "pizzafalcone" above.
        "companyszuerich",
        # Kevin identified these four as restaurants after they showed up
        # too ambiguous to guess in the second mining pass — "Bankhaus
        # Metzler" and "Hauptsitz Postfinance" read like banking/postal
        # institutions but are actually restaurant names at those
        # locations; "Reinhard AG" likewise.
        "bankhausmetzler", "bankhaus metzler", "reinhard ag",
        "hauptsitz postfinance",
    ],
    "Transport": [
        "sbb", "sbb mobile", "tankstell", "parkingpay", "taxifahrt",
        "ubr* pending",
        # Generic ride/refund fallback — shorter than "* eats"/"uber eats"
        # above, so a food delivery line still wins Restaurants/Ausgang;
        # this only catches plain Uber rides and refunds like
        # "Rückerstattung ... UBER 00000 AMSTERDAM".
        "uber",
        # Gas station brands, parking, and e-scooter/ride-share apps, mined
        # from real statements — none of these are covered by the generic
        # "tankstell"/"uber" keywords above.
        "shell", "socar", "avia", "agrola", "dott scooter", "bolt.",
        "amag leasing", "parkhaus", "carwash",
        # Cornercard's comma-joined format glues this one too — same issue
        # as "pizzafalcone"/"swissintlairlines" above.
        "dottscooterride",
        # Second real-data mining pass: "taxi" generalizes past the
        # existing "taxifahrt" — a taxi company's own name (e.g. "Taxi
        # Asmat") doesn't contain that word at all. "pedaggi" (Italian) and
        # "asfinag" (Austrian) are foreign highway-toll charges;
        # "parkdepot" is a parking-garage operator.
        "taxi", "pedaggi", "asfinag", "parkdepot",
    ],
    "Reisen": [
        "swiss intl air lines",
        # Real vendors print the same airline with no spaces at all
        # ("SwissIntlAirlines,Frankfurt") — the spaced keyword above never
        # matches that; kept as a second keyword rather than replacing it,
        # in case a spaced format ever does show up.
        "swissintlairlines",
        "easyjet", "emirates", "hotel", "airbnb", "meininger",
        "getyourguide", "airalo",
        # Second real-data mining pass: another airline, two car-rental
        # sites, and a Frankfurt museum-district visit (Kevin travels there
        # regularly per other Reisen/Frankfurt entries elsewhere).
        "lufthansa", "rentalcars", "sunnycars", "frankfurtmuseumsufer",
    ],
    "Versicherungen": [
        "ökk",
        # ZKB drops the umlaut entirely on this one too ("ÖKK" -> "OKK").
        # Kept as "okk kranken" rather than the bare 3-letter "okk" to
        # avoid an accidental substring match inside unrelated words.
        "okk kranken",
        "helsana", "axa leben", "axa versicherungen",
        "innova versicherungen", "mobiliar versicherungsgesellschaft",
        # The insurer's own marketing name ("Die Mobiliar") — broader than
        # the full legal name above, but kept as this exact phrase rather
        # than the bare word "mobiliar" (which is also plain German for
        # "furniture" and could false-positive elsewhere).
        "die mobiliar",
        "swiss life", "protekta",
        # Rega (Swiss air rescue) — an annual patronage membership,
        # functionally the same kind of recurring protection payment as
        # the insurers above.
        "rega,",
    ],
    "Gesundheit": [
        "apotheke",
        # "zahnarzt" (dentist, in general) is broader than the specific
        # "zahnarztpraxis" vendor name below — kept both since a shorter
        # keyword only matters if the longer one doesn't also match.
        "zahnarzt", "zahnarztpraxis", "gynpraxis", "gemeinschaftspraxis",
        "shiatsu", "physio-therapien", "orthopaedie-technik",
        # Traditional Chinese Medicine practices — no single vendor name to
        # anchor on, so matched by the generic practice type instead. Kept
        # as "tcm praxis" rather than the bare 3-letter "tcm": TWINT-routed
        # payments are common in Kevin's data, and a 3-letter keyword loses
        # to the 5-letter generic "twint" catch-all below (categorize()
        # prefers the longest match).
        "tcm praxis",
        # Hospital visits — kept shorter/broader than "stadtspital triemli
        # cafe" (Restaurants/Ausgang) above so the on-site café still wins
        # for actual café purchases; this only catches the hospital itself.
        "stadtspital triemli",
        # Second real-data mining pass: Amavita is a Swiss pharmacy chain;
        # Hirslanden is a private-hospital-group brand (kept as the bare
        # name so it also catches other Hirslanden-branded facilities, not
        # just this specific headache clinic).
        "amavita", "hirslanden",
    ],
    "Shopping": [
        "zalando", "digitec galaxus", "galaxus mobile", "media markt",
        "velotec", "calzedoni", "cutie socks", "scooter planet", "ofinto",
        "baby-walz", "ikea", "hornbach", "jysk", "brack", "about you",
        "amzn",
        # Cornercard's comma-joined "MERCHANT,CITY" format has no space
        # between words at all — same issue as "pizzafalcone"/
        # "swissintlairlines" above, for these existing space-separated
        # keywords.
        "cutiesocks", "scooterplanet", "mediamarkt",
        # H&M purchased via the Klarna checkout — ZKB strips the "&" and
        # prints inconsistent spacing after the asterisk across statements,
        # so both observed spellings are kept.
        "klarna*h m", "klarna* h m",
        # Second real-data mining pass: a bicycle brand, a bike-suspension
        # shop, an online bike importer, an optician chain, a pet-supplies
        # retailer, and a children's-clothing brand bought via Klarna.
        "canyon", "suspension center", "bike-import", "fielmann", "zooplus",
        "ehrenkind",
    ],
    "Abos": [
        "spotify", "netflix", "apple.com/bill", "sayintentions",
        "salt mobile", "navigraph",
        # digitec Galaxus's own subscription product — distinct from the
        # plain "digitec galaxus"/"galaxus mobile" one-off purchases above
        # (Shopping).
        "galaxus abos",
        # Second real-data mining pass: a fitness-tracker subscription and
        # a content-subscription platform.
        "whoop", "onlyfans",
    ],
    "Freizeit": [
        "steamgames", "coiffure", "playstation network", "sanapark",
        "bergbahnen",
    ],
    "Bargeldbezug": [
        "bezug zkb visa debit card",
    ],
    "Sparen/Anlegen": [
        "findependent",
        # ZKB's own pillar-3a app; the risk-strategy fund name appears in
        # the statement text ("Frankly Risky").
        "frankly",
    ],
    "Miete/Wohnen": [
        "barth real ag", "otto markwalder", "elektrizitaetswerke",
        # Second real-data mining pass: a condo owners' association fee
        # (Miteigentümergemeinschaft) is housing-related, not generic
        # "Sonstiges". Kept as this literal (umlaut-dropped) substring
        # rather than folding, since the stored description text is
        # itself truncated at this point.
        "miteigentumergemeinschaf",
    ],
    # Both sides of the "pay off the credit card bill from the checking
    # account" event: the credit-card statement's own payment-received
    # line ("Ihre Zahlung – Besten Dank", a credit) and the checking
    # account's matching collection debit (identified by the card issuer's
    # legal name as it appears on the ZKB statement). Excluded from totals
    # (see categories.excluded_from_totals) because the money already
    # counted once, either way, when the individual card transactions
    # themselves were imported — counting this too would double it.
    "Kreditkarten-Ausgleich": [
        "ihre zahlung",
        # Some PDF exports merge "IHRE"/"ZAHLUNG" into one word with no
        # space (a pdfplumber word-extraction quirk on that specific
        # statement layout) — kept as a separate keyword rather than
        # loosening to bare "zahlung", which would false-positive on
        # "Zahlungszweck"/"Ratenzahlung"/etc.
        "ihrezahlung", "swisscard aecs", "corner banca",
        # "Saldovortrag" (balance carried forward) is the credit-card
        # statement's own opening-balance line — the mirror image of "Ihre
        # Zahlung" above. Both represent last month's already-counted
        # balance, not a new expense/income this month, so both are
        # excluded from totals.
        "saldovortrag",
    ],
    "Sonstiges": [
        # Unclear small vendors, kept out of real spending categories.
        "ubs - zahlungen div", "corporate benefits", "marko switzerland",
        # Cantonal tax withdrawal and generic bank transaction fees — no
        # dedicated category for either, and both are clearly not everyday
        # spending.
        "steuerbezug", "zahlungsverkehrspreise",
        # Government/administrative offices — not personal spending, but
        # not a transfer either.
        "post ch", "bevölkerungsamt",
        # ZKB drops the umlaut entirely here too ("Bevölkerungsamt" ->
        # "Bevolkerungsamt"), same issue as "bäckerei"/"backerei" above.
        "bevolkerungsamt", "einwohnermeldeamt", "gemeindeverwaltung",
        # Second real-data mining pass: a payment-rounding adjustment, a
        # generic annual-membership-fee line, the Cornercard no-space
        # spelling of the existing "corporate benefits" keyword, and a
        # primary-school-district payment.
        "rundung", "jahresbeitrag", "corporatebenefits",
        "primarschulgemeinde",
        # Kevin identified these as Sonstiges after they showed up too
        # ambiguous to guess in the second mining pass.
        "echst.net", "nvg zentrum",
    ],
    "Lohn/Einkommen": [
        # Any "Gutschrift Salär: <employer>" line, regardless of employer
        # — one general keyword instead of one entry per employer name.
        "salaer",
    ],
    "Privatüberweisungen": [
        # Recurring transfers to/from family, identified by name as they
        # appear on the statement.
        "jayaweera kevin oder fabienne", "fabienne brun",
        "jasmin xenia liviero", "kevin jayaweera",
        # ZKB's own transaction-type label for any account-to-account
        # transfer — broader than the name-specific keywords above, so a
        # transfer to/from someone not yet named here is still recognized
        # instead of needing a new rule added.
        "kontouebertrag",
        # Generic TWINT catch-all. Kept last / shortest on purpose: every
        # merchant-routed "TWINT: X" line above has a longer, more specific
        # keyword that must win first (categorize() prefers the longest
        # matching keyword), so this only catches person-to-person
        # transfers like "TWINT: SCHWARTZ, PATRICK +4176..." that have no
        # merchant-specific rule.
        "twint",
    ],
}


def _flatten_category_keyword_groups(groups):
    return [
        (keyword, category)
        for category, keywords in groups.items()
        for keyword in keywords
    ]


DEFAULT_CATEGORY_RULES = _flatten_category_keyword_groups(DEFAULT_CATEGORY_KEYWORD_GROUPS)


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
    for name in CATEGORIES_EXCLUDED_FROM_TOTALS:
        conn.execute(
            "UPDATE categories SET excluded_from_totals = 1 WHERE name = ?", (name,)
        )
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
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
    # One-off retarget for a pre-existing database where "ihre zahlung" was
    # already seeded under its old category (Sonstiges), before this
    # exclude-from-totals fix existed. Deliberately NOT a generic
    # "resync every seeded rule to its DEFAULT_CATEGORY_RULES category"
    # mechanism: is_seeded is never cleared when a rule is retargeted via
    # PUT /api/rules/<id> (see update_rule), so a generic version would
    # silently undo any manual re-categorization Kevin makes through
    # "Regeln verwalten" on every restart.
    kreditkarten_ausgleich_id = conn.execute(
        "SELECT id FROM categories WHERE name = 'Kreditkarten-Ausgleich'"
    ).fetchone()["id"]
    sonstiges_id = conn.execute(
        "SELECT id FROM categories WHERE name = 'Sonstiges'"
    ).fetchone()["id"]
    conn.execute(
        "UPDATE category_rules SET category_id = ? "
        "WHERE keyword = 'ihre zahlung' AND category_id = ?",
        (kreditkarten_ausgleich_id, sonstiges_id),
    )
    # Same one-off retarget for "saldovortrag", seeded under Sonstiges before
    # the credit-card-settlement exclusion existed for it too.
    conn.execute(
        "UPDATE category_rules SET category_id = ? "
        "WHERE keyword = 'saldovortrag' AND category_id = ?",
        (kreditkarten_ausgleich_id, sonstiges_id),
    )
    _backfill_accounts(conn)
    conn.commit()
    return conn


def get_or_create_account(conn, source_key):
    """Look up the account for a source_key (the filename/folder-derived
    string import_service.py already computes as `source`), creating it
    with name=source_key on first sight. Renaming an account only changes
    its display name, so it keeps resolving to the same row on future
    re-imports of that source."""
    row = conn.execute(
        "SELECT id FROM accounts WHERE source_key = ?", (source_key,)
    ).fetchone()
    if row:
        return row["id"]
    cursor = conn.execute(
        "INSERT INTO accounts (source_key, name) VALUES (?, ?)",
        (source_key, source_key),
    )
    return cursor.lastrowid


def _backfill_accounts(conn):
    """Create an account for every distinct `source` string already present
    in transactions/pending_transactions (e.g. from before the accounts
    table existed, or rows inserted by code that hasn't been updated to set
    account_id directly) and point account_id at it. Only fills in NULLs —
    never overwrites an existing account_id — so it's safe to run on every
    init_db() call without disturbing a row someone reassigned."""
    sources = set()
    for table in ("transactions", "pending_transactions"):
        for row in conn.execute(f"SELECT DISTINCT source FROM {table} WHERE account_id IS NULL"):
            sources.add(row["source"])
    for source in sources:
        account_id = get_or_create_account(conn, source)
        for table in ("transactions", "pending_transactions"):
            conn.execute(
                f"UPDATE {table} SET account_id = ? WHERE source = ? AND account_id IS NULL",
                (account_id, source),
            )


def reset_db(db_path):
    """Wipe all data (transactions, pending rows, imported-file records,
    learned/seeded rules, categories, accounts, tags) and reseed the
    defaults — a clean-slate reset for repeated test imports, not a
    normal-operation code path."""
    conn = get_connection(db_path)
    conn.execute("DELETE FROM transaction_tags")
    conn.execute("DELETE FROM tags")
    conn.execute("DELETE FROM transactions")
    conn.execute("DELETE FROM pending_transactions")
    conn.execute("DELETE FROM imported_files")
    conn.execute("DELETE FROM category_rules")
    conn.execute("DELETE FROM budgets")
    conn.execute("DELETE FROM categories")
    conn.execute("DELETE FROM accounts")
    conn.execute("DELETE FROM settings")
    conn.commit()
    conn.close()
    return init_db(db_path)


def reset_imported_data(db_path):
    """Wipe only what an import produced (transactions, pending rows,
    imported-file records, accounts — accounts are re-derived from source
    on the next scan) so statements can be rescanned from scratch — unlike
    reset_db(), this leaves categories, learned/seeded rules, budgets, tags
    and settings untouched. Meant for repeatedly re-testing imports without
    losing the categorization the user has already trained."""
    conn = get_connection(db_path)
    conn.execute("DELETE FROM transaction_tags")
    conn.execute("DELETE FROM transactions")
    conn.execute("DELETE FROM pending_transactions")
    conn.execute("DELETE FROM imported_files")
    conn.execute("DELETE FROM accounts")
    conn.commit()
    return conn
