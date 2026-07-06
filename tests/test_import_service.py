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

    created = scan_and_parse(conn, statements_dir)

    assert created == 1
    pending = conn.execute("SELECT * FROM pending_transactions").fetchall()
    assert len(pending) == 1
    assert pending[0]["description"] == "Migros Zürich"
    assert pending[0]["amount_cents"] == -4590

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
    second_run_created = scan_and_parse(conn, statements_dir)

    assert second_run_created == 0
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

    created = scan_and_parse(conn, statements_dir)

    assert created == 3

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
