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

    assert result == {"created": 1, "duplicates_skipped": 0, "failed_files": [], "row_errors": []}
    pending = conn.execute("SELECT * FROM pending_transactions").fetchall()
    assert len(pending) == 1
    assert pending[0]["description"] == "Migros Zürich"
    assert pending[0]["amount_cents"] == -4590
    assert pending[0]["suggested_category_id"] == pending[0]["category_id"]

    files = conn.execute("SELECT * FROM imported_files").fetchall()
    assert len(files) == 1


def test_scan_and_parse_sets_account_id_matching_source(tmp_path):
    statements_dir = tmp_path / "statements"
    (statements_dir / "ZKB").mkdir(parents=True)
    (statements_dir / "ZKB" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    conn = init_db(tmp_path / "test.db")

    scan_and_parse(conn, statements_dir)

    pending = conn.execute("SELECT account_id FROM pending_transactions").fetchone()
    assert pending["account_id"] is not None
    account = conn.execute("SELECT source_key, name FROM accounts WHERE id = ?", (pending["account_id"],)).fetchone()
    assert account["source_key"] == "ZKB"
    assert account["name"] == "ZKB"


def test_scan_and_parse_reuses_same_account_across_files(tmp_path):
    statements_dir = tmp_path / "statements"
    (statements_dir / "ZKB").mkdir(parents=True)
    (statements_dir / "ZKB" / "a.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    (statements_dir / "ZKB" / "b.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n02.03.2026;Coop Bern;-12.30;CHF\n",
        encoding="utf-8-sig",
    )
    conn = init_db(tmp_path / "test.db")

    scan_and_parse(conn, statements_dir)

    account_ids = {row["account_id"] for row in conn.execute("SELECT account_id FROM pending_transactions")}
    assert len(account_ids) == 1
    assert conn.execute("SELECT COUNT(*) c FROM accounts").fetchone()["c"] == 1


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

    assert second_run == {"created": 0, "duplicates_skipped": 0, "failed_files": [], "row_errors": []}
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

    assert result["created"] == 3
    assert result["duplicates_skipped"] == 0
    # The whole-file failure must be reported, not just silently swallowed
    # (previously the only trace was an stderr print nobody running the
    # desktop app would ever see).
    assert len(result["failed_files"]) == 1
    assert result["failed_files"][0]["filename"] == "b_malformed.csv"
    assert result["failed_files"][0]["reason"]

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


def test_scan_and_parse_skips_only_the_malformed_row_within_a_file(tmp_path):
    # Regression test for the critical bug found in the pre-release audit:
    # previously, ANY row-level parse failure (bad date/amount format)
    # raised out of parse_csv/parse_pdf and was caught by scan_and_parse's
    # file-level except — discarding every other, well-formed row in the
    # same file, not just the bad one. Only the malformed row should be
    # skipped (and reported), the rest of the file must still import.
    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    (statements_dir / "mixed.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "01.03.2026;Migros Zürich;-45.90;CHF\n"
        "not-a-date;Kaputte Zeile;-12.00;CHF\n"
        "03.03.2026;Coop Bern;-8.00;CHF\n",
        encoding="utf-8-sig",
    )
    conn = init_db(tmp_path / "test.db")

    result = scan_and_parse(conn, statements_dir)

    assert result["created"] == 2
    assert result["failed_files"] == []
    assert len(result["row_errors"]) == 1
    assert result["row_errors"][0]["filename"] == "mixed.csv"
    assert result["row_errors"][0]["reason"]

    pending = conn.execute(
        "SELECT description FROM pending_transactions ORDER BY description"
    ).fetchall()
    descriptions = {row["description"] for row in pending}
    assert descriptions == {"Migros Zürich", "Coop Bern"}
    # The whole file (not just the good rows) is still marked imported —
    # a bad row is reported, not treated as "this file needs a full rescan".
    imported_filenames = {
        row["filename"]
        for row in conn.execute("SELECT filename FROM imported_files").fetchall()
    }
    assert imported_filenames == {"mixed.csv"}


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

    assert result == {"created": 1, "duplicates_skipped": 1, "failed_files": [], "row_errors": []}
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

    assert result == {"created": 0, "duplicates_skipped": 1, "failed_files": [], "row_errors": []}
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

    assert result == {"created": 1, "duplicates_skipped": 1, "failed_files": [], "row_errors": []}
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

    assert result == {"created": 2, "duplicates_skipped": 0, "failed_files": [], "row_errors": []}


def test_scan_and_parse_dry_run_reports_counts_without_writing_anything(tmp_path):
    # A fail-safe preview: the caller can see what a scan WOULD do (new vs.
    # duplicate counts) before anything lands in the database, so the UI can
    # warn about duplicates and let the user cancel before any write happens.
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
        "01.03.2026;Migros Zürich;-45.90;CHF\n"
        "02.03.2026;Coop Bern;-10.00;CHF\n",
        encoding="utf-8-sig",
    )

    result = scan_and_parse(conn, statements_dir, dry_run=True)

    assert result == {"created": 1, "duplicates_skipped": 1, "failed_files": [], "row_errors": []}
    assert conn.execute("SELECT COUNT(*) c FROM pending_transactions").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) c FROM imported_files").fetchone()["c"] == 0


def test_scan_and_parse_dry_run_then_real_run_produce_the_same_result(tmp_path):
    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    (statements_dir / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )
    conn = init_db(tmp_path / "test.db")

    preview = scan_and_parse(conn, statements_dir, dry_run=True)
    real = scan_and_parse(conn, statements_dir)

    assert preview == real == {"created": 1, "duplicates_skipped": 0, "failed_files": [], "row_errors": []}
    assert conn.execute("SELECT COUNT(*) c FROM pending_transactions").fetchone()["c"] == 1


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


def test_scan_and_parse_routes_hidden_category_straight_into_transactions(tmp_path):
    statements_dir = tmp_path / "statements"
    statements_dir.mkdir()
    (statements_dir / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;OnlyFans.com Payment;-19.99;CHF\n",
        encoding="utf-8-sig",
    )
    conn = init_db(tmp_path / "test.db")

    result = scan_and_parse(conn, statements_dir)

    assert result == {"created": 1, "duplicates_skipped": 0, "failed_files": [], "row_errors": []}
    # Never touches pending_transactions — no review step, no KPI count.
    assert conn.execute("SELECT COUNT(*) c FROM pending_transactions").fetchone()["c"] == 0
    transactions = conn.execute("SELECT * FROM transactions").fetchall()
    assert len(transactions) == 1
    versteckt_id = conn.execute(
        "SELECT id FROM categories WHERE name = 'Versteckt'"
    ).fetchone()["id"]
    assert transactions[0]["category_id"] == versteckt_id
    assert transactions[0]["manually_corrected"] == 0

    # Still counted by the per-file reconciliation total.
    file_row = conn.execute(
        "SELECT f.id FROM imported_files f WHERE f.filename = 'test.csv'"
    ).fetchone()
    assert transactions[0]["file_id"] == file_row["id"]
