import sqlite3

from server.db import init_db, DEFAULT_CATEGORIES, DEFAULT_CATEGORY_RULES
from server.categorize import categorize


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

    def category_name(description):
        category_id = categorize(description, conn)
        return conn.execute(
            "SELECT name FROM categories WHERE id = ?", (category_id,)
        ).fetchone()["name"]

    assert category_name("Belastung TWINT: SBB MOBILE BERN") == "Transport"
    assert category_name("Belastung TWINT: PARKINGPAY-TWINT SCHLIEREN") == "Transport"
    assert category_name("Belastung TWINT: GALAXUS MOBILE ZURICH") == "Shopping"
    assert category_name("Belastung TWINT: BABY-WALZ AG ST. GALLEN") == "Shopping"
    assert category_name("Gutschrift TWINT: SCHWARTZ, PATRICK +41764535887") == "Privatüberweisungen"
    conn.close()
