import sqlite3

from server.db import init_db, reset_db, reset_imported_data, DEFAULT_CATEGORIES, DEFAULT_CATEGORY_RULES
from server.categorize import RuleBasedCategorizer


def test_init_db_creates_tables(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)

    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "imported_files", "categories", "category_rules",
        "transactions", "pending_transactions",
    }.issubset(tables)
    conn.close()


def test_init_db_seeds_default_categories(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)

    names = {row["name"] for row in conn.execute("SELECT name FROM categories").fetchall()}
    assert names == set(DEFAULT_CATEGORIES)
    conn.close()


def test_init_db_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path).close()
    conn = init_db(db_path)  # second call must not fail or duplicate rows

    count = conn.execute("SELECT COUNT(*) as c FROM categories").fetchone()["c"]
    assert count == len(DEFAULT_CATEGORIES)
    conn.close()


def test_init_db_seeds_default_category_rules(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)

    count = conn.execute("SELECT COUNT(*) as c FROM category_rules").fetchone()["c"]
    assert count == len(DEFAULT_CATEGORY_RULES)

    migros_category_id = conn.execute(
        "SELECT category_id FROM category_rules WHERE keyword = 'migros'"
    ).fetchone()["category_id"]
    lebensmittel_id = conn.execute(
        "SELECT id FROM categories WHERE name = 'Lebensmittel'"
    ).fetchone()["id"]
    assert migros_category_id == lebensmittel_id
    conn.close()


def test_init_db_category_rules_are_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path).close()
    conn = init_db(db_path)  # second call must not fail (UNIQUE keyword) or duplicate

    count = conn.execute("SELECT COUNT(*) as c FROM category_rules").fetchone()["c"]
    assert count == len(DEFAULT_CATEGORY_RULES)
    conn.close()


def test_default_category_rules_prefer_merchant_over_generic_twint_keyword(tmp_path):
    # Several ZKB statement lines route a merchant payment through TWINT
    # (e.g. "Belastung TWINT: SBB MOBILE BERN") — these must land in the
    # merchant's real category, not the generic "twint" person-to-person
    # transfer catch-all, which only wins when no more specific keyword
    # matches (categorize() prefers the longest matching keyword).
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    def category_name(description):
        category_id, _, _ = categorizer.predict(description)
        return conn.execute(
            "SELECT name FROM categories WHERE id = ?", (category_id,)
        ).fetchone()["name"]

    assert category_name("Belastung TWINT: SBB MOBILE BERN") == "Transport"
    assert category_name("Belastung TWINT: PARKINGPAY-TWINT SCHLIEREN") == "Transport"
    assert category_name("Belastung TWINT: GALAXUS MOBILE ZURICH") == "Shopping"
    assert category_name("Belastung TWINT: BABY-WALZ AG ST. GALLEN") == "Shopping"
    assert category_name("Gutschrift TWINT: SCHWARTZ, PATRICK +41764535887") == "Privatüberweisungen"
    conn.close()


def test_default_category_rules_recognize_common_fast_food_and_cafe_chains(tmp_path):
    # Well-known chains a first-time import should already get right without
    # manual correction — including two real vendors from Kevin's own
    # statements that were previously landing in "Unkategorisiert".
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    def category_name(description):
        category_id, _, _ = categorizer.predict(description)
        return conn.execute(
            "SELECT name FROM categories WHERE id = ?", (category_id,)
        ).fetchone()["name"]

    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Subway Ruemlang 0000") == "Restaurants/Ausgang"
    assert category_name(
        "Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Steiner Flughafebeck AG"
    ) == "Restaurants/Ausgang"
    assert category_name("MCDONALDS ZUERICH HB") == "Restaurants/Ausgang"
    assert category_name("STARBUCKS COFFEE BASEL") == "Restaurants/Ausgang"
    assert category_name("BURGER KING WINTERTHUR") == "Restaurants/Ausgang"
    assert category_name("MANORA ZUERICH") == "Restaurants/Ausgang"
    conn.close()


