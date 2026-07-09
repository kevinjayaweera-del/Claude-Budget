from server.db import init_db
from server.import_service import scan_and_parse


def test_scan_and_parse_creates_pending_rows_and_marks_file_imported(tmp_path):
    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    (statements_dir / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    conn = init_db(tmp_path / "test.db")

    result = scan_and_parse(conn, statements_dir)

    assert result == {"created": 1, "duplicates_skipped": 0}
    pending = conn.execute("SELECT * FROM pending_transactions").fetchall()
    assert len(pending) == 1
    assert pending[0]["description"] == "Migros Zürich"
    assert pending[0]["amount_cents"] == -4590
    assert pending[0]["suggested_category_id"] == pending[0]["category_id"]

    files = conn.execute("SELECT * FROM imported_files").fetchall()
    assert len(files) == 1


def test_scan_and_parse_skips_already_imported_files(tmp_path):
    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    (statements_dir / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    conn = init_db(tmp_path / "test.db")

    scan_and_parse(conn, statements_dir)
    second_run = scan_and_parse(conn, statements_dir)

    assert second_run == {"created": 0, "duplicates_skipped": 0}
    files = conn.execute("SELECT * FROM imported_files").fetchall()
    assert len(files) == 1


def test_scan_and_parse_uses_subfolder_name_as_source(tmp_path):
    statements_dir = tmp_path / "statements"
    (statements_dir / "ZKB").mkdir(parents=True)
    (statements_dir / "ZKB" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    conn = init_db(tmp_path / "test.db")

    scan_and_parse(conn, statements_dir)

    pending = conn.execute("SELECT * FROM pending_transactions").fetchone()
    assert pending["source"] == "ZKB"


def test_scan_and_parse_skips_malformed_file_and_continues(tmp_path):
    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    # Sorted order: a_first.csv, b_malformed.csv, c_last.csv
    # The malformed file is placed in the middle so we verify processing
    # continues past it rather than it merely being the last file.
    (statements_dir / "a_first.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    (statements_dir / "b_malformed.csv").write_text(
        "Foo;Bar;Baz\n01.03.2026;Nonsense;123\n",
        encoding="utf-8-sig",
    )
    (statements_dir / "c_last.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n02.03.2026;Coop Bern;-12.50;CHF\n"
        "03.03.2026;SBB Billett;-8.00;CHF\n",
        encoding="utf-8-sig",
    )
    conn = init_db(tmp_path / "test.db")

    result = scan_and_parse(conn, statements_dir)

    assert result == {"created": 3, "duplicates_skipped": 0}

    pending = conn.execute(
        "SELECT description FROM pending_transactions ORDER BY description"
    ).fetchall()
    descriptions = {row["description"] for row in pending}
    assert descriptions == {"Migros Zürich", "Coop Bern", "SBB Billett"}

    imported_filenames = {
        row["filename"]
        for row in conn.execute("SELECT filename FROM imported_files").fetchall()
    }
    assert imported_filenames == {"a_first.csv", "c_last.csv"}
    assert "b_malformed.csv" not in imported_filenames


def test_scan_and_parse_skips_duplicate_found_in_pending_transactions(tmp_path):
    # Simulates re-downloading the same statement under a different filename
    # (e.g. the export tool bakes a timestamp into the name) before the first
    # import was ever confirmed. The second file's content must differ from
    # the first (here: one extra, genuinely new row) so file-hash dedup
    # doesn't short-circuit the scan before transaction-level dedup runs.
    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    (statements_dir / "a.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    conn = init_db(tmp_path / "test.db")
    scan_and_parse(conn, statements_dir)

    (statements_dir / "b_rescan.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "01.03.2026;Migros Zürich;-45.90;CHF\n"
        "02.03.2026;Coop Bern;-10.00;CHF\n",
        encoding="utf-8-sig",
    )
    result = scan_and_parse(conn, statements_dir)

    assert result == {"created": 1, "duplicates_skipped": 1}
    pending = conn.execute("SELECT description FROM pending_transactions ORDER BY description").fetchall()
    assert {row["description"] for row in pending} == {"Migros Zürich", "Coop Bern"}


def test_scan_and_parse_skips_duplicate_found_in_confirmed_transactions(tmp_path):
    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    conn = init_db(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO transactions (date, description, amount_cents, currency, source, manually_corrected) "
        "VALUES ('2026-03-01', 'Migros Zürich', -4590, 'CHF', 'a', 0)"
    )
    conn.commit()

    (statements_dir / "b.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    result = scan_and_parse(conn, statements_dir)

    assert result == {"created": 0, "duplicates_skipped": 1}
    assert conn.execute("SELECT COUNT(*) AS c FROM pending_transactions").fetchone()["c"] == 0


def test_scan_and_parse_only_skips_matching_count_not_every_occurrence(tmp_path):
    # One matching transaction already exists; the new file has two
    # identical-looking rows. Only one is a re-import of the known
    # transaction — the other is a genuinely new occurrence and must still
    # be imported, not silently dropped, since a "duplicate" match must be
    # unambiguous (count-for-count), never a blanket "seen this key before".
    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    conn = init_db(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO transactions (date, description, amount_cents, currency, source, manually_corrected) "
        "VALUES ('2026-03-01', 'Kaffee Bar', -500, 'CHF', 'a', 0)"
    )
    conn.commit()

    (statements_dir / "b.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "01.03.2026;Kaffee Bar;-5.00;CHF\n"
        "01.03.2026;Kaffee Bar;-5.00;CHF\n",
        encoding="utf-8-sig",
    )
    result = scan_and_parse(conn, statements_dir)

    assert result == {"created": 1, "duplicates_skipped": 1}
    pending = conn.execute("SELECT * FROM pending_transactions").fetchall()
    assert len(pending) == 1


def test_scan_and_parse_does_not_dedupe_across_different_amounts_or_dates(tmp_path):
    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    conn = init_db(tmp_path / "test.db")
    conn.execute(
        "INSERT INTO transactions (date, description, amount_cents, currency, source, manually_corrected) "
        "VALUES ('2026-03-01', 'Migros Zürich', -4590, 'CHF', 'a', 0)"
    )
    conn.commit()

    (statements_dir / "b.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "02.03.2026;Migros Zürich;-45.90;CHF\n"  # different date
        "01.03.2026;Migros Zürich;-12.00;CHF\n",  # different amount
        encoding="utf-8-sig",
    )
    result = scan_and_parse(conn, statements_dir)

    assert result == {"created": 2, "duplicates_skipped": 0}


def test_scan_and_parse_skips_categorization_when_disabled(tmp_path):
    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    (statements_dir / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    conn = init_db(tmp_path / "test.db")
    conn.execute("UPDATE settings SET value = 'false' WHERE key = 'auto_categorize_enabled'")
    conn.commit()

    scan_and_parse(conn, statements_dir)

    pending = conn.execute("SELECT * FROM pending_transactions").fetchone()
    unkategorisiert_id = conn.execute(
        "SELECT id FROM categories WHERE name = 'Unkategorisiert'"
    ).fetchone()["id"]
    assert pending["category_id"] == unkategorisiert_id
    assert pending["suggested_rule_id"] is None
    assert pending["category_confidence"] is None
