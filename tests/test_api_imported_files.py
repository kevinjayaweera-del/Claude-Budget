import pytest

from server.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(
        db_path=tmp_path / "test.db",
        statements_dir=tmp_path / "statements",
    )
    app.config["TESTING"] = True
    return app.test_client()


def _write_csv(tmp_path, name, rows):
    lines = ["Datum;Buchungstext;Betrag;Währung"]
    lines.extend(f"{date};{desc};{amount};CHF" for date, desc, amount in rows)
    (tmp_path / "statements" / name).write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def test_imported_files_empty_before_any_scan(client):
    response = client.get("/api/imported-files")

    assert response.status_code == 200
    assert response.get_json() == []


def test_imported_files_aggregates_pending_rows_before_confirm(client, tmp_path):
    _write_csv(tmp_path, "a.csv", [
        ("01.03.2026", "Migros Zuerich", "-45.90"),
        ("02.03.2026", "Lohn Maerz", "5200.00"),
    ])
    client.post("/api/scan")

    rows = client.get("/api/imported-files").get_json()

    assert len(rows) == 1
    assert rows[0]["filename"] == "a.csv"
    assert rows[0]["transaction_count"] == 2
    assert rows[0]["income_cents"] == 520000
    assert rows[0]["expense_cents"] == -4590
    assert rows[0]["net_cents"] == 520000 - 4590


def test_imported_files_still_aggregates_after_confirm(client, tmp_path):
    _write_csv(tmp_path, "a.csv", [
        ("01.03.2026", "Migros Zuerich", "-45.90"),
        ("02.03.2026", "Lohn Maerz", "5200.00"),
    ])
    client.post("/api/scan")
    client.post("/api/import/confirm")

    # The whole point: file totals must survive confirmation, not just show
    # while rows are still sitting in the pending review queue — otherwise
    # there's no way to reconcile an already-imported file against its PDF.
    rows = client.get("/api/imported-files").get_json()

    assert len(rows) == 1
    assert rows[0]["transaction_count"] == 2
    assert rows[0]["income_cents"] == 520000
    assert rows[0]["expense_cents"] == -4590


def test_imported_files_combines_partially_confirmed_rows(client, tmp_path):
    _write_csv(tmp_path, "a.csv", [
        ("01.03.2026", "Migros Zuerich", "-45.90"),
        ("02.03.2026", "Coop Basel", "-12.30"),
    ])
    client.post("/api/scan")
    pending = client.get("/api/pending").get_json()
    first_id = next(r["id"] for r in pending if r["description"] == "Migros Zuerich")

    # Confirm only one of the two rows — the other stays in
    # pending_transactions. The file's total must still count both.
    client.post("/api/import/confirm", json={"ids": [first_id]})

    rows = client.get("/api/imported-files").get_json()

    assert len(rows) == 1
    assert rows[0]["transaction_count"] == 2
    assert rows[0]["expense_cents"] == -4590 - 1230


def test_imported_files_lists_multiple_files_separately(client, tmp_path):
    _write_csv(tmp_path, "a.csv", [("01.03.2026", "Migros Zuerich", "-45.90")])
    _write_csv(tmp_path, "b.csv", [("02.03.2026", "Coop Basel", "-12.30")])
    client.post("/api/scan")

    rows = client.get("/api/imported-files").get_json()

    filenames = {r["filename"] for r in rows}
    assert filenames == {"a.csv", "b.csv"}
    a_row = next(r for r in rows if r["filename"] == "a.csv")
    b_row = next(r for r in rows if r["filename"] == "b.csv")
    assert a_row["transaction_count"] == 1
    assert b_row["transaction_count"] == 1