def test_default_category_rules_recognize_patterns_mined_from_real_statements(tmp_path):
    # Derived by analyzing Kevin's full real transaction history (a year of
    # ZKB/Cornercard statements) for recurring merchants that were landing
    # in "Unkategorisiert" — see the categorization-improvement pass this
    # test documents. Each sample is the exact normalized text shape as it
    # actually appears on his statements (e.g. ZKB drops umlauts as bare
    # ASCII rather than folding them to "ae", unlike Cornercard).
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    def category_name(description):
        category_id, _, _ = categorizer.predict(description)
        return conn.execute(
            "SELECT name FROM categories WHERE id = ?", (category_id,)
        ).fetchone()["name"]

    # Lebensmittel
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Backerei Stutz 0891 Auftrags-Nr.X") == "Lebensmittel"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Lidl Affoltern 0891 Auftrags-Nr.X") == "Lebensmittel"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, SPAR DANKT 0891 Affoltern Auftrags-Nr.X") == "Lebensmittel"
    # Restaurants/Ausgang
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Lehmanns Backer-Imbiss 0540 Auftrags-Nr.X") == "Restaurants/Ausgang"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Sabsins Thai Take-Away 0450 Auftrags-Nr.X") == "Restaurants/Ausgang"
    assert category_name("PIZZAFALCONE,BONSTETTEN") == "Restaurants/Ausgang"
    # Transport
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Shell Birmensdorf 0890 Auftrags-Nr.X") == "Transport"
    assert category_name("Online-Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Dott scooter ride Auftrags-Nr.X") == "Transport"
    # Reisen
    assert category_name("SWISSINTLAIRLINES,FRANKFURTAM") == "Reisen"
    assert category_name("Online-Einkauf ZKB Visa Debit Card Nr. xxxx 7369, AIRALO 07990 Auftrags-Nr.X") == "Reisen"
    # Gesundheit
    assert category_name("Belastung Mobile Banking: Zahnarztpraxis Birmensdorf AG Auftrags-Nr.X") == "Gesundheit"
    assert category_name("Belastung TWINT: STADTSPITAL TRIEMLI ZURICH Auftrags-Nr.X") == "Gesundheit"
    # Versicherungen
    assert category_name("Belastung TWINT: AXA VERSICHERUNGEN AG WINTERTHUR Auftrags-Nr.X") == "Versicherungen"
    assert category_name("Belastung Mobile Banking: Swiss Life AG, General-Guisan-Quai 40, 8002 Auftrags-Nr.X") == "Versicherungen"
    # Abos
    assert category_name("Belastung eBill: Salt Mobile SA, Avenue de Malley 2, 1008 Prilly, CH Auftrags-Nr.X") == "Abos"
    assert category_name("Belastung TWINT: GALAXUS ABOS ZURICH Auftrags-Nr.X") == "Abos"
    # Sparen/Anlegen
    assert category_name("Belastung Dauerauftrag: Frankly Risky, 8904 Aesch ZH, CH Auftrags-Nr.X") == "Sparen/Anlegen"
    # Miete/Wohnen
    assert category_name("Belastung Dauerauftrag: Otto Markwalder, c/o Barth Real AG, 8055 Auftrags-Nr.X") == "Miete/Wohnen"
    # Lohn/Einkommen
    assert category_name("Gutschrift Salär: Kanton Zürich, Walcheplatz 1, 8090 Zürich, CH Auftrags-Nr.X") == "Lohn/Einkommen"
    assert category_name(
        "Gutschrift Salär: BSI BUSINESS SYSTEMS INTEGRATION AG, TAEFERNWEG 1 CH Auftrags-Nr.X"
    ) == "Lohn/Einkommen"
    # Shopping
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, IKEA AG, Spreitenbach (A Auftrags-Nr.X") == "Shopping"
    assert category_name("Einkauf ZKB Visa Debit Card Nr. xxxx 7369, Hornbach Baumarkt Affolt Auftrags-Nr.X") == "Shopping"
    # Versicherungen — ZKB drops the umlaut here too ("ÖKK" -> "OKK"), same
    # issue as the "bäckerei"/"backerei" pair above.
    assert category_name("Gutschrift Auftraggeber: OKK Kranken- und Unfallvers., Bahnhofstrasse Auftrags-Nr.X") == "Versicherungen"
    # Abos
    assert category_name("Online-Einkauf ZKB Visa Debit Card Nr. xxxx 7369, NAVIGRAPH 00000 Auftrags-Nr.X") == "Abos"
    # Privatüberweisungen
    assert category_name("Gutschrift Auftraggeber: Kevin Jayaweera, Chilegässli 12d, 8904 Aesch Auftrags-Nr.X") == "Privatüberweisungen"
    conn.close()


def test_init_db_adds_learning_columns_to_category_rules(tmp_path):
    conn = init_db(tmp_path / "test.db")

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(category_rules)")}
    assert {"match_count", "correction_count", "iban", "is_seeded", "created_at"} <= columns
    conn.close()


def test_init_db_adds_suggestion_columns_to_pending_transactions(tmp_path):
    conn = init_db(tmp_path / "test.db")

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(pending_transactions)")}
    assert {"suggested_category_id", "suggested_rule_id", "category_confidence"} <= columns
    conn.close()


def test_init_db_seeds_default_rules_with_starting_track_record(tmp_path):
    conn = init_db(tmp_path / "test.db")

    row = conn.execute(
        "SELECT match_count, correction_count, is_seeded FROM category_rules WHERE keyword = 'migros'"
    ).fetchone()
    assert row["match_count"] == 3
    assert row["correction_count"] == 0
    assert row["is_seeded"] == 1
    conn.close()


def test_init_db_migrates_existing_database_without_losing_data(tmp_path):
    # Simulates a database created before this migration existed.
    db_path = tmp_path / "test.db"
    old_conn = sqlite3.connect(db_path)
    old_conn.executescript("""
        CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE);
        CREATE TABLE category_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL UNIQUE,
            category_id INTEGER NOT NULL REFERENCES categories(id)
        );
        CREATE TABLE pending_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            description TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            currency TEXT NOT NULL DEFAULT 'CHF',
            category_id INTEGER REFERENCES categories(id),
            source TEXT NOT NULL,
            file_id INTEGER
        );
    """)
    old_conn.execute("INSERT INTO categories (name) VALUES ('Lebensmittel')")
    old_conn.execute("INSERT INTO category_rules (keyword, category_id) VALUES ('migros', 1)")
    old_conn.commit()
    old_conn.close()

    conn = init_db(db_path)

    # Pre-existing row survives with conservative (non-seed) defaults —
    # a migration must never silently rewrite a user's existing rule stats.
    migrated = conn.execute(
        "SELECT category_id, match_count, correction_count, is_seeded FROM category_rules WHERE keyword = 'migros'"
    ).fetchone()
    assert migrated["category_id"] == 1
    assert migrated["match_count"] == 0
    assert migrated["correction_count"] == 0
    assert migrated["is_seeded"] == 0
    conn.close()


def test_init_db_migration_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path).close()
    conn = init_db(db_path)  # second call must not raise (duplicate ALTER TABLE)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(category_rules)")}
    assert "match_count" in columns
    conn.close()


