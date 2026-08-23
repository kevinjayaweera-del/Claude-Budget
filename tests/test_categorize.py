from server.db import init_db
from server.categorize import RuleBasedCategorizer, compute_confidence, CONFIDENCE_THRESHOLD


def _category_id(conn, name):
    return conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()["id"]


def test_compute_confidence_new_rule_starts_at_fifty_percent():
    assert compute_confidence(match_count=0, correction_count=0) == 0.5


def test_compute_confidence_increases_with_matches():
    assert compute_confidence(match_count=5, correction_count=0) == 6 / 7


def test_compute_confidence_decreases_with_corrections():
    low = compute_confidence(match_count=4, correction_count=1)
    high = compute_confidence(match_count=5, correction_count=0)
    assert low < high


def test_confidence_threshold_is_seventy_five_percent():
    assert CONFIDENCE_THRESHOLD == 0.75


def test_predict_falls_back_to_uncategorized_with_zero_confidence(tmp_path):
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    category_id, confidence, rule_id = categorizer.predict("Unbekannte Buchung XYZ")

    assert category_id == _category_id(conn, "Unkategorisiert")
    assert confidence == 0.0
    assert rule_id is None
    conn.close()


def test_predict_matches_existing_rule_and_returns_its_confidence(tmp_path):
    conn = init_db(tmp_path / "test.db")
    lebensmittel_id = _category_id(conn, "Lebensmittel")
    cursor = conn.execute(
        "INSERT INTO category_rules (keyword, category_id, match_count, correction_count, is_seeded, created_at) "
        "VALUES ('fischmarkt', ?, 5, 0, 0, datetime('now'))",
        (lebensmittel_id,),
    )
    conn.commit()
    rule_id = cursor.lastrowid
    categorizer = RuleBasedCategorizer(conn)

    category_id, confidence, matched_rule_id = categorizer.predict("Fischmarkt Zürich AG")

    assert category_id == lebensmittel_id
    assert confidence == compute_confidence(5, 0)
    assert matched_rule_id == rule_id
    conn.close()


def test_predict_prefers_longer_more_specific_keyword(tmp_path):
    conn = init_db(tmp_path / "test.db")
    lebensmittel_id = _category_id(conn, "Lebensmittel")
    transport_id = _category_id(conn, "Transport")
    # OR REPLACE: "coop" is also pre-seeded by init_db()'s DEFAULT_CATEGORY_RULES
    # — this test asserts predict() behavior for known rules, not that these
    # specific keywords are unseeded, so replacing rather than colliding is
    # correct here.
    conn.execute(
        "INSERT OR REPLACE INTO category_rules (keyword, category_id, match_count, correction_count, is_seeded, created_at) "
        "VALUES ('coop', ?, 3, 0, 0, datetime('now'))",
        (lebensmittel_id,),
    )
    conn.execute(
        "INSERT OR REPLACE INTO category_rules (keyword, category_id, match_count, correction_count, is_seeded, created_at) "
        "VALUES ('coop pronto', ?, 3, 0, 0, datetime('now'))",
        (transport_id,),
    )
    conn.commit()
    categorizer = RuleBasedCategorizer(conn)

    category_id, _, _ = categorizer.predict("Coop Pronto Zuerich")

    assert category_id == transport_id
    conn.close()


def test_predict_normalizes_before_matching(tmp_path):
    # "BAECKEREI" (ASCII spelling) must match the pre-seeded "bäckerei" rule
    # via normalization, without a separate hand-added keyword.
    conn = init_db(tmp_path / "test.db")
    categorizer = RuleBasedCategorizer(conn)

    category_id, confidence, rule_id = categorizer.predict("Einkauf ZKB Visa Debit Card Nr. xxxx 1234, BAECKEREI BODE")

    assert category_id == _category_id(conn, "Lebensmittel")
    assert rule_id is not None
    conn.close()


def test_learn_creates_new_rule_with_match_count_one(tmp_path):
    conn = init_db(tmp_path / "test.db")
    freizeit_id = _category_id(conn, "Freizeit")
    categorizer = RuleBasedCategorizer(conn)

    categorizer.learn("Kletterzentrum Adliswil", freizeit_id)
    category_id, confidence, rule_id = categorizer.predict("Kletterzentrum Adliswil Eintritt")

    assert category_id == freizeit_id
    assert confidence == compute_confidence(1, 0)
    conn.close()


def test_learn_ignores_description_with_no_extractable_keyword(tmp_path):
    conn = init_db(tmp_path / "test.db")
    freizeit_id = _category_id(conn, "Freizeit")
    categorizer = RuleBasedCategorizer(conn)

    categorizer.learn("", freizeit_id)
    categorizer.learn("   ", freizeit_id)
    category_id, confidence, rule_id = categorizer.predict("Some totally unrelated new transaction")

    assert category_id == _category_id(conn, "Unkategorisiert")
    conn.close()


def test_learn_never_extracts_twint_as_the_keyword(tmp_path):
    # Regression guard: learning from a merchant-routed TWINT line must not
    # overwrite the generic "twint" system rule (Privatüberweisungen), which
    # would break the length-based override for lines like "TWINT: SBB MOBILE".
    conn = init_db(tmp_path / "test.db")
    shopping_id = _category_id(conn, "Shopping")
    categorizer = RuleBasedCategorizer(conn)

    categorizer.learn("Belastung TWINT: NEUERHAENDLER XYZ", shopping_id)

    twint_rule = conn.execute("SELECT category_id FROM category_rules WHERE keyword = 'twint'").fetchone()
    assert twint_rule["category_id"] == _category_id(conn, "Privatüberweisungen")
    conn.close()


