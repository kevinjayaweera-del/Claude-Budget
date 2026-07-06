import sqlite3

from server.db import init_db, DEFAULT_CATEGORIES


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