def test_reset_db_clears_all_data_and_reseeds_defaults(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    lebensmittel_id = conn.execute(
        "SELECT id FROM categories WHERE name = 'Lebensmittel'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO imported_files (hash, filename, source, imported_at) "
        "VALUES ('abc', 'test.csv', 'test', datetime('now'))"
    )
    conn.execute(
        "INSERT INTO transactions (date, description, amount_cents, currency, category_id, source, manually_corrected) "
        "VALUES ('2026-03-01', 'Testausgabe', -1000, 'CHF', ?, 'test', 0)",
        (lebensmittel_id,),
    )
    conn.execute(
        "INSERT INTO pending_transactions (date, description, amount_cents, currency, category_id, source) "
        "VALUES ('2026-03-02', 'Noch offen', -500, 'CHF', ?, 'test')",
        (lebensmittel_id,),
    )
    conn.execute(
        "INSERT INTO category_rules (keyword, category_id, match_count, correction_count, is_seeded, created_at) "
        "VALUES ('gelernteregel', ?, 5, 0, 0, datetime('now'))",
        (lebensmittel_id,),
    )
    conn.commit()
    conn.close()

    conn = reset_db(db_path)

    assert conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM pending_transactions").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM imported_files").fetchone()["c"] == 0
    assert conn.execute(
        "SELECT COUNT(*) c FROM category_rules WHERE keyword = 'gelernteregel'"
    ).fetchone()["c"] == 0

    names = {row["name"] for row in conn.execute("SELECT name FROM categories").fetchall()}
    assert names == set(DEFAULT_CATEGORIES)
    rule_count = conn.execute("SELECT COUNT(*) c FROM category_rules").fetchone()["c"]
    assert rule_count == len(DEFAULT_CATEGORY_RULES)
    conn.close()


def test_init_db_creates_budgets_table(tmp_path):
    conn = init_db(tmp_path / "test.db")

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert "budgets" in tables
    conn.close()


