from server.db import init_db
from server.categorize import categorize, learn_rule


def _category_id(conn, name):
    return conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()["id"]


def test_categorize_falls_back_to_uncategorized(tmp_path):
    conn = init_db(tmp_path / "test.db")
    result = categorize("Unbekannte Buchung XYZ", conn)
    assert result == _category_id(conn, "Unkategorisiert")


def test_categorize_matches_existing_rule(tmp_path):
    conn = init_db(tmp_path / "test.db")
    lebensmittel_id = _category_id(conn, "Lebensmittel")
    conn.execute(
        # OR REPLACE: "migros"/"coop"/"coop pronto" are also pre-seeded by
        # init_db()'s DEFAULT_CATEGORY_RULES — this test asserts categorize()
        # behavior for a known rule, not that these specific keywords are
        # unseeded, so replacing rather than colliding is correct here.
        "INSERT OR REPLACE INTO category_rules (keyword, category_id) VALUES (?, ?)",
        ("migros", lebensmittel_id),
    )
    conn.commit()

    result = categorize("MIGROS Zuerich Filiale 12", conn)
    assert result == lebensmittel_id


def test_learn_rule_stores_keyword_and_is_reused(tmp_path):
    conn = init_db(tmp_path / "test.db")
    freizeit_id = _category_id(conn, "Freizeit")

    learn_rule(conn, "Starbucks Bahnhof", freizeit_id)
    result = categorize("Starbucks Hauptbahnhof", conn)

    assert result == freizeit_id


def test_learn_rule_ignores_empty_keyword(tmp_path):
    conn = init_db(tmp_path / "test.db")
    freizeit_id = _category_id(conn, "Freizeit")

    learn_rule(conn, "", freizeit_id)
    learn_rule(conn, "   ", freizeit_id)

    result = categorize("Some totally unrelated new transaction", conn)
    assert result == _category_id(conn, "Unkategorisiert")


def test_categorize_prefers_longer_more_specific_keyword(tmp_path):
    conn = init_db(tmp_path / "test.db")
    lebensmittel_id = _category_id(conn, "Lebensmittel")
    transport_id = _category_id(conn, "Transport")

    conn.execute(
        # OR REPLACE: "migros"/"coop"/"coop pronto" are also pre-seeded by
        # init_db()'s DEFAULT_CATEGORY_RULES — this test asserts categorize()
        # behavior for a known rule, not that these specific keywords are
        # unseeded, so replacing rather than colliding is correct here.
        "INSERT OR REPLACE INTO category_rules (keyword, category_id) VALUES (?, ?)",
        ("coop", lebensmittel_id),
    )
    conn.execute(
        # OR REPLACE: "migros"/"coop"/"coop pronto" are also pre-seeded by
        # init_db()'s DEFAULT_CATEGORY_RULES — this test asserts categorize()
        # behavior for a known rule, not that these specific keywords are
        # unseeded, so replacing rather than colliding is correct here.
        "INSERT OR REPLACE INTO category_rules (keyword, category_id) VALUES (?, ?)",
        ("coop pronto", transport_id),
    )
    conn.commit()

    result = categorize("Coop Pronto Zuerich", conn)
    assert result == transport_id
