import sqlite3

from server.db import init_db, reset_db, DEFAULT_CATEGORIES, DEFAULT_CATEGORY_RULES
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