def test_reset_db_clears_budgets(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    lebensmittel_id = conn.execute(
        "SELECT id FROM categories WHERE name = 'Lebensmittel'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO budgets (category_id, monthly_limit_cents) VALUES (?, 50000)",
        (lebensmittel_id,),
    )
    conn.commit()
    conn.close()

    conn = reset_db(db_path)

    assert conn.execute("SELECT COUNT(*) c FROM budgets").fetchone()["c"] == 0
    conn.close()


def test_reset_imported_data_clears_transactions_pending_and_imported_files(tmp_path):
    # A "clean slate for testing" reset: wipes only what an import produced
    # (transactions, pending rows, imported-file records) so statements can
    # be rescanned from scratch — but unlike reset_db(), leaves everything
    # the user configured/learned (categories, rules, budgets, settings)
    # untouched.
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    lebensmittel_id = conn.execute(
        "SELECT id FROM categories WHERE name = 'Lebensmittel'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO imported_files (hash, filename, source, imported_at) "
        "VALUES ('abc', 'test.csv', 'test', datetime('now'))"
    )
    conn.execute(
        "INSERT INTO transactions (date, description, amount_cents, currency, category_id, source, manually_corrected) "
        "VALUES ('2026-03-01', 'Testausgabe', -1000, 'CHF', ?, 'test', 0)",
        (lebensmittel_id,),
    )
    conn.execute(
        "INSERT INTO pending_transactions (date, description, amount_cents, currency, category_id, source) "
        "VALUES ('2026-03-02', 'Noch offen', -500, 'CHF', ?, 'test')",
        (lebensmittel_id,),
    )
    conn.commit()
    conn.close()

    conn = reset_imported_data(db_path)

    assert conn.execute("SELECT COUNT(*) c FROM transactions").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM pending_transactions").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM imported_files").fetchone()["c"] == 0
    conn.close()


