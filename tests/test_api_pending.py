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


def _write_sample_csv(tmp_path):
    (tmp_path / "statements" / "test.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Migros Zürich;-45.90;CHF\n",
        encoding="utf-8-sig",
    )


def _write_foreign_currency_csv(tmp_path):
    (tmp_path / "statements" / "foreign.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n01.03.2026;Amazon.de;-20.00;EUR\n",
        encoding="utf-8-sig",
    )


def _write_two_row_csv(tmp_path):
    (tmp_path / "statements" / "two_rows.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "01.03.2026;Migros Zürich;-45.90;CHF\n"
        "02.03.2026;Coop Basel;-12.30;CHF\n",
        encoding="utf-8-sig",
    )


def test_scan_endpoint_returns_new_pending_count(client, tmp_path):
    _write_sample_csv(tmp_path)

    response = client.post("/api/scan")

    assert response.status_code == 200
    assert response.get_json() == {"new_pending": 1, "duplicates_skipped": 0}


def test_scan_endpoint_reports_duplicates_skipped_on_rescan_under_new_filename(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")

    # Different byte content (extra row) than test.csv, so file-hash dedup
    # doesn't short-circuit before transaction-level dedup runs — mirrors a
    # re-downloaded statement whose export tool renames/re-timestamps the file.
    (tmp_path / "statements" / "test_redownloaded.csv").write_text(
        "Datum;Buchungstext;Betrag;Währung\n"
        "01.03.2026;Migros Zürich;-45.90;CHF\n"
        "02.03.2026;Coop Basel;-12.30;CHF\n",
        encoding="utf-8-sig",
    )
    response = client.post("/api/scan")

    assert response.get_json() == {"new_pending": 1, "duplicates_skipped": 1}
    assert len(client.get("/api/pending").get_json()) == 2


def test_pending_list_reflects_scanned_rows(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")

    rows = client.get("/api/pending").get_json()

    assert len(rows) == 1
    assert rows[0]["description"] == "Migros Zürich"
    assert rows[0]["amount_cents"] == -4590


def test_update_pending_row_changes_category(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")
    pending_id = client.get("/api/pending").get_json()[0]["id"]
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}

    response = client.put(f"/api/pending/{pending_id}", json={
        "date": "2026-03-01",
        "description": "Migros Zürich",
        "amount_cents": -4590,
        "currency": "CHF",
        "category_id": categories["Lebensmittel"],
    })

    assert response.status_code == 200
    updated = client.get("/api/pending").get_json()[0]
    assert updated["category_id"] == categories["Lebensmittel"]


def test_update_pending_row_with_missing_body_returns_400(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")
    pending_id = client.get("/api/pending").get_json()[0]["id"]

    response = client.put(f"/api/pending/{pending_id}")

    assert response.status_code == 400


def test_update_pending_row_with_missing_field_returns_400(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")
    pending_id = client.get("/api/pending").get_json()[0]["id"]

    response = client.put(f"/api/pending/{pending_id}", json={
        "date": "2026-03-01",
        "description": "Migros Zürich",
        "amount_cents": -4590,
        "currency": "CHF",
        # category_id intentionally omitted
    })

    assert response.status_code == 400


def test_update_nonexistent_pending_row_returns_404(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}

    response = client.put("/api/pending/99999", json={
        "date": "2026-03-01",
        "description": "Migros Zürich",
        "amount_cents": -4590,
        "currency": "CHF",
        "category_id": categories["Lebensmittel"],
    })

    assert response.status_code == 404


def test_delete_nonexistent_pending_row_returns_404(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")

    response = client.delete("/api/pending/99999")

    assert response.status_code == 404


def test_delete_pending_row_removes_it(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")
    pending_id = client.get("/api/pending").get_json()[0]["id"]

    response = client.delete(f"/api/pending/{pending_id}")

    assert response.status_code == 200
    assert client.get("/api/pending").get_json() == []


def test_confirm_import_moves_rows_to_transactions_and_clears_pending(client, tmp_path):
    _write_sample_csv(tmp_path)
    client.post("/api/scan")

    response = client.post("/api/import/confirm")

    assert response.get_json() == {"imported": 1}
    assert client.get("/api/pending").get_json() == []


def test_confirm_import_preserves_non_chf_currency(client, tmp_path):
    """Regression test for the currency-clobbering bug: the PUT that saves a
    pending row's edits must preserve whatever currency it is sent (this is
    the invariant the frontend fix relies on), and confirm_import must carry
    that currency through into the transactions table unchanged."""
    _write_foreign_currency_csv(tmp_path)
    client.post("/api/scan")
    pending = client.get("/api/pending").get_json()[0]
    assert pending["currency"] == "EUR"
    categories = {c["name"]: c["id"] for c in client.get("/api/categories").get_json()}

    put_response = client.put(f"/api/pending/{pending['id']}", json={
        "date": pending["date"],
        "description": pending["description"],
        "amount_cents": pending["amount_cents"],
        "currency": "EUR",
        "category_id": categories["Sonstiges"] if "Sonstiges" in categories else list(categories.values())[0],
    })
    assert put_response.status_code == 200

    response = client.post("/api/import/confirm")
    assert response.get_json() == {"imported": 1}

    transactions = client.get("/api/transactions").get_json()
    assert len(transactions) == 1
    assert transactions[0]["currency"] == "EUR"


def test_confirm_import_with_explicit_ids_only_imports_selected_rows(client, tmp_path):
    _write_two_row_csv(tmp_path)
    client.post("/api/scan")
    pending_rows = client.get("/api/pending").get_json()
    assert len(pending_rows) == 2
    first_id = pending_rows[0]["id"]
    second_id = pending_rows[1]["id"]

    response = client.post("/api/import/confirm", json={"ids": [first_id]})

    assert response.get_json() == {"imported": 1}
    transactions = client.get("/api/transactions").get_json()
    assert len(transactions) == 1
    assert transactions[0]["description"] == pending_rows[0]["description"]

    remaining_pending = client.get("/api/pending").get_json()
    assert len(remaining_pending) == 1
    assert remaining_pending[0]["id"] == second_id