def test_learn_upsert_preserves_history_when_category_unchanged(tmp_path):
    conn = init_db(tmp_path / "test.db")
    freizeit_id = _category_id(conn, "Freizeit")
    categorizer = RuleBasedCategorizer(conn)

    categorizer.learn("Kletterzentrum Adliswil", freizeit_id)
    categorizer.learn("Kletterzentrum Adliswil", freizeit_id)
    categorizer.learn("Kletterzentrum Adliswil", freizeit_id)

    row = conn.execute(
        "SELECT match_count, correction_count FROM category_rules WHERE keyword = 'kletterzentrum'"
    ).fetchone()
    assert row["match_count"] == 3
    assert row["correction_count"] == 0
    conn.close()


def test_learn_upsert_resets_history_when_category_changes(tmp_path):
    conn = init_db(tmp_path / "test.db")
    freizeit_id = _category_id(conn, "Freizeit")
    shopping_id = _category_id(conn, "Shopping")
    categorizer = RuleBasedCategorizer(conn)

    categorizer.learn("Kletterzentrum Adliswil", freizeit_id)
    categorizer.learn("Kletterzentrum Adliswil", freizeit_id)
    categorizer.learn("Kletterzentrum Adliswil", shopping_id)  # retargeted

    row = conn.execute(
        "SELECT category_id, match_count, correction_count FROM category_rules WHERE keyword = 'kletterzentrum'"
    ).fetchone()
    assert row["category_id"] == shopping_id
    assert row["match_count"] == 1
    assert row["correction_count"] == 0
    conn.close()


def test_learn_skips_when_extracted_keyword_matches_exclude_keyword(tmp_path):
    # Regression guard for a real bug: correcting a single "Migros Zürich"
    # transaction away from Lebensmittel used to be able to instantly
    # retarget the *entire* "migros" rule to the new category (since
    # _extract_keyword() can land back on the very keyword that produced the
    # now-corrected suggestion) — silently undoing the correction_count
    # penalty confirm_import() applies and breaking every future Migros
    # purchase. exclude_keyword lets the caller name that keyword so learn()
    # skips instead of stealing it.
    conn = init_db(tmp_path / "test.db")
    lebensmittel_id = _category_id(conn, "Lebensmittel")
    sonstiges_id = _category_id(conn, "Sonstiges")
    categorizer = RuleBasedCategorizer(conn)
    migros_before = dict(conn.execute(
        "SELECT category_id, match_count, correction_count FROM category_rules WHERE keyword = 'migros'"
    ).fetchone())

    categorizer.learn("Migros", sonstiges_id, was_correction=True, exclude_keyword="migros")

    migros_after = dict(conn.execute(
        "SELECT category_id, match_count, correction_count FROM category_rules WHERE keyword = 'migros'"
    ).fetchone())
    assert migros_after == migros_before
    assert migros_after["category_id"] == lebensmittel_id
    conn.close()


def test_learn_still_learns_a_different_keyword_despite_exclude_keyword(tmp_path):
    # exclude_keyword must only block the one specific keyword, not learning
    # entirely — a correction that genuinely extracts a different word must
    # still teach it normally.
    conn = init_db(tmp_path / "test.db")
    freizeit_id = _category_id(conn, "Freizeit")
    categorizer = RuleBasedCategorizer(conn)

    categorizer.learn("Kletterzentrum Adliswil", freizeit_id, was_correction=True, exclude_keyword="migros")

    row = conn.execute(
        "SELECT category_id FROM category_rules WHERE keyword = 'kletterzentrum'"
    ).fetchone()
    assert row is not None
    assert row["category_id"] == freizeit_id
    conn.close()


def test_extract_keyword_never_learns_a_purely_numeric_token(tmp_path):
    # Regression guard: a masked postal code or similar digit placeholder
    # (e.g. "0000") must never become "the" keyword — found via a real-data
    # audit where "00000" had been learned this way with 157 hits.
    conn = init_db(tmp_path / "test.db")
    freizeit_id = _category_id(conn, "Freizeit")
    categorizer = RuleBasedCategorizer(conn)

    categorizer.learn("Irgendein Laden 00000", freizeit_id)

    assert conn.execute("SELECT id FROM category_rules WHERE keyword = '00000'").fetchone() is None
    row = conn.execute("SELECT id FROM category_rules WHERE keyword = 'irgendein'").fetchone()
    assert row is not None
    conn.close()


def test_extract_keyword_never_learns_a_city_or_country_name(tmp_path):
    # Regression guard: a real-data audit found city/country-name fragments
    # (e.g. "zuerich", from "Gutschrift Salär: Kanton Zürich, ...") had been
    # learned as "keywords" this way, poisoning every future transaction
    # whose address happens to be in that city — including salary credits
    # and grocery purchases whose real merchant name was shorter.
    conn = init_db(tmp_path / "test.db")
    lohn_id = _category_id(conn, "Lohn/Einkommen")
    categorizer = RuleBasedCategorizer(conn)

    categorizer.learn("Gutschrift Salaer: Kanton Zuerich, Walcheplatz 1, 8090 Zuerich, CH", lohn_id)

    assert conn.execute("SELECT id FROM category_rules WHERE keyword = 'zuerich'").fetchone() is None
    conn.close()