def test_reset_imported_data_keeps_learned_rules_categories_budgets_and_settings(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    lebensmittel_id = conn.execute(
        "SELECT id FROM categories WHERE name = 'Lebensmittel'"
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO category_rules (keyword, category_id, match_count, correction_count, is_seeded, created_at) "
        "VALUES ('gelernteregel', ?, 5, 0, 0, datetime('now'))",
        (lebensmittel_id,),
    )
    conn.execute("INSERT INTO categories (name) VALUES ('Haustier')")
    conn.execute(
        "INSERT INTO budgets (category_id, monthly_limit_cents) VALUES (?, 50000)",
        (lebensmittel_id,),
    )
    conn.execute("UPDATE settings SET value = 'false' WHERE key = 'auto_categorize_enabled'")
    conn.commit()
    conn.close()

    conn = reset_imported_data(db_path)

    assert conn.execute(
        "SELECT COUNT(*) c FROM category_rules WHERE keyword = 'gelernteregel'"
    ).fetchone()["c"] == 1
    assert conn.execute(
        "SELECT COUNT(*) c FROM categories WHERE name = 'Haustier'"
    ).fetchone()["c"] == 1
    assert conn.execute("SELECT COUNT(*) c FROM budgets").fetchone()["c"] == 1
    assert conn.execute(
        "SELECT value FROM settings WHERE key = 'auto_categorize_enabled'"
    ).fetchone()["value"] == "false"
    conn.close()


def test_kreditkarten_ausgleich_category_is_excluded_from_totals(tmp_path):
    conn = init_db(tmp_path / "test.db")

    row = conn.execute(
        "SELECT excluded_from_totals FROM categories WHERE name = 'Kreditkarten-Ausgleich'"
    ).fetchone()
    assert row["excluded_from_totals"] == 1
    conn.close()


def test_other_categories_are_not_excluded_from_totals(tmp_path):
    conn = init_db(tmp_path / "test.db")

    row = conn.execute(
        "SELECT excluded_from_totals FROM categories WHERE name = 'Lebensmittel'"
    ).fetchone()
    assert row["excluded_from_totals"] == 0
    conn.close()


def test_default_category_rules_recognize_credit_card_settlement_lines(tmp_path):
    # Both sides of "pay off the credit card bill from the checking
    # account": the credit-card statement's own payment line, and the ZKB
    # checking-account's matching collection debit for each issuer.
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    def category_name(description):
        category_id, _, _ = categorizer.predict(description)
        return conn.execute(
            "SELECT name FROM categories WHERE id = ?", (category_id,)
        ).fetchone()["name"]

    assert category_name("Gutschrift TWINT: IHRE ZAHLUNG - BESTEN DANK") != "Sonstiges"
    assert category_name("IHREZAHLUNG–BESTENDANK") == "Kreditkarten-Ausgleich"
    assert category_name("Swisscard AECS GmbH, Postfach 227, 8810 Horgen, CH") == "Kreditkarten-Ausgleich"
    assert category_name("Corner Banca SA Cornercard, Via Canova 16, 6901 Lugano, CH") == "Kreditkarten-Ausgleich"
    conn.close()


def test_init_db_migrates_ihre_zahlung_away_from_sonstiges(tmp_path):
    # A database created before this fix existed has "ihre zahlung" seeded
    # under "Sonstiges" — must be retargeted on the next init_db() call.
    db_path = tmp_path / "test.db"
    old_conn = init_db(db_path)
    sonstiges_id = old_conn.execute(
        "SELECT id FROM categories WHERE name = 'Sonstiges'"
    ).fetchone()["id"]
    old_conn.execute(
        "UPDATE category_rules SET category_id = ? WHERE keyword = 'ihre zahlung'",
        (sonstiges_id,),
    )
    old_conn.commit()
    old_conn.close()

    conn = init_db(db_path)

    row = conn.execute(
        "SELECT c.name FROM category_rules r JOIN categories c ON r.category_id = c.id "
        "WHERE r.keyword = 'ihre zahlung'"
    ).fetchone()
    assert row["name"] == "Kreditkarten-Ausgleich"
    conn.close()


def test_default_category_rules_treat_saldovortrag_as_credit_card_settlement(tmp_path):
    # "Saldovortrag" (balance carried forward from the previous statement) is
    # the credit-card statement's own opening-balance line, not a new June
    # expense — it and "Ihre Zahlung" are the two halves of last month's
    # already-counted balance, and must be excluded from totals the same way.
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    category_id, _, _ = categorizer.predict("Saldovortrag")
    name = conn.execute("SELECT name FROM categories WHERE id = ?", (category_id,)).fetchone()["name"]
    assert name == "Kreditkarten-Ausgleich"
    conn.close()


def test_init_db_migrates_saldovortrag_away_from_sonstiges(tmp_path):
    # A database created before this fix existed has "saldovortrag" seeded
    # under "Sonstiges" — must be retargeted on the next init_db() call.
    db_path = tmp_path / "test.db"
    old_conn = init_db(db_path)
    sonstiges_id = old_conn.execute(
        "SELECT id FROM categories WHERE name = 'Sonstiges'"
    ).fetchone()["id"]
    old_conn.execute(
        "UPDATE category_rules SET category_id = ? WHERE keyword = 'saldovortrag'",
        (sonstiges_id,),
    )
    old_conn.commit()
    old_conn.close()

    conn = init_db(db_path)

    row = conn.execute(
        "SELECT c.name FROM category_rules r JOIN categories c ON r.category_id = c.id "
        "WHERE r.keyword = 'saldovortrag'"
    ).fetchone()
    assert row["name"] == "Kreditkarten-Ausgleich"
    conn.close()


def test_init_db_seeds_default_settings(tmp_path):
    conn = init_db(tmp_path / "test.db")

    settings = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")}
    assert settings["auto_categorize_enabled"] == "true"
    assert settings["confidence_threshold"] == "0.75"
    conn.close()


def test_init_db_settings_are_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    conn.execute("UPDATE settings SET value = 'false' WHERE key = 'auto_categorize_enabled'")
    conn.commit()
    conn.close()

    conn = init_db(db_path)  # second call must not overwrite Kevin's existing setting

    value = conn.execute(
        "SELECT value FROM settings WHERE key = 'auto_categorize_enabled'"
    ).fetchone()["value"]
    assert value == "false"
    conn.close()


def test_reset_db_restores_default_settings(tmp_path):
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    conn.execute("UPDATE settings SET value = 'false' WHERE key = 'auto_categorize_enabled'")
    conn.commit()
    conn.close()

    conn = reset_db(db_path)

    value = conn.execute(
        "SELECT value FROM settings WHERE key = 'auto_categorize_enabled'"
    ).fetchone()["value"]
    assert value == "true"
    conn.close()
